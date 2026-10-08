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
条件不中而 upsert 试图新建时会撞唯一索引（DuplicateKeyError）。撞了**不等于**「已应用过」：
也可能是并发的另一条写刚把这份文档 / 这一行建出来。所以每个带 upsert 的累计都在撞了之后不带 upsert
再试一次，由那一次的条件中不中来判——同一条并发投两遍只累计一次，两条不同的各算各的。
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
    return _inc_current(user, dedupe_key, inc)


def move_session(
    user: str,
    dedupe_key: str,
    seconds: int,
    from_project: str | None,
    from_task: str | None,
    to_project: str | None,
    to_task: str | None,
) -> bool:
    """v2.11 改挂：一段的秒数从旧归属挪到新归属，``totalSeconds`` 不变。``dedupe_key`` 是那条
    ``session.reassigned`` 的防重键，记在 ``movedKeys``（不读出）——``appliedKeys`` 仍然只是「算进来的段」，
    所以重建（不重放改挂）出来的文档与实时投影的读出来一样。
    纯 ``$inc``、带 upsert：与那一段自己的累计、与别的改挂谁先谁后到达，结果都一样。"""
    inc: dict[str, int] = {}
    for field, delta in (
        (f"projects.{from_project}", -seconds if from_project else 0),
        (f"tasks.{from_task}", -seconds if from_task else 0),
        (f"projects.{to_project}", seconds if to_project else 0),
        (f"tasks.{to_task}", seconds if to_task else 0),
    ):
        inc[field] = inc.get(field, 0) + delta  # 同项目内改挂：项目那一项一减一加，抵成 0
    inc = {field: delta for field, delta in inc.items() if delta}
    return bool(inc) and _inc_current(user, dedupe_key, inc, applied="movedKeys")


def _inc_current(user: str, dedupe_key: str, inc: dict[str, int], applied: str = "appliedKeys") -> bool:
    query = {"user": user, applied: {"$ne": dedupe_key}}
    update = {"$inc": inc, "$addToSet": {applied: dedupe_key}}
    try:
        result = _col().update_one(query, {**update, "$setOnInsert": {"user": user}}, upsert=True)
    except DuplicateKeyError:
        # 两种可能：① 本键早已应用过；② 这个租户还没有文档，并发的**另一条**写（另一段落账、或这一段的改挂）
        # 抢先建了出来。②不能当成「已应用」，否则这一笔就永远丢了。文档此刻一定在，不带 upsert 再试一次：
        # 条件中 = 本键没应用过（同 apply_daily_stat）。
        result = _col().update_one(query, update)
        return result.modified_count > 0
    return result.modified_count > 0 or result.upserted_id is not None


