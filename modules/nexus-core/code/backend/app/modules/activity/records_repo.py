"""``activity_records``（原始记录）与 ``activity_input``（键盘鼠标活动）两个集合的存取（v2.38 三级分类）。规矩同 ``repo.py``：查询一律带 ``user``。

记录：``(user, dedupeKey)`` 唯一就是上传防重本身；``blockKey`` 存下来读时不再算（重算见 ``block_key.py``）。
输入活动：每 ``(user, deviceId, 本地日)`` 一份有序去重的 10 秒格数组。
"""

from __future__ import annotations

from datetime import datetime

from pymongo import UpdateOne
from pymongo.errors import DuplicateKeyError

from ...repo import get_db

_REC, _INP = "activity_records", "activity_input"
PURGE_BATCH = 5000  # 惰性清理一次最多删这么多条（有界；余下的下次再清）
_ready = False


def _col(name: str = _REC):
    global _ready
    db = get_db()
    if not _ready:
        r = db[_REC]
        r.create_index([("user", 1), ("dedupeKey", 1)], unique=True, name="uniq_user_dedupe")
        r.create_index([("user", 1), ("date", 1), ("blockKey", 1), ("startTs", 1)], name="user_date_block_start")
        r.create_index([("user", 1), ("startTs", 1)], name="user_start")
        r.create_index([("user", 1), ("endTs", 1)], name="user_end")  # 第二部分对账按它查
        db[_INP].create_index([("user", 1), ("deviceId", 1), ("date", 1)], unique=True, name="uniq_user_dev_date")
        _ready = True
    return db[name]


def insert_if_absent(doc: dict) -> bool:
    try:
        _col().insert_one(dict(doc))
    except DuplicateKeyError:
        return False
    return True


def purge(user: str, cutoff: datetime, cutoff_date: str) -> None:
    """早于 cutoff 的记录与早于 cutoff_date 的输入活动删掉；记录一次至多 ``PURGE_BATCH`` 条。"""
    ids = [d["_id"] for d in _col().find({"user": user, "startTs": {"$lt": cutoff}}, {"_id": 1}).limit(PURGE_BATCH)]
    if ids:
        _col().delete_many({"_id": {"$in": ids}})
    _col(_INP).delete_many({"user": user, "date": {"$lt": cutoff_date}})


def day_records(user: str, date: str) -> list[dict]:
    """一天的记录（聚合在 Python 里做：秒数要跨设备取时间并集；一天的记录数量级在万以内，契约「七」）。"""
    return list(_col().find({"user": user, "date": date},
                            {"_id": 0, "blockKey": 1, "keySource": 1, "startTs": 1, "endTs": 1, "app": 1, "seconds": 1}))


def top_app(user: str, date: str, block_key: str) -> str | None:
    """块里时长最多的程序。"""
    rows = list(_col().aggregate([{"$match": {"user": user, "date": date, "blockKey": block_key}},
                                  {"$group": {"_id": "$app", "s": {"$sum": "$seconds"}}}, {"$sort": {"s": -1}}, {"$limit": 1}]))
    return rows[0]["_id"] if rows else None


def block_records(user: str, date: str, block_key: str, limit: int) -> list[dict]:
    return list(_col().find({"user": user, "date": date, "blockKey": block_key}, {"_id": 0})
                .sort("startTs", 1).limit(limit))


def stale(user: str, from_date: str, version: int, limit: int) -> list[dict]:
    """重算候选：标题不是代号、不是空标题、``rv`` 不是当前版本的记录（所在块是不是待分类由调用方判）。"""
    return list(_col().find({"user": user, "date": {"$gte": from_date}, "titleIsPseudonym": False, "title": {"$ne": ""}, "rv": {"$ne": version}},
                            {"_id": 1, "date": 1, "app": 1, "title": 1, "blockKey": 1, "keySource": 1}).limit(limit))


def apply_keys(updates: list[tuple]) -> None:
    """``[(_id, blockKey, keySource, rv)]`` 批量写回。"""
    if updates:
        _col().bulk_write([UpdateOne({"_id": i}, {"$set": {"blockKey": k, "keySource": ks, "rv": v}})
                           for i, k, ks, v in updates], ordered=False)


# ── 键盘鼠标活动 ──


def add_bins(user: str, device_id: str, date: str, bins: list[int], at: datetime) -> None:
    """``$addToSet``：并发上传不丢格；有序在读时排（GET 本来就合并多设备）。"""
    _col(_INP).update_one({"user": user, "deviceId": device_id, "date": date},
                          {"$addToSet": {"bins": {"$each": bins}}, "$set": {"updatedAt": at}}, upsert=True)


def day_bins(user: str, date: str) -> list[int]:
    out: set[int] = set()
    for d in _col(_INP).find({"user": user, "date": date}, {"_id": 0, "bins": 1}):
        out.update(d.get("bins", []))
    return sorted(out)
