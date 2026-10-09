"""``views/lanes`` 的封顶（契约 v2.23）：按代理身份封顶，吵闹的代理挤不掉别的代理，丢掉的折进 ``dropped``。

事故：外部上报者一天里同名短运行刷了几百条，全局「最新 200 条」把所有代理上午的运行都挤没了。
"""

from __future__ import annotations

from datetime import datetime, time, timedelta

from app.modules.views import lane_cap

LANES = "/api/core/views/lanes"


def _tz():
    from app.config import settings  # noqa: PLC0415

    return settings.tz


def _day():
    return datetime.now(_tz()).date() - timedelta(days=1)


def _at(hh: int, mm: int = 0, day=None) -> datetime:
    return datetime.combine(day or _day(), time(hh, mm), _tz())


def _put_run(run_id: str, label: str, start: datetime, seconds: int, agent: str = "cc") -> None:
    from app.modules.projector import repo  # noqa: PLC0415

    repo.apply_lane({"user": "u_local", "key": run_id, "kind": "run", "runId": run_id, "agent": agent, "label": label,
                     "startAt": start, "endAt": start + timedelta(seconds=seconds), "durationSeconds": seconds,
                     "phases": [], "interactions": [], "taskId": None, "projectId": None, "outcome": "done"})


def _get(client) -> dict:
    resp = client.get(LANES, params={"date": _day().isoformat()})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _flood(label: str, n: int, first: datetime | None = None, seconds: int = 30) -> None:
    first = first or _at(13)
    for i in range(n):
        _put_run(f"{label}-{i}", label, first + timedelta(seconds=60 * i), seconds)


def test_noisy_lane_does_not_evict_other_lanes_and_totals_still_add_up(client):
    _flood("A", 500, _at(13))  # 下午 500 条短运行（共 500 × 30 秒）
    for label in ("B", "C"):
        for i in range(3):
            _put_run(f"{label}-{i}", label, _at(8 + i), 600)
    body = _get(client)
    by: dict[str, list[dict]] = {}
    for a in body["agents"]:
        by.setdefault(a["label"], []).append(a)
    assert len(by["B"]) == len(by["C"]) == 3, "上午的运行一条都不能被挤掉"
    assert len(by["A"]) == lane_cap.MAX_RUNS_PER_LANE
    # A 留下的是最新的 100 条
    assert min(a["startAt"] for a in by["A"]) == (_at(13) + timedelta(seconds=60 * 400)).isoformat()
    (dropped,) = body["dropped"]
    assert (dropped["label"], dropped["runs"], dropped["elapsedSeconds"]) == ("A", 400, 400 * 30)
    assert body["truncated"] is True
    shown_secs = sum(a["elapsedSeconds"] for a in by["A"])
    assert (len(by["A"]) + dropped["runs"], shown_secs + dropped["elapsedSeconds"]) == (500, 500 * 30)


def test_more_than_200_lanes_keeps_the_newest_200(client):
    for i in range(210):
        _put_run(f"r{i}", f"L{i:03d}", _at(1) + timedelta(minutes=i), 60)
    body = _get(client)
    assert len(body["agents"]) == 200 and body["truncated"] is True
    assert {a["label"] for a in body["agents"]} == {f"L{i:03d}" for i in range(10, 210)}
    assert sorted(d["label"] for d in body["dropped"]) == [f"L{i:03d}" for i in range(10)]


def test_nothing_dropped_means_not_truncated(client):
    _flood("A", 3)
    body = _get(client)
    assert len(body["agents"]) == 3 and body["dropped"] == [] and body["truncated"] is False


def test_total_ceiling_is_the_last_resort(client, monkeypatch):
    monkeypatch.setattr(lane_cap, "MAX_RUNS_TOTAL", 5)
    _flood("A", 4, _at(8))
    _flood("B", 4, _at(9))
    body = _get(client)
    assert len(body["agents"]) == 5 and body["truncated"] is True
    assert [(d["label"], d["runs"]) for d in body["dropped"]] == [("A", 3)]  # 最旧的先丢


def test_dropped_seconds_are_clipped_to_the_window(client, monkeypatch):
    monkeypatch.setattr(lane_cap, "MAX_RUNS_PER_LANE", 1)
    # 最旧的一条从前一天 23:00 跨零点到窗口 00:30：只算窗口里的 1800 秒
    _put_run("old", "A", _at(23, day=_day() - timedelta(days=1)), 5400)
    _put_run("new", "A", _at(10), 60)
    body = _get(client)
    assert [a["runId"] for a in body["agents"]] == ["new"]
    assert [(d["runs"], d["elapsedSeconds"]) for d in body["dropped"]] == [(1, 1800)]


