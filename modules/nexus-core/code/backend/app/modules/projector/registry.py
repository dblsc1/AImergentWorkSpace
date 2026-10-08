"""DISPATCH 表 —— **全系统唯一联动真相**。

「事件 type → 哪些 handler → 动谁的投影」只在这张表上可见。
**引入本表以及此后任何修改，都单独成一个 commit**，commit 消息说明影响面
（rules.md §7.8）——与业务代码混在一个 commit 里 = 违规，reviewer 直接打回。

handler 约束：幂等、只写自己的投影集合、禁止发新事件。
未知 ``type`` 在这里静默落空（B8/Postel）：事件照常在库里，投影不动。
"""

from __future__ import annotations

from collections.abc import Callable

from .handlers import agent_daily_stats, current, daily_stats, lanes

#: type → handler 元组。当前联动面：
#:   session.completed → handlers/current.handle      → 只动 proj_current（贡献圆环）
#:                      → handlers/daily_stats.handle  → 只动 proj_daily_stats（甘特「事实」图层）
#:   agent.run.completed → handlers/agent_daily_stats.handle → 只动 proj_agent_daily_stats（v2.1）
#:     AI 代理时长只走这一行：人的两个 handler 不在这里 = 人的读端结构上看不见它。
#:   两种事实各追加 → handlers/lanes.handle → 只动 proj_lanes（v2.4 时间线区间，不求和）。
#:     既有 handler 一行不改；lanes 不进任何人的汇总，人的投影照旧看不见代理。
#:   session.reassigned（v2.11 改挂未分类时间）→ 三个人的投影各自的 handle_reassign：
#:     current / daily_stats 把那一段的秒数从旧归属减、往新归属加；lanes 换那一段的 taskId / projectId。
#:     代理的投影不动。**重建不走这一行**：rebuild 按当前归属重放 session.completed（见 rebuild.py）。
DISPATCH: dict[str, tuple[Callable[[dict], None], ...]] = {
    "session.completed": (current.handle, daily_stats.handle, lanes.handle),
    "agent.run.completed": (agent_daily_stats.handle, lanes.handle),
    "session.reassigned": (current.handle_reassign, daily_stats.handle_reassign, lanes.handle_reassign),
}


def dispatch(envelope: dict) -> None:
    """按 ``type`` 路由到 handler。**唯一的派发入口**——别处不许自建路由。"""
    for handler in DISPATCH.get(envelope.get("type", ""), ()):
        handler(envelope)
