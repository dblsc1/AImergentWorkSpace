"""``detector_settings`` / ``detector_rules`` 集合的存取。**本文件是 detector 子边界唯一碰 mongo 的地方。**

设置：一台设备一个文档 ``{user, deviceId, settings?, updatedAt?, lastFetchAt?}``，唯一约束 ``(user, deviceId)``。
规则（detector.rules.v1）：一个租户一个文档，见下半部分。查询一律带 ``user``：别的租户的看不见。
"""

from __future__ import annotations

from datetime import datetime

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from ...repo import get_db

_COLLECTION = "detector_settings"
_indexes_ready = False


def _col():
    global _indexes_ready
    col = get_db()[_COLLECTION]
    if not _indexes_ready:
        col.create_index([("user", 1), ("deviceId", 1)], unique=True, name="uniq_user_device")
        _indexes_ready = True
    return col


def get(user: str, device_id: str) -> dict | None:
    return _col().find_one({"user": user, "deviceId": device_id}, {"_id": 0})


def put(user: str, device_id: str, settings: dict, at: datetime) -> None:
    _col().update_one({"user": user, "deviceId": device_id},
                      {"$set": {"settings": settings, "updatedAt": at}}, upsert=True)


def clear(user: str, device_id: str) -> None:
    _col().update_one({"user": user, "deviceId": device_id}, {"$unset": {"settings": "", "updatedAt": ""}})


def touch_fetch(user: str, device_id: str, at: datetime) -> None:
    _col().update_one({"user": user, "deviceId": device_id}, {"$set": {"lastFetchAt": at}}, upsert=True)


def all_devices(user: str) -> list[dict]:
    return list(_col().find({"user": user}, {"_id": 0}))


# ── detector.rules.v1：每租户一个文档 {user, version, rules, updatedAt?, draft?} ──
_RULES = "detector_rules"
_rules_indexes_ready = False


def _rules_col():
    global _rules_indexes_ready
    col = get_db()[_RULES]
    if not _rules_indexes_ready:
        col.create_index([("user", 1)], unique=True, name="uniq_user")
        _rules_indexes_ready = True
    return col


def get_rules(user: str) -> dict | None:
    return _rules_col().find_one({"user": user}, {"_id": 0})


def replace_rules(user: str, expected_version: int, rules: list[dict], at: datetime,
                  draft_id: str | None = None) -> dict | None:
    """version == expected（没有文档算 0）时整套换掉并 +1，原子地回写后的文档；给了 draft_id 还要求草稿就是它、
    并删掉草稿。条件不成立返回 None（调用方再读一次分辨是 412 还是 404）。"""
    q: dict = {"user": user, "version": expected_version}
    upd: dict = {"$set": {"rules": rules, "version": expected_version + 1, "updatedAt": at}}
    if draft_id is not None:
        q["draft.id"] = draft_id
        upd["$unset"] = {"draft": ""}
    try:
        return _rules_col().find_one_and_update(q, upd, projection={"_id": 0}, return_document=ReturnDocument.AFTER,
                                                upsert=expected_version == 0 and draft_id is None)
    except DuplicateKeyError:  # 期望 0、而文档已在（version 不是 0）：upsert 撞唯一键 = 冲突
        return None


def put_draft(user: str, draft: dict) -> dict:
    """写草稿并原子地回写后的文档（别的请求紧接着顶掉 / 应用，也不影响这次的返回）。"""
    return _rules_col().find_one_and_update(
        {"user": user}, {"$set": {"draft": draft}, "$setOnInsert": {"version": 0, "rules": []}},
        projection={"_id": 0}, upsert=True, return_document=ReturnDocument.AFTER)


def drop_draft(user: str, draft_id: str | None = None) -> None:
    q: dict = {"user": user}
    if draft_id is not None:
        q["draft.id"] = draft_id
    _rules_col().update_one(q, {"$unset": {"draft": ""}})
