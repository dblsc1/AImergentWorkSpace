"""``session.completed`` / ``agent.run.completed`` → ``proj_lanes``（契约 v2.4「投影 proj_lanes」）。

一条事实 → 一条区间，给 ``views/lanes`` 画时间线。**本投影不求和、不进任何人的汇总**——
两种 kind 住一张表只为按时间区间一次查出来；谁也不许拿它算时长。

铁则同其余 handler：幂等（唯一约束 (user, key)）、只写自己的集合、不发事件；
坏载荷（时刻解析不了 / 不带偏移、时长非有限数或超 31 天、结束早于开始）静默跳过。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from .. import repo
from .agent_daily_stats import applied_key

_MAX_SECONDS = 31 * 86400  # 同 agent_daily_stats：只拦坏载荷，不是业务规则


def _moment(raw) -> datetime | None:
    if not isinstance(raw, str):
        return None
    try:
        moment = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else None


def _seconds(raw) -> int | None:
    # 纯比较、不转 float：NaN 两边都不成立、±Inf 与超大 int 都超上限（同 agent_daily_stats）
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not 0 < raw <= _MAX_SECONDS:
        return None
    return int(raw)


def _clean_phases(raw) -> list[dict]:
    """外部 source 也能写 agent.run.completed：形状不对的观测丢掉，别让一条坏事件炸了读端。"""
    out = []
    for item in raw if isinstance(raw, list) else []:
        if isinstance(item, dict) and _moment(item.get("at")) and isinstance(item.get("phase"), str):
            detail = item.get("detail")
            out.append({"at": item["at"], "phase": item["phase"],
                        **({"detail": detail} if isinstance(detail, str) else {})})
    return out


def _clean_interactions(raw) -> list[dict]:
    out = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict) or not _moment(item.get("at")):
            continue
        if item.get("kind") == "reply":
            out.append({"kind": "reply", "at": item["at"]})
        elif item.get("kind") == "attend" and _moment(item.get("until")):
            out.append({"kind": "attend", "at": item["at"], "until": item["until"]})
    return out


def handle(envelope: dict) -> None:
    subject = envelope.get("subject") or {}
    data = envelope.get("data") or {}
    start = _moment(data.get("startAt"))
    seconds = _seconds(data.get("durationSeconds"))
    if start is None or seconds is None:
        return
    doc = {
        "user": envelope["user"],
        "key": applied_key(envelope),
        "startAt": start,
        "taskId": subject.get("task"),
        "projectId": subject.get("project"),
        "durationSeconds": seconds,
    }
    if envelope.get("type") == "session.completed":
        end = _moment(envelope.get("time"))  # endAt = 事件 time
        if end is None or end < start:
            return
        doc.update(kind="session", endAt=end, mode=data.get("mode") or "do", source=envelope.get("source"))
    else:
        dedupe = envelope.get("dedupeKey") or ""
        doc.update(
            kind="run", endAt=start + timedelta(seconds=seconds),
            runId=dedupe.removeprefix("agent:"),
            agent=data.get("agent"), tool=data.get("tool"), model=data.get("model"),
            label=data.get("label"), outcome=data.get("outcome"),
            phases=_clean_phases(data.get("phases")),
            interactions=_clean_interactions(data.get("interactions")),
        )
    repo.apply_lane(doc)


def handle_reassign(envelope: dict) -> None:
    """吃一条已落库的 ``session.reassigned``（契约 v2.11）：换那一段的 taskId / projectId，其余不动。"""
    data = envelope.get("data") or {}
    subject = envelope.get("subject") or {}
    session = data.get("session")
    seq = data.get("seq")
    if (
        type(seq) is not int or not isinstance(session, dict)
        or not isinstance(session.get("source"), str) or not isinstance(session.get("dedupeKey"), str)
    ):
        return
    repo.reassign_lane(envelope["user"], applied_key(session), subject.get("task"), subject.get("project"), seq)


def read_lanes(user: str, kind: str, start: datetime, end: datetime, limit: int) -> list[dict]:
    """views 的指定读路径（v2.4 ``views/lanes``）。"""
    return repo.read_lanes(user, kind, start, end, limit)
