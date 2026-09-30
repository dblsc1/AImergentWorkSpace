"""投影重建（契约「投影重建」v0.9）：**投影是事实的派生物，必须能从事实完全重建。**

```
.venv/bin/python -m app.modules.projector.rebuild [--only <投影名>]
```

三条硬约束（逐条对应契约）：

- **只读事实、只写投影。** 重建绝不碰 ``events`` 集合——事实是唯一真相，重建是
  从它推导，不是反过来。事实读取走 ``events/service.py::iter_all_events``
  （跨子边界只准调对方 service 的公开函数，rules.md §7.3 红线 2）。
- **先清目标投影再重放**，否则会在已有计数上重复累加。
- **幂等**：连跑两次结果必须相同——本实现「先清后放」天然保证这一点，
  因为每次都是从同一份不变的事实集合重新算起，不依赖上一次跑到哪。

**不改 DISPATCH 表**（规格明文不做）。``--only`` 需要按投影名单独重放，
所以本文件自带一张「投影名 → (handler, clear 函数)」的小映射；它只是
DISPATCH 表的一个**只读**投影（用 handler 是否在 ``DISPATCH[type]`` 里
来判断某条事件要不要喂给某个 handler），不新增、不修改联动真相本身。
"""

from __future__ import annotations

import argparse
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from ..events import service as events_service
from . import repo as projector_repo
from .handlers import agent_daily_stats, current, daily_stats, lanes
from .registry import DISPATCH

#: 投影名 → (handler, 对应 clear 函数)。投影名取自集合名，与 contract.md 一致。
_TARGETS: dict[str, tuple[Callable[[dict], None], Callable[[], None]]] = {
    "proj_current": (current.handle, projector_repo.clear_current),
    "proj_daily_stats": (daily_stats.handle, projector_repo.clear_daily_stats),
    # v2.1：AI 代理时长。快照恢复末尾调的也是 rebuild()，所以恢复同样重建它。
    "proj_agent_daily_stats": (agent_daily_stats.handle, projector_repo.clear_agent_daily_stats),
    # v2.4：时间线区间。上线本版须跑一次重建，否则历史时间线是空的。
    "proj_lanes": (lanes.handle, projector_repo.clear_lanes),
}


def rebuild(only: str | None = None) -> dict[str, int]:
    """清空目标投影后，从全部历史事实重放。返回 ``{投影名: 重放的事件数}``——
    调用方（CLI 或测试）拿这个数核对「是不是真的把历史事实都喂过了」。

    ``only`` 缺省时重建 ``_TARGETS`` 里的全部投影；给了非法投影名直接
    ``ValueError``（关键路径缺参数/参数错必须响亮失败，不是静默忽略）。
    """
    if only is not None and only not in _TARGETS:
        raise ValueError(f"未知投影名 {only!r}，可选：{sorted(_TARGETS)}")
    names = [only] if only else list(_TARGETS)

    for name in names:
        _, clear = _TARGETS[name]
        clear()  # 先清目标投影再重放（契约硬约束），不在已有计数上累加

    counts = dict.fromkeys(names, 0)
    for envelope in events_service.iter_all_events(all_tenants=True):  # 全体租户，见该函数
        routed = DISPATCH.get(envelope.get("type", ""), ())  # 只读 DISPATCH，不改它
        for name in names:
            handler, _ = _TARGETS[name]
            if handler in routed:
                handler(envelope)
                counts[name] += 1
    return counts


#: 喂给 proj_lanes 的事实类型（只读 DISPATCH 推出来，不另写一份）
_LANES_TYPES = frozenset(t for t, handlers in DISPATCH.items() if lanes.handle in handlers)
_LOCK_STALE = timedelta(minutes=10)


def backfill_lanes_if_empty() -> int:
    """启动时调（契约 v2.4「上线」补注）：``proj_lanes`` 为空而台账里有它要的事实 → 从全体租户的事实补建。

    升级上来的发布版用户不会手跑 ``rebuild --only proj_lanes``，不补的话历史时间线是空的。
    **不清空、只重放**：handler 按 (user, key) 幂等，所以与同时进来的新事实、与另一个实例的补建
    都不会重复或丢失。触发条件是「集合为空」或「上次补建没做完」（进度标记还在）——拿到锁之后
    不再看集合空不空：那时新进来的事实会让它非空，但历史还没补。返回重放的事实数，no-op 为 0。
    ponytail: 从没触发过、只是「非空但缺了几条」不在这里修，那是手动 rebuild 的事。
    """
    if not projector_repo.lanes_empty() and not projector_repo.is_pending("proj_lanes"):
        return 0
    owner = uuid.uuid4().hex
    if not projector_repo.acquire_startup_lock("proj_lanes", owner, datetime.now(timezone.utc), _LOCK_STALE):
        return 0
    try:
        projector_repo.set_pending("proj_lanes", True)
        count = 0
        for envelope in events_service.iter_all_events(all_tenants=True):
            if envelope.get("type") in _LANES_TYPES:
                lanes.handle(envelope)
                count += 1
        projector_repo.set_pending("proj_lanes", False)  # 只在整遍重放成功后清掉
        return count
    finally:
        projector_repo.release_startup_lock("proj_lanes", owner)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.modules.projector.rebuild",
        description="从 events 集合完全重放，重建投影（契约「投影重建」v0.9）。",
    )
    parser.add_argument(
        "--only", choices=sorted(_TARGETS), default=None,
        help="只重建指定的一张投影；不给则重建全部",
    )
    args = parser.parse_args(argv)

    counts = rebuild(only=args.only)
    for name, n in counts.items():
        print(f"{name}: 重放 {n} 条事件")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
