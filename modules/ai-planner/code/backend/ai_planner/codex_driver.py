"""codex 驱动适配器 —— O2 选型：**codex 提议，受控层裁夺**。

选型理由（写进 module_docs/contract.md 的「codex 驱动形态」节，此处呼应）：
codex 是有 shell 的通用 agent，给它 MCP 工具去直接写 nexus，就必须靠 codex sandbox
把它的 shell 网络封死才安全，而 sandbox 配置难以确定性回归。改用**结构化输出**：
codex 跑在 `--sandbox read-only`、无任何触达 nexus 的工具，只对**喂进 prompt 的日程文本**
推理，产出一份 JSON 动作清单；受控工具层是**唯一执行者**。安全边界因此落在我们写的
Python（可测），codex 物理上碰不到 events、伪造不了 actor、执行不了高风险写。

`mcp-server` 常驻方案留作备选（需 codex 侧 MCP 工具白名单 + 沙盒禁网），本轮不采用。

降级（F-AI-6）：codex 缺失 / 超时 / 非零退出 —— 产一条 degraded 事件、无动作，
**绝不抛给主流程**。待办区规则层没有 AI 照常跑。
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any, Iterator

from .action_parsing import parse_actions_json
from .config import Config
from .driver_base import PlannerDriver
from .planning_prompt import build_action_prompt
from .streaming import StreamEvent

_ACTION_SCHEMA_PATH = Path(__file__).with_name("action_schema.json")

# 向后兼容别名：本模块曾经是驱动接口唯一定义处，`ABC` 本体已搬到 driver_base.py
# （驱动可插拔后，接口不该挂在某一个具体驱动的文件名下）。旧引用名继续可用。
CodexDriver = PlannerDriver


class CodexExecDriver(CodexDriver):
    """`codex exec --json` 具体实现，复用宿主 ChatGPT 登录态（免 API key）。"""

    def __init__(self, config: Config, *, guide_text: str) -> None:
        self._cfg = config
        self._guide = guide_text

    def _argv(self, prompt: str, out_file: str) -> list[str]:
        argv = [
            self._cfg.codex_bin, "exec",
            "--json",
            "--skip-git-repo-check",
            "--sandbox", "read-only",
            "--output-schema", str(_ACTION_SCHEMA_PATH),
            "-o", out_file,
        ]
        if self._cfg.codex_model:
            argv += ["-m", self._cfg.codex_model]
        argv.append(prompt)
        return argv

    def plan(self, schedule: dict[str, Any], goal: str) -> Iterator[StreamEvent]:
        prompt = build_action_prompt(self._guide, schedule, goal)
        out_file = os.path.join("/tmp", f"ai_planner_codex_{os.getpid()}.json")
        argv = self._argv(prompt, out_file)
        try:
            proc = subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        except FileNotFoundError:
            yield StreamEvent(
                type="degraded",
                message="AI 规划暂不可用：未找到 codex 可执行文件",
            )
            return

        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                narration = _narrate(line)
                if narration:
                    yield StreamEvent(type="thinking", message=narration)
            proc.wait(timeout=self._cfg.codex_timeout_s)
        except subprocess.TimeoutExpired:
            proc.kill()
            yield StreamEvent(
                type="degraded",
                message=f"AI 规划暂不可用：codex 超时（>{self._cfg.codex_timeout_s}s）",
            )
            return

        if proc.returncode != 0:
            yield StreamEvent(
                type="degraded",
                message=f"AI 规划暂不可用：codex 非零退出（{proc.returncode}）",
            )
            return

        actions = _read_actions(out_file)
        if actions is None:
            yield StreamEvent(
                type="degraded",
                message="AI 规划暂不可用：codex 输出无法解析为动作清单",
            )
            return
        yield StreamEvent(
            type="action_plan",
            message=f"AI 产出 {len(actions)} 条动作",
            data={"actions": actions},
        )


class StubDriver(CodexDriver):
    """确定性桩驱动 —— 单测与「codex 接不通」时的兜底，不依赖外部进程。"""

    def __init__(self, actions: list[dict[str, Any]] | None = None) -> None:
        self._actions = actions or []

    def plan(self, schedule: dict[str, Any], goal: str) -> Iterator[StreamEvent]:
        yield StreamEvent(type="thinking", message="（stub）读了日程，开始规划")
        yield StreamEvent(
            type="action_plan",
            message=f"（stub）产出 {len(self._actions)} 条动作",
            data={"actions": list(self._actions)},
        )


def _narrate(json_line: str) -> str:
    """把 codex 的 --json 事件行转成一句可读叙述；无关行返回空串。"""
    try:
        evt = json.loads(json_line)
    except json.JSONDecodeError:
        return ""
    # codex --json 的事件形状随版本变化，这里做宽松提取，只为流式叙述
    msg = evt.get("msg") if isinstance(evt, dict) else None
    if isinstance(msg, dict):
        t = msg.get("type", "")
        if t in ("agent_reasoning", "agent_reasoning_delta"):
            return str(msg.get("text") or msg.get("delta") or "").strip()
        if t in ("agent_message", "agent_message_delta"):
            return str(msg.get("message") or msg.get("delta") or "").strip()
    return ""


def _read_actions(out_file: str) -> list[dict[str, Any]] | None:
    try:
        with open(out_file, encoding="utf-8") as fh:
            raw = fh.read()
    except OSError:
        return None
    return parse_actions_json(raw)
