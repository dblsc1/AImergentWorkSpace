"""v2.30 AI 提议新项目 / 项目下的新任务（契约「AI 提议新项目与任务」节，唯一事实源）。

仓主 2026-10-19：「给 mcp 加新增项目（需要人批准）和新增项目下的任务两个接口（都需要人批准）」。
**AI 只提议，人批准才建**：``propose_project`` / ``propose_task`` 只存一条提议，planner 一个字节不动。

- 建项目 / 建任务都走 planner **既有的写入口**（``guard.run_write`` → ``create_project`` / ``create_task``，审计一样不少）；
- 同 ``proposals.task_for`` 的做法：id 在提议时预留，批准 = pending → accepted（条件更新）+ 持久的「建」占位，
  只有占到的那一次去建，并发批准复用它；占位永不放掉（失败即安全失败）；
- 同一（分区 / 项目 / 提议, 归一化名字）一条提议（id 由它派生）；人「不要」过的永远留着，AI 不许再提；
- 任务可以挂在**还没批准的项目提议**下（``projectProposalId``）：项目批准前它不能单独批准（409），批准后指向真项目；
  不要项目 = 同时不要它下面待批准的任务。
- ponytail: 「待批准至多 N 条」是先数后插，并发提交最多超出并发数；提议不过期（被不要的要永远记着）。
"""

from __future__ import annotations

import hashlib
import time
import uuid
from datetime import datetime, timezone

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from ...repo import get_db
from ...tenant import current as current_tenant
from ..planner import audit, guard
from ..planner import service as planner_service
from ..planner.errors import InvalidInputError, NotFoundError
from . import proposals
from .proposals import ConflictError
from .reports import TooManyReportsError

MAX_PENDING = {"project": 20, "task": 100}  # 每租户待批准的条数
MAX_REASON = 300  # 码点
_COLL = "activity_new_items"
_ready = False


def _col():
    global _ready
    col = get_db()[_COLL]
    if not _ready:
        col.create_index([("user", 1), ("id", 1)], unique=True, name="uniq_user_id")
        _ready = True
    return col


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _get(user: str, item_id: str) -> dict | None:
    return _col().find_one({"user": user, "id": item_id}, {"_id": 0})


def _put(user: str, kind: str, parent: str, name: str, reason: str, extra: dict) -> dict:
    """取（或新建）提议。已批准但东西后来被删了（调用方已确认没有同名活的）→ 换一条新的。"""
    item_id = ("np_" if kind == "project" else "nt_") + hashlib.sha256(
        f"{parent}\n{proposals._norm(name)}".encode()).hexdigest()[:20]
    d = _get(user, item_id)
    if d and d["status"] == "accepted":
        getter = planner_service.get_project if kind == "project" else planner_service.get_task
        claimed = d.get("createClaimedAt")
        if getter(d["reservedId"]) is None and claimed is not None \
                and (_now() - claimed.replace(tzinfo=timezone.utc)).total_seconds() < proposals.WAIT_SECONDS:
            raise ConflictError("这条提议正在批准、建的过程中，请稍后再试")
        _col().delete_one({"user": user, "id": item_id, "status": "accepted", "createClaimedAt": claimed})
        d = None
    if d:
        if d["status"] == "rejected":
            raise ConflictError("用户已经否掉过这个提议，不要再提")
        return d
    if _col().count_documents({"user": user, "kind": kind, "status": "pending"}) >= MAX_PENDING[kind]:
        raise TooManyReportsError(f"已有 {MAX_PENDING[kind]} 条待批准的{'项目' if kind == 'project' else '任务'}提议，"
                                  "请先让用户处理旧的")
    reserved = f"p_{uuid.uuid4().hex[:6]}" if kind == "project" else f"t_{uuid.uuid4().hex[:12]}"
    doc = {"user": user, "id": item_id, "kind": kind, "status": "pending", "name": name, "reason": reason,
           "reservedId": reserved, "createClaimedAt": None, "createdAt": _now(), **extra}
    try:
        _col().insert_one(dict(doc))
    except DuplicateKeyError:  # 并发的同一条提议：用先到的
        return _get(user, item_id)
    return doc


def clean_name(name: str, what: str) -> str:
    try:
        return proposals.clean_name(name, what)
    except ValueError as exc:
        raise InvalidInputError(str(exc)) from None


