"""``activity_ai_asks`` 集合的存取（v2.15 让 AI 认窗口）。``repo.py`` 贴着 300 行预算，拆出来——规矩同它：
activity 子边界里只有 ``repo.py`` 与本文件碰 mongo，查询一律带 ``user``。

- 每个 (user, key) 一份问询：``{user, key, app, title, claimedAt, answeredAt?, outcome?, taskId?, projectId?,
  confidence?, reason?, rejectedAt?, humanAt?, humanChose?}``，``outcome`` ∈ suggested / none / rejected。
  同一个窗口隔了 ``AI_RETRY`` 再问才整份换掉。``humanAt`` = 这次问询期间人自己对这个窗口做了决定（选了 / 这次不选），
  ``humanChose`` = 人选的目标 ``[taskId, projectId]``（这次不选为 null）。
- 每租户另有一份 ``key == "_tenant"``：``{polledAt, claims: [最近一小时里每次认领的时刻]}``——AI 这条路活着没有、
  这一小时问了几次。
活状态，不是事实：不进台账 / 投影 / 导出 / 快照恢复。过期的只在认领（写路径）时删。
"""

from __future__ import annotations

from datetime import datetime

from pymongo import ReturnDocument
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
    """``since`` 之后认领、还没回答、人也没抢先决定的那一份（万一有几份取最新）。"""
    filt = {"user": user, "key": {"$ne": _TENANT}, "claimedAt": {"$gte": since}, "answeredAt": None, "humanAt": None}
    return _col().find_one(filt, {"_id": 0}, sort=[("claimedAt", -1)])


def count_claim(user: str, now: datetime, hour_ago: datetime, cap: int) -> bool:
    """这一小时的认领数 +1；已满 ``cap`` → False、不加。两步都是单文档原子更新，并发也超不了。"""
    col = _col()
    col.update_one({"user": user, "key": _TENANT}, {"$pull": {"claims": {"$lt": hour_ago}}})
    return col.update_one({"user": user, "key": _TENANT, f"claims.{cap - 1}": {"$exists": False}},
                          {"$push": {"claims": now}}).matched_count > 0


def claim(doc: dict, stale_before: datetime) -> bool:
    """写下新的一次问询——**单个条件 upsert**：这个窗口没有问询、或那份早于 ``stale_before`` 才写（整份换掉）。
    有一份不够旧的（别的认领刚建的，可能已经答了）→ 条件不中、upsert 撞唯一键 → False，什么都不改。"""
    filt = {"user": doc["user"], "key": doc["key"], "claimedAt": {"$lte": stale_before}}
    try:
        _col().replace_one(filt, dict(doc), upsert=True)
    except DuplicateKeyError:
        return False
    return True


def unclaim(user: str, key: str, claimed_at: datetime) -> None:
    """撤掉自己刚写下、还没人答的那一次认领（名额没占到）。"""
    _col().delete_one({"user": user, "key": key, "claimedAt": claimed_at, "answeredAt": None})


def answer(user: str, key: str, claimed_at: datetime, fields: dict) -> bool:
    """回答：只在「还是那一次认领、还没人答、人也没抢先决定」时写（条件更新）——一次问询恰好被答一次。"""
    filt = {"user": user, "key": key, "claimedAt": claimed_at, "answeredAt": None, "humanAt": None}
    return _col().update_one(filt, {"$set": fields}).matched_count > 0


def reject(user: str, key: str, at: datetime) -> bool:
    """人说「不对」：suggested → rejected（条件更新，只成一次）。此前「人也选了它」的记号一并作废。"""
    return _col().update_one({"user": user, "key": key, "outcome": "suggested"},
                             {"$set": {"outcome": "rejected", "rejectedAt": at, "humanChose": None}}).matched_count > 0


def decided(user: str, key: str, at: datetime, chose: list | None) -> dict | None:
    """人对这个窗口做了决定：在它的问询上留记号（没有问询就什么都不做）。返回写后的问询。"""
    return _col().find_one_and_update({"user": user, "key": key}, {"$set": {"humanAt": at, "humanChose": chose}},
                                      projection={"_id": 0}, return_document=ReturnDocument.AFTER)
