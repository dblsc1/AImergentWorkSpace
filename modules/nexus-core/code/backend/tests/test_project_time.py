"""``GET /api/core/views/project-time``（契约 v2.48）：区间，按任务；代理只算 working 相位。"""

from __future__ import annotations

from datetime import datetime, time, timedelta

import pytest

API = "/api/core"
URL = f"{API}/views/project-time"


def _tz():
    from app.config import settings  # noqa: PLC0415

    return settings.tz


def _at(day, hh, mm=0):
    return datetime.combine(day, time(hh, mm), _tz())


def _today():
    return datetime.now(_tz()).date()


def _completed(n, project, task, start, minutes, phases, interactions=None):
    from app.modules.projector.handlers import lanes  # noqa: PLC0415

    subject = {"zone": "z", "project": project, **({"task": task} if task else {})}
    lanes.handle({"user": "u_local", "source": "ext", "dedupeKey": f"agent:r{n}", "type": "agent.run.completed",
                  "time": (start + timedelta(minutes=minutes)).isoformat(), "subject": subject,
                  "data": {"startAt": start.isoformat(), "durationSeconds": minutes * 60, "outcome": "done",
                           "agent": "cc", "label": "garden", "taskId": task, "projectId": project,
                           "phases": [{"at": a.isoformat(), "phase": p} for a, p in phases],
                           "interactions": interactions or []}})


