"""给缺 ``dependsOn``/``plan`` 的老任务文档回填（契约 v1.1「排期与依赖」）。

**两条纪律缺一不可**（`migrations/migrate.py` 头部注释 / `handoff.md`「迁移与备份」）：

1. 读老数据一律 ``.get(字段, 默认值)``——本轮已在读取侧做到：
   `TaskOut.dependsOn` 有 `Field(default_factory=list)`，`plan` 默认 `None`，
   `views/queries.py::get_gantt` 也用 `task.get("plan")`/`task.get("dependsOn") or []`。
   缺字段的老文档经这些默认值读出来就是「未排期、无依赖」，**不会炸**。
2. 但纪律 2（回填）不能因为 1 已经兜底就省——补上真实字段能让**直接查库**
   （运维排查、`repo.find_dependents` 的 `{"dependsOn": task_id}` 查询、
   将来任何新读取点）不必人人记得 `.get()`；否则默认值散落各处，
   迟早有一处漏写（`migrate.py` 头部原话）。
"""

DESCRIPTION = "给缺 dependsOn/plan 的老任务文档补默认值（[]/null）"


def up(db):
    tasks = db["tasks"]
    n_deps = tasks.update_many(
        {"dependsOn": {"$exists": False}}, {"$set": {"dependsOn": []}}
    ).modified_count
    n_plan = tasks.update_many(
        {"plan": {"$exists": False}}, {"$set": {"plan": None}}
    ).modified_count
    if n_deps:
        print(f"    tasks: 回填 dependsOn 缺失 {n_deps} 条")
    if n_plan:
        print(f"    tasks: 回填 plan 缺失 {n_plan} 条")
