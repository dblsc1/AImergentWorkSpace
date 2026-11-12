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
from .lane_cap import lane_key
from .lane_order import phase_of
from .queries import _today
from .schemas import ProjectTimeOut, TimeLanesOut

MAX_ROWS = 5000
MAX_SINCE_DAYS = 8  #: ``since`` 到此刻至多这么久（滚动 7 天 + 零头）


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


def _clip(a: datetime, b: datetime, start: datetime, end: datetime, now: datetime) -> tuple[datetime, datetime] | None:
    a, b = max(a, start), min(b, end, now)
    return (a, b) if b > a else None


def _since_window(since: str, now: datetime) -> tuple[datetime, datetime]:
    """v2.54 滚动窗口：``since``（带偏移的 ISO 时刻，不带偏移按 NEXUS_TZ）→ (起, 止)；止 = 此刻后一天（只是读库的上界，区间仍裁到此刻）。"""
    try:
        start = datetime.fromisoformat(since.replace("Z", "+00:00"))
    except ValueError:
        raise UnprocessableError(f"since 不是时刻：{since!r}") from None
    if start.tzinfo is None:
        start = start.replace(tzinfo=settings.tz)
    if now - start > timedelta(days=MAX_SINCE_DAYS):
        raise UnprocessableError(f"since 距现在超过 {MAX_SINCE_DAYS} 天")
    return start, now + timedelta(days=1)


def collect(project_id: str | None, date_from: str | None, date_to: str | None, since: str | None = None):
    """人 / 代理的区间平铺（project-time 与 time-lanes 共用）。``project_id`` 给了只留该项目，``None`` = 全部（含没去向的）。
    返回 ``(now, start, end, humans, agents, runs)``：humans = ``[(项目, 任务, 起, 止)]``，
    agents = ``[(项目, 任务, 名字, 代理键, runId, 起, 止)]``，都已裁到窗口且不晚于此刻；runs = ``{runId: 运行}``（time-lanes 要它的身份、相位与 attend）。
    ``since`` 给了就用滚动窗口 [since, 此刻]，不能同时给 from / to。"""
    user = current_tenant()
    now, open_runs = timer_service.list_lane_runs(user)
    if since:
        if date_from or date_to:
            raise UnprocessableError("since 不能与 from / to 同时给")
        start, end = _since_window(since, now)
    else:
        first, last, start, end = lanes_view._span(None, date_from, date_to)
        if first > last:
            raise UnprocessableError("from 晚于 to")
    names = timer_service.agent_name_rules(user)
    humans: list[tuple] = []
    agents: list[tuple] = []

    def want(pid: str | None) -> bool:
        return project_id is None or pid == project_id

    def clip(a: datetime, b: datetime) -> tuple[datetime, datetime] | None:
        return _clip(a, b, start, end, now)

    for s in lanes_projection.read_lanes(user, "session", start, end, MAX_ROWS):
        if want(s.get("projectId")) and (c := clip(s["startAt"], s["endAt"])):
            humans.append((s.get("projectId"), s.get("taskId"), *c))
    state = timer_service.get_running_state(user)
    if state and want(state.get("projectId")):
        if c := clip(datetime.fromisoformat(state["startAt"]), now):
            humans.append((state.get("projectId"), state.get("taskId"), *c))

    runs = [{**r, "startTs": r["startAt"], "endTs": r["endAt"]} for r in lanes_projection.read_lanes(user, "run", start, end, MAX_ROWS)]
    seen = {r["runId"] for r in runs}
    runs += [r for r in open_runs if r["runId"] not in seen]
    for r in runs:
        # 同 views/agent-time：在跑的算到 起点 + elapsedSeconds（失联止于最后一次信号，封顶在上限）
        stop = r["endTs"] or r["startTs"] + timedelta(seconds=r.get("elapsedSeconds") or 0)
        name = names.get(r.get("agent")) or r.get("label") or r.get("agent")
        work = working_intervals(r.get("phases"), r["startTs"], stop)
        for p_from, p_to, p_project, p_task in _pieces(r, stop):
            if not want(p_project):
                continue
            for a, b in work:
                if (c := clip(max(a, p_from), min(b, p_to))):
                    agents.append((p_project, p_task, name, r.get("agent"), r["runId"], *c))
    return now, start, end, humans, agents, {r["runId"]: r for r in runs}


