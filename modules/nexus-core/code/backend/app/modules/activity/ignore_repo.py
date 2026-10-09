"""``activity_ignores`` 集合的存取（v2.22「忽略并记住」）。规矩同 ``repo.py``：查询一律带 ``user``。

每条规则一份 ``{user, id, app, titleContains, createdAt, hits, seconds, lastHitAt}``；``id`` 由（程序, 标题片段）派生，
所以同一条规则再建一次是同一份（幂等）。只存规则本身与计数器——**不存任何被忽略的窗口标题**。
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


def mark_purged(user: str, rule_id: str) -> None:
    """清理做完了才标：没标的规则（清理中途出错）会在下一次写入时被补清。"""
    _col().update_one({"user": user, "id": rule_id}, {"$set": {"purged": True}})


def delete(user: str, rule_id: str) -> bool:
    return _col().delete_one({"user": user, "id": rule_id}).deleted_count > 0


def count(user: str) -> int:
    return _col().count_documents({"user": user})


def hit(user: str, rule_id: str, records: int, seconds: int, at: datetime) -> None:
    _col().update_one({"user": user, "id": rule_id},
                      {"$inc": {"hits": records, "seconds": seconds}, "$set": {"lastHitAt": at}})


# ── 建规则时清掉已存下的、带被忽略窗口标题的活状态（presence / 临时选择 / AI 问询）。都带 user；其余集合的存取归各自的 repo，
# 这里只做「命中就抹掉标题」这一件事，不读别的字段做别的判断。
_CAS_TRIES = 5


def mask_presence(user: str, hit) -> int:
    """在场文档里命中的段 / 当前窗口抹成「没有窗口」（app、title 空，去掉 guess / runId）。返回抹了几处。``hit(app, title)`` 判命中。"""
    col, n = get_db()["activity_presence"], 0
    for doc in list(col.find({"user": user}, {"_id": 0})):
        for _ in range(_CAS_TRIES):
            spans, changed = [], 0
            for sp in doc.get("spans") or []:
                if hit(sp["app"], sp["title"]):
                    sp = {k: v for k, v in sp.items() if k not in ("guess", "runId")} | {"app": "", "title": ""}
                    changed += 1
                spans.append(sp)
            top = hit(doc.get("app", ""), doc.get("title", ""))
            if not changed and not top:
                break
            # 像正常写入者一样把 v 加一：读了旧文档、正要条件写的心跳会因此写不中，重读到抹过的这份（不然它会把标题写回来）
            new = {**doc, "spans": spans, "v": (doc.get("v") or 0) + 1, **({"app": "", "title": ""} if top else {})}
            if col.replace_one({"user": user, "deviceId": doc["deviceId"], "v": doc.get("v"), "gen": doc.get("gen")}, new).matched_count:
                n += changed + top
                break
            doc = col.find_one({"user": user, "deviceId": doc["deviceId"]}, {"_id": 0})
            if doc is None:
                break
    return n


def drop_windows(user: str, collection: str, hit) -> int:
    """activity_choices / activity_ai_asks：命中（按存下的 app / title）的整份删掉。没有 app 的（如 ``_tenant`` 那份）不碰。"""
    col = get_db()[collection]
    ids = [d["_id"] for d in col.find({"user": user, "app": {"$exists": True}}, {"app": 1, "title": 1})
           if hit(d["app"], d.get("title", ""))]
    return col.delete_many({"_id": {"$in": ids}}).deleted_count if ids else 0
