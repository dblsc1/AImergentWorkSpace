"""``activity_choices`` 集合的存取（v2.14，人对某个窗口的临时选择 / 「这次不选」；v2.15 AI 认到时也写一份）。
``repo.py`` 贴着 300 行预算，拆出来——规矩同它：查询一律带 ``user``。

每个 (user, key) 一份：kind ``"choice"``（taskId 或 projectId）/ ``"dismiss"``；``expiresAt`` 之后当作没有。
AI 写的带 ``by: "ai"``（人写的没有这个键）：它**只在没有未过期的那一份时**才写得进（``put`` 的 ``unless_live``），
所以盖不掉人的选择；人写的无条件盖。活状态，不是事实：不进台账 / 投影 / 导出 / 快照恢复。过期文档只在心跳写入时删。
"""

from __future__ import annotations

from datetime import datetime

from pymongo.errors import DuplicateKeyError

from ...repo import get_db

_COLLECTION = "activity_choices"
_indexes_ready = False


def _col():
    global _indexes_ready
    col = get_db()[_COLLECTION]
    if not _indexes_ready:
        col.create_index([("user", 1), ("key", 1)], unique=True, name="uniq_user_key")
        _indexes_ready = True
    return col


def put(doc: dict, unless_live: datetime | None = None) -> bool:
    """写下（整份换掉）。``unless_live`` 给了 = 只在这个窗口此刻没有未过期的那一份时写（单个条件 upsert：
    有活的 → 条件不中、upsert 撞唯一键 → False，什么都不改）。"""
    filt = {"user": doc["user"], "key": doc["key"]}
    if unless_live is not None:
        filt["expiresAt"] = {"$lte": unless_live}
    try:
        _col().replace_one(filt, dict(doc), upsert=True)
    except DuplicateKeyError:
        return False
    return True


def drop_ai(user: str, key: str) -> None:
    """撤掉 AI 给这个窗口写的那一份（人写的不动）。"""
    _col().delete_one({"user": user, "key": key, "by": "ai"})


def live(user: str, now: datetime) -> dict[str, dict]:
    """没过期的：{key: 文档}。"""
    return {d["key"]: d for d in _col().find({"user": user, "expiresAt": {"$gt": now}}, {"_id": 0})}


def seen(user: str, key: str, now: datetime, until: datetime) -> None:
    """这个窗口又在前台了：没过期的临时选择续期（「这次不选」不续）；顺手清掉该租户过期的。"""
    col = _col()
    col.delete_many({"user": user, "expiresAt": {"$lte": now}})
    col.update_one({"user": user, "key": key, "kind": "choice"}, {"$set": {"expiresAt": until}})
