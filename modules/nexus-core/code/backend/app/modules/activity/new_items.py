"""v2.30 AI 提议新项目 / 项目下的新任务（契约「AI 提议新项目与任务」节，唯一事实源）。

仓主 2026-10-19：「给 mcp 加新增项目（需要人批准）和新增项目下的任务两个接口（都需要人批准）」。
**AI 只提议，人批准才建**：``propose_project`` / ``propose_task`` 只存一条提议，planner 一个字节不动。

- 建项目 / 建任务都走 planner **既有的写入口**（``guard.run_write`` → ``create_project`` / ``create_task``，审计一样不少）；
- 同 ``proposals.task_for`` 的做法：id 在提议时预留，批准 = pending → accepted（条件更新）+ 持久的「建」占位，
  只有占到的那一次去建，并发批准复用它；占位永不放掉（失败即安全失败）；
- 同一（分区 / 项目 / 提议, 归一化名字）一条提议（id 由它派生）；人「不要」过的永远留着，AI 不许再提；
- 任务可以挂在**还没批准的项目提议**下（``projectProposalId``）：项目批准前它不能单独批准（409），批准后指向真项目；
  不要项目 = 同时不要它下面待批准的任务。
- v2.36：另两种 kind——``change``（改名 / 搬移 / 删除现成的项目或任务，``nc_`` 前缀）与 ``ignore``（「以后都忽略」规则，``ni_``），
  同集合同口径：AI 只提议，批准 = pending → accepted（条件更新，只有赢的那次动手）后经 ``guard.run_write`` / ``ignore.add`` 落地，失败回到 pending。
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
from ...textfold import fold
from ..planner import audit, guard, inbox, repo as planner_repo, unclassified
from ..planner import service as planner_service
from ..planner.actor import with_actor
from ..planner.errors import InvalidInputError, NotFoundError, UnprocessableError
from . import ignore, ignore_repo, proposals
from .proposals import ConflictError
from .reports import TooManyReportsError

MAX_PENDING = {"project": 20, "task": 100, "change": 50, "ignore": 20,
               "windowRule": 50, "blockRule": 50, "blockAction": 200, "sessionChange": 50}  # 每租户待批准的条数（后三种：契约 v2.38「五」）
TIER_KINDS = ("windowRule", "blockRule", "blockAction")
_LABEL = {"change": "修改", "ignore": "忽略规则", "windowRule": "窗口规则", "blockRule": "块规则", "blockAction": "块操作", "sessionChange": "改时间"}
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


def _view(user: str, d: dict, human: bool = False) -> dict:
    if d["kind"] in TIER_KINDS:
        from . import tier_items  # noqa: PLC0415 —— tier_items 反过来 import 本文件

        return tier_items.view(d, human)
    if d["kind"] == "sessionChange":
        from . import session_items  # noqa: PLC0415

        return session_items.view(d)
    if d["kind"] == "change":
        return _change_view(d)
    if d["kind"] == "ignore":
        return _ignore_view(d, human)
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


def listing(pending_only: bool, limit: int = 100, human: bool = False) -> dict:
    """``{projects: [提议 + tasks[]], tasks: [挂在现成项目上的提议]}``。挂在列出的项目提议下的任务进它的 tasks。"""
    user = current_tenant()
    filt = {"user": user, **({"status": "pending"} if pending_only else {})}
    def rows_of(kinds):  # 每种各自取 limit 条：一种刷满不会把另一种挤出去
        return list(_col().find({**filt, "kind": {"$in": kinds}}, {"_id": 0}).sort("createdAt", -1).limit(limit))

    rows = rows_of(["project", "task"])
    chg, ign = rows_of(["change"]), rows_of(["ignore"])
    from . import session_items, tier_items  # noqa: PLC0415

    tier = {k: [tier_items.view(d, human) for d in rows_of([k])] for k in TIER_KINDS}
    projects = {d["id"]: {**_view(user, d), "tasks": []} for d in rows if d["kind"] == "project"}
    tasks = []
    for d in rows:
        if d["kind"] == "task":
            (projects[d["parentId"]]["tasks"] if d.get("parentId") in projects else tasks).append(_view(user, d))
    return {"projects": list(projects.values()), "tasks": tasks,
            "changes": [_change_view(d) for d in chg], "ignores": [_ignore_view(d, human) for d in ign],
            "windowRules": tier["windowRule"], "blockRules": tier["blockRule"], "blockActions": tier["blockAction"],
            "sessionChanges": [session_items.view(d) for d in rows_of(["sessionChange"])]}


def reject(item_id: str, human: bool = False) -> dict:
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
    return _view(user, _get(user, item_id), human)


def approve(item_id: str, request, human: bool = False, edit: dict | None = None) -> dict:
    """批准并建出来（幂等）：同一提议只建一次，并发批准复用它（做法同 ``proposals.task_for``）。
    ``edit``（v2.38）只对 windowRule / blockRule / blockAction 有意义：批准时更正，其余 kind 给了 → 422。"""
    user = current_tenant()
    d = _get(user, item_id)
    if d is None:
        raise NotFoundError(f"没有这条提议：{item_id!r}")
    if d["kind"] in TIER_KINDS:
        from . import tier_items  # noqa: PLC0415

        return tier_items.approve(user, d, request, edit)
    if d["kind"] == "sessionChange":
        from . import session_items  # noqa: PLC0415

        return session_items.approve(user, d, request, edit)
    if edit is not None:
        raise UnprocessableError("这种提议不能在批准时更正")
    if d["status"] == "rejected":
        raise ConflictError("这条提议已被否掉")
    if d["kind"] in ("change", "ignore"):
        return _approve_edit(user, d, request, human)
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


# ── v2.36：改名 / 搬移 / 删除 / 忽略规则的提议 ─────────────────────────────

def _target(target: str, target_id: str) -> dict | None:
    return (planner_service.get_project if target == "project" else planner_service.get_task)(target_id)


def _put_edit(user: str, kind: str, item_id: str, extra: dict, reason: str, capped: bool = True) -> dict:
    """取（或新建）提议：待批准 → 原样返回；被否掉 → 409；已批准 → 换成新的（东西可能又变回去了）。"""
    d = _get(user, item_id)
    if d and d["status"] == "rejected":
        raise ConflictError("用户已经否掉过这个提议，不要再提")
    if d and d["status"] == "pending":
        if "snapName" in extra and (d.get("snapName"), d.get("snapParent")) != (extra["snapName"], extra["snapParent"]):
            snap = {"snapName": extra["snapName"], "snapParent": extra["snapParent"]}  # 目标后来被改过：快照跟着刷新
            _col().update_one({"user": user, "id": item_id, "status": "pending"}, {"$set": snap})
            d = {**d, **snap}
        return d
    if capped and _col().count_documents({"user": user, "kind": kind, "status": "pending"}) >= MAX_PENDING[kind]:  # 直接施加不占待批准名额
        raise TooManyReportsError(f"已有 {MAX_PENDING[kind]} 条待批准的{_LABEL[kind]}提议，"
                                  "请先让用户处理旧的")
    doc = {"user": user, "id": item_id, "kind": kind, "status": "pending", "reason": reason, "createdAt": _now(),
           "applyClaimedAt": None, **extra}
    if d:  # accepted：整条换掉（条件删除，输家撞唯一索引就用先到的）；批准正在落地时不许换
        claimed = d.get("applyClaimedAt")
        if claimed is not None and (_now() - claimed.replace(tzinfo=timezone.utc)).total_seconds() < proposals.WAIT_SECONDS:
            raise ConflictError("这条提议正在批准、落地的过程中，请稍后再试")
        _col().delete_one({"user": user, "id": item_id, "status": {"$in": ["accepted", "superseded"]}, "applyClaimedAt": claimed})
    try:
        _col().insert_one(dict(doc))
    except DuplicateKeyError:
        return _get(user, item_id)
    return doc


def supersede(user: str, date: str | None, block_key: str, except_id: str | None = None) -> None:
    """人对这个块做了写操作：它上面待批准的 ``blockAction`` 提议记为 ``superseded``（不算被否掉，同样的可以再提）。``date`` 为 None = 各天。"""
    q = {"user": user, "kind": "blockAction", "status": "pending", "blockKey": block_key, **({"date": date} if date else {}),
         **({"id": {"$ne": except_id}} if except_id else {})}
    _col().update_many(q, {"$set": {"status": "superseded", "decidedAt": _now()}})


def _parent(target: str, cur: dict) -> str:
    return cur["zoneId"] if target == "project" else cur["projectId"]


def _clash(target: str, target_id: str, parent: str, name: str) -> dict | None:
    """``parent`` 下除自己外同名（归一化）的兄弟。"""
    sibs = planner_service.list_projects(parent) if target == "project" else planner_service.list_tasks(parent)
    n = proposals._norm(name)
    return next((x for x in sibs if x["id"] != target_id and proposals._norm(x["name"]) == n), None)


def propose_change(target: str, target_id: str, op: str, name: str | None, to_id: str | None, reason: str,
                   direct: bool = False, request=None) -> dict:
    from . import direct_log  # noqa: PLC0415 —— direct_log 反过来 import 本文件

    if direct:
        direct_log.gate("honeycombRecords")  # 先过闸门：不满足条件就 403，什么都不做
    user = current_tenant()
    cur = _target(target, target_id)
    label = "项目" if target == "project" else "任务"
    if cur is None:
        raise InvalidInputError(f"{label}不存在：{target_id!r}")
    if target == "project" and inbox.is_protected_project(target_id):
        raise InvalidInputError(f"项目 {target_id!r} 是系统收件箱，不能改")
    if target == "task" and unclassified.is_bucket(cur):
        raise InvalidInputError(f"任务 {target_id!r} 是「未分类」时间桶（系统任务），不能改")
    if (name is not None) != (op == "rename") or (to_id is not None) != (op == "move"):
        raise InvalidInputError("rename 只给 name，move 只给 toId，delete 都不给")
    key = ""
    if op == "rename":
        name = clean_name(name, f"{label}的 name")
        n = proposals._norm(name)
        if n == proposals._norm(cur["name"]):
            raise InvalidInputError("新名字和现在的一样")
        same = _clash(target, target_id, _parent(target, cur), name)
        if same is not None:
            raise ConflictError(f"同级已有同名{label} {same['id']!r}")
        key = n
    elif op == "move":
        dest, here = (planner_service.get_zone, cur["zoneId"]) if target == "project" else (planner_service.get_project, cur["projectId"])
        if dest(to_id) is None:
            raise InvalidInputError(f"{'zoneId' if target == 'project' else 'projectId'} 不存在：{to_id!r}")
        if to_id == here:
            raise InvalidInputError("已经在那里了")
        same = _clash(target, target_id, to_id, cur["name"])
        if same is not None:
            raise ConflictError(f"目的地已有同名{label} {same['id']!r}")
        key = to_id
    elif target == "project":
        left = [t for t in planner_service.list_tasks(target_id) if not unclassified.is_bucket(t)]
        if left:
            raise ConflictError(f"项目 {target_id!r} 下还有 {len(left)} 个任务，先提议删掉或移走它的任务")
    else:
        deps = planner_repo.find_dependents(target_id)
        if deps:
            raise ConflictError(f"任务 {target_id!r} 被 {len(deps)} 个任务依赖，不能提议删")
    item_id = "nc_" + hashlib.sha256(f"{target}\n{target_id}\n{op}\n{key}".encode()).hexdigest()[:20]
    pre = _get(user, item_id)
    temp = not (pre and pre["status"] == "pending")
    d = _put_edit(user, "change", item_id, {"target": target, "targetId": target_id, "op": op,
                                             "name": name, "toId": to_id, "snapName": cur["name"],
                                             "snapParent": _parent(target, cur)}, _clean_reason(reason), capped=not direct)
    if direct:  # 契约「五」：跳过待批准立即落地（仍走 guard.run_write，高风险写的二次设防照旧）
        if d["status"] == "rejected":
            raise ConflictError("用户已经否掉过这个提议，不要再提")

        def go():
            try:
                return _approve_edit(user, d, request, False), {"name": d["snapName"], "parent": d["snapParent"]}, None
            except Exception:
                if temp:  # 临时建的提议不留：落地没成就删掉（原来就有的待批准提议不动）
                    _col().delete_one({"user": user, "id": item_id, "status": "pending"})
                raise

        return direct_log.run("change", {"target": target, "targetId": target_id, "op": op, "name": name, "toId": to_id,
                                         "reason": _clean_reason(reason)}, go)
    return _change_view(d)


def propose_ignore(app: str, title_contains: str | None, reason: str, human: bool = False, direct: bool = False) -> dict:
    from . import direct_log  # noqa: PLC0415

    if direct:
        direct_log.gate("windowsClassify")  # v2.39「五」：忽略规则的直接施加归 windowsClassify
    user = current_tenant()
    app, title = ignore._clean(app), ignore._clean(title_contains)
    if not fold(app):
        raise InvalidInputError("app 不能为空")
    if len(app) > ignore.MAX_APP or len(title) > ignore.MAX_TITLE:
        raise InvalidInputError(f"app 至多 {ignore.MAX_APP}、titleContains 至多 {ignore.MAX_TITLE} 个字符")
    title = title if fold(title) else ""
    if any(r["id"] == ignore.rule_id(app, title) for r in ignore_repo.all_rules(user)):
        raise ConflictError("已经有这条忽略规则了")
    item_id = "ni_" + hashlib.sha256(f"{fold(app)}\n{fold(title)}".encode()).hexdigest()[:20]
    if direct:
        old = _get(user, item_id)
        if old and old["status"] == "rejected":
            raise ConflictError("用户已经否掉过这个提议，不要再提")

        def go():
            rule = ignore.add(app, title, "ai")
            shown = {k: v for k, v in rule.items() if k not in ("titleContains", "lastHitAt")}  # 标题不外露
            return {**shown, "hasTitleFilter": bool(rule["titleContains"])}, None, rule["id"]

        def finish():
            _col().update_many({"user": user, "id": item_id, "status": "pending"},
                               {"$set": {"status": "accepted", "decidedAt": _now(), "applyClaimedAt": None}})

        return direct_log.run("ignore", {"app": app, "titleContains": title or None, "reason": _clean_reason(reason)}, go, finish)
    d = _put_edit(user, "ignore", item_id, {"app": app, "titleContains": title or None, "createdId": None},
                  _clean_reason(reason))
    return _ignore_view(d, human)


def _change_view(d: dict) -> dict:  # snapName / snapParent 是内部快照，不外露
    cur = _target(d["target"], d["targetId"])
    to_name = None
    if d["op"] == "move":
        dest = (planner_service.get_zone if d["target"] == "project" else planner_service.get_project)(d["toId"])
        to_name = dest["name"] if dest else None
    return {"id": d["id"], "kind": "change", "status": d["status"], "op": d["op"], "target": d["target"],
            "targetId": d["targetId"], "targetName": cur["name"] if cur else None,
            "projectId": cur["projectId"] if cur and d["target"] == "task" else None,
            "name": d["name"], "toId": d["toId"], "toName": to_name, "reason": d["reason"],
            "createdAt": d["createdAt"].replace(tzinfo=timezone.utc).isoformat(), "createdId": None}


def _ignore_view(d: dict, human: bool) -> dict:
    out = {"id": d["id"], "kind": "ignore", "status": d["status"], "app": d["app"], "reason": d["reason"],
           "createdAt": d["createdAt"].replace(tzinfo=timezone.utc).isoformat(), "createdId": d.get("createdId")}
    # 同 ignore._out：非人拿不到匹配文字（可能是隐私窗口标题的一部分）
    return {**out, "titleContains": d["titleContains"]} if human else {**out, "hasTitleFilter": bool(d["titleContains"])}


def _precheck(d: dict) -> None:
    """批准前的 409 复查（目标还在 / 快照没变 / 没有同名）。"""
    if d["kind"] != "change":
        return
    cur = _target(d["target"], d["targetId"])
    if cur is None:
        raise ConflictError("要改的东西已不存在，批不了；请选「不要」")
    if (d["op"] == "rename" and cur["name"] != d.get("snapName")) or \
            (d["op"] == "move" and _parent(d["target"], cur) != d.get("snapParent")):
        raise ConflictError("目标已被改过，请选「不要」")
    label = "项目" if d["target"] == "project" else "任务"
    if d["op"] == "rename":
        same = _clash(d["target"], d["targetId"], _parent(d["target"], cur), d["name"])
    elif d["op"] == "move":
        same = _clash(d["target"], d["targetId"], d["toId"], cur["name"])
    else:
        same = None
    if same is not None:  # service 层不查同名：这里查，撞了不动手、留在待批准
        raise ConflictError(f"同级已有同名{label} {same['id']!r}，批不了；请选「不要」")


def _reflected(user: str, d: dict) -> str | None | bool:
    """崩溃后接手用：提议要的结果是不是已经在了。返回 False = 还没有；ignore 返回规则 id；其余 True。"""
    if d["kind"] == "ignore":
        rid = ignore.rule_id(d["app"], d["titleContains"] or "")
        return rid if any(r["id"] == rid for r in ignore_repo.all_rules(user)) else False
    cur = _target(d["target"], d["targetId"])
    if d["op"] == "delete":
        return cur is None
    if cur is None:
        return False
    return cur["name"] == d["name"] if d["op"] == "rename" else _parent(d["target"], cur) == d["toId"]


def _aged(claimed) -> bool:
    return claimed is not None and (_now() - claimed.replace(tzinfo=timezone.utc)).total_seconds() >= proposals.WAIT_SECONDS


def _approve_edit(user: str, d: dict, request, human: bool) -> dict:
    item_id = d["id"]
    if d["status"] == "pending":
        try:
            _precheck(d)
        except ConflictError:
            fresh = _get(user, item_id)  # 复查期间别人已批准并落地了：不是冲突，走输家路径看结果
            if fresh is None or fresh["status"] != "accepted":
                raise
    mark = _now()
    won = _col().find_one_and_update({"user": user, "id": item_id, "status": "pending"},
                                     {"$set": {"status": "accepted", "decidedAt": mark, "applyClaimedAt": mark}})
    if won is None:  # 别人先批了（或刚被否）
        cur_d = _get(user, item_id)
        if cur_d is None:
            raise NotFoundError(f"没有这条提议：{item_id!r}")
        if cur_d["status"] == "accepted" and _aged(cur_d.get("applyClaimedAt")):  # 上一个批准崩在半路：人再点一次 = 接手
            taken = _col().find_one_and_update(
                {"user": user, "id": item_id, "status": "accepted", "applyClaimedAt": cur_d["applyClaimedAt"]},
                {"$set": {"applyClaimedAt": mark}})
            if taken is not None:
                done = _reflected(user, cur_d)
                if done is not False:  # 其实已经落地了：只放占位、补 createdId
                    _col().update_one({"user": user, "id": item_id, "applyClaimedAt": mark},
                                      {"$set": {"applyClaimedAt": None, **({"createdId": done} if isinstance(done, str) else {})}})
                    return _view(user, _get(user, item_id), human)
                try:  # 没落地：接手前也要过一遍批准前的复查，不过就放回待批准
                    _precheck(cur_d)
                except Exception:
                    _release(user, item_id, mark)
                    raise
                return _apply(user, cur_d, request, mark, human)
        deadline = time.monotonic() + proposals.WAIT_SECONDS
        while cur_d is not None and cur_d["status"] == "accepted" and cur_d.get("applyClaimedAt") is not None:
            if time.monotonic() > deadline:
                raise ConflictError("这条提议正在批准、落地的过程中，请稍后再看")
            time.sleep(0.1)
            cur_d = _get(user, item_id)
        if cur_d is None:
            raise NotFoundError(f"没有这条提议：{item_id!r}")
        return _view(user, cur_d, human)
    return _apply(user, d, request, mark, human)


def _release(user: str, item_id: str, mark) -> None:
    """放回待批准；只在占位还是 ``mark`` 的时候，不碰别人换上来的新提议。"""
    _col().update_one({"user": user, "id": item_id, "status": "accepted", "applyClaimedAt": mark},
                      {"$set": {"status": "pending", "applyClaimedAt": None}, "$unset": {"decidedAt": ""}})


def _apply(user: str, d: dict, request, mark, human: bool) -> dict:
    """占到（或接手）占位之后动手。``mark`` = 本次的占位标记。"""
    item_id, change = d["id"], d["kind"] == "change"
    mine = {"user": user, "id": item_id, "status": "accepted", "applyClaimedAt": mark}
    created = None
    try:
        if not change:
            created = ignore.add(d["app"], d["titleContains"])["id"]
        elif d["op"] == "delete":
            guard.run_write(request, op=audit.OP_DELETE, object_type=d["target"] + "s", entity_id=d["targetId"], changes={},
                            action=lambda actor: (planner_service.delete_project if d["target"] == "project"
                                                  else planner_service.delete_task)(d["targetId"]))
        else:
            fields = {"name": d["name"]} if d["op"] == "rename" else \
                {"zoneId" if d["target"] == "project" else "projectId": d["toId"]}
            guard.run_write(request, op=audit.OP_UPDATE, object_type=d["target"] + "s", entity_id=d["targetId"],
                            changes=fields, action=lambda actor: (planner_service.update_project if d["target"] == "project"
                                                                  else planner_service.update_task)(
                                d["targetId"], with_actor(fields, actor)))
    except Exception:
        try:  # 抛错但结果其实已经在了（如删除时目标刚好没了）：当作做完
            done = _reflected(user, d)
        except Exception:  # noqa: BLE001
            done = False
        if done is False:  # 没落地：放回待批准
            _release(user, item_id, mark)
            raise
        created = done if isinstance(done, str) else None
    # 已落地：记结果、放占位；这一步失败不回滚（东西已经改了）
    _col().update_one(mine, {"$set": {"applyClaimedAt": None, **({"createdId": created} if created else {})}})
    return _view(user, _get(user, item_id), human)
