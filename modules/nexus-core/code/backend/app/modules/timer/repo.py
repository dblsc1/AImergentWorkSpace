"""``timer_state`` 集合的存取。**本文件是 timer 子边界唯一碰 mongo 的地方。**

一个 user 至多一条活状态（唯一索引 ``user``）——「start 自动关上一个」
在数据层就成立，不靠上层自觉。
"""

from __future__ import annotations

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

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


# ------------------------------------------------ agent_runs（v2.1，AI 代理运行活状态）
#
# 与 timer_state 相反：**一个 user 可以有任意多条**（没有 uniq_user）——人一条泳道，
# 代理很多条。唯一约束是 runId（uuid 生成），查询一律带 user，别的租户的 runId 查不到。

_AGENT_COLLECTION = "agent_runs"
_agent_indexes_ready = False


def _agent_col():
    global _agent_indexes_ready
    col = get_db()[_AGENT_COLLECTION]
    if not _agent_indexes_ready:
        col.create_index([("runId", 1)], unique=True, name="uniq_run")
        col.create_index([("user", 1), ("startedAt", 1)], name="user_started")
        # v2.4：同一租户里同一 clientKey 至多一个**未关闭**的运行——关闭标记同时 $unset 掉 clientKey，
        # 所以部分索引只管还在跑的；并发的两次同 key start 由它挡成一条。
        col.create_index(
            [("user", 1), ("clientKey", 1)], unique=True, name="uniq_user_client_key",
            partialFilterExpression={"clientKey": {"$type": "string"}},
        )
        _agent_indexes_ready = True
    return col


def add_agent_run(doc: dict) -> bool:
    """False = 同租户同 clientKey 的运行还在跑（唯一索引挡下）。"""
    try:
        _agent_col().insert_one(dict(doc))
    except DuplicateKeyError:
        return False
    return True


def find_agent_run_by_client_key(user: str, client_key: str) -> dict | None:
    return _agent_col().find_one({"user": user, "clientKey": client_key}, {"_id": 0})


def count_unverified_agent_runs(user: str) -> int:
    """v2.19：该租户还在跑（未打关闭标记）的匿名运行个数。"""
    return _agent_col().count_documents({"user": user, "unverified": True, "closing": {"$exists": False}})


def relabel_agent_run(user: str, run_id: str, fields: dict) -> None:
    """v2.13 会话改名：未关闭才改 ``label`` / ``match``。不碰 ``v``——相位 / 连线的乐观锁管的是那两个数组。"""
    _agent_col().update_one({"user": user, "runId": run_id, "closing": {"$exists": False}}, {"$set": fields})


def touch_agent_run(
    user: str, run_id: str, seen_at: str, declare: bool = False, beat_source: str | None = None, beat: bool = False,
) -> bool:
    """v2.18 活性：未关闭才记 ``lastSeenAt``；``declare`` = 同时记下「这个运行会发心跳」；``beat_source`` 给了就记
    （谁在发心跳，后来的盖先来的）；``beat`` = 这是一次心跳，``beatCount`` 加一。
    不碰 ``v``（同 relabel）。False = 不存在或已关闭。
    ``lastSeenAt`` 单调：只增不减、不早于 ``startedAt``（聚合管道更新，``$max``）——迟到的老请求不会把新信号盖回去，
    也不会让失联关闭把结束时刻算到起点之前。"""
    fields = {"lastSeenAt": _seen_max(seen_at), **({"heartbeat": True} if declare else {}),
              **({"beatSource": {"$literal": beat_source}} if beat_source else {}),
              **({"beatCount": {"$add": [{"$ifNull": ["$beatCount", 0]}, 1]}} if beat else {})}
    return _agent_col().update_one(
        {"user": user, "runId": run_id, "closing": {"$exists": False}}, [{"$set": fields}],
    ).matched_count > 0


def _seen_max(seen_at: str) -> dict:
    return {"$max": ["$lastSeenAt", "$startedAt", {"$literal": seen_at}]}


def mark_agent_run_closing(user: str, run_id: str, marker: dict, guard: dict | None = None) -> dict | None:
    """关闭边界（v2.4）：一次条件更新打上关闭标记，取回**那一刻的整份文档**作快照。
    None = 不存在或已被别人标记。标记后相位 / attend 的条件更新一律落空。
    ``guard``（v2.18）：追加的相等条件——失联关闭带着读到的 ``lastSeenAt``，这期间又来了信号就不关。"""
    return _agent_col().find_one_and_update(
        {"user": user, "runId": run_id, "closing": {"$exists": False}, **(guard or {})},
        {"$set": {"closing": marker}, "$unset": {"clientKey": ""}},
        projection={"_id": 0},
        return_document=ReturnDocument.AFTER,
    )


def cas_agent_run(user: str, run_id: str, version: int | None, fields: dict, seen_at: str | None = None) -> bool:
    """未关闭 + 版本号没变才写（乐观锁）。False = 被并发写抢先或已关闭，调用方重读再算。
    v2.4 之前的文档没有 ``v``：``{"v": None}`` 恰好匹配缺字段。
    ``seen_at``（v2.18）：同一次条件更新里一并推进 ``lastSeenAt``（单调）——相位写入与活性信号是一个原子步骤，
    失联清理带着旧 ``lastSeenAt`` 的关闭条件于是落空，不会出现「相位收下了、随后运行被按旧信号关掉」。"""
    if seen_at is not None:
        return _agent_col().update_one(
            {"user": user, "runId": run_id, "closing": {"$exists": False}, "v": version},
            [{"$set": {**{k: {"$literal": x} for k, x in fields.items()}, "lastSeenAt": _seen_max(seen_at),
                       "v": {"$add": [{"$ifNull": ["$v", 0]}, 1]}}}],
        ).matched_count > 0
    return _agent_col().update_one(
        {"user": user, "runId": run_id, "closing": {"$exists": False}, "v": version},
        {"$set": fields, "$inc": {"v": 1}},
    ).matched_count > 0


def get_agent_run(user: str, run_id: str) -> dict | None:
    return _agent_col().find_one({"user": user, "runId": run_id}, {"_id": 0})


def list_agent_runs(user: str) -> list[dict]:
    return list(_agent_col().find({"user": user}, {"_id": 0}).sort("startedAt", 1))


def delete_agent_run(user: str, run_id: str) -> None:
    _agent_col().delete_one({"user": user, "runId": run_id})
