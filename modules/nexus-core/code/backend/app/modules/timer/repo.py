"""``timer_state`` 集合的存取。**本文件是 timer 子边界唯一碰 mongo 的地方。**

一个 user 至多一条活状态（唯一索引 ``user``）——「start 自动关上一个」
在数据层就成立，不靠上层自觉。
"""

from __future__ import annotations

from ...repo import get_db

_COLLECTION = "timer_state"
_indexes_ready = False


def _col():
    global _indexes_ready
    col = get_db()[_COLLECTION]
    if not _indexes_ready:
        col.create_index([("user", 1)], unique=True, name="uniq_user")
        _indexes_ready = True
    return col


def get_running(user: str) -> dict | None:
    return _col().find_one({"user": user}, {"_id": 0})


def set_running(doc: dict) -> None:
    """写活状态（覆盖同 user 旧条目——但正常路径上 service 会先 stop 清掉）。"""
    _col().replace_one({"user": doc["user"]}, dict(doc), upsert=True)


def clear_running(user: str) -> None:
    _col().delete_one({"user": user})
