"""每周回顾聚合读端（contract.md v1.5「每周回顾读端」，PRD F-REVIEW-1）。

覆盖：四块聚合——本周计划vs事实、过期项目、久未动任务、inbox 待清空计数；
全部现有数据重新聚合，零新集合。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from app.config import settings
from app.modules.planner import inbox
from app.modules.views.review import STALE_TASK_DAYS
from app.timeutil import local_date

API = "/api/core"
PLANNER = f"{API}/planner"
EVENTS = f"{API}/events"
REVIEW = f"{API}/views/review"


def _today() -> str:
    return local_date(datetime.now(settings.tz), settings.tz)


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


def _post_session(client, *, dedupe_key, project_id, task_id, start_at):
    doc = {
        "spec": "yq-event/v1", "id": f"evt_{dedupe_key}", "dedupeKey": dedupe_key,
        "type": "session.completed", "user": "u_local", "source": "review-test",
        "time": start_at,
        "subject": {"zone": "z_x", "project": project_id, "task": task_id},
        "data": {"durationSeconds": 900, "startAt": start_at},
    }
    resp = client.post(EVENTS, json=doc)
    assert resp.status_code == 200 and resp.json()["accepted"] == 1, resp.text


# --------------------------------------------------------------- 结构 & today/week


def test_review_shape_on_empty_db(client):
    resp = client.get(REVIEW)
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {
        "today", "weekStart", "weekEnd",
        "planVsActual", "overdueProjects", "staleTasks", "inboxPendingCount",
    }
    assert body["today"] == _today()
    assert body["planVsActual"] == []
    assert body["overdueProjects"] == []
    assert body["staleTasks"] == []
    assert body["inboxPendingCount"] == 0


def test_week_bounds_are_monday_to_sunday(client):
    body = client.get(REVIEW).json()
    start = datetime.strptime(body["weekStart"], "%Y-%m-%d").date()
    end = datetime.strptime(body["weekEnd"], "%Y-%m-%d").date()
    assert start.weekday() == 0  # 周一
    assert end.weekday() == 6  # 周日
    assert (end - start).days == 6
    today = datetime.strptime(body["today"], "%Y-%m-%d").date()
    assert start <= today <= end


# --------------------------------------------------------------- 计划vs事实


def test_plan_vs_actual_includes_all_projects_with_actual_seconds(client):
    zone = _mk_zone(client)
    project = _mk_project(client, zone["id"])
    task = _mk_task(client, project["id"])
    now = datetime.now(settings.tz)
    _post_session(
        client, dedupe_key="review-1", project_id=project["id"], task_id=task["id"],
        start_at=now.isoformat(),
    )

    body = client.get(REVIEW).json()
    entry = next(p for p in body["planVsActual"] if p["projectId"] == project["id"])
    assert entry["actualSecondsThisWeek"] == 900
    assert entry["scheduledThisWeek"] is False  # 该项目无 plan


def test_plan_scheduled_this_week_flag(client):
    zone = _mk_zone(client)
    project = _mk_project(client, zone["id"])
    today = _today()
    client.patch(
        f"{PLANNER}/projects/{project['id']}",
        json={"plan": {"start": today, "end": today}},
    )
    body = client.get(REVIEW).json()
    entry = next(p for p in body["planVsActual"] if p["projectId"] == project["id"])
    assert entry["scheduledThisWeek"] is True


def test_plan_not_overlapping_this_week_is_false(client):
    zone = _mk_zone(client)
    project = _mk_project(client, zone["id"])
    far_future = (datetime.strptime(_today(), "%Y-%m-%d") + timedelta(days=365)).strftime(
        "%Y-%m-%d"
    )
    client.patch(
        f"{PLANNER}/projects/{project['id']}",
        json={"plan": {"start": far_future, "end": far_future}},
    )
    body = client.get(REVIEW).json()
    entry = next(p for p in body["planVsActual"] if p["projectId"] == project["id"])
    assert entry["scheduledThisWeek"] is False


# --------------------------------------------------------------- 过期项目


def test_overdue_project_listed(client):
    zone = _mk_zone(client)
    project = _mk_project(client, zone["id"])
    yesterday = (
        datetime.strptime(_today(), "%Y-%m-%d") - timedelta(days=1)
    ).strftime("%Y-%m-%d")
    client.patch(
        f"{PLANNER}/projects/{project['id']}",
        json={"plan": {"start": "2020-01-01", "end": yesterday}},
    )
    body = client.get(REVIEW).json()
    assert project["id"] in [p["id"] for p in body["overdueProjects"]]


def test_project_without_plan_not_in_overdue(client):
    zone = _mk_zone(client)
    project = _mk_project(client, zone["id"])
    body = client.get(REVIEW).json()
    assert project["id"] not in [p["id"] for p in body["overdueProjects"]]


def test_done_project_not_in_overdue_even_if_plan_end_passed(client):
    zone = _mk_zone(client)
    project = _mk_project(client, zone["id"])
    yesterday = (
        datetime.strptime(_today(), "%Y-%m-%d") - timedelta(days=1)
    ).strftime("%Y-%m-%d")
    client.patch(
        f"{PLANNER}/projects/{project['id']}",
        json={"plan": {"start": "2020-01-01", "end": yesterday}, "status": "done"},
    )
    body = client.get(REVIEW).json()
    assert project["id"] not in [p["id"] for p in body["overdueProjects"]]


# --------------------------------------------------------------- 久未动任务


def test_never_timed_undone_task_is_stale(client):
    zone = _mk_zone(client)
    project = _mk_project(client, zone["id"])
    task = _mk_task(client, project["id"])
    body = client.get(REVIEW).json()
    entry = next(t for t in body["staleTasks"] if t["id"] == task["id"])
    assert entry["lastActiveDate"] is None


def test_recently_active_task_is_not_stale(client):
    zone = _mk_zone(client)
    project = _mk_project(client, zone["id"])
    task = _mk_task(client, project["id"])
    now = datetime.now(settings.tz)
    _post_session(
        client, dedupe_key="review-recent", project_id=project["id"], task_id=task["id"],
        start_at=now.isoformat(),
    )
    body = client.get(REVIEW).json()
    assert task["id"] not in [t["id"] for t in body["staleTasks"]]


def test_task_active_long_ago_is_stale_with_last_active_date(client):
    zone = _mk_zone(client)
    project = _mk_project(client, zone["id"])
    task = _mk_task(client, project["id"])
    old_moment = datetime.now(settings.tz) - timedelta(days=STALE_TASK_DAYS + 5)
    _post_session(
        client, dedupe_key="review-old", project_id=project["id"], task_id=task["id"],
        start_at=old_moment.isoformat(),
    )
    body = client.get(REVIEW).json()
    entry = next(t for t in body["staleTasks"] if t["id"] == task["id"])
    assert entry["lastActiveDate"] == local_date(old_moment, settings.tz)


def test_done_task_never_appears_in_stale(client):
    zone = _mk_zone(client)
    project = _mk_project(client, zone["id"])
    task = _mk_task(client, project["id"])
    client.patch(f"{PLANNER}/tasks/{task['id']}", json={"done": True})
    body = client.get(REVIEW).json()
    assert task["id"] not in [t["id"] for t in body["staleTasks"]]


# --------------------------------------------------------------- inbox 待清空


def test_inbox_pending_count_reflects_unmoved_tasks(client):
    inbox.ensure()
    _mk_task(client, "p_inbox", name="随手记1")
    _mk_task(client, "p_inbox", name="随手记2")

    body = client.get(REVIEW).json()
    assert body["inboxPendingCount"] == 2


def test_inbox_pending_count_decreases_after_moving_task_out(client):
    inbox.ensure()
    task = _mk_task(client, "p_inbox", name="待归类")
    zone = _mk_zone(client)
    project = _mk_project(client, zone["id"])
    client.patch(f"{PLANNER}/tasks/{task['id']}", json={"projectId": project["id"]})

    body = client.get(REVIEW).json()
    assert body["inboxPendingCount"] == 0


def test_inbox_pending_count_zero_without_seeding(client):
    """没跑过 `inbox.ensure()` 种子时，库里根本没有 p_inbox，计数应为 0
    （不因缺失容器而报错）。"""
    body = client.get(REVIEW).json()
    assert body["inboxPendingCount"] == 0


# --------------------------------------------------------------- 只读


def test_review_is_read_only(client):
    zone = _mk_zone(client)
    project = _mk_project(client, zone["id"])
    _mk_task(client, project["id"])
    from app.repo import get_db  # noqa: PLC0415

    collections = ("zones", "projects", "tasks", "events", "proj_daily_stats")
    before = {c: get_db()[c].count_documents({}) for c in collections}
    client.get(REVIEW)
    client.get(REVIEW)
    after = {c: get_db()[c].count_documents({}) for c in collections}
    assert before == after
