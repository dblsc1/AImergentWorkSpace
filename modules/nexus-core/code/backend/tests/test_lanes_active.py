"""``views/lanes`` 只显示在干活的泳道（契约 v2.24）：出错 / 空闲满 1 小时的进 ``inactiveAgents``，其余照旧。

``now`` 钉死在今天 12:00（本地），在跑的运行用桩出来的 ``list_lane_runs`` 给，已结束的直接写投影。
"""

from __future__ import annotations

from datetime import datetime, time, timedelta

import pytest

LANES = "/api/core/views/lanes"
PREFS = "/api/core/lanes/prefs/agent"


def _tz():
    from app.config import settings  # noqa: PLC0415

    return settings.tz


def _now() -> datetime:
    return datetime.combine(datetime.now(_tz()).date(), time(12), _tz())


def _ago(minutes: int) -> datetime:
    return _now() - timedelta(minutes=minutes)


class World:
    def __init__(self, monkeypatch):
        self.open: list[dict] = []
        from app.modules.timer import service  # noqa: PLC0415

        monkeypatch.setattr(service, "list_lane_runs", lambda user=None: (_now(), self.open))

    def live(self, label, start_min=300, phases=(), lost=False, seen_min=1):
        rid = f"o-{label}-{len(self.open)}"
        self.open.append({"runId": rid, "agent": "cc", "tool": "t", "model": None, "label": label, "taskId": None,
                          "projectId": None, "beatSource": None, "beatCount": 0, "unverified": False, "match": None,
                          "startTs": _ago(start_min), "endTs": None, "outcome": None,
                          "elapsedSeconds": start_min * 60, "overdue": False, "lost": lost,
                          "lastSeenTs": _ago(seen_min),
                          "phases": [{"at": _ago(m).isoformat(), "phase": p} for m, p in phases],
                          "interactions": []})
        return rid

    def closed(self, label, start_min, end_min, outcome="done", phases=()):
        from app.modules.projector import repo  # noqa: PLC0415

        rid = f"c-{label}-{start_min}-{end_min}"
        repo.apply_lane({"user": "u_local", "key": rid, "kind": "run", "runId": rid, "agent": "cc", "label": label,
                         "startAt": _ago(start_min), "endAt": _ago(end_min),
                         "durationSeconds": (start_min - end_min) * 60,
                         "phases": [{"at": _ago(m).isoformat(), "phase": p} for m, p in phases],
                         "interactions": [], "taskId": None, "projectId": None, "outcome": outcome})
        return rid


@pytest.fixture()
def w(client, monkeypatch):
    return World(monkeypatch)


def _get(client, **kw):
    resp = client.get(LANES, **kw)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _split(client):
    body = _get(client)
    return {a["label"] for a in body["agents"]}, {a["label"]: a for a in body["inactiveAgents"]}, body


def test_working_and_waiting_always_shown_even_after_hours(client, w):
    w.live("work", phases=[(200, "working")])
    w.live("wait", phases=[(200, "waiting_permission")])
    w.live("ask", phases=[(200, "waiting_input")])
    shown, inactive, _ = _split(client)
    assert shown == {"work", "wait", "ask"} and inactive == {}


def test_idle_threshold_59_shown_61_inactive(client, w):
    w.live("p59", phases=[(59, "idle")])  # 59 分钟前转入空闲 = 最后干活在 59 分钟前
    w.live("p61", phases=[(61, "idle")])
    shown, inactive, _ = _split(client)
    assert shown == {"p59"} and inactive["p61"]["reason"] == "idle"
    assert inactive["p61"]["lastWorkAt"] == _ago(61).isoformat() and inactive["p61"]["runs"] == 1


def test_error_open_run_is_inactive_even_if_recent(client, w):
    w.live("bad", phases=[(5, "error")])
    shown, inactive, _ = _split(client)
    assert shown == set() and inactive["bad"]["reason"] == "error"


