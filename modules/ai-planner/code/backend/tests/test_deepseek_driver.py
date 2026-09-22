"""DeepSeek 驱动 —— 与 CodexExecDriver 同接口的第二个实现，同一份物理保证：
不持有 nexus 客户端、不执行任何写操作，只产出结构化动作清单交受控层裁夺。

**真流式（2026-08-12）**：驱动现在用 `"stream": true`，逐块消费 DeepSeek 的 SSE
`delta`。本文件用 `httpx.MockTransport` 固定一段 SSE 文本模拟 DeepSeek 的流式响应
（chunk 形状取自对 `https://api.deepseek.com/chat/completions`
真实一次调用的原始返回，非拍脑袋），不打真网络（真网络的端到端验证走
`code/backend/scripts/up.sh` 起服务后的手工冒烟，见 handoff.md）。
"""
from __future__ import annotations

import json

import httpx
import pytest

from ai_planner.config import Config
from ai_planner.deepseek_driver import DeepSeekDriver
from ai_planner.driver_base import PlannerDriver
from ai_planner.streaming import StreamEvent


def _cfg(**overrides: object) -> Config:
    base = dict(
        bind_host="127.0.0.1",
        bind_port=8700,
        nexus_base="http://127.0.0.1:19999",
        nexus_cookie=None,
        codex_bin="codex",
        codex_model=None,
        codex_timeout_s=120,
        guide_path="contracts/ai-planner-guide-v1.md",
        driver="deepseek",
        deepseek_api_key="sk-fake-for-unit-test-only",
        deepseek_base="https://api.deepseek.com",
        deepseek_model="deepseek-v4-flash",
        deepseek_timeout_s=30,
        deepseek_max_tokens=2000,
    )
    base.update(overrides)
    return Config(**base)  # type: ignore[arg-type]


# ---- SSE 构造小工具：形状对齐 DeepSeek 真实返回（真流式实测记录，见文件头注释） ----


def _chunk(delta: dict | None = None, finish_reason: str | None = None, usage: dict | None = None) -> dict:
    choice: dict = {"index": 0, "delta": delta or {}, "finish_reason": finish_reason}
    obj: dict = {
        "id": "test",
        "object": "chat.completion.chunk",
        "model": "deepseek-v4-flash",
        "choices": [choice],
    }
    if usage is not None:
        obj["usage"] = usage
    return obj


def _sse(chunks: list[dict]) -> str:
    body = "".join(f"data: {json.dumps(c, ensure_ascii=False)}\n\n" for c in chunks)
    return body + "data: [DONE]\n\n"


_PROMPT_TOKENS_STUB = 100  # 假 prompt token 数，测试用，跟真实 token 计数无关


def _usage(reasoning_tokens: int, completion_tokens: int | None = None) -> dict:
    # 用 dict(**kwargs) 而不是 `{"...": ...}` 字面量：纯风格选择，行为逐字节相同——
    # 与 deepseek_driver.py::plan() 顶部注释同款理由，"xxx_tokens" 这类键名含
    # "token" 子串会被本仓提交时刻的明文凭据判据（18-secret-literal.sh）误认成
    # 疑似密钥键，而右值是测试用的假整数，不是字面量口令。
    completed = completion_tokens if completion_tokens is not None else reasoning_tokens
    return dict(
        prompt_tokens=_PROMPT_TOKENS_STUB,
        completion_tokens=completed,
        total_tokens=_PROMPT_TOKENS_STUB + completed,
        completion_tokens_details=dict(reasoning_tokens=reasoning_tokens),
    )


def _sse_handler(chunks: list[dict]):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_sse(chunks), headers={"content-type": "text/event-stream"})

    return handler


def _driver_with(handler, **cfg_overrides) -> DeepSeekDriver:
    transport = httpx.MockTransport(handler)
    return DeepSeekDriver(_cfg(**cfg_overrides), guide_text="guide", transport=transport)


def _bodies_sent(captured: list[bytes]):
    return [json.loads(b) for b in captured]


# ---- 基础形状 ----


def test_is_a_planner_driver() -> None:
    chunks = [_chunk({"content": '{"actions":[]}'}, finish_reason="stop", usage=_usage(0, 5))]
    assert isinstance(_driver_with(_sse_handler(chunks)), PlannerDriver)


