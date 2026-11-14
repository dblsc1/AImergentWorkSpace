"""无项目计时与白条（契约 hive.stage.v1「三·1」，nexus-core v2.44）。

在跑的计时 ``taskId`` 为 None = 还没记到项目；停下时存成白条（``timer_unplaced``），不进台账。
记到项目 = 记到该项目的「未分类」桶（``reassign._bucket``，同 ``POST planner/projects/{id}/unclassified``）。
只许人：设备令牌 / AI 来源 403（``reassign.guard``）。
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime, timedelta

from ...tenant import current as current_tenant
from ..planner.errors import HasChildrenError, NotFoundError, UnprocessableError
from ..projector.handlers import lanes as lanes_projection
from . import backfill as backfill_impl
from . import reassign, repo

#: v2.62：一段计时记到项目时，怎么对待这段时间里的窗口聚焦记录（hive.stage.v1「三十四」）
#:   ignore —— 忽略记录：整段记给计时的目标，期间的窗口块让位（对账照旧扣掉与计时重叠的部分）；
#:   both   —— 我全都要（缺省）：整段记给计时的目标，期间已分类的窗口块照记各自的去向（对账不扣这段）；
#:   fill   —— 填充空隙：只记已分类窗口时间之外的空隙；空隙里没分类的窗口时间算计时的（对账扣掉）。
WINDOW_RECORDS = ("ignore", "both", "fill")
DEFAULT_WINDOW_RECORDS = "both"
BOTH_PREFIX = "timer-both:"  # 对账按防重键前缀认出「我全都要」的段，不扣窗口块
_FILL_SCAN = 5000

_WHAT = "操作无项目计时"


def check_human(request) -> None:
    reassign.guard(request, _WHAT)


def _duration(start_at: str, end_at: str) -> int:
    return max(int((datetime.fromisoformat(end_at) - datetime.fromisoformat(start_at)).total_seconds()), 1)


def start(now: Callable[[], datetime], request, countdown_seconds: int | None = None) -> dict:
    check_human(request)
    user = current_tenant()
    t0 = now()
    start_at = t0.isoformat()
    deadline = (t0 + timedelta(seconds=countdown_seconds)).isoformat() if countdown_seconds else None
    doc = {"user": user, "sessionId": f"sess_{uuid.uuid4().hex[:12]}", "taskId": None, "projectId": None,
           "zoneId": None, "startAt": start_at, "mode": backfill_impl.DEFAULT_MODE}
    if deadline:
        doc["deadlineAt"] = deadline
    if not repo.insert_running(doc):
        raise HasChildrenError("已经在计时：先停下或把它记到项目")
    return {"running": True, "taskId": None, "projectId": None, "startAt": start_at, "mode": doc["mode"], "deadlineAt": deadline}


def save_bar(state: dict, end_at: str, user: str) -> str:
    """停下时没有目标：存白条。id 由段的 sessionId 派生，重试不会存出两条。"""
    bar_id = "unp_" + state["sessionId"].removeprefix("sess_")
    repo.add_unplaced({"user": user, "id": bar_id, "startAt": state["startAt"], "endAt": end_at, "createdAt": end_at})
    return bar_id


def _target(task_id, project_id, request, resolve_chain) -> tuple[str, str, str]:
    """恰好一个；projectId 记到该项目的未分类桶（取或建）。返回 (taskId, projectId, zoneId)。"""
    if (task_id is None) == (project_id is None):
        raise UnprocessableError("taskId 与 projectId 必须恰好给一个")
    if project_id is not None:
        task_id = reassign._bucket(project_id, request)  # noqa: SLF001 —— 同一条取或建的写入口
    _task, project, zone = resolve_chain(task_id, action="拒绝记到该目标")
    return task_id, project, zone


def place_running(task_id, project_id, request, resolve_chain, as_timer_out: Callable[[dict], dict],
                  window_records: str = DEFAULT_WINDOW_RECORDS) -> dict:
    from .service import NoRunningTimerError  # noqa: PLC0415 —— service 也 import 本文件

    check_human(request)
    user = current_tenant()
    if (task_id is None) == (project_id is None):
        raise UnprocessableError("taskId 与 projectId 必须恰好给一个")
    state = repo.get_running(user)
    if state is None:
        raise NoRunningTimerError("当前没有正在计时的会话，没有可记的东西")
    task, project, zone = _target(task_id, project_id, request, resolve_chain)
    fields = {"taskId": task, "projectId": project, "zoneId": zone, "windowRecords": window_records}  # v2.62：停下时按它记
    if not repo.retarget_running(user, state["sessionId"], fields):
        raise NoRunningTimerError("计时刚刚已经停下")
    return as_timer_out({**state, **fields})


def list_bars(request) -> dict:
    check_human(request)
    return {"items": [{"id": b["id"], "startAt": b["startAt"], "endAt": b["endAt"],
                       "durationSeconds": _duration(b["startAt"], b["endAt"])}
                      for b in repo.list_unplaced(current_tenant())]}


def classified_window_time(user: str, start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
    """[start, end) 里已经入账的窗口块时间（``source: activity-block`` 的段，已并好）。读不全（超过上限）→ 抛错，不拿截断的数据切。"""
    rows = lanes_projection.read_lanes(user, "session", start, end, _FILL_SCAN, sources=["activity-block"])
    if len(rows) >= _FILL_SCAN:
        raise UnprocessableError("这段时间里的窗口记录太多，没法按「填充空隙」切；换别的记法")
    out: list[list[datetime]] = []
    for a, b in sorted((max(r["startAt"], start), min(r["endAt"], end)) for r in rows):
        if b <= a:
            continue
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def gaps(start: datetime, end: datetime, taken: list[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    """[start, end) 去掉 ``taken``（已并好、升序）后剩下的空隙，< 1 秒的不要。"""
    out, cur = [], start
    for a, b in taken:
        if a > cur:
            out.append((cur, min(a, end)))
        cur = max(cur, b)
    if cur < end:
        out.append((cur, end))
    return [(a, b) for a, b in out if (b - a).total_seconds() >= 1]


def record_with(user: str, subject: dict, start_at: str, end_at: str, key: str, window_records: str, mode: str) -> None:
    """按记法写台账：ignore 防重键 ``timer…:<key>`` 照旧；both 防重键带 ``BOTH_PREFIX``；fill 每个空隙一条（防重键带空隙起点毫秒）。"""
    if window_records == "fill":
        start, end = datetime.fromisoformat(start_at), datetime.fromisoformat(end_at)
        for a, b in gaps(start, end, classified_window_time(user, start, end)):
            a, b = a.astimezone(start.tzinfo), b.astimezone(start.tzinfo)  # 库里读出来的是 UTC：与计时起点同一个时区写
            backfill_impl.record_session(user, subject, a.isoformat(), b.isoformat(), max(int((b - a).total_seconds()), 1),
                                         source="timer-backend", dedupe_key=f"timer-fill:{key}:{round(a.timestamp() * 1000)}", mode=mode)
        return
    dedupe = f"{BOTH_PREFIX}{key}" if window_records == "both" else f"timer-unplaced:{key}"
    backfill_impl.record_session(user, subject, start_at, end_at, _duration(start_at, end_at),
                                 source="timer-backend", dedupe_key=dedupe, mode=mode)


def place_bar(bar_id: str, task_id, project_id, request, resolve_chain, window_records: str = DEFAULT_WINDOW_RECORDS) -> dict:
    check_human(request)
    user = current_tenant()
    if (task_id is None) == (project_id is None):
        raise UnprocessableError("taskId 与 projectId 必须恰好给一个")
    bar = repo.get_unplaced(user, bar_id)
    if bar is None:
        raise NotFoundError(f"白条不存在：{bar_id!r}")
    task, project, zone = _target(task_id, project_id, request, resolve_chain)
    record_with(user, {"zone": zone, "project": project, "task": task}, bar["startAt"], bar["endAt"], bar_id,
                window_records, backfill_impl.DEFAULT_MODE)
    repo.delete_unplaced(user, bar_id)  # 先写台账后删：重试命中防重，不会重复记
    return {"recorded": True, "taskId": task, "projectId": project, "windowRecords": window_records}


def discard_bar(bar_id: str, request) -> None:
    """v2.62：白条扔进垃圾桶 = 删掉，不进台账。不存在 → 404。"""
    check_human(request)
    user = current_tenant()
    if repo.get_unplaced(user, bar_id) is None:
        raise NotFoundError(f"白条不存在：{bar_id!r}")
    repo.delete_unplaced(user, bar_id)


def export_bars() -> list[dict]:
    return repo.list_unplaced(current_tenant())


def restore_bars(docs: list[dict]) -> int:
    user = current_tenant()
    for d in docs:
        repo.add_unplaced({**d, "user": user})
    return len(docs)
