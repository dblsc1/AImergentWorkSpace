"""``agent.run.completed`` → ``proj_agent_daily_stats``（契约 v2.1「AI 代理运行」）。

**这是 AI 代理时长唯一进得去的投影。** ``session.completed`` 的两个 handler 不认
``agent.run.completed``，本 handler 也不认 ``session.completed``——DISPATCH 表上两条
泳道各走各的，人的圆环/甘特/回顾结构上看不见代理时长。

铁则同 ``daily_stats.py``：幂等（``appliedKeys``）、只写自己的集合、不发事件；
归日：事件带 ``data.days``（v2.31，写事件时已按 ``NEXUS_TZ`` 切好）就按它逐日写；
没有的老事件 / 外部 source 的事件仍取 ``data.startAt`` 经 ``NEXUS_TZ`` 整段归一天（理由见 ``daily_stats.py``）。
坏载荷静默跳过不炸投影——事件本身已落库，可事后排查。
"""

from __future__ import annotations

import json
import re
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


#: ``days`` 只信代理子系统自己写的事件（= ``timer.agents.SOURCE``；不 import，免得投影依赖 timer）；
#: 外部 source 的事件即使带 ``days`` 也走旧规则。行数上限 32：合法运行最多 ~8 天（遗忘超时 7 天封顶）。
_OWN_SOURCE = "agent-hook"
_MAX_DAY_ROWS = 32
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _valid_seconds(value, low: float) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and low <= value <= _MAX_SECONDS


def _rows(source, data: dict) -> list[tuple[str, int, int]]:
    """一次运行 → [(本地日, 秒, runs)]；坏载荷空列表。``handle`` 与 ``handle_place`` 共用同一套口径。"""
    days = data.get("days")
    if source == _OWN_SOURCE and isinstance(days, list) and len(days) <= _MAX_DAY_ROWS:  # v2.31：每天一行，首个有效日行记 runs=1，其余 0
        rows = []
        for item in days:
            # 纯比较、不转 float（理由同下）
            if not (isinstance(item, dict) and isinstance(item.get("date"), str)
                    and _DATE.fullmatch(item["date"]) and _valid_seconds(item.get("seconds"), 0)):
                continue
            rows.append((item["date"], int(item["seconds"]), 0 if rows else 1))
        return rows
    seconds, start_at = data.get("durationSeconds"), data.get("startAt")
    # 纯比较、不转 float：NaN 两边都不成立、±Inf 与超大 int（10**400）都超上限——
    # math.isfinite 会对超大 int 抛 OverflowError，反而重新引入崩溃
    if not isinstance(start_at, str) or not _valid_seconds(seconds, 0) or seconds == 0:
        return []
    try:
        moment = datetime.fromisoformat(start_at)
    except ValueError:
        return []
    if moment.tzinfo is None:
        return []
    return [(local_date(moment, settings.tz), int(seconds), 1)]


def handle(envelope: dict) -> None:
    subject = envelope.get("subject") or {}
    project_id = subject.get("project")
    data = envelope.get("data") or {}
    if data.get("unverified") is True:
        return  # v2.19.1：匿名开的运行只写泳道（契约「调用方范围与匿名上报」），不计入代理时长统计
    agent = data.get("agent")
    if not project_id or not isinstance(agent, str):
        return
    common = dict(user=envelope["user"], dedupe_key=applied_key(envelope), project_id=project_id,
                  task_id=subject.get("task"), agent=agent)
    for date, seconds, runs in _rows(envelope.get("source"), data):
        repo.apply_agent_daily_stat(**common, date=date, seconds=seconds, runs=runs)


def handle_place(envelope: dict) -> None:
    """``agent.run.placed``（v2.44）：已结束的运行整条秒数从旧去向搬到新去向（事件里抄了那条运行的时长）。
    在跑的运行没有抄录 → 不动（它还没进这张表）。**重建不走这里**：重建按最后一条去向重放 ``agent.run.completed``。"""
    data, subject = envelope.get("data") or {}, envelope.get("subject") or {}
    run = data.get("run")
    if not isinstance(run, dict) or run.get("unverified") is True or not isinstance(run.get("agent"), str):
        return
    old, new = (data.get("fromProjectId"), data.get("fromTaskId")), (subject.get("project"), subject.get("task"))
    if not old[0] or not new[0] or old == new:
        return
    for date, seconds, runs in _rows(run.get("source"), run):
        repo.move_agent_daily_stat(envelope["user"], date, run["agent"], old, new, seconds, runs)


def read_agent_daily_stats(
    user: str, date_from: str | None = None, date_to: str | None = None
) -> list[dict]:
    """views 的指定读路径（v2.3 ``views/agent-time``），同 ``daily_stats.read_daily_stats``。"""
    return repo.read_agent_daily_stats(user, date_from=date_from, date_to=date_to)
