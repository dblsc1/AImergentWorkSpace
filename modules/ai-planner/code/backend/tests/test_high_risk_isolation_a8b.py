"""A8b：AI 无执行高风险写的路径 —— move/reschedule/delete 只产提议，绝不触达 nexus。"""
from __future__ import annotations

from ai_planner.controlled_tools import ControlledToolLayer
from ai_planner.proposals import Proposal

HIGH_RISK = ["propose_move", "propose_reschedule", "propose_delete"]


def test_propose_move_produces_proposal_no_write(layer: ControlledToolLayer, spy):
    prop = layer.dispatch(
        "propose_move",
        {"type": "tasks", "id": "t_1", "newParent": "p_2", "reason": "从收件箱理清"},
    )
    assert isinstance(prop, Proposal)
    assert prop.kind == "move"
    # 关键：没有任何写打到 nexus
    assert spy.creates == []
    assert spy.patches == []


def test_propose_reschedule_and_delete_never_write(layer: ControlledToolLayer, spy):
    layer.dispatch(
        "propose_reschedule",
        {"type": "tasks", "id": "t_1", "plan": {"start": "2026-08-11", "end": "2026-08-12"}, "reason": "延后"},
    )
    layer.dispatch("propose_delete", {"type": "tasks", "id": "t_9", "reason": "重复任务"})
    assert spy.creates == []
    assert spy.patches == []
    assert len(layer.proposals) == 2


def test_no_whitelisted_tool_executes_high_risk(layer: ControlledToolLayer, spy):
    """穷举白名单：没有任何命令会执行搬移(改projectId)/改plan/删除。"""
    # 跑遍所有低风险写命令，断言它们发出的 body 里绝不含 projectId 变更/plan/删除语义
    layer.dispatch("create_task", {"projectId": "p_1", "name": "a"})
    layer.dispatch("create_project", {"zoneId": "z_1", "name": "b"})
    layer.dispatch("create_zone", {"name": "c"})
    layer.dispatch("set_title", {"type": "tasks", "id": "t_1", "name": "x"})
    layer.dispatch("set_weight", {"type": "tasks", "id": "t_1", "plannedWeight": 10})
    layer.dispatch("set_order", {"id": "z_1", "order": 3})

    # PATCH 只允许改 name / plannedWeight / order —— 绝不含 projectId / zoneId / plan
    for _type, _id, body in spy.patches:
        assert "projectId" not in body, "低风险写不得改 projectId（那是高风险搬移）"
        assert "zoneId" not in body, "低风险写不得改 zoneId（那是高风险搬移）"
        assert "plan" not in body, "低风险写不得改 plan（那是高风险改期）"
    # 没有任何 delete 路径（客户端根本无 delete 方法，见 conftest 反证）
    assert not hasattr(spy, "delete")


def test_high_risk_dispatch_returns_proposal_type(layer: ControlledToolLayer):
    for name, args in [
        ("propose_move", {"type": "tasks", "id": "t", "newParent": "p", "reason": "r"}),
        ("propose_reschedule", {"type": "tasks", "id": "t", "plan": None, "reason": "r"}),
        ("propose_delete", {"type": "tasks", "id": "t", "reason": "r"}),
    ]:
        assert isinstance(layer.dispatch(name, args), Proposal)
