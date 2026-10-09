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
