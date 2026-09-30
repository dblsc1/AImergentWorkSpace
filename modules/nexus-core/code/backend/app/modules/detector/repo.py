"""``detector_settings`` 集合的存取。**本文件是 detector 子边界唯一碰 mongo 的地方。**

一台设备一个文档 ``{user, deviceId, settings?, updatedAt?, lastFetchAt?}``，唯一约束 ``(user, deviceId)``。
查询一律带 ``user``：别的租户的设备看不见。
"""

from __future__ import annotations

from datetime import datetime

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
