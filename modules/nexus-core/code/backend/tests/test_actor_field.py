"""写者字段 actor/lastWriter（contract.md v1.5「写者字段 actor/lastWriter」节，
PRD F-ACTOR-1，**仅字段**）。

覆盖：缺省 human、可带 actor=ai、非法值 400、PATCH 不传 actor 不动
lastWriter、PATCH 传 actor 会改 lastWriter、三类对象各至少覆盖一次入口。
**不测来源校验**——契约明文本版不做来源区分（留波2），任何调用方现在都能
自报 actor，这是已知的、有意的行为，本文件只验证"字段落库对不对"。
"""

from __future__ import annotations

API = "/api/core"
PLANNER = f"{API}/planner"


def _mk_zone(client, name="示例分区一", **extra) -> dict:
    resp = client.post(f"{PLANNER}/zones", json={"name": name, **extra})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _mk_project(client, zone_id, name="项目", **extra) -> dict:
    resp = client.post(f"{PLANNER}/projects", json={"zoneId": zone_id, "name": name, **extra})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _mk_task(client, project_id, name="任务", **extra) -> dict:
    resp = client.post(f"{PLANNER}/tasks", json={"projectId": project_id, "name": name, **extra})
    assert resp.status_code == 200, resp.text
    return resp.json()


# --------------------------------------------------------------- 建：缺省与可选


def test_create_task_without_actor_defaults_to_human(client):
    zone = _mk_zone(client)
    project = _mk_project(client, zone["id"])
    task = _mk_task(client, project["id"])
    assert task["lastWriter"] == "human"


def test_create_task_with_actor_ai_records_ai(client):
    zone = _mk_zone(client)
    project = _mk_project(client, zone["id"])
    task = _mk_task(client, project["id"], actor="ai")
    assert task["lastWriter"] == "ai"

    got = client.get(f"{PLANNER}/tasks", params={"projectId": project["id"]}).json()
    assert next(t for t in got if t["id"] == task["id"])["lastWriter"] == "ai"


def test_create_zone_and_project_also_accept_actor(client):
    """三类对象都收 actor，不是只有 task——建 zone/project 时各测一次。"""
    zone = _mk_zone(client, actor="ai")
    assert zone["lastWriter"] == "ai"
    project = _mk_project(client, zone["id"], actor="ai")
    assert project["lastWriter"] == "ai"


def test_invalid_actor_value_rejected_with_400(client):
    zone = _mk_zone(client)
    project = _mk_project(client, zone["id"])
    resp = client.post(
        f"{PLANNER}/tasks",
        json={"projectId": project["id"], "name": "坏actor", "actor": "robot"},
    )
    assert resp.status_code == 422, resp.text  # pydantic Literal 校验先拦（不到 service 层）


# --------------------------------------------------------------- 改：PATCH 语义


def test_patch_without_actor_does_not_change_last_writer(client):
    zone = _mk_zone(client)
    project = _mk_project(client, zone["id"])
    task = _mk_task(client, project["id"], actor="ai")
    assert task["lastWriter"] == "ai"

    patched = client.patch(f"{PLANNER}/tasks/{task['id']}", json={"name": "改个名"})
    assert patched.status_code == 200, patched.text
    assert patched.json()["lastWriter"] == "ai", "不传 actor 时 lastWriter 不许被悄悄改回 human"
    assert patched.json()["name"] == "改个名"


def test_patch_with_actor_updates_last_writer(client):
    zone = _mk_zone(client)
    project = _mk_project(client, zone["id"])
    task = _mk_task(client, project["id"])  # 缺省 human
    assert task["lastWriter"] == "human"

    patched = client.patch(
        f"{PLANNER}/tasks/{task['id']}", json={"actor": "ai"}
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["lastWriter"] == "ai"
    # 其余字段不受影响（actor 只翻译成 lastWriter，不污染别的字段）
    assert patched.json()["name"] == task["name"]


def test_patch_project_actor_also_works(client):
    zone = _mk_zone(client)
    project = _mk_project(client, zone["id"])  # 缺省 human
    patched = client.patch(f"{PLANNER}/projects/{project['id']}", json={"actor": "ai"})
    assert patched.status_code == 200, patched.text
    assert patched.json()["lastWriter"] == "ai"


def test_normalize_actor_rejects_bad_value_at_service_layer():
    """`actor.normalize_actor` 是 pydantic Literal 校验之外的第二道防线——
    直接单元测（不经 HTTP 层，pydantic 422 会先拦住，这里测的是 service
    层自己也不会对坏值沉默放行）。"""
    from app.modules.planner.actor import normalize_actor
    from app.modules.planner.errors import InvalidInputError

    assert normalize_actor(None) == "human"
    assert normalize_actor("ai") == "ai"
    try:
        normalize_actor("robot")
    except InvalidInputError as exc:
        assert "robot" in str(exc)
    else:
        raise AssertionError("非法 actor 值必须抛 InvalidInputError")


def test_patch_with_invalid_actor_rejected_and_does_not_partially_apply(client):
    zone = _mk_zone(client)
    project = _mk_project(client, zone["id"])
    task = _mk_task(client, project["id"])
    resp = client.patch(
        f"{PLANNER}/tasks/{task['id']}", json={"name": "不该生效", "actor": "robot"}
    )
    assert resp.status_code == 422, resp.text

    got = client.get(f"{PLANNER}/tasks", params={"projectId": project["id"]}).json()
    unchanged = next(t for t in got if t["id"] == task["id"])
    assert unchanged["name"] == task["name"], "422 拒绝必须全有全无，name 不许被部分应用"
