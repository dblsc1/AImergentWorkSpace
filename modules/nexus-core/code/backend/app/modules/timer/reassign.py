"""改挂未分类时间：``POST /api/core/sessions/{eventId}/reassign``（契约 v2.11「改挂未分类时间」，唯一事实源）。

仓主 2026-10-08：「时间记录到未分类以后，能不能追加记录『归入 xxx 任务』？不能破坏可追溯性。」

**只追加，不改写**：原来那条 ``session.completed`` 一个字不动，每次改挂往台账追加一条
``session.reassigned``（``subject`` = 去向，``data`` = 哪一段、从哪来、第几次）。一段当前算在谁头上 =
它 ``seq`` 最大的那条改挂的去向。

住在 timer 子边界的理由同补登：与人的时间有关的信封只在这里组装，归属链与 ``start()`` 同一套判据
（``service._resolve_task_chain``）。写台账只经 ``events_service.ingest``。

并发：``dedupeKey = reassign:<sessionEventId>:<seq>``。两个并发的改挂都想写第 n+1 条，唯一索引只放进
一条；没抢到的重读再试。所以台账里永远是一条连续的链，不需要锁。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from ...tenant import current as current_tenant
from ..events import service as events_service
from ..events.schemas import SPEC
from ..planner import audit as planner_audit
from ..planner import guard as planner_guard
from ..planner import unclassified
from ..planner.errors import (
    ForbiddenError,
    HasChildrenError,
    HighRiskDeniedError,
    InvalidInputError,
    NotFoundError,
)
from . import service as timer_service

SOURCE = "session-reassign"
_MAX_TRIES = 5


def _guard(request: Any) -> None:
    """改挂是人的决定：设备令牌 403；来源凭据按 v1.6 的高风险写判（actor=ai / 严格模式缺人路径凭据 → 403）。"""
    if (request.headers.get("authorization") or "").strip().lower().startswith("bearer "):
        raise ForbiddenError("设备令牌不能改挂时间：这是人的决定，请在 Cockpit 页面上登录后操作")
    source = planner_guard.resolve_source(request.headers)
    denial = planner_guard.denial_reason(
        planner_guard.resolve_actor(source, None), source, "改挂时间段（session.reassigned）")
    if denial is not None:
        raise HighRiskDeniedError(denial)


def _bucket(project_id: str, request: Any) -> str:
    """项目的「未分类」时间桶，取或建（同 ``POST /planner/projects/{id}/unclassified``，同一条写入口）。"""
    task = unclassified.find(project_id) or planner_guard.run_write(
        request, op=planner_audit.OP_CREATE, object_type="tasks", changes=unclassified.changes(project_id),
        action=lambda actor: unclassified.create(project_id, actor),
    )
    return task["id"]


def _out(event_id: str, duplicate: bool, from_task: str, subject: dict, seq: int, event: dict | None) -> dict:
    return {
        "sessionEventId": event_id, "duplicate": duplicate, "fromTaskId": from_task,
        "taskId": subject["task"], "projectId": subject["project"], "seq": seq,
        "event": {k: event[k] for k in ("id", "dedupeKey", "type")} if event else None,
    }


def reassign(event_id: str, task_id: str | None, project_id: str | None, request: Any) -> dict:
    _guard(request)
    if (task_id is None) == (project_id is None):
        raise InvalidInputError("taskId 与 projectId 必须给一个、且只能给一个")

    sessions = events_service.find_sessions(event_id)
    if not sessions:
        raise NotFoundError(f"没有这条计时段：{event_id!r}（台账里查无此 id 的 session.completed）")
    if len(sessions) > 1:
        raise HasChildrenError(f"事件 id {event_id!r} 对上了 {len(sessions)} 条计时段（事件 id 不唯一），不猜是哪一条")
    session = sessions[0]
    origin = session.get("subject") or {}
    # 判据只看台账：桶的 id 由项目 id 派生，项目 / 桶以后删了也判得出「这一段原本记在桶上」
    if not origin.get("project") or origin.get("task") != unclassified.task_id(origin["project"]):
        raise HasChildrenError(
            f"计时段 {event_id!r} 原本不是记在项目的「未分类」上的——只有未分类的时间能改挂到任务")

    to_bucket = task_id is None
    if to_bucket:
        task_id = _bucket(project_id, request)  # 放回某个项目的未分类（撤回用）
    task, to_project, to_zone = timer_service._resolve_task_chain(task_id, action="拒绝改挂")  # noqa: SLF001
    if not to_bucket and unclassified.is_bucket(task):
        raise InvalidInputError(f"任务 {task_id!r} 是「未分类」时间桶——要放回未分类请给 projectId")
    target = {"zone": to_zone, "project": to_project, "task": task_id}

    user = current_tenant()
    data = session.get("data") or {}
    for _ in range(_MAX_TRIES):
        chain = events_service.reassignments_of(session)
        last = chain[-1] if chain else None
        current = (last or session)["subject"]
        seq = last["data"]["seq"] if last else 0
        if current.get("task") == task_id:
            return _out(event_id, True, task_id, current, seq, last)  # 已经在那儿：幂等，什么都不追加

        envelope = {
            "spec": SPEC,
            "id": f"evt_{uuid.uuid4().hex[:12]}",
            "dedupeKey": f"reassign:{event_id}:{seq + 1}",  # 同一段的第 n 次改挂只可能落一条
            "type": events_service.REASSIGNED_TYPE,
            "user": user,
            "source": SOURCE,
            "time": datetime.now(timezone.utc).isoformat(),
            "subject": target,
            "data": {
                "sessionEventId": event_id,
                "seq": seq + 1,
                "fromTaskId": current.get("task"), "fromProjectId": current.get("project"),
                "toTaskId": task_id, "toProjectId": to_project,
                "actor": planner_guard.ACTOR_HUMAN,
                # 那一段的抄录：投影的 handler 不回头查台账（它们不许 import events）
                "session": {"source": session.get("source"), "dedupeKey": session.get("dedupeKey"),
                            "startAt": data.get("startAt"), "durationSeconds": data.get("durationSeconds")},
            },
            "flags": [],
        }
        result = events_service.ingest(envelope, internal=True)
        if result.rejected:
            raise RuntimeError(f"{SOURCE} 组装的信封未过事件校验：{result.rejected[0].reason}")
        if result.accepted:
            return _out(event_id, False, current.get("task"), target, seq + 1, envelope)
        # duplicate：别的请求抢先写了第 seq+1 条 → 重读，按新的当前归属再来
    raise HasChildrenError(f"计时段 {event_id!r} 正被并发改挂，连续 {_MAX_TRIES} 次没抢到，请重试")
