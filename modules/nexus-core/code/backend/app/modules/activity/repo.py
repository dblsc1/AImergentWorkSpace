"""``activity_suggestions`` 集合的存取。**本文件是 activity 子边界唯一碰 mongo 的地方。**

唯一约束 ``(user, dedupeKey)`` 就是上传防重本身：先查再插在并发下有竞态，唯一索引没有
（同 events/repo.py）。``id`` 由 dedupeKey 派生，所以 ``(user, id)`` 也唯一。
查询一律带 ``user``：别的租户的 id 查不到，形状与「不存在」一样。
"""

from __future__ import annotations

from datetime import datetime

from pymongo.errors import DuplicateKeyError

from ...repo import get_db

_COLLECTION = "activity_suggestions"
_indexes_ready = False


def _col():
    global _indexes_ready
    col = get_db()[_COLLECTION]
    if not _indexes_ready:
        col.create_index([("user", 1), ("dedupeKey", 1)], unique=True, name="uniq_user_dedupe")
        col.create_index([("user", 1), ("id", 1)], unique=True, name="uniq_user_id")
        col.create_index([("user", 1), ("status", 1), ("startTs", -1)], name="user_status_start")
        _indexes_ready = True
    return col


def insert_if_absent(doc: dict) -> bool:
    """True=新写入，False=防重命中（**什么都不改**：已确认/已忽略的不会被重传改回 pending）。"""
    try:
        _col().insert_one(dict(doc))
    except DuplicateKeyError:
        return False
    return True


def get(user: str, sug_id: str) -> dict | None:
    return _col().find_one({"user": user, "id": sug_id}, {"_id": 0})


def page(user: str, status: str, limit: int, offset: int) -> tuple[int, list[dict]]:
    filt = {"user": user, "status": status}
    col = _col()
    docs = col.find(filt, {"_id": 0}).sort("startTs", -1).skip(offset).limit(limit)
    return col.count_documents(filt), list(docs)


def set_status(user: str, sug_id: str, status: str, at: datetime, *, only_from: str | None = None) -> bool:
    """改状态。``only_from`` 给了就是条件更新（忽略只能从 pending 转过去，防与确认赛跑）。"""
    filt = {"user": user, "id": sug_id}
    if only_from is not None:
        filt["status"] = only_from
    return _col().update_one(filt, {"$set": {"status": status, "decidedAt": at}}).matched_count > 0


def purge(user: str, cutoff: datetime) -> None:
    """待确认的按收到时刻、已处理的按处理时刻，早于 cutoff 的删掉（契约「过期」）。"""
    _col().delete_many({"user": user, "$or": [
        {"status": "pending", "receivedAt": {"$lt": cutoff}},
        {"status": {"$ne": "pending"}, "decidedAt": {"$lt": cutoff}},
    ]})
