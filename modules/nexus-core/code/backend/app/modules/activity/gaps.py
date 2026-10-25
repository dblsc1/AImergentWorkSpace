"""v2.40 / v0.4.2：``GET activity/gaps``（当天原始记录的空洞）与 ``GET activity/blocks/history``（窗口以前归到哪）。**只读**。

空洞只依据原始记录算；与检测程序掉线区间有交集的只是加标注（掉线期间的记录事后会从 ActivityWatch 补传）。
"""

from __future__ import annotations

from datetime import date as Date, datetime, time, timedelta

from ... import config
from ...tenant import current as current_tenant
from ..planner import service as planner_service
from ..planner.errors import UnprocessableError
from ..views import address as address_mod
from . import blocks, records_repo

GAP_MIN, GAP_FROM_HOUR, MAX_GAPS, MAX_DEST = 600, 8, 50, 20


def _offline(user: str) -> list[tuple[datetime, datetime]]:
    from ..detector import repo as detector_repo  # noqa: PLC0415 —— detector 反过来 import activity，顶层 import 成环

    return [(e["at"], e["endAt"]) for e in detector_repo.user_events(user) if e["kind"] == "detector_offline" and e.get("endAt")]


def day_gaps(d: Date) -> list[dict]:
    """当天 08:00 到现在（或当天结束）里没有任何记录、长于 10 分钟的时间段，按时间升序。"""
    user, tz = current_tenant(), config.settings.tz
    lo = datetime.combine(d, time(GAP_FROM_HOUR), tzinfo=tz)
    hi = min(datetime.now(tz), datetime.combine(d + timedelta(days=1), time(0), tzinfo=tz))
    spans = sorted((max(r["startTs"], lo), min(r["endTs"], hi)) for r in records_repo.day_records(user, d.isoformat())
                   if min(r["endTs"], hi) > max(r["startTs"], lo))
    holes, cur = [], lo
    for s, e in spans + [(hi, hi)]:
        if (s - cur).total_seconds() > GAP_MIN:
            holes.append((cur, s))
        cur = max(cur, e)
    off = _offline(user) if holes else []
    return [{"from": a.astimezone(tz).isoformat(), "to": b.astimezone(tz).isoformat(), "seconds": round((b - a).total_seconds()),
             "detectorOffline": any(s < b and e > a for s, e in off)} for a, b in holes[:MAX_GAPS]]


def gaps(d: Date | None) -> dict:
    d = d or blocks.today()
    return {"date": d.isoformat(), "gaps": day_gaps(d)}


def match_history(block: str | None, app: str | None, title: str | None, days: int) -> dict:
    if (block is None) == (app is None) or (block is None and title is None):
        raise UnprocessableError("block 与 app+title 二选一：要么给 block，要么同时给 app 和 title")
    user, today = current_tenant(), blocks.today()
    first = (today - timedelta(days=days - 1)).isoformat()
    by_day: dict[str, set[str]] = {}
    for date, key in records_repo.match_blocks(user, first, block, app, title):
        by_day.setdefault(date, set()).add(key)
    addrs, dest = address_mod.load(), {}
    for date in sorted(by_day):  # 升序：后写的 lastAt / via 就是最近的
        for it in blocks._items(user, Date.fromisoformat(date)):  # noqa: SLF001
            if it["blockKey"] not in by_day[date] or it["status"] != "assigned":
                continue
            for p in it["parts"]:
                task = planner_service.get_task(p["taskId"]) if p.get("taskId") else None
                pid = (task or {}).get("projectId") or p.get("projectId")
                obj = task or (planner_service.get_project(pid) if pid else None)
                if obj is None:
                    continue
                row = dest.setdefault((p.get("taskId"), pid), {
                    "taskId": p.get("taskId"), "projectId": pid, "address": addrs.target(p.get("taskId"), pid),
                    "name": obj["name"], "times": 0, "seconds": 0})
                row["times"] += 1
                row["seconds"] += p["seconds"]
                row["lastAt"], row["via"] = it["lastAt"], it["via"]
    out = sorted(sorted(dest.values(), key=lambda r: r["lastAt"], reverse=True), key=lambda r: -r["times"])[:MAX_DEST]  # 次数多的在前，同次数近的在前
    return {"query": {"block": block} if block is not None else {"app": app, "title": title}, "days": days, "destinations": out}
