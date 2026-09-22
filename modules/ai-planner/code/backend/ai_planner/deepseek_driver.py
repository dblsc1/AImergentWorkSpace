"""DeepSeek 驱动适配器 —— codex ChatGPT 登录态被吊销时的替代路径。

同一 `PlannerDriver` 接口的第二个实现（module_docs/contract.md「驱动可插拔」/「真流式」节）：
换驱动不改变安全边界一个字节 —— 本驱动只产出结构化动作清单，**不持有 nexus 客户端、
不执行任何写操作**，执行/裁夺仍在受控工具层（controlled_tools.py + action_router.py）。

**真流式**：改用 `"stream": true`，逐块消费 DeepSeek 的 SSE
`delta`。两层流式要分清楚（module_docs/contract.md「流式的两层」节）：
  1. 驱动层：DeepSeek 逐块回 `delta.reasoning_content` / `delta.content`。
  2. 我们的 SSE 事件层：`reasoning_content` 每来一块就发一条 `thinking`（增量语义 ——
     `message` 是**这一块的增量文本**，前端拼接展示，不是整段重发）；`content`
     只在流结束（收到 `finish_reason`）后才整体拼完再解析，**解析成功才发
     `action_plan`**——半个 JSON 不能拿去执行（受控层是唯一执行者，喂它半份清单
     没有任何好处，只有误执行风险）。

**推理预算判据（写进代码防止下一次调参重踩，判据来自真流式实测）**：
DeepSeek 是推理模型，`max_tokens` 是「推理 + 正文」合计预算。流式下 DeepSeek 在最后一个
chunk 上报 `finish_reason` + `usage.completion_tokens_details.reasoning_tokens`，这给了
一个可判定的信号：
  - `finish_reason == "length"`：本次响应被 token 限额截断 —— **正文可能因此完全没
    写出来，也可能写到一半（半个 JSON）**。这是「预算被推理吃光/正文被截断」，
    **不是模型没话说**。判到这个信号，驱动自动重试一次（`max_tokens` 翻 4 倍，
    封顶 `_BUDGET_RETRY_MAX_TOKENS_CAP`），重试仍不行才终态降级，降级消息里点名
    `reasoning_tokens`/`finish_reason=length`，不含糊说"AI 不可用"。
  - `finish_reason != "length"`（一般是 `"stop"`）但正文仍为空：**模型这次真的没有
    产出正文**，这与预算耗尽是两件不同的事，消息里明确写「非预算截断」，不重试
    （重试对"模型确实没话说"这件事没有帮助，只会多打一次 API）。
  - 正文非空但解析不出动作清单（非 JSON / markdown 围栏剥完仍不是期望形状）：走
    与 codex 共用的 `action_parsing.parse_actions_json`，失败一律降级，不崩。

**别假设 OpenAI 兼容 API 一定守 `response_format: json_object` schema**——用了它
（实测流式 + `response_format` 可以同时用，DeepSeek 不报错），但解析仍走共用解析器，解析失败一律降级。
"""
from __future__ import annotations

import json
from typing import Any, Iterator, NamedTuple

import httpx

from .action_parsing import parse_actions_json
from .config import Config
from .driver_base import PlannerDriver
from .planning_prompt import build_action_prompt
from .streaming import StreamEvent

# 预算耗尽自动重试的策略：只重试一次，倍数与封顶都是防止重试本身失控地
# 把一次规划拖成天价调用。倍数选 4 是因为实测 20→200（10 倍）才够，
# 给点余量但不无限翻。
_BUDGET_RETRY_MULTIPLIER = 4
_BUDGET_RETRY_MAX_TOKENS_CAP = 8000


class _Outcome(NamedTuple):
    """`_run_once` 的终态（generator 的返回值，经 `yield from` 交给 `plan`）。"""

    kind: str  # "ok" | "budget_exhausted" | "error"
    actions: list[dict[str, Any]] | None = None
    message: str = ""
    reasoning_tokens: int | None = None


