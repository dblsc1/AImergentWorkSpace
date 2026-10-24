"""``GET /api/core/views/arrangements``（契约 v2.39「MCP v0.5 的后端」三，唯一事实源）：排好的、能做的、卡住的、过期的、久没动的，及谁在做。**只读**。

复用：待办判据与环防御 ``next_actions._tasks_in_cycle``、久未动的最后有时间日 ``review._last_active_by_task``、周界与周回顾 ``review``、
时间 ``proj_daily_stats``、代理干活秒数与在跑运行 ``collab_runs``、编号 ``address``。
"""

from __future__ import annotations

from datetime import date as Date, timedelta

from ...config import settings
from ...tenant import current as current_tenant
from ..activity import service as activity_service
from ..planner import service as planner_service
from ..planner.errors import UnprocessableError
from ..projector.handlers import daily_stats as daily_stats_projection
from ..timer import service as timer_service
from . import address as address_mod
from . import collab_runs as cr
from . import next_actions, review
from .queries import _today

RANGES = ("today", "week")
CAP = 200
STALE_DAYS = 14  # 与 views/review 的 STALE_TASK_DAYS 同值；这里是「≥ 14 天」（契约「14 天以上」），review 是「> 14 天」


def _plan(task: dict) -> dict | None:
    plan = task.get("plan") or {}
    start, end = plan.get("start"), plan.get("end")
    start, end = start or end, end or start  # 只有 start（或只有 end）时按 end = start
    return {"start": start, "end": end} if start else None


def _created(doc_id: str, created: dict) -> Date | None:
    when = getattr(created.get("tasks", {}).get(doc_id), "generation_time", None)
    return when.astimezone(settings.tz).date() if when else None


