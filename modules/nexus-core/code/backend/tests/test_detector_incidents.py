"""检测程序事故时间线与启动日志（契约 v2.28）。

要害：事件只在**变化**时记、只有枚举；静默过久记 detector_offline、此刻静默读时算；每台设备 ≤ 200 条；
启动日志严格校验（超限 422 不截断）、关开关即删、只留最新一份；按租户隔离；老检测程序（不带新字段）照常。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.modules.detector import service

API = "/api/core/detector"
DEV = "dev_3f9a1c2b7d4e5a60"
A = {"X-Nexus-Tenant": "ch_aaaa"}
B = {"X-Nexus-Tenant": "ch_bbbb"}
BEARER = {"Authorization": "Bearer tok_device"}


@pytest.fixture
def clock(monkeypatch):
    t = {"now": datetime.now(timezone.utc)}
    monkeypatch.setattr(service, "_now", lambda: t["now"])
    return t


def _health(client, body, headers=None, device=DEV):
    return client.put(f"{API}/health", params={"deviceId": device}, json=body, headers={**BEARER, **(headers or {})})


def _inc(client, headers=None, device=DEV, **params):
    return client.get(f"{API}/incidents", params={"deviceId": device, **params}, headers=headers or {})


def _kinds(client, **kw):
    return [e["kind"] for e in _inc(client, **kw).json()["events"]]


def test_events_only_on_transitions(client, clock):
    _health(client, {"awState": "ok"})
    for _ in range(3):  # 状态没变：不记
        clock["now"] += timedelta(seconds=15)
        _health(client, {"awState": "ok"})
    assert _kinds(client) == []
    for st, kind in (("unreachable", "aw_unreachable"), ("no_window", "aw_no_window"), ("paused", "aw_paused"),
                     ("disabled", "aw_disabled"), ("ok", "aw_recovered")):
        clock["now"] += timedelta(seconds=15)
        _health(client, {"awState": st})
        clock["now"] += timedelta(seconds=15)
        _health(client, {"awState": st})  # 重复不记
        assert _kinds(client)[0] == kind
    assert len(_kinds(client)) == 5


def test_first_report_not_ok_is_recorded(client, clock):
    _health(client, {"awState": "unreachable"})
    assert _kinds(client) == ["aw_unreachable"]


def test_detector_gap_recorded_with_duration_and_offline_computed(client, clock):
    t0 = clock["now"]
    _health(client, {"awState": "ok"})
    clock["now"] = t0 + timedelta(seconds=60)  # 60 秒 < 90：不算
    _health(client, {"awState": "ok"})
    assert _kinds(client) == []
    clock["now"] = t0 + timedelta(seconds=60 + 80 * 60)
    # 此刻静默：读时算离线（没有新报告也一样）
    r = _inc(client).json()
    assert r["offline"] is True and r["lastReportAt"]
    _health(client, {"awState": "unreachable"})
    r = _inc(client).json()
    assert r["offline"] is False
    gap, aw = r["events"][1], r["events"][0]
    assert (gap["kind"], gap["durationSeconds"]) == ("detector_offline", 80 * 60)
    assert gap["at"] != gap["endAt"] and aw["kind"] == "aw_unreachable"


def test_auto_start_events(client, clock):
    _health(client, {"awState": "unreachable", "autoStart": "idle"})
    clock["now"] += timedelta(seconds=15)
    _health(client, {"awState": "unreachable", "autoStart": "idle", "autoReason": "not_up"})
    clock["now"] += timedelta(seconds=15)
    _health(client, {"awState": "unreachable", "autoStart": "idle", "autoReason": "not_up"})  # 没变
    clock["now"] += timedelta(seconds=15)
    _health(client, {"awState": "unreachable", "autoStart": "gave_up", "autoReason": "daily_cap"})
    clock["now"] += timedelta(seconds=15)
    _health(client, {"awState": "ok", "autoStart": "started"})
    ev = _inc(client).json()["events"]
    assert [(e["kind"], e["reason"]) for e in ev] == [
        ("auto_started", None), ("aw_recovered", None), ("auto_gave_up", "daily_cap"), ("auto_failed", "not_up"),
        ("aw_unreachable", None)]


def test_restart_events(client, clock):
    _health(client, {"awState": "unreachable"})
    n = client.post(f"{API}/restart", params={"deviceId": DEV}).json()["nonce"]
    clock["now"] += timedelta(seconds=15)
    _health(client, {"awState": "unreachable", "restart": {"nonce": n, "result": "failed", "reason": "not_up"}})
    _health(client, {"awState": "unreachable", "restart": {"nonce": n, "result": "started"}})  # 重复结果不记
    ev = _inc(client).json()["events"]
    assert [(e["kind"], e["result"], e["reason"]) for e in ev[:2]] == [("restart_result", "failed", "not_up"),
                                                                          ("restart_requested", None, None)]
    assert [e["kind"] for e in ev].count("restart_result") == 1
    # 没人理的请求：过期只记一次
    n2 = client.post(f"{API}/restart", params={"deviceId": DEV}).json()["nonce"]
    clock["now"] += timedelta(minutes=6)
    _health(client, {"awState": "unreachable"})
    _health(client, {"awState": "unreachable"})
    kinds = _kinds(client)
    assert kinds.count("restart_expired") == 1 and n2


def test_bounded_to_200_events_and_limit(client, clock):
    for i in range(230):  # 每次都变 → 230 条，只留最新 200
        clock["now"] += timedelta(seconds=15)
        _health(client, {"awState": "unreachable" if i % 2 == 0 else "ok"})
    assert len(_kinds(client, limit=200)) == 200
    from app.repo import get_db
    assert len(get_db()["detector_incidents"].find_one({})["events"]) == 200   # 存储本身有界，不只是读的时候截
    assert len(_inc(client).json()["events"]) == 50
    assert len(_kinds(client, limit=7)) == 7
    for bad in (0, 201, "x"):
        assert _inc(client, limit=bad).status_code == 422
    # 最老的被丢：留下的是最新的 200 条，仍是新的在前
    ats = [e["at"] for e in _inc(client, limit=200).json()["events"]]
    assert ats == sorted(ats, reverse=True)


def test_events_older_than_30_days_hidden(client, clock):
    _health(client, {"awState": "unreachable"})
    clock["now"] += timedelta(days=31)
    assert _kinds(client) == []


def test_incidents_auth_404_and_isolation(client, clock):
    assert _inc(client).status_code == 404
    assert _inc(client, device="bad id!").status_code == 422
    _health(client, {"awState": "unreachable"}, A)
    assert _inc(client, A).status_code == 200 and _inc(client, B).status_code == 404
    # 同 GET /devices：设备令牌与 cookie 都能读
    assert client.get(f"{API}/incidents", params={"deviceId": DEV}, headers={**A, **BEARER}).status_code == 200


def test_no_health_yet_reports_unknown(client, clock):
    client.get(f"{API}/settings", params={"deviceId": DEV}, headers=BEARER)  # 只拉过设置
    r = _inc(client).json()
    assert r["offline"] is None and r["health"] is None and r["logUpload"] is False and r["events"] == []


# ── 启动日志 ──

LOG = "starting aw-qt\nwarning: no display"


def test_start_log_stored_latest_only_and_deleted_on_switch_off(client, clock):
    _health(client, {"awState": "ok", "logUpload": True, "startLog": LOG})
    r = _inc(client).json()
    assert r["logUpload"] is True and r["startLog"]["text"] == LOG and r["startLog"]["at"]
    assert client.get(f"{API}/devices").json()["devices"][0]["health"]["logUpload"] is True
    # 开着但这次没带日志（内容没变）：保留
    _health(client, {"awState": "ok", "logUpload": True})
    assert _inc(client).json()["startLog"]["text"] == LOG
    # 新的替换旧的
    _health(client, {"awState": "ok", "logUpload": True, "startLog": "second"})
    assert _inc(client).json()["startLog"]["text"] == "second"
    # 关开关：删
    _health(client, {"awState": "ok", "logUpload": False})
    r = _inc(client).json()
    assert r["logUpload"] is False and r["startLog"] is None
    from app.repo import get_db
    assert "startLog" not in get_db()["detector_settings"].find_one({"deviceId": DEV})
    # 重新打开但没带日志：什么都没有
    _health(client, {"awState": "ok", "logUpload": True})
    assert _inc(client).json()["startLog"] is None


def test_old_detector_without_fields_clears_log(client, clock):
    _health(client, {"awState": "ok", "logUpload": True, "startLog": LOG})
    _health(client, {"awState": "ok"})  # 老检测程序：不报 = 按关
    r = _inc(client).json()
    assert r["logUpload"] is False and r["startLog"] is None


def test_start_log_validation_is_strict_never_truncates(client, clock):
    ok = {"awState": "ok", "logUpload": True}
    bad = [
        {"startLog": "x\n" * 41},                    # > 40 行
        {"startLog": "\n".join(["x"] * 41)},
        {"startLog": "x" * 301},                     # 行太长
        {"startLog": ("中" * 100 + "\n") * 15},      # > 4096 字节
        {"startLog": "a\x00b"}, {"startLog": "a\x1b[31m"}, {"startLog": "a\rb"}, {"startLog": "a‮b"},
        {"startLog": ""}, {"startLog": "   "},
        {"startLog": 5}, {"startLog": ["a"]},
        {"logUpload": "yes"}, {"logUpload": 1},
    ]
    for extra in bad:
        r = _health(client, {**ok, **extra})
        assert r.status_code == 422, extra
        assert "x" * 50 not in r.text
    # 没开开关却带日志
    assert _health(client, {"awState": "ok", "logUpload": False, "startLog": LOG}).status_code == 422
    assert _health(client, {"awState": "ok", "startLog": LOG}).status_code == 422
    # 恰好 40 行 / 4096 字节 / 300 字符的边界收
    assert _health(client, {**ok, "startLog": "\n".join(["x" * 100] * 40)}).status_code == 200
    assert _health(client, {**ok, "startLog": "x" * 300}).status_code == 200
    assert _health(client, {**ok, "startLog": "\n".join(["中" * 45] * 30)}).status_code == 200  # 4050 字节
    assert _inc(client).json()["startLog"]["text"] == "\n".join(["中" * 45] * 30)
    # 带日志的报告体可以超过 1024，但不带日志的仍然 413
    assert _health(client, {"awState": "ok", "pad": "x" * 2000}).status_code == 413
    assert _health(client, {**ok, "startLog": "x" * 300, "pad": "y" * 40000}).status_code == 413
    r = _health(client, {**ok, "startLog": "x" * 300, "pad": "y" * 40000})
    assert "1024" not in r.json()["detail"] and "32" in r.json()["detail"]
    # 放宽只给真的带了 startLog 的：null / 只在别处出现这个词的，超过 1 KiB 仍是 413
    for raw in (" " * 2000 + '{"awState":"ok","startLog":null}', '{"awState":"ok","logUpload":false,"x":"startLog","p":"' + "y" * 2000 + '"}'):
        r = client.put(f"{API}/health", params={"deviceId": DEV}, content=raw, headers=BEARER)
        assert r.status_code == 413 and "1024" in r.json()["detail"], raw[:40]


def test_start_log_never_logged(client, clock, caplog):
    import logging
    caplog.set_level(logging.INFO)
    _health(client, {"awState": "ok", "logUpload": True, "startLog": "needle-in-log"})
    _health(client, {"awState": "ok", "logUpload": True, "startLog": "needle-in-log\n" + "x" * 500})  # 422
    assert "needle-in-log" not in caplog.text


def test_start_log_tenant_isolation(client, clock):
    _health(client, {"awState": "ok", "logUpload": True, "startLog": LOG}, A)
    _health(client, {"awState": "ok"}, B)
    assert _inc(client, B).json()["startLog"] is None and _inc(client, A).json()["startLog"]["text"] == LOG