def test_hidden_lane_dropped_summary_is_not_exposed(client, monkeypatch):
    monkeypatch.setattr(lane_cap, "MAX_RUNS_PER_LANE", 1)
    _flood("secret", 3, _at(8))
    _flood("open", 3, _at(9))
    body = {"agent": "cc", "label": "secret", "hidden": True}
    assert client.put("/api/core/lanes/prefs/agent", json=body).status_code == 200
    body = _get(client)
    assert [d["label"] for d in body["dropped"]] == ["open"]
    assert [a["label"] for a in body["agents"]] == ["open"]


def test_report_scope_never_gets_dropped(client, monkeypatch):
    from app.modules.views import lanes  # noqa: PLC0415

    monkeypatch.setattr(lane_cap, "MAX_RUNS_PER_LANE", 1)
    _flood("A", 3)
    monkeypatch.setattr(lanes, "caller", lambda: type("C", (), {"scope": "report"})())
    body = lanes.get_lanes(_day().isoformat()).model_dump()
    assert body["dropped"] == [] and body["hiddenAgents"] == []


def test_live_runs_are_never_dropped(client, monkeypatch):
    monkeypatch.setattr(lane_cap, "MAX_RUNS_PER_LANE", 1)
    _flood("cc", 3, _at(8))
    for _ in range(2):
        resp = client.post("/api/core/agents/start", json={"agent": "cc", "tool": "t", "label": "cc"})
        assert resp.status_code == 201
    live = [a for a in client.get(LANES).json()["agents"] if a["endAt"] is None]
    assert len(live) == 2


def _fake_live(monkeypatch, runs: list[dict]) -> None:
    """替换活状态读取：/agents/start 不限开着的个数，直接造几百上千条在跑的运行。"""
    from app.modules.views import lanes  # noqa: PLC0415

    now = datetime.now(_tz())
    rows = [{"runId": r["runId"], "agent": "cc", "tool": "t", "label": r["label"], "startTs": r["startTs"], "endTs": None,
             "phases": r.get("phases", []), "elapsedSeconds": 0, "overdue": False, "unverified": False} for r in runs]
    monkeypatch.setattr(lanes.timer_service, "list_lane_runs", lambda user=None: (now, rows))


def test_live_runs_are_capped_and_rest_goes_to_dropped(client, monkeypatch):
    start = _at(1)
    live = [{"runId": f"L{i}", "label": f"lab{i % 300}", "startTs": start + timedelta(seconds=i)} for i in range(1500)]
    _fake_live(monkeypatch, live)
    _flood("closed", 5, _at(0, 10))
    body = _get(client)
    got = [a for a in body["agents"] if a["endAt"] is None]
    assert len(got) == lane_cap.MAX_LIVE == 500
    assert {a["runId"] for a in got} == {f"L{i}" for i in range(1000, 1500)}  # 留的是最新的
    assert sum(a["label"] == "closed" for a in body["agents"]) == 5  # 已结束的照样有位置
    assert body["truncated"] is True
    assert sum(d["runs"] for d in body["dropped"] if d["label"].startswith("lab")) == 1000
    end = datetime.fromisoformat(body["windowEnd"])  # 昨天的窗口：在跑的算到窗口末尾
    truth = sum(int((end - r["startTs"]).total_seconds()) for r in live[:1000])
    assert sum(d["elapsedSeconds"] for d in body["dropped"] if d["label"].startswith("lab")) == truth


def test_hidden_flood_does_not_evict_visible_lane(client):
    _flood("vis", 5, _at(2))  # 比洪水更早
    before = _get(client)
    for i in range(25):
        _flood(f"h{i}", 100, _at(4) + timedelta(seconds=i))
        assert client.put("/api/core/lanes/prefs/agent", json={"agent": "cc", "label": f"h{i}", "hidden": True}).status_code == 200
    body = _get(client)
    assert sum(a["label"] == "vis" for a in body["agents"]) == 5
    assert body["dropped"] == [] and body["truncated"] is False
    assert body["hiddenAgents"] != [] and len(body["hiddenAgents"]) == 25 and before["hiddenAgents"] == []


