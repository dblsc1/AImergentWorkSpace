"""``activity_suggestions`` 集合的存取。**activity 子边界只有本文件与 ``ask_repo.py`` / ``choice_repo.py`` 碰 mongo。**

唯一约束 ``(user, dedupeKey)`` 就是上传防重本身：先查再插在并发下有竞态，唯一索引没有
（同 events/repo.py）。``id`` 由 dedupeKey 派生，所以 ``(user, id)`` 也唯一。
查询一律带 ``user``：别的租户的 id 查不到，形状与「不存在」一样。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from ...repo import get_db

_COLLECTION = "activity_suggestions"
_indexes_ready = False


def _col():
    global _indexes_ready
    col = get_db()[_COLLECTION]
    if not _indexes_ready:
        col.create_index([("user", 1), ("dedupeKey", 1)], unique=True, name="uniq_user_dedupe")
        col.create_index([("user", 1), ("id", 1)], unique=True, name="uniq_user_id")
        col.create_index([("user", 1), ("status", 1), ("startTs", -1)], name="user_status_start")
        # v2.16「此刻的焦点」按以往认：某个程序最近确认的若干条（等值 + 按 decidedAt 倒序，走索引取前 N 条）
        col.create_index([("user", 1), ("status", 1), ("app", 1), ("decidedAt", -1)], name="user_status_app_decided")
        _indexes_ready = True
    return col


def insert_if_absent(doc: dict) -> bool:
    """True=新写入，False=防重命中（**什么都不改**：已确认/已忽略的不会被重传改回 pending）。"""
    try:
        _col().insert_one(dict(doc))
    except DuplicateKeyError:
        return False
    return True


def get(user: str, sug_id: str) -> dict | None:
    return _col().find_one({"user": user, "id": sug_id}, {"_id": 0})


def page(user: str, status: str, limit: int, offset: int) -> tuple[int, list[dict]]:
    filt = {"user": user, "status": status}
    col = _col()
    docs = col.find(filt, {"_id": 0}).sort("startTs", -1).skip(offset).limit(limit)
    return col.count_documents(filt), list(docs)


def confirmed(user: str, cap: int) -> list[dict]:
    """v2.12 匹配历史：已确认的建议，最近处理的在前，至多 ``cap`` 条（只取用得着的字段）。"""
    proj = {"_id": 0, "id": 1, "app": 1, "title": 1, "decidedAt": 1, "suggestion.collection": 1}
    # v2.14：自动记下的不算——那不是人的决定
    filt = {"user": user, "status": "confirmed", "auto": {"$ne": True}}
    return list(_col().find(filt, proj).sort("decidedAt", -1).limit(cap))


def confirmed_for_app(user: str, app: str, cap: int) -> list[dict]:
    """v2.16 此刻的焦点：这个程序下人确认过的建议，最近处理的在前，至多 ``cap`` 条（自动记下的不算）。"""
    filt = {"user": user, "status": "confirmed", "app": app, "auto": {"$ne": True}}
    return list(_col().find(filt, {"_id": 0, "id": 1, "app": 1, "title": 1}).sort("decidedAt", -1).limit(cap))


def auto_between(user: str, start: datetime, end: datetime, cap: int) -> list[dict]:
    """v2.14 自动记下的段：``startTs`` 在 [start, end) 里的，新的在前，至多 ``cap`` 条。"""
    filt = {"user": user, "status": "confirmed", "auto": True, "startTs": {"$gte": start, "$lt": end}}
    return list(_col().find(filt, {"_id": 0}).sort("startTs", -1).limit(cap))


def with_rejections(user: str, cap: int) -> list[dict]:
    """v2.12 匹配历史：人否掉过任务的建议（任何状态），新的在前，至多 ``cap`` 条。"""
    proj = {"_id": 0, "app": 1, "title": 1, "rejectedTaskIds": 1}
    filt = {"user": user, "rejectedTaskIds.0": {"$exists": True}}
    return list(_col().find(filt, proj).sort("startTs", -1).limit(cap))


def set_status(user: str, sug_id: str, status: str, at: datetime, *, only_from: str | None = None) -> bool:
    """改状态。``only_from`` 给了就是条件更新（忽略只能从 pending 转过去，防与确认赛跑）。"""
    filt = {"user": user, "id": sug_id}
    if only_from is not None:
        filt["status"] = only_from
    return _col().update_one(filt, {"$set": {"status": status, "decidedAt": at}}).matched_count > 0


def claim(user: str, sug_id: str, at: datetime, *, takeover: bool = False, only_task: str | None = None,
          only_proposal: str | None = None, auto: bool | str = False) -> bool:
    """确认的占位：pending→confirmed，``claims`` 记有几个确认正占着（在写事实）。``takeover`` = 加入一个已占位、
    事实还没写的（对方还在写，或占位后崩了）。``only_task`` 给了还要求建议的任务仍是它
    （v2.7：确认用的是建议里的任务时，防与否 / 重配赛跑）。``auto`` = v2.14 自动记录占的位（标 ``auto: true``）；
    给的是字符串（``"rules"`` / ``"choice"``）就一并记成 ``autoSource``——按规则记的还是按人的临时选择记的。"""
    filt = {"user": user, "id": sug_id, "status": "confirmed" if takeover else "pending"}
    if only_task is not None:
        filt["suggestion.taskId"] = only_task
    if only_proposal is not None:  # v2.8：确认的是助理提议的新任务，要求提议没变
        filt["suggestion.newTask.proposalId"] = only_proposal
    upd = {"$inc": {"claims": 1}} if takeover else {
        "$set": {"status": "confirmed", "decidedAt": at, "claims": 1, **({"auto": True} if auto else {}),
                 **({"autoSource": auto} if isinstance(auto, str) else {})}}
    return _col().update_one(filt, upd).matched_count > 0


def release(user: str, sug_id: str, at: datetime) -> None:
    """写事实失败：退出占位；**没有别的确认还占着**才放回 pending（还有人在写就不放，免得它写成后状态却是待确认）。"""
    filt = {"user": user, "id": sug_id, "status": "confirmed"}
    _col().update_one(filt, {"$inc": {"claims": -1}})
    _col().update_one({**filt, "claims": {"$lte": 0}},
                      {"$set": {"status": "pending", "decidedAt": at}, "$unset": {"auto": "", "autoSource": ""}})


def set_match(user: str, sug_id: str, suggestion: dict) -> bool:
    """v2.7 助理配任务：只在仍 pending、这个任务没被人否过、原建议可盖（没任务或也是助理配的）时换上。
    v2.8 配的是新任务提议时，换成「这个提议没被人否过」。"""
    filt = {"user": user, "id": sug_id, "status": "pending",
            "$or": [{"suggestion.taskId": None}, {"suggestion.classifier": "assistant"}]}
    if suggestion.get("newTask"):
        filt["rejectedProposalIds"] = {"$ne": suggestion["newTask"]["proposalId"]}
    else:
        filt["rejectedTaskIds"] = {"$ne": suggestion["taskId"]}
    return _col().update_one(filt, {"$set": {"suggestion": suggestion}}).matched_count > 0


def set_labels(user: str, sug_id: str, labels: dict) -> bool:
    """v2.10 只贴标签（``suggestion.collection`` / ``suggestion.projectId``）：仍 pending 才写，任务、把握、来源不动。"""
    upd: dict = {"$set": {f"suggestion.{k}": v for k, v in labels.items()}}
    if "projectId" in labels:  # v2.13：助理给了项目，就不再是「按代理会话对上的」
        upd["$unset"] = {"suggestion.projectSource": ""}
    return _col().update_one({"user": user, "id": sug_id, "status": "pending"}, upd).matched_count > 0


def clear_match(user: str, sug_id: str, task_id: str) -> bool:
    """v2.7 人说「否」：只在仍 pending 且建议的任务还是 ``task_id`` 时清掉，并记住它。"""
    filt = {"user": user, "id": sug_id, "status": "pending", "suggestion.taskId": task_id}
    upd = {"$set": {"suggestion.taskId": None, "suggestion.confidence": 0.0, "suggestion.reason": ""},
           "$addToSet": {"rejectedTaskIds": task_id}}
    return _col().update_one(filt, upd).matched_count > 0


def clear_proposal(user: str, sug_id: str, proposal_id: str) -> bool:
    """v2.8 人对提议说「否」：只在仍 pending 且建议的提议还是它时清掉，并记住。"""
    filt = {"user": user, "id": sug_id, "status": "pending", "suggestion.newTask.proposalId": proposal_id}
    upd = {"$set": {"suggestion.confidence": 0.0, "suggestion.reason": ""}, "$unset": {"suggestion.newTask": ""},
           "$addToSet": {"rejectedProposalIds": proposal_id}}
    return _col().update_one(filt, upd).matched_count > 0


def last_received(user: str) -> dict:
    rows = _col().aggregate([{"$match": {"user": user}},
                             {"$group": {"_id": "$deviceId", "at": {"$max": "$receivedAt"}}}])
    return {r["_id"]: r["at"] for r in rows}


def purge(user: str, cutoff: datetime) -> None:
    """待确认的按收到时刻、已处理的按处理时刻，早于 cutoff 的删掉（契约「过期」）。"""
    _col().delete_many({"user": user, "$or": [
        {"status": "pending", "receivedAt": {"$lt": cutoff}},
        {"status": {"$ne": "pending"}, "decidedAt": {"$lt": cutoff}},
    ]})
    # v2.8 提议：最后一次被提 / 被处理之后过期（总比指着它的建议活得久）
    _proposals_col().delete_many({"user": user, "updatedAt": {"$lt": cutoff}})


# ------------------------------------------------ activity_task_proposals（v2.8，AI 提议的新任务，不是事实）
#
# 每个 (user, 项目, 归一化名字) 一份（id 由它派生）：status pending / accepted（人点了「是」）/ rejected。
# taskId = 第一次提出时预留的任务 id（建任务就用它，见 proposals.task_for）；
# createClaimedAt = 有一次确认占了「建」（持久，任务被删也不清，于是同一 id 永远只建一次）。

_PROPOSALS = "activity_task_proposals"
_proposals_ready = False


def _proposals_col():
    global _proposals_ready
    col = get_db()[_PROPOSALS]
    if not _proposals_ready:
        col.create_index([("user", 1), ("id", 1)], unique=True, name="uniq_user_id")
        _proposals_ready = True
    return col


def proposal_get(user: str, proposal_id: str) -> dict | None:
    return _proposals_col().find_one({"user": user, "id": proposal_id}, {"_id": 0})


def proposal_upsert(user: str, proposal_id: str, project_id: str, name: str, at: datetime) -> dict:
    """取（或新建）提议，刷新 updatedAt。名字与预留的任务 id 只在第一次提出时定下。返回现在的文档。
    任务 id 是新的随机号（同 planner 建任务），不由提议 id 派生：提议过期后再被提出，拿到的是新号，
    人删掉的任务 id 永远不会被再用（planner「id 不可复用」）。"""
    insert = {"projectId": project_id, "name": name, "status": "pending", "taskId": f"t_{uuid.uuid4().hex[:12]}",
              "createClaimedAt": None, "createdAt": at}
    return _proposals_col().find_one_and_update(
        {"user": user, "id": proposal_id}, {"$setOnInsert": insert, "$set": {"updatedAt": at}},
        upsert=True, return_document=ReturnDocument.AFTER, projection={"_id": 0})


def proposal_statuses(user: str, proposal_ids: list[str]) -> dict:
    """{提议 id: status}；查不到（过期）的不在里面。"""
    rows = _proposals_col().find({"user": user, "id": {"$in": proposal_ids}}, {"id": 1, "status": 1})
    return {r["id"]: r["status"] for r in rows}


def proposal_accept(user: str, proposal_id: str, name: str | None, at: datetime) -> None:
    """人点「是」：pending → accepted（条件更新，与否掉二选一），定下名字（给了才改）。已 accepted / rejected 不动。"""
    upd = {"status": "accepted", "updatedAt": at, **({"name": name} if name else {})}
    _proposals_col().update_one({"user": user, "id": proposal_id, "status": "pending"}, {"$set": upd})


def proposal_claim_create(user: str, proposal_id: str, task_id: str, at: datetime) -> dict | None:
    """占「建」：只有一次能占到（空 → 现在，条件更新）。返回占到的那份提议，没占到为 None。
    条件里带调用方读到的预留任务 id：提议 id 由（项目, 名字）派生，过期清掉又被重新提出时 id 相同、预留的任务 id 不同——
    拿着旧读数的调用方占不到新的那份（ABA）。"""
    filt = {"user": user, "id": proposal_id, "taskId": task_id, "status": "accepted", "createClaimedAt": None}
    return _proposals_col().find_one_and_update(filt, {"$set": {"createClaimedAt": at}},
                                                return_document=ReturnDocument.AFTER, projection={"_id": 0})


def proposal_reject_if_unused(user: str, proposal_id: str, at: datetime) -> None:
    """没有待确认的建议还指着它、且没建成 → 标已否掉（助理不许再提）。"""
    if _col().count_documents({"user": user, "status": "pending", "suggestion.newTask.proposalId": proposal_id},
                              limit=1):
        return
    _proposals_col().update_one({"user": user, "id": proposal_id, "status": "pending"},
                                {"$set": {"status": "rejected", "updatedAt": at}})


# ------------------------------------------------ activity_presence（v2.4，在场心跳，活状态）
#
# 每个 (user, deviceId) 一份文档：最新一次心跳 + 合并过的近况 spans。不是事实：
# 不进台账 / 投影 / 导出 / 快照恢复。过期文档只在心跳写入时删（读端只过滤）。

_PRESENCE = "activity_presence"
_presence_ready = False


def _presence_col():
    global _presence_ready
    col = get_db()[_PRESENCE]
    if not _presence_ready:
        col.create_index([("user", 1), ("deviceId", 1)], unique=True, name="uniq_user_device")
        col.create_index([("user", 1), ("lastAt", 1)], name="user_last")
        _presence_ready = True
    return col


def presence_get(user: str, device_id: str) -> dict | None:
    return _presence_col().find_one({"user": user, "deviceId": device_id}, {"_id": 0})


def presence_cas(doc: dict, version: int | None) -> bool:
    """版本号 ``v`` 没变才整份换掉（乐观锁，同 detector/repo 的写法）。False = 同一台设备的另一拍抢先写了，调用方重读再算。
    ``version`` = 读到的那份的 ``v``（没读到文档 / v2.17.1 之前的文档没有这个键 = None，``{"v": None}`` 恰好匹配缺字段）；
    文档在而版本对不上时 upsert 撞唯一键 = 冲突。"""
    try:
        _presence_col().replace_one({"user": doc["user"], "deviceId": doc["deviceId"], "v": version},
                                    {**doc, "v": (version or 0) + 1}, upsert=True)
    except DuplicateKeyError:
        return False
    return True


def presence_purge(user: str, cutoff: datetime) -> None:
    _presence_col().delete_many({"user": user, "lastAt": {"$lt": cutoff}})


def presence_evictable(user: str, keep_self: str, cap: int) -> list[str]:
    """除 ``keep_self`` 外按最近心跳排，最旧的那些设备 id——删掉它们后该租户至多 ``cap`` 台。"""
    ids = [d["deviceId"] for d in _presence_col().find({"user": user, "deviceId": {"$ne": keep_self}},
                                                       {"deviceId": 1}).sort("lastAt", 1)]
    return ids[: max(len(ids) - (cap - 1), 0)]


def presence_delete(user: str, device_ids: list[str]) -> None:
    if device_ids:
        _presence_col().delete_many({"user": user, "deviceId": {"$in": device_ids}})


def presence_list(user: str) -> list[dict]:
    return list(_presence_col().find({"user": user}, {"_id": 0}))
