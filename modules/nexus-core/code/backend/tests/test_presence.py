"""在场心跳 + attend 连线（契约 v2.4「在场心跳」「连线」两节）。

活状态、只留 2 小时、写入时清理、每设备 500 段、每租户 20 台设备、租户隔离、不进导出；
心跳标题命中在跑运行的 match 记 attend（45 秒内延长）。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

API = "/api/core"
URL = f"{API}/activity/presence"
A = {"X-Nexus-Tenant": "ch_aaaa"}
B = {"X-Nexus-Tenant": "ch_bbbb"}
T0 = datetime(2026, 9, 30, 2, 0, tzinfo=timezone.utc)


def _db():
    from app.repo import get_db  # noqa: PLC0415

    return get_db()


@pytest.fixture()
def clock(monkeypatch):
    """把心跳的服务端时钟钉在 T0 + 偏移（秒）。"""
    from app.modules.activity import presence  # noqa: PLC0415

    state = {"t": T0}
    monkeypatch.setattr(presence, "_now", lambda: state["t"])

    def at(seconds):
        state["t"] = T0 + timedelta(seconds=seconds)

    return at


def _beat(client, headers=None, device="dev_1", app="code", title="plot.gd — garden", afk=False):
    resp = client.post(URL, json={"deviceId": device, "app": app, "title": title, "afk": afk},
                       headers=headers or {})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"ok": True}


def _spans(device="dev_1", user="u_local"):
    doc = _db()["activity_presence"].find_one({"user": user, "deviceId": device})
    return [(s["from"], s["to"], s["title"], s["afk"]) for s in doc["spans"]] if doc else None


def _t(seconds):
    return T0 + timedelta(seconds=seconds)


def test_merge_within_45s_and_split_on_change_or_gap(client, clock):
    for sec in (0, 15, 60):  # 15→60 间隔 45s：仍合并
        clock(sec)
        _beat(client)
    clock(75)
    _beat(client, title="other")
    clock(200)  # 同内容但间隔 125s：新段
    _beat(client, title="other")
    clock(215)
    _beat(client, title="other", afk=True)
    assert _spans() == [
        (_t(0), _t(60), "plot.gd — garden", False), (_t(75), _t(75), "other", False),
        (_t(200), _t(200), "other", False), (_t(215), _t(215), "other", True)]


def test_two_hour_window_drops_and_clips(client, clock):
    clock(0)
    _beat(client, title="old")
    for sec in range(3000, 10241, 40):  # 一段从 +3000s 连续到 +10240s（间隔 40s，一直合并）
        clock(sec)
        _beat(client, title="long")
    spans = _spans()
    cutoff = _t(10240) - timedelta(hours=2)
    assert [s[2] for s in spans] == ["long"]  # "old" 段 to 早于截止线：删
    assert spans[0][0] == cutoff  # 跨截止线的段：from 裁到截止线


def test_span_cap(client, clock, monkeypatch):
    from app.modules.activity import presence  # noqa: PLC0415

    monkeypatch.setattr(presence, "MAX_SPANS", 3)
    for i in range(5):
        clock(i * 10)
        _beat(client, title=f"t{i}")
    assert [s[2] for s in _spans()] == ["t2", "t3", "t4"]


def test_device_cap_evicts_least_recent(client, clock, monkeypatch):
    from app.modules.activity import presence  # noqa: PLC0415

    monkeypatch.setattr(presence, "MAX_DEVICES", 2)
    for i, dev in enumerate(("dev_a", "dev_b", "dev_a", "dev_c")):
        clock(i)
        _beat(client, device=dev)
    assert sorted(d["deviceId"] for d in _db()["activity_presence"].find({})) == ["dev_a", "dev_c"]


def test_stale_device_deleted_on_next_write_of_same_tenant_only(client, clock):
    clock(0)
    _beat(client, headers=A, device="dev_old")
    _beat(client, headers=B, device="dev_b")
    clock(7201)
    _beat(client, headers=B, device="dev_b")  # 别的租户的写入不清 A
    assert _spans("dev_old", "ch_aaaa") is not None
    _beat(client, headers=A, device="dev_new")
    assert _spans("dev_old", "ch_aaaa") is None


def test_truncation(client, clock):
    clock(0)
    _beat(client, app="a" * 200, title="t" * 600)
    doc = _db()["activity_presence"].find_one({})
    assert (len(doc["app"]), len(doc["title"])) == (128, 512)


@pytest.mark.parametrize("body", [
    {"deviceId": "dev:1", "app": "", "title": "", "afk": False},
    {"deviceId": "d" * 65, "app": "", "title": "", "afk": False},
    {"deviceId": "dev_1", "app": "", "title": "", "afk": "true"},
    {"deviceId": "dev_1", "app": 1, "title": "", "afk": False},
    {"deviceId": "dev_1", "app": "", "title": None, "afk": False},
    [],
])
def test_validation(client, body):
    assert client.post(URL, json=body).status_code == 422


def test_presence_is_not_exported(client, clock):
    clock(0)
    _beat(client)
    export = client.get(f"{API}/export").json()
    assert "activity_presence" not in str(sorted(export)) and "presence" not in export
    assert "proj_lanes" not in export["projections"]


# ─────────────────────────────────────────── attend


def _start(client, headers=None, **body):
    resp = client.post(f"{API}/agents/start", json={"agent": "c", "tool": "c", **body}, headers=headers or {})
    assert resp.status_code == 201, resp.text
    return resp.json()["runId"]


def _inter(run_id):
    return _db()["agent_runs"].find_one({"runId": run_id}).get("interactions") or []


def test_attend_created_extended_and_split(client, clock):
    hit = _start(client, match="Garden")
    both = _start(client, match="plot.gd")
    none = _start(client)  # 没给 match：永远没有 attend
    miss = _start(client, match="kitchen")
    for sec in (0, 30):
        clock(sec)
        _beat(client, title="plot.gd — GARDEN — VS Code")  # 不分大小写
    clock(40)
    _beat(client, title="plot.gd — garden", afk=True)  # 离开：不算
    clock(100)  # 距上次 until 70s：新开一条
    _beat(client)
    iso = lambda s: _t(s).isoformat()  # noqa: E731
    expected = [{"kind": "attend", "at": iso(0), "until": iso(30)},
                {"kind": "attend", "at": iso(100), "until": iso(100)}]
    assert _inter(hit) == expected and _inter(both) == expected
    assert _inter(none) == [] and _inter(miss) == []


def test_attend_tenant_isolated_and_capped(client, clock, monkeypatch):
    from app.modules.timer import agent_phases  # noqa: PLC0415

    monkeypatch.setattr(agent_phases, "MAX_INTERACTIONS", 1)
    a_run = _start(client, headers=A, match="garden")
    clock(0)
    _beat(client, headers=B)
    assert _inter(a_run) == []
    _beat(client, headers=A)
    clock(500)
    _beat(client, headers=A)  # 超了不再记，不报错
    assert len(_inter(a_run)) == 1
