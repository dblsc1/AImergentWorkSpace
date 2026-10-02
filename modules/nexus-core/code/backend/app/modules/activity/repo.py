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


def set_status(user: str, sug_id: str, status: str, at: datetime, *, only_from: str | None = None,
               only_task: str | None = None) -> bool:
    """改状态。``only_from`` 给了就是条件更新（忽略只能从 pending 转过去，防与确认赛跑）；
    ``only_task`` 给了还要求建议的任务仍是它（v2.7：确认用的是建议里的任务时，防与否 / 重配赛跑）。"""
    filt = {"user": user, "id": sug_id}
    if only_from is not None:
        filt["status"] = only_from
    if only_task is not None:
        filt["suggestion.taskId"] = only_task
    return _col().update_one(filt, {"$set": {"status": status, "decidedAt": at}}).matched_count > 0


def set_match(user: str, sug_id: str, suggestion: dict) -> bool:
    """v2.7 助理配任务：只在仍 pending、这个任务没被人否过、原建议可盖（没任务或也是助理配的）时换上。"""
    filt = {"user": user, "id": sug_id, "status": "pending", "rejectedTaskIds": {"$ne": suggestion["taskId"]},
            "$or": [{"suggestion.taskId": None}, {"suggestion.classifier": "assistant"}]}
    return _col().update_one(filt, {"$set": {"suggestion": suggestion}}).matched_count > 0


def clear_match(user: str, sug_id: str, task_id: str) -> bool:
    """v2.7 人说「否」：只在仍 pending 且建议的任务还是 ``task_id`` 时清掉，并记住它。"""
    filt = {"user": user, "id": sug_id, "status": "pending", "suggestion.taskId": task_id}
    upd = {"$set": {"suggestion.taskId": None, "suggestion.confidence": 0.0, "suggestion.reason": ""},
           "$addToSet": {"rejectedTaskIds": task_id}}
    return _col().update_one(filt, upd).matched_count > 0


def last_received(user: str) -> dict:
    rows = _col().aggregate([{"$match": {"user": user}},
                             {"$group": {"_id": "$deviceId", "at": {"$max": "$receivedAt"}}}])
    return {r["_id"]: r["at"] for r in rows}


def purge(user: str, cutoff: datetime) -> None:
    """待确认的按收到时刻、已处理的按处理时刻，早于 cutoff 的删掉（契约「过期」）。"""
    _col().delete_many({"user": user, "$or": [
        {"status": "pending", "receivedAt": {"$lt": cutoff}},
        {"status": {"$ne": "pending"}, "decidedAt": {"$lt": cutoff}},
    ]})


# ------------------------------------------------ activity_presence（v2.4，在场心跳，活状态）
#
# 每个 (user, deviceId) 一份文档：最新一次心跳 + 合并过的近况 spans。不是事实：
# 不进台账 / 投影 / 导出 / 快照恢复。过期文档只在心跳写入时删（读端只过滤）。

_PRESENCE = "activity_presence"
_presence_ready = False


def _presence_col():
    global _presence_ready
    col = get_db()[_PRESENCE]
    if not _presence_ready:
        col.create_index([("user", 1), ("deviceId", 1)], unique=True, name="uniq_user_device")
        col.create_index([("user", 1), ("lastAt", 1)], name="user_last")
        _presence_ready = True
    return col


def presence_get(user: str, device_id: str) -> dict | None:
    return _presence_col().find_one({"user": user, "deviceId": device_id}, {"_id": 0})


def presence_put(doc: dict) -> None:
    _presence_col().replace_one({"user": doc["user"], "deviceId": doc["deviceId"]}, dict(doc), upsert=True)


def presence_purge(user: str, cutoff: datetime) -> None:
    _presence_col().delete_many({"user": user, "lastAt": {"$lt": cutoff}})


def presence_evictable(user: str, keep_self: str, cap: int) -> list[str]:
    """除 ``keep_self`` 外按最近心跳排，最旧的那些设备 id——删掉它们后该租户至多 ``cap`` 台。"""
    ids = [d["deviceId"] for d in _presence_col().find({"user": user, "deviceId": {"$ne": keep_self}},
                                                       {"deviceId": 1}).sort("lastAt", 1)]
    return ids[: max(len(ids) - (cap - 1), 0)]


def presence_delete(user: str, device_ids: list[str]) -> None:
    if device_ids:
        _presence_col().delete_many({"user": user, "deviceId": {"$in": device_ids}})


def presence_list(user: str) -> list[dict]:
    return list(_presence_col().find({"user": user}, {"_id": 0}))
