"""快照恢复的 planner 一半（契约 v1.9「快照恢复」）。

拆成独立文件的理由同 `deps.py`/`inbox.py`——`service.py` 贴着 300 行预算。
**不是新的子边界**：经 `service.py` 转发，`restore/` 只认 `service.restore_snapshot`。

**写对象不走 `create_*`**：那条路会重新发 id、重算 key，恰好是恢复最不能做
的两件事。**设防与留痕照走 `guard.run_write`**：整次恢复算一次高风险写（它一次
能写满全库），AI 来源 403、严格模式下未携人路径凭据 403，applied/denied/failed
都进审计——没有为这个端点另开一套更宽松的口子。
"""

from __future__ import annotations

from typing import Any

from . import audit, guard, repo

TYPES: tuple[str, ...] = ("zones", "projects", "tasks")


def registry_from_keys(snapshot: dict[str, list[dict]]) -> tuple[dict[str, int], int]:
    """从快照里的 `key` 反推「名字 → 号」登记表，外加出现过的最大号。

    export 不带 `name_registry`/`counters`（发号器状态，不是用户数据），不补的
    后果是新实例从 1 重新发号，新建对象与恢复进来的对象撞 key。能反推是因为
    J10 的 key 格式：分区 `Z`、项目 `Z-P`、任务 `Z-P-T-序号`，且**改名会重算
    自己那一段**——所以每个对象自己那一段与它当前的 `name` 恒配对。前缀段指向
    的可能是旧名字（出生地原则），只计入最大号，不登记。
    """
    pairs: dict[str, int] = {}
    top = 0
    for own, type_ in enumerate(TYPES):
        for doc in snapshot[type_]:
            segs = str(doc.get("key") or "").split("-")[: own + 1]
            name = doc.get("name")
            if len(segs) != own + 1 or not all(s.isdigit() for s in segs):
                continue  # 手改或老数据的 key：登记表是派生物，缺一条只是那个名字下次发新号
            top = max(top, *(int(s) for s in segs))
            if isinstance(name, str):
                pairs.setdefault(name, int(segs[own]))
    return pairs, top


def restore_snapshot(request: Any, snapshot: dict[str, list[dict]]) -> dict[str, int]:
    """按 id 原样写入 zones→projects→tasks，并补登记表。调用方已校验过引用闭包
    与「库为空」；本函数只负责设防、写、留痕。"""
    counts = {type_: len(snapshot[type_]) for type_ in TYPES}

    def _write(_actor: str | None) -> dict[str, int]:
        for type_ in TYPES:
            repo.seed_many(type_, snapshot[type_])
        repo.restore_name_registry(*registry_from_keys(snapshot))
        return counts

    return guard.run_write(
        request, op=audit.OP_RESTORE, object_type="snapshot", changes=counts, action=_write,
    )
