"""AI 代理时长读端（契约 v2.3「AI 代理时长读端」）。

要害：代理时长是另一个维度——泳道秒数（并行各算各的）、归日同人（不切分）、
在跑的不计入汇总、响应里没有人的时长、按租户隔离。
"""

from __future__ import annotations

from datetime import datetime

import pytest

from app.config import load_settings

API = "/api/core"
URL = f"{API}/views/agent-time"
A = {"X-Nexus-Tenant": "ch_aaaa"}
B = {"X-Nexus-Tenant": "ch_bbbb"}
KEYS = {"today", "totalSeconds", "runs", "days", "agents", "tasks", "open", "hiddenCount"}
SHANGHAI = load_settings(
    {"NEXUS_MONGO_URI": "mongodb://x", "NEXUS_DB_NAME": "nexus_core_test", "NEXUS_TZ": "Asia/Shanghai"}
)


def _run(client, n, *, agent="a", start="2026-09-20T09:00:00+00:00", seconds=3600,
         project="p1", task="t1", headers=None):
    """直接投一条 agent.run.completed（外部 source），精确控制 startAt/时长。"""
    subject = {"zone": "z1", "project": project}
    if task:
        subject["task"] = task
    env = {
        "spec": "yq-event/v1", "id": f"evt_at{n}", "dedupeKey": f"at-{n}",
        "type": "agent.run.completed", "source": "test-hook", "user": "u_local",  # 服务端按租户重盖
        "time": start, "subject": subject,
        "data": {"agent": agent, "tool": "x", "startAt": start,
                 "durationSeconds": seconds, "outcome": "done"},
    }
    resp = client.post(f"{API}/events", json=env, headers=headers or {})
    assert resp.json()["accepted"] == 1, resp.text


@pytest.fixture()
def shift_clock(monkeypatch):
    from app.modules.timer import service  # noqa: PLC0415

    real = service._now

    def shift(**delta):
        from datetime import timedelta  # noqa: PLC0415

        monkeypatch.setattr(service, "_now", lambda: real() + timedelta(**delta))

    return shift


def test_empty(client):
    body = client.get(URL).json()
    assert set(body) == KEYS
    assert body["totalSeconds"] == 0 and body["runs"] == 0
    assert body["days"] == body["agents"] == body["tasks"] == body["open"] == []
    assert len(body["today"]) == 10


def test_parallel_runs_are_lane_seconds_and_human_untouched(client):
    """同一小时里：两个不同代理 + 同一代理的第二个并行运行 → 3h 泳道秒数，不是 1h 墙钟。"""
    _run(client, 1, agent="claude-code")
    _run(client, 2, agent="claude-code")
    _run(client, 3, agent="codex", task=None, project="p_inbox")
    body = client.get(URL).json()
    assert set(body) == KEYS  # 没有任何人的时长字段
    assert (body["totalSeconds"], body["runs"]) == (3 * 3600, 3)
    assert body["days"] == [{"date": "2026-09-20", "seconds": 3 * 3600, "runs": 3}]
    assert body["agents"] == [
        {"agent": "claude-code", "seconds": 7200, "runs": 2},
        {"agent": "codex", "seconds": 3600, "runs": 1},
    ]
    assert body["tasks"] == [
        {"projectId": "p1", "taskId": "t1", "seconds": 7200, "runs": 2},
        {"projectId": "p_inbox", "taskId": None, "seconds": 3600, "runs": 1},
    ]
    # 人的读端照旧看不见
    assert client.get(f"{API}/views/current").json()["running"] is False


def test_day_attribution_matches_human_rule_no_split(client, monkeypatch):
    """归日经 NEXUS_TZ、整段归开始那天：上海 23:30 开跑 2h 全记前一天，不切到次日。"""
    from app.modules.projector.handlers import agent_daily_stats  # noqa: PLC0415

    monkeypatch.setattr(agent_daily_stats, "settings", SHANGHAI)
    _run(client, 1, start="2026-09-20T15:30:00+00:00", seconds=7200)  # 上海 09-20 23:30
    _run(client, 2, start="2026-09-20T17:00:00+00:00", seconds=600)   # 上海 09-21 01:00
    body = client.get(URL).json()
    assert body["days"] == [
        {"date": "2026-09-20", "seconds": 7200, "runs": 1},
        {"date": "2026-09-21", "seconds": 600, "runs": 1},
    ]


def test_range_is_inclusive_and_inverted_range_is_empty(client):
    for n, day in enumerate(("18", "19", "20", "21"), 1):
        _run(client, n, start=f"2026-09-{day}T09:00:00+00:00", seconds=60 * n)
    body = client.get(URL, params={"from": "2026-09-19", "to": "2026-09-20"}).json()
    assert [d["date"] for d in body["days"]] == ["2026-09-19", "2026-09-20"]
    assert body["totalSeconds"] == 120 + 180
    assert client.get(URL, params={"from": "2026-09-21"}).json()["totalSeconds"] == 240
    inverted = client.get(URL, params={"from": "2026-09-21", "to": "2026-09-18"})
    assert inverted.status_code == 200 and inverted.json()["days"] == []


@pytest.mark.parametrize("params", [
    {"from": "2026-9-1"}, {"to": "20260901"}, {"from": "yesterday"}, {"to": "2026-09-01T00:00"},
])
def test_bad_date_is_422(client, params):
    assert client.get(URL, params=params).status_code == 422


def test_open_run_listed_clipped_to_now_not_summed(client, seeded, shift_clock):
    task = seeded["tasks"]["示例任务三"]
    run = client.post(f"{API}/agents/start", json={"taskId": task["id"], "agent": "codex", "tool": "t"}).json()
    shift_clock(hours=1)
    body = client.get(URL).json()
    assert body["totalSeconds"] == 0 and body["days"] == []
    [item] = body["open"]
    assert item["runId"] == run["runId"] and item["agent"] == "codex"
    assert item["taskId"] == task["id"] and item["projectId"] == seeded["projects"]["示例项目三"]["id"]
    assert 3600 <= item["elapsedSeconds"] <= 3610

    # 开始日之前的范围不列它
    from app.config import settings  # noqa: PLC0415
    from app.timeutil import local_date  # noqa: PLC0415

    day = local_date(datetime.fromisoformat(run["startedAt"]), settings.tz)
    assert client.get(URL, params={"to": "2000-01-01"}).json()["open"] == []
    assert len(client.get(URL, params={"from": day, "to": day}).json()["open"]) == 1


def test_forgotten_run_is_swept_into_totals_capped(client, seeded, shift_clock):
    client.post(f"{API}/agents/start", json={"agent": "codex", "tool": "t"})
    shift_clock(hours=30)  # 默认上限 12h
    body = client.get(URL).json()
    assert body["open"] == []
    assert (body["totalSeconds"], body["runs"]) == (12 * 3600, 1)


def test_tenant_isolation(client):
    _run(client, 1, headers=A)
    client.post(f"{API}/agents/start", json={"agent": "x", "tool": "t"}, headers=A)
    assert client.get(URL, headers=A).json()["totalSeconds"] == 3600
    assert len(client.get(URL, headers=A).json()["open"]) == 1
    other = client.get(URL, headers=B).json()
    assert other["totalSeconds"] == 0 and other["days"] == [] and other["open"] == []
