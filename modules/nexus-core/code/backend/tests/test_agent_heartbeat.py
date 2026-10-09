"""代理运行的心跳与失联（契约 v2.18「心跳与失联」；对外协议 ``contracts/agent.lane.v1``）。

要害：活性规则住在服务端——会发心跳的运行 30 分钟没信号就是失联，关在**最后一次信号的时刻**
（代理时长不虚增）；不发心跳的运行（老适配器）一切照旧。时钟全靠拨，不 sleep。
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

API = "/api/core"
AGENTS = f"{API}/agents"
A = {"X-Nexus-Tenant": "ch_aaaa"}
B = {"X-Nexus-Tenant": "ch_bbbb"}


def _db():
    from app.repo import get_db  # noqa: PLC0415

    return get_db()


def _doc(run_id: str) -> dict | None:
    return _db()["agent_runs"].find_one({"runId": run_id}, {"_id": 0})


def _events(user: str = "u_local") -> list[dict]:
    return list(_db()["events"].find({"type": "agent.run.completed", "user": user}, {"_id": 0}))


def _start(client, expect=201, headers=None, **body):
    resp = client.post(f"{AGENTS}/start", json={"agent": "cc", "tool": "claude-code", **body}, headers=headers or {})
    assert resp.status_code == expect, resp.text
    return resp.json()


def _beat(client, run_id, expect=200, headers=None, **kw):
    resp = client.post(f"{AGENTS}/{run_id}/heartbeat", headers=headers or {}, **kw)
    assert resp.status_code == expect, resp.text
    return resp.json()


def _lane(client, run_id):
    return next(a for a in client.get(f"{API}/views/lanes").json()["agents"] if a["runId"] == run_id)


def _seen(run_id: str) -> datetime:
    return datetime.fromisoformat(_doc(run_id)["lastSeenAt"])


@pytest.fixture()
def shift_clock(monkeypatch):
    """把 timer 子边界的服务端时钟拨到「真实此刻 + delta」（每次调用都是绝对偏移，不累加）。"""
    from app.modules.timer import service  # noqa: PLC0415

    real = service._now

    def shift(**delta):
        monkeypatch.setattr(service, "_now", lambda: real() + timedelta(**delta))

    return shift


# ─────────────────────────────────────────── 常量与响应


def test_constants_are_thirty_and_fifteen_minutes():
    from app.modules.timer import agent_liveness  # noqa: PLC0415

    assert agent_liveness.AGENT_HEARTBEAT_SECONDS == 900
    assert agent_liveness.AGENT_LOST_AFTER_SECONDS == 1800


def test_start_and_heartbeat_responses_carry_heartbeat_seconds(client):
    run = _start(client, clientKey="k1", heartbeat=True)
    assert run["heartbeatSeconds"] == 900
    assert _start(client, expect=200, clientKey="k1")["heartbeatSeconds"] == 900  # 回原运行时也带
    assert _beat(client, run["runId"]) == {
        "runId": run["runId"], "applied": True, "reason": None, "heartbeatSeconds": 900}


# ─────────────────────────────────────────── lastSeenAt：每个收下的信号都更新


def test_last_seen_moves_on_start_phase_and_heartbeat(client, shift_clock):
    run = _start(client, clientKey="k1", heartbeat=True)
    t0 = datetime.fromisoformat(run["startedAt"])
    assert _seen(run["runId"]) == t0

    shift_clock(minutes=5)
    at = (t0 + timedelta(minutes=5)).isoformat()
    client.post(f"{AGENTS}/{run['runId']}/phase", json={"phase": "working", "at": at})
    assert _seen(run["runId"]) >= t0 + timedelta(minutes=5)
    dup = client.post(f"{AGENTS}/{run['runId']}/phase", json={"phase": "working", "at": at}).json()
    assert dup["reason"] == "duplicate"

    shift_clock(minutes=10)
    assert client.post(f"{AGENTS}/{run['runId']}/phase", json={"phase": "working", "at": at}).json()["applied"] is False
    assert _seen(run["runId"]) >= t0 + timedelta(minutes=10)  # 重复的相位没写进 phases，但它是信号

    shift_clock(minutes=20)
    _beat(client, run["runId"])
    assert _seen(run["runId"]) >= t0 + timedelta(minutes=20)

    shift_clock(minutes=30)
    assert _start(client, expect=200, clientKey="k1")["runId"] == run["runId"]  # 同 key 再 start 也是信号
    assert _seen(run["runId"]) >= t0 + timedelta(minutes=30)
    assert _doc(run["runId"])["startedAt"] == run["startedAt"]


def test_heartbeat_does_not_touch_phases_or_version(client):
    run = _start(client, phase="idle", heartbeat=True)
    before = _doc(run["runId"])
    _beat(client, run["runId"], json={})  # 空体与 {} 都收
    after = _doc(run["runId"])
    assert {**after, "lastSeenAt": None} == {**before, "lastSeenAt": None, "beatCount": 1}  # 只多一个计数


# ─────────────────────────────────────────── 失联：读端只标不写


def test_lost_after_thirty_minutes_shown_by_lanes_without_writing(client, shift_clock):
    run = _start(client, phase="working", heartbeat=True)
    shift_clock(minutes=29)
    live = _lane(client, run["runId"])
    assert live["lost"] is False and live["lastSeenAt"] is not None and live["endAt"] is None

    shift_clock(minutes=31)
    lost = _lane(client, run["runId"])
    assert lost["lost"] is True and lost["endAt"] is None and lost["outcome"] is None
    assert lost["overdue"] is False and lost["elapsedSeconds"] == 0  # 止于最后一次信号（= 开始）
    assert datetime.fromisoformat(lost["lastSeenAt"]) == datetime.fromisoformat(run["startedAt"])
    assert "closing" not in _doc(run["runId"]) and _events() == []  # views/lanes 不写


def test_beating_run_is_never_lost_and_never_capped(client, shift_clock, monkeypatch):
    import dataclasses  # noqa: PLC0415

    from app import config  # noqa: PLC0415

    monkeypatch.setattr(config, "settings", dataclasses.replace(config.settings, agent_run_timeout_hours=1))
    run = _start(client, heartbeat=True)
    for minutes in range(15, 181, 15):  # 3 小时，远超 1 小时的遗忘上限
        shift_clock(minutes=minutes)
        assert _beat(client, run["runId"])["applied"] is True
    lane = _lane(client, run["runId"])
    assert (lane["lost"], lane["overdue"]) == (False, False) and lane["elapsedSeconds"] >= 3 * 3600
    assert [a["runId"] for a in client.get(f"{API}/views/current").json()["agents"]] == [run["runId"]]
    assert _events() == []


# ─────────────────────────────────────────── 失联：惰性关闭，结束 = 最后一次信号


def test_lazy_close_ends_at_last_seen_and_agent_time_is_not_inflated(client, shift_clock):
    run = _start(client, phase="working", heartbeat=True)
    t0 = datetime.fromisoformat(run["startedAt"])
    shift_clock(minutes=10)
    client.post(f"{AGENTS}/{run['runId']}/phase", json={"phase": "idle", "at": (t0 + timedelta(minutes=10)).isoformat()})
    shift_clock(minutes=20)
    _beat(client, run["runId"])
    seen = _seen(run["runId"])

    shift_clock(hours=5)  # CLI 早就没了；5 小时后才有人来读
    assert client.get(f"{API}/views/current").json()["agents"] == []  # 读时收掉
    [event] = _events()
    assert event["dedupeKey"] == f"agent:{run['runId']}"
    assert event["data"]["outcome"] == "lost"
    assert datetime.fromisoformat(event["time"]) == seen  # 不是现在，也不是开始 + 上限
    assert 20 * 60 <= event["data"]["durationSeconds"] < 21 * 60
    assert [p["phase"] for p in event["data"]["phases"]] == ["working", "idle"]

    stats = client.get(f"{API}/views/agent-time").json()
    assert (stats["totalSeconds"], stats["runs"], stats["open"]) == (event["data"]["durationSeconds"], 1, [])
    closed = _lane(client, run["runId"])  # 已结束：来自投影
    assert closed["outcome"] == "lost" and closed["lost"] is False and closed["lastSeenAt"] is None
    assert abs(datetime.fromisoformat(closed["endAt"]) - seen) < timedelta(seconds=1)  # 投影按整秒时长算终点


def test_signal_between_read_and_close_keeps_the_run(client, shift_clock):
    """读到「失联」之后、打关闭标记之前又来了心跳：标记带着读到的 lastSeenAt 条件，不关。"""
    from app.modules.timer import agent_liveness, repo  # noqa: PLC0415

    run = _start(client, heartbeat=True)
    stale = _doc(run["runId"])
    later = datetime.fromisoformat(run["startedAt"]) + timedelta(hours=1)
    repo.touch_agent_run("u_local", run["runId"], later.isoformat())
    assert agent_liveness.mark_lost(stale, later) is None
    assert "closing" not in _doc(run["runId"])


# ─────────────────────────────────────────── 不发心跳的运行：一切照旧


def test_run_without_heartbeat_capability_is_unaffected(client, shift_clock):
    run = _start(client, phase="idle")  # 老适配器 / 检测程序的旧桥：不带 heartbeat
    shift_clock(hours=3)
    lane = _lane(client, run["runId"])
    assert (lane["lost"], lane["overdue"], lane["endAt"]) == (False, False, None)
    assert [a["runId"] for a in client.get(f"{API}/views/current").json()["agents"]] == [run["runId"]]
    assert _events() == []

    shift_clock(hours=13)  # 遗忘超时照旧兜底
    assert _lane(client, run["runId"])["overdue"] is True
    assert client.get(f"{API}/views/current").json()["agents"] == []
    assert _events()[0]["data"]["outcome"] == "timeout"


def test_first_heartbeat_declares_capability(client, shift_clock):
    run = _start(client)
    assert "heartbeat" not in _doc(run["runId"])
    shift_clock(hours=2)
    assert _beat(client, run["runId"])["applied"] is True  # 之前空闲 2 小时不算失联
    assert _doc(run["runId"])["heartbeat"] is True
    shift_clock(hours=2, minutes=31)
    assert _lane(client, run["runId"])["lost"] is True


# ─────────────────────────────────────────── 迟到的信号 → closed → 同 key 再 start 开新运行


def test_late_signals_after_lost_get_closed_then_restart_opens_new_run_now(client, shift_clock):
    old = _start(client, clientKey="k1", heartbeat=True, label="garden")
    shift_clock(hours=2)
    late = _beat(client, old["runId"])  # 心跳是写端点：先收失联，再回 closed
    assert late == {"runId": old["runId"], "applied": False, "reason": "closed", "heartbeatSeconds": 900}
    at = (datetime.fromisoformat(old["startedAt"]) + timedelta(hours=2)).isoformat()
    phase = client.post(f"{AGENTS}/{old['runId']}/phase", json={"phase": "working", "at": at}).json()
    assert (phase["applied"], phase["reason"]) == (False, "closed")
    stop = client.post(f"{AGENTS}/{old['runId']}/stop", json={"outcome": "done"}).json()
    assert (stop["duplicate"], stop["outcome"], stop["durationSeconds"]) == (True, "lost", 1)

    new = _start(client, clientKey="k1", heartbeat=True, label="garden")  # 201：新运行
    assert new["runId"] != old["runId"]
    started = datetime.fromisoformat(new["startedAt"])
    assert started >= datetime.fromisoformat(old["startedAt"]) + timedelta(hours=2)  # 从现在起，不是原来的起点
    assert _seen(new["runId"]) == started and len(_events()) == 1


def test_restart_itself_sweeps_the_lost_run(client, shift_clock):
    old = _start(client, clientKey="k1", heartbeat=True)
    shift_clock(minutes=45)
    new = _start(client, clientKey="k1", heartbeat=True)  # 没有先收到 closed 也一样：start 先收失联
    assert new["runId"] != old["runId"]
    assert _events()[0]["data"]["outcome"] == "lost" and _doc(old["runId"]) is None


# ─────────────────────────────────────────── beatSource / beatCount：两种发心跳的办法可以比
#
# 一天用下来按 beatSource 分组看：发了几下（beatCount）、多少条是 lost、多少条是适配器自己 stop 的。


def test_beat_source_and_count_are_stored_shown_and_kept_on_the_closed_run(client, shift_clock):
    run = _start(client, clientKey="k1")
    live = _lane(client, run["runId"])
    assert (live["beatSource"], live["beatCount"]) == (None, None)  # 没报过：键在，值 null

    _beat(client, run["runId"], json={"beatSource": "monitor"})
    _beat(client, run["runId"], json={"beatSource": "monitor"})
    _beat(client, run["runId"])  # 不带 beatSource：计数照加，来源不动
    live = _lane(client, run["runId"])
    assert (live["beatSource"], live["beatCount"]) == ("monitor", 3)
    _start(client, expect=200, clientKey="k1", beatSource="companion")  # 同 key 再 start 带了就换；不算一次心跳
    assert (_doc(run["runId"])["beatSource"], _doc(run["runId"])["beatCount"]) == ("companion", 3)

    shift_clock(hours=2)
    client.get(f"{API}/views/current")  # 收成 lost
    [event] = _events()
    assert (event["data"]["outcome"], event["data"]["beatSource"], event["data"]["beatCount"]) == ("lost", "companion", 3)
    closed = _lane(client, run["runId"])
    assert (closed["outcome"], closed["beatSource"], closed["beatCount"]) == ("lost", "companion", 3)


def test_beat_source_on_start_and_stopped_run_keeps_it(client):
    run = _start(client, heartbeat=True, beatSource="wrapper")
    assert _doc(run["runId"])["beatSource"] == "wrapper" and "beatCount" not in _doc(run["runId"])
    client.post(f"{AGENTS}/{run['runId']}/stop", json={"outcome": "cancelled"})
    [event] = _events()
    assert (event["data"]["outcome"], event["data"]["beatSource"]) == ("cancelled", "wrapper")
    assert "beatCount" not in event["data"]  # 一下都没发：不出现
    plain = _start(client)
    client.post(f"{AGENTS}/{plain['runId']}/stop", json={"outcome": "done"})
    assert "beatSource" not in _events()[-1]["data"]  # 不发心跳的运行：事实与 v2.17 逐键相同


@pytest.mark.parametrize("bad", ["", "Monitor", "has space", "-x", "x" * 33, 7, ["monitor"]])
def test_beat_source_must_be_a_short_slug(client, bad):
    run = _start(client)
    _beat(client, run["runId"], expect=422, json={"beatSource": bad})
    _start(client, expect=422, beatSource=bad)
    assert "beatSource" not in _doc(run["runId"]) and "heartbeat" not in _doc(run["runId"])


# ─────────────────────────────────────────── 端点：校验与租户


def test_heartbeat_validation_and_unknown_run(client):
    run = _start(client)
    assert _beat(client, "run_nope", expect=404)["detail"]
    _beat(client, run["runId"], expect=422, json={"phase": "working"})  # 没有字段：多了就拒
    _beat(client, run["runId"], expect=422, json=[1])
    client.post(f"{AGENTS}/{run['runId']}/stop", json={"outcome": "done"})
    assert _beat(client, run["runId"])["reason"] == "closed"  # 正常结束的运行同样回 closed
    assert client.post(f"{AGENTS}/start", json={"agent": "a", "tool": "t", "heartbeat": "yes please"}).status_code == 422
    assert client.post(f"{AGENTS}/{run['runId']}/stop", json={"outcome": "lost"}).status_code == 422  # lost 只由服务端写


def test_heartbeat_is_tenant_scoped(client, shift_clock):
    mine = _start(client, headers=A, heartbeat=True)
    _beat(client, mine["runId"], expect=404, headers=B)  # 别的租户：同形状 404，也不替它续命
    assert "heartbeat" in _doc(mine["runId"])
    theirs = _start(client, headers=B, heartbeat=True)
    shift_clock(minutes=20)
    _beat(client, theirs["runId"], headers=B)
    shift_clock(minutes=40)
    assert client.get(f"{API}/views/current", headers=A).json()["agents"] == []  # A 的失联只收 A 的
    assert [e["data"]["outcome"] for e in _events("ch_aaaa")] == ["lost"]
    assert _events("ch_bbbb") == [] and _lane_for(client, B, theirs["runId"])["lost"] is False


def _lane_for(client, headers, run_id):
    agents = client.get(f"{API}/views/lanes", headers=headers).json()["agents"]
    return next(a for a in agents if a["runId"] == run_id)
