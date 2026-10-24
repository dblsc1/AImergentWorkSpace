"""v2.39 4.1 秘书报告 ``activity_secretary_reports``（契约「MCP v0.5 的后端」4.1）。不是事实：不进台账与投影。

同一 ``date`` 只留一份，再交 = 整份替换（``revision`` +1）。
契约待定：``author`` 缺省 ``"secretary"``；``from``/``to`` 都给时按 ``from ≤ to`` 且含两端共 ≤ 92 天；只给一个 → 400。
"""

from __future__ import annotations

import secrets
from datetime import date as Date, datetime, timedelta, timezone

from pymongo import ReturnDocument

from ...repo import get_db
from ...tenant import current as current_tenant
from ..planner.errors import InvalidInputError
from . import blocks

_COLL, MAX_TEXT, MAX_AUTHOR, MAX_DAYS = "activity_secretary_reports", 8000, 64, 92
_ready = False


def _col():
    global _ready
    col = get_db()[_COLL]
    if not _ready:
        col.create_index([("user", 1), ("date", 1)], unique=True, name="uniq_user_date")
        _ready = True
    return col


def parse_date(raw, what: str = "date") -> Date:
    try:
        return Date.fromisoformat(raw)
    except (TypeError, ValueError):
        raise InvalidInputError(f"{what} 须是 YYYY-MM-DD：{raw!r}") from None


def _view(d: dict) -> dict:
    iso = lambda t: t.replace(tzinfo=timezone.utc).isoformat()  # noqa: E731
    return {"id": d["id"], "date": d["date"], "text": d["text"], "author": d["author"],
            "createdAt": iso(d["createdAt"]), "updatedAt": iso(d["updatedAt"]), "revision": d["revision"]}


def submit(date: str | None, text, author: str | None) -> dict:
    day = parse_date(date).isoformat() if date is not None else blocks.today().isoformat()
    if not isinstance(text, str) or not 1 <= len(text) <= MAX_TEXT:
        raise InvalidInputError(f"text 须是 1–{MAX_TEXT} 个字符")
    author = "secretary" if author is None else author
    if not isinstance(author, str) or len(author) > MAX_AUTHOR:
        raise InvalidInputError(f"author 至多 {MAX_AUTHOR} 个字符")
    user, now = current_tenant(), datetime.now(timezone.utc)
    got = _col().find_one_and_update(
        {"user": user, "date": day},
        {"$set": {"text": text, "author": author, "updatedAt": now}, "$inc": {"revision": 1},
         "$setOnInsert": {"id": "sr_" + secrets.token_hex(8), "createdAt": now}},
        upsert=True, return_document=ReturnDocument.AFTER, projection={"_id": 0})
    return _view(got)


def listing(date: str | None, from_: str | None, to: str | None) -> dict:
    if date is not None and (from_ is not None or to is not None):
        raise InvalidInputError("date 与 from/to 只能给一种")
    if date is not None:
        lo = hi = parse_date(date)
    elif from_ is not None or to is not None:
        if from_ is None or to is None:
            raise InvalidInputError("from 与 to 须同时给")
        lo, hi = parse_date(from_, "from"), parse_date(to, "to")
        if hi < lo or (hi - lo) > timedelta(days=MAX_DAYS - 1):
            raise InvalidInputError(f"from ≤ to，且最多 {MAX_DAYS} 天")
    else:
        lo = hi = blocks.today()
    rows = _col().find({"user": current_tenant(), "date": {"$gte": lo.isoformat(), "$lte": hi.isoformat()}}, {"_id": 0}).sort("date", -1)
    return {"items": [_view(d) for d in rows]}
