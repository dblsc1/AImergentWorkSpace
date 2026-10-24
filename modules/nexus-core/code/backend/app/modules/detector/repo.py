"""``detector_settings`` / ``detector_rules`` 集合的存取。**本文件是 detector 子边界唯一碰 mongo 的地方。**

设置：一台设备一个文档 ``{user, deviceId, settings?, updatedAt?, lastFetchAt?, health?, restart?}``，唯一约束 ``(user, deviceId)``。
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


def set_health(user: str, device_id: str, health: dict, start_log: str | None = None, clear_log: bool = False) -> None:
    """start_log 给了就替换存着的启动日志；clear_log（开关关了）就删掉它；都没有 = 不动。"""
    upd: dict = {"$set": {"health": health}}
    if start_log is not None:
        upd["$set"]["startLog"] = {"text": start_log, "at": health["at"]}
    elif clear_log:
        upd["$unset"] = {"startLog": ""}
    _col().update_one({"user": user, "deviceId": device_id}, upd, upsert=True)


def set_restart(user: str, device_id: str, restart: dict) -> bool:
    """只对已有的设备写；设备不存在返回 False（不 upsert）。新的请求整个顶掉旧的。"""
    return _col().update_one({"user": user, "deviceId": device_id}, {"$set": {"restart": restart}}).matched_count == 1


def set_restart_result(user: str, device_id: str, nonce: str, result: str, reason: str | None, not_before: datetime,
                       at: datetime) -> bool:
    """nonce 对得上、还没有结果、且请求没过期（requestedAt ≥ not_before）时才记（旧 nonce、重复上报、过期的都落空）。
    返回这次有没有真的记下（事故时间线只记真的发生了的）。"""
    return _col().update_one({"user": user, "deviceId": device_id, "restart.nonce": nonce, "restart.result": None,
                       "restart.requestedAt": {"$gte": not_before}},
                      {"$set": {"restart.result": result, "restart.reason": reason, "restart.resultAt": at}}).modified_count == 1


def mark_restart_expired(user: str, device_id: str, nonce: str) -> bool:
    """请求过期的事故只记一次：第一次标记成功才返回 True。"""
    return _col().update_one({"user": user, "deviceId": device_id, "restart.nonce": nonce, "restart.result": None,
                              "restart.expiredLogged": {"$ne": True}},
                             {"$set": {"restart.expiredLogged": True}}).modified_count == 1


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


def get_ai_direct(user: str) -> bool:
    return (_rules_col().find_one({"user": user}, {"aiDirectRules": 1}) or {}).get("aiDirectRules") is True


def set_ai_direct(user: str, on: bool) -> None:
    _rules_col().update_one({"user": user}, {"$set": {"aiDirectRules": on}, "$setOnInsert": {"version": 0, "rules": []}},
                            upsert=True)


def replace_rules(user: str, expected_version: int, rules: list[dict], at: datetime,
                  draft_id: str | None = None, snapshots: list | None = None,
                  require_ai_direct: bool = False) -> dict | None:
    """version == expected（没有文档算 0）时整套换掉并 +1，原子地回写后的文档；给了 draft_id 还要求草稿就是它、
    并删掉草稿。snapshots：给了就在同一次更新里整个换成这个快照栈（调用方按读到的同一版本算好，保持原子）；
    require_ai_direct：条件里再加「账号开关仍开着」（ai-apply 用，开关被并发关掉就落空）。
    条件不成立返回 None（调用方再读一次分辨是 412 还是 404）。"""
    q: dict = {"user": user, "version": expected_version}
    upd: dict = {"$set": {"rules": rules, "version": expected_version + 1, "updatedAt": at}}
    if snapshots is not None:
        upd["$set"]["snapshots"] = snapshots
    if require_ai_direct:
        q["aiDirectRules"] = True
    if draft_id is not None:
        q["draft.id"] = draft_id
        upd["$unset"] = {"draft": ""}
    try:
        return _rules_col().find_one_and_update(q, upd, projection={"_id": 0}, return_document=ReturnDocument.AFTER,
                                                upsert=expected_version == 0 and draft_id is None and not require_ai_direct)
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


# ── 事故时间线（v2.28）：每台设备一个文档 {user, deviceId, events:[{kind, at, endAt?, reason?, result?}]}，最多 200 条 ──
MAX_EVENTS = 200
_INCIDENTS = "detector_incidents"
_incidents_indexes_ready = False


def _incidents_col():
    global _incidents_indexes_ready
    col = get_db()[_INCIDENTS]
    if not _incidents_indexes_ready:
        col.create_index([("user", 1), ("deviceId", 1)], unique=True, name="uniq_user_device")
        _incidents_indexes_ready = True
    return col


def add_events(user: str, device_id: str, events: list[dict]) -> None:
    """追加；超过 MAX_EVENTS 时丢最老的（$slice，一条原子更新）。"""
    if events:
        _incidents_col().update_one({"user": user, "deviceId": device_id},
                                    {"$push": {"events": {"$each": events, "$slice": -MAX_EVENTS}}}, upsert=True)


def get_events(user: str, device_id: str) -> list[dict]:
    doc = _incidents_col().find_one({"user": user, "deviceId": device_id}, {"_id": 0, "events": 1})
    return (doc or {}).get("events", [])
