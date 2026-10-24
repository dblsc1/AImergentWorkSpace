"""``views/history`` 与 ``views/arrangements`` 共用的代理运行读法（契约 v2.39 二、三）。

相位段复用 ``lane_tier.segments``（v2.18 的推法：第一个转入点之前当 working，失联 / 不发心跳的止于最后信号），
运行条目复用 ``lanes._run_item`` / ``lanes_projection.read_lanes``；这里只补「合并相邻同相位」「干活区间」「人回复 / 人在看」
这几样纯计算。**只读**（用 ``list_lane_runs``，不收超时运行）。匿名开的运行（``unverified``）不算（同 agent-time）。
"""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone

from ...config import settings
from ...tenant import current as current_tenant
from ..projector.handlers import lanes as lanes_projection
from ..timer import service as timer_service
from .lane_tier import segments
from .lanes import MAX_CLOSED_READ, _run_item

Interval = tuple[datetime, datetime]


def midnight(day) -> datetime:
    return datetime.combine(day, time(), settings.tz)


def iso(moment: datetime) -> str:
    return moment.astimezone(settings.tz).isoformat()


def clip(a: datetime, b: datetime, start: datetime, end: datetime) -> Interval | None:
    lo, hi = max(a, start), min(b, end)
    return (lo, hi) if hi > lo else None


def seconds(spans: list[Interval]) -> float:
    return sum((b - a).total_seconds() for a, b in spans)


def union(spans: list[Interval]) -> list[Interval]:
    out: list[list[datetime]] = []
    for a, b in sorted(spans):
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def split_days(spans: list[Interval]) -> dict[str, float]:
    """区间按 ``NEXUS_TZ`` 的本地午夜切开，每个本地日一个秒数（夏令时当天长度照 UTC 相减）。"""
    per: dict[str, float] = {}
    for a, b in spans:
        cur = a
        while cur < b:
            day = cur.astimezone(settings.tz).date()
            nxt = min(b, midnight(day + timedelta(days=1)))
            per[day.isoformat()] = per.get(day.isoformat(), 0.0) + (nxt.astimezone(timezone.utc) - cur.astimezone(timezone.utc)).total_seconds()
            cur = nxt
    return per


def _attends(run: dict) -> list[Interval]:
    return union([(datetime.fromisoformat(i["at"]), datetime.fromisoformat(i["until"]))
                  for i in run.get("interactions") or [] if i.get("kind") == "attend"])


def _merged(segs: list[tuple[datetime, datetime, str]]) -> list[tuple[datetime, datetime, str]]:
    out: list[list] = []
    for a, b, ph in segs:
        if b <= a:
            continue
        if out and out[-1][2] == ph and out[-1][1] == a:
            out[-1][1] = b
        else:
            out.append([a, b, ph])
    return [(a, b, ph) for a, b, ph in out]


def load(start: datetime, end: datetime) -> tuple[datetime, list[dict], bool]:
    """与 [start, end) 有交集的运行 → ``(now, 条目, 是否因读取上限而不全)``。条目：
    ``{runId, agent, label, taskId, projectId, startAt, endAt|None, open, lost, segs（合并后的相位段，未钳）, attend（人在看，未钳）, replies}``。"""
    user = current_tenant()
    now, open_runs = timer_service.list_lane_runs(user)
    closed = lanes_projection.read_lanes(user, "run", start, end, MAX_CLOSED_READ + 1)
    over = len(closed) > MAX_CLOSED_READ
    runs = [{**r, "startTs": r["startAt"], "endTs": r["endAt"], "elapsedSeconds": r["durationSeconds"], "overdue": False}
            for r in closed[:MAX_CLOSED_READ]]
    seen = {r["runId"] for r in runs}
    runs += [{**r, "open": True} for r in open_runs
             if r["runId"] not in seen and r["startTs"] < end and (r["endTs"] or now) > start]
    out = []
    for run in runs:
        item = _run_item(run, start, end)
        if item["unverified"]:
            continue
        out.append({
            "runId": item["runId"], "agent": item["agent"], "label": item["label"],
            "taskId": item["taskId"], "projectId": item["projectId"],
            "startAt": datetime.fromisoformat(item["startAt"]),
            "endAt": datetime.fromisoformat(item["endAt"]) if item["endAt"] else None,
            "open": bool(run.get("open")), "lost": item["lost"], "overdue": bool(item.get("overdue")),
            "segs": _merged(segments(item, now)),
            "attend": _attends(run),
            "replies": sorted(datetime.fromisoformat(i["at"]) for i in run.get("interactions") or [] if i.get("kind") == "reply"),
        })
    return now, out, over


def working(run: dict) -> list[Interval]:
    return [(a, b) for a, b, ph in run["segs"] if ph == "working"]
