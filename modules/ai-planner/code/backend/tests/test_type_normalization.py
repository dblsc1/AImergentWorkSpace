"""断言：LLM 常用单数 type（'task'/'project'/'zone'），受控层必须宽进归一。

实证来源：codex 冒烟里真实产出 {"type":"task",...}，早期版本 Proposal 直接
literal_error 拒掉全部高风险提议。修在类层面（所有吃 type 的命令统一归一），
并留此断言防回归。
"""
from __future__ import annotations

import pytest

from ai_planner.controlled_tools import ControlledToolLayer, ToolNotAllowed
from ai_planner.proposals import Proposal


@pytest.mark.parametrize("singular,plural", [("task", "tasks"), ("project", "projects"), ("zone", "zones")])
def test_singular_type_normalized_in_proposals(layer: ControlledToolLayer, singular, plural):
    p = layer.dispatch("propose_delete", {"type": singular, "id": "x_1", "reason": "r"})
    assert isinstance(p, Proposal)
    assert p.target_type == plural


def test_singular_type_normalized_in_low_risk_patch(layer: ControlledToolLayer, spy):
    layer.dispatch("set_title", {"type": "task", "id": "t_1", "name": "新名"})
    assert spy.patches[0][0] == "tasks"  # 已归一为复数发给 nexus


def test_unknown_type_rejected(layer: ControlledToolLayer):
    with pytest.raises(ToolNotAllowed):
        layer.dispatch("propose_delete", {"type": "widget", "id": "x", "reason": "r"})


def test_case_insensitive_type(layer: ControlledToolLayer):
    p = layer.dispatch("propose_move", {"type": "TASK", "id": "t", "newParent": "p", "reason": "r"})
    assert p.target_type == "tasks"