class DeepSeekDriver(PlannerDriver):
    """DeepSeek `/chat/completions`（OpenAI 兼容，流式）具体实现。"""

    def __init__(
        self,
        config: Config,
        *,
        guide_text: str,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._cfg = config
        self._guide = guide_text
        # `transport` 而不是接受一个现成的 `httpx.Client`：Authorization 头必须由本
        # 驱动自己从 config 组出来，不能靠调用方（包括测试）另行传入的 client 代传 ——
        # 否则「驱动真的会把 key 放进请求头」这件事就测不到，只测到了调用方的行为。
        #
        # trust_env=False：与 nexus_client.py 同款理由 —— 宿主 shell 常配 SOCKS 代理
        # 供上网用，httpx 默认 trust_env=True 会在构造期就去枚举代理环境变量，缺
        # socksio 时直接 ImportError；这与 DeepSeek 端点是不是走代理毫无关系，
        # 不该让「装配这个驱动」这一步无端炸穿。
        self._http = httpx.Client(
            base_url=config.deepseek_base.rstrip("/"),
            timeout=config.deepseek_timeout_s,
            trust_env=False,
            headers={"Authorization": f"Bearer {config.deepseek_api_key}"},
            transport=transport,
        )

    def plan(self, schedule: dict[str, Any], goal: str) -> Iterator[StreamEvent]:
        prompt = build_action_prompt(self._guide, schedule, goal)
        max_tokens = self._cfg.deepseek_max_tokens
        retried = False

        while True:
            outcome = yield from self._run_once(prompt, max_tokens)

            if outcome.kind == "ok":
                actions = outcome.actions or []
                yield StreamEvent(
                    type="action_plan",
                    message=f"AI 产出 {len(actions)} 条动作",
                    data={"actions": actions},
                )
                return

            if (
                outcome.kind == "budget_exhausted"
                and not retried
                and max_tokens < _BUDGET_RETRY_MAX_TOKENS_CAP
            ):
                retried = True
                new_max_tokens = min(
                    max_tokens * _BUDGET_RETRY_MULTIPLIER, _BUDGET_RETRY_MAX_TOKENS_CAP
                )
                # 这不是模型的推理片段，是驱动自身的重试narration——仍归 thinking
                # 类型（F-AI-5「边想边报」），因为它跟"正在想"一样是**过程中**的
                # 状态叙述，不是终态；终态失败才用 degraded（否则 run_planning 会把
                # 这次成功的规划错误标成"降级结束"，见 service.py::run_planning 的
                # `degraded` 标志一旦置真不会复位）。
                yield StreamEvent(
                    type="thinking",
                    message=(
                        f"（系统）推理预算耗尽（reasoning_tokens="
                        f"{outcome.reasoning_tokens}/{max_tokens}，finish_reason=length）"
                        f"——自动重试一次：max_tokens {max_tokens}→{new_max_tokens}"
                    ),
                )
                max_tokens = new_max_tokens
                continue

            # 终态失败：预算问题重试过仍不行，或者压根不是预算问题。
            message = outcome.message
            if outcome.kind == "budget_exhausted":
                # outcome.message 本身不断言"是否重试过"——那要看到这里才知道
                # （例如 max_tokens 一上来就 >= 封顶，压根不会进重试分支）。
                # 补一句真实的重试状态，不许说了没做过的事。
                message += (
                    "；已自动重试（max_tokens 翻倍）仍未恢复，建议调大"
                    " AI_PLANNER_DEEPSEEK_MAX_TOKENS"
                    if retried
                    else f"；未重试（max_tokens={max_tokens} 已达或超过重试封顶"
                    f" {_BUDGET_RETRY_MAX_TOKENS_CAP}），建议调大"
                    " AI_PLANNER_DEEPSEEK_MAX_TOKENS"
                )
            yield StreamEvent(type="degraded", message=message)
            return

    def _run_once(
        self, prompt: str, max_tokens: int
    ) -> Iterator[StreamEvent]:  # 实际是 Generator[..., _Outcome]
        """发一次流式请求，边收 `reasoning_content` delta 边发 `thinking`；
        `content` delta 只攒不发，流结束才整体拼、解析。返回 `_Outcome`。
        """
        body = dict(
            model=self._cfg.deepseek_model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
            stream=True,
        )

        try:
            with self._http.stream("POST", "/chat/completions", json=body) as resp:
                if resp.status_code != 200:
                    resp.read()
                    return _Outcome(
                        "error",
                        message=(
                            f"AI 规划暂不可用：DeepSeek 非 200（{resp.status_code}）"
                        ),
                    )

                content_parts: list[str] = []
                # 见下方"reasoning 兜底"注释：真流式实测发现的第三个坑，同样要攒起来。
                reasoning_parts: list[str] = []
                finish_reason: str | None = None
                reasoning_tokens: int | None = None

                for line in resp.iter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[len("data:") :].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(chunk, dict):
                        continue

                    choices = chunk.get("choices")
                    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
                        choice0 = choices[0]
                        delta = choice0.get("delta")
                        if isinstance(delta, dict):
                            reasoning_delta = delta.get("reasoning_content")
                            if isinstance(reasoning_delta, str) and reasoning_delta:
                                reasoning_parts.append(reasoning_delta)
                                # 增量语义：message = 这一块新增的文本，不是整段重发
                                yield StreamEvent(type="thinking", message=reasoning_delta)
                            content_delta = delta.get("content")
                            if isinstance(content_delta, str) and content_delta:
                                content_parts.append(content_delta)
                        fr = choice0.get("finish_reason")
                        if isinstance(fr, str):
                            finish_reason = fr

                    usage = chunk.get("usage")
                    if isinstance(usage, dict):
                        details = usage.get("completion_tokens_details")
                        if isinstance(details, dict):
                            rt = details.get("reasoning_tokens")
                            if isinstance(rt, int):
                                reasoning_tokens = rt
        except httpx.TimeoutException:
            return _Outcome(
                "error",
                message=f"AI 规划暂不可用：DeepSeek 超时（>{self._cfg.deepseek_timeout_s}s）",
            )
        except httpx.RequestError as exc:
            return _Outcome(
                "error", message=f"AI 规划暂不可用：DeepSeek 请求失败（{exc}）"
            )

        content = "".join(content_parts)
        actions = parse_actions_json(content) if content.strip() else None

        if actions is None:
            # 真流式实测坑（真调 api.deepseek.com 撞见）：这个模型/
            # 网关组合在 stream=true + response_format=json_object 时，偶尔会把最终
            # 结构化答案整段写进 reasoning_content，content 留空、finish_reason 仍是
            # "stop"（模型认为自己正常结束，不是被截断）。用同一份解析器对 reasoning
            # 全文兜底尝试一次——校验标准与 content 完全一致（必须是合法 JSON 且形状
            # 匹配 action_schema），不是"信任推理文本的措辞"，只是换一个信源去解析，
            # 解析不出来照样走后面的降级分支。
            reasoning_text = "".join(reasoning_parts)
            if reasoning_text.strip():
                actions = parse_actions_json(reasoning_text)

        if actions is not None:
            return _Outcome("ok", actions=actions)

        if finish_reason == "length":
            # 预算耗尽判据命中：无论正文完全空还是写到一半解析不出来，都是同一件事——
            # token 限额把这次响应截断了，不是模型没话说。
            return _Outcome(
                "budget_exhausted",
                reasoning_tokens=reasoning_tokens,
                message=(
                    "AI 规划暂不可用：DeepSeek 推理预算耗尽（finish_reason=length，"
                    f"reasoning_tokens={reasoning_tokens}/{max_tokens}）—— 这不是模型"
                    "没话说，是 max_tokens（推理+正文合计预算）被推理吃光或正文被截断"
                ),
            )

        if not content.strip():
            return _Outcome(
                "error",
                message=(
                    f"AI 规划暂不可用：DeepSeek 正文为空（finish_reason="
                    f"{finish_reason!r}，非预算截断 —— 模型本轮确实没有产出正文，"
                    "与 max_tokens 预算无关）"
                ),
            )

        return _Outcome(
            "error",
            message="AI 规划暂不可用：DeepSeek 输出无法解析为动作清单",
        )
