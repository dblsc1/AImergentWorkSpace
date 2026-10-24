"""``views/history`` 要的两个只读查询（v2.39）：一天的原始记录全列、时间范围内待确认的建议。只读，不写任何东西。"""

from __future__ import annotations

from datetime import datetime

from . import blocks_repo, records_repo
from . import repo as suggestions_repo


def day_records_full(user: str, date: str) -> list[dict]:
    return list(records_repo._col().find(  # noqa: SLF001
        {"user": user, "date": date},
        {"_id": 0, "blockKey": 1, "startTs": 1, "endTs": 1, "seconds": 1, "app": 1, "title": 1, "ledgerEventId": 1}))


def pending_suggestions(user: str, lo: datetime, hi: datetime, limit: int) -> list[dict]:
    """``startTs`` 在 [lo, hi) 的待确认建议，开始时刻新的在前，至多 ``limit`` 条。"""
    return list(suggestions_repo._col().find(  # noqa: SLF001
        {"user": user, "status": "pending", "startTs": {"$gte": lo, "$lt": hi}},
        {"_id": 0, "startTs": 1, "endAt": 1, "durationSeconds": 1, "app": 1, "title": 1}).sort("startTs", -1).limit(limit))


def pending_spans(user: str, first: str, last: str, limit: int) -> list[dict]:
    """[first, last] 日里还没入账的原始记录，按 (日, 块名) 一次聚合成时间区间列表；至多 ``limit`` 个块（调用方多要 1 个判截断）。"""
    rows = records_repo._col().aggregate([  # noqa: SLF001
        {"$match": {"user": user, "date": {"$gte": first, "$lte": last}, "ledgerEventId": None}},
        {"$group": {"_id": {"d": "$date", "k": "$blockKey"}, "spans": {"$push": {"s": "$startTs", "e": "$endTs"}}}},
        {"$limit": limit}])
    return [{"date": r["_id"]["d"], "blockKey": r["_id"]["k"], "spans": [(x["s"], x["e"]) for x in r["spans"]]} for r in rows]


def range_records(user: str, first: str, last: str) -> list[dict]:
    """[first, last] 日的原始记录全列（detail=segments / windows，至多 7 天，一次读完）。"""
    return list(records_repo._col().find(  # noqa: SLF001
        {"user": user, "date": {"$gte": first, "$lte": last}},
        {"_id": 0, "date": 1, "blockKey": 1, "keySource": 1, "startTs": 1, "endTs": 1, "seconds": 1, "app": 1, "title": 1,
         "ledgerEventId": 1}))


def block_docs(user: str, first: str, last: str) -> dict[tuple[str, str], dict]:
    return {(d["date"], d["blockKey"]): d for d in blocks_repo._col("activity_blocks").find(  # noqa: SLF001
        {"user": user, "date": {"$gte": first, "$lte": last}}, {"_id": 0})}
