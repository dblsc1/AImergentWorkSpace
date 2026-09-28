"""``agent.run.completed`` → ``proj_agent_daily_stats``（契约 v2.1「AI 代理运行」）。

**这是 AI 代理时长唯一进得去的投影。** ``session.completed`` 的两个 handler 不认
``agent.run.completed``，本 handler 也不认 ``session.completed``——DISPATCH 表上两条
泳道各走各的，人的圆环/甘特/回顾结构上看不见代理时长。

铁则同 ``daily_stats.py``：幂等（``appliedKeys``）、只写自己的集合、不发事件；
归日同样取 ``data.startAt`` 经 ``NEXUS_TZ``（理由见 ``daily_stats.py`` 模块说明）。
坏载荷静默跳过不炸投影——事件本身已落库，可事后排查。
"""

from __future__ import annotations

from datetime import datetime

from ....config import settings
from ....timeutil import local_date
from .. import repo


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
        or seconds <= 0
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
        dedupe_key=envelope["dedupeKey"],
        date=local_date(moment, settings.tz),
        project_id=project_id,
        task_id=subject.get("task"),
        agent=agent,
        seconds=int(seconds),
    )
