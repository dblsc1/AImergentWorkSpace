"""代理只算干活的时间（契约 v2.31）：干活区间、按本地日切分、老事件回退、幂等、重建一致。"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.modules.projector import rebuild as rebuild_module
from app.modules.projector import repo as projector_repo
from app.modules.projector.handlers import agent_daily_stats
from app.modules.timer.agent_working import working_days

API = "/api/core"
SH = ZoneInfo("Asia/Shanghai")
T0 = datetime.fromisoformat("2026-10-13T09:00:00+08:00")


def _ph(*pairs):
    return [{"at": at, "phase": ph} for at, ph in pairs]


def test_idle_heavy_run_counts_only_working_spans():
    end = datetime.fromisoformat("2026-10-13T20:00:00+08:00")
    phases = _ph(("2026-10-13T09:00:00+08:00", "working"), ("2026-10-13T09:10:00+08:00", "idle"),
                 ("2026-10-13T18:00:00+08:00", "working"), ("2026-10-13T18:05:00+08:00", "error"),
                 ("2026-10-13T19:00:00+08:00", "working"))  # 尾部 working 算到运行结束
    total, days = working_days(phases, T0, end, SH)
    assert total == 600 + 300 + 3600 and days == [{"date": "2026-10-13", "seconds": 4500}]


def test_time_before_first_phase_counts_as_working():
    # 开始 09:00，第一条相位 idle 在 09:10：开头 10 分钟算干活
    assert working_days(_ph(("2026-10-13T09:10:00+08:00", "idle")), T0, T0.replace(hour=12), SH)[0] == 600


def test_only_phase_after_end_counts_whole_run():
    phases = _ph(("2026-10-13T15:00:00+08:00", "idle"))  # 在结束（10:00）之后
    assert working_days(phases, T0, T0.replace(hour=10), SH)[0] == 3600


def test_dst_day_is_23_hours_and_total_never_exceeds_wall_clock():
    ny = ZoneInfo("America/New_York")
    a = datetime.fromisoformat("2026-03-07T12:00:00-05:00")
    b = datetime.fromisoformat("2026-03-09T12:00:00-04:00")  # 墙钟 47 小时
    total, days = working_days(None, a, b, ny)
    assert days == [{"date": "2026-03-07", "seconds": 12 * 3600}, {"date": "2026-03-08", "seconds": 23 * 3600},
                    {"date": "2026-03-09", "seconds": 12 * 3600}]
    assert total == int((b - a).total_seconds()) == 47 * 3600


def test_total_is_floored_not_above_duration():
    s = datetime.fromisoformat("2026-10-13T23:59:59.600+08:00")
    e = datetime.fromisoformat("2026-10-14T00:00:01.300+08:00")  # 1.7 秒，跨零点
    assert working_days(None, s, e, SH)[0] <= int((e - s).total_seconds())


def test_mixed_offsets_compare_as_instants():
    # 01:00+00:00 == 09:00+08:00；01:30+00:00 == 09:30+08:00
    phases = _ph(("2026-10-13T01:00:00+00:00", "working"), ("2026-10-13T09:30:00+08:00", "idle"))
    assert working_days(phases, T0, T0.replace(hour=12), SH)[0] == 1800


def test_run_spanning_midnight_splits_two_days():
    total, days = working_days(None, datetime.fromisoformat("2026-10-13T23:00:00+08:00"),
                               datetime.fromisoformat("2026-10-14T01:30:00+08:00"), SH)
    assert total == 9000
    assert days == [{"date": "2026-10-13", "seconds": 3600}, {"date": "2026-10-14", "seconds": 5400}]


def test_whole_middle_day_is_credited():
    phases = _ph(("2026-10-13T22:00:00+08:00", "working"))
    _, days = working_days(phases, datetime.fromisoformat("2026-10-13T22:00:00+08:00"),
                           datetime.fromisoformat("2026-10-15T02:00:00+08:00"), SH)
    assert days == [{"date": "2026-10-13", "seconds": 7200}, {"date": "2026-10-14", "seconds": 86400},
                    {"date": "2026-10-15", "seconds": 7200}]


def test_phaseless_run_is_whole_duration_and_never_working_run_is_zero():
    end = T0.replace(hour=10)
    assert working_days(None, T0, end, SH)[0] == 3600
    assert working_days(_ph(("2026-10-13T09:00:00+08:00", "idle")), T0, end, SH) == (
        0, [{"date": "2026-10-13", "seconds": 0}])


# ---- 投影：经事件入口 ----

def _post(client, n, *, start, seconds, days=None, source="agent-hook"):
    data = {"agent": "a", "tool": "x", "startAt": start, "durationSeconds": seconds, "outcome": "done"}
    if days is not None:
        data["workingSeconds"] = sum(d["seconds"] for d in days)
        data["days"] = days
    env = {"spec": "yq-event/v1", "id": f"evt_aw{n}", "dedupeKey": f"aw-{n}", "type": "agent.run.completed",
           "source": source, "user": "u_local", "time": start,
           "subject": {"zone": "z1", "project": "p1", "task": "t1"}, "data": data}
    assert client.post(f"{API}/events", json=env).json()["accepted"] == 1
    return env


def _days(client):
    body = client.get(f"{API}/views/agent-time").json()
    return body["days"], body["runs"], body["totalSeconds"]


def test_projection_uses_days_and_counts_run_once(client):
    start = "2026-10-13T23:00:00+08:00"
    _post(client, 1, start=start, seconds=90000,
          days=[{"date": "2026-10-13", "seconds": 3600}, {"date": "2026-10-15", "seconds": 7200}])
    days, runs, total = _days(client)
    assert days == [{"date": "2026-10-13", "seconds": 3600, "runs": 1},
                    {"date": "2026-10-15", "seconds": 7200, "runs": 0}]
    assert runs == 1 and total == 10800  # 墙钟 90000 不进投影


def test_old_event_without_days_uses_start_day_rule(client):
    _post(client, 1, start="2026-10-13T09:00:00+00:00", seconds=100000)
    days, runs, total = _days(client)
    assert days == [{"date": "2026-10-13", "seconds": 100000, "runs": 1}] and total == 100000


def test_reapply_is_idempotent_and_rebuild_equal(client):
    env = _post(client, 1, start="2026-10-13T23:00:00+08:00", seconds=9000,
                days=[{"date": "2026-10-13", "seconds": 3600}, {"date": "2026-10-14", "seconds": 5400}])
    _post(client, 2, start="2026-10-14T09:00:00+00:00", seconds=60)  # 老形状混着重建
    before = _days(client)
    agent_daily_stats.handle({**env, "user": "u_local"})  # 同一事件重放
    assert _days(client) == before
    rebuild_module.rebuild(only="proj_agent_daily_stats")
    assert _days(client) == before
    rows = projector_repo.read_agent_daily_stats("u_local")
    assert sorted(r["runs"] for r in rows) == [1, 1]  # 10-14 行 = 首条的 0 + 老事件的 1


def test_run_end_writes_working_fields(client):
    run = client.post(f"{API}/agents/start", json={"agent": "a", "tool": "x", "phase": "working"}).json()
    client.post(f"{API}/agents/{run['runId']}/stop", json={"outcome": "done"})
    ev = client.get(f"{API}/events").json()
    item = next(e for e in (ev["items"] if isinstance(ev, dict) else ev) if e["type"] == "agent.run.completed")
    assert item["data"]["workingSeconds"] == sum(d["seconds"] for d in item["data"]["days"])
    assert item["data"]["workingSeconds"] <= item["data"]["durationSeconds"]


def test_open_run_reports_working_so_far(client):
    client.post(f"{API}/agents/start", json={"agent": "a", "tool": "x", "phase": "working"})
    run = client.get(f"{API}/views/agent-time").json()["open"][0]
    assert 0 <= run["workingSeconds"] <= run["elapsedSeconds"]
    client.post(f"{API}/agents/start", json={"agent": "b", "tool": "x"})  # 没报相位：整段算干活
    assert all(r["workingSeconds"] == r["elapsedSeconds"] for r in client.get(f"{API}/views/agent-time").json()["open"] if r["agent"] == "b")


def test_external_source_days_ignored_and_over_32_rows_ignored(client):
    d = [{"date": "2026-10-13", "seconds": 5}]
    _post(client, 1, start="2026-10-13T09:00:00+00:00", seconds=100, days=d, source="test-hook")
    assert _days(client)[0] == [{"date": "2026-10-13", "seconds": 100, "runs": 1}]
    many = [{"date": f"2026-09-{i:02d}", "seconds": 1} for i in range(1, 31)] + [
        {"date": "2026-10-01", "seconds": 1}, {"date": "2026-10-02", "seconds": 1}, {"date": "2026-10-03", "seconds": 1}]
    _post(client, 2, start="2026-10-20T09:00:00+00:00", seconds=50, days=many)
    assert _days(client)[2] == 150  # 33 条 -> 旧规则


def test_partial_apply_then_replay_counts_each_day_once(client):
    env = _post(client, 1, start="2026-10-13T23:00:00+08:00", seconds=9000,
                days=[{"date": "2026-10-13", "seconds": 3600}, {"date": "2026-10-14", "seconds": 5400}])
    # 模拟只写成了首日行就崩：清掉后手工只应用首日
    projector_repo.clear_agent_daily_stats()
    key = agent_daily_stats.applied_key(env)
    projector_repo.apply_agent_daily_stat(user="u_local", dedupe_key=key, date="2026-10-13",
                                          project_id="p1", task_id="t1", agent="a", seconds=3600, runs=1)
    agent_daily_stats.handle({**env, "user": "u_local"})
    assert _days(client) == ([{"date": "2026-10-13", "seconds": 3600, "runs": 1},
                              {"date": "2026-10-14", "seconds": 5400, "runs": 0}], 1, 9000)