def _clean_reason(reason: str) -> str:
    return " ".join((reason or "").split())[:MAX_REASON]


def propose_project(zone_id: str, name: str, reason: str) -> dict:
    user = current_tenant()
    name = clean_name(name, "项目的 name")
    if planner_service.get_zone(zone_id) is None:
        raise InvalidInputError(f"zoneId 不存在：{zone_id!r}")
    n = proposals._norm(name)
    same = next((p for p in planner_service.list_projects(zone_id) if proposals._norm(p["name"]) == n), None)
    if same is not None:
        raise ConflictError(f"分区里已有同名项目 {same['id']!r}，请直接用它的 projectId")
    return _view(user, _put(user, "project", zone_id, name, _clean_reason(reason), {"zoneId": zone_id}))


def propose_task(project_id: str | None, project_proposal_id: str | None, name: str, reason: str) -> dict:
    user = current_tenant()
    if (project_id is None) == (project_proposal_id is None):
        raise InvalidInputError("projectId 与 projectProposalId 须恰好给一个")
    name = clean_name(name, "任务的 name")
    if project_id is not None:
        if planner_service.get_project(project_id) is None:
            raise InvalidInputError(f"项目不存在：{project_id!r}")
        same = proposals._same_name_task(project_id, name)
        if same is not None:
            raise ConflictError(f"项目里已有同名任务 {same['id']!r}，请直接用它的 taskId")
        parent, extra = project_id, {"projectId": project_id, "parentId": None}
    else:
        pp = _get(user, project_proposal_id)
        if pp is None or pp["kind"] != "project":
            raise InvalidInputError(f"项目提议不存在：{project_proposal_id!r}")
        if pp["status"] != "pending":
            real = f"；已批准，请改用 projectId={pp['reservedId']!r}" if pp["status"] == "accepted" else ""
            raise ConflictError(f"这个项目提议已{'批准' if real else '被否掉'}，不能再往下挂{real}")
        parent, extra = "pp:" + pp["id"], {"projectId": None, "parentId": pp["id"]}
    return _view(user, _put(user, "task", parent, name, _clean_reason(reason), extra))


def _project_id_of(user: str, d: dict) -> str | None:
    """任务提议所属的真项目 id；挂在没批准的项目提议下 → None。"""
    if d.get("projectId"):
        return d["projectId"]
    pp = _get(user, d["parentId"]) if d.get("parentId") else None
    ok = pp is not None and pp["status"] == "accepted" and planner_service.get_project(pp["reservedId"]) is not None
    return pp["reservedId"] if ok else None


def _view(user: str, d: dict) -> dict:
    out = {"id": d["id"], "kind": d["kind"], "status": d["status"], "name": d["name"], "reason": d["reason"],
           "createdAt": d["createdAt"].replace(tzinfo=timezone.utc).isoformat(),
           "createdId": d["reservedId"] if d["status"] == "accepted" else None}
    if d["kind"] == "project":
        zone = planner_service.get_zone(d["zoneId"])
        out.update(zoneId=d["zoneId"], zoneName=zone["name"] if zone else None)
    else:
        pid = _project_id_of(user, d)
        proj = planner_service.get_project(pid) if pid else None
        out.update(projectId=pid, projectName=proj["name"] if proj else None, projectProposalId=d.get("parentId"))
    return out


def listing(pending_only: bool, limit: int = 100) -> dict:
    """``{projects: [提议 + tasks[]], tasks: [挂在现成项目上的提议]}``。挂在列出的项目提议下的任务进它的 tasks。"""
    user = current_tenant()
    filt = {"user": user, **({"status": "pending"} if pending_only else {})}
    rows = list(_col().find(filt, {"_id": 0}).sort("createdAt", -1).limit(limit))
    projects = {d["id"]: {**_view(user, d), "tasks": []} for d in rows if d["kind"] == "project"}
    tasks = []
    for d in rows:
        if d["kind"] == "task":
            (projects[d["parentId"]]["tasks"] if d.get("parentId") in projects else tasks).append(_view(user, d))
    return {"projects": list(projects.values()), "tasks": tasks}


