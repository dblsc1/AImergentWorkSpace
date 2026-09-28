"""``agent.run.completed`` → ``proj_agent_daily_stats``（契约 v2.1「AI 代理运行」）。

**这是 AI 代理时长唯一进得去的投影。** ``session.completed`` 的两个 handler 不认
``agent.run.completed``，本 handler 也不认 ``session.completed``——DISPATCH 表上两条
泳道各走各的，人的圆环/甘特/回顾结构上看不见代理时长。

铁则同 ``daily_stats.py``：幂等（``appliedKeys``）、只写自己的集合、不发事件；
归日同样取 ``data.startAt`` 经 ``NEXUS_TZ``（理由见 ``daily_stats.py`` 模块说明）。
坏载荷静默跳过不炸投影——事件本身已落库，可事后排查。
"""

from __future__ import annotations

import json
from datetime import datetime

from ....config import settings
from ....timeutil import local_date
from .. import repo

#: 单次运行时长的健全上限（31 天）。不是业务规则（业务上限是 NEXUS_AGENT_RUN_TIMEOUT_HOURS，
#: 由 agents.py 在写事件时封顶），只拦外部 source 的坏载荷：单位填错、1e308 之类。
_MAX_SECONDS = 31 * 86400


def applied_key(envelope: dict) -> str:
    """投影侧的「已应用」身份 = 事件入口的防重身份 ``(source, dedupeKey)``（user 已在行键里）。

    只用 ``dedupeKey`` 会把两条合法的不同事件（不同 source、碰巧同一个 dedupeKey、落进
    同一行）当成一条，少计。用 JSON 数组拼接：任何 source/dedupeKey 取值都不会拼出歧义。
    ⚠️ 人的 ``proj_daily_stats``/``proj_current`` 仍只记 ``dedupeKey``（既有行为，本版不动）。
    """
    return json.dumps([envelope["source"], envelope["dedupeKey"]], ensure_ascii=False)


def handle(envelope: dict) -> None:
    subject = envelope.get("subject") or {}
    project_id = subject.get("project")
    data = envelope.get("data") or {}
    seconds = data.get("durationSeconds")
    agent = data.get("agent")
    start_at = data.get("startAt")
    if (
        not project_id
        or not isinstance(agent, str)
        or not isinstance(start_at, str)
        or not isinstance(seconds, (int, float))
        or isinstance(seconds, bool)
        # 纯比较、不转 float：NaN 两边都不成立、±Inf 与超大 int（10**400）都超上限——
        # math.isfinite 会对超大 int 抛 OverflowError，反而重新引入崩溃
        or not 0 < seconds <= _MAX_SECONDS
    ):
        return
    try:
        moment = datetime.fromisoformat(start_at)
    except ValueError:
        return
    if moment.tzinfo is None:
        return

    repo.apply_agent_daily_stat(
        user=envelope["user"],
        dedupe_key=applied_key(envelope),
        date=local_date(moment, settings.tz),
        project_id=project_id,
        task_id=subject.get("task"),
        agent=agent,
        seconds=int(seconds),
    )


def read_agent_daily_stats(
    user: str, date_from: str | None = None, date_to: str | None = None
) -> list[dict]:
    """views 的指定读路径（v2.3 ``views/agent-time``），同 ``daily_stats.read_daily_stats``。"""
    return repo.read_agent_daily_stats(user, date_from=date_from, date_to=date_to)
