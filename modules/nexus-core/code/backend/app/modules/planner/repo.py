"""zones / projects / tasks / name_registry 集合的存取。
**本文件是 planner 子边界唯一碰 mongo 的地方。**

HTTP 面仍只读（contract.md「planner 只读面」）；``insert_task`` 与登记表
只服务于 ``service.create_task``（J10：建任务时 id/key 由系统生成）与种子脚本。

索引口径（J10 标识三分，验收 R7）：
- **唯一约束压在 ``id`` 上**（``uniq_id``）。
- **``key`` 不建任何索引**——搬移时会重算，过程中可能短暂重复；
  key 算错不致命，id 撞了才是事故。

「名字 → 号」登记表（``name_registry``）：见到新名字发新号（``counters`` 自增），
同名复用同号；中英文同一张表同一套规则，**绝不做拼音音译**（音译塌陷：
塔/她 同为 ta；且方案不唯一，换库标识全变）。
"""

from __future__ import annotations

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from ...repo import get_db


def _find_one(collection: str, doc_id: str) -> dict | None:
    return get_db()[collection].find_one({"id": doc_id}, {"_id": 0})


def get_zone(zone_id: str) -> dict | None:
    return _find_one("zones", zone_id)


def get_project(project_id: str) -> dict | None:
    return _find_one("projects", project_id)


def get_task(task_id: str) -> dict | None:
    return _find_one("tasks", task_id)


def list_zones() -> list[dict]:
    return list(get_db()["zones"].find({}, {"_id": 0}).sort("order", 1))


def list_projects(zone_id: str | None = None) -> list[dict]:
    query = {"zoneId": zone_id} if zone_id else {}
    return list(get_db()["projects"].find(query, {"_id": 0}))


def list_tasks(project_id: str | None = None) -> list[dict]:
    query = {"projectId": project_id} if project_id else {}
    return list(get_db()["tasks"].find(query, {"_id": 0}))


def list_tasks_by_project(project_id: str) -> list[dict]:
    return list_tasks(project_id)


def seed_many(collection: str, docs: list[dict]) -> int:
    """按 ``id`` 幂等 upsert，文档原样落库。只供种子脚本与快照恢复
    （``snapshot.py``，v1.9）——后者是 HTTP 面唯一经过它的路径。"""
    col = get_db()[collection]
    col.create_index([("id", 1)], unique=True, name="uniq_id")
    for doc in docs:
        col.replace_one({"id": doc["id"]}, doc, upsert=True)
    return len(docs)


# ── 「名字 → 号」登记表（J10 key 生成的事实源） ──────────────────