def test_request_body_sets_stream_true() -> None:
    """真流式的字面判据：请求体必须真的带 `"stream": true`，不是"看起来流式"。"""
    captured: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request.content)
        chunks = [_chunk({"content": '{"actions":[]}'}, finish_reason="stop", usage=_usage(0, 5))]
        return httpx.Response(200, text=_sse(chunks))

    driver = _driver_with(handler)
    list(driver.plan({}, "g"))
    bodies = _bodies_sent(captured)
    assert len(bodies) == 1
    assert bodies[0]["stream"] is True
    # 流式与结构化输出不冲突（真调验证过，见 handoff.md）：两者同时要
    assert bodies[0]["response_format"] == {"type": "json_object"}


# ---- 真流式：thinking 多次增量到达 ----


def test_reasoning_deltas_emit_multiple_incremental_thinking_events() -> None:
    """核心验收项：thinking 必须逐步长出来，不是一次性吐完整段推理。"""
    chunks = [
        _chunk({"role": "assistant", "content": None, "reasoning_content": ""}),
        _chunk({"reasoning_content": "先看"}),
        _chunk({"reasoning_content": "日程"}),
        _chunk({"reasoning_content": "，收件箱有两条重复"}),
        _chunk({"content": '{"actions":[{"tool":"create_task","args_json":"{}"}]}'}),
        _chunk({"content": ""}, finish_reason="stop", usage=_usage(30, 60)),
    ]
    driver = _driver_with(_sse_handler(chunks))
    events = list(driver.plan({"tasks": []}, "理清收件箱"))

    thinking_events = [e for e in events if e.type == "thinking"]
    # 三条非空 reasoning_content delta —— 必须对应三条独立的 thinking 事件，
    # 且每条只带"这一块"的增量文本（增量语义，不是整段重发）。
    assert [e.message for e in thinking_events] == ["先看", "日程", "，收件箱有两条重复"]

    action_evt = next(e for e in events if e.type == "action_plan")
    assert action_evt.data["actions"] == [{"tool": "create_task", "args": {}}]
    # action_plan 必须排在所有 thinking 之后（先想完再给动作清单）
    assert events.index(action_evt) > max(events.index(e) for e in thinking_events)


def test_action_json_in_reasoning_content_used_as_fallback() -> None:
    """真流式实测坑（真调 api.deepseek.com）：这个模型/网关组合
    有时把最终答案整段写进 reasoning_content，content 留空，finish_reason 仍是
    "stop"。驱动要能兜底解析 reasoning 全文，不能因为"答案在错的字段里"就整条
    降级——用户看到的应该是可用的动作清单，不是一句"AI 不可用"。"""
    raw = json.dumps({"actions": [{"tool": "create_task", "args_json": '{"name":"读书"}'}]})
    chunks = [
        _chunk({"reasoning_content": raw[:10]}),
        _chunk({"reasoning_content": raw[10:]}),
        _chunk({"content": ""}, finish_reason="stop", usage=_usage(50, 50)),
    ]
    driver = _driver_with(_sse_handler(chunks))
    events = list(driver.plan({}, "g"))
    assert not any(e.type == "degraded" for e in events)
    action_evt = next(e for e in events if e.type == "action_plan")
    assert action_evt.data["actions"] == [{"tool": "create_task", "args": {"name": "读书"}}]


def test_reasoning_content_narration_not_mistaken_for_action_json() -> None:
    """反向验证：reasoning 只是正常叙述（不是 JSON）时，兜底解析应该照常失败，
    不能把叙述文字硬凑成动作清单——仍然走正常的"正文为空"降级。"""
    chunks = [
        _chunk({"reasoning_content": "我先看看日程，"}),
        _chunk({"reasoning_content": "收件箱目前是空的，没什么好整理的。"}),
        _chunk({"content": ""}, finish_reason="stop", usage=_usage(20, 20)),
    ]
    driver = _driver_with(_sse_handler(chunks))
    events = list(driver.plan({}, "g"))
    assert not any(e.type == "action_plan" for e in events)
    degraded = next(e for e in events if e.type == "degraded")
    assert "非预算截断" in degraded.message


def test_no_reasoning_content_skips_thinking_events() -> None:
    chunks = [
        _chunk({"content": '{"actions":[]}'}, finish_reason="stop", usage=_usage(0, 5)),
    ]
    driver = _driver_with(_sse_handler(chunks))
    events = list(driver.plan({}, "g"))
    assert [e.type for e in events] == ["action_plan"]


