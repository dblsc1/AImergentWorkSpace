"""``activity_ai_asks`` 集合的存取（v2.15 让 AI 认窗口）。``repo.py`` 贴着 300 行预算，拆出来——规矩同它：
activity 子边界里只有 ``repo.py`` 与本文件碰 mongo，查询一律带 ``user``。

- 每个 (user, key) 一份问询：``{user, key, app, title, claimedAt, answeredAt?, outcome?, taskId?, projectId?,
  confidence?, reason?, rejectedAt?, humanAt?, rejected?}``，``outcome`` ∈ suggested / none / rejected。
  同一个窗口隔了 ``AI_RETRY`` 再问才换成新的一次（回答清掉）。``humanAt`` = 这次问询期间人自己对这个窗口做了决定
  （选了 / 这次不选）。``rejected`` = 人对这个窗口否掉过的目标 ``[{taskId, projectId, at}]``（最近 8 个）：**不随再问清掉**，
  人后来自己选了其中哪个就拿掉哪个。
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
    # ``uncount_claim`` 两步之间断了会留下一个 null：它占着名额、上面按时间的 $pull 又清不掉，这里一并清
    col.update_one({"user": user, "key": _TENANT, "claims": None}, {"$pull": {"claims": None}})
    return col.update_one({"user": user, "key": _TENANT, f"claims.{cap - 1}": {"$exists": False}},
                          {"$push": {"claims": now}}).matched_count > 0


#: 再问时清掉的键 = 上一次问询的回答；``rejected``（人否掉过的目标）不在里面，跟着窗口走
_ANSWER_KEYS = ("answeredAt", "outcome", "taskId", "projectId", "confidence", "reason", "rejectedAt", "humanAt")
_MAX_REJECTED = 8  # 每个窗口记最近这么多个被否掉的目标


def uncount_claim(user: str, at: datetime) -> None:
    """退回 ``count_claim`` 占的那一个名额（问询没写进去）。恰好退一个：同一毫秒有别的认领也不多退。"""
    col = _col()
    col.update_one({"user": user, "key": _TENANT, "claims": at}, {"$unset": {"claims.$": ""}})
    col.update_one({"user": user, "key": _TENANT}, {"$pull": {"claims": None}})


def claim(doc: dict, stale_before: datetime) -> bool:
    """写下新的一次问询——**单个条件 upsert**：这个窗口没有问询、或那份早于 ``stale_before`` 才写
    （上一次的回答清掉，``rejected`` 留着）。有一份不够旧的（别的认领刚建的，可能已经答了）→ 条件不中、
    upsert 撞唯一键 → False，什么都不改。"""
    filt = {"user": doc["user"], "key": doc["key"], "claimedAt": {"$lte": stale_before}}
    upd = {"$set": {k: v for k, v in doc.items() if k not in ("user", "key")}, "$unset": dict.fromkeys(_ANSWER_KEYS, "")}
    try:
        _col().update_one(filt, upd, upsert=True)
    except DuplicateKeyError:
        return False
    return True


def answer(user: str, key: str, claimed_at: datetime, fields: dict) -> bool:
    """回答：只在「还是那一次认领、还没人答、人也没抢先决定」时写（条件更新）——一次问询恰好被答一次。"""
    filt = {"user": user, "key": key, "claimedAt": claimed_at, "answeredAt": None, "humanAt": None}
    return _col().update_one(filt, {"$set": fields}).matched_count > 0


def reject(user: str, key: str, at: datetime) -> bool:
    """人说「不对」：suggested → rejected（条件更新，只成一次），同一次更新里把那个目标记进 ``rejected``
    （最近 ``_MAX_REJECTED`` 个）——以后再问 AI、问询的回答被清掉，它也还在。"""
    was = {"taskId": {"$ifNull": ["$taskId", None]}, "projectId": {"$ifNull": ["$projectId", None]}, "at": at}
    kept = {"$slice": [{"$concatArrays": [{"$ifNull": ["$rejected", []]}, [was]]}, -_MAX_REJECTED]}
    return _col().update_one({"user": user, "key": key, "outcome": "suggested"},
                             [{"$set": {"outcome": "rejected", "rejectedAt": at, "rejected": kept}}]).matched_count > 0


def decided(user: str, key: str, at: datetime, chose: dict | None) -> dict | None:
    """人对这个窗口做了决定：在它的问询上留记号（没有问询就什么都不做）；人选的那个目标（``chose``，这次不选为 None）
    若被否掉过，从 ``rejected`` 里拿掉——那是人的决定。返回写后的问询。"""
    upd: dict = {"$set": {"humanAt": at}}
    if chose:
        upd["$pull"] = {"rejected": {"taskId": chose.get("taskId"), "projectId": chose.get("projectId")}}
    return _col().find_one_and_update({"user": user, "key": key}, upd, projection={"_id": 0},
                                      return_document=ReturnDocument.AFTER)