def get_arrangements(range_: str, scope: str | None) -> dict:
    if range_ not in RANGES:
        raise UnprocessableError(f"参数 range 取值须为 {' | '.join(RANGES)}：{range_!r}")
    user = current_tenant()
    today = _today()
    first, last = (today, today) if range_ == "today" else review._week_bounds(today)  # noqa: SLF001
    start, end = cr.midnight(Date.fromisoformat(first)), cr.midnight(Date.fromisoformat(last) + timedelta(days=1))

    zones, projects = planner_service.list_zones(), planner_service.list_projects()
    all_tasks = planner_service.list_tasks()
    created = planner_service.creation_order()
    addrs = address_mod.build(zones, projects, all_tasks, created)
    by_id = {t["id"]: t for t in all_tasks}
    zone_rank = {z["id"]: i for i, z in enumerate(zones)}
    proj = {p["id"]: p for p in projects}
    if scope is not None and scope not in {x["id"] for x in (*zones, *projects)}:
        raise UnprocessableError(f"参数 scope 不是已有的分区 / 项目 id：{scope!r}")
    # 「树里的顺序」：分区 order → 项目在 planner 列表里的先后 → 任务在 planner 列表里的先后（与 views/tree 同一份列表）
    proj_rank = {p["id"]: (zone_rank.get(p.get("zoneId"), len(zones)), i) for i, p in enumerate(projects)}
    task_rank = {t["id"]: i for i, t in enumerate(all_tasks)}
    in_cycle = next_actions._tasks_in_cycle({t["id"]: list(t.get("dependsOn") or []) for t in all_tasks})  # noqa: SLF001

    tasks = [t for t in all_tasks
             if not t.get("done") and t.get("kind", "normal") == "normal"
             and (p := proj.get(t.get("projectId"))) is not None and p.get("status", "active") != "archived"
             and (scope is None or scope in (p["id"], p.get("zoneId")))]
    tasks.sort(key=lambda t: (proj_rank[t["projectId"]], task_rank[t["id"]]))

    human = {}
    for row in daily_stats_projection.read_daily_stats(user, first, last):
        if row.get("taskId"):
            human[row["taskId"]] = human.get(row["taskId"], 0) + row["seconds"]
    _now, runs, _over = cr.load(start, end)
    agent: dict[str, float] = {}
    live: dict[str, list[str]] = {}
    for run in runs:
        if not run["taskId"]:
            continue
        agent[run["taskId"]] = agent.get(run["taskId"], 0.0) + sum(
            cr.seconds([c]) for a, b in cr.working(run) if (c := cr.clip(a, b, start, end)))
        if run["open"] and run["endAt"] is None and not run["lost"] and not run["overdue"]:
            live.setdefault(run["taskId"], []).append(run["runId"])
    mine = timer_service.get_running_state(user)
    auto = activity_service.auto_state(user, mine is not None)["auto"]
    human_now = {x for x in ((mine or {}).get("taskId"), (auto or {}).get("taskId")) if x}

    def base(t: dict) -> dict:
        return {"taskId": t["id"], "projectId": t["projectId"], "address": addrs.tasks[t["id"]], "name": t["name"]}

    def extra(t: dict) -> dict:
        return {"spentThisRange": {"humanSeconds": int(human.get(t["id"], 0)),
                                   "agentWorkingSeconds": round(agent.get(t["id"], 0.0))},
                "activeNow": (["human"] if t["id"] in human_now else []) + sorted(live.get(t["id"], []))}

    groups: dict[str, list] = {"overdue": [], "blocked": [], "scheduled": [], "actionable": []}
    for t in tasks:
        plan = _plan(t)
        blockers = [] if t["id"] in in_cycle else [by_id[d] for d in t.get("dependsOn") or [] if d in by_id and not by_id[d].get("done")]
        if plan and plan["end"] < today:
            groups["overdue"].append({**base(t), "plan": plan, "planEnd": plan["end"],
                                      **({"blockedBy": [addrs.tasks[b["id"]] for b in blockers]} if blockers else {}), **extra(t)})
        elif blockers:
            groups["blocked"].append({**base(t), "plan": plan, "blockedBy": [addrs.tasks[b["id"]] for b in blockers], **extra(t)})
        elif plan and plan["start"] <= last and plan["end"] >= first:
            groups["scheduled"].append({**base(t), "plan": plan, **extra(t)})
        else:
            groups["actionable"].append({**base(t), "plan": plan, **extra(t)})  # 契约待定：有排期但不在范围内的，plan 照给（示例里是 null）

    def by_start(x: dict) -> tuple:
        return (x["plan"] is None, x["plan"]["start"] if x["plan"] else "")

    groups["overdue"].sort(key=lambda x: x["planEnd"])
    groups["scheduled"].sort(key=by_start)
    groups["blocked"].sort(key=by_start)

    last_active = review._last_active_by_task()  # noqa: SLF001 —— 一次 $group，下面的 week 回顾共用
    cutoff = (Date.fromisoformat(today) - timedelta(days=STALE_DAYS)).isoformat()
    stale = []
    for t in tasks:
        if proj[t["projectId"]].get("status", "active") != "active":
            continue
        seen = last_active.get(t["id"])
        if seen is not None:
            old = seen <= cutoff
        else:  # 从没有时间：任务建了 14 天以上才算（创建时间取 Mongo _id 的时间；契约待定：文档没存 createdAt）
            day = _created(t["id"], created)
            old = day is not None and day.isoformat() <= cutoff
        if old:
            stale.append({**base(t), "lastActive": seen})
    stale.sort(key=lambda x: (x["lastActive"] is not None, x["lastActive"] or ""))

    out = {"today": today, "range": range_, "from": first, "to": last}
    truncated = False
    for name in ("scheduled", "actionable", "blocked", "overdue"):
        truncated |= len(groups[name]) > CAP
        out[name] = groups[name][:CAP]
    truncated |= len(stale) > CAP
    out["stale"] = stale[:CAP]
    if range_ == "week":
        out["review"] = review.get_review(last_active).model_dump()
    out["truncated"] = truncated
    return out
