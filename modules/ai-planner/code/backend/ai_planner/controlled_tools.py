"""受控工具层 —— 本模块的**核心安全边界**（不是 codex）。

对齐 `contracts/ai-planner-guide-v1.md` §3 的白名单，逐条映射 nexus planner CRUD。

三条物理保证（都由本层代码结构而非「LLM 自觉」兑现，机械可测）：
- **A8**：AI 只能调 WHITELIST 里的命令；events / timer / mongo / 凭据 / 裸 shell / 任意 HTTP
  在本层**根本没有对应方法**，dispatch 未知命令直接 raise。
- **A8c**：所有写命令由本层**注入 actor="ai"**（硬编码字面量），任何工具签名里
  **没有 actor 形参**，AI 无从带 actor="human"。
- **A8b**：高风险 move/reschedule/delete 的工具**只构造 Proposal，不接触 nexus 客户端**，
  从提议到一次 CRUD 写，本层没有代码路径。
"""
from __future__ import annotations

import inspect
from typing import Any, Callable, Literal

from .nexus_client import NexusClientProtocol
from .proposals import Proposal

Risk = Literal["read", "low", "high"]

# LLM 自然会说 "task" 而非 nexus 要的 "tasks"；在受控层边界做宽进（Postel）。
_TYPE_ALIASES = {
    "zone": "zones", "zones": "zones",
    "project": "projects", "projects": "projects",
    "task": "tasks", "tasks": "tasks",
}


def _normalize_type(type_: str) -> str:
    norm = _TYPE_ALIASES.get(str(type_).strip().lower())
    if norm is None:
        raise ToolNotAllowed(
            f"未知对象类型 {type_!r}（合法：zones/projects/tasks）"
        )
    return norm


class ToolNotAllowed(RuntimeError):
    """AI 试图调用白名单外的命令 —— 直接拒，不静默降级。"""


class ToolSpec:
    __slots__ = ("name", "risk", "handler", "summary")

    def __init__(self, name: str, risk: Risk, handler: Callable[..., Any], summary: str):
        self.name = name
        self.risk = risk
        self.handler = handler
        self.summary = summary


class ControlledToolLayer:
    """白名单命令面。AI（codex）产出的每个动作都必须过 dispatch()。"""

    # actor 永远是这个字面量，绝不来自入参（A8c）
    _AI_ACTOR = "ai"

    def __init__(self, client: NexusClientProtocol) -> None:
        self._client = client
        # 本次会话产生的提议（高风险动作只落这里，不执行）
        self.proposals: list[Proposal] = []
        self._registry: dict[str, ToolSpec] = self._build_registry()

    # ================= 白名单注册表（唯一事实源）=================
    def _build_registry(self) -> dict[str, ToolSpec]:
        specs = [
            # ---- 读（无风险）----
            ToolSpec("read_schedule", "read", self.read_schedule, "全量日程"),
            ToolSpec("read_next_actions", "read", self.read_next_actions, "待办区分类"),
            ToolSpec("read_review", "read", self.read_review, "每周回顾聚合"),
            # ---- 低风险写（actor=ai 直接落库）----
            ToolSpec("create_task", "low", self.create_task, "建任务"),
            ToolSpec("create_project", "low", self.create_project, "建项目"),
            ToolSpec("create_zone", "low", self.create_zone, "建分区"),
            ToolSpec("set_title", "low", self.set_title, "改显示名"),
            ToolSpec("set_weight", "low", self.set_weight, "调优先级权重"),
            ToolSpec("set_order", "low", self.set_order, "调分区排序"),
            # ---- 高风险（只产提议，不执行）----
            ToolSpec("propose_move", "high", self.propose_move, "提议搬移"),
            ToolSpec("propose_reschedule", "high", self.propose_reschedule, "提议改计划期"),
            ToolSpec("propose_delete", "high", self.propose_delete, "提议删除"),
        ]
        return {s.name: s for s in specs}

    @property
    def whitelist(self) -> frozenset[str]:
        return frozenset(self._registry)

    def describe(self) -> list[dict[str, str]]:
        """给 system prompt / 自省用的工具清单。"""
        return [
            {"name": s.name, "risk": s.risk, "summary": s.summary}
            for s in self._registry.values()
        ]

    def dispatch(self, name: str, args: dict[str, Any] | None = None) -> Any:
        """AI 动作的唯一入口 —— 白名单外一律拒绝（A8）。"""
        spec = self._registry.get(name)
        if spec is None:
            raise ToolNotAllowed(
                f"命令 {name!r} 不在受控白名单内（events/timer/mongo/shell 等均不暴露）"
            )
        return spec.handler(**(args or {}))

    # ================= 读 =================
    def read_schedule(self) -> dict[str, Any]:
        return self._client.get_export()

    def read_next_actions(self) -> dict[str, Any]:
        return self._client.get_next_actions()

    def read_review(self) -> dict[str, Any]:
        return self._client.get_review()

    # ================= 低风险写（每处硬注入 actor="ai"）=================
    def create_task(
        self,
        projectId: str,
        name: str,
        plannedWeight: float | None = None,
        kind: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"projectId": projectId, "name": name, "actor": self._AI_ACTOR}
        if plannedWeight is not None:
            body["plannedWeight"] = plannedWeight
        if kind is not None:
            body["kind"] = kind
        return self._client.create("tasks", body)

    def create_project(
        self, zoneId: str, name: str, plannedWeight: float | None = None
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"zoneId": zoneId, "name": name, "actor": self._AI_ACTOR}
        if plannedWeight is not None:
            body["plannedWeight"] = plannedWeight
        return self._client.create("projects", body)

    def create_zone(self, name: str, color: str | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {"name": name, "actor": self._AI_ACTOR}
        if color is not None:
            body["color"] = color
        return self._client.create("zones", body)

    def set_title(self, type: str, id: str, name: str) -> dict[str, Any]:
        return self._client.patch(
            _normalize_type(type), id, {"name": name, "actor": self._AI_ACTOR}
        )

    def set_weight(self, type: str, id: str, plannedWeight: float) -> dict[str, Any]:
        return self._client.patch(
            _normalize_type(type), id,
            {"plannedWeight": plannedWeight, "actor": self._AI_ACTOR},
        )

    def set_order(self, id: str, order: int) -> dict[str, Any]:
        return self._client.patch(
            "zones", id, {"order": order, "actor": self._AI_ACTOR}
        )

    # ================= 高风险（只产提议，绝不碰 self._client）=================
    def propose_move(
        self, type: str, id: str, newParent: str, reason: str
    ) -> Proposal:
        prop = Proposal(
            kind="move", target_type=_normalize_type(type), target_id=id,
            payload={"newParent": newParent}, reason=reason,
        )
        self.proposals.append(prop)
        return prop

    def propose_reschedule(
        self, type: str, id: str, plan: dict[str, Any] | None, reason: str
    ) -> Proposal:
        prop = Proposal(
            kind="reschedule", target_type=_normalize_type(type), target_id=id,
            payload={"plan": plan}, reason=reason,
        )
        self.proposals.append(prop)
        return prop

    def propose_delete(self, type: str, id: str, reason: str) -> Proposal:
        prop = Proposal(
            kind="delete", target_type=_normalize_type(type), target_id=id,
            payload={}, reason=reason,
        )
        self.proposals.append(prop)
        return prop

    # ================= 自省（供测试与保真核验）=================
    def signature_params(self, name: str) -> list[str]:
        spec = self._registry[name]
        return [
            p for p in inspect.signature(spec.handler).parameters
            if p not in ("self",)
        ]
