"""``activity_ai_asks`` 集合的存取（v2.15 让 AI 认窗口）。``repo.py`` 贴着 300 行预算，拆出来——规矩同它：
activity 子边界里只有 ``repo.py`` 与本文件碰 mongo，查询一律带 ``user``。

- 每个 (user, key) 一份问询：``{user, key, app, title, claimedAt, answeredAt?, outcome?, taskId?, projectId?,
  confidence?, reason?, rejectedAt?}``，``outcome`` ∈ suggested / none / rejected。同一个窗口再问就整份换掉。
- 每租户另有一份 ``key == "_tenant"``：``{polledAt, claims: [最近一小时里每次认领的时刻]}``——AI 这条路活着没有、
  这一小时问了几次。
活状态，不是事实：不进台账 / 投影 / 导出 / 快照恢复。过期的只在认领（写路径）时删。
"""

from __future__ import annotations

from datetime import datetime

from pymongo.errors import DuplicateKeyError

from ...repo import get_db

_COLLECTION = "activity_ai_asks"
_TENANT = "_tenant"  # 不可能是窗口的键（那是 wk_ + 20 位十六进制）
_indexes_ready = False


def _col():
    global _indexes_ready
    col = get_db()[_COLLECTION]
    if not _indexes_ready:
        col.create_index([("user", 1), ("key", 1)], unique=True, name="uniq_user_key")
        _indexes_ready = True
    return col


def get(user: str, key: str) -> dict | None:
    return _col().find_one({"user": user, "key": key}, {"_id": 0})


def by_keys(user: str, keys: list[str]) -> dict[str, dict]:
    return {d["key"]: d for d in _col().find({"user": user, "key": {"$in": keys}}, {"_id": 0})}


def tenant(user: str) -> dict:
    return get(user, _TENANT) or {}


def polled(user: str, now: datetime, keep_from: datetime) -> None:
    """有人来认领 / 回答了：记下时刻（AI 这条路活着），顺手清掉该租户太旧的问询。"""
    col = _col()
    try:
        col.update_one({"user": user, "key": _TENANT}, {"$set": {"polledAt": now}}, upsert=True)
    except DuplicateKeyError:  # 两个认领同时建这一份：另一个已经写了，时刻差不到一秒
        pass
    col.delete_many({"user": user, "key": {"$ne": _TENANT}, "claimedAt": {"$lt": keep_from}})


def live(user: str, since: datetime) -> dict | None:
    """``since`` 之后认领、还没回答的那一份（至多一份在等；万一有几份取最新）。"""
    filt = {"user": user, "key": {"$ne": _TENANT}, "claimedAt": {"$gte": since}, "answeredAt": None}
    return _col().find_one(filt, {"_id": 0}, sort=[("claimedAt", -1)])


def count_claim(user: str, now: datetime, hour_ago: datetime, cap: int) -> bool:
    """这一小时的认领数 +1；已满 ``cap`` → False、不加。两步都是单文档原子更新，并发也超不了。"""
    col = _col()
    col.update_one({"user": user, "key": _TENANT}, {"$pull": {"claims": {"$lt": hour_ago}}})
    return col.update_one({"user": user, "key": _TENANT, f"claims.{cap - 1}": {"$exists": False}},
                          {"$push": {"claims": now}}).matched_count > 0


def claim(doc: dict) -> None:
    """写下新的一次问询（同一个窗口以前的那份整份换掉）。"""
    try:
        _col().replace_one({"user": doc["user"], "key": doc["key"]}, dict(doc), upsert=True)
    except DuplicateKeyError:  # 两个认领同时建同一份：内容一样，谁写的都行
        pass


def answer(user: str, key: str, claimed_at: datetime, fields: dict) -> bool:
    """回答：只在「还是那一次认领、还没人答」时写（条件更新）——一次问询恰好被答一次。"""
    filt = {"user": user, "key": key, "claimedAt": claimed_at, "answeredAt": None}
    return _col().update_one(filt, {"$set": fields}).matched_count > 0


def reject(user: str, key: str, at: datetime) -> bool:
    """人说「不对」：suggested → rejected（条件更新，只成一次）。"""
    return _col().update_one({"user": user, "key": key, "outcome": "suggested"},
                             {"$set": {"outcome": "rejected", "rejectedAt": at}}).matched_count > 0
