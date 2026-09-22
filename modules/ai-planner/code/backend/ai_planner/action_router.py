"""动作路由 —— 「codex 提议，受控层裁夺」的裁夺侧。

codex 在 read-only 沙盒里**只输出**一份结构化动作清单，自己不执行任何东西。
本路由把每条动作过受控层 dispatch()：
- 低风险 → 执行（受控层注入 actor=ai）
- 高风险 propose_* → 收集为提议，不执行
- 白名单外（codex 幻觉出的 delete_events / run_shell 等）→ **拒绝**并记录（纵深防御）

这样即便 codex 输出越界动作，本层也没有执行它的路径 —— A8 的第二道闸。
"""
from __future__ import annotations

from typing import Any, Iterator

from .controlled_tools import ControlledToolLayer, ToolNotAllowed
from .proposals import Proposal
from .streaming import StreamEvent

# 高风险命令名（只应产提议）——用于把「codex 直发高风险执行动作」也挡在门外
_HIGH_RISK = frozenset({"propose_move", "propose_reschedule", "propose_delete"})


class RouteResult:
    def __init__(self) -> None:
        self.executed: list[dict[str, Any]] = []
        self.proposals: list[Proposal] = []
        self.rejected: list[dict[str, Any]] = []


def route_actions(
    layer: ControlledToolLayer, actions: list[dict[str, Any]]
) -> Iterator[StreamEvent]:
    """逐条路由，边路由边产事件。调用方负责把提议汇总给前端确认。"""
    for action in actions:
        name = action.get("tool", "")
        args = action.get("args", {}) or {}
        try:
            result = layer.dispatch(name, args)
        except ToolNotAllowed as exc:
            yield StreamEvent(
                type="rejected",
                message=f"拒绝越界动作 {name!r}：{exc}",
                data={"tool": name},
            )
            continue
        except Exception as exc:  # nexus 4xx / 校验失败等 —— 报出来但不崩
            yield StreamEvent(
                type="rejected",
                message=f"动作 {name!r} 执行失败：{exc}",
                data={"tool": name},
            )
            continue

        if isinstance(result, Proposal):
            yield StreamEvent(
                type="proposal",
                message=f"提议：{result.kind} {result.target_type}/{result.target_id} —— {result.reason}",
                data=result.to_ui(),
            )
        else:
            yield StreamEvent(
                type="executed",
                message=f"已执行 {name}（actor=ai）",
                data={"tool": name, "result": result},
            )
