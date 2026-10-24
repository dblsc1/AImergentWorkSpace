"""v2.39 4.3 AI 提议改已记的时间 ``sessionChange``（契约「MCP v0.5 的后端」4.3）。

存在 ``activity_new_items``（前缀 ``ns_``）；提交、去重、不要过的 409、上限 429 复用 ``new_items._put_edit``。
批准（只许人，可带 ``edit``）与直接施加共用 ``timer.reassign.reassign_as``（内部函数）：人批准 / 撤回 ``actor: human``，直接施加 ``actor: ai``。
公开端点 ``POST sessions/{id}/reassign`` 不变，仍只许人。
契约待定：事件 id 不唯一（对上多条 ``session.completed``）→ 409，同改挂端点；原本不是「未分类」也不是自动记下的段，提议时不拦，批准 / 直接施加时 409。
契约待定：批准时目标已是当前归属 = 空操作，提议记 ``accepted``（同 blockAction）；``edit`` 形状错 422、目标不存在 400。
契约待定：撤回时「之后又被改挂过」= 这段最新一条 ``session.reassigned`` 的 ``seq`` 不是直接施加写下的那条。
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from ...tenant import current as current_tenant
from ..events import service as events_service
from ..planner import service as planner_service
from ..planner import unclassified
from ..planner.errors import HasChildrenError, InvalidInputError, UnprocessableError
from ..timer import reassign as reassign_impl
from . import direct_log, new_items
from .proposals import ConflictError


def _owner(session: dict) -> tuple[str | None, str | None, int]:
    """这一段当前的 (taskId, projectId, 最新改挂 seq)。"""
    chain = events_service.reassignments_of(session)
    last = chain[-1] if chain else None
    subject = (last or session)["subject"]
    return subject.get("task"), subject.get("project"), last["data"]["seq"] if last else 0


def _target(task_id, project_id, exc=InvalidInputError) -> str:
    """校验目标，返回它落在的任务 id（项目 = 它的「未分类」桶）。"""
    if (task_id is None) == (project_id is None):
        raise exc("taskId 与 projectId 须恰好给一个")
    if task_id is not None:
        task = planner_service.get_task(task_id)
        if task is None:
            raise InvalidInputError(f"任务不存在：{task_id!r}")
        if unclassified.is_bucket(task):
            raise InvalidInputError(f"任务 {task_id!r} 是「未分类」时间桶——要改到未分类请给 projectId")
        return task_id
    if planner_service.get_project(project_id) is None:
        raise InvalidInputError(f"项目不存在：{project_id!r}")
    return unclassified.task_id(project_id)


def view(d: dict) -> dict:
    """``fromTaskId`` / ``fromProjectId`` 读时取这段当前的归属（台账查不到才用提议时的快照）。"""
    found = events_service.find_sessions(d["eventId"])
    from_task, from_project = _owner(found[0])[:2] if len(found) == 1 else (d["fromTaskId"], d["fromProjectId"])
    return {"id": d["id"], "kind": "sessionChange", "status": d["status"], "eventId": d["eventId"], "op": d["op"],
            "taskId": d["taskId"], "projectId": d["projectId"], "fromTaskId": from_task, "fromProjectId": from_project,
            "startAt": d["startAt"], "durationSeconds": d["durationSeconds"], "reason": d["reason"],
            "createdAt": d["createdAt"].replace(tzinfo=timezone.utc).isoformat(), "corrected": bool(d.get("corrected"))}


def propose(event_id: str, task_id, project_id, reason: str, direct: bool, request) -> dict:
    if direct:
        direct_log.gate("honeycombRecords")  # 先过闸门：不满足条件就 403，什么都不做
    user = current_tenant()
    sessions = events_service.find_sessions(event_id)
    if not sessions:
        raise InvalidInputError(f"没有这条计时段：{event_id!r}（须是本账号的 session.completed）")
    if len(sessions) > 1:
        raise HasChildrenError(f"事件 id {event_id!r} 对上了 {len(sessions)} 条计时段，不猜是哪一条")
    session = sessions[0]
    if events_service.is_voided(session):
        raise ConflictError("这段已作废")
    reassign_impl.check_eligible(session)  # 不能改挂的段（不是记在未分类 / 自动记下的）在写提议 / 日志之前就 409
    dest = _target(task_id, project_id)
    cur_task, cur_project, _seq = _owner(session)
    if cur_task == dest:
        raise ConflictError("目标与这段现在的归属相同")
    item_id = "ns_" + hashlib.sha256(f"{event_id}\n{'task:' + task_id if task_id else 'project:' + project_id}".encode()).hexdigest()[:20]
    reason = new_items._clean_reason(reason)  # noqa: SLF001
    data = session.get("data") or {}
    extra = {"eventId": event_id, "op": "reassign", "taskId": task_id, "projectId": project_id, "fromTaskId": cur_task,
             "fromProjectId": cur_project, "startAt": data.get("startAt"), "durationSeconds": data.get("durationSeconds"), "corrected": False}
    if not direct:
        return view(new_items._put_edit(user, "sessionChange", item_id, extra, reason))  # noqa: SLF001
    old = new_items._get(user, item_id)  # noqa: SLF001
    if old and old["status"] == "rejected":
        raise ConflictError("用户已经否掉过这个提议，不要再提")

    def go():
        out = reassign_impl.reassign_as(event_id, task_id, project_id, request, "ai")
        if out["duplicate"]:
            raise ConflictError("目标与这段现在的归属相同")
        return out, {"taskId": cur_task, "projectId": cur_project}, {"taskId": out["taskId"], "projectId": out["projectId"], "seq": out["seq"]}

    def finish():
        new_items._col().update_many({"user": user, "id": item_id, "status": "pending"},  # noqa: SLF001
                                     {"$set": {"status": "accepted", "decidedAt": datetime.now(timezone.utc), "applyClaimedAt": None}})

    return direct_log.run("sessionChange", {"eventId": event_id, "op": "reassign", "taskId": task_id, "projectId": project_id, "reason": reason}, go, finish)


def _edited(edit) -> dict:
    if not isinstance(edit, dict) or not edit or set(edit) - {"taskId", "projectId"}:
        raise UnprocessableError("sessionChange 的 edit 形如 {taskId} 或 {projectId}")
    _target(edit.get("taskId"), edit.get("projectId"), UnprocessableError)
    return {"taskId": edit.get("taskId"), "projectId": edit.get("projectId")}


def _seq_of(event_id: str) -> int | None:
    found = events_service.find_sessions(event_id)
    return _owner(found[0])[2] if len(found) == 1 else None


def approve(user: str, d: dict, request, edit: dict | None) -> dict:
    col = new_items._col()  # noqa: SLF001
    if d["status"] == "accepted":
        claimed = d.get("applyClaimedAt")
        if claimed is None:
            return view(d)
        if not new_items._aged(claimed):  # noqa: SLF001
            raise ConflictError("这条提议正在批准、落地的过程中，请稍后再看")
        # 上一个批准崩在半路（占位过期）。已在目标 = 其实落地了，收尾；这段的改挂 seq 还是占位时的 = 没人动过，接着做；变了 = 别人改过，不重做
        found = events_service.find_sessions(d["eventId"])
        try:
            landed = len(found) == 1 and _owner(found[0])[0] == _target(d["taskId"], d["projectId"])
        except InvalidInputError:  # 目标已被删：不可能落地
            landed = False
        if landed:
            col.update_one({"user": user, "id": d["id"], "applyClaimedAt": claimed}, {"$set": {"applyClaimedAt": None}})
            return view(new_items._get(user, d["id"]))  # noqa: SLF001
        back = {"$set": {"status": "pending", "applyClaimedAt": None}, "$unset": {"decidedAt": ""}}
        if col.update_one({"user": user, "id": d["id"], "status": "accepted", "applyClaimedAt": claimed}, back).modified_count != 1:
            raise ConflictError("这条提议刚被别处处理了")
        if _seq_of(d["eventId"]) != d.get("claimSeq"):
            raise ConflictError("这段在上次批准中断之后又被改挂过，没有重做；提议留在待批准，请重新决定")
        d = new_items._get(user, d["id"])  # noqa: SLF001
    if d["status"] != "pending":
        raise ConflictError("这条提议已被否掉")
    fields = _edited(edit) if edit is not None else {}
    mark = datetime.now(timezone.utc)
    claim = {"status": "accepted", "decidedAt": mark, "applyClaimedAt": mark, "claimSeq": _seq_of(d["eventId"]),
             **fields, **({"corrected": True} if fields else {})}
    if col.update_one({"user": user, "id": d["id"], "status": "pending"}, {"$set": claim}).modified_count != 1:
        raise ConflictError("这条提议刚被别处批准或否掉了")
    p = {**d, **fields}
    try:
        reassign_impl.reassign_as(p["eventId"], p["taskId"], p["projectId"], request, "human")  # 归属以批准时台账里的当前归属为准
    except Exception:  # 没落地（作废 / 目标没了 / 并发）：放回待批准，更正一并撤销
        col.update_one({"user": user, "id": d["id"], "applyClaimedAt": mark},
                       {"$set": {"status": "pending", "applyClaimedAt": None, "corrected": bool(d.get("corrected")),
                                 "taskId": d["taskId"], "projectId": d["projectId"]}, "$unset": {"decidedAt": ""}})
        raise
    col.update_one({"user": user, "id": d["id"], "applyClaimedAt": mark}, {"$set": {"applyClaimedAt": None}})
    return view(new_items._get(user, d["id"]))  # noqa: SLF001


def undo(user: str, log: dict) -> None:
    """撤回直接施加：再改挂回 ``before`` 的归属；这段之后又被改挂过 → 409。"""
    sessions = events_service.find_sessions(log["payload"]["eventId"])
    if len(sessions) != 1:
        raise ConflictError("这段已不在台账里，不能撤回")
    if _owner(sessions[0])[2] != log["after"]["seq"]:
        raise ConflictError("这段在直接施加之后又被改挂过，不能撤回")
    b = log["before"]
    back = b["projectId"] if b["taskId"] == unclassified.task_id(b["projectId"] or "") else None
    reassign_impl.reassign_as(log["payload"]["eventId"], None if back else b["taskId"], back, None, "human")
