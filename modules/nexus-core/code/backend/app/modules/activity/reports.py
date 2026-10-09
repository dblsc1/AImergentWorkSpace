"""AI 报告（契约 v2.20「AI 报告」节，唯一事实源）。

仓主 2026-10-09：「AI 一次提交一份报告，我可以一键批准全部。当然单条编辑还是保留的」。

**AI 只提议，一条都不替人定**：报告里每一条都是**已有**的某种动作（记到项目 / 现成任务、提议新任务并记进去、忽略），
提交时只校验、存起来；人批准时逐条走**同一个** ``service.confirm`` / ``service.dismiss`` / ``proposals.task_for``——
没有另一条写路径，事实与事件跟手点一模一样，只在事件的 ``ai`` 块多一个出处 ``report {id, author}``。

- 并发 / 幂等不另加锁：每段的结果只增不改（``reports_repo.set_results``），每段入账靠 ``confirm`` 自己的防重键与条件占位；
  ``newTask`` 条的任务由 ``proposals.task_for`` 保证只建一个。批准一次的工作量以 ``MAX_REFS`` 为界。
- ponytail: 「待批准报告至多 5 份」是先数后插，并发提交最多超出并发数；要硬上限得加计数文档。
- ponytail: 人在「全部批准」进行中点同一条的「不要」，条会记 rejected，但已入账的段结果照实留在 ``results`` 里。
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictStr, ValidationError, model_validator

from ... import config
from ...tenant import current as current_tenant
from ..planner import service as planner_service
from ..planner.errors import ForbiddenError, InvalidInputError, NotFoundError
from ..timer.service import UnknownTaskError
from . import proposals, repo, reports_repo, service

log = logging.getLogger("uvicorn.error")

MAX_ITEMS = 200  # 每份报告的条数（超了整个请求 422）
MAX_ITEM_REFS = 200  # 每条的建议数
MAX_REFS = 500  # 每份报告所有条的建议数合计：「全部批准」的工作量上界
MAX_SUMMARY = 2000
MAX_REASON = 300  # 码点
MAX_AUTHOR = 64
MAX_PENDING_REPORTS = 5  # 每租户同时待批准的报告
DEFAULT_AUTHOR = "ai"
_LIST_LIMIT = 50

# 批准时「做不成」的域错误：写进该段的 failed、不挡别的段；别的异常记日志、对外只说内部错误
_DOMAIN = (NotFoundError, InvalidInputError, ForbiddenError, UnknownTaskError, service.ConflictError)


class TooManyReportsError(RuntimeError):
    """别的作者已有 MAX_PENDING_REPORTS 份待批准。main.py 映射成 429。"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ------------------------------------------------ 提交

class _NewTask(BaseModel):
    model_config = ConfigDict(extra="forbid")

    projectId: Annotated[StrictStr, Field(min_length=1, max_length=128)]
    name: Annotated[StrictStr, Field(max_length=1024)]  # 1–64 码点的判据在 proposals.clean_name


