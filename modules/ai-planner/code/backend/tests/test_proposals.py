"""提议对象 schema —— 形状稳定、无执行方法、UI 可展示。"""
from __future__ import annotations

from ai_planner.proposals import Proposal


def test_proposal_shape_and_defaults():
    p = Proposal(kind="delete", target_type="tasks", target_id="t_1", reason="重复")
    d = p.to_ui()
    assert d["kind"] == "delete"
    assert d["target_type"] == "tasks"
    assert d["target_id"] == "t_1"
    assert d["proposed_by"] == "ai"
    assert d["id"].startswith("prop_")
    assert "created_at" in d


def test_proposal_has_no_execution_method():
    """物理保证：Proposal 上没有任何能触发写的方法/属性。"""
    p = Proposal(kind="move", target_type="tasks", target_id="t_1",
                 payload={"newParent": "p_2"}, reason="搬")
    for attr in ("execute", "commit", "apply", "run", "delete", "save"):
        assert not hasattr(p, attr)


def test_proposal_frozen():
    import pytest
    from pydantic import ValidationError

    p = Proposal(kind="delete", target_type="tasks", target_id="t_1", reason="x")
    with pytest.raises((ValidationError, AttributeError, TypeError)):
        p.target_id = "t_2"  # type: ignore[misc]
