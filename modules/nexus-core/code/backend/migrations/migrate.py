"""数据迁移跑手 —— 幂等，已应用的不重跑。

    .venv/bin/python migrations/migrate.py [--dry-run]

**为什么需要它**：Mongo 无 schema，加字段时老文档静默缺字段。
2026-08-01 已实证：`views/queries.py` 硬取 `project["key"]` 对切片1时期的旧数据
直接炸 500——那批数据早于 key 生成逻辑。

**两条纪律，缺一不可**：

1. **读老数据一律 `.get(字段, 默认值)`**，不许硬取。
   这条管「已经在库里的旧文档」。
2. **加字段时写一个迁移回填**。这条管「让旧文档也长出新字段」。

只做 1 不做 2：默认值散落在各处读取点，迟早有一处漏写。
只做 2 不做 1：迁移跑之前那段时间照样炸，而且备份还原回来的老数据又是旧形状。

## 写一个迁移

`migrations/NNN_描述.py`，导出两个东西：

```python
DESCRIPTION = "给 projects 回填 priority 默认值"

def up(db):
    db["projects"].update_many({"priority": {"$exists": False}}, {"$set": {"priority": 0}})
```

**只增不改**：已经跑过的迁移文件不许再改内容——别人的库上已经按旧内容跑过了，
你改了它，两边就永久分叉。要修就写下一个迁移。

**必须幂等**：每个迁移都要能重复跑而不出错（用 `$exists: False` 之类的条件）。
跑手虽然记录了已应用集合，但记录本身可能因为还原备份而回退。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

APPLIED = "_migrations"


def _load(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    dry = "--dry-run" in sys.argv
    from app.repo import get_db  # 晚导入：要等 env 就位

    db = get_db()
    done = {d["name"] for d in db[APPLIED].find({}, {"_id": 0, "name": 1})}

    files = sorted(p for p in HERE.glob("[0-9][0-9][0-9]_*.py"))
    if not files:
        print("没有迁移文件。")
        return 0

    pending = [p for p in files if p.stem not in done]
    print(f"共 {len(files)} 个迁移，已应用 {len(files) - len(pending)}，待跑 {len(pending)}")
    if not pending:
        return 0

    for path in pending:
        mod = _load(path)
        desc = getattr(mod, "DESCRIPTION", "")
        if dry:
            print(f"  [dry-run] {path.stem}  {desc}")
            continue
        if not hasattr(mod, "up"):
            print(f"❌ {path.stem} 没有 up(db) 函数 —— 拒绝跳过，请修正后重跑", file=sys.stderr)
            return 1
        print(f"  跑 {path.stem}  {desc}")
        mod.up(db)
        # 记录**在迁移成功之后**：失败就不记，下次重跑（所以迁移必须幂等）
        db[APPLIED].update_one(
            {"name": path.stem},
            {"$set": {"name": path.stem, "description": desc}},
            upsert=True,
        )
    print("✅ 迁移完成" if not dry else "（dry-run，什么都没改）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
