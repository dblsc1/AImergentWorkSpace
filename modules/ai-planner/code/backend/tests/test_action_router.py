"""动作路由 + 编排：codex 提议，受控层裁夺；越界动作被拒（纵深防御第二道闸）。"""
from __future__ import annotations

from ai_planner.action_router import route_actions
from ai_planner.codex_driver import StubDriver
from ai_planner.controlled_tools import ControlledToolLayer
from ai_planner.service import run_planning


def test_low_risk_executed_high_risk_proposed(layer: ControlledToolLayer, spy):
    actions = [
        {"tool": "create_task", "args": {"projectId": "p_inbox", "name": "读论文"}},
        {"tool": "propose_move", "args": {"type": "tasks", "id": "t_1", "newParent": "p_2", "reason": "理清"}},
    ]
    events = list(route_actions(layer, actions))
    kinds = [e.type for e in events]
    assert "executed" in kinds
    assert "proposal" in kinds
    assert len(spy.creates) == 1           # 低风险执行了
    assert len(layer.proposals) == 1       # 高风险只提议


def test_out_of_whitelist_action_rejected_not_executed(layer: ControlledToolLayer, spy):
    """codex 幻觉出 events 写 / shell —— 路由拒绝，绝不执行。"""
    actions = [
        {"tool": "write_event", "args": {"type": "session.completed"}},
        {"tool": "run_shell", "args": {"cmd": "mongosh"}},
        {"tool": "create_task", "args": {"projectId": "p_1", "name": "正常"}},
    ]
    events = list(route_actions(layer, actions))
    rejected = [e for e in events if e.type == "rejected"]
    executed = [e for e in events if e.type == "executed"]
    assert len(rejected) == 2
    assert len(executed) == 1
    assert len(spy.creates) == 1  # 只有合法那条落库


def test_full_orchestration_with_stub_driver(layer: ControlledToolLayer, spy):
    driver = StubDriver([
        {"tool": "create_task", "args": {"projectId": "p_inbox", "name": "捕捉的想法"}},
        {"tool": "propose_delete", "args": {"type": "tasks", "id": "t_dup", "reason": "重复"}},
    ])
    events = list(run_planning(layer, driver, "理清收件箱"))
    types = [e.type for e in events]
    assert "action_plan" in types
    assert "executed" in types
    assert "proposal" in types
    assert types[-1] == "done"
    # done 事件汇总了提议
    done = events[-1]
    assert len(done.data.get("proposals", [])) == 1
    assert spy.reads == ["export"]  # 编排先读了日程