def read_current(user: str) -> dict | None:
    doc = _col().find_one({"user": user}, {"_id": 0, "movedKeys": 0})
    for field in ("projects", "tasks"):
        if doc and field in doc:
            # v2.11：改挂把旧归属减到 0，那个键不再读出（从没记过和全挪走了，读起来一样）
            # 只摘「是数、且 ≤ 0」的：外部事件的 id 带点时 $inc 写出的是嵌套文档，原样留着，不拿它比大小
            doc[field] = {key: secs for key, secs in doc[field].items()
                          if not (isinstance(secs, (int, float)) and secs <= 0)}
    return doc


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
    applied: str = "appliedKeys",
) -> bool:
    """``(user, date, projectId, taskId)`` 唯一；同一 ``dedupe_key`` 只累计一次。

    ``task_id`` 可以是 ``None``（外部事件可以没有具体任务，B5）——``None`` 作为
    Mongo 字段值参与唯一索引没有问题，同一 ``(user,date,projectId)`` 下所有
    「无任务」的事件会落进同一份文档，语义上等价于「这个项目当天的无任务时长」。

    v2.11：改挂用负的 ``seconds`` 从旧归属那行减（``dedupe_key`` 是那条 ``session.reassigned`` 的，
    ``applied="movedKeys"``——``appliedKeys`` 里是外部 source 自己定的 dedupeKey，同住一个数组的话，
    一条 dedupeKey 恰好叫 ``reassign:…`` 的外部事件就能把某次改挂的加减吞掉）。
    纯 ``$inc``、带 upsert，所以与那一段自己的累计、与别的改挂的到达顺序无关；减到 0 的行读端不出。
    """
    key = {"user": user, "date": date, "projectId": project_id, "taskId": task_id}
    query = {**key, applied: {"$ne": dedupe_key}}
    update = {"$inc": {"seconds": seconds}, "$addToSet": {applied: dedupe_key}}
    try:
        result = _daily_col().update_one(query, {**update, "$setOnInsert": key}, upsert=True)
    except DuplicateKeyError:
        # 两种可能：① 本键早已应用过；② 并发的**另一条**事件抢先建出了同一行（v2.11 起会有：两段同时
        # 归到同一任务同一天）。②不能当成「已应用」，否则这一次的秒数就漏了。行此刻一定在，不带 upsert
        # 再试一次：条件中 = 本键没应用过（同 apply_agent_daily_stat）。
        result = _daily_col().update_one(query, update)
        return result.modified_count > 0
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
    query: dict = {"user": user, "seconds": {"$gt": 0}}  # v2.11：改挂减到 0 的行不读出
    date_range: dict = {}
    if date_from:
        date_range["$gte"] = date_from
    if date_to:
        date_range["$lte"] = date_to
    if date_range:
        query["date"] = date_range
    return list(
        _daily_col().find(query, {"_id": 0, "user": 0, "appliedKeys": 0, "movedKeys": 0}).sort("date", 1)
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
    """同一 (user, key) 只写一次；重放 / 重投递是 no-op。

    那一行可能已经被 ``reassign_lane`` 占了位（只有去向、没有 ``kind``：改挂赶在这一行之前到了）——这时补上其余字段，
    归属留占位行记下的那个。一条管道更新原子地分三种情况：没有 → 整份写入；只有占位 → 合并；已经写过 → 不动。
    ``$literal``：文档里以 ``$`` 开头的字符串（外部事件的标签、任务 id）是数据，不是字段引用。
    """
    # 占位只对人的段作数（改挂只针对 session.completed）；别的 kind 以自己的文档为准
    merged = [{"$literal": doc}, "$$ROOT"] if doc.get("kind") == "session" else ["$$ROOT", {"$literal": doc}]
    query = {"user": doc["user"], "key": doc["key"]}
    update = [{"$replaceWith": {"$cond": [
        {"$eq": [{"$type": "$kind"}, "missing"]}, {"$mergeObjects": merged}, "$$ROOT"]}}]
    try:
        result = _lanes_col().update_one(query, update, upsert=True)
    except DuplicateKeyError:
        # 并发的同一条、或一条改挂的占位抢先建出了这一行：行此刻一定在，不带 upsert 再走一遍同样的三分支
        result = _lanes_col().update_one(query, update)
    return result.modified_count > 0 or result.upserted_id is not None


def reassign_lane(user: str, key: str, task_id: str | None, project_id: str | None, seq: int) -> bool:
    """v2.11 改挂：换这一段的归属。按 ``seq`` 只进不退——并发的两条改挂后到的若更旧，不盖新的。

    段的那一行还没写进来（刚落账就改挂，``lanes.handle`` 还没轮到）时先占位：只记去向与 ``assignSeq``、
    没有 ``kind``，所以读端与 ``lanes_empty`` 都看不见它；``apply_lane`` 随后补上其余字段、归属以占位为准。
    那一段永远不进时间线（没有 ``startAt`` 的外部段）时占位行就一直留着，重建时清掉。
    """
    query = {"user": user, "key": key, "kind": {"$in": ["session", None]}, "assignSeq": {"$not": {"$gte": seq}}}
    update = {"$set": {"taskId": task_id, "projectId": project_id, "assignSeq": seq}}
    try:
        result = _lanes_col().update_one(query, update, upsert=True)
    except DuplicateKeyError:
        # 行在但条件不中（已是更新的 seq / 不是人的段），或并发的另一条写刚建出这一行：不带 upsert 再试一次
        result = _lanes_col().update_one(query, update)
    return result.modified_count > 0 or result.upserted_id is not None


def read_lanes(user: str, kind: str, start, end, limit: int) -> list[dict]:
    """与 [start, end) 有重叠的区间，最新（startAt 大）的在前，至多 ``limit`` 条。"""
    query = {"user": user, "kind": kind, "startAt": {"$lt": end}, "endAt": {"$gt": start}}
    return list(_lanes_col().find(query, {"_id": 0}).sort("startAt", -1).limit(limit))


def clear_lanes() -> None:
    """重建专用，同 ``clear_daily_stats``。"""
    _lanes_col().delete_many({})


def lanes_empty() -> bool:
    """一条区间都没有（改挂留下的占位行没有 ``kind``，不算）。"""
    return _lanes_col().find_one({"kind": {"$exists": True}}, {"_id": 1}) is None


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
