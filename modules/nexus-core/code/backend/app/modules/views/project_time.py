"""``GET /api/core/views/project-time``（契约 v2.48）：一个项目、一段日期里，人与代理在每个任务上的**时间区间**。

只读、不写。人 = 已入账的计时段 + 在计时（到此刻）；代理 = 运行里 working 相位的区间（同 ``timer.agent_working``，
即 ``agent-time`` 的干活口径）。按任务分组，``taskId: null`` = 挂在项目上、没挂任务的时间。区间裁到窗口（且不晚于此刻）。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from ...config import settings
from ...tenant import current as current_tenant
from ..planner.errors import UnprocessableError
from ..projector.handlers import lanes as lanes_projection
from ..timer import service as timer_service
from ..timer.agent_working import working_intervals
from . import lanes as lanes_view
from .queries import _today
from .schemas import ProjectTimeOut

MAX_ROWS = 5000


def _iso(moment: datetime) -> str:
    return moment.astimezone(settings.tz).isoformat()


def _pieces(run: dict, stop: datetime) -> list[tuple[datetime, datetime, str | None, str | None]]:
    """运行按去向切成的 [(起, 止, 项目, 任务)]（v2.48 只算以后）：已结束的看 ``segments``，在跑的看 ``cuts``
    （每条 = 那个去向算到 at 为止），都没有 = 整条一个去向。"""
    if run.get("segments"):
        return [(datetime.fromisoformat(s["startAt"]), datetime.fromisoformat(s["endAt"]), s["projectId"], s.get("taskId"))
                for s in run["segments"]]
    out, since = [], run["startTs"]
    for cut in run.get("cuts") or []:
        at = datetime.fromisoformat(cut["at"])
        out.append((since, at, cut["projectId"], cut.get("taskId")))
        since = at
    return [*out, (since, max(stop, since), run.get("projectId"), run.get("taskId"))]


def get_project_time(project_id: str, date_from: str | None, date_to: str | None) -> ProjectTimeOut:
    user = current_tenant()
    first, last, start, end = lanes_view._span(None, date_from, date_to)
    if first > last:
        raise UnprocessableError("from 晚于 to")
    now, open_runs = timer_service.list_lane_runs(user)
    names = timer_service.agent_name_rules(user)
    rows: dict[str | None, dict] = {}

    def row(task_id: str | None) -> dict:
        return rows.setdefault(task_id, {"taskId": task_id, "human": [], "agent": []})

    def clip(a: datetime, b: datetime) -> tuple[datetime, datetime] | None:
        a, b = max(a, start), min(b, end, now)
        return (a, b) if b > a else None

    for s in lanes_projection.read_lanes(user, "session", start, end, MAX_ROWS):
        if s.get("projectId") == project_id and (c := clip(s["startAt"], s["endAt"])):
            row(s.get("taskId"))["human"].append({"start": _iso(c[0]), "end": _iso(c[1])})
    state = timer_service.get_running_state(user)
    if state and state.get("projectId") == project_id:
        if c := clip(datetime.fromisoformat(state["startAt"]), now):
            row(state.get("taskId"))["human"].append({"start": _iso(c[0]), "end": _iso(c[1])})

    runs = [{**r, "startTs": r["startAt"], "endTs": r["endAt"]} for r in lanes_projection.read_lanes(user, "run", start, end, MAX_ROWS)]
    seen = {r["runId"] for r in runs}
    runs += [r for r in open_runs if r["runId"] not in seen]
    for r in runs:
        # 同 views/agent-time：在跑的算到 起点 + elapsedSeconds（失联止于最后一次信号，封顶在上限）
        stop = r["endTs"] or r["startTs"] + timedelta(seconds=r.get("elapsedSeconds") or 0)
        name = names.get(r.get("agent")) or r.get("label") or r.get("agent")
        work = working_intervals(r.get("phases"), r["startTs"], stop)
        for p_from, p_to, p_project, p_task in _pieces(r, stop):
            if p_project != project_id:
                continue
            for a, b in work:
                if (c := clip(max(a, p_from), min(b, p_to))):
                    row(p_task)["agent"].append({"start": _iso(c[0]), "end": _iso(c[1]), "agent": name, "runId": r["runId"]})

    tasks = sorted(rows.values(), key=lambda t: (t["taskId"] is not None, t["taskId"] or ""))
    for t in tasks:
        t["human"].sort(key=lambda i: i["start"])
        t["agent"].sort(key=lambda i: i["start"])
    return ProjectTimeOut(today=_today(), now=_iso(now), windowStart=_iso(start), windowEnd=_iso(end),
                          projectId=project_id, tasks=tasks)
