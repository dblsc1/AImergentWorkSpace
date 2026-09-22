"""A8c：actor 由受控层注入，AI 无法带 actor=human。"""
from __future__ import annotations

from ai_planner.controlled_tools import ControlledToolLayer

LOW_RISK_WRITES = ["create_task", "create_project", "create_zone",
                   "set_title", "set_weight", "set_order"]


def test_no_write_tool_accepts_actor_param(layer: ControlledToolLayer):
    """任何写命令的签名里都不能有 actor 形参 —— AI 无从传 actor=human。"""
    for name in LOW_RISK_WRITES:
        params = layer.signature_params(name)
        assert "actor" not in params, f"{name} 不该暴露 actor 形参"


def test_every_write_injects_actor_ai(layer: ControlledToolLayer, spy):
    layer.dispatch("create_task", {"projectId": "p_1", "name": "a"})
    layer.dispatch("create_project", {"zoneId": "z_1", "name": "b"})
    layer.dispatch("create_zone", {"name": "c"})
    layer.dispatch("set_title", {"type": "projects", "id": "p_1", "name": "x"})
    layer.dispatch("set_weight", {"type": "tasks", "id": "t_1", "plannedWeight": 5})
    layer.dispatch("set_order", {"id": "z_1", "order": 2})

    assert len(spy.creates) == 3
    assert len(spy.patches) == 3
    for _type, body in spy.creates:
        assert body.get("actor") == "ai"
    for _type, _id, body in spy.patches:
        assert body.get("actor") == "ai"


def test_ai_cannot_smuggle_actor_human_via_args(layer: ControlledToolLayer, spy):
    """即便 AI 在 args 里塞 actor=human，dispatch 也会因未知形参 TypeError，
    而不是把 human 透传下去。"""
    import pytest

    with pytest.raises(TypeError):
        layer.dispatch(
            "create_task",
            {"projectId": "p_1", "name": "a", "actor": "human"},
        )
    # 没有任何一次写落库带了 human
    for _type, body in spy.creates:
        assert body.get("actor") != "human"
