"""检测程序设置（契约 v2.5，contracts/detector.settings.v1）。

要害：设备令牌只能读；schema 严格（未知键——包括想关强制脱敏的键——一律 422）；
存回的是补齐缺省的完整文档；按租户隔离；设置不进导出。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

API = "/api/core"
S = f"{API}/detector/settings"
DEV = "dev_3f9a1c2b7d4e5a60"
A = {"X-Nexus-Tenant": "ch_aaaa"}
B = {"X-Nexus-Tenant": "ch_bbbb"}
BEARER = {"Authorization": "Bearer tok_device"}

DEFAULTS = {
    "schemaVersion": 1,
    "privacy": {
        "paths": "full", "pathWhitelist": [], "titles": "keep", "appOnly": True, "appOnlyApps": None,
        "browser": "domain", "queryStrings": True, "emails": True, "phones": True, "addresses": True,
        "ips": True, "usernames": True, "longNumbers": True,
    },
    "idle": {
        "afkThresholdMinutes": 0, "audibleAsPresent": False, "focusAppsEnabled": False, "focusApps": None,
        "focusMaxMinutes": 60, "idleSuggestions": False,
    },
}


def _put(client, body, headers=None, device=DEV):
    return client.put(S, params={"deviceId": device}, json=body, headers=headers or {})


def test_unset_is_null(client):
    r = client.get(S, params={"deviceId": DEV})
    assert r.status_code == 200
    assert r.json() == {"deviceId": DEV, "settings": None, "updatedAt": None}


def test_put_fills_defaults_and_get_returns_it(client):
    r = _put(client, {"schemaVersion": 1, "privacy": {"titles": "pseudonymize"}, "idle": {"idleSuggestions": True}})
    assert r.status_code == 200, r.text
    want = {**DEFAULTS, "privacy": {**DEFAULTS["privacy"], "titles": "pseudonymize"},
            "idle": {**DEFAULTS["idle"], "idleSuggestions": True}}
    assert r.json()["settings"] == want
    assert r.json()["updatedAt"]
    got = client.get(S, params={"deviceId": DEV}, headers=BEARER).json()
    assert got["settings"] == want


def test_device_token_cannot_write(client):
    assert _put(client, {"schemaVersion": 1}, headers=BEARER).status_code == 403
    assert client.delete(S, params={"deviceId": DEV}, headers=BEARER).status_code == 403
    # 大小写、多余空格都算 Bearer
    assert _put(client, {"schemaVersion": 1}, headers={"Authorization": "  bearer x"}).status_code == 403
    # 403 在任何写入之前：设置没被建出来
    assert client.get(S, params={"deviceId": DEV}).json()["settings"] is None
    # 别的认证方案不是设备令牌（auth.gate：只有 Bearer 算令牌）
    assert _put(client, {"schemaVersion": 1}, headers={"Authorization": "Basic Zm9vOmJhcg=="}).status_code == 200


@pytest.mark.parametrize("body", [
    {},                                                           # 缺 schemaVersion
    {"schemaVersion": 2},
    {"schemaVersion": "1"},
    {"schemaVersion": True},
    {"schemaVersion": 1.0},
    {"schemaVersion": 1, "privacy": {"pathWhitelist": ["(?(1)a|b)"]}},  # 条件组
    {"schemaVersion": 1, "privacy": {"pathWhitelist": ["(?>ab)"]}},     # 原子组
    {"schemaVersion": 1, "privacy": {"pathWhitelist": ["a*+"]}},        # 占有量词
    {"schemaVersion": 1, "extra": 1},
    {"schemaVersion": 1, "privacy": {"secrets": False}},          # 强制脱敏不在 schema 里
    {"schemaVersion": 1, "privacy": {"bankCards": False}},
    {"schemaVersion": 1, "privacy": {"mandatory": False}},
    {"schemaVersion": 1, "privacy": {"paths": "none"}},
    {"schemaVersion": 1, "privacy": {"emails": "false"}},
    {"schemaVersion": 1, "privacy": {"emails": 0}},
    {"schemaVersion": 1, "privacy": {"pathWhitelist": ["("]}},
    {"schemaVersion": 1, "privacy": {"pathWhitelist": ["(?=x)y"]}},     # RE2 不支持
    {"schemaVersion": 1, "privacy": {"pathWhitelist": [r"(a)\1"]}},
    {"schemaVersion": 1, "privacy": {"pathWhitelist": ["x"] * 21}},
    {"schemaVersion": 1, "privacy": {"appOnlyApps": [""]}},
    {"schemaVersion": 1, "privacy": {"appOnlyApps": ["x" * 65]}},
    {"schemaVersion": 1, "idle": {"afkThresholdMinutes": 241}},
    {"schemaVersion": 1, "idle": {"afkThresholdMinutes": 5.0}},
    {"schemaVersion": 1, "idle": {"focusMaxMinutes": 0}},
    {"schemaVersion": 1, "idle": {"focusApps": ["a"] * 201}},
    [],
])
def test_schema_is_strict(client, body):
    r = _put(client, body)
    assert r.status_code == 422, r.text
    assert isinstance(r.json()["detail"], str)


def test_go_only_regex_syntax_accepted(client):
    body = {"schemaVersion": 1, "privacy": {"pathWhitelist": [r"\p{Han}+", r"\++", r"(?<proj>\w+)/\S+", r"x\z"]}}
    assert _put(client, body).status_code == 200


def test_body_limits_and_device_id(client):
    assert client.put(S, params={"deviceId": DEV}, content=b"not json").status_code == 422
    big = b'{"schemaVersion":1,"x":"' + b"a" * 70000 + b'"}'
    assert client.put(S, params={"deviceId": DEV}, content=big).status_code == 413
    assert _put(client, {"schemaVersion": 1}, device="bad:id").status_code == 422
    assert client.get(S, params={"deviceId": "a" * 65}).status_code == 422


def test_delete_returns_to_local(client):
    _put(client, {"schemaVersion": 1})
    assert client.delete(S, params={"deviceId": DEV}).status_code == 204
    assert client.delete(S, params={"deviceId": DEV}).status_code == 204  # 幂等
    assert client.get(S, params={"deviceId": DEV}).json()["settings"] is None


def test_tenant_isolation(client):
    _put(client, {"schemaVersion": 1, "privacy": {"titles": "drop"}}, headers=A)
    assert client.get(S, params={"deviceId": DEV}, headers=B).json()["settings"] is None
    assert client.get(f"{API}/detector/devices", headers=B).json() == {"devices": []}


def test_devices_list(client):
    now = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=10)
    seg = {"startAt": now.isoformat(), "endAt": (now + timedelta(minutes=4)).isoformat(), "durationSeconds": 240,
           "app": "code", "title": "x", "idle": True,
           "suggestion": {"taskId": None, "confidence": 0.3, "reason": "无操作，可能在阅读", "classifier": "rules"}}
    r = client.post(f"{API}/activity/suggestions", json={"deviceId": "dev_upload", "segments": [seg]})
    assert r.json()["accepted"] == 1
    client.get(S, params={"deviceId": "dev_fetch"}, headers=BEARER)
    client.get(S, params={"deviceId": "dev_ui"})  # 人读不算设备活动
    _put(client, {"schemaVersion": 1}, device="dev_set")
    devs = {d["deviceId"]: d for d in client.get(f"{API}/detector/devices").json()["devices"]}
    assert set(devs) == {"dev_upload", "dev_fetch", "dev_set"}
    assert devs["dev_upload"]["lastUploadAt"] and not devs["dev_upload"]["hasSettings"]
    assert devs["dev_fetch"]["lastFetchAt"] and devs["dev_fetch"]["lastUploadAt"] is None
    assert devs["dev_set"]["hasSettings"] and devs["dev_set"]["settingsUpdatedAt"]
    # 活动建议列表带出 idle（v2.5）
    items = client.get(f"{API}/activity/suggestions").json()["items"]
    assert items[0]["idle"] is True


def test_settings_not_exported(client):
    _put(client, {"schemaVersion": 1})
    assert "detector" not in client.get(f"{API}/export").text
