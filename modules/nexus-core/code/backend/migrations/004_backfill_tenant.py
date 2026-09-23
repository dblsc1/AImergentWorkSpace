"""给没有 ``user`` 字段的老 planner 文档补 ``u_local``（契约 v2.0「按租户分数据」）。

**纪律 1 已在读取侧兜底**：``app/tenant.py::scope()`` 让 ``u_local`` 同时认没有
``user`` 字段的老文档，不跑本迁移单人部署也照常工作。补上真实字段是为了直接查库
与将来的读取点不必人人记得那条 ``$in``（同 003 的理由）。

**为什么一律是 ``u_local``**：这批文档都产生于租户存在之前，那时全库只有一个用户——
是已知事实，不是猜。events / timer_state / 两张投影本来就带 ``user``，不在此列。
"""

DESCRIPTION = "给缺 user 的老 zones/projects/tasks/name_registry/planner_audit 文档补 u_local"


def up(db):
    total = 0
    for collection in ("zones", "projects", "tasks", "name_registry", "planner_audit"):
        n = db[collection].update_many(
            {"user": {"$exists": False}}, {"$set": {"user": "u_local"}}
        ).modified_count
        if n:
            print(f"    {collection}: 回填 user 缺失 {n} 条")
        total += n
    return total
