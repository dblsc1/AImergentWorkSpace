"""检测程序分类规则与 AI 草稿（契约 v2.6，contracts/detector.rules.v1）。

要害：设备令牌只读（PUT / 建草稿 / 应用 / 丢弃带 Bearer 一律 403）；If-Match 乐观并发（缺 428、对不上 412）；
校验逐条带下标（taskId 必须存在、正则须 RE2 能用）；草稿整套替换、diff 按 id、应用原子、14 天过期；按租户隔离。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

API = "/api/core/detector/rules"
DRAFTS = f"{API}/drafts"
BEARER = {"Authorization": "Bearer tok_device"}
A = {"X-Nexus-Tenant": "ch_aaaa"}
B = {"X-Nexus-Tenant": "ch_bbbb"}


@pytest.fixture()
def tid(seeded):
    return seeded["tasks"]["示例任务三"]["id"]


def _put(client, rules, version=0, headers=None):
    h = {"If-Match": f'"{version}"', **(headers or {})}
    return client.put(API, json={"rules": rules}, headers=h)


def _draft(client, rules, summary="按终端标题分开项目", headers=None):
    return client.post(DRAFTS, json={"rules": rules, "summary": summary}, headers=headers or {})


def test_empty_state(client):
    r = client.get(API)
    assert r.status_code == 200 and r.json() == {"version": 0, "updatedAt": None, "rules": []}
    assert r.headers["ETag"] == '"0"'
    assert client.get(f"{DRAFTS}/current").json() == {"draft": None}


def test_put_fills_defaults_assigns_ids_and_bumps_version(client, tid):
    r = _put(client, [{"title": "garden", "taskId": tid},
                      {"id": "keep-me", "app": "code", "title": None, "taskId": tid, "confidence": 1,
                       "note": "编辑器", "enabled": False}])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["version"] == 1 and r.headers["ETag"] == '"1"' and body["updatedAt"]
    first, second = body["rules"]
    assert first["id"].startswith("r_") and len(first["id"]) == 14
    assert first == {"id": first["id"], "app": None, "title": "garden", "taskId": tid, "confidence": 0.9,
                     "note": None, "enabled": True}
    assert second["id"] == "keep-me" and second["enabled"] is False and second["confidence"] == 1
    got = client.get(API, headers=BEARER)  # 设备令牌能读
    assert got.status_code == 200 and got.json() == body


def test_if_match_required_and_conflict(client, tid):
    rule = [{"title": "x", "taskId": tid}]
    assert client.put(API, json={"rules": rule}).status_code == 428
    assert client.put(API, json={"rules": rule}, headers={"If-Match": "*"}).status_code == 428
    for bad in ('"0', '0"', '"x"'):
        assert client.put(API, json={"rules": rule}, headers={"If-Match": bad}).status_code == 428, bad
    assert _put(client, rule, 0).status_code == 200
    r = _put(client, rule, 0)  # 基于旧版本
    assert r.status_code == 412 and r.json()["currentVersion"] == 1 and isinstance(r.json()["detail"], str)
    assert client.put(API, json={"rules": []}, headers={"If-Match": 'W/"1"'}).status_code == 200
    assert client.get(API).json()["version"] == 2 and client.get(API).json()["rules"] == []


def test_device_token_cannot_write(client, tid):
    rule = [{"title": "x", "taskId": tid}]
    assert _put(client, rule, headers=BEARER).status_code == 403
    assert _put(client, rule, headers={"Authorization": "  bearer x"}).status_code == 403
    assert _draft(client, rule, headers=BEARER).status_code == 403
    d = _draft(client, rule).json()
    assert client.post(f"{DRAFTS}/{d['id']}/apply", headers={**BEARER, "If-Match": '"0"'}).status_code == 403
    assert client.post(f"{DRAFTS}/{d['id']}/discard", headers=BEARER).status_code == 403
    # 403 在任何写入之前
    assert client.get(API).json()["version"] == 0
    assert client.get(f"{DRAFTS}/current").json()["draft"]["id"] == d["id"]


@pytest.mark.parametrize("rule,field", [
    ({"taskId": "T"}, None),                                   # app / title 都没有
    ({"title": "x", "taskId": "t_nope"}, "taskId"),            # 任务不存在
    ({"title": "(?=a)b", "taskId": "T"}, "title"),             # RE2 不支持
    ({"app": "a(b", "taskId": "T"}, "app"),                    # 写错
    ({"title": "", "taskId": "T"}, "title"),
    ({"title": "x" * 201, "taskId": "T"}, "title"),
    ({"title": "x", "taskId": "T", "confidence": 0}, "confidence"),
    ({"title": "x", "taskId": "T", "confidence": 1.5}, "confidence"),
    ({"title": "x", "taskId": "T", "confidence": True}, "confidence"),
    ({"title": "x", "taskId": "T", "note": "n" * 121}, "note"),
    ({"title": "x", "taskId": "T", "enabled": "yes"}, "enabled"),
    ({"title": "x", "taskId": "T", "id": "bad id"}, "id"),
    ({"title": "x", "taskId": "T", "extra": 1}, "extra"),
])
def test_rule_validation_reports_index(client, tid, rule, field):
    rule = {k: (tid if v == "T" else v) for k, v in rule.items()}
    r = _put(client, [{"title": "ok", "taskId": tid}, rule])
    assert r.status_code == 422, r.text
    errs = r.json()["errors"]
    assert errs[0]["index"] == 1 and errs[0]["field"] == field
    assert isinstance(r.json()["detail"], str) and "rules[1]" in r.json()["detail"]
    assert client.get(API).json()["version"] == 0


def test_go_only_syntax_and_duplicate_ids(client, tid):
    ok = [{"title": r"\p{Han}+", "taskId": tid}, {"title": r"(?<proj>\w+)\z", "taskId": tid}]
    assert _put(client, ok).status_code == 200
    r = _put(client, [{"id": "a", "title": "x", "taskId": tid}, {"id": "a", "title": "y", "taskId": tid}], 1)
    assert r.status_code == 422 and r.json()["errors"] == [{"index": 1, "field": "id", "message": "id 重复：a"}]


def test_limits(client, tid):
    many = [{"title": f"t{i}", "taskId": tid} for i in range(501)]
    r = _put(client, many)
    assert r.status_code == 422 and r.json()["errors"][0]["field"] == "rules"
    assert _put(client, many[:500]).status_code == 200
    big = b'{"rules":[],"x":"' + b"a" * (260 * 1024) + b'"}'
    assert client.put(API, content=big, headers={"If-Match": '"1"'}).status_code == 413
    assert client.put(API, content=b"nope", headers={"If-Match": '"1"'}).status_code == 422
    assert client.put(API, json={"rules": [], "x": 1}, headers={"If-Match": '"1"'}).status_code == 422
    # 错误最多 50 条
    r = _put(client, [{"title": "x", "taskId": "t_nope"}] * 60, 1)
    assert r.status_code == 422 and len(r.json()["errors"]) == 50 and r.json()["detail"].startswith("60 处")


def test_draft_diff_apply_flow(client, tid):
    base = _put(client, [{"id": "keep", "title": "a", "taskId": tid},
                         {"id": "edit", "title": "b", "taskId": tid},
                         {"id": "gone", "title": "c", "taskId": tid}]).json()
    r = _draft(client, [{"id": "edit", "title": "b2", "taskId": tid}, {"id": "keep", "title": "a", "taskId": tid},
                        {"title": "new", "taskId": tid}])
    assert r.status_code == 201, r.text
    d = r.json()
    new_id = d["rules"][2]["id"]
    assert d["status"] == "pending" and d["author"] == "assistant" and d["summary"] == "按终端标题分开项目"
    assert d["baseVersion"] == d["currentVersion"] == base["version"] == 1
    assert d["diff"] == {"added": [new_id], "removed": ["gone"], "changed": ["edit"], "unchanged": 1,
                         "reordered": True}
    exp = datetime.fromisoformat(d["expiresAt"]) - datetime.fromisoformat(d["createdAt"])
    assert exp == timedelta(days=14)
    assert client.get(f"{DRAFTS}/current").json()["draft"] == d
    # 草稿不改生效规则
    assert client.get(API).json() == base
    # 应用：If-Match 必带、须是此刻的版本
    assert client.post(f"{DRAFTS}/{d['id']}/apply").status_code == 428
    assert client.post(f"{DRAFTS}/drf_nope/apply", headers={"If-Match": '"1"'}).status_code == 404
    r = client.post(f"{DRAFTS}/{d['id']}/apply", headers={"If-Match": '"1"'})
    assert r.status_code == 200 and r.headers["ETag"] == '"2"'
    assert r.json()["version"] == 2 and r.json()["rules"] == d["rules"]
    assert client.get(f"{DRAFTS}/current").json() == {"draft": None}
    assert client.post(f"{DRAFTS}/{d['id']}/apply", headers={"If-Match": '"2"'}).status_code == 404


def test_apply_stale_version_412_and_diff_recomputed(client, tid):
    _put(client, [{"id": "a", "title": "a", "taskId": tid}])
    d = _draft(client, [{"id": "a", "title": "a", "taskId": tid}]).json()
    assert d["diff"]["unchanged"] == 1
    _put(client, [], 1)  # 人在草稿之后又改了规则
    r = client.post(f"{DRAFTS}/{d['id']}/apply", headers={"If-Match": '"1"'})
    assert r.status_code == 412 and r.json()["currentVersion"] == 2
    again = client.get(f"{DRAFTS}/current").json()["draft"]
    assert again["currentVersion"] == 2 and again["baseVersion"] == 1 and again["diff"]["added"] == ["a"]
    assert client.post(f"{DRAFTS}/{d['id']}/apply", headers={"If-Match": '"2"'}).status_code == 200


def test_new_draft_replaces_old_and_discard(client, tid):
    d1 = _draft(client, [{"title": "a", "taskId": tid}]).json()
    d2 = _draft(client, [], summary="全部删掉").json()
    assert d1["id"] != d2["id"]
    assert client.get(f"{DRAFTS}/current").json()["draft"]["id"] == d2["id"]
    assert client.post(f"{DRAFTS}/{d1['id']}/apply", headers={"If-Match": '"0"'}).status_code == 404
    assert client.post(f"{DRAFTS}/{d1['id']}/discard").status_code == 204  # 不是当前那份：什么都不动
    assert client.get(f"{DRAFTS}/current").json()["draft"]["id"] == d2["id"]
    assert client.post(f"{DRAFTS}/{d2['id']}/discard").status_code == 204
    assert client.post(f"{DRAFTS}/{d2['id']}/discard").status_code == 204  # 幂等
    assert client.get(f"{DRAFTS}/current").json() == {"draft": None}


def test_empty_draft_applied_makes_server_authoritative(client):
    d = _draft(client, [], summary="清空").json()
    assert d["diff"] == {"added": [], "removed": [], "changed": [], "unchanged": 0, "reordered": False}
    r = client.post(f"{DRAFTS}/{d['id']}/apply", headers={"If-Match": '"0"'})
    assert r.status_code == 200 and r.json()["version"] == 1 and r.json()["rules"] == []


@pytest.mark.parametrize("body,field", [
    ({"rules": []}, "summary"),
    ({"rules": [], "summary": ""}, "summary"),
    ({"rules": [], "summary": "s" * 501}, "summary"),
    ({"rules": [], "summary": "s", "author": "root"}, "author"),
    ({"rules": [], "summary": "s", "extra": 1}, "extra"),
    ({"summary": "s"}, "rules"),
    ({"rules": {}, "summary": "s"}, "rules"),
])
def test_draft_validation(client, body, field):
    r = client.post(DRAFTS, json=body)
    assert r.status_code == 422 and r.json()["errors"][0]["field"] == field
    assert client.get(f"{DRAFTS}/current").json() == {"draft": None}


def test_draft_author_human_and_per_rule_errors(client, tid):
    r = client.post(DRAFTS, json={"rules": [{"title": "x", "taskId": tid}], "summary": "s", "author": "human"})
    assert r.status_code == 201 and r.json()["author"] == "human"
    r = _draft(client, [{"title": "x", "taskId": tid}, {"title": "(?!x)", "taskId": "t_nope"}])
    assert r.status_code == 422 and [e["index"] for e in r.json()["errors"]] == [1]


def test_apply_rejects_deleted_task(client, tid):
    zone = client.post("/api/core/planner/zones", json={"name": "临时区"}).json()
    proj = client.post("/api/core/planner/projects", json={"zoneId": zone["id"], "name": "临时项目"}).json()
    task = client.post("/api/core/planner/tasks", json={"projectId": proj["id"], "name": "马上删"}).json()
    d = _draft(client, [{"title": "a", "taskId": tid}, {"title": "b", "taskId": task["id"]}]).json()
    assert client.delete(f"/api/core/planner/tasks/{task['id']}").status_code in (200, 204)
    r = client.post(f"{DRAFTS}/{d['id']}/apply", headers={"If-Match": '"0"'})
    assert r.status_code == 422 and r.json()["errors"][0] == {"index": 1, "field": "taskId",
                                                             "message": f"任务不存在：{task['id']}"}
    assert client.get(f"{DRAFTS}/current").json()["draft"]["id"] == d["id"]  # 草稿不动


def test_draft_expires(client, tid):
    from app.repo import get_db  # noqa: PLC0415

    d = _draft(client, [{"title": "a", "taskId": tid}]).json()
    past = datetime.now(timezone.utc) - timedelta(seconds=1)
    get_db()["detector_rules"].update_one({"draft.id": d["id"]}, {"$set": {"draft.expiresAt": past}})
    assert client.get(f"{DRAFTS}/current").json() == {"draft": None}
    assert client.post(f"{DRAFTS}/{d['id']}/apply", headers={"If-Match": '"0"'}).status_code == 404
    assert "draft" not in get_db()["detector_rules"].find_one({})


def test_tenant_isolation(client):
    zone = client.post("/api/core/planner/zones", json={"name": "A区"}, headers=A).json()
    proj = client.post("/api/core/planner/projects", json={"zoneId": zone["id"], "name": "A项目"}, headers=A).json()
    task = client.post("/api/core/planner/tasks", json={"projectId": proj["id"], "name": "A任务"}, headers=A).json()
    assert _put(client, [{"title": "a", "taskId": task["id"]}], headers=A).status_code == 200
    d = _draft(client, [], headers=A).json()
    # B 看不到 A 的规则与草稿，也不能用 A 的任务、不能应用 A 的草稿
    assert client.get(API, headers=B).json()["version"] == 0
    assert client.get(f"{DRAFTS}/current", headers=B).json() == {"draft": None}
    assert _put(client, [{"title": "a", "taskId": task["id"]}], headers=B).status_code == 422
    assert client.post(f"{DRAFTS}/{d['id']}/apply", headers={**B, "If-Match": '"0"'}).status_code == 404
    assert client.post(f"{DRAFTS}/{d['id']}/discard", headers=B).status_code == 204
    assert client.get(f"{DRAFTS}/current", headers=A).json()["draft"]["id"] == d["id"]


def test_rules_not_exported(client, tid):
    _put(client, [{"title": "secret-rule-marker", "taskId": tid}])
    assert "secret-rule-marker" not in client.get("/api/core/export").text
