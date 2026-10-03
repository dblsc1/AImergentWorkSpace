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


# ------------------------------------------------ proj_agent_daily_stats（v2.1）
#
# AI 代理时长单独一张集合，**不**往 proj_daily_stats 里加一个 agent 维度——
# 人的一切读端都读那张表，加维度就得每个读端记得过滤；分表是结构上看不见，不靠自觉。

_AGENT_DAILY_COLLECTION = "proj_agent_daily_stats"
_agent_daily_indexes_ready = False


def _agent_daily_col():
    global _agent_daily_indexes_ready
    col = get_db()[_AGENT_DAILY_COLLECTION]
    if not _agent_daily_indexes_ready:
        col.create_index(
            [("user", 1), ("date", 1), ("projectId", 1), ("taskId", 1), ("agent", 1)],
            unique=True,
            name="uniq_user_date_project_task_agent",
        )
        _agent_daily_indexes_ready = True
    return col


def apply_agent_daily_stat(
    user: str,
    dedupe_key: str,
    date: str,
    project_id: str,
    task_id: str | None,
    agent: str,
    seconds: int,
) -> bool:
    """同 ``apply_daily_stat`` 的原子幂等写法，唯一约束多一个 ``agent``；另计 ``runs`` 次数。"""
    key = {"user": user, "date": date, "projectId": project_id, "taskId": task_id, "agent": agent}
    query = {**key, "appliedKeys": {"$ne": dedupe_key}}
    update = {"$inc": {"seconds": seconds, "runs": 1}, "$addToSet": {"appliedKeys": dedupe_key}}
    try:
        result = _agent_daily_col().update_one(query, {**update, "$setOnInsert": key}, upsert=True)
    except DuplicateKeyError:
        # 撞唯一索引有两种可能：① 本键早已应用过；② 并发的**另一条**事件刚好抢先建出了
        # 同一行（几个代理同时 stop 在同一任务同一天——代理泳道的常态，人的泳道是串行的
        # 碰不到）。②不能当成「已应用」直接返回，否则那次运行的时长就漏了。行此刻一定在，
        # 不带 upsert 再试一次：条件中 = 本键没应用过、现在加上；不中 = 真的应用过。
        result = _agent_daily_col().update_one(query, update)
        return result.modified_count > 0
    return result.modified_count > 0 or result.upserted_id is not None


def read_agent_daily_stats(user: str, date_from: str | None = None, date_to: str | None = None) -> list[dict]:
    """同 ``read_daily_stats``：按用户、可选闭区间日期过滤（v2.3 读端）。"""
    query: dict = {"user": user}
    date_range = {op: v for op, v in (("$gte", date_from), ("$lte", date_to)) if v}
    if date_range:
        query["date"] = date_range
    return list(_agent_daily_col().find(query, {"_id": 0, "user": 0, "appliedKeys": 0}))


def clear_agent_daily_stats() -> None:
    """重建专用，同 ``clear_daily_stats``。"""
    _agent_daily_col().delete_many({})


# ------------------------------------------------ proj_lanes（v2.4，时间线区间）
#
# 一条事实 → 一份文档（人的一段 / 代理的一次运行），只为按时间区间一次查出来画图。
# **不求和、不进任何人的汇总**。幂等靠唯一约束 (user, key)，key = 已应用身份。

_LANES_COLLECTION = "proj_lanes"
_lanes_indexes_ready = False


def _lanes_col():
    global _lanes_indexes_ready
    col = get_db()[_LANES_COLLECTION]
    if not _lanes_indexes_ready:
        col.create_index([("user", 1), ("key", 1)], unique=True, name="uniq_user_key")
        col.create_index([("user", 1), ("kind", 1), ("startAt", -1)], name="user_kind_start")
        _lanes_indexes_ready = True
    return col


def apply_lane(doc: dict) -> bool:
    """同一 (user, key) 只写一次；重放 / 重投递是 no-op。"""
    try:
        result = _lanes_col().update_one(
            {"user": doc["user"], "key": doc["key"]}, {"$setOnInsert": doc}, upsert=True,
        )
    except DuplicateKeyError:
        return False  # 并发的同一条抢先插入
    return result.upserted_id is not None


def read_lanes(user: str, kind: str, start, end, limit: int) -> list[dict]:
    """与 [start, end) 有重叠的区间，最新（startAt 大）的在前，至多 ``limit`` 条。"""
    query = {"user": user, "kind": kind, "startAt": {"$lt": end}, "endAt": {"$gt": start}}
    return list(_lanes_col().find(query, {"_id": 0}).sort("startAt", -1).limit(limit))


def clear_lanes() -> None:
    """重建专用，同 ``clear_daily_stats``。"""
    _lanes_col().delete_many({})


def lanes_empty() -> bool:
    return _lanes_col().find_one({}, {"_id": 1}) is None


# ------------------------------------------------ 启动期一次性任务的锁与进度标记（v2.4：proj_lanes 自动补建）
#
# 锁：多个实例同时启动时只让一个去做；带持有者令牌，只删自己的；过了 ``stale`` 可被接管。
# 补建本身幂等（唯一约束），锁只省重复劳动，不承担正确性（ponytail: 不续租，超时后可能两个
# 实例同时重放——结果一样，只是多做一遍）。
# 进度标记 ``<name>:pending``：重放前写、成功后删；崩在半路留下它，下次启动即使集合已非空也接着补。

_LOCKS_COLLECTION = "_startup_locks"


def acquire_startup_lock(name: str, owner: str, now, stale) -> bool:
    try:
        get_db()[_LOCKS_COLLECTION].find_one_and_update(
            {"_id": name, "at": {"$lt": now - stale}}, {"$set": {"at": now, "owner": owner}}, upsert=True,
        )
    except DuplicateKeyError:
        return False  # 锁在且没过期：别的实例正在做
    return True


def release_startup_lock(name: str, owner: str) -> None:
    get_db()[_LOCKS_COLLECTION].delete_one({"_id": name, "owner": owner})


def set_pending(name: str, pending: bool) -> None:
    col = get_db()[_LOCKS_COLLECTION]
    if pending:
        col.update_one({"_id": f"{name}:pending"}, {"$set": {"pending": True}}, upsert=True)
    else:
        col.delete_one({"_id": f"{name}:pending"})


def is_pending(name: str) -> bool:
    return get_db()[_LOCKS_COLLECTION].find_one({"_id": f"{name}:pending"}) is not None
