"""任务「取消」（契约 v2.55）：``PATCH planner/tasks/{id}`` 的 ``canceled``。取消 = 已了结 + 标记；撤销取消 = 回到未完成。"""

from __future__ import annotations

from datetime import datetime, time, timedelta

from tests.test_planner_crud import PLANNER, _mk_project, _mk_task, _mk_zone

API = "/api/core"


def _task(client):
    zone = _mk_zone(client, name="取消区")
    project = _mk_project(client, zone["id"], name="取消项目")
    return project, _mk_task(client, project["id"], name="要取消的任务")


def _open_ids(client):
    return [t["id"] for z in client.get(f"{API}/views/next-actions").json()["zones"] for t in z["actionable"] + z["waiting"]]


def test_cancel_marks_done_and_canceled_and_leaves_open_list(client):
    project, task = _task(client)
    assert task["canceled"] is False and task["id"] in _open_ids(client)
    out = client.patch(f"{PLANNER}/tasks/{task['id']}", json={"canceled": True}).json()
    assert out["canceled"] is True and out["done"] is True and out["doneAt"] is not None
    assert task["id"] not in _open_ids(client)
    tree = client.get(f"{API}/views/tree").json()
    row = next(t for p in tree["projects"] if p["id"] == project["id"] for t in p["tasks"] if t["id"] == task["id"])
    assert row["canceled"] is True and row["done"] is True


def test_undo_cancel_returns_to_open(client):
    _, task = _task(client)
    client.patch(f"{PLANNER}/tasks/{task['id']}", json={"canceled": True})
    out = client.patch(f"{PLANNER}/tasks/{task['id']}", json={"canceled": False}).json()
    assert out["canceled"] is False and out["done"] is False and out["doneAt"] is None
    assert task["id"] in _open_ids(client)


def test_done_flag_changes_clear_canceled(client):
    _, task = _task(client)
    client.patch(f"{PLANNER}/tasks/{task['id']}", json={"canceled": True})
    undone = client.patch(f"{PLANNER}/tasks/{task['id']}", json={"done": False}).json()   # 老的「撤销」按钮
    assert undone["canceled"] is False and undone["done"] is False
    client.patch(f"{PLANNER}/tasks/{task['id']}", json={"canceled": True})
    done = client.patch(f"{PLANNER}/tasks/{task['id']}", json={"done": True}).json()
    assert done["canceled"] is False and done["done"] is True


def test_conflicting_done_and_canceled_is_400(client):
    _, task = _task(client)
    resp = client.patch(f"{PLANNER}/tasks/{task['id']}", json={"canceled": True, "done": False})
    assert resp.status_code == 400, resp.text


def test_cancel_is_in_audit_and_keeps_time_records(client):
    _, task = _task(client)
    from app.config import settings  # noqa: PLC0415
    day = datetime.now(settings.tz).date() - timedelta(days=1)
    start = datetime.combine(day, time(9), settings.tz).isoformat()
    assert client.post(f"{API}/timer/backfill", json={"taskId": task["id"], "startAt": start, "durationSeconds": 600, "mode": "do"}).status_code in (200, 201)
    client.patch(f"{PLANNER}/tasks/{task['id']}", json={"canceled": True})
    items = client.get(f"{PLANNER}/audit", params={"limit": 50}).json()["items"]
    assert any(i["objectId"] == task["id"] and i["changes"].get("canceled") is True for i in items)
    pt = client.get(f"{API}/views/project-time", params={"projectId": task["projectId"], "from": day.isoformat(), "to": day.isoformat()}).json()
    assert any(t["taskId"] == task["id"] and t["human"] for t in pt["tasks"])   # 计时记录还在
