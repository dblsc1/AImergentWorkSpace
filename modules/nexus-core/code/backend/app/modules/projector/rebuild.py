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

**不改 DISPATCH 表**（任务单明文不做）。``--only`` 需要按投影名单独重放，
所以本文件自带一张「投影名 → (handler, clear 函数)」的小映射；它只是
DISPATCH 表的一个**只读**投影（用 handler 是否在 ``DISPATCH[type]`` 里
来判断某条事件要不要喂给某个 handler），不新增、不修改联动真相本身。
"""

from __future__ import annotations

import argparse
from collections.abc import Callable

from ..events import service as events_service
from . import repo as projector_repo
from .handlers import current, daily_stats
from .registry import DISPATCH

#: 投影名 → (handler, 对应 clear 函数)。投影名取自集合名，与 contract.md 一致。
_TARGETS: dict[str, tuple[Callable[[dict], None], Callable[[], None]]] = {
    "proj_current": (current.handle, projector_repo.clear_current),
    "proj_daily_stats": (daily_stats.handle, projector_repo.clear_daily_stats),
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
    for envelope in events_service.iter_all_events():
        routed = DISPATCH.get(envelope.get("type", ""), ())  # 只读 DISPATCH，不改它
        for name in names:
            handler, _ = _TARGETS[name]
            if handler in routed:
                handler(envelope)
                counts[name] += 1
    return counts


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
