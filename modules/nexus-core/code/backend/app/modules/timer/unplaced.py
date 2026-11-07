"""无项目计时与白条（契约 hive.stage.v1「三·1」，nexus-core v2.44）。

在跑的计时 ``taskId`` 为 None = 还没记到项目；停下时存成白条（``timer_unplaced``），不进台账。
记到项目 = 记到该项目的「未分类」桶（``reassign._bucket``，同 ``POST planner/projects/{id}/unclassified``）。
只许人：设备令牌 / AI 来源 403（``reassign.guard``）。
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime

from ...tenant import current as current_tenant
from ..planner.errors import HasChildrenError, NotFoundError, UnprocessableError
from . import backfill as backfill_impl
from . import reassign, repo

_WHAT = "操作无项目计时"


def check_human(request) -> None:
    reassign.guard(request, _WHAT)


def _duration(start_at: str, end_at: str) -> int:
    return max(int((datetime.fromisoformat(end_at) - datetime.fromisoformat(start_at)).total_seconds()), 1)


def start(now: Callable[[], datetime], request) -> dict:
    check_human(request)
    user = current_tenant()
    start_at = now().isoformat()
    doc = {"user": user, "sessionId": f"sess_{uuid.uuid4().hex[:12]}", "taskId": None, "projectId": None,
           "zoneId": None, "startAt": start_at, "mode": backfill_impl.DEFAULT_MODE}
    if not repo.insert_running(doc):
        raise HasChildrenError("已经在计时：先停下或把它记到项目")
    return {"running": True, "taskId": None, "projectId": None, "startAt": start_at, "mode": doc["mode"]}


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


def place_running(task_id, project_id, request, resolve_chain, as_timer_out: Callable[[dict], dict]) -> dict:
    from .service import NoRunningTimerError  # noqa: PLC0415 —— service 也 import 本文件

    check_human(request)
    user = current_tenant()
    if (task_id is None) == (project_id is None):
        raise UnprocessableError("taskId 与 projectId 必须恰好给一个")
    state = repo.get_running(user)
    if state is None:
        raise NoRunningTimerError("当前没有正在计时的会话，没有可记的东西")
    task, project, zone = _target(task_id, project_id, request, resolve_chain)
    fields = {"taskId": task, "projectId": project, "zoneId": zone}
    if not repo.retarget_running(user, state["sessionId"], fields):
        raise NoRunningTimerError("计时刚刚已经停下")
    return as_timer_out({**state, **fields})


def list_bars(request) -> dict:
    check_human(request)
    return {"items": [{"id": b["id"], "startAt": b["startAt"], "endAt": b["endAt"],
                       "durationSeconds": _duration(b["startAt"], b["endAt"])}
                      for b in repo.list_unplaced(current_tenant())]}


def place_bar(bar_id: str, task_id, project_id, request, resolve_chain) -> dict:
    check_human(request)
    user = current_tenant()
    if (task_id is None) == (project_id is None):
        raise UnprocessableError("taskId 与 projectId 必须恰好给一个")
    bar = repo.get_unplaced(user, bar_id)
    if bar is None:
        raise NotFoundError(f"白条不存在：{bar_id!r}")
    task, project, zone = _target(task_id, project_id, request, resolve_chain)
    backfill_impl.record_session(
        user, {"zone": zone, "project": project, "task": task},
        bar["startAt"], bar["endAt"], _duration(bar["startAt"], bar["endAt"]),
        source="timer-backend", dedupe_key=f"timer-unplaced:{bar_id}",
    )
    repo.delete_unplaced(user, bar_id)  # 先写台账后删：重试命中防重，不会重复记
    return {"recorded": True, "taskId": task, "projectId": project}


def export_bars() -> list[dict]:
    return repo.list_unplaced(current_tenant())


def restore_bars(docs: list[dict]) -> int:
    user = current_tenant()
    for d in docs:
        repo.add_unplaced({**d, "user": user})
    return len(docs)
