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


def relabel_agent_run(user: str, run_id: str, fields: dict) -> None:
    """v2.13 会话改名：未关闭才改 ``label`` / ``match``。不碰 ``v``——相位 / 连线的乐观锁管的是那两个数组。"""
    _agent_col().update_one({"user": user, "runId": run_id, "closing": {"$exists": False}}, {"$set": fields})


def mark_agent_run_closing(user: str, run_id: str, marker: dict) -> dict | None:
    """关闭边界（v2.4）：一次条件更新打上关闭标记，取回**那一刻的整份文档**作快照。
    None = 不存在或已被别人标记。标记后相位 / attend 的条件更新一律落空。"""
    return _agent_col().find_one_and_update(
        {"user": user, "runId": run_id, "closing": {"$exists": False}},
        {"$set": {"closing": marker}, "$unset": {"clientKey": ""}},
        projection={"_id": 0},
        return_document=ReturnDocument.AFTER,
    )


def cas_agent_run(user: str, run_id: str, version: int | None, fields: dict) -> bool:
    """未关闭 + 版本号没变才写（乐观锁）。False = 被并发写抢先或已关闭，调用方重读再算。
    v2.4 之前的文档没有 ``v``：``{"v": None}`` 恰好匹配缺字段。"""
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
