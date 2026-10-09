"""``activity_reports`` 集合的存取（v2.20，AI 报告，不是事实）。``repo.py`` 贴着 300 行预算，拆出来。

每个 (user, id) 一份：``{user, id, author, summary, status, createdAt, decidedAt, items[]}``，条见 ``reports.py``。
查询一律带 ``user``：别的租户的 id 查不到，形状与「不存在」一样。读 ``activity_suggestions`` 的两个查询也放在这里
（只读，带 ``user``），免得为报告去改 ``repo.py``。
"""

from __future__ import annotations

from datetime import datetime

from pymongo import UpdateOne

from ...repo import get_db

_COLLECTION = "activity_reports"
_indexes_ready = False
#: 条还能批准 / 不要的状态
OPEN = ("pending", "failed")


def _col():
    global _indexes_ready
    col = get_db()[_COLLECTION]
    if not _indexes_ready:
        col.create_index([("user", 1), ("id", 1)], unique=True, name="uniq_user_id")
        col.create_index([("user", 1), ("status", 1), ("createdAt", -1)], name="user_status_created")
        _indexes_ready = True
    return col


def insert(doc: dict) -> None:
    _col().insert_one(dict(doc))


def get(user: str, report_id: str) -> dict | None:
    return _col().find_one({"user": user, "id": report_id}, {"_id": 0})


def page(user: str, pending_only: bool, limit: int) -> list[dict]:
    filt = {"user": user, **({"status": "pending"} if pending_only else {})}
    return list(_col().find(filt, {"_id": 0}).sort("createdAt", -1).limit(limit))


def pending_count(user: str) -> int:
    return _col().count_documents({"user": user, "status": "pending"})


def purge(user: str, cutoff: datetime) -> None:
    _col().delete_many({"user": user, "createdAt": {"$lt": cutoff}})


# ---- 条的条件更新：都只动 ``user`` + ``id`` 对上的那一份里 ``itemId`` 那一条

def _item_filter(item_id: str, **extra) -> list[dict]:
    return [{"i.id": item_id, **{f"i.{k}": v for k, v in extra.items()}}]


def set_target(user: str, report_id: str, item_id: str, fields: dict, unset: list[str]) -> bool:
    """改目标（只在条还 pending / failed、报告还 pending 时）。"""
    upd: dict = {"$set": {f"items.$[i].{k}": v for k, v in fields.items()}}
    if unset:
        upd["$unset"] = {f"items.$[i].{k}": "" for k in unset}
    return _col().update_one({"user": user, "id": report_id, "status": "pending"}, upd,
                             array_filters=_item_filter(item_id, status={"$in": list(OPEN)})).modified_count > 0


def set_results(user: str, report_id: str, item_id: str, results: dict[str, dict]) -> None:
    """写逐段结果。``applied`` 一旦写下不再被改（并发的另一个批准写的 ``stale`` 盖不掉它）。"""
    ops = [UpdateOne({"user": user, "id": report_id}, {"$set": {f"items.$[i].results.{sid}": res}},
                     array_filters=_item_filter(item_id, **{f"results.{sid}.state": {"$ne": "applied"}}))
           for sid, res in results.items()]
    if ops:
        _col().bulk_write(ops, ordered=False)


def set_item_status(user: str, report_id: str, item_id: str, status: str, frm: tuple = OPEN) -> bool:
    """条 ``frm``（缺省 pending / failed）→ status（条件更新；rejected 的永远不动）。"""
    return _col().update_one({"user": user, "id": report_id}, {"$set": {"items.$[i].status": status}},
                             array_filters=_item_filter(item_id, status={"$in": list(frm)})).modified_count > 0


def reject_open_items(user: str, report_id: str) -> None:
    _col().update_one({"user": user, "id": report_id}, {"$set": {"items.$[i].status": "rejected"}},
                      array_filters=[{"i.status": {"$in": list(OPEN)}}])


def finish(user: str, report_id: str, status: str, at: datetime) -> bool:
    return _col().update_one({"user": user, "id": report_id, "status": "pending"},
                             {"$set": {"status": status, "decidedAt": at}}).modified_count > 0


# ---- activity_suggestions 的只读查询（报告用）

def _sugs():
    return get_db()["activity_suggestions"]


def suggestions(user: str, ids: list[str]) -> dict[str, dict]:
    proj = {"_id": 0, "id": 1, "status": 1, "app": 1, "title": 1, "startAt": 1, "durationSeconds": 1,
            "rejectedTaskIds": 1, "suggestion.taskId": 1, "suggestion.classifier": 1}  # 后三个给 service.match_refusal
    return {d["id"]: d for d in _sugs().find({"user": user, "id": {"$in": ids}}, proj)}


def pending_in_collection(user: str, key: str, cap: int) -> list[str]:
    """集合（``suggestion.collection.key``）里此刻待确认的建议 id，新的在前，至多 ``cap`` 个。"""
    filt = {"user": user, "status": "pending", "suggestion.collection.key": key}
    return [d["id"] for d in _sugs().find(filt, {"_id": 0, "id": 1}).sort("startTs", -1).limit(cap)]
