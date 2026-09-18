"""GTD 待办区规则读端（contract.md v1.5「下一步行动读端」，PRD F-TODO-1）。

**离线确定，不依赖 LLM**（PRD G3）：全部输入是 planner 现有字段
（``done``/``dependsOn``/``plan``/``plannedWeight``/``zoneId``），零新集合、
零新投影——只读 `planner/service.py` 的公开函数，不碰 `events`/`timer`。

## 环防御（A8d，硬约束）

写入时的成环校验（`planner/deps.py::validate_depends_on`）只保证"这次写入
之后"图无环，**不保证历史数据**——那道校验是切片 2 中途才加的（v1.1），
更早写入的任务、或任何绕过 API 直接写库的数据都可能带环。本模块在遍历
依赖图前先做一次**图级环检测**（标准三色 DFS，保证终止）+ 硬深度上限兜底，
任何位于环上的未完成任务一律降级为"可做"并打 ``cycleWarning``——不是崩溃、
不是 500、不是挂起。
"""

from __future__ import annotations

from datetime import datetime

from ...config import settings
from ...timeutil import local_date
from ..planner import service as planner_service
from .schemas import NextActionBlocker, NextActionsOut, NextActionTask, NextActionZone

#: 深度上限兜底（防御性护栏）：个人任务管理场景下真实依赖链深度远小于此；
#: 触发只可能是数据本身异常（环或异常长链），此时保守地把当前路径整段判为
#: "环"处理，绝不继续递归——这是"绝不死循环崩服务"的最后一道闸，不是预期路径。
_MAX_DEPTH = 500


def _today() -> str:
    return local_date(datetime.now(settings.tz), settings.tz)


def _tasks_in_cycle(graph: dict[str, list[str]]) -> set[str]:
    """标准三色 DFS：返回位于任意环上的全部节点 id。``O(点数+边数)``，
    每个节点最多进出一次栈，**保证终止**——即便撞上深度上限也只是把当前
    路径判为可疑环退出这条分支，不会无限递归。悬空引用（``dependsOn``
    指向不存在的任务）不算图的一部分，直接跳过。
    """
    UNVISITED, VISITING, DONE = 0, 1, 2
    state: dict[str, int] = {n: UNVISITED for n in graph}
    in_cycle: set[str] = set()

    for origin in graph:
        if state[origin] != UNVISITED:
            continue
        state[origin] = VISITING
        stack = [origin]
        idx_stack = [0]
        while stack:
            if len(stack) > _MAX_DEPTH:
                in_cycle.update(stack)  # 深度上限兜底：整条路径判为可疑环
                state[stack.pop()] = DONE
                idx_stack.pop()
                continue
            node = stack[-1]
            neighbors = graph.get(node, ())
            i = idx_stack[-1]
            if i >= len(neighbors):
                state[stack.pop()] = DONE
                idx_stack.pop()
                continue
            idx_stack[-1] += 1
            nxt = neighbors[i]
            if nxt not in graph:
                continue  # 悬空引用，不算环的一部分
            st = state.get(nxt, UNVISITED)
            if st == VISITING:  # 回边：栈上从 nxt 到 node 的这一段就是环
                in_cycle.update(stack[stack.index(nxt):])
            elif st == UNVISITED:
                state[nxt] = VISITING
                stack.append(nxt)
                idx_stack.append(0)
    return in_cycle


def get_next_actions() -> NextActionsOut:
    zones = {z["id"]: z for z in planner_service.list_zones()}
    projects = {p["id"]: p for p in planner_service.list_projects()}
    tasks = planner_service.list_tasks()
    tasks_by_id = {t["id"]: t for t in tasks}

    graph = {t["id"]: list(t.get("dependsOn") or []) for t in tasks}
    in_cycle = _tasks_in_cycle(graph)

    today = _today()
    by_zone: dict[str, dict[str, list[NextActionTask]]] = {}

    for task in tasks:
        if task.get("done"):
            continue
        project = projects.get(task.get("projectId"))
        if project is None:
            continue  # 数据完整性假设被破坏（不应发生），防御性跳过而不是炸
        zone = zones.get(project.get("zoneId"))
        if zone is None:
            continue

        depends_on = list(task.get("dependsOn") or [])
        plan = task.get("plan")
        plan_end = plan.get("end") if plan else None
        overdue = bool(plan_end and plan_end < today)
        due_today = bool(plan_end and plan_end == today)
        cycle_warning = task["id"] in in_cycle

        blockers: list[dict] = []
        if not cycle_warning:
            # 环上的"前置"关系本身已不可信，不展示可能误导的阻塞信息（恒 []）。
            for dep_id in depends_on:
                dep = tasks_by_id.get(dep_id)
                if dep is not None and not dep.get("done"):
                    blockers.append(dep)
        waiting = bool(blockers)  # cycle_warning 时 blockers 恒空 → 必落 actionable

        item = NextActionTask(
            id=task["id"], key=task["key"], name=task["name"],
            projectId=project["id"], projectName=project["name"],
            plannedWeight=task.get("plannedWeight", 0.0),
            plan=plan, dependsOn=depends_on,
            overdue=overdue, dueToday=due_today, cycleWarning=cycle_warning,
            blockedBy=[
                NextActionBlocker(id=b["id"], key=b["key"], name=b["name"])
                for b in blockers
            ],
        )
        bucket = "waiting" if waiting else "actionable"
        by_zone.setdefault(zone["id"], {"actionable": [], "waiting": []})[bucket].append(item)

    def _sort_key(item: NextActionTask) -> tuple[int, float, str]:
        urgent = item.overdue or item.dueToday
        return (0 if urgent else 1, -item.plannedWeight, item.key)

    shaped_zones = [
        NextActionZone(
            id=zones[zone_id]["id"], key=zones[zone_id]["key"], name=zones[zone_id]["name"],
            actionable=sorted(buckets["actionable"], key=_sort_key),
            waiting=sorted(buckets["waiting"], key=_sort_key),
        )
        for zone_id, buckets in by_zone.items()
    ]
    shaped_zones.sort(key=lambda z: zones[z.id]["order"])

    return NextActionsOut(today=today, zones=shaped_zones)
