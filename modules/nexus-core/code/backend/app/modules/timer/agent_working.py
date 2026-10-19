"""代理「干活秒数」与按本地日切分（契约 v2.31「代理只算干活的时间」，唯一事实源）。

纯函数，不碰库、不碰时钟：结束时（``agents.snapshot_data``）和读在跑运行时（``views/agent-time``）共用。

口径：
- 干活区间 = 一条 ``working`` 相位到**下一条非 working 相位**（没有就到运行结束）；连续的 working 并成一段。
- 从运行开始到第一条相位算干活（同 ``views/lane_tier.segments``：开始 = 代理开始干活）；所以一条相位都没报过的运行
  （hermes / codex 等 API 起停）整段算干活，与旧行为相同；相位全在结束之后的运行同理，相位先钳到 [起点, 结束]。
- 相位的 ``at`` 偏移可混用（+00:00 / +08:00），一律转成时刻比较；落在起点之前的钳到起点。
- 按 ``NEXUS_TZ`` 的本地午夜切分，区间与每天长度都在 UTC 里相减（夏令时当天是 23 / 25 小时），每天秒数四舍五入成整数，总数不超过干活秒数的整数部分（多出的从最后一天扣），因此不会超过 ``durationSeconds``。
"""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo


def working_intervals(
    phases: list[dict] | None, started: datetime, ended: datetime
) -> list[tuple[datetime, datetime]]:
    # ponytail: 相位上限 MAX_PHASES=1000，超了之后的相位被丢，最后一条 working 会一直算到运行结束；不补偿
    out: list[tuple[datetime, datetime]] = []
    since: datetime | None = started
    for p in sorted(phases or [], key=lambda p: datetime.fromisoformat(p["at"])):  # 稳定排序：同刻保持到达先后
        at = min(max(datetime.fromisoformat(p["at"]), started), ended)
        if p["phase"] == "working":
            since = since or at
        elif since is not None:
            out.append((since, at))
            since = None
    if since is not None:
        out.append((since, ended))
    return [(a, b) for a, b in out if b > a]


def working_days(
    phases: list[dict] | None, started: datetime, ended: datetime, tz: ZoneInfo
) -> tuple[int, list[dict]]:
    """返回 ``(workingSeconds, days)``；``days`` 升序、只含秒数 > 0 的日子，全为 0 时给起点那天一条 0。"""
    per_day: dict[str, float] = {}
    for a, b in working_intervals(phases, started, ended):
        cur, end = a.astimezone(timezone.utc), b.astimezone(timezone.utc)
        while cur < end:
            day = cur.astimezone(tz).date()
            # 本地次日零点 → UTC：只用 tz 找切点，长度一律在 UTC 里减
            nxt = datetime.combine(day + timedelta(days=1), time.min, tzinfo=tz).astimezone(timezone.utc)
            seg_end = min(end, nxt)
            per_day[day.isoformat()] = per_day.get(day.isoformat(), 0.0) + (seg_end - cur).total_seconds()
            cur = seg_end
    days = [{"date": d, "seconds": round(s)} for d, s in sorted(per_day.items())]
    # 各天四舍五入后总数可能多出 1 秒：多出的从最后一天扣，保证 ≤ int(干活秒数)，因而 ≤ durationSeconds
    excess = sum(d["seconds"] for d in days) - int(sum(per_day.values()) + 1e-6)
    for d in reversed(days):
        cut = min(excess, d["seconds"])
        d["seconds"] -= cut
        excess -= cut
    days = [d for d in days if d["seconds"] > 0]
    if not days:
        days = [{"date": started.astimezone(tz).date().isoformat(), "seconds": 0}]
    return sum(d["seconds"] for d in days), days
