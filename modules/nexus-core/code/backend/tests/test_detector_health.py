"""检测程序健康状态与「重启 ActivityWatch」（契约 v2.27）。

要害：health 只有枚举、没有任何自由文本；restart 只收人（设备令牌 403）；nonce 一次一个、
旧 nonce 的结果不收；按租户隔离；devices 旧字段不变。
"""

from __future__ import annotations

API = "/api/core/detector"
DEV = "dev_3f9a1c2b7d4e5a60"
A = {"X-Nexus-Tenant": "ch_aaaa"}
B = {"X-Nexus-Tenant": "ch_bbbb"}
BEARER = {"Authorization": "Bearer tok_device"}


def _health(client, body, headers=None, device=DEV):
    return client.put(f"{API}/health", params={"deviceId": device}, json=body, headers=headers or {})


def _restart(client, headers=None, device=DEV):
    return client.post(f"{API}/restart", params={"deviceId": device}, headers=headers or {})


def _dev(client, headers=None, device=DEV):
    return {d["deviceId"]: d for d in client.get(f"{API}/devices", headers=headers or {}).json()["devices"]}.get(device)


def test_health_stored_with_server_clock_and_listed(client):
    r = _health(client, {"awState": "unreachable"}, BEARER)
    assert r.status_code == 200 and r.json() == {"restartNonce": None}
    d = _dev(client)
    assert d["health"]["awState"] == "unreachable" and d["health"]["at"]
    assert set(d["health"]) == {"awState", "at", "autoStart", "autoReason"} and d["health"]["autoStart"] is None
    assert d["restart"] is None
    # 旧字段不变
    assert {"deviceId", "lastUploadAt", "lastFetchAt", "hasSettings", "settingsUpdatedAt"} <= set(d)
    # cookie（无 Bearer）同样能报
    assert _health(client, {"awState": "ok"}).status_code == 200
    assert _dev(client)["health"]["awState"] == "ok"


def test_devices_without_health_have_nulls(client):
    client.get(f"{API}/settings", params={"deviceId": DEV}, headers=BEARER)
    d = _dev(client)
    assert d["health"] is None and d["restart"] is None


def test_health_schema_strict(client):
    for body in (
        {"awState": "ok", "extra": 1},                                  # 未知键
        {"awState": "ok", "title": "secret window"},                    # 没有自由文本字段
        {"awState": "boom"},                                            # 枚举外
        {},                                                             # 缺 awState
        {"awState": "ok", "restart": {"nonce": "n", "result": "meh"}},  # 结果枚举外
        {"awState": "ok", "restart": {"nonce": "n"}},                   # 缺 result
        {"awState": "ok", "restart": {"nonce": "n", "result": "failed", "error": "/home/x"}},
        {"awState": "ok", "restart": {"nonce": "x" * 65, "result": "failed"}},
        {"awState": "ok", "restart": {"nonce": "", "result": "failed"}},
        {"awState": 1},
        {"awState": "ok", "autoStart": "maybe"},
        {"awState": "ok", "autoReason": "/home/me/secret"},                       # 原因只收枚举
        {"awState": "ok", "restart": {"nonce": "n", "result": "failed", "reason": "boom"}},
        {"awState": "ok", "restart": {"nonce": "n", "result": "failed", "reason": "x" * 500}},
        {"awState": "ok", "autoStart": "started; rm -rf"},
    ):
        assert _health(client, body, BEARER).status_code == 422, body
    assert _health(client, {"awState": "ok", "pad": "x" * 2000}, BEARER).status_code == 413
    r = client.put(f"{API}/health", params={"deviceId": DEV}, content=b"not json")
    assert r.status_code == 422
    assert _health(client, {"awState": "ok"}, device="bad id!").status_code == 422
    assert _dev(client) is None  # 一次都没存


def test_restart_rejects_bearer_and_unknown_device(client):
    assert _restart(client, BEARER).status_code == 403
    assert _restart(client).status_code == 404  # 没有这台设备
    _health(client, {"awState": "ok"}, BEARER)
    assert _restart(client, BEARER).status_code == 403
    assert _dev(client)["restart"] is None  # 403 什么都没写
    assert _restart(client, device="bad id!").status_code == 422