def test_failed_latest_closed_inactive_but_newer_working_run_shows(client, w):
    w.closed("f", 30, 10, outcome="failed")
    shown, inactive, _ = _split(client)
    assert shown == set() and inactive["f"]["reason"] == "error"
    w.live("f", start_min=5)
    shown, inactive, _ = _split(client)
    assert shown == {"f"} and inactive == {}


def test_closed_runs_by_end_time(client, w):
    w.closed("old", 200, 120)
    w.closed("fresh", 100, 20)
    shown, inactive, _ = _split(client)
    assert shown == {"fresh"} and inactive["old"]["reason"] == "idle"


def test_parked_open_run_with_fresh_heartbeats_is_inactive(client, w):
    w.live("parked", phases=[(130, "idle")], seen_min=0)
    shown, inactive, _ = _split(client)
    assert shown == set() and inactive["parked"]["reason"] == "idle"


def test_pinned_inactive_is_shown_and_hidden_only_in_hidden(client, w):
    w.live("pin", phases=[(300, "idle")])
    w.live("hid", phases=[(300, "idle")])
    w.live("hid2", phases=[(5, "error")])
    assert client.put(PREFS, json={"agent": "cc", "label": "pin", "pinned": True}).status_code == 200
    assert client.put(PREFS, json={"agent": "cc", "label": "hid", "hidden": True}).status_code == 200
    assert client.put(PREFS, json={"agent": "cc", "label": "hid2", "hidden": True}).status_code == 200
    shown, inactive, body = _split(client)
    assert shown == {"pin"} and inactive == {}
    assert {h["label"] for h in body["hiddenAgents"]} == {"hid", "hid2"}


def test_past_day_is_unfiltered(client, w):
    from app.modules.projector import repo  # noqa: PLC0415

    day = datetime.now(_tz()).date() - timedelta(days=1)
    start = datetime.combine(day, time(8), _tz())
    repo.apply_lane({"user": "u_local", "key": "p1", "kind": "run", "runId": "p1", "agent": "cc", "label": "past",
                     "startAt": start, "endAt": start + timedelta(hours=1), "durationSeconds": 3600, "phases": [],
                     "interactions": [], "taskId": None, "projectId": None, "outcome": "failed"})
    body = _get(client, params={"date": day.isoformat()})
    assert [a["label"] for a in body["agents"]] == ["past"] and body["inactiveAgents"] == []


def test_every_run_counted_once_and_dropped_follows_inactive_lane(client, w, monkeypatch):
    from app.modules.views import lane_cap  # noqa: PLC0415

    monkeypatch.setattr(lane_cap, "MAX_RUNS_PER_LANE", 2)
    for i in range(5):  # 吵闹的空闲身份：5 条已结束，封顶只留 2 条，3 条进 dropped，但整身份不活跃 → 全并入摘要
        w.closed("noisy", 400 - 10 * i, 395 - 10 * i)
    for i in range(4):  # 吵闹但刚干完的身份：2 条留下，2 条仍在 dropped
        w.closed("busy", 40 - 10 * i, 38 - 10 * i)
    w.live("work", phases=[(1, "working")])
    w.live("hidden", phases=[(1, "working")])
    assert client.put(PREFS, json={"agent": "cc", "label": "hidden", "hidden": True}).status_code == 200
    shown, inactive, body = _split(client)
    assert inactive["noisy"]["runs"] == 5 and inactive["noisy"]["elapsedSeconds"] == 5 * 300
    assert [d["label"] for d in body["dropped"]] == ["busy"]
    total = len(body["agents"]) + sum(i["runs"] for i in body["inactiveAgents"]) + sum(d["runs"] for d in body["dropped"])
    assert total == 5 + 4 + 1  # 隐藏的那条另算（在 hiddenAgents）
    assert shown == {"busy", "work"}


def test_report_scope_cannot_read_lanes(client, w):
    w.live("x", phases=[(5, "error")])
    assert client.get(LANES, headers={"X-Nexus-Scope": "report", "X-Nexus-Anonymous": "1"}).status_code == 403
    assert client.get(LANES, headers={"X-Nexus-Scope": "read", "Authorization": "Bearer t"}).status_code == 200
