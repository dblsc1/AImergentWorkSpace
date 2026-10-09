"""代理运行的活性（契约 v2.18「心跳与失联」，唯一事实源；对外协议见 ``contracts/agent.lane.v1``）。

钩子只在有事件时才响：CLI 崩了 / 被杀了，stop 永远不来，泳道就停在最后的相位上。
所以**活性规则住在服务端**：声明过会发心跳的运行，超过 ``AGENT_LOST_AFTER_SECONDS`` 没有任何信号
（start / phase / heartbeat）就是**失联**。没声明过的（老适配器、裸 curl）不受影响，仍只有遗忘超时兜底。

会发心跳的运行活着就不封顶：遗忘超时（``agent_run_timeout_hours``）只剩给不发心跳的运行兜底。

独立成文件同 ``backfill.py``（300 行纪律）；不 import ``agents``，``agents`` / ``agent_phases`` 都来读它。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from . import repo

#: 建议的心跳间隔（秒）。随 start / heartbeat 响应回给适配器（``heartbeatSeconds``），适配器不必写死。
AGENT_HEARTBEAT_SECONDS = 900
#: 这么久没有信号 = 失联（两个心跳间隔：丢一次心跳不算）。
AGENT_LOST_AFTER_SECONDS = 1800


def lost_at(run: dict, now: datetime) -> datetime | None:
    """失联的运行回它**最后一次有信号的时刻**（= 关闭它时的结束时刻），否则 None。"""
    if not run.get("heartbeat") or "closing" in run:
        return None
    seen = datetime.fromisoformat(run.get("lastSeenAt") or run["startedAt"])
    return seen if now - seen > timedelta(seconds=AGENT_LOST_AFTER_SECONDS) else None


def mark_lost(run: dict, now: datetime) -> dict | None:
    """失联就打关闭标记（``outcome: lost``，结束 = 最后一次信号——不是现在、不是开始 + 上限，代理时长不虚增），
    回快照给 ``agents._finish`` 落账。没失联 → None；带着读到的 ``lastSeenAt`` 打标记，这期间又来了信号
    （或别人先关了）也是 None。"""
    seen = lost_at(run, now)
    if seen is None:
        return None
    marker = {"outcome": "lost", "endedAt": seen.isoformat()}
    return repo.mark_agent_run_closing(run["user"], run["runId"], marker, {"lastSeenAt": run.get("lastSeenAt")})
