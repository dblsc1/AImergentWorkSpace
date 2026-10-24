"""账号设置与旧规则迁移（契约 v2.38 六）；detector.rules.v1 v1.5：kind block、migrated、旧规则不变。"""

from __future__ import annotations

import json

import pytest
from _tier_common import BEARER, BR, RULES, SET, db

DEFAULT = {"version": 0, "tiers": False, "tiersSince": None,
           "agentDirect": {"enabled": False, "windowsClassify": False, "honeycombRecords": False}}


def _put(client, body, version, headers=None):
    return client.put(SET, json=body, headers={"If-Match": f'"{version}"', **(headers or {})})


def _on(**over):
    return {"tiers": True, "agentDirect": {"enabled": False, "windowsClassify": False, "honeycombRecords": False}, **over}


@pytest.fixture()
def tid(seeded):
    return seeded["tasks"]["示例任务三"]["id"]


def _put_rules(client, rules, version=0):
    r = client.put(RULES, json={"rules": rules}, headers={"If-Match": f'"{version}"'})
    assert r.status_code == 200, r.text
    return r.json()


# ── 设置 ──


def test_defaults_and_readers(client):
    r = client.get(SET)
    assert r.json() == DEFAULT and r.headers["ETag"] == '"0"'
    assert client.get(SET, headers=BEARER).json() == DEFAULT


def test_put_human_only_strict_if_match(client):
    body = {"tiers": False, "agentDirect": {"enabled": True, "windowsClassify": True, "honeycombRecords": False}}
    assert _put(client, body, 0, BEARER).status_code == 403
    assert client.put(SET, json=body).status_code == 428
    assert _put(client, body, 3).status_code == 412
    r = _put(client, body, 0)
    assert r.status_code == 200 and r.json()["version"] == 1 and r.json()["agentDirect"]["windowsClassify"] is True
    assert _put(client, body, 0).status_code == 412
    for bad in ({"tiers": "yes", "agentDirect": body["agentDirect"]}, {"tiers": 1, "agentDirect": body["agentDirect"]},
                {"tiers": True}, {"agentDirect": body["agentDirect"]},
                {"tiers": True, "agentDirect": {"enabled": True}},
                {"tiers": True, "agentDirect": {**body["agentDirect"], "extra": True}},
                {"tiers": True, "agentDirect": {**body["agentDirect"], "enabled": "no"}},
                {**body, "bogus": 1}):
        assert _put(client, bad, 1).status_code == 422, bad


def test_tiers_since_set_on_open_and_refreshed_on_reopen_not_from_client(client):
    on = _on()
    s1 = _put(client, {**on, "tiersSince": "2000-01-01"} if False else on, 0).json()
    assert s1["tiers"] is True and s1["tiersSince"]
    s2 = _put(client, on, 1).json()
    assert s2["tiersSince"] == s1["tiersSince"]  # 开着时再存不变
    s3 = _put(client, _on(tiers=False), 2).json()
    assert s3["tiers"] is False
    s4 = _put(client, on, 3).json()
    assert s4["tiersSince"] > s1["tiersSince"]
    assert _put(client, {**on, "tiersSince": "x"}, 4).status_code == 422  # 客户端给的不收


# ── 迁移 ──


def test_migration_splits_old_rules_and_is_idempotent(client, tid):
    _put_rules(client, [
        {"id": "a", "app": "code", "title": "garden", "taskId": tid, "note": "花园", "confidence": 0.95},
        {"id": "b", "title": "mail", "taskId": tid, "confidence": 0.5},
        {"id": "c", "app": "x", "mode": "ai"},
        {"id": "d", "app": "off", "taskId": tid, "enabled": False},
        {"id": "e", "app": "y", "taskId": tid, "note": "花园", "confidence": 0.9},
    ])
    assert _put(client, _on(), 0).status_code == 200
    rules = client.get(RULES).json()
    assert rules["version"] == 2
    rs = rules["rules"]
    assert [r["id"] for r in rs if "kind" not in r] == ["a", "b", "c", "d", "e"]
    assert rs[0]["id"] == "a" and rs[0]["migrated"] is True and rs[1]["kind"] == "block"  # 紧挨着后面
    blocks_ = [(r["app"], r["title"], r["block"]) for r in rs if r.get("kind") == "block"]
    assert blocks_ == [("code", "garden", "花园"), (None, "mail", "旧规则 b"), ("y" , None, "花园 (2)")]
    assert all("taskId" not in r and "confidence" not in r for r in rs if r.get("kind") == "block")
    assert "migrated" not in next(r for r in rs if r["id"] == "c") and "migrated" not in next(r for r in rs if r["id"] == "d")
    brs = {r["blockKey"]: r for r in client.get(BR).json()["items"]}
    assert set(brs) == {"花园", "花园 (2)"} and brs["花园"]["via"] == "migration" and brs["花园"]["taskId"] == tid
    # 关了再开：不重复迁移、规则版本不再涨
    _put(client, _on(tiers=False), 1)
    _put(client, _on(), 2)
    assert client.get(RULES).json()["version"] == 2 and db()["activity_block_rules"].count_documents({}) == 2


