"""代理名字（hive.stage.v1「代理名」）：PUT agents/{runId}/name、views/lanes 的 humanName / conversation、对话名清洗。"""

from __future__ import annotations

import pytest

from app.modules.timer.agent_name import conversation
from test_agent_place import AGENTS, AI, API, BEARER, _finished, _start, ai_token  # noqa: F401


def _name(client, run_id, name, headers=None):
    return client.put(f"{AGENTS}/{run_id}/name", json={"name": name}, headers=headers or {})


def _lane(client, run_id):
    return next(a for a in client.get(f"{API}/views/lanes").json()["agents"] if a["runId"] == run_id)


def test_human_names_and_clears(client):
    run = _start(client)
    assert _name(client, run, "  小蜜  ").json() == {"runId": run, "agentKey": "garden", "name": "小蜜"}
    assert _lane(client, run)["humanName"] == "小蜜"
    assert _name(client, run, None).json() == {"runId": run, "agentKey": "garden", "name": None}
    assert _lane(client, run)["humanName"] is None


def test_device_token_and_ai_forbidden(client, ai_token):  # noqa: F811
    run = _start(client)
    assert _name(client, run, "x", BEARER).status_code == 403
    assert _name(client, run, "x", AI).status_code == 403
    assert _lane(client, run)["humanName"] is None


def test_unknown_run_404_and_bad_name_422(client):
    assert _name(client, "run_nope", "x").status_code == 404
    run = _start(client)
    assert _name(client, run, "").status_code == 422
    assert _name(client, run, "x" * 65).status_code == 422
    assert client.put(f"{AGENTS}/{run}/name", json={}).status_code == 422


def test_name_sticks_to_agent_for_later_runs(client):
    old = _finished(client, "run_old")
    assert _name(client, old, "老蜂").json()["agentKey"] == "garden"
    new = _start(client)
    other = _start(client, agent="other")
    assert _lane(client, new)["humanName"] == "老蜂"
    assert _lane(client, other)["humanName"] is None


def test_lane_conversation_from_label_or_match(client):
    a = _start(client, label="修登录页")
    b = _start(client, agent="b", label="gnome-terminal", match="部署脚本")
    c = _start(client, agent="c", label="Windows Terminal")
    assert _lane(client, a)["conversation"] == "修登录页"
    assert _lane(client, b)["conversation"] == "部署脚本"
    assert _lane(client, c)["conversation"] is None


@pytest.mark.parametrize("label, match, want", [
    ("fix bug", None, "fix bug"),
    ("  fix   bug ", None, "fix bug"),
    ("Konsole", "x1 title", "x1 title"),
    ("iTerm2", "ptyxis", None),
    ("Windows Terminal", None, None),
    (None, None, None),
    ("", "tilix", None),
])
def test_conversation_cleaning(label, match, want):
    assert conversation(label, match) == want
