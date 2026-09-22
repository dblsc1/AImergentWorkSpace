"""A8：AI 只能调受控层白名单；events/timer/mongo/凭据/裸 shell 不可达。"""
from __future__ import annotations

import pytest

from ai_planner.controlled_tools import ControlledToolLayer, ToolNotAllowed

# 白名单唯一事实（对齐 contracts/ai-planner-guide-v1.md §3）
EXPECTED_WHITELIST = {
    "read_schedule", "read_next_actions", "read_review",
    "create_task", "create_project", "create_zone",
    "set_title", "set_weight", "set_order",
    "propose_move", "propose_reschedule", "propose_delete",
}

# 这些命令**必须**在白名单外 —— events 红线、裸 shell、直连 mongo、凭据、任意 HTTP
FORBIDDEN = [
    "write_event", "post_event", "create_event", "ingest",   # events 红线
    "timer_start", "timer_stop",                              # timer
    "mongo_query", "db_exec", "find", "aggregate",            # 直连 mongo
    "run_shell", "exec", "system", "bash",                   # 裸 shell
    "read_secret", "get_credentials", "read_auth",           # 凭据
    "http_get", "curl", "request", "fetch",                  # 任意 HTTP
    "delete_task", "delete_project", "delete", "move", "reschedule",  # 高风险直执行
]


def test_whitelist_is_exactly_the_guide_set(layer: ControlledToolLayer):
    assert set(layer.whitelist) == EXPECTED_WHITELIST


@pytest.mark.parametrize("name", FORBIDDEN)
def test_forbidden_commands_are_not_reachable(layer: ControlledToolLayer, name: str):
    assert name not in layer.whitelist
    with pytest.raises(ToolNotAllowed):
        layer.dispatch(name, {})


def test_dispatch_unknown_name_raises(layer: ControlledToolLayer):
    with pytest.raises(ToolNotAllowed):
        layer.dispatch("definitely_not_a_tool", {"x": 1})


def test_no_whitelist_name_hints_at_forbidden_surface(layer: ControlledToolLayer):
    banned_substr = ("event", "timer", "mongo", "shell", "secret", "cred", "curl", "http")
    for name in layer.whitelist:
        low = name.lower()
        assert not any(b in low for b in banned_substr), f"白名单命令名可疑：{name}"


def test_spy_client_has_no_dangerous_surface(spy):
    # 反证：受控层下面的客户端连 delete/events/timer 方法都没有
    for attr in ("delete", "write_event", "timer_start", "mongo_query", "request"):
        assert not hasattr(spy, attr)
