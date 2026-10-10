"""``views/lanes`` 的「灵活留存」过滤（契约 v2.24 起、v2.25 加权重档位）。纯函数，不读库；只是视图过滤，不动任何计时。

一条泳道（身份 = ``prefs.service.ident``，与 ``lane_cap`` / ``lane_order.arrange`` 同口径）**显示**，当且仅当：

- 有在跑（未失联、未超上限 ``overdue``）的运行正处于干活相位，或在等人（waiting_input / waiting_permission，人要动手，
  等多久都不自动藏——只要没 ``overdue``）。干活相位的另有条件：声明过心跳（``beatCount > 0``，失联检测已管着它）
  或距 ``lastSeenAt`` 不到 ``IDLE_HIDE_SECONDS``。**代价**：不发心跳的工具静默超过 1 小时，就折叠，直到它的下一个事件；
- 或人置顶了它；
- 或按权重档位（``lane_tier``：high / normal / low）还在「留存期」内：有在跑的运行但都空闲 → 最后干活后 ``IDLE_KEEP[档位]`` 秒内；
  没有在跑的运行（全结束 / 失联 / 超上限）→ 最后结束后 ``ENDED_KEEP[档位]`` 秒内；出错 → 只有 high 在出错后留 ``ENDED_KEEP['high']`` 秒。
  最后干活 = 各运行里 working 段的最晚结束时刻——**不是最后心跳**，否则停着的会话永远不空闲。

**不显示**的进折叠列表（``inactiveAgents``，``reason`` = error / idle / ended，带 ``tier``）；low 档折叠满 ``FOLD_EXPIRE['low']`` 秒后不再列出，
只计入 ``expired``（视图层过期，什么都没删）。手动隐藏的身份不在此列（归 ``arrange`` 的 ``hiddenAgents``）。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from ..prefs.service import ident
from .lane_order import WAITING, _t, phase_of
from .lane_tier import ENDED_KEEP, FOLD_EXPIRE, IDLE_KEEP, lane_tier, seen, segments, stop_of

IDLE_HIDE_SECONDS = 3600  #: 干活相位却静默（无心跳）多久就不再算「在干活」


def _live_shown(item: dict, now: datetime) -> bool:
    """在跑、没失联、没超上限，且（在等人，或干活且有心跳 / 近 1 小时有信号）。"""
    if item["endAt"] is not None or item["lost"] or item.get("overdue"):
        return False
    ph = phase_of(item)
    if ph in WAITING:
        return True
    return ph == "working" and (bool(item.get("beatCount")) or (now - seen(item)).total_seconds() < IDLE_HIDE_SECONDS)


def _alive(item: dict) -> bool:
    return item["endAt"] is None and not item["lost"] and not item.get("overdue")


def _last_work(item: dict, now: datetime) -> datetime:
    """这条运行最后一次处于 working 的结束时刻（从没干过活 = 开始时刻；超上限的当作早已放弃 = 开始时刻）。"""
    return max([_t(item["startAt"])] + [b for _, b, ph in segments(item, now) if ph == "working"])


def _gone_end(item: dict, now: datetime) -> datetime:
    """这条运行「结束」的时刻：已结束 = 结束；失联 / 不发心跳 = 最后一次信号；超上限 = 开始（早已放弃）。"""
    return stop_of(item, now) or _t(item["startAt"])


def _verdict(every: list[dict], tier: str, now: datetime) -> tuple[str, datetime, datetime] | None:
    """不是「在干活 / 在等人」的泳道：留存期内 → None（继续显示）；否则 (reason, 最后干活, 折叠起算时刻)。"""
    latest = max(every, key=lambda r: r["startAt"])
    last = max(_last_work(r, now) for r in every)
    if latest["endAt"] is None and phase_of(latest) == "error":
        mark = max(_t(p["at"]) for p in latest["phases"] if p["phase"] == "error")
        reason, keep = "error", ENDED_KEEP["high"] if tier == "high" else 0
    elif latest["endAt"] is not None and latest["outcome"] == "failed":
        mark = _t(latest["endAt"])
        reason, keep = "error", ENDED_KEEP["high"] if tier == "high" else 0
    elif any(_alive(r) for r in every):
        mark, reason, keep = last, "idle", IDLE_KEEP[tier]
    else:
        mark, reason, keep = max(_gone_end(r, now) for r in every), "ended", ENDED_KEEP[tier]
    since = mark + timedelta(seconds=keep)
    return None if now < since else (reason, last, since)


def split(items: list[dict], gone: list[dict], prefs: dict, now: datetime, start: datetime, end: datetime):
    """``items`` = ``_run_item`` 的结果，``gone`` = ``lane_cap`` 的 dropped 聚合（带内部键 ``key``），``start`` / ``end`` = 查询窗口。
    返回 (留下的 items，inactiveAgents，剩下的 gone，expired 聚合 {agents, runs, elapsedSeconds})。
    不活跃身份被封顶丢掉的运行并入它自己的摘要（或 expired），不在 gone 里重复数。"""
    hidden = {a["key"] for a in prefs["agents"] if a["hidden"]}
    pinned = {a["key"] for a in prefs["agents"] if a["pinned"] and not a.get("unverified")}
    extra = {g["key"]: g.get("live", []) for g in gone}  # 封顶丢掉的在跑运行：只参与判定，不计入行数
    drop_secs = {g["key"]: g["elapsedSeconds"] for g in gone}
    lanes: dict[str, list[dict]] = {}
    for it in items:
        lanes.setdefault(ident(it["agent"], it["label"], it["unverified"]), []).append(it)

    firsts = {g["key"]: g for g in gone if g.get("live")}  # 整条泳道的运行都被封顶丢了、但还有在跑的：照样判定
    inactive: dict[str, dict] = {}
    expired = {"agents": 0, "runs": 0, "elapsedSeconds": 0}
    gone_keys: set[str] = set()  # 折叠的与过期的：运行都不再进 agents
    for key in {**{k: [] for k in firsts if k not in lanes}, **lanes}:
        rs = lanes.get(key, [])
        if key in hidden or key in pinned:
            continue
        every = rs + extra.get(key, [])
        if any(_live_shown(r, now) for r in every):
            continue
        tier = lane_tier(rs, extra.get(key, []), drop_secs.get(key, 0), now, start, end)
        verdict = _verdict(every, tier, now)
        if verdict is None:
            continue
        reason, last, since = verdict
        gone_keys.add(key)
        first = rs[0] if rs else firsts[key]
        runs = len(rs) + next((g["runs"] for g in gone if g["key"] == key), 0)
        secs = sum(r["elapsedSeconds"] or 0 for r in rs) + drop_secs.get(key, 0)
        if tier in FOLD_EXPIRE and (now - since).total_seconds() >= FOLD_EXPIRE[tier]:  # 视图层过期：只不再列出，什么都没删
            expired["agents"] += 1
            expired["runs"] += runs
            expired["elapsedSeconds"] += secs
            continue
        inactive[key] = {"agent": first["agent"], "label": first["label"], "unverified": first["unverified"],
                         "reason": reason, "tier": tier, "lastWorkAt": last.isoformat(), "runs": runs,
                         "elapsedSeconds": secs}
    kept = [it for it in items if ident(it["agent"], it["label"], it["unverified"]) not in gone_keys]
    rest = [g for g in gone if g["key"] not in gone_keys]
    out = sorted(inactive.values(), key=lambda a: a["lastWorkAt"], reverse=True)
    return kept, out, rest, expired