class _Item(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["assign", "newTask", "dismiss"]
    suggestionIds: list[Annotated[StrictStr, Field(min_length=1, max_length=64)]] | None = Field(
        None, min_length=1, max_length=MAX_ITEM_REFS)
    collection: Annotated[StrictStr, Field(max_length=1024)] | None = None  # 名字的判据在 proposals.collection
    taskId: Annotated[StrictStr, Field(min_length=1, max_length=128)] | None = None
    projectId: Annotated[StrictStr, Field(min_length=1, max_length=128)] | None = None
    newTask: _NewTask | None = None
    reason: StrictStr = Field("", max_length=MAX_REASON)

    @model_validator(mode="after")
    def _shape(self):
        if (self.suggestionIds is None) == (self.collection is None):
            raise ValueError("suggestionIds 与 collection 必须二选一")
        targets = sum(x is not None for x in (self.taskId, self.projectId, self.newTask))
        want = {"assign": "taskId 与 projectId 恰好给一个", "newTask": "只给 newTask", "dismiss": "不给任何目标"}
        ok = {"assign": targets == 1 and self.newTask is None, "newTask": targets == 1 and self.newTask is not None,
              "dismiss": targets == 0}[self.kind]
        if not ok:
            raise ValueError(f"kind={self.kind} 时{want[self.kind]}")
        return self


def _bad(code: str, reason: str) -> tuple[str, str]:
    return code, reason


def _check_item(user: str, it: _Item, seen: set[str], now: datetime) -> tuple[dict | None, tuple[str, str] | None]:
    """校验一条并解析成存下的形状。返回 ``(条, None)`` 或 ``(None, (code, 理由))``。
    引用查不到（不存在 / 别的租户的 / 已不是待确认）一律同一句话，不给存在性预言机。"""
    if it.collection is not None:
        try:
            key = proposals.collection(it.collection)["key"]
        except ValueError as exc:
            return None, _bad("invalid_item", str(exc))
        ids = reports_repo.pending_in_collection(user, key, MAX_ITEM_REFS + 1)
        if not ids:
            return None, _bad("unknown_suggestion", "这个集合里没有待确认的建议")
        if len(ids) > MAX_ITEM_REFS:
            return None, _bad("invalid_item", f"集合里的建议超过 {MAX_ITEM_REFS} 个，请用 suggestionIds 分几条交")
    else:
        ids = list(dict.fromkeys(it.suggestionIds))
    docs = reports_repo.suggestions(user, ids)
    for sid in ids:
        if sid not in docs or docs[sid]["status"] != "pending":
            return None, _bad("unknown_suggestion", f"建议不存在或已不是待确认：{sid}")
    if any(sid in seen for sid in ids):
        return None, _bad("duplicate_suggestion", "同一个建议在这份报告里已出现在别的条里")
    if len(seen) + len(ids) > MAX_REFS:
        return None, _bad("too_many_refs", f"一份报告最多 {MAX_REFS} 个建议，其余请下次再交")
    out = {"kind": it.kind, "suggestionIds": ids, "reason": it.reason, "status": "pending", "results": {}}
    if it.taskId is not None:
        if planner_service.get_task(it.taskId) is None:
            return None, _bad("unknown_task", f"任务不存在：{it.taskId}")
        out["taskId"] = it.taskId
    if it.projectId is not None:
        if planner_service.get_project(it.projectId) is None:
            return None, _bad("unknown_project", f"项目不存在：{it.projectId}")
        out["projectId"] = it.projectId
    if it.newTask is not None:
        # 同 matches 的 newTask：同样的校验与去重，生成待定提议（人批准时才建任务）
        nt, why = proposals.propose(user, repo.get(user, ids[0]), it.newTask.projectId, it.newTask.name, now)
        if why:
            return None, _bad("newtask_refused", why)
        out["newTask"] = nt
    seen.update(ids)
    return out, None


def _err(exc: ValidationError) -> str:
    e = exc.errors()[0]
    return f"{'.'.join(str(p) for p in e['loc']) or '<root>'}: {e['msg']}"


def submit(summary: str, author: str, raw_items: list[Any]) -> dict:
    user, now = current_tenant(), _now()
    reports_repo.purge(user, now - timedelta(days=config.settings.suggestion_ttl_days))
    # author 只是自报的显示标签（谁都能写成别人的名字）：不参与任何判定。身份只有租户（网关覆盖的头）
    if reports_repo.pending_count(user) >= MAX_PENDING_REPORTS:
        raise TooManyReportsError(f"已有 {MAX_PENDING_REPORTS} 份待批准的报告，请先让用户处理旧的")
    items, rejected, seen = [], [], set()
    for index, raw in enumerate(raw_items):
        try:
            it = _Item.model_validate(raw)
        except ValidationError as exc:
            rejected.append({"index": index, "code": "invalid_item", "reason": _err(exc)})
            continue
        item, why = _check_item(user, it, seen, now)
        if why:
            rejected.append({"index": index, "code": why[0], "reason": why[1]})
        else:
            items.append({"id": f"i{index}", **item})  # 条 id 取提交时的下标：AI 能对上 rejected / 结果
    if not items:
        return {"reportId": None, "status": None, "accepted": 0, "rejected": rejected}
    rid = "rp_" + uuid.uuid4().hex[:12]
    reports_repo.insert({"user": user, "id": rid, "author": author, "summary": summary, "status": "pending",
                         "createdAt": now, "decidedAt": None, "items": items})
    return {"reportId": rid, "status": "pending", "accepted": len(items), "rejected": rejected}


# ------------------------------------------------ 读

def _count(item: dict) -> dict:
    c = {"applied": 0, "stale": 0, "failed": 0}
    for r in item.get("results", {}).values():
        c[r["state"]] += 1
    return c


def _failure(item: dict) -> str | None:
    return next((r.get("reason") for r in item.get("results", {}).values() if r["state"] == "failed"), None)


def _item_view(it: dict, docs: dict | None) -> dict:
    out = {"id": it["id"], "kind": it["kind"], "status": it["status"], "reason": it["reason"],
           "taskId": it.get("taskId"), "projectId": it.get("projectId"), "newTask": it.get("newTask"),
           "results": it["results"], **_count(it), "failure": _failure(it)}
    if docs is not None:
        out["suggestions"] = [{k: docs[s][k] for k in ("id", "app", "title", "startAt", "durationSeconds", "status")}
                              for s in it["suggestionIds"] if s in docs]
        out["seconds"] = sum(s["durationSeconds"] for s in out["suggestions"])
        out["staleNow"] = sum(1 for s in it["suggestionIds"] if s not in docs or docs[s]["status"] != "pending")
    return out


def _iso(t: datetime) -> str:
    return (t if t.tzinfo else t.replace(tzinfo=timezone.utc)).isoformat()  # mongo 取回的是不带时区的 UTC


def _view(doc: dict, docs: dict | None, with_items: bool = True) -> dict:
    items = [_item_view(it, docs) for it in doc["items"]]
    by_kind: dict[str, int] = {}
    for it in doc["items"]:
        by_kind[it["kind"]] = by_kind.get(it["kind"], 0) + 1
    counts = {"items": len(items), "pending": sum(1 for it in items if it["status"] == "pending"), "byKind": by_kind}
    if docs is not None:
        counts["seconds"] = sum(it["seconds"] for it in items)
    out = {k: doc[k] for k in ("id", "author", "summary", "status", "createdAt", "decidedAt")}
    out.update(createdAt=_iso(doc["createdAt"]), decidedAt=_iso(doc["decidedAt"]) if doc["decidedAt"] else None,
               counts=counts)
    if with_items:
        out["items"] = items
    return out


def _load(user: str, report_id: str) -> dict:
    doc = reports_repo.get(user, report_id)
    if doc is None:
        raise NotFoundError(f"报告不存在：{report_id!r}")
    return doc


def listing(pending_only: bool, limit: int, with_items: bool = False) -> dict:
    user = current_tenant()
    reports_repo.purge(user, _now() - timedelta(days=config.settings.suggestion_ttl_days))
    limit = _LIST_LIMIT if limit <= 0 else min(limit, _LIST_LIMIT)
    return {"items": [_view(d, None, with_items) for d in reports_repo.page(user, pending_only, limit)]}


def get(report_id: str, resolve: bool) -> dict:
    user = current_tenant()
    doc = _load(user, report_id)
    docs = reports_repo.suggestions(user, [s for it in doc["items"] for s in it["suggestionIds"]]) if resolve else None
    return _view(doc, docs)


# ------------------------------------------------ 批准 / 不要

def _status_of(item: dict) -> str:
    """逐段结果 → 条的状态：有失败的 failed（可重试，已成功的不重做）；否则有入账的 applied；否则（全是已被处理）stale。"""
    states = {item["results"].get(s, {}).get("state") for s in item["suggestionIds"]}
    if "failed" in states:
        return "failed"
    if None in states:
        return "pending"
    return "applied" if "applied" in states else "stale"


def _why(exc: Exception) -> str:
    return str(exc)[:300]


def _apply_item(user: str, report: dict, item: dict, request) -> None:
    """对条里每个还没入账的建议做一次，结果落库。一段失败不挡别的段。"""
    sids = [s for s in item["suggestionIds"] if item["results"].get(s, {}).get("state") != "applied"]
    docs = reports_repo.suggestions(user, sids)
    results: dict[str, dict] = {}
    live = []
    for sid in sids:
        if sid in docs and docs[sid]["status"] == "pending":
            live.append(sid)
        else:
            results[sid] = {"state": "stale", "reason": "建议已被处理或已过期"}
    task_id, broken = item.get("taskId"), None
    if live and item["kind"] == "newTask":
        nt = item["newTask"]
        try:  # 同一提议只建一次任务（planner 写入口：判来源、留审计）
            task_id = proposals.task_for(user, nt["proposalId"], nt["name"], request)
        except _DOMAIN as exc:
            broken = _why(exc)
    prov = {"id": report["id"], "author": report["author"]}
    for sid in live:
        if broken:
            results[sid] = {"state": "failed", "reason": broken}
            continue
        try:
            if item["kind"] == "dismiss":
                service.dismiss(sid)
                results[sid] = {"state": "applied"}
            else:
                out = service.confirm(sid, task_id, "do", request=request, project_id=item.get("projectId"), report=prov)
                # duplicate = 别的确认（手点 / 并发的批准）抢先写了这一段
                results[sid] = {"state": "stale", "reason": "已被别处确认"} if out["duplicate"] else {"state": "applied"}
        except service.ConflictError as exc:
            results[sid] = {"state": "stale", "reason": _why(exc)}
        except _DOMAIN as exc:
            results[sid] = {"state": "failed", "reason": _why(exc)}
        except Exception:  # noqa: BLE001 —— 一段的意外不许挡住别的段
            log.exception("AI 报告 %s 条 %s 的建议 %s 批准失败", report["id"], item["id"], sid)
            results[sid] = {"state": "failed", "reason": "内部错误"}
    reports_repo.set_results(user, report["id"], item["id"], results)
    fresh = next(i for i in _load(user, report["id"])["items"] if i["id"] == item["id"])
    reports_repo.set_item_status(user, report["id"], item["id"], _status_of(fresh))


def _finalize(user: str, report_id: str) -> dict:
    """没有 pending / failed 的条了 → 报告收尾（全被不要 = rejected，其余 approved）。返回最新文档。"""
    doc = _load(user, report_id)
    sts = [i["status"] for i in doc["items"]]
    if doc["status"] == "pending" and not any(s in reports_repo.OPEN for s in sts):
        reports_repo.finish(user, report_id, "rejected" if all(s == "rejected" for s in sts) else "approved", _now())
        doc = _load(user, report_id)
    return doc


def _brief(it: dict) -> dict:
    return {"id": it["id"], "status": it["status"], **_count(it), "failure": _failure(it)}


def approve_all(report_id: str, request) -> dict:
    user = current_tenant()
    doc = _load(user, report_id)
    if doc["status"] not in ("pending", "approved"):
        raise service.ConflictError(f"报告已{'不要' if doc['status'] == 'rejected' else '作废'}，不能批准")
    done = {"applied": 0, "stale": 0, "failed": 0}
    for it in doc["items"]:
        if it["status"] in reports_repo.OPEN:
            _apply_item(user, doc, it, request)
            now_item = next(i for i in _load(user, report_id)["items"] if i["id"] == it["id"])
            if now_item["status"] in done:
                done[now_item["status"]] += 1
    doc = _finalize(user, report_id)
    return {"id": report_id, "status": doc["status"], **done, "items": [_brief(i) for i in doc["items"]]}


def _item_of(doc: dict, item_id: str) -> dict:
    it = next((i for i in doc["items"] if i["id"] == item_id), None)
    if it is None:
        raise NotFoundError(f"报告里没有这一条：{item_id!r}")
    return it


def _retarget(user: str, doc: dict, it: dict, task_id: str | None, project_id: str | None) -> None:
    """人改目标：存下（之后批准用的就是它）。这一条就成了 assign。"""
    if (task_id is None) == (project_id is None):
        raise InvalidInputError("taskId 与 projectId 必须恰好给一个")
    if it["kind"] == "dismiss":
        raise InvalidInputError("「忽略」的条不能改目标")
    if it["status"] not in reports_repo.OPEN or doc["status"] != "pending":
        raise service.ConflictError("这一条已处理，不能再改")
    if task_id is not None and planner_service.get_task(task_id) is None:
        raise NotFoundError(f"任务不存在：{task_id!r}")
    if project_id is not None and planner_service.get_project(project_id) is None:
        raise NotFoundError(f"项目不存在：{project_id!r}")
    fields = {"kind": "assign", "taskId": task_id, "projectId": project_id}
    if not reports_repo.set_target(user, doc["id"], it["id"], fields, ["newTask"]):
        raise service.ConflictError("这一条刚被改动，请刷新后再改")


def approve_item(report_id: str, item_id: str, task_id: str | None, project_id: str | None, request) -> dict:
    user = current_tenant()
    doc = _load(user, report_id)
    it = _item_of(doc, item_id)
    if task_id is not None or project_id is not None:
        _retarget(user, doc, it, task_id, project_id)
        doc = _load(user, report_id)
        it = _item_of(doc, item_id)
    if doc["status"] not in ("pending", "approved"):
        raise service.ConflictError(f"报告已{'不要' if doc['status'] == 'rejected' else '作废'}，不能批准")
    if it["status"] in reports_repo.OPEN:
        _apply_item(user, doc, it, request)
    doc = _finalize(user, report_id)
    return {"reportId": report_id, "reportStatus": doc["status"], **_brief(_item_of(doc, item_id))}


def _reject_proposal(user: str, item: dict) -> None:
    if item["kind"] == "newTask":  # 同 unmatch：人说「不要」就记住，助理不许再提
        repo.proposal_reject_if_unused(user, item["newTask"]["proposalId"], _now())


def reject_item(report_id: str, item_id: str) -> dict:
    user = current_tenant()
    doc = _load(user, report_id)
    it = _item_of(doc, item_id)
    if doc["status"] != "pending" and it["status"] != "rejected":
        raise service.ConflictError("报告已处理，不能再不要这一条")
    if reports_repo.set_item_status(user, report_id, item_id, "rejected"):
        _reject_proposal(user, it)
    elif it["status"] != "rejected":
        raise service.ConflictError(f"这一条已{it['status']}，不能再不要")
    doc = _finalize(user, report_id)
    return {"reportId": report_id, "reportStatus": doc["status"], **_brief(_item_of(doc, item_id))}


def reject_report(report_id: str) -> dict:
    user = current_tenant()
    doc = _load(user, report_id)
    if doc["status"] == "rejected":
        return {"id": report_id, "status": "rejected"}
    if doc["status"] != "pending":
        raise service.ConflictError(f"报告已{'批准' if doc['status'] == 'approved' else '作废'}，不能整份不要")
    for it in doc["items"]:
        if it["status"] in reports_repo.OPEN:
            _reject_proposal(user, it)
    reports_repo.reject_open_items(user, report_id)
    reports_repo.finish(user, report_id, "rejected", _now())
    return {"id": report_id, "status": _load(user, report_id)["status"]}
