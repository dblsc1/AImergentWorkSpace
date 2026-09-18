"""收件箱 well-known 项目（contract.md v1.5「收件箱」节，PRD F-INBOX-1）。

覆盖：种子幂等、`p_inbox`/`z_inbox` 固定 id、`DELETE p_inbox` 恒 409、
任务仍可正常搬出/搬入 p_inbox（禁删保护的是容器本身，不限制任务流动）。
"""

from __future__ import annotations

from app.modules.planner import inbox

API = "/api/core"
PLANNER = f"{API}/planner"


def _mk_zone(client, name="示例分区一", **extra) -> dict:
    resp = client.post(f"{PLANNER}/zones", json={"name": name, **extra})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _mk_project(client, zone_id, name="示例项目一", **extra) -> dict:
    resp = client.post(f"{PLANNER}/projects", json={"zoneId": zone_id, "name": name, **extra})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _mk_task(client, project_id, name="任务", **extra) -> dict:
    resp = client.post(f"{PLANNER}/tasks", json={"projectId": project_id, "name": name, **extra})
    assert resp.status_code == 200, resp.text
    return resp.json()


# --------------------------------------------------------------- 幂等种子


def test_inbox_ensure_creates_well_known_zone_and_project():
    project = inbox.ensure()
    assert project["id"] == "p_inbox"
    assert project["zoneId"] == "z_inbox"
    assert project["name"] == "收件箱"
    assert project["status"] == "active"


def test_inbox_ensure_is_idempotent_no_duplicate():
    from app.repo import get_db  # noqa: PLC0415

    first = inbox.ensure()
    second = inbox.ensure()
    assert first["id"] == second["id"] == "p_inbox"

    db = get_db()
    assert db["projects"].count_documents({"id": "p_inbox"}) == 1
    assert db["zones"].count_documents({"id": "z_inbox"}) == 1


def test_inbox_appears_in_planner_list_with_normal_shape(client):
    inbox.ensure()
    resp = client.get(f"{PLANNER}/projects")
    assert resp.status_code == 200
    inbox_project = next(p for p in resp.json() if p["id"] == "p_inbox")
    assert inbox_project["key"]
    assert inbox_project["zoneId"] == "z_inbox"
    assert inbox_project["lastWriter"] == "human"


# --------------------------------------------------------------- 禁删


def test_delete_p_inbox_rejected_with_409(client):
    inbox.ensure()
    resp = client.delete(f"{PLANNER}/projects/p_inbox")
    assert resp.status_code == 409, resp.text
    detail = resp.json()["detail"]
    assert "p_inbox" in detail
    assert "收件箱" in detail or "禁止删除" in detail


def test_delete_p_inbox_rejected_even_when_empty(client):
    """禁删不是"有子对象"那道 409 的另一种触发——收件箱下**没有任何任务**
    时同样必须 409（触发条件是"这是系统单例"，不是"下面还有东西"）。"""
    inbox.ensure()
    from app.repo import get_db  # noqa: PLC0415

    assert get_db()["tasks"].count_documents({"projectId": "p_inbox"}) == 0
    resp = client.delete(f"{PLANNER}/projects/p_inbox")
    assert resp.status_code == 409, resp.text


def test_delete_nonexistent_project_still_404_not_409(client):
    """不能因为加了保护就把"根本不存在的项目" 也误判成受保护——404/409 分流不能乱。"""
    resp = client.delete(f"{PLANNER}/projects/p_不存在")
    assert resp.status_code == 404, resp.text


# --------------------------------------------------------------- 任务流动不受限


def test_task_can_be_created_in_and_moved_out_of_inbox(client):
    inbox.ensure()
    task = _mk_task(client, "p_inbox", name="随手记的想法")
    assert task["projectId"] == "p_inbox"

    zone = _mk_zone(client)
    project = _mk_project(client, zone["id"])
    moved = client.patch(
        f"{PLANNER}/tasks/{task['id']}", json={"projectId": project["id"]}
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["projectId"] == project["id"]
    assert moved.json()["id"] == task["id"], "换归属不许换 id（J10）"


def test_other_projects_can_still_be_deleted_normally(client):
    """禁删保护只钉住 p_inbox 这一个 id，不影响其余项目正常删除。"""
    zone = _mk_zone(client)
    project = _mk_project(client, zone["id"])
    resp = client.delete(f"{PLANNER}/projects/{project['id']}")
    assert resp.status_code == 204, resp.text