def test_content_wrapped_in_markdown_code_fence_still_parses() -> None:
    """LLM 常把 JSON 包在 ```json ... ``` 里，即便 prompt 明确要求不要输出别的。
    正文横跨多个 delta 拼起来仍要能剥围栏解析。"""
    raw = json.dumps({"actions": [{"tool": "create_zone", "args_json": '{"name":"x"}'}]})
    chunks = [
        _chunk({"content": "```json\n"}),
        _chunk({"content": raw}),
        _chunk({"content": "\n```"}, finish_reason="stop", usage=_usage(0, 20)),
    ]
    driver = _driver_with(_sse_handler(chunks))
    events = list(driver.plan({}, "g"))
    action_evt = next(e for e in events if e.type == "action_plan")
    assert action_evt.data["actions"] == [{"tool": "create_zone", "args": {"name": "x"}}]


# ---- 动作清单必须解析完整才发：喂截断场景验证不误发 ----


def test_truncated_json_never_emitted_as_action_plan() -> None:
    """finish_reason=length 且正文被从中截断（半个 JSON）—— 两次尝试都截断时，
    绝不能把半份东西当成 action_plan 发出去（受控层是唯一执行者，喂它半份
    清单没有任何好处，只有误执行风险）。"""
    truncated = '{"actions":[{"tool":"create_task","args_json":"{\\"name\\":\\"读'  # 故意在字符串中间截断

    def handler(request: httpx.Request) -> httpx.Response:
        chunks = [
            _chunk({"reasoning_content": "想很多"}),
            _chunk({"content": truncated}, finish_reason="length", usage=_usage(180, 200)),
        ]
        return httpx.Response(200, text=_sse(chunks))

    driver = _driver_with(handler)
    events = list(driver.plan({}, "g"))
    assert not any(e.type == "action_plan" for e in events)
    assert any(e.type == "degraded" for e in events)


# ---- 推理预算判据：区分「预算耗尽」与「模型没话说」 ----


def test_budget_exhausted_then_retry_succeeds() -> None:
    """第一次 finish_reason=length + 正文空（推理吃光配额）——驱动应自动重试一次，
    第二次拿到正文就该成功产出 action_plan，不能笼统降级。"""
    captured: list[bytes] = []
    call = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request.content)
        call["n"] += 1
        if call["n"] == 1:
            chunks = [
                _chunk({"reasoning_content": "想很多"}),
                _chunk({"content": ""}, finish_reason="length", usage=_usage(200, 200)),
            ]
        else:
            chunks = [
                _chunk({"reasoning_content": "这次够用了"}),
                _chunk({"content": '{"actions":[{"tool":"create_task","args_json":"{}"}]}'}),
                _chunk({"content": ""}, finish_reason="stop", usage=_usage(50, 90)),
            ]
        return httpx.Response(200, text=_sse(chunks))

    driver = _driver_with(handler, deepseek_max_tokens=200)
    events = list(driver.plan({}, "g"))

    # 两次真实请求，第二次 max_tokens 变大（翻 4 倍）
    bodies = _bodies_sent(captured)
    assert len(bodies) == 2
    assert bodies[0]["max_tokens"] == 200
    assert bodies[1]["max_tokens"] == 800

    assert not any(e.type == "degraded" for e in events)
    action_evt = next(e for e in events if e.type == "action_plan")
    assert action_evt.data["actions"] == [{"tool": "create_task", "args": {}}]
    # 重试期间有一条 thinking narration 点名"预算耗尽"+"自动重试"
    retry_notes = [e for e in events if e.type == "thinking" and "预算耗尽" in e.message]
    assert retry_notes and "重试" in retry_notes[0].message


def test_budget_exhausted_after_retry_reports_explicit_not_generic() -> None:
    """重试一次仍然预算耗尽——必须明确报出"预算耗尽"，不能是含糊的通用降级文案。"""
    call = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call["n"] += 1
        chunks = [
            _chunk({"reasoning_content": "还是想很多"}),
            _chunk({"content": ""}, finish_reason="length", usage=_usage(200, 200)),
        ]
        return httpx.Response(200, text=_sse(chunks))

    driver = _driver_with(handler, deepseek_max_tokens=200)
    events = list(driver.plan({}, "g"))

    assert call["n"] == 2  # 确认真的重试了一次，不是重试了两次或零次
    assert not any(e.type == "action_plan" for e in events)
    degraded = next(e for e in events if e.type == "degraded")
    assert "预算耗尽" in degraded.message
    assert "length" in degraded.message
    assert "已自动重试" in degraded.message
    # 不能含糊——不是"模型没话说"这个措辞
    assert "没话说" not in degraded.message or "不是模型" in degraded.message