def test_empty(client):
    resp = client.get(URL, params={"projectId": "p1"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["tasks"] == [] and body["projectId"] == "p1"


def test_human_and_agent_intervals_by_task(client, seeded):
    task = next(iter(seeded["tasks"].values()))
    pid, tid = task["projectId"], task["id"]
    day = _today() - timedelta(days=2)
    client.post(f"{API}/timer/backfill", json={"taskId": tid, "startAt": _at(day, 9).isoformat(),
                                               "durationSeconds": 1800, "mode": "do"})
    start = _at(day, 10)
    # 10:00 起 60 分钟：working 10:00–10:20，idle 10:20–10:40，working 10:40–11:00
    _completed(1, pid, tid, start, 60, [(start + timedelta(minutes=20), "idle"), (start + timedelta(minutes=40), "working")])
    _completed(2, pid, None, start, 30, [])  # 没挂任务、没报相位 = 整段干活
    _completed(3, "other_project", None, start, 30, [])  # 别的项目不出现
    body = client.get(URL, params={"projectId": pid, "from": (day - timedelta(days=1)).isoformat(),
                                   "to": _today().isoformat()}).json()
    by = {t["taskId"]: t for t in body["tasks"]}
    assert [t["taskId"] for t in body["tasks"]] == [None, tid]  # 没挂任务的在前
    assert [(datetime.fromisoformat(s["start"]).hour, datetime.fromisoformat(s["end"]) - datetime.fromisoformat(s["start"]))
            for s in by[tid]["human"]] == [(9, timedelta(minutes=30))]
    spans = by[tid]["agent"]
    assert [(datetime.fromisoformat(s["start"]).strftime("%H:%M"), datetime.fromisoformat(s["end"]).strftime("%H:%M"))
            for s in spans] == [("10:00", "10:20"), ("10:40", "11:00")]
    assert spans[0]["runId"] == "r1" and spans[0]["agent"] == "garden"
    assert by[None]["human"] == [] and len(by[None]["agent"]) == 1


def test_span_over_seven_days_is_422_and_window_clips(client):
    assert client.get(URL, params={"projectId": "p", "from": "2026-01-01", "to": "2026-01-09"}).status_code == 422
    start = _at(_today() - timedelta(days=3), 23)
    _completed(1, "p", None, start, 120, [])  # 跨零点：只留窗口内的一小时
    day = (_today() - timedelta(days=2)).isoformat()
    body = client.get(URL, params={"projectId": "p", "from": day, "to": day}).json()
    [t] = body["tasks"]
    [s] = t["agent"]
    assert datetime.fromisoformat(s["end"]) - datetime.fromisoformat(s["start"]) == timedelta(hours=1)


def test_time_before_a_now_move_stays_under_the_old_target(client, monkeypatch):
    """v2.48 只算以后：在跑的运行看 cuts，结束后看 segments；切点前算旧去向、切点后算新去向。"""
    from app.modules.timer import service  # noqa: PLC0415

    zone = client.post(f"{API}/planner/zones", json={"name": "区"}).json()["id"]
    p, q = [client.post(f"{API}/planner/projects", json={"zoneId": zone, "name": n}).json()["id"] for n in ("P", "Q")]
    t = client.post(f"{API}/planner/tasks", json={"projectId": q, "name": "t"}).json()["id"]
    real = service._now

    def at(seconds):
        monkeypatch.setattr(service, "_now", lambda: real() + timedelta(seconds=seconds))

    run = client.post(f"{API}/agents/start", json={"agent": "garden", "tool": "x", "projectId": p}).json()["runId"]
    at(600)
    assert client.post(f"{API}/agents/{run}/place", json={"taskId": t, "remember": False, "timeFrom": "now"}).status_code == 200
    at(1800)

    def spans(project):
        body = client.get(URL, params={"projectId": project}).json()
        return {x["taskId"]: [(datetime.fromisoformat(s["end"]) - datetime.fromisoformat(s["start"])).total_seconds()
                              for s in x["agent"]] for x in body["tasks"]}

    for stopped in (False, True):  # 在跑（cuts）与结束后（segments）一样
        if stopped:
            assert client.post(f"{API}/agents/{run}/stop", json={"outcome": "done"}).status_code == 200
        assert spans(p) == {None: [pytest.approx(600, abs=2)]}
        assert spans(q) == {t: [pytest.approx(1200, abs=2)]}


def test_time_lanes_all_projects_one_lane_per_run(client, seeded):
    task = next(iter(seeded["tasks"].values()))
    pid, tid = task["projectId"], task["id"]
    day = _today() - timedelta(days=1)
    client.post(f"{API}/timer/backfill", json={"taskId": tid, "startAt": _at(day, 9).isoformat(),
                                               "durationSeconds": 1800, "mode": "do"})
    start = _at(day, 10)
    _completed(1, pid, tid, start, 60, [(start + timedelta(minutes=20), "idle"), (start + timedelta(minutes=40), "working")])
    _completed(2, "other_project", None, start, 30, [])
    body = client.get(f"{API}/views/time-lanes", params={"from": day.isoformat(), "to": _today().isoformat()}).json()
    assert [(h["projectId"], h["taskId"]) for h in body["human"]] == [(pid, tid)]
    by = {a["runId"]: a for a in body["agents"]}
    assert set(by) == {"r1", "r2"} and by["r1"]["name"] == "garden" and by["r1"]["agent"] == "cc"
    assert [(s["projectId"], s["taskId"]) for s in by["r1"]["segments"]] == [(pid, tid)] * 2
    assert by["r2"]["segments"][0]["projectId"] == "other_project"
    assert client.get(f"{API}/views/time-lanes", params={"from": "2026-01-01", "to": "2026-01-09"}).status_code == 422


def test_time_lanes_since_window_identity_phase_and_attend(client, seeded):
    """v2.54：``since`` 滚动窗口；每条运行带 tool / laneKey / running / phase / attend（裁到窗口）。"""
    task = next(iter(seeded["tasks"].values()))
    pid, tid = task["projectId"], task["id"]
    now = datetime.now(_tz())
    start = now - timedelta(hours=5)
    att = [{"kind": "attend", "at": (start + timedelta(minutes=10)).isoformat(), "until": (start + timedelta(minutes=30)).isoformat()},
           {"kind": "attend", "at": (start - timedelta(hours=3)).isoformat(), "until": (start - timedelta(hours=2)).isoformat()}]
    _completed(1, pid, tid, start, 60, [], interactions=att)
    _completed(2, pid, None, now - timedelta(days=3), 30, [])
    since = (now - timedelta(hours=6)).isoformat()
    body = client.get(f"{API}/views/time-lanes", params={"since": since}).json()
    assert [a["runId"] for a in body["agents"]] == ["r1"]          # 3 天前的那条在窗口外
    a = body["agents"][0]
    assert a["running"] is False and a["phase"] == "working" and a["laneKey"] == "cc\ngarden" and a["projectId"] == pid
    assert [(datetime.fromisoformat(i["start"]), datetime.fromisoformat(i["end"])) for i in a["attend"]] == [
        (start + timedelta(minutes=10), start + timedelta(minutes=30))]   # 窗口外的那段被裁掉
    wide = client.get(f"{API}/views/time-lanes", params={"since": (now - timedelta(days=7)).isoformat()}).json()
    assert {x["runId"] for x in wide["agents"]} == {"r1", "r2"}
    assert client.get(f"{API}/views/time-lanes", params={"since": (now - timedelta(days=9)).isoformat()}).status_code == 422
    assert client.get(f"{API}/views/time-lanes", params={"since": since, "from": "2026-01-01"}).status_code == 422
    assert client.get(f"{API}/views/time-lanes", params={"since": "昨天"}).status_code == 422


def test_time_lanes_since_lists_every_running_working_run(client, monkeypatch):
    """v2.57：在跑、没失联、相位 working 的运行一定在 ``agents[]`` 里（带 running / phase），不论它的干活区间落不落在窗口里。"""
    from app.modules.timer import service  # noqa: PLC0415

    real = service._now
    run = client.post(f"{API}/agents/start", json={"agent": "garden", "tool": "x", "phase": "working"}).json()["runId"]

    def listed():
        since = (service._now() - timedelta(hours=3)).isoformat()
        body = client.get(f"{API}/views/time-lanes", params={"since": since}).json()
        return {a["runId"]: a for a in body["agents"]}

    monkeypatch.setattr(service, "_now", lambda: real() + timedelta(seconds=600))
    a = listed()[run]
    assert (a["running"], a["lost"], a["phase"]) == (True, False, "working")
    # 超过遗忘上限（12 小时）的在跑运行：干活区间止于上限、早已出了 3 小时窗口，运行仍要列出（segments 为空）
    monkeypatch.setattr(service, "_now", lambda: real() + timedelta(hours=20))
    a = listed().get(run)
    assert a is not None and (a["running"], a["phase"]) == (True, "working") and a["segments"] == []
