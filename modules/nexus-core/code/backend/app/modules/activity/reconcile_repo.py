"""对账用的存取（契约 v2.38「四」）：块文档上的比较并交换、候选块、开着三级分类的租户。规矩同 ``blocks_repo.py``：查询一律带 ``user``。

块文档上对账另加的字段：``runs``（已写的段）、``voids``（已从 ``runs`` 拿掉、作废事件还没写成的）、``rv``（对账版本，对账之间的比较并交换）、
``reconciling`` / ``more``（动作没做完 / 预算不够留到下一轮）。``seq`` 只在分类字段变了时 +1：写 ``runs`` 不能让人的 ``ifSeq`` 白白失效。
"""

from __future__ import annotations

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from ...repo import get_db
from . import blocks_repo

_BLK, _SET = "activity_blocks", "activity_settings"


def _blk():
    return blocks_repo._col(_BLK)  # noqa: SLF001 —— 借它建好索引


def tier_users() -> list[str]:
    return [d["user"] for d in get_db()[_SET].find({"tiers": True}, {"user": 1})]


def work_blocks(user: str) -> list[tuple[str, str]]:
    """有已写的段、没做完的动作的块（要作废 / 续做）。"""
    q = {"user": user, "$or": [{"runs.0": {"$exists": True}}, {"voids.0": {"$exists": True}}, {"reconciling": True}]}
    return [(d["date"], d["blockKey"]) for d in _blk().find(q, {"date": 1, "blockKey": 1})]


def _rv(doc: dict) -> dict:
    return {"$or": [{"rv": 0}, {"rv": {"$exists": False}}]} if not doc.get("rv") else {"rv": doc["rv"]}


def commit(user: str, date: str, key: str, old: dict | None, fields: dict, inc_seq: int, inc_gen: int) -> dict | None:
    """对账的第一步（先写意图）：``seq`` 与 ``rv`` 都没变才提交；没有文档就建（``seq`` 1、``gen`` ≥ 1）。不符 → None。"""
    if old is None:
        doc = {"status": None, "parts": None, "via": None, "ruleId": None, **fields,
               "user": user, "date": date, "blockKey": key, "seq": 1, "gen": max(inc_gen, 1), "rv": 1}
        try:
            _blk().insert_one(dict(doc))
        except DuplicateKeyError:
            return None
        return doc
    flt = {"$and": [{"user": user, "date": date, "blockKey": key, "seq": old["seq"]}, _rv(old)]}
    return _blk().find_one_and_update(flt, {"$set": fields, "$inc": {"rv": 1, "seq": inc_seq, "gen": inc_gen}},
                                      projection={"_id": 0}, return_document=ReturnDocument.AFTER)


def pending_blocks(user: str) -> list[tuple[str, str]]:
    """意图写了、事件没写完的块（tiers 关掉后也要收口）。"""
    q = {"user": user, "$or": [{"voids.0": {"$exists": True}}, {"runs.pend": {"$exists": True}}]}
    return [(d["date"], d["blockKey"]) for d in _blk().find(q, {"date": 1, "blockKey": 1})]


def pending_users() -> list[str]:
    q = {"$or": [{"voids.0": {"$exists": True}}, {"runs.pend": {"$exists": True}}]}
    return list(_blk().distinct("user", q))


def reconciling_blocks(user: str) -> list[tuple[str, str]]:
    return [(d["date"], d["blockKey"]) for d in _blk().find({"user": user, "reconciling": True}, {"date": 1, "blockKey": 1})]


def mark_reconciling(user: str, date: str, key: str) -> None:
    _blk().update_one({"user": user, "date": date, "blockKey": key}, {"$set": {"reconciling": True}})


def finish(user: str, date: str, key: str, doc: dict, runs: list[dict], blocked: list[dict]) -> bool:
    """对账的第二步（事件写完）：``rv`` 没变才收尾——去掉 ``pend``、清 ``voids``，``reconciling`` 看是不是还有留到下一轮的。"""
    flt = {"$and": [{"user": user, "date": date, "blockKey": key}, _rv(doc)]}
    more = bool(doc.get("more"))
    return _blk().update_one(flt, {"$set": {"runs": runs, "voids": [], "reconciling": more, "more": more, "blocked": blocked}, "$inc": {"rv": 1}}).matched_count == 1


def clear_flag(user: str, date: str, key: str, doc: dict) -> None:
    _blk().update_one({"$and": [{"user": user, "date": date, "blockKey": key}, _rv(doc)]},
                      {"$set": {"reconciling": False, "more": False}, "$inc": {"rv": 1}})
