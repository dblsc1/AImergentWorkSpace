"""``activity_asked``（onboarding.v1「三」只问一次）：``(user, blockKey)`` 唯一，永久保留（不进任何清理），进导出 / 恢复。"""

from __future__ import annotations

from datetime import datetime

from pymongo.errors import DuplicateKeyError

from ...repo import get_db

_ASKED = "activity_asked"
KEYS = ("blockKey", "answer", "askedAt")
_SHOW = {"_id": 0, **dict.fromkeys(KEYS, 1)}
_ready = False


def _col():
    global _ready
    col = get_db()[_ASKED]
    if not _ready:
        col.create_index([("user", 1), ("blockKey", 1)], unique=True, name="uniq_user_block")
        _ready = True
    return col


def get(user: str, key: str) -> dict | None:
    return _col().find_one({"user": user, "blockKey": key}, _SHOW)


def add_if_absent(user: str, key: str, answer: str, at: datetime) -> dict:
    """已有就不动，返回库里的那条。"""
    try:
        _col().insert_one({"user": user, "blockKey": key, "answer": answer, "askedAt": at})
    except DuplicateKeyError:
        pass
    return get(user, key)


def list_all(user: str) -> list[dict]:
    return list(_col().find({"user": user}, _SHOW).sort("askedAt", 1))


def restore(user: str, docs: list[dict]) -> int:
    for d in docs:
        add_if_absent(user, d["blockKey"], d["answer"], d["askedAt"])
    return len(docs)
