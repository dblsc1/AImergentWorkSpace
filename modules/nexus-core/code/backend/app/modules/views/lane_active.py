"""``views/lanes`` 的「只显示在干活的」过滤（契约 v2.24）。纯函数，不读库；只是视图过滤，不动任何计时。

一条泳道（身份 = ``prefs.service.ident``，与 ``lane_cap`` / ``lane_order.arrange`` 同口径）**显示**，当且仅当：

- 有在跑（未失联、未超上限 ``overdue``）的运行正处于干活相位，或在等人（waiting_input / waiting_permission，人要动手，
  等多久都不自动藏——只要没 ``overdue``）。干活相位的另有条件：声明过心跳（``beatCount > 0``，失联检测已管着它）
  或距 ``lastSeenAt`` 不到 ``IDLE_HIDE_SECONDS``。**代价**：不发心跳的工具静默超过 1 小时，就折叠，直到它的下一个事件；
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


def _seen(item: dict) -> datetime:
    return _t(item.get("lastSeenAt") or item["startAt"])


def _live_shown(item: dict, now: datetime) -> bool:
    """在跑、没失联、没超上限，且（在等人，或干活且有心跳 / 近 1 小时有信号）。"""
    if item["endAt"] is not None or item["lost"] or item.get("overdue"):
        return False
    ph = phase_of(item)
    if ph in WAITING:
        return True
    return ph == "working" and (bool(item.get("beatCount")) or (now - _seen(item)).total_seconds() < IDLE_HIDE_SECONDS)


def _last_work(item: dict, now: datetime) -> datetime:
    """这条运行最后一次处于 working 的结束时刻（从没干过活 = 开始时刻）。
    在跑的：失联 / 不发心跳的，干活段到 ``lastSeenAt`` 为止（静默不算干活）；超上限的当作早已放弃 = 开始时刻。"""
    start = _t(item["startAt"])
    if item["endAt"] is not None:
        stop = _t(item["endAt"])
    elif item.get("overdue"):
        return start
    elif item["lost"] or not item.get("beatCount"):
        stop = min(max(_seen(item), start), now)
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
    extra = {g["key"]: g.get("live", []) for g in gone}  # 封顶丢掉的在跑运行：只参与判定，不计入行数
    lanes: dict[str, list[dict]] = {}
    for it in items:
        lanes.setdefault(ident(it["agent"], it["label"], it["unverified"]), []).append(it)

    firsts = {g["key"]: g for g in gone if g.get("live")}  # 整条泳道的运行都被封顶丢了、但还有在跑的：照样判定
    inactive: dict[str, dict] = {}
    for key in {**{k: [] for k in firsts if k not in lanes}, **lanes}:
        rs = lanes.get(key, [])
        if key in hidden or key in pinned:
            continue
        every = rs + extra.get(key, [])
        if any(_live_shown(r, now) for r in every):
            continue
        latest = max(every, key=lambda r: r["startAt"])
        failed = phase_of(latest) == "error" if latest["endAt"] is None else latest["outcome"] == "failed"
        last = max(_last_work(r, now) for r in every)
        if failed:
            reason = "error"
        elif (now - last).total_seconds() >= IDLE_HIDE_SECONDS:
            reason = "idle"
        else:
            continue
        first = rs[0] if rs else firsts[key]
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
