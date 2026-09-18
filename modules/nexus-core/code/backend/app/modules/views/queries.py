"""读取逻辑（真实现）。**读取口径住在这一层，不住 router。**

数据来源（红线 §7.3 第 2/4 条）：
- 活状态：``timer/service.py`` 公开函数（跨子边界只准调 service）。
- 累计时长：``projector/handlers/current.py`` 的 ``read_current``——
  rules.md §7.4 钉死的公开读路径；views **不读 events、不碰任何 repo**。
- 按天时长（甘特「事实」图层）：``projector/handlers/daily_stats.py`` 的
  ``read_daily_stats``，同一条红线的另一个指定读路径。
- 显示名：``planner/service.py`` 按 ID 现查（显示名不进事件）。

占比口径（contract.md「占比语义」，规范性）：
- **0–100 百分数**，允许一位小数——不是 0–1 小数。
- ``shareOfPlan``   = 该项目累计秒 / 全部项目累计秒 × 100（按事件聚合的实际值）。
- ``shareOfProject`` = 该任务累计秒 / 该项目累计秒 × 100。
- 空闲态四字段全 ``null``，**不是 0**——0 会被圆环画成真实存在的零弧。
"""

from __future__ import annotations

from datetime import datetime

from ...config import LOCAL_USER, settings
from ...timeutil import local_date
from ..planner import service as planner_service
from ..projector.handlers import current as current_projection
from ..projector.handlers import daily_stats as daily_stats_projection
from ..timer import service as timer_service
from .schemas import (
    CurrentOut,
    CurrentProject,
    CurrentTask,
    CurrentZone,
    GanttOut,
    TreeOut,
)

EPHEMERAL_KIND = "ephemeral"

_IDLE = {"running": False, "zone": None, "project": None, "task": None, "sessionStartAt": None}


def _share(part: int, whole: int) -> float:
    """0–100 一位小数；分母为零时 0.0（有 running 任务但还没有任何完成会话）。"""
    if whole <= 0:
        return 0.0
    return round(min(part / whole, 1.0) * 100, 1)


def get_current() -> CurrentOut:
    state = timer_service.get_running_state(LOCAL_USER)
    if state is None:
        return CurrentOut(**_IDLE)

    task_doc = planner_service.get_task(state["taskId"])
    project_doc = (
        planner_service.get_project(task_doc["projectId"])
        if task_doc and task_doc.get("projectId")
        else None
    )
    zone_doc = (
        planner_service.get_zone(project_doc["zoneId"])
        if project_doc and project_doc.get("zoneId")
        else None
    )

    projection = current_projection.read_current(LOCAL_USER) or {}
    project_totals: dict[str, int] = projection.get("projects", {})
    task_totals: dict[str, int] = projection.get("tasks", {})
    all_seconds = sum(project_totals.values())
    project_seconds = int(project_totals.get(project_doc["id"], 0)) if project_doc else 0
    task_seconds = int(task_totals.get(state["taskId"], 0))

    return CurrentOut(
        running=True,
        zone=CurrentZone(id=zone_doc["id"], key=zone_doc["key"], name=zone_doc["name"])
        if zone_doc
        else None,
        project=CurrentProject(
            id=project_doc["id"],
            key=project_doc["key"],
            name=project_doc["name"],
            totalSeconds=project_seconds,
            shareOfPlan=_share(project_seconds, all_seconds),
        )
        if project_doc
        else None,
        task=CurrentTask(
            id=task_doc["id"],
            key=task_doc["key"],
            name=task_doc["name"],
            totalSeconds=task_seconds,
            shareOfProject=_share(task_seconds, project_seconds),
        )
        if task_doc
        else None,
        sessionStartAt=state["startAt"],
    )


def _progress(project: dict, tasks: list[dict]) -> int:
    """``manual`` 原样返回；``computed`` 按**全量任务**（含 ephemeral）算——
    过滤后重算会让进度随查询参数漂移，那是数据说谎（F2 的语义）。"""
    if project.get("progressSource") == "manual":
        return int(project.get("progress", 0))
    if not tasks:
        return 0
    done = sum(1 for t in tasks if t.get("done") is True)
    return round(done / len(tasks) * 100)