def name_num(name: str) -> int:
    """名字的号：已登记复用，新名字原子发号。

    并发下两个进程同时登记同一个新名字：唯一索引让后到者撞
    DuplicateKeyError，回头读已登记的号——号**永不重发、永不改**。
    """
    reg = get_db()["name_registry"]
    reg.create_index([("name", 1)], unique=True, name="uniq_name")
    found = reg.find_one({"name": name}, {"_id": 0})
    if found:
        return found["num"]
    counter = get_db()["counters"].find_one_and_update(
        {"_id": "name_registry"},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    num = int(counter["seq"])
    try:
        reg.insert_one({"name": name, "num": num})
    except DuplicateKeyError:  # 并发登记同名：用先到者的号（本号作废不回收，无妨）
        return reg.find_one({"name": name}, {"_id": 0})["num"]
    return num


def restore_name_registry(pairs: dict[str, int], top: int) -> None:
    """快照恢复专用：补登记从 key 反推的「名字 → 号」，发号计数器抬到不低于 ``top``。

    已登记的名字不动（``$setOnInsert``，号永不改）；计数器只升不降（``$max``），
    之后新名字从 ``top + 1`` 发起——号永不重发。
    """
    reg = get_db()["name_registry"]
    reg.create_index([("name", 1)], unique=True, name="uniq_name")
    for name, num in pairs.items():
        reg.update_one({"name": name}, {"$setOnInsert": {"num": num}}, upsert=True)
    get_db()["counters"].update_one(
        {"_id": "name_registry"}, {"$max": {"seq": top}}, upsert=True,
    )


def count_same_name_in_project(project_id: str, name: str, *, exclude_id: str | None = None) -> int:
    """同一项目下同名任务的现存数量（同名序号 = 现存数 + 1）。

    ``exclude_id``：搬移/改名重算 key 时把自己排除在外，否则自己算自己一次。
    """
    query: dict = {"projectId": project_id, "name": name}
    if exclude_id:
        query["id"] = {"$ne": exclude_id}
    return get_db()["tasks"].count_documents(query)


def insert_one(collection: str, doc: dict) -> None:
    """插入新对象。唯一索引只在 ``id``；**不给 ``key`` 建索引**（R7）。"""
    col = get_db()[collection]
    col.create_index([("id", 1)], unique=True, name="uniq_id")
    col.insert_one(dict(doc))


def insert_task(doc: dict) -> None:
    insert_one("tasks", doc)


def update_by_id(collection: str, doc_id: str, fields: dict) -> dict | None:
    """按 id 改字段，返回改后的文档（无则 None）。``id`` 永远不在 fields 里——
    调用方要是想改 id，这层直接炸掉比默默改掉好。"""
    assert "id" not in fields, "id 不可变（J10）——这是实现级断言，不是校验"
    return get_db()[collection].find_one_and_update(
        {"id": doc_id},
        {"$set": dict(fields)},
        projection={"_id": 0},
        return_document=ReturnDocument.AFTER,
    )


def delete_by_id(collection: str, doc_id: str) -> bool:
    """按 id 删除。返回是否真的删了（False=本来就不存在）。"""
    return get_db()[collection].delete_one({"id": doc_id}).deleted_count == 1


def count_children(collection: str, parent_field: str, parent_id: str) -> int:
    """子对象计数（删除拒级联的 409 依据）。"""
    return get_db()[collection].count_documents({parent_field: parent_id})


# ── 审计流水 planner_audit（契约 v1.6，F-ACTOR-2） ─────────────────
#
# **append-only 是这里的实现级保证，不只是文档承诺**：本段只有
# `insert_one` / `find` / `count_documents` / `create_index` 四种调用，
# 没有 update/replace/delete/find_one_and_* 的任何变体，将来也不许长出来
# ——`tests/test_planner_audit.py` 有一条 AST 断言盯着本段（修完/定完规矩必须
# 留下能重现拦住违规的断言，不能只写在注释里）。


#: 审计集合名。**与 `events` 是两个东西**：事件是跨模块开放标准（信封只增不改），
#: 审计是本模块的运维追溯（字段随需求长）。混在一起会同时毁掉两边。
AUDIT_COLLECTION = "planner_audit"


def next_audit_seq() -> int:
    """审计流水的全序号（原子自增，同 `name_registry` 的发号机制）。

    **为什么不用时间戳排序**：AI 批量写可以在同一毫秒里发十几条，
    "做到第几步"要的是全序，不是近似。
    """
    counter = get_db()["counters"].find_one_and_update(
        {"_id": AUDIT_COLLECTION},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return int(counter["seq"])


def append_audit(doc: dict) -> None:
    """追加一条审计记录。**唯一的审计写路径**，只有 insert。"""
    col = get_db()[AUDIT_COLLECTION]
    col.create_index([("seq", -1)], unique=True, name="uniq_seq")
    col.insert_one(dict(doc))


def count_audit(query: dict) -> int:
    return get_db()[AUDIT_COLLECTION].count_documents(query)


def list_audit(query: dict, limit: int) -> list[dict]:
    """按 `seq` 降序取最近 `limit` 条（最新在前，契约 v1.6 读端）。"""
    cursor = get_db()[AUDIT_COLLECTION].find(query, {"_id": 0}).sort("seq", -1).limit(limit)
    return list(cursor)


def find_dependents(task_id: str) -> list[dict]:
    """返回 ``dependsOn`` 数组里含 ``task_id`` 的全部任务（契约 v1.1 删除拒绝的依据）。

    Mongo 对数组字段用标量值查询天然是「数组包含该值」语义，不需要 ``$elemMatch``。
    不建索引（同 ``key`` 一样的口径：这条查询频率低、数据量在个人任务管理场景下
    很小，线性扫描足够；见 contract.md 「排期与依赖」节的取舍说明）。
    """
    return list(get_db()["tasks"].find({"dependsOn": task_id}, {"_id": 0}))
