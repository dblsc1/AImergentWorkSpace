"""``lane_prefs`` 集合的存取（v2.22）。**本子边界只有本文件碰 mongo。**

每租户一个文档 ``{user, v, agents: [{key, agent, label, hidden, pinned, pinnedAt}], order: [{runId, slot}]}``；
写一律带版本号条件（``cas``），撞了的由 service 重读重算。查询一律带 ``user``。
"""

from __future__ import annotations

from pymongo.errors import DuplicateKeyError

from ...repo import get_db

_COLLECTION = "lane_prefs"
_indexes_ready = False


def _col():
    global _indexes_ready
    col = get_db()[_COLLECTION]
    if not _indexes_ready:
        col.create_index([("user", 1)], unique=True, name="uniq_user")
        _indexes_ready = True
    return col


def get(user: str) -> dict | None:
    return _col().find_one({"user": user}, {"_id": 0})


def cas(user: str, old: dict | None, agents: list[dict], order: list[dict]) -> bool:
    """``old`` 是读到的文档（没有 = None）；版本没变才写，True = 写中。"""
    version = old["v"] if old else 0
    doc = {"user": user, "v": version + 1, "agents": agents, "order": order}
    try:
        if old is None:
            _col().insert_one(doc)
            return True
        return _col().replace_one({"user": user, "v": version}, doc).matched_count > 0
    except DuplicateKeyError:
        return False