def get_tree(include_ephemeral: bool = False) -> TreeOut:
    zones, projects = planner_service.list_tree()
    shaped = []
    for project in projects:
        all_tasks = project.get("tasks", [])
        visible = (
            all_tasks
            if include_ephemeral
            else [t for t in all_tasks if t.get("kind") != EPHEMERAL_KIND]
        )
        plan = project.get("plan") or {}
        shaped.append(
            {
                "id": project["id"],
                "key": project["key"],
                "zoneId": project["zoneId"],
                "name": project["name"],
                "status": project.get("status", "active"),
                "progress": _progress(project, all_tasks),
                "progressSource": project.get("progressSource", "computed"),
                # deadline = plan.end 的投影（契约）；无计划则回落到旧存量字段
                "deadline": plan.get("end") or project.get("deadline"),
                "tasks": [
                    {
                        "id": t["id"],
                        "key": t["key"],
                        "name": t["name"],
                        "done": bool(t.get("done")),
                        "kind": t.get("kind", "normal"),
                        "flags": t.get("flags", []),
                        # v1.2：只读附带，与 TaskOut/GanttTask 同形状；老文档缺字段时
                        # 落到 None/[]（同一份默认值口径，见契约「排期与依赖」）。
                        "plan": t.get("plan"),
                        "dependsOn": t.get("dependsOn") or [],
                    }
                    for t in visible
                ],
            }
        )
    return TreeOut(
        zones=[
            {"id": z["id"], "key": z["key"], "name": z["name"],
             "color": z["color"], "order": z["order"]}
            for z in zones
        ],
        projects=shaped,
    )


def _today() -> str:
    """服务端的今天（契约「日界与时区」v0.9 + 「甘特读端」G8）：**不用客户端时钟**，
    用 ``NEXUS_TZ`` 下的日期——与 ``proj_daily_stats`` 的归日（``daily_stats.py``）
    共用 ``timeutil.local_date``，两处不许各算一次，否则迟早漂。"""
    return local_date(datetime.now(settings.tz), settings.tz)


def get_gantt(date_from: str | None = None, date_to: str | None = None) -> GanttOut:
    """``GET /api/core/views/gantt``：计划图层（读 planner）与事实图层
    （读 ``proj_daily_stats``）叠加。**纯只读**（G9）——不写任何东西，
    甘特拖拽改期走已有的 ``PATCH /api/core/planner/projects/{id}``（项目）与
    ``PATCH /api/core/planner/tasks/{id}``（任务，v1.1）。

    返回**全部**项目（不按 status/zone 过滤，与 ``get_tree`` 同口径）——
    没有计划、没有事实的项目也要出现在时间轴上（空 plan + 空 actual），
    不然用户新建的项目要等第一次计时后才「冒出来」。

    v1.1（O1）：项目层之下叠一层任务（``tasks[]``）。**不是新投影**——
    ``proj_daily_stats`` 从 v0.8 起就按 ``(user,date,projectId,taskId)`` 存，
    这里只是把此前被按 ``projectId`` 求和丢掉的 ``taskId`` 维度重新读出来
    （契约「甘特读端」O1 节）。项目层 ``actual`` 仍是全项目当天总量（含无具体
    任务的事件），任务层 ``actual`` 是按 ``(date, taskId)`` 分组，两者互不冲突。
    """
    projects = planner_service.list_projects()
    daily_rows = daily_stats_projection.read_daily_stats(
        LOCAL_USER, date_from=date_from, date_to=date_to
    )

    # 项目层：按 projectId 分组、同一天多条（不同 taskId）求和——契约「甘特读端」
    # 的项目层 JSON 示例就是这个形状，含没有具体任务的事件（B5）。
    per_project_by_date: dict[str, dict[str, int]] = {}
    # 任务层（v1.1，O1）：额外按 (projectId, taskId) 分组，taskId 为 None 的行
    # 不属于任何具体任务，不进任务层（但仍计入上面的项目层总量）。
    per_task_by_date: dict[tuple[str, str], dict[str, int]] = {}
    for row in daily_rows:
        by_date = per_project_by_date.setdefault(row["projectId"], {})
        by_date[row["date"]] = by_date.get(row["date"], 0) + row["seconds"]
        task_id = row.get("taskId")
        if task_id:
            task_key = (row["projectId"], task_id)
            task_by_date = per_task_by_date.setdefault(task_key, {})
            task_by_date[row["date"]] = task_by_date.get(row["date"], 0) + row["seconds"]

    shaped = []
    for project in projects:
        by_date = per_project_by_date.get(project["id"], {})
        tasks = planner_service.list_tasks(project["id"])
        shaped_tasks = []
        for task in tasks:
            task_by_date = per_task_by_date.get((project["id"], task["id"]), {})
            shaped_tasks.append(
                {
                    "id": task["id"],
                    "key": task["key"],
                    "name": task["name"],
                    "done": bool(task.get("done")),
                    "plan": task.get("plan"),
                    "dependsOn": task.get("dependsOn") or [],
                    "actual": [
                        {"date": date, "seconds": seconds}
                        for date, seconds in sorted(task_by_date.items())
                    ],
                }
            )
        shaped.append(
            {
                "id": project["id"],
                "key": project["key"],
                "name": project["name"],
                "plan": project.get("plan"),
                "actual": [
                    {"date": date, "seconds": seconds}
                    for date, seconds in sorted(by_date.items())
                ],
                "tasks": shaped_tasks,
            }
        )

    return GanttOut(projects=shaped, today=_today())
