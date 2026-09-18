"""首个迁移：给缺 key 的老文档回填。

**这不是假想的**：2026-08-01 `views/queries.py` 硬取 `project["key"]`
对切片1时期的旧数据直接炸 500——那批数据早于 J10 的 key 生成逻辑。
当时的处置是清库重种（数据没价值），但同样的形状在真实使用后就不能那么处理了。

本迁移把「缺 key 的文档补一个可辨认的占位」，让读取不炸。
**占位不是真 key**——真 key 要按登记表重算，那需要知道父级关系，
不是这个迁移的职责。这里只保证「不炸」，并让缺失可见（前缀 `MISSING-`）。
"""

DESCRIPTION = "给缺 key 的 zones/projects/tasks 补占位，避免硬取时 KeyError"


def up(db):
    for col in ("zones", "projects", "tasks"):
        n = 0
        for doc in db[col].find({"key": {"$exists": False}}, {"_id": 1, "id": 1}):
            db[col].update_one(
                {"_id": doc["_id"]},
                {"$set": {"key": f"MISSING-{doc.get('id', 'unknown')}"}},
            )
            n += 1
        if n:
            print(f"    {col}: 回填 {n} 条")
