"""动作清单 JSON 解析（action_parsing.py）—— codex/deepseek 两个驱动共用一份逻辑。

这份解析逻辑是从 codex_driver.py 里提出来的（原来只有 codex 一份实现），
提出来的理由正是「同一个形状不能有两份实现」——deepseek_driver.py 复用同一个函数，
这里的测试覆盖两个驱动都依赖的公共契约，任何一个驱动的测试都不必重复覆盖这些形状。
"""
from __future__ import annotations

import json

from ai_planner.action_parsing import parse_actions_json, strip_code_fence


def test_parses_well_formed_actions() -> None:
    raw = json.dumps(
        {"actions": [{"tool": "create_task", "args_json": '{"projectId":"p_1","name":"x"}'}]}
    )
    actions = parse_actions_json(raw)
    assert actions == [{"tool": "create_task", "args": {"projectId": "p_1", "name": "x"}}]


def test_accepts_direct_args_dict_not_just_args_json() -> None:
    """宽进：非 strict-schema 模型（如 DeepSeek）可能直接给 args（dict）而不是 args_json（字符串）。"""
    raw = json.dumps({"actions": [{"tool": "create_zone", "args": {"name": "z"}}]})
    actions = parse_actions_json(raw)
    assert actions == [{"tool": "create_zone", "args": {"name": "z"}}]


def test_empty_or_blank_returns_none() -> None:
    assert parse_actions_json("") is None
    assert parse_actions_json("   \n  ") is None


def test_non_json_returns_none() -> None:
    assert parse_actions_json("这不是 JSON") is None


def test_missing_actions_key_returns_none() -> None:
    assert parse_actions_json(json.dumps({"foo": "bar"})) is None


def test_actions_not_a_list_returns_none() -> None:
    assert parse_actions_json(json.dumps({"actions": "not-a-list"})) is None


def test_skips_malformed_entries_but_keeps_valid_ones() -> None:
    raw = json.dumps(
        {
            "actions": [
                {"no_tool_key": True},
                "not-even-a-dict",
                {"tool": "create_task", "args_json": '{"name":"ok"}'},
            ]
        }
    )
    actions = parse_actions_json(raw)
    assert actions == [{"tool": "create_task", "args": {"name": "ok"}}]


def test_malformed_args_json_string_falls_back_to_empty_dict() -> None:
    raw = json.dumps({"actions": [{"tool": "create_task", "args_json": "{not valid json"}]})
    actions = parse_actions_json(raw)
    assert actions == [{"tool": "create_task", "args": {}}]


def test_strip_code_fence_removes_json_fence() -> None:
    fenced = "```json\n{\"actions\":[]}\n```"
    assert strip_code_fence(fenced) == '{"actions":[]}'


def test_strip_code_fence_removes_bare_fence() -> None:
    fenced = "```\n{\"actions\":[]}\n```"
    assert strip_code_fence(fenced) == '{"actions":[]}'


def test_strip_code_fence_noop_when_no_fence() -> None:
    assert strip_code_fence('{"actions":[]}') == '{"actions":[]}'


def test_parses_actions_wrapped_in_code_fence() -> None:
    raw = "```json\n" + json.dumps({"actions": [{"tool": "create_zone", "args_json": '{"name":"x"}'}]}) + "\n```"
    actions = parse_actions_json(raw)
    assert actions == [{"tool": "create_zone", "args": {"name": "x"}}]
