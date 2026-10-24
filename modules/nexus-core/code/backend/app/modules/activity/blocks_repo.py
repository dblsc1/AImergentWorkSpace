"""窗口块的分类文档 ``activity_blocks``、通用窗口 ``activity_generic``、第二级规则 ``activity_block_rules``、账号设置 ``activity_settings``
（v2.38 三级分类）。规矩同 ``repo.py``：查询一律带 ``user``。

``activity_blocks`` 的 ``seq`` 是乐观锁：没有文档 = 0；每次改动 +1；删（reset）后回到 0。
``activity_direct_log`` 本期只在导出 / 恢复 / 快照里登记，不实现（契约「五」，第二部分）。
"""

from __future__ import annotations

import secrets
from datetime import datetime

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from ...repo import get_db

_BLK, _GEN, _RULE, _SET = "activity_blocks", "activity_generic", "activity_block_rules", "activity_settings"
MAX_RULES = 2000
_ready = False


def _col(name: str):
    global _ready
    db = get_db()
    if not _ready:
        db[_BLK].create_index([("user", 1), ("date", 1), ("blockKey", 1)], unique=True, name="uniq_user_date_block")
        db[_GEN].create_index([("user", 1), ("blockKey", 1)], unique=True, name="uniq_user_block")
        db[_RULE].create_index([("user", 1), ("id", 1)], unique=True, name="uniq_user_id")
        db[_RULE].create_index([("user", 1), ("blockKey", 1)], unique=True, name="uniq_user_block")
        db[_SET].create_index([("user", 1)], unique=True, name="uniq_user")
        _ready = True
    return db[name]


# ── 分类文档 ──


def blocks_of_day(user: str, date: str) -> dict[str, dict]:
    return {d["blockKey"]: d for d in _col(_BLK).find({"user": user, "date": date}, {"_id": 0})}


def get_block(user: str, date: str, key: str) -> dict | None:
    return _col(_BLK).find_one({"user": user, "date": date, "blockKey": key}, {"_id": 0})


def put_block(user: str, date: str, key: str, fields: dict, if_seq: int, old: dict | None) -> dict | None:
    """``seq`` 等于 ``if_seq`` 才写（0 = 现在没有文档），写后 ``seq`` +1 并回写后的文档；不符 → None。
    ``gen``（入账代号）：``fields["parts"]`` 与 ``old`` 的不同（生效目标或拆分点变了）就 +1。``runs`` 第二部分才写，这里只建字段。"""
    bump = 1 if (old or {}).get("parts") != fields.get("parts") else 0
    if if_seq == 0:
        doc = {**fields, "user": user, "date": date, "blockKey": key, "seq": 1, "gen": bump, "runs": []}
        try:
            _col(_BLK).insert_one(dict(doc))
        except DuplicateKeyError:
            return None
        return doc
    return _col(_BLK).find_one_and_update({"user": user, "date": date, "blockKey": key, "seq": if_seq},
                                          {"$set": fields, "$inc": {"seq": 1, "gen": bump}}, projection={"_id": 0},
                                          return_document=ReturnDocument.AFTER)


def reset_block(user: str, date: str, key: str, if_seq: int, at: datetime) -> bool:
    """``status`` / ``parts`` / ``via`` 置 null，``seq`` +1，文档保留（seq 永不回退）。没有文档（``if_seq`` 0）也建一份，``seq`` 从 1 起。"""
    if if_seq == 0:
        try:
            _col(_BLK).insert_one({"user": user, "date": date, "blockKey": key, "status": None, "parts": None, "via": None,
                                   "ruleId": None, "seq": 1, "gen": 0, "runs": [], "updatedAt": at})
        except DuplicateKeyError:
            return False
        return True
    return _col(_BLK).update_one({"user": user, "date": date, "blockKey": key, "seq": if_seq},
                                 {"$set": {"status": None, "parts": None, "via": None, "ruleId": None, "updatedAt": at},
                                  "$inc": {"seq": 1, "gen": 1}}).matched_count == 1


def classified_since(user: str, from_date: str) -> set[tuple[str, str]]:
    """``(date, blockKey)``：已经有分类的（文档 ``status`` 非 null）。"""
    return {(d["date"], d["blockKey"]) for d in _col(_BLK).find({"user": user, "date": {"$gte": from_date}, "status": {"$ne": None}},
                                                                {"_id": 0, "date": 1, "blockKey": 1})}


# ── 通用窗口（块名级） ──


def generic_since(user: str) -> dict[str, str]:
    """通用窗口标记：块名 → 标记的本地日（只管这一天及以后）。"""
    return {d["blockKey"]: d["since"] for d in _col(_GEN).find({"user": user}, {"_id": 0, "blockKey": 1, "since": 1})}


def set_generic(user: str, key: str, on: bool, since: str) -> None:
    if on:
        _col(_GEN).update_one({"user": user, "blockKey": key}, {"$setOnInsert": {"since": since}}, upsert=True)
    else:
        _col(_GEN).delete_one({"user": user, "blockKey": key})


# ── 第二级规则 ──


def rules_all(user: str) -> list[dict]:
    return list(_col(_RULE).find({"user": user}, {"_id": 0}).sort("createdAt", 1).limit(MAX_RULES))


def rules_by_keys(user: str, keys: list[str]) -> dict[str, dict]:
    return {d["blockKey"]: d for d in _col(_RULE).find({"user": user, "blockKey": {"$in": keys}}, {"_id": 0})}


def rule_count(user: str) -> int:
    return _col(_RULE).count_documents({"user": user})


def upsert_rule(user: str, key: str, fields: dict, keep_existing: bool = False) -> tuple[dict, bool]:
    """同块名改目标（保留原 id 与 ``createdAt``，其余换成这次的）。返回 ``(规则, 是不是新建的)``；``keep_existing`` = 已有就不动（迁移用）。"""
    new_id = "br_" + secrets.token_hex(6)
    once = {"id": new_id, "createdAt": fields["updatedAt"]}
    upd = {"$setOnInsert": {**fields, **once}} if keep_existing else {"$set": fields, "$setOnInsert": once}
    try:
        res = _col(_RULE).update_one({"user": user, "blockKey": key}, upd, upsert=True)
    except DuplicateKeyError:  # 并发的另一个创建者刚插完：再来一次就走替换
        res = _col(_RULE).update_one({"user": user, "blockKey": key}, upd, upsert=True)
    doc = _col(_RULE).find_one({"user": user, "blockKey": key}, {"_id": 0})
    return doc, res.upserted_id is not None


def delete_rule(user: str, rule_id: str) -> bool:
    return _col(_RULE).delete_one({"user": user, "id": rule_id}).deleted_count > 0


# ── 账号设置 ──


def get_settings(user: str) -> dict | None:
    return _col(_SET).find_one({"user": user}, {"_id": 0})


def put_settings(user: str, expected: int, fields: dict) -> bool:
    """``version`` 等于 ``expected`` 才写（0 = 没有文档），写后 +1；不符 False。``fields`` 之外的内部键（``migratedAt``）不动。"""
    if expected == 0:
        try:
            _col(_SET).insert_one({**fields, "user": user, "version": 1})
        except DuplicateKeyError:
            return False
        return True
    return _col(_SET).update_one({"user": user, "version": expected}, {"$set": fields, "$inc": {"version": 1}}).matched_count == 1