def test_nonce_lifecycle(client):
    _health(client, {"awState": "unreachable"}, BEARER)
    n1 = _restart(client).json()["nonce"]
    assert n1
    d = _dev(client)["restart"]
    assert d["nonce"] == n1 and d["requestedAt"] and d["result"] is None and d["resultAt"] is None
    # 待处理 nonce 随每次 health 的响应回给检测程序
    assert _health(client, {"awState": "unreachable"}, BEARER).json() == {"restartNonce": n1}
    # 新请求顶掉旧的
    n2 = _restart(client).json()["nonce"]
    assert n2 != n1
    # 旧 nonce 的结果忽略
    r = _health(client, {"awState": "unreachable", "restart": {"nonce": n1, "result": "started"}}, BEARER)
    assert r.json() == {"restartNonce": n2}
    assert _dev(client)["restart"]["result"] is None
    # 对的 nonce：记下结果，pending 变 null
    r = _health(client, {"awState": "ok", "restart": {"nonce": n2, "result": "started"}}, BEARER)
    assert r.json() == {"restartNonce": None}
    d = _dev(client)["restart"]
    assert d["nonce"] == n2 and d["result"] == "started" and d["resultAt"]
    # 结果只记一次：重复上报不覆盖
    _health(client, {"awState": "ok", "restart": {"nonce": n2, "result": "failed"}}, BEARER)
    assert _dev(client)["restart"]["result"] == "started"
    # 再点一次又是新一轮
    n3 = _restart(client).json()["nonce"]
    assert _dev(client)["restart"] == {**_dev(client)["restart"], "nonce": n3, "result": None, "resultAt": None}


def test_tenant_isolation(client):
    _health(client, {"awState": "ok"}, {**BEARER, **A})
    n = _restart(client, A).json()["nonce"]
    assert _dev(client, B) is None
    assert _restart(client, B).status_code == 404
    # B 用 A 的 nonce 报结果：落空，A 的不受影响
    _health(client, {"awState": "ok", "restart": {"nonce": n, "result": "started"}}, {**BEARER, **B})
    assert _dev(client, A)["restart"]["result"] is None
    assert _dev(client, B)["restart"] is None and _dev(client, B)["health"]["awState"] == "ok"


def test_auto_start_enum_stored_and_replaced(client):
    for v in ("off", "idle", "started", "gave_up", "unsupported"):
        assert _health(client, {"awState": "unreachable", "autoStart": v}, BEARER).status_code == 200
        assert _dev(client)["health"]["autoStart"] == v
    _health(client, {"awState": "ok"}, BEARER)  # 老检测程序不带：回到 null
    assert _dev(client)["health"]["autoStart"] is None


def test_reasons_stored_and_listed(client):
    _health(client, {"awState": "unreachable"}, BEARER)
    n = _restart(client).json()["nonce"]
    assert _dev(client)["restart"]["reason"] is None
    for reason in ("not_found", "spawn_error", "exited_early", "not_up", "no_display", "state_unwritable"):
        n = _restart(client).json()["nonce"]
        r = _health(client, {"awState": "unreachable", "autoStart": "gave_up", "autoReason": reason,
                             "restart": {"nonce": n, "result": "failed", "reason": reason}}, BEARER)
        assert r.status_code == 200, (reason, r.text)
        d = _dev(client)
        assert d["restart"]["result"] == "failed" and d["restart"]["reason"] == reason
        assert d["health"]["autoReason"] == reason
    # 不带 reason（老检测程序 / 成功）：null；autoReason 随下一次报告清掉
    n = _restart(client).json()["nonce"]
    _health(client, {"awState": "ok", "restart": {"nonce": n, "result": "started"}}, BEARER)
    d = _dev(client)
    assert d["restart"]["reason"] is None and d["health"]["autoReason"] is None


def test_stale_restart_request_expires(client, monkeypatch):
    from datetime import datetime, timedelta, timezone

    from app.modules.detector import service

    _health(client, {"awState": "unreachable"}, BEARER)
    n = _restart(client).json()["nonce"]
    t0 = datetime.now(timezone.utc)
    # 4 分钟：还在
    monkeypatch.setattr(service, "_now", lambda: t0 + timedelta(minutes=4))
    assert _health(client, {"awState": "unreachable"}, BEARER).json() == {"restartNonce": n}
    assert _dev(client)["restart"]["expired"] is False
    # 6 分钟：不再给检测程序；结果也不收；devices 里 result 仍是 null，expired = true
    monkeypatch.setattr(service, "_now", lambda: t0 + timedelta(minutes=6))
    assert _health(client, {"awState": "unreachable"}, BEARER).json() == {"restartNonce": None}
    r = _health(client, {"awState": "ok", "restart": {"nonce": n, "result": "started"}}, BEARER)
    assert r.json() == {"restartNonce": None}
    d = _dev(client)["restart"]
    assert d["nonce"] == n and d["result"] is None and d["resultAt"] is None and d["expired"] is True
    # 重新点一次：新的请求是新鲜的
    n2 = _restart(client).json()["nonce"]
    assert _health(client, {"awState": "unreachable"}, BEARER).json() == {"restartNonce": n2}
    _health(client, {"awState": "ok", "restart": {"nonce": n2, "result": "started"}}, BEARER)
    assert _dev(client)["restart"]["result"] == "started" and _dev(client)["restart"]["expired"] is False


def test_devices_carry_server_now(client):
    from datetime import datetime, timezone

    body = client.get(f"{API}/devices").json()
    assert abs((datetime.fromisoformat(body["now"]) - datetime.now(timezone.utc)).total_seconds()) < 30
    assert body["devices"] == []
