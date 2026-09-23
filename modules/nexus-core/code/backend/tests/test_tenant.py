"""按租户分数据（契约 v2.0）。

要害：**一个租户看不到、改不了、写不进另一个租户的任何东西**；不带租户头的请求
与 v1.x 完全一样（单人部署零变化，既有 300 多条用例一条不改照样绿就是证明）。
另守三条护栏：头格式非法 400、严格模式缺头 401（不静默落到 u_local）、
信封里的 user 由服务端按租户盖章。
"""

from __future__ import annotations

import dataclasses

import pytest

API = "/api/core"
PLANNER = f"{API}/planner"
A = {"X-Nexus-Tenant": "ch_aaaa"}
B = {"X-Nexus-Tenant": "ch_bbbb"}


def _db():
    from app.repo import get_db  # noqa: PLC0415

    return get_db()


def _post(client, path, body, headers):
    resp = client.post(path, json=body, headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _tree(client, headers):
    zone = _post(client, f"{PLANNER}/zones", {"name": "甲的分区"}, headers)
    project = _post(client, f"{PLANNER}/projects", {"zoneId": zone["id"], "name": "甲的项目"}, headers)
    task = _post(client, f"{PLANNER}/tasks", {"projectId": project["id"], "name": "甲的任务"}, headers)
    return zone, project, task


@pytest.fixture()
def strict(monkeypatch):
    from app import config, main  # noqa: PLC0415

    patched = dataclasses.replace(config.settings, tenant_strict=True)
    monkeypatch.setattr(config, "settings", patched)
    monkeypatch.setattr(main, "settings", patched)  # health 读的是 main 模块里的名字


# ------------------------------------------------------------ 看不到


def test_other_tenant_sees_nothing_anywhere(client):
    zone, project, task = _tree(client, A)
    client.post(f"{API}/timer/start", json={"taskId": task["id"]}, headers=A)
    client.post(f"{API}/timer/stop", headers=A)

    for path in ("/zones", "/projects", "/tasks"):
        assert client.get(f"{PLANNER}{path}", headers=B).json() == [], path
    assert client.get(f"{API}/views/tree", headers=B).json()["zones"] == []
    assert client.get(f"{API}/events", headers=B).json()["total"] == 0
    assert client.get(f"{PLANNER}/audit", headers=B).json()["total"] == 0
    assert client.get(f"{API}/views/current", headers=B).json()["running"] is False
    export = client.get(f"{API}/export", headers=B).json()
    assert (export["zones"], export["tasks"], export["events"]) == ([], [], [])
    assert export["projections"]["proj_current"] is None

    # 甲自己什么都在
    assert len(client.get(f"{PLANNER}/tasks", headers=A).json()) == 1
    assert client.get(f"{API}/events", headers=A).json()["total"] == 1


def test_no_header_is_u_local_and_separate_from_tenants(client):
    _tree(client, A)
    assert client.get(f"{PLANNER}/zones").json() == [], "不带头 = u_local，看不到甲的"
    _post(client, f"{PLANNER}/zones", {"name": "单人的分区"}, {})
    assert [z["name"] for z in client.get(f"{PLANNER}/zones", headers=A).json()] == ["甲的分区"]


def test_responses_do_not_carry_the_user_field(client):
    """租户是存储层的事：响应形状与 v1.x 一模一样。"""
    zone, _, task = _tree(client, A)
    assert "user" not in client.get(f"{PLANNER}/zones", headers=A).json()[0]
    assert "user" not in client.get(f"{PLANNER}/tasks", headers=A).json()[0]
    assert "user" not in client.get(f"{API}/export", headers=A).json()["zones"][0]


# ------------------------------------------------------------ 改不了


@pytest.mark.parametrize("method", ["patch", "delete"])
def test_other_tenant_cannot_touch_an_object_by_id(client, method):
    _, _, task = _tree(client, A)
    kwargs = {"json": {"name": "乙改的"}} if method == "patch" else {}
    resp = getattr(client, method)(f"{PLANNER}/tasks/{task['id']}", headers=B, **kwargs)
    assert resp.status_code == 404, resp.text
    assert [t["name"] for t in client.get(f"{PLANNER}/tasks", headers=A).json()] == ["甲的任务"]


def test_other_tenant_cannot_hang_children_on_my_parent(client):
    zone, project, _ = _tree(client, A)
    assert client.post(f"{PLANNER}/projects", json={"zoneId": zone["id"], "name": "x"}, headers=B).status_code == 400
    assert client.post(f"{PLANNER}/tasks", json={"projectId": project["id"], "name": "x"}, headers=B).status_code == 400


def test_other_tenant_cannot_time_my_task(client):
    _, _, task = _tree(client, A)
    assert client.post(f"{API}/timer/start", json={"taskId": task["id"]}, headers=B).status_code == 404


def test_timers_are_per_tenant(client):
    _, _, task_a = _tree(client, A)
    _, _, task_b = _tree(client, B)
    client.post(f"{API}/timer/start", json={"taskId": task_a["id"]}, headers=A)
    client.post(f"{API}/timer/start", json={"taskId": task_b["id"]}, headers=B)
    client.post(f"{API}/timer/stop", headers=B)
    assert client.get(f"{API}/views/current", headers=A).json()["running"] is True, "乙停表不许停掉甲的"


# ------------------------------------------------------------ 写不进


def test_envelope_user_is_stamped_from_the_tenant_not_the_client(client):
    _, project, task = _tree(client, B)
    envelope = {
        "spec": "yq-event/v1", "id": "evt_forge", "dedupeKey": "dk-forge",
        "type": "session.completed", "user": "ch_aaaa", "source": "tenant-test",
        "time": "2026-09-01T09:00:00+00:00",
        "subject": {"zone": project["zoneId"], "project": project["id"], "task": task["id"]},
        "data": {"durationSeconds": 60, "startAt": "2026-09-01T08:59:00+00:00"},
    }
    assert client.post(f"{API}/events", json=envelope, headers=B).json()["accepted"] == 1
    assert client.get(f"{API}/events", headers=A).json()["total"] == 0, "信封自称是甲，也写不进甲的台账"
    assert _db()["events"].find_one({"id": "evt_forge"})["user"] == "ch_bbbb"


def test_same_snapshot_restores_into_two_tenants(client):
    """同一份快照（同样的 id）恢复进两个租户：各一份，互不冲突。"""
    _, _, task = _tree(client, A)
    client.post(f"{API}/timer/start", json={"taskId": task["id"]}, headers=A)
    client.post(f"{API}/timer/stop", headers=A)
    snapshot = client.get(f"{API}/export", headers=A).json()
    for tenant in ({"X-Nexus-Tenant": "ch_cccc"}, {"X-Nexus-Tenant": "ch_dddd"}):
        dry = client.post(f"{API}/restore", json=snapshot, headers=tenant)
        assert dry.status_code == 200, dry.text
        apply = client.post(f"{API}/restore", json=snapshot, headers=tenant,
                            params={"dryRun": "false", "checksum": dry.json()["checksum"]})
        assert apply.status_code == 200, apply.text
    ids = {z["id"] for z in snapshot["zones"]}
    assert _db()["zones"].count_documents({"id": {"$in": list(ids)}}) == 3
    # 快照里的事件自称属于甲；恢复进丙，就属于丙——甲的台账不许多出一条
    assert client.get(f"{API}/events", headers=A).json()["total"] == len(snapshot["events"])
    assert client.get(f"{API}/events", headers={"X-Nexus-Tenant": "ch_cccc"}).json()["total"] == len(snapshot["events"])


def test_name_numbers_are_per_tenant(client):
    """登记表按租户：甲用过的名字，乙建同名对象不会拿到甲的号（不泄露别人用过什么名字）。"""
    for name in ("一", "二", "三"):
        _post(client, f"{PLANNER}/zones", {"name": name}, A)
    first_b = _post(client, f"{PLANNER}/zones", {"name": "三"}, B)
    assert first_b["key"] == "1"


# ------------------------------------------------------------ 老数据与护栏


def test_legacy_documents_without_user_stay_visible_to_u_local(client):
    """迁移是手动跑的。升级后不跑迁移，单人部署的老数据也必须照常可见；别的租户看不到。"""
    _db()["zones"].insert_one({"id": "z_legacy", "key": "9", "name": "老分区", "color": "#999999", "order": 0})
    assert [z["id"] for z in client.get(f"{PLANNER}/zones").json()] == ["z_legacy"]
    assert client.get(f"{PLANNER}/zones", headers=A).json() == []


def test_malformed_tenant_header_is_400(client):
    for bad in ("has space", "x" * 65, "a/b", "ch_\"quoted\""):
        resp = client.get(f"{PLANNER}/zones", headers={"X-Nexus-Tenant": bad})
        assert resp.status_code == 400, bad
        assert "X-Nexus-Tenant" in resp.json()["detail"]


def test_strict_mode_rejects_a_missing_header_instead_of_falling_back(client, strict):
    resp = client.get(f"{PLANNER}/zones")
    assert resp.status_code == 401
    assert "NEXUS_TENANT_STRICT" in resp.json()["detail"]
    assert client.get(f"{PLANNER}/zones", headers=A).status_code == 200
    assert client.get(f"{API}/health").status_code == 200, "健康检查直连不经网关，不看租户"
    assert client.get(f"{API}/health").json()["tenantGuard"] == "strict"


def test_startup_replaces_the_legacy_global_unique_indexes(client):
    """v1.x 的库上有全局唯一索引（zones/projects/tasks 的 id、登记表的 name）。
    不换掉它，第二个租户建收件箱（固定 id p_inbox）或同名分区就撞 DuplicateKeyError。"""
    from app.modules.planner.repo import ensure_tenant_indexes  # noqa: PLC0415

    db = _db()
    for name in ("uniq_user_id",):
        if name in db["zones"].index_information():
            db["zones"].drop_index(name)
    if "uniq_user_name" in db["name_registry"].index_information():
        db["name_registry"].drop_index("uniq_user_name")
    db["zones"].create_index([("id", 1)], unique=True, name="uniq_id")
    db["name_registry"].create_index([("name", 1)], unique=True, name="uniq_name")

    ensure_tenant_indexes()

    assert "uniq_id" not in db["zones"].index_information()
    assert "uniq_name" not in db["name_registry"].index_information()
    for tenant in (A, B):
        _post(client, f"{PLANNER}/zones", {"name": "同名分区"}, tenant)
    db["zones"].insert_one({"id": "z_same", "user": "ch_aaaa"})
    db["zones"].insert_one({"id": "z_same", "user": "ch_bbbb"})
