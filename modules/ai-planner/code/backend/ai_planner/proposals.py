"""高风险提议对象 —— 受控层对 move/reschedule/delete **只产提议，不执行**（F-API-3）。

一个 Proposal 是一个纯数据对象：它描述「AI 建议做什么」，交 UI 逐条展示，
用户点确认后由前端正常路径（actor=human）直接调 nexus CRUD。
本对象**没有任何执行方法、不持有 nexus 客户端引用** —— 这是 A8b 的物理保证：
从 Proposal 到一次 CRUD 写，代码里根本没有路径。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

HighRiskKind = Literal["move", "reschedule", "delete"]
TargetType = Literal["zones", "projects", "tasks"]


class Proposal(BaseModel):
    """一条高风险提议。UI 逐条展示，人确认后走 actor=human 路径执行。"""

    model_config = {"frozen": True}

    id: str = Field(default_factory=lambda: f"prop_{uuid.uuid4().hex[:12]}")
    kind: HighRiskKind
    target_type: TargetType
    target_id: str
    # 建议的改动（move: {"newParent": "..."}；reschedule: {"plan": {...}|None}；delete: {}）
    payload: dict[str, Any] = Field(default_factory=dict)
    reason: str
    # 提议由 AI 产生，永远打 ai 标记；确认执行时前端另发 actor=human
    proposed_by: Literal["ai"] = "ai"
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_ui(self) -> dict[str, Any]:
        """给前端逐条展示用的形状。"""
        return self.model_dump()
