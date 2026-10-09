"""代理运行的活性（契约 v2.18「心跳与失联」，唯一事实源；对外协议见 ``contracts/agent.lane.v1``）。

钩子只在有事件时才响：CLI 崩了 / 被杀了，stop 永远不来，泳道就停在最后的相位上。
所以**活性规则住在服务端**：声明过会发心跳的运行，超过 ``AGENT_LOST_AFTER_SECONDS`` 没有任何信号
（start / phase / heartbeat）就是**失联**。没声明过的（老适配器、裸 curl）不受影响，仍只有遗忘超时兜底。

会发心跳的运行不看遗忘超时（``agent_run_timeout_hours``，只给不发心跳的运行兜底），但有更长的安全上限
``AGENT_DECLARED_MAX_SECONDS``：时长是**进程寿命**不是活动量，开着不关的会话不能无限记下去。

独立成文件同 ``backfill.py``（300 行纪律）；不 import ``agents``，``agents`` / ``agent_phases`` 都来读它。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from . import repo

#: 建议的心跳间隔（秒）。随 start / heartbeat 响应回给适配器（``heartbeatSeconds``），适配器不必写死。
AGENT_HEARTBEAT_SECONDS = 900
#: 这么久没有信号 = 失联（两个心跳间隔：丢一次心跳不算）。
AGENT_LOST_AFTER_SECONDS = 1800
#: 声明过心跳的运行的安全上限（7 天）：到了按 ``timeout`` 关（结束 = 开始 + 上限）。须远小于投影的
#: ``_MAX_SECONDS``（31 天，超了的事件统计里会静默丢掉）。
AGENT_DECLARED_MAX_SECONDS = 7 * 86400


def lost_at(run: dict, now: datetime) -> datetime | None:
    """失联的运行回它**最后一次有信号的时刻**（= 关闭它时的结束时刻），否则 None。"""
    if not run.get("heartbeat") or "closing" in run:
        return None
    seen = datetime.fromisoformat(run.get("lastSeenAt") or run["startedAt"])
    return seen if now - seen > timedelta(seconds=AGENT_LOST_AFTER_SECONDS) else None


def mark_expired(run: dict, now: datetime) -> dict | None:
    """声明过心跳的运行该关就打关闭标记，回快照给 ``agents._finish`` 落账；不该关 → None。
    - 超过 ``AGENT_DECLARED_MAX_SECONDS``：``outcome: timeout``，结束 = 开始 + 上限（同遗忘超时的算法）；
    - 失联：``outcome: lost``，结束 = 最后一次信号——不是现在、不是开始 + 上限，代理时长不虚增。
    两者都成立时取结束更早的（失联的结束在上限之前就按失联）。带着读到的 ``lastSeenAt`` 打标记，
    这期间又来了信号（或别人先关了）也是 None。"""
    if not run.get("heartbeat") or "closing" in run:
        return None
    seen = lost_at(run, now)
    cap_end = datetime.fromisoformat(run["startedAt"]) + timedelta(seconds=AGENT_DECLARED_MAX_SECONDS)
    if now > cap_end and (seen is None or seen > cap_end):
        marker = {"outcome": "timeout", "endedAt": cap_end.isoformat()}
    elif seen is not None:
        marker = {"outcome": "lost", "endedAt": seen.isoformat()}
    else:
        return None
    return repo.mark_agent_run_closing(run["user"], run["runId"], marker, {"lastSeenAt": run.get("lastSeenAt")})