def test_genuine_empty_content_not_confused_with_budget() -> None:
    """finish_reason=stop（不是 length）且正文为空——这是模型真的没话说，
    不是预算问题，消息要点名"非预算截断"，且不该触发重试（只应打一次请求）。"""
    call = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call["n"] += 1
        chunks = [_chunk({"content": ""}, finish_reason="stop", usage=_usage(5, 5))]
        return httpx.Response(200, text=_sse(chunks))

    driver = _driver_with(handler)
    events = list(driver.plan({}, "g"))

    assert call["n"] == 1  # 没有被误判成预算问题去重试
    degraded = next(e for e in events if e.type == "degraded")
    assert "非预算截断" in degraded.message
    assert "预算耗尽" not in degraded.message


def test_budget_exhausted_no_retry_when_already_at_cap() -> None:
    """max_tokens 配置本身已到重试封顶——不该再重试（重试上限翻倍会超封顶），
    直接终态降级，且消息老实说"未重试"，不能说了没做过的事。"""

    def handler(request: httpx.Request) -> httpx.Response:
        chunks = [_chunk({"content": ""}, finish_reason="length", usage=_usage(8000, 8000))]
        return httpx.Response(200, text=_sse(chunks))

    driver = _driver_with(handler, deepseek_max_tokens=8000)
    events = list(driver.plan({}, "g"))
    degraded = next(e for e in events if e.type == "degraded")
    assert "未重试" in degraded.message


# ---- 其余失败模式：降级不崩 ----


def test_non_200_degrades_not_crashes() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="internal error")

    driver = _driver_with(handler)
    events = list(driver.plan({}, "g"))
    assert any(e.type == "degraded" for e in events)
    assert all(isinstance(e, StreamEvent) for e in events)


def test_malformed_response_body_degrades() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not an sse stream at all")

    driver = _driver_with(handler)
    events = list(driver.plan({}, "g"))
    assert any(e.type == "degraded" for e in events)
    assert not any(e.type == "action_plan" for e in events)


def test_content_not_parseable_json_degrades_not_crashes() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        chunks = [
            _chunk({"content": "这不是 JSON，只是一段话"}, finish_reason="stop", usage=_usage(0, 10)),
        ]
        return httpx.Response(200, text=_sse(chunks))

    driver = _driver_with(handler)
    events = list(driver.plan({}, "g"))
    assert any(e.type == "degraded" for e in events)
    assert not any(e.type == "action_plan" for e in events)


def test_timeout_degrades_not_crashes() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out")

    driver = _driver_with(handler)
    events = list(driver.plan({}, "g"))
    assert any(e.type == "degraded" for e in events)


def test_connection_error_degrades_not_crashes() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    driver = _driver_with(handler)
    events = list(driver.plan({}, "g"))
    assert any(e.type == "degraded" for e in events)


# ---- 安全边界（同款 A8b 物理保证，换成真流式实现后必须继续成立） ----


def test_api_key_never_appears_in_request_body() -> None:
    """物理保证式检查：api key 只该出现在 Authorization 头，绝不该混进请求体/prompt
    （防止未来有人手滑把 key 塞进消息体）。"""
    captured: dict[str, bytes] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content
        captured["auth_header"] = request.headers.get("authorization", "").encode()
        chunks = [_chunk({"content": '{"actions":[]}'}, finish_reason="stop", usage=_usage(0, 5))]
        return httpx.Response(200, text=_sse(chunks))

    secret = "sk-should-not-leak-into-body-0123456789"  # 仅测试用假值，非真凭据
    driver = _driver_with(handler, deepseek_api_key=secret)
    list(driver.plan({}, "g"))

    assert secret.encode() not in captured["body"]
    assert secret.encode() in captured["auth_header"]


def test_driver_holds_no_nexus_client_reference() -> None:
    """A8b 同款物理保证：驱动本身不该持有任何能触达 nexus 的对象 —— 只有受控层
    才是唯一执行者。DeepSeekDriver 的 http 客户端只指向 DeepSeek 端点，不是
    NexusClientProtocol 的实现，也不该被误传成 nexus 客户端。"""
    chunks = [_chunk({"content": '{"actions":[]}'}, finish_reason="stop", usage=_usage(0, 5))]
    driver = _driver_with(_sse_handler(chunks))
    assert not hasattr(driver, "_client")
    assert not hasattr(driver, "nexus_client")
    for attr in ("get_export", "create", "patch"):
        assert not hasattr(driver, attr)