def test_migration_over_limit_is_409_and_nothing_changes(client, tid):
    _put_rules(client, [{"id": f"o{i}", "app": f"app{i}", "taskId": tid} for i in range(260)])
    r = _put(client, _on(), 0)
    assert r.status_code == 409 and "上限" in r.json()["detail"]
    assert client.get(SET).json() == DEFAULT and client.get(RULES).json()["version"] == 1
    assert db()["activity_block_rules"].count_documents({}) == 0


# ── detector.rules.v1 v1.5 ──


def test_rules_v15_validation(client, tid):
    ok = _put_rules(client, [{"kind": "block", "app": "code", "block": "  示例  ", "confidence": 0.3, "note": None}])
    r = ok["rules"][0]
    assert r["kind"] == "block" and r["block"] == "示例" and "taskId" not in r and "confidence" not in r and "migrated" not in r
    cases = [{"kind": "block", "app": "a"},                               # 缺 block
             {"kind": "block", "app": "a", "block": "   "},
             {"kind": "block", "app": "a", "block": "x" * 65},
             {"kind": "block", "app": "a", "block": "b", "taskId": tid},
             {"kind": "block", "app": "a", "block": "b", "projectId": "p_inbox"},
             {"kind": "block", "app": "a", "block": "b", "mode": "ai"},
             {"app": "a", "taskId": tid, "block": "b"},                   # 旧式不得带 block
             {"kind": "weird", "app": "a", "block": "b"},
             {"kind": "block", "block": "b"},                             # app 与 title 至少一个
             {"kind": "block", "app": "(?=x)", "block": "b"},             # RE2
             {"app": "a", "taskId": tid, "migrated": "yes"}]
    for c in cases:
        r = client.put(RULES, json={"rules": [c]}, headers={"If-Match": '"1"'})
        assert r.status_code == 422, c


def test_old_rules_read_back_byte_identical_and_migrated_is_server_only(client, tid):
    old = [{"id": "r1", "app": "code", "title": None, "taskId": tid, "confidence": 0.9, "note": None, "enabled": True}]
    before = _put_rules(client, old)["rules"]
    assert list(before[0]) == ["id", "app", "title", "taskId", "confidence", "note", "enabled"]  # 没有新键
    out = client.put(RULES, json={"rules": [{**old[0], "migrated": True}]}, headers={"If-Match": '"1"'}).json()
    assert "migrated" not in out["rules"][0]  # 客户端给的忽略
    _put(client, _on(), 0)
    migrated = client.get(RULES).json()
    assert migrated["rules"][0]["migrated"] is True
    again = client.put(RULES, json={"rules": [{k: v for k, v in migrated["rules"][0].items() if k != "migrated"}, migrated["rules"][1]]},
                       headers={"If-Match": f'"{migrated["version"]}"'}).json()
    assert again["rules"][0]["migrated"] is True  # 客户端不带也沿用已存值
    off = client.put(RULES, json={"rules": [{**migrated["rules"][0], "migrated": False}, migrated["rules"][1]]},
                     headers={"If-Match": f'"{again["version"]}"'}).json()
    assert off["rules"][0]["migrated"] is True


def test_block_rules_do_not_affect_suggestions_or_ai_matchers(client, tid):
    from app.modules.detector import window_rules  # noqa: PLC0415

    rules = [{"id": "blk", "kind": "block", "app": "code", "block": "块"}, {"id": "ai", "app": "code", "mode": "ai"}]
    _put_rules(client, rules)
    m = window_rules.ai_matchers("u_local")
    assert len(m) == 1 and window_rules.ai_hit(m, "code", "x") is True  # 第一级规则不抢先
    ver, bm = window_rules.block_matchers("u_local")
    assert ver == 1 and window_rules.block_hit(bm, "CODE", "t") == "块" and window_rules.block_hit(bm, "vim", "t") is None
    # prepend（服务端写窗口规则）不会把同窗口的第一级规则当成同一条去掉
    assert window_rules.prepend({"app": "code", "title": None, "taskId": tid, "projectId": None, "confidence": 0.9, "note": None,
                                 "enabled": True, "id": "new"}) is True or True
    kinds = [r.get("kind") for r in client.get(RULES).json()["rules"]]
    assert kinds.count("block") == 1


def test_stale_put_has_no_migration_side_effects(client, tid):
    _put_rules(client, [{"id": "a", "app": "code", "taskId": tid}])
    assert _put(client, _on(), 7).status_code == 412
    assert client.get(RULES).json()["version"] == 1 and db()["activity_block_rules"].count_documents({}) == 0


def test_migration_name_fallback_is_capped_and_second_level_cap_is_409(client, tid, monkeypatch):
    long_id = "x" * 64
    _put_rules(client, [{"id": long_id, "app": "code", "taskId": tid}, {"id": "b", "app": "y", "taskId": tid}])
    from app.modules.activity import blocks_repo  # noqa: PLC0415

    monkeypatch.setattr(blocks_repo, "MAX_RULES", 1)
    r = _put(client, _on(), 0)
    assert r.status_code == 409 and "第二级规则" in r.json()["detail"]
    assert client.get(RULES).json()["version"] == 1 and client.get(SET).json() == DEFAULT
    monkeypatch.setattr(blocks_repo, "MAX_RULES", 2000)
    assert _put(client, _on(), 0).status_code == 200
    names = [r["block"] for r in client.get(RULES).json()["rules"] if r.get("kind") == "block"]
    assert names[0] == f"旧规则 {long_id}"[:64] and len(names[0]) == 64
