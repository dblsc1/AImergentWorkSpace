"""动作清单 JSON 解析 —— codex/deepseek 两个驱动共用一份解析逻辑。

解析「LLM 产出的动作清单 JSON」这件事只应该有一份
实现，写两份迟早分叉（一个驱动修了坑，另一个驱动原样躺着）。两个驱动都只做
「拿到一段原始文本 → 拿到规范化的 `[{"tool":..., "args": {...}}]` 或 None」，
差异只在文本从哪来（codex 是文件、deepseek 是 HTTP 响应体的 message.content）。

解析失败一律返回 None，调用方据此产 `degraded` 事件 —— 不抛异常、不崩主流程。
"""
from __future__ import annotations

import json
import re
from typing import Any

# LLM（尤其是聊天式模型）常把 JSON 包在 ```json ... ``` 代码块围栏里，
# 即便 prompt 明确要求「不要输出别的」。宽进剥掉围栏，解析失败才降级，不因为
# 多了几个反引号就整条动作清单作废。
_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def strip_code_fence(text: str) -> str:
    """剥掉可能包着 JSON 的 markdown 代码块围栏；没有围栏则原样返回（去首尾空白）。"""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = _FENCE_RE.sub("", stripped).strip()
    return stripped


def parse_actions_json(raw: str) -> list[dict[str, Any]] | None:
    """把一段可能带 markdown 围栏的 JSON 文本解析成规范化动作清单。

    期望形状：`{"actions":[{"tool":"...", "args_json":"..."}]}`（strict schema 约束下
    `args_json` 是把参数编码成的字符串，见 action_schema.json 的注释）；也宽进接受
    直接给 `args`（dict）的形状，兼容非 strict-schema 的模型（如 DeepSeek）。

    解析失败（非 JSON / 形状不对）返回 None，调用方负责降级，不在这里抛异常。
    """
    if not raw or not raw.strip():
        return None
    text = strip_code_fence(raw)
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        return None
    actions = obj.get("actions") if isinstance(obj, dict) else None
    if not isinstance(actions, list):
        return None
    normalized: list[dict[str, Any]] = []
    for a in actions:
        if not isinstance(a, dict) or "tool" not in a:
            continue
        args = a.get("args", {})
        # schema 让模型用 args_json（strict schema 不允许自由 object），此处解回 dict
        if "args_json" in a and isinstance(a["args_json"], str):
            try:
                args = json.loads(a["args_json"] or "{}")
            except json.JSONDecodeError:
                args = {}
        if not isinstance(args, dict):
            args = {}
        normalized.append({"tool": a["tool"], "args": args})
    return normalized