def test_hidden_lane_live_waiting_run_still_counted(client, monkeypatch):
    _fake_live(monkeypatch, [{"runId": "w", "label": "sec", "startTs": _at(1),
                              "phases": [{"at": _at(2).isoformat(), "phase": "waiting_input"}]}])
    _flood("sec", 150, _at(8))  # 已结束的把名额填满也不该影响
    assert client.put("/api/core/lanes/prefs/agent", json={"agent": "cc", "label": "sec", "hidden": True}).status_code == 200
    body = _get(client)
    assert body["hiddenWaiting"] == 1 and body["agents"] == [] and body["dropped"] == [] and body["truncated"] is False


def test_hidden_live_is_bounded_and_skips_heavy_work(client, monkeypatch):
    from app.modules.views import lanes  # noqa: PLC0415

    ph = [{"at": _at(2).isoformat(), "phase": "working"}] * 50
    ph[-1] = {"at": _at(3).isoformat(), "phase": "waiting_input"}
    _fake_live(monkeypatch, [{"runId": f"H{i}", "label": "sec", "startTs": _at(1) + timedelta(seconds=i), "phases": ph}
                             for i in range(5000)])
    assert client.put("/api/core/lanes/prefs/agent", json={"agent": "cc", "label": "sec", "hidden": True}).status_code == 200
    calls = {"item": 0, "att": 0}
    item, att = lanes._run_item, lanes._attention
    monkeypatch.setattr(lanes, "_run_item", lambda *a: calls.__setitem__("item", calls["item"] + 1) or item(*a))
    monkeypatch.setattr(lanes, "_attention", lambda *a: calls.__setitem__("att", calls["att"] + 1) or att(*a))
    body = _get(client)
    assert calls == {"item": 0, "att": 0}
    assert body["agents"] == [] and body["dropped"] == [] and body["truncated"] is False
    assert body["hiddenWaiting"] == lane_cap.MAX_LIVE
    assert [(h["label"], h["live"], h["phase"]) for h in body["hiddenAgents"]] == [("sec", True, "waiting_input")]


def test_hidden_summary_same_as_unbounded_when_uncapped(client, monkeypatch):
    from app.modules.views import lanes  # noqa: PLC0415
    from app.modules.views.lane_order import arrange  # noqa: PLC0415

    runs = [{"runId": f"w{i}", "label": "sec", "startTs": _at(1, i),
             "phases": [{"at": _at(2, i).isoformat(), "phase": "waiting_input" if i % 2 else "idle"}]} for i in range(6)]
    _fake_live(monkeypatch, runs + [{"runId": "v", "label": "vis", "startTs": _at(1)}])
    assert client.put("/api/core/lanes/prefs/agent", json={"agent": "cc", "label": "sec", "hidden": True}).status_code == 200
    body = _get(client)
    start = datetime.fromisoformat(body["windowStart"])
    end = datetime.fromisoformat(body["windowEnd"])
    now, rows = lanes.timer_service.list_lane_runs("u_local")
    full = arrange([lanes._run_item(r, start, end) for r in rows], lanes.prefs_service.load("u_local"), now)
    assert (body["hiddenAgents"], body["hiddenWaiting"], body["stalePinned"]) == (full[1], full[2], full[3])
    assert body["hiddenWaiting"] == 3


def test_pinned_lane_with_all_live_dropped_is_not_stale(client, monkeypatch):
    live = [{"runId": f"p{i}", "label": "pin", "startTs": _at(1) + timedelta(seconds=i)} for i in range(10)]
    live += [{"runId": f"n{i}", "label": "noise", "startTs": _at(2) + timedelta(seconds=i)} for i in range(lane_cap.MAX_LIVE)]
    _fake_live(monkeypatch, live)
    assert client.put("/api/core/lanes/prefs/agent", json={"agent": "cc", "label": "pin", "pinned": True}).status_code == 200
    body = _get(client)
    assert not any(a["label"] == "pin" for a in body["agents"]) and body["stalePinned"] == []


def test_live_counts_against_total_ceiling(client, monkeypatch):
    monkeypatch.setattr(lane_cap, "MAX_RUNS_TOTAL", 5)
    _fake_live(monkeypatch, [{"runId": f"L{i}", "label": "lv", "startTs": _at(20) + timedelta(seconds=i)} for i in range(3)])
    _flood("A", 4, _at(8))
    body = _get(client)
    assert len(body["agents"]) == 5 and sum(a["endAt"] is None for a in body["agents"]) == 3
    assert [(d["label"], d["runs"]) for d in body["dropped"]] == [("A", 2)]
