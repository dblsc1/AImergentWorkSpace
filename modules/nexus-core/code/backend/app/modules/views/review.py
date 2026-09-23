"""GTD 每周回顾聚合读端（contract.md v1.5「每周回顾读端」，PRD F-REVIEW-1）。

**全部是现有 events/planner/投影的重新聚合，零新集合、零新投影**——四块
逐块只调既有只读函数：`planner.list_projects`/`list_tasks`（状态）+
`projector.handlers.daily_stats.read_daily_stats`（按天时长，同 `views/queries.py`
的公开读路径，rules.md §7.4 钉死）。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from ...config import settings
from ...tenant import current as current_tenant
from ...timeutil import local_date
from ..planner import service as planner_service
from ..projector.handlers import daily_stats as daily_stats_projection
from .schemas import ReviewOut, ReviewOverdueProject, ReviewPlanVsActual, ReviewStaleTask

#: 久未动 = 未完成且最近这么多天内没有任何计时（含从未计时过）。**经验阈值，
#: 非契约硬约束**（同 PRD O1 的开放口径）——用户试用后可调，暂定 14 天。
STALE_TASK_DAYS = 14


def _today() -> str:
    return local_date(datetime.now(settings.tz), settings.tz)


def _week_bounds(today: str) -> tuple[str, str]:
    """本周 = ISO 周一到周日（服务端归日下"今天"所在的那一周）。"""
    d = datetime.strptime(today, "%Y-%m-%d").date()
    monday = d - timedelta(days=d.weekday())
    return monday.isoformat(), (monday + timedelta(days=6)).isoformat()


def _plans_overlap(plan: dict | None, start: str, end: str) -> bool:
    if not plan or not plan.get("start") or not plan.get("end"):
        return False
    return not (plan["end"] < start or plan["start"] > end)


def _last_active_by_task() -> dict[str, str]:
    """全量按天投影里，每个 taskId 最近一次出现的日期。复用全量读取
    （不限日期范围），避免另开一条按 taskId 查询的路径——同「甘特读端」
    O1 节的判断标准：这份数据已经在 `proj_daily_stats` 里，不必新加口子。
    """
    last_active: dict[str, str] = {}
    for row in daily_stats_projection.read_daily_stats(current_tenant()):
        task_id = row.get("taskId")
        if not task_id:
            continue
        if task_id not in last_active or row["date"] > last_active[task_id]:
            last_active[task_id] = row["date"]
    return last_active


def get_review() -> ReviewOut:
    today = _today()
    week_start, week_end = _week_bounds(today)

    projects = planner_service.list_projects()
    tasks = planner_service.list_tasks()
    week_rows = daily_stats_projection.read_daily_stats(
        current_tenant(), date_from=week_start, date_to=week_end
    )

    actual_by_project: dict[str, int] = {}
    for row in week_rows:
        actual_by_project[row["projectId"]] = actual_by_project.get(row["projectId"], 0) + row["seconds"]

    plan_vs_actual = [
        ReviewPlanVsActual(
            projectId=p["id"], key=p["key"], name=p["name"],
            plan=p.get("plan"),
            scheduledThisWeek=_plans_overlap(p.get("plan"), week_start, week_end),
            actualSecondsThisWeek=actual_by_project.get(p["id"], 0),
        )
        for p in projects
    ]

    overdue_projects = [
        ReviewOverdueProject(
            id=p["id"], key=p["key"], name=p["name"], plan=p["plan"],
            status=p.get("status", "active"),
        )
        for p in projects
        if p.get("status", "active") == "active"
        and p.get("plan") and p["plan"].get("end") and p["plan"]["end"] < today
    ]

    last_active = _last_active_by_task()
    stale_cutoff = (
        datetime.strptime(today, "%Y-%m-%d").date() - timedelta(days=STALE_TASK_DAYS)
    ).isoformat()
    stale_tasks = [
        ReviewStaleTask(
            id=t["id"], key=t["key"], name=t["name"], projectId=t["projectId"],
            lastActiveDate=last_active.get(t["id"]),
        )
        for t in tasks
        if not t.get("done")
        and (last_active.get(t["id"]) is None or last_active[t["id"]] < stale_cutoff)
    ]

    inbox_pending_count = sum(
        1 for t in tasks if t.get("projectId") == planner_service.INBOX_PROJECT_ID
    )

    return ReviewOut(
        today=today, weekStart=week_start, weekEnd=week_end,
        planVsActual=plan_vs_actual,
        overdueProjects=overdue_projects,
        staleTasks=stale_tasks,
        inboxPendingCount=inbox_pending_count,
    )
