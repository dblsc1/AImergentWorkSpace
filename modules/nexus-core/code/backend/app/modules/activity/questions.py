"""v2.39 4.2 问人 ``activity_questions``（契约「MCP v0.5 的后端」4.2）。不是事实。

AI 问、人答；选了带 ``target`` 的选项且问题带时段 → 走「补登」的代码路径（``timer.service.backfill``）记一段，
dedupeKey 用 ``question:<id>``：一个问题只可能记一次。补登拒绝原样透传，问题留 ``pending``。
契约待定：``answer.option`` 记选项下标（没选 = null）；``date`` 没给 = null（不从 from 推）；
契约待定：``from``/``to`` 时刻不带偏移 → 400；``to ≤ 现在`` 按服务端时钟（不留容差）；
契约待定：补登的 dedupeKey 用 ``question:<id>:<taskId>``（契约写 ``question:<id>``）：同一问题换了选项也不会撞上前一次的事件；回答先在问题上原子地记下所选选项（``claimed``），
崩溃后重答必须选同一个选项，否则 409；补登被拒则放掉 ``claimed``。同一 ``askKey`` 的 pending 有部分唯一索引，并发提交撞上就用先到的。
契约待定：回答的校验顺序：404 → 非 pending 409 → 不允许文本却给了 text 400 → 下标 400 → 什么都没给 400。
契约待定：问题的 ``title``/``label`` 首尾空白去掉再算长度；选项 ``ignore`` 只能是 ``true``（给 ``false`` 当没给）。
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import date as Date, datetime, timedelta, timezone

from pymongo.errors import DuplicateKeyError

from ...repo import get_db
from ...tenant import current as current_tenant
from ..planner import service as planner_service
from ..planner.errors import InvalidInputError, NotFoundError
from ..timer import reassign as reassign_impl
from ..timer import service as timer_service
from . import proposals
from .proposals import ConflictError
from .reports import TooManyReportsError

_COLL, MAX_PENDING, MAX_BATCH, MAX_OPTIONS = "activity_questions", 30, 10, 6
CLAIM_TTL = timedelta(minutes=2)  # 回答崩在半路留下的占位，过了这么久撤下可以无视
_ready = False


def _col():
    global _ready
    col = get_db()[_COLL]
    if not _ready:
        col.create_index([("user", 1), ("id", 1)], unique=True, name="uniq_user_id")
        col.create_index([("user", 1), ("askKey", 1)], unique=True, name="uniq_pending_askkey", partialFilterExpression={"status": "pending"})
        _ready = True
    return col


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _str(v, what: str, lo: int, hi: int) -> str:
    if not isinstance(v, str) or not lo <= len(v.strip()) <= hi:
        raise InvalidInputError(f"{what} 须是 {lo}–{hi} 个字符的字符串")
    return v.strip()


def _instant(v, what: str) -> datetime:
    try:
        t = datetime.fromisoformat(v)
    except (TypeError, ValueError):
        raise InvalidInputError(f"{what} 须是带偏移的 ISO8601 时刻：{v!r}") from None
    if t.tzinfo is None:
        raise InvalidInputError(f"{what} 缺少时区偏移：{v!r}")
    return t


def _option(o) -> dict:
    if not isinstance(o, dict) or set(o) - {"label", "target", "ignore"}:
        raise InvalidInputError("选项形如 {label, target?, ignore?}")
    out = {"label": _str(o.get("label"), "label", 1, 40), "target": None, "ignore": False}
    t = o.get("target")
    if t is not None:
        if not isinstance(t, dict) or len(t) != 1 or next(iter(t)) not in ("taskId", "projectId") or not isinstance(next(iter(t.values())), str):
            raise InvalidInputError("target 形如 {taskId} 或 {projectId}")
        (kind, tid), = t.items()
        if (planner_service.get_task if kind == "taskId" else planner_service.get_project)(tid) is None:
            raise InvalidInputError(f"target 不存在：{t!r}")
        out["target"] = {kind: tid}
    if o.get("ignore") is not None and not isinstance(o["ignore"], bool):
        raise InvalidInputError("ignore 须是 bool")
    if o.get("ignore"):
        if out["target"]:
            raise InvalidInputError("target 与 ignore 至多一个")
        out["ignore"] = True
    return out


def _question(q) -> dict:
    if not isinstance(q, dict) or set(q) - {"title", "text", "date", "from", "to", "options", "allowText"}:
        raise InvalidInputError("问题形如 {title, text?, date?, from?, to?, options?, allowText?}")
    title = _str(q.get("title"), "title", 1, 120)
    text = "" if q.get("text") is None else q["text"]
    if not isinstance(text, str) or len(text) > 1000:
        raise InvalidInputError("text 至多 1000 个字符")
    day = None if q.get("date") is None else _date(q["date"])
    if (q.get("from") is None) != (q.get("to") is None):
        raise InvalidInputError("from 与 to 须同时给或同时不给")
    lo = hi = None
    if q.get("from") is not None:
        lo, hi = _instant(q["from"], "from"), _instant(q["to"], "to")
        if not lo < hi <= _now():
            raise InvalidInputError("须 from < to ≤ 现在")
        if hi - lo > timedelta(hours=24):
            raise InvalidInputError("from 到 to 至多 24 小时")
    opts = q.get("options") or []
    if not isinstance(opts, list) or len(opts) > MAX_OPTIONS:
        raise InvalidInputError(f"options 至多 {MAX_OPTIONS} 个")
    allow = q.get("allowText", False)
    if not isinstance(allow, bool):
        raise InvalidInputError("allowText 须是 bool")
    return {"title": title, "text": text, "date": day, "from": q.get("from"), "to": q.get("to"),
            "options": [_option(o) for o in opts], "allowText": allow, "_lo": lo, "_hi": hi}


def _date(v) -> str:
    try:
        return Date.fromisoformat(v).isoformat()
    except (TypeError, ValueError):
        raise InvalidInputError(f"date 须是 YYYY-MM-DD：{v!r}") from None


def _ask_key(q: dict) -> str:
    opts = "\n".join(f"{proposals._norm(o['label'])}|{o['target'] or ''}|{o['ignore']}" for o in q["options"])  # noqa: SLF001
    iso = lambda t: t.astimezone(timezone.utc).isoformat() if t else ""  # noqa: E731
    return hashlib.sha256(f"{proposals._norm(q['title'])}\n{q['date'] or ''}\n{iso(q['_lo'])}\n{iso(q['_hi'])}\n{opts}".encode()).hexdigest()  # noqa: SLF001


def view(d: dict) -> dict:
    a = d.get("answer")
    return {"id": d["id"], "status": d["status"], "title": d["title"], "text": d["text"], "date": d["date"],
            "from": d["from"], "to": d["to"], "options": d["options"], "allowText": d["allowText"],
            "createdAt": d["createdAt"].replace(tzinfo=timezone.utc).isoformat(),
            "answer": None if a is None else {**a, "at": a["at"].replace(tzinfo=timezone.utc).isoformat()}}


def submit(questions) -> dict:
    if not isinstance(questions, list) or not 1 <= len(questions) <= MAX_BATCH:
        raise InvalidInputError(f"questions 须有 1..{MAX_BATCH} 条")
    user, parsed = current_tenant(), [_question(q) for q in questions]
    keys = [_ask_key(q) for q in parsed]
    found = {d["askKey"]: d for d in _col().find({"user": user, "status": "pending", "askKey": {"$in": keys}}, {"_id": 0})}
    fresh = len(set(keys) - set(found))
    if fresh and _col().count_documents({"user": user, "status": "pending"}) + fresh > MAX_PENDING:  # ponytail: 先数后插，并发提交最多超出并发数
        raise TooManyReportsError(f"已有太多待回答的问题（上限 {MAX_PENDING}），请先让用户处理旧的")
    out = []
    for q, key in zip(parsed, keys):
        if key not in found:
            found[key] = {"user": user, "id": "aq_" + secrets.token_hex(8), "askKey": key, "status": "pending", "createdAt": _now(),
                          "answer": None, **{k: v for k, v in q.items() if not k.startswith("_")}}
            for _ in range(3):
                try:
                    _col().insert_one(dict(found[key]))
                    break
                except DuplicateKeyError:  # 并发的同一个问题：用先到的；先到的已被答掉 / 撤下（查不到 pending）就再插一次
                    first = _col().find_one({"user": user, "askKey": key, "status": "pending"}, {"_id": 0})
                    if first is not None:
                        found[key] = first
                        break
            else:
                raise ConflictError("问题正被并发提交，请重试")
        out.append(view(found[key]))
    return {"items": out}


def listing(status: str, limit: int) -> dict:
    q = {"user": current_tenant(), **({"status": "pending"} if status == "pending" else {})}
    return {"items": [view(d) for d in _col().find(q, {"_id": 0}).sort([("createdAt", -1), ("_id", -1)]).limit(limit)]}


def _get(qid: str) -> dict:
    d = _col().find_one({"user": current_tenant(), "id": qid}, {"_id": 0})
    if d is None:
        raise NotFoundError(f"没有这个问题：{qid!r}")
    return d


def answer(qid: str, option, text, request) -> dict:
    d = _get(qid)
    if d["status"] != "pending":
        raise ConflictError("这个问题已经回答或撤下了")
    if text is not None and (not isinstance(text, str) or len(text) > 1000):
        raise InvalidInputError("text 至多 1000 个字符")
    if text is not None and not d["allowText"]:
        raise InvalidInputError("这个问题不收文字回答（allowText 为 false）")
    if option is not None and (isinstance(option, bool) or not isinstance(option, int) or not 0 <= option < len(d["options"])):
        raise InvalidInputError(f"option 须是 0..{len(d['options']) - 1} 的下标")
    if option is None and text is None:
        raise InvalidInputError("option 与 text 至少给一个")
    user = current_tenant()
    target = d["options"][option]["target"] if option is not None else None
    record = bool(target and d["from"])
    seconds = int((datetime.fromisoformat(d["to"]) - datetime.fromisoformat(d["from"])).total_seconds()) if record else 0
    if record:  # 先把能校验的都校验了，再动任何东西（建桶、占位）
        if seconds <= 0:
            raise InvalidInputError("时段不足 1 秒，补登不了")
        if "projectId" in target and planner_service.get_project(target["projectId"]) is None:
            raise NotFoundError(f"项目不存在：{target['projectId']!r}")
    # 原子地记下所选选项：崩溃后重答必须选同一个，否则 409（补登的 dedupeKey 跟着目标走，换选项可能记两次）
    if _col().update_one({"user": user, "id": qid, "status": "pending", "$or": [{"claimed": None}, {"claimed.option": option}]},
                         {"$set": {"claimed": {"option": option, "at": _now()}}}).matched_count != 1:
        raise ConflictError("这个问题正在回答（或上次回答中断），请选同一个选项；已回答 / 撤下的不能再答")
    event_id = None
    try:
        if record:
            task_id = target.get("taskId") or reassign_impl._bucket(target["projectId"], request)  # noqa: SLF001 —— 桶只在补登之前一刻取或建
            got = timer_service.backfill(task_id, d["from"], seconds, dedupe_key=f"question:{qid}:{task_id}")  # 拒绝（404 / 400）原样抛出，问题留 pending
            event_id = got["event"]["id"]
    except Exception:
        _col().update_one({"user": user, "id": qid, "status": "pending"}, {"$set": {"claimed": None}})
        raise
    ans = {"option": option, "text": text, "at": _now(), "recorded": event_id is not None, "eventId": event_id}
    if _col().update_one({"user": user, "id": qid, "status": "pending"}, {"$set": {"status": "answered", "answer": ans}}).modified_count != 1:
        raise ConflictError("这个问题刚被别处回答或撤下了")
    return view(_get(qid))


def dismiss(qid: str) -> dict:
    _get(qid)
    # claimed 为空（或占位已过期）才撤：刚被 answer 占位（可能正在补登）的不能撤，否则补登照记、回答却 409
    if _col().update_one({"user": current_tenant(), "id": qid, "status": "pending", "$or": [{"claimed": None}, {"claimed.at": {"$lt": _now() - CLAIM_TTL}}, {"claimed.at": {"$exists": False}}]},
                          {"$set": {"status": "dismissed"}}).modified_count != 1:
        raise ConflictError("这个问题已经回答、撤下，或正在回答")
    return view(_get(qid))