def get_project_time(project_id: str, date_from: str | None, date_to: str | None) -> ProjectTimeOut:
    now, start, end, humans, agents, _runs = collect(project_id, date_from, date_to)
    rows: dict[str | None, dict] = {}

    def row(task_id: str | None) -> dict:
        return rows.setdefault(task_id, {"taskId": task_id, "human": [], "agent": []})

    for _p, task, a, b in humans:
        row(task)["human"].append({"start": _iso(a), "end": _iso(b)})
    for _p, task, name, _key, run_id, a, b in agents:
        row(task)["agent"].append({"start": _iso(a), "end": _iso(b), "agent": name, "runId": run_id})
    tasks = sorted(rows.values(), key=lambda t: (t["taskId"] is not None, t["taskId"] or ""))
    for t in tasks:
        t["human"].sort(key=lambda i: i["start"])
        t["agent"].sort(key=lambda i: i["start"])
    return ProjectTimeOut(today=_today(), now=_iso(now), windowStart=_iso(start), windowEnd=_iso(end),
                          projectId=project_id, tasks=tasks)


def get_time_lanes(date_from: str | None, date_to: str | None, since: str | None = None) -> TimeLanesOut:
    """``GET /api/core/views/time-lanes``（v2.50）：全部项目的人 / 代理区间，代理一条运行一道泳道。v2.54 追加 ``since`` 与每条运行的身份 / 相位 / attend。"""
    now, start, end, humans, agents, by_id = collect(None, date_from, date_to, since)
    runs: dict[str, dict] = {}

    def lane_of(run_id: str, name: str | None, key: str | None) -> dict:
        r = by_id[run_id]
        return runs.setdefault(run_id, {
            "agent": key, "name": name, "runId": run_id, "segments": [],
            "tool": r.get("tool"), "laneKey": lane_key(r), "projectId": r.get("projectId"),
            "running": r["endTs"] is None, "lost": bool(r.get("lost")), "phase": phase_of({"phases": r.get("phases") or []}),
            "attend": [{"start": _iso(c[0]), "end": _iso(c[1])} for i in r.get("interactions") or []
                       if i.get("kind") == "attend" and (c := _clip(datetime.fromisoformat(i["at"]), datetime.fromisoformat(i["until"]), start, end, now))]})

    for pid, task, name, key, run_id, a, b in sorted(agents, key=lambda x: x[5]):
        lane_of(run_id, name, key)["segments"].append({"start": _iso(a), "end": _iso(b), "projectId": pid, "taskId": task})
    # v2.57：在跑的运行一定列出：干活区间落在窗口外的（超过遗忘上限的、窗口里一直空闲的）segments 为空
    names = timer_service.agent_name_rules(current_tenant())
    for run_id, r in by_id.items():
        if end > now and r["endTs"] is None and run_id not in runs and r["startTs"] <= now:
            lane_of(run_id, names.get(r.get("agent")) or r.get("label") or r.get("agent"), r.get("agent"))
    first = lambda r: r["segments"][0]["start"] if r["segments"] else _iso(by_id[r["runId"]]["startTs"])  # noqa: E731
    return TimeLanesOut(today=_today(), now=_iso(now), windowStart=_iso(start), windowEnd=_iso(end),
                        human=[{"start": _iso(a), "end": _iso(b), "projectId": pid, "taskId": task}
                               for pid, task, a, b in sorted(humans, key=lambda x: x[2])],
                        agents=sorted(runs.values(), key=first))
