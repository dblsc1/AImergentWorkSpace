"""``proj_current`` / ``proj_daily_stats`` 两张投影集合的存取。
**本文件是 projector 子边界唯一碰 mongo 的地方**（两张集合共用本文件，不拆——
红线是「只有 repo.py 能 import mongo」，不是「一集合一文件」）。

窄接口（rules.md §7.4）：

- ``apply_session(user, dedupe_key, project_id, task_id, seconds) -> bool``
  ``proj_current`` 幂等累计：同一 ``dedupe_key`` 只累计一次（True=本次生效，False=早已应用过）。
- ``read_current(user) -> dict | None``
- ``apply_daily_stat(user, dedupe_key, date, project_id, task_id, seconds) -> bool``
  ``proj_daily_stats`` 幂等累计，语义同上，唯一约束换成
  ``(user, date, projectId, taskId)``（contract.md「甘特读端」）。
- ``read_daily_stats(user, date_from=None, date_to=None) -> list[dict]``
- ``clear_current() -> None`` / ``clear_daily_stats() -> None``
  **仅供 ``projector.rebuild`` 使用**：契约「投影重建」硬约束——先清目标投影
  再重放，否则会在已有计数上重复累加。除 rebuild 外别处不许调用这两个函数。

两张集合的幂等实现都是**原子的**，不是「先查再改」：
文档带 ``appliedKeys`` 数组，更新条件是 ``appliedKeys $ne dedupe_key``——
条件不中而 upsert 试图新建时会撞唯一索引（DuplicateKeyError），
那恰好就是「已应用过」的判定。竞态下两个并发投递也只会累计一次。
"""

from __future__ import annotations

from pymongo.errors import DuplicateKeyError

from ...repo import get_db

_COLLECTION = "proj_current"
_DAILY_COLLECTION = "proj_daily_stats"
_indexes_ready = False
_daily_indexes_ready = False


def _col():
    global _indexes_ready
    col = get_db()[_COLLECTION]
    if not _indexes_ready:
        col.create_index([("user", 1)], unique=True, name="uniq_user")
        _indexes_ready = True
    return col


def _daily_col():
    global _daily_indexes_ready
    col = get_db()[_DAILY_COLLECTION]
    if not _daily_indexes_ready:
        col.create_index(
            [("user", 1), ("date", 1), ("projectId", 1), ("taskId", 1)],
            unique=True,
            name="uniq_user_date_project_task",
        )
        _daily_indexes_ready = True
    return col


def apply_session(
    user: str,
    dedupe_key: str,
    project_id: str | None,
    task_id: str | None,
    seconds: int,
) -> bool:
    inc: dict[str, int] = {"totalSeconds": seconds}
    if project_id:
        inc[f"projects.{project_id}"] = seconds
    if task_id:
        inc[f"tasks.{task_id}"] = seconds
    try:
        result = _col().update_one(
            {"user": user, "appliedKeys": {"$ne": dedupe_key}},
            {
                "$inc": inc,
                "$addToSet": {"appliedKeys": dedupe_key},
                "$setOnInsert": {"user": user},
            },
            upsert=True,
        )
    except DuplicateKeyError:
        return False  # 文档在，但 appliedKeys 已含此键 → 早已应用过
    return result.modified_count > 0 or result.upserted_id is not None


def read_current(user: str) -> dict | None:
    return _col().find_one({"user": user}, {"_id": 0})


def clear_current() -> None:
    """重建专用：清空整张 ``proj_current`` 集合（全体用户，本版只有 ``u_local``）。
    只碰投影集合，不碰 ``events``（契约「投影重建」硬约束）。"""
    _col().delete_many({})


def apply_daily_stat(
    user: str,
    dedupe_key: str,
    date: str,
    project_id: str,
    task_id: str | None,
    seconds: int,
) -> bool:
    """``(user, date, projectId, taskId)`` 唯一；同一 ``dedupe_key`` 只累计一次。

    ``task_id`` 可以是 ``None``（外部事件可以没有具体任务，B5）——``None`` 作为
    Mongo 字段值参与唯一索引没有问题，同一 ``(user,date,projectId)`` 下所有
    「无任务」的事件会落进同一份文档，语义上等价于「这个项目当天的无任务时长」。
    """
    try:
        result = _daily_col().update_one(
            {
                "user": user,
                "date": date,
                "projectId": project_id,
                "taskId": task_id,
                "appliedKeys": {"$ne": dedupe_key},
            },
            {
                "$inc": {"seconds": seconds},
                "$addToSet": {"appliedKeys": dedupe_key},
                "$setOnInsert": {
                    "user": user,
                    "date": date,
                    "projectId": project_id,
                    "taskId": task_id,
                },
            },
            upsert=True,
        )
    except DuplicateKeyError:
        return False  # 文档在，但 appliedKeys 已含此键 → 早已应用过
    return result.modified_count > 0 or result.upserted_id is not None


def read_daily_stats(
    user: str,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict]:
    """按用户读出全部（可选按 ``date`` 范围过滤的）按天聚合文档。

    ``date`` 是 ``YYYY-MM-DD`` 字符串，字典序比较与日期序一致，直接用
    ``$gte``/``$lte`` 过滤不需要先转 ``datetime``。
    """
    query: dict = {"user": user}
    date_range: dict = {}
    if date_from:
        date_range["$gte"] = date_from
    if date_to:
        date_range["$lte"] = date_to
    if date_range:
        query["date"] = date_range
    return list(
        _daily_col().find(query, {"_id": 0, "user": 0, "appliedKeys": 0}).sort("date", 1)
    )


def clear_daily_stats() -> None:
    """重建专用：清空整张 ``proj_daily_stats`` 集合。只碰投影集合，不碰
    ``events``（契约「投影重建」硬约束）。"""
    _daily_col().delete_many({})
