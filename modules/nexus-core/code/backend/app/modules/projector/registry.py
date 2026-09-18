"""DISPATCH 表 —— **全系统唯一联动真相**。

「事件 type → 哪些 handler → 动谁的投影」只在这张表上可见。
**引入本表以及此后任何修改，都单独成一个 commit**，commit 消息说明影响面
（rules.md §7.8）——与业务代码混在一个 commit 里 = 违规，reviewer 直接打回。

handler 约束：幂等、只写自己的投影集合、禁止发新事件。
未知 ``type`` 在这里静默落空（B8/Postel）：事件照常在库里，投影不动。
"""

from __future__ import annotations

from collections.abc import Callable

from .handlers import current, daily_stats

#: type → handler 元组。当前联动面：
#:   session.completed → handlers/current.handle      → 只动 proj_current（贡献圆环）
#:                      → handlers/daily_stats.handle  → 只动 proj_daily_stats（甘特「事实」图层）
DISPATCH: dict[str, tuple[Callable[[dict], None], ...]] = {
    "session.completed": (current.handle, daily_stats.handle),
}


def dispatch(envelope: dict) -> None:
    """按 ``type`` 路由到 handler。**唯一的派发入口**——别处不许自建路由。"""
    for handler in DISPATCH.get(envelope.get("type", ""), ()):
        handler(envelope)
