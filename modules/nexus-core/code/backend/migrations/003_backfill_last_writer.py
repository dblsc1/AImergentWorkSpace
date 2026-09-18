"""给缺 ``lastWriter`` 的老 zones/projects/tasks 文档回填（契约 v1.5「写者
字段 actor/lastWriter」节，PRD F-ACTOR-1）。

**两条纪律缺一不可**（`migrations/migrate.py` 头部注释 / `handoff.md`）：

1. 读老数据一律 ``.get(字段, 默认值)``——本轮已在读取侧做到：三类对象的
   ``*Out`` 模型（`planner/schemas.py`）都给 ``lastWriter`` 设了默认值
   ``"human"``，缺字段的老文档经这个默认值读出来就是"人工写的"，不会炸。
2. 但纪律 2（回填）不能因为 1 已经兜底就省——补上真实字段能让**直接查库**
   （运维排查、将来任何新读取点）不必人人记得 `.get()`；否则默认值散落各处，
   迟早有一处漏写（同 `002_backfill_task_deps.py` 的理由）。

**为什么回填值一律是 ``"human"``，不是"猜"**：这批老文档全部产生于
``actor``/``lastWriter`` 字段存在之前——那时系统里只有人工写入这一条路径
（`ai-planner` 模块尚未创建），所以"人工写的"是**已知事实**，不是默认猜测。
"""

DESCRIPTION = "给缺 lastWriter 的老 zones/projects/tasks 文档补默认值 human"


def up(db):
    total = 0
    for collection in ("zones", "projects", "tasks"):
        n = db[collection].update_many(
            {"lastWriter": {"$exists": False}}, {"$set": {"lastWriter": "human"}}
        ).modified_count
        if n:
            print(f"    {collection}: 回填 lastWriter 缺失 {n} 条")
        total += n
    return total
