"""流式事件模型（F-AI-5：边想边报）。

驱动产出一串 StreamEvent，服务层用 SSE 逐条推给前端，不憋十几秒一次性吐。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal

EventType = Literal[
    "thinking",     # codex 的推理/叙述片段
    "action_plan",  # codex 产出的结构化动作清单（最终一条）
    "executed",     # 受控层执行了一条低风险写
    "proposal",     # 受控层产出一条高风险提议
    "rejected",     # 白名单外动作被拒（纵深防御命中）
    "degraded",     # AI 不可用 —— 明确提示，不拖垮主栈（F-AI-6）
    "done",         # 本次规划结束
]


@dataclass
class StreamEvent:
    type: EventType
    message: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    def to_sse(self) -> str:
        payload = {"type": self.type, "message": self.message, "data": self.data}
        return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
