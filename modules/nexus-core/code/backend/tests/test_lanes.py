"""``GET /api/core/views/lanes`` + 投影 ``proj_lanes``（契约 v2.4）。

时间线不是汇总：与窗口重叠即列出、不裁剪、没有合计字段、**不写**（超时的标 overdue）。
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pytest

API = "/api/core"
LANES = f"{API}/views/lanes"
A = {"X-Nexus-Tenant": "ch_aaaa"}
B = {"X-Nexus-Tenant": "ch_bbbb"}


def _db():
    from app.repo import get_db  # noqa: PLC0415

    return get_db()


def _tz():
    from app.config import settings  # noqa: PLC0415

    return settings.tz


def _today() -> date:
    return datetime.now(_tz()).date()


def _local(day: date, hh: int, mm: int = 0) -> str:
    return datetime.combine(day, time(hh, mm), _tz()).isoformat()


def _lanes(client, headers=None, expect=200, **params):
    resp = client.get(LANES, params=params, headers=headers or {})
    assert resp.status_code == expect, resp.text
    return resp.json()


def _backfill(client, task_id, start_at, seconds, headers=None, mode="do"):
    body = {"taskId": task_id, "startAt": start_at, "durationSeconds": seconds, "mode": mode}
    resp = client.post(f"{API}/timer/backfill", json=body, headers=headers or {})
    assert resp.status_code == 200, resp.text


def _start(client, headers=None, **body):
    resp = client.post(f"{API}/agents/start", json={"agent": "cc", "tool": "claude-code", **body},
                       headers=headers or {})
    assert resp.status_code == 201, resp.text
    return resp.json()


@pytest.fixture()
def task(seeded):
    return next(iter(seeded["tasks"].values()))


@pytest.fixture()
def shift_clock(monkeypatch):
    from app.modules.timer import service  # noqa: PLC0415

    real = service._now

    def shift(**delta):
        monkeypatch.setattr(service, "_now", lambda: real() + timedelta(**delta))

    return shift


def test_empty_shape_defaults_to_today(client):
    body = _lanes(client)
    today = _today()
    assert body["today"] == today.isoformat()
    assert body["windowStart"] == _local(today, 0)
    assert body["windowEnd"] == datetime.combine(today + timedelta(days=1), time(), _tz()).isoformat()
    assert body["human"] == {"sessions": [], "running": None, "presence": [],
                             "auto": None, "needsChoice": None, "aiThinking": None,  # v2.14 两个 + v2.15 一个
                             "focus": None}  # v2.16
    assert (body["agents"], body["interactions"], body["truncated"]) == ([], [], False)
    assert (body["hiddenAgents"], body["hiddenWaiting"], body["dropped"]) == ([], 0, [])
    assert set(body) == {"today", "now", "windowStart", "windowEnd", "human", "agents", "interactions",
                         "truncated", "hiddenAgents", "hiddenWaiting", "stalePinned", "dropped", "inactiveAgents",
                         "expiredAgents", "expired"}  # v2.22 + v2.23 + v2.24 + v2.25
    assert (body["expiredAgents"], body["expired"]) == (0, {"agents": 0, "runs": 0, "elapsedSeconds": 0})


def test_sessions_listed_on_every_overlapping_day_unclipped(client, task):
    day = _today() - timedelta(days=3)
    _backfill(client, task["id"], _local(day, 23, 30), 3600, mode="prompt")
    for d in (day, day + timedelta(days=1)):
        sessions = _lanes(client, date=d.isoformat())["human"]["sessions"]
        assert len(sessions) == 1
        s = sessions[0]
        assert datetime.fromisoformat(s["startAt"]) == datetime.fromisoformat(_local(day, 23, 30))
        assert datetime.fromisoformat(s["endAt"]) - datetime.fromisoformat(s["startAt"]) == timedelta(hours=1)
        assert (s["mode"], s["durationSeconds"], s["taskId"], s["source"]) == ("prompt", 3600, task["id"],
                                                                              "manual-backfill")
    assert _lanes(client, date=(day + timedelta(days=2)).isoformat())["human"]["sessions"] == []
    # 窗口拼起来：同一段只出现一次
    both = _lanes(client, **{"from": day.isoformat(), "to": (day + timedelta(days=1)).isoformat()})
    assert len(both["human"]["sessions"]) == 1


def test_live_agents_running_human_presence_and_interactions(client, task):
    client.post(f"{API}/timer/start", json={"taskId": task["id"]})
    run = _start(client, taskId=task["id"], phase="idle", label="garden", match="garden")
    at = (datetime.fromisoformat(run["startedAt"]) + timedelta(seconds=1)).isoformat()
    client.post(f"{API}/agents/{run['runId']}/phase", json={"phase": "working", "at": at, "reply": True,
                                                              "detail": "Bash"})
    client.post(f"{API}/activity/presence", json={"deviceId": "dev_1", "app": "ptyxis",
                                                   "title": "✳ garden", "afk": False})
    body = _lanes(client)
    assert body["human"]["running"]["taskId"] == task["id"]
    assert [(p["title"], p["runId"]) for p in body["human"]["presence"]] == [("✳ garden", run["runId"])]
    [agent] = body["agents"]
    assert (agent["runId"], agent["label"], agent["endAt"], agent["outcome"], agent["overdue"]) == (
        run["runId"], "garden", None, None, False)
    assert agent["phases"] == [{"at": run["startedAt"], "phase": "idle", "detail": None},
                               {"at": at, "phase": "working", "detail": "Bash"}]
    attend, reply = body["interactions"]  # 按 at 升序：心跳（此刻）早于 reply（起点 +1s）
    assert reply == {"runId": run["runId"], "kind": "reply", "at": at}  # reply 没有 until 键
    assert attend["kind"] == "attend" and "until" in attend
    [seen] = agent["attention"]  # v2.17：同一份 attend，按运行给出、裁到窗口
    assert datetime.fromisoformat(seen["from"]) == datetime.fromisoformat(attend["at"]) and seen["from"] == seen["to"]


def test_closed_run_comes_from_projection(client):
    run = _start(client, phase="working", label="garden")
    client.post(f"{API}/agents/{run['runId']}/stop", json={"outcome": "done"})
    [agent] = _lanes(client)["agents"]
    assert agent["outcome"] == "done" and agent["endAt"] is not None and agent["overdue"] is False
    assert agent["elapsedSeconds"] >= 1 and agent["label"] == "garden"
    assert [p["phase"] for p in agent["phases"]] == ["working"]
    assert _db()["proj_lanes"].count_documents({"kind": "run"}) == 1


def test_overdue_run_listed_but_never_closed(client, shift_clock):
    run = _start(client)
    shift_clock(hours=13)
    # v2.24：超期的运行在还没过完的窗口里默认不显示（收进 inactiveAgents）；这里关心的是运行本身，先置顶（置顶永远显示）。
    # 不置顶的话结果随真实时刻变：拨 13 小时后落在今天窗口之内（上午跑）就被收起，之外（下午跑）才照旧列出
    for x in _lanes(client)["inactiveAgents"]:
        resp = client.put(f"{API}/lanes/prefs/agent", json={"agent": x["agent"] or "", "label": x["label"] or "",
                                                             "pinned": True, "unverified": x["unverified"]})
        assert resp.status_code == 200, resp.text
    [agent] = _lanes(client)["agents"]
    assert agent["runId"] == run["runId"] and agent["overdue"] is True
    assert agent["elapsedSeconds"] == 12 * 3600 and agent["endAt"] is None
    assert _db()["agent_runs"].count_documents({}) == 1  # 本端点不写
    assert _db()["events"].count_documents({}) == 0


def test_marked_but_unfinished_run_is_drawn_as_ended(client):
    from datetime import timezone  # noqa: PLC0415

    from app.modules.timer import repo  # noqa: PLC0415

    run = _start(client)
    # v2.25：刚结束的普通泳道立刻折叠；这里关心的是运行本身，所以先置顶（置顶永远显示）
    assert client.put(f"{API}/lanes/prefs/agent", json={"agent": "cc", "label": "", "pinned": True}).status_code == 200
    repo.mark_agent_run_closing("u_local", run["runId"],
                                {"outcome": "cancelled", "endedAt": datetime.now(timezone.utc).isoformat()})
    [agent] = _lanes(client)["agents"]
    assert agent["outcome"] == "cancelled" and agent["endAt"] is not None


@pytest.mark.parametrize("params", [
    {"date": "2026-09-30", "from": "2026-09-29"}, {"date": "2026/09/30"}, {"date": "2026-02-30"},
    {"from": "2026-09-01", "to": "2026-09-08"},
])
def test_bad_params_are_422(client, params):
    _lanes(client, expect=422, **params)


def test_seven_days_ok_reverse_empty_single_bound(client, task):
    day = _today() - timedelta(days=2)
    _backfill(client, task["id"], _local(day, 10), 600)
    week = _lanes(client, **{"from": (day - timedelta(days=4)).isoformat(), "to": (day + timedelta(days=2)).isoformat()})
    assert len(week["human"]["sessions"]) == 1
    reverse = _lanes(client, **{"from": day.isoformat(), "to": (day - timedelta(days=1)).isoformat()})
    assert reverse["human"]["sessions"] == [] and reverse["agents"] == []
    assert len(_lanes(client, **{"from": day.isoformat()})["human"]["sessions"]) == 1


def test_caps_keep_newest_and_flag_truncated(client, task, monkeypatch):
    from app.modules.views import lane_cap, lanes  # noqa: PLC0415

    monkeypatch.setattr(lanes, "MAX_SESSIONS", 2)
    monkeypatch.setattr(lane_cap, "MAX_RUNS_PER_LANE", 1)
    day = _today() - timedelta(days=1)
    for hh in (8, 9, 10):
        _backfill(client, task["id"], _local(day, hh), 600)
    body = _lanes(client, date=day.isoformat())
    assert body["truncated"] is True
    assert [datetime.fromisoformat(s["startAt"]).astimezone(_tz()).hour for s in body["human"]["sessions"]] == [9, 10]
    for _ in range(2):  # 同一身份的已结束运行：每个身份只留最新 1 条，其余折进 dropped（v2.23）
        run = _start(client)
        client.post(f"{API}/agents/{run['runId']}/stop", json={"outcome": "done"})
    body = _lanes(client)
    assert body["truncated"] is True and [a["runId"] for a in body["agents"]] == [run["runId"]]
    assert [d["runs"] for d in body["dropped"]] == [1]


def test_tenant_isolation(client, task):
    _start(client, headers=A)
    client.post(f"{API}/activity/presence", headers=A,
                json={"deviceId": "d", "app": "x", "title": "y", "afk": False})
    body = _lanes(client, headers=B)
    assert body["agents"] == [] and body["human"]["presence"] == []


def test_rebuild_restores_same_lanes(client, task):
    from app.modules.projector.rebuild import rebuild  # noqa: PLC0415

    day = _today() - timedelta(days=1)
    _backfill(client, task["id"], _local(day, 9), 900)
    run = _start(client, phase="working")
    client.post(f"{API}/agents/{run['runId']}/stop", json={"outcome": "done"})
    before = {k: v for k, v in _lanes(client, **{"from": day.isoformat()}).items() if k != "now"}
    _db()["proj_lanes"].delete_many({})
    assert rebuild(only="proj_lanes") == {"proj_lanes": 2}
    rebuild()  # 全量重建再跑一次：幂等
    after = {k: v for k, v in _lanes(client, **{"from": day.isoformat()}).items() if k != "now"}
    assert after == before and _db()["proj_lanes"].count_documents({}) == 2


def test_projection_skips_bad_payloads():
    from app.modules.projector.handlers import lanes  # noqa: PLC0415

    base = {"user": "u_local", "source": "x", "dedupeKey": "k", "type": "agent.run.completed",
            "time": "2026-09-30T10:00:00+00:00", "subject": {"zone": "z", "project": "p"}}
    for data in ({"startAt": "2026-09-30T09:00:00", "durationSeconds": 60},
                 {"startAt": "2026-09-30T09:00:00+00:00", "durationSeconds": float("nan")},
                 {"startAt": "2026-09-30T09:00:00+00:00", "durationSeconds": 32 * 86400},
                 {"startAt": "2026-09-30T09:00:00+00:00", "durationSeconds": True}):
        lanes.handle({**base, "data": data})
    lanes.handle({**base, "type": "session.completed", "time": "2026-09-30T08:00:00+00:00",
                  "data": {"startAt": "2026-09-30T09:00:00+00:00", "durationSeconds": 60}})  # 结束早于开始
    assert _db()["proj_lanes"].count_documents({}) == 0


def test_projection_drops_malformed_phase_and_interaction_items(client):
    from app.modules.projector.handlers import lanes  # noqa: PLC0415

    lanes.handle({"user": "u_local", "source": "ext", "dedupeKey": "agent:r1", "type": "agent.run.completed",
                  "time": "2026-09-30T10:00:00+00:00", "subject": {"zone": "z", "project": "p"},
                  "data": {"startAt": "2026-09-30T09:00:00+00:00", "durationSeconds": 60, "outcome": "done",
                           "phases": [1, {"at": "bad", "phase": "idle"}, {"at": "2026-09-30T09:00:10+00:00",
                                                                         "phase": "idle", "detail": 5}],
                           "interactions": [{"kind": "attend", "at": "2026-09-30T09:00:10+00:00"},
                                            {"kind": "reply", "at": "2026-09-30T09:00:20+00:00", "x": 1}]}})
    doc = _db()["proj_lanes"].find_one({})
    assert doc["runId"] == "r1"
    assert doc["phases"] == [{"at": "2026-09-30T09:00:10+00:00", "phase": "idle"}]
    assert doc["interactions"] == [{"kind": "reply", "at": "2026-09-30T09:00:20+00:00"}]
    body = _lanes(client, date="2026-09-30")
    assert [a["runId"] for a in body["agents"]] == ["r1"]
