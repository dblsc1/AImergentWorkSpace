"""``activity_ignores`` 集合的存取（v2.22「忽略并记住」）。规矩同 ``repo.py``：查询一律带 ``user``。

每条规则一份 ``{user, id, app, titleContains, createdAt, hits, seconds, lastHitAt}``；``id`` 由（程序, 标题片段）派生，
所以同一条规则再建一次是同一份（幂等）。只存规则本身与计数器（规则里的 ``titleContains`` 是人填的匹配文字，只对人可见）。
"""

from __future__ import annotations

from datetime import datetime

from pymongo.errors import DuplicateKeyError

from ...repo import get_db

_COLLECTION = "activity_ignores"
_indexes_ready = False


def _col():
    global _indexes_ready
    col = get_db()[_COLLECTION]
    if not _indexes_ready:
        col.create_index([("user", 1), ("id", 1)], unique=True, name="uniq_user_id")
        _indexes_ready = True
    return col


def all_rules(user: str) -> list[dict]:
    return list(_col().find({"user": user}, {"_id": 0}).sort("createdAt", 1))


def insert_if_absent(doc: dict) -> bool:
    try:
        _col().insert_one(dict(doc))
    except DuplicateKeyError:
        return False
    return True


def delete(user: str, rule_id: str) -> bool:
    return _col().delete_one({"user": user, "id": rule_id}).deleted_count > 0


def count(user: str) -> int:
    return _col().count_documents({"user": user})


def hit(user: str, rule_id: str, records: int, seconds: int, at: datetime) -> None:
    _col().update_one({"user": user, "id": rule_id},
                      {"$inc": {"hits": records, "seconds": seconds}, "$set": {"lastHitAt": at}})