def reject(item_id: str) -> dict:
    user = current_tenant()
    d = _get(user, item_id)
    if d is None:
        raise NotFoundError(f"没有这条提议：{item_id!r}")
    if d["status"] == "accepted":
        raise ConflictError("这条提议已经批准，建出来的东西请到项目页处理")
    at = _now()
    won = _col().update_one({"user": user, "id": item_id, "status": "pending"},
                            {"$set": {"status": "rejected", "decidedAt": at}}).modified_count
    if d["kind"] == "project" and won:
        _col().update_many({"user": user, "parentId": item_id, "status": "pending"},
                           {"$set": {"status": "rejected", "decidedAt": at}})
    return _view(user, _get(user, item_id))


def approve(item_id: str, request) -> dict:
    """批准并建出来（幂等）：同一提议只建一次，并发批准复用它（做法同 ``proposals.task_for``）。"""
    user = current_tenant()
    d = _get(user, item_id)
    if d is None:
        raise NotFoundError(f"没有这条提议：{item_id!r}")
    if d["status"] == "rejected":
        raise ConflictError("这条提议已被否掉")
    project = d["kind"] == "project"
    if project:
        if planner_service.get_zone(d["zoneId"]) is None:
            raise ConflictError("这个项目要放的分区已经不存在了，批不了；请选「不要」")
    elif d.get("projectId") and planner_service.get_project(d["projectId"]) is None:
        raise ConflictError("这个任务要放的项目已经不存在了，批不了；请选「不要」")
    pid = None if project else _project_id_of(user, d)
    if not project and pid is None:
        pp = _get(user, d["parentId"]) if d.get("parentId") else None
        if pp is not None and pp["status"] == "accepted":
            raise ConflictError("它所属的新项目已批准过，但那个项目现在已不存在了，批不了；请选「不要」")
        raise ConflictError("它所属的新项目还没批准：请先批准项目（项目批准后它就能批准了）")
    now = _now()
    _col().update_one({"user": user, "id": item_id, "status": "pending"},
                      {"$set": {"status": "accepted", "decidedAt": now}})
    rid = d["reservedId"]
    getter = planner_service.get_project if project else planner_service.get_task
    if getter(rid) is None:
        won = _col().find_one_and_update(
            {"user": user, "id": item_id, "status": "accepted", "createClaimedAt": None},
            {"$set": {"createClaimedAt": now}}, return_document=ReturnDocument.AFTER, projection={"_id": 0})
        if won is not None:
            try:
                _create(project, won, pid, rid, request)
            except Exception:
                if getter(rid) is None:  # 没建出来：放掉占位、回到待批准，人可以再批或不要
                    _col().update_one({"user": user, "id": item_id, "createClaimedAt": now},
                                      {"$set": {"status": "pending", "createClaimedAt": None}})
                raise
        else:  # 别人占着「建」：等它建好；等不到 = 建过又删了 / 那一次崩了（绝不再建）
            deadline = time.monotonic() + proposals.WAIT_SECONDS
            while getter(rid) is None:
                if time.monotonic() > deadline:
                    raise NotFoundError(f"这条提议要建的东西（{rid!r}）已不存在：建过又被删掉，或建的时候出了错")
                time.sleep(0.1)
    return _view(user, _get(user, item_id))


def _create(project: bool, won: dict, pid: str | None, rid: str, request) -> None:
    n = proposals._norm(won["name"])
    if project:  # 占到「建」之后、真建之前再看一眼：期间有人手建了同名的
        same = next((p for p in planner_service.list_projects(won["zoneId"]) if proposals._norm(p["name"]) == n), None)
        if same is not None:
            raise ConflictError(f"分区里刚有了同名项目 {same['id']!r}，没有重复建；请选「不要」，用它的 projectId")
        guard.run_write(
            request, op=audit.OP_CREATE, object_type="projects",
            changes={"zoneId": won["zoneId"], "name": won["name"]},
            action=lambda actor: planner_service.create_project(won["zoneId"], won["name"], actor=actor, project_id=rid))
        return
    same = proposals._same_name_task(pid, won["name"])
    if same is not None:
        raise ConflictError(f"项目里刚有了同名任务 {same['id']!r}，没有重复建；请选「不要」，用它的 taskId")
    guard.run_write(
        request, op=audit.OP_CREATE, object_type="tasks", changes={"projectId": pid, "name": won["name"]},
        action=lambda actor: planner_service.create_task(won["name"], pid, actor=actor, task_id=rid))
