"""``views/lanes`` 的「只显示在干活的」过滤（契约 v2.24）。纯函数，不读库；只是视图过滤，不动任何计时。

一条泳道（身份 = ``prefs.service.ident``，与 ``lane_cap`` / ``lane_order.arrange`` 同口径）**显示**，当且仅当：

- 有在跑（未失联）的运行正处于干活相位，或在等人（waiting_input / waiting_permission，人要动手，永不自动藏）；
- 或「最后一次干活」距现在不到 ``IDLE_HIDE_SECONDS``；最后干活 = 各运行里 working 段的最晚结束时刻
  （没报过相位的已结束运行 = 它的结束；没报过相位的在跑运行 = 从开始就在干活）——**不是最后心跳**，否则停着的会话永远不空闲；
- 或人置顶了它。

**不显示**（``inactiveAgents``）：当前状态是出错（最新一条运行是在跑且相位 error，或已结束且 outcome=failed），
或空闲 / 全部结束已满 ``IDLE_HIDE_SECONDS``。手动隐藏的身份不在此列（归 ``arrange`` 的 ``hiddenAgents``）。
"""

from __future__ import annotations

from datetime import datetime

from ..prefs.service import ident
from .lane_order import PHASES, WAITING, _t, phase_of

IDLE_HIDE_SECONDS = 3600


def _last_work(item: dict, now: datetime) -> datetime:
    """这条运行最后一次处于 working 的结束时刻（从没干过活 = 开始时刻）。"""
    start = _t(item["startAt"])
    if item["endAt"] is not None:
        stop = _t(item["endAt"])
    elif item["lost"] and item["lastSeenAt"]:
        stop = _t(item["lastSeenAt"])
    else:
        stop = now
    cuts = [(start, "working")] + sorted(
        ((min(max(_t(p["at"]), start), stop), p["phase"]) for p in item["phases"] if p["phase"] in PHASES),
        key=lambda c: c[0])
    last = start
    for i, (at, ph) in enumerate(cuts):
        if ph == "working":
            last = max(last, cuts[i + 1][0] if i + 1 < len(cuts) else stop)
    return last


def split(items: list[dict], gone: list[dict], prefs: dict, now: datetime) -> tuple[list[dict], list[dict], list[dict]]:
    """``items`` = ``_run_item`` 的结果，``gone`` = ``lane_cap`` 的 dropped 聚合（带内部键 ``key``）。
    返回 (留下的 items，inactiveAgents，剩下的 gone)。不活跃身份被封顶丢掉的运行并入它自己的摘要，不在 gone 里重复数。"""
    hidden = {a["key"] for a in prefs["agents"] if a["hidden"]}
    pinned = {a["key"] for a in prefs["agents"] if a["pinned"] and not a.get("unverified")}
    lanes: dict[str, list[dict]] = {}
    for it in items:
        lanes.setdefault(ident(it["agent"], it["label"], it["unverified"]), []).append(it)

    inactive: dict[str, dict] = {}
    for key, rs in lanes.items():
        if key in hidden or key in pinned:
            continue
        live = [r for r in rs if r["endAt"] is None and not r["lost"]]
        if any(phase_of(r) == "working" or phase_of(r) in WAITING for r in live):
            continue
        latest = max(rs, key=lambda r: r["startAt"])
        failed = phase_of(latest) == "error" if latest["endAt"] is None else latest["outcome"] == "failed"
        last = max(_last_work(r, now) for r in rs)
        if failed:
            reason = "error"
        elif (now - last).total_seconds() >= IDLE_HIDE_SECONDS:
            reason = "idle"
        else:
            continue
        first = rs[0]
        inactive[key] = {"agent": first["agent"], "label": first["label"], "unverified": first["unverified"],
                         "reason": reason, "lastWorkAt": last.isoformat(), "runs": len(rs),
                         "elapsedSeconds": sum(r["elapsedSeconds"] or 0 for r in rs)}
    for g in gone:
        if g["key"] in inactive:
            inactive[g["key"]]["runs"] += g["runs"]
            inactive[g["key"]]["elapsedSeconds"] += g["elapsedSeconds"]
    kept = [it for it in items if ident(it["agent"], it["label"], it["unverified"]) not in inactive]
    rest = [g for g in gone if g["key"] not in inactive]
    out = sorted(inactive.values(), key=lambda a: a["lastWorkAt"], reverse=True)
    return kept, out, rest
