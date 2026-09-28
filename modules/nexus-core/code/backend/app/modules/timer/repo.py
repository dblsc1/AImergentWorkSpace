"""``timer_state`` 集合的存取。**本文件是 timer 子边界唯一碰 mongo 的地方。**

一个 user 至多一条活状态（唯一索引 ``user``）——「start 自动关上一个」
在数据层就成立，不靠上层自觉。
"""

from __future__ import annotations

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
        _agent_indexes_ready = True
    return col


def add_agent_run(doc: dict) -> None:
    _agent_col().insert_one(dict(doc))


def get_agent_run(user: str, run_id: str) -> dict | None:
    return _agent_col().find_one({"user": user, "runId": run_id}, {"_id": 0})


def list_agent_runs(user: str) -> list[dict]:
    return list(_agent_col().find({"user": user}, {"_id": 0}).sort("startedAt", 1))


def delete_agent_run(user: str, run_id: str) -> None:
    _agent_col().delete_one({"user": user, "runId": run_id})
