"""``views/lanes`` 的代理排序与藏起来（契约 v2.22）。纯函数，不读库。

排法（规范性）：置顶且在跑的（按置顶先后）→ 其余按活跃排，手动排过位的在跑运行**固定**在它的 slot（位置）上、没排过位的
按活跃排填满剩下的位置 → 已结束的。手动的按 slot 升序依次插入（slot 夹在「未置顶的在跑个数」之内；同 slot 只在
排过位的运行结束后留下空位 / 新运行出现时可能发生，按活跃排先后）。写 slot 的一侧（``prefs.service.set_order``）
保证 slot 两两不同，所以平时每个手动的恰好落在自己的 slot。
「活跃排」与计时页 lanes.js 的 ``rankRuns`` 同一个口径：档位（在等你 0 → 干活 1 → 出错 2 → 空闲 3 → 失联 3.5 → 已结束 4），
同档按近 ``RANK_WINDOW`` 里不空闲的秒数倒序，同分按最近一次相位转入倒序。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from ..prefs.service import ident

RANK_WINDOW = timedelta(hours=3)
PHASES = ("working", "waiting_input", "waiting_permission", "idle", "error")
WAITING = ("waiting_input", "waiting_permission")


def _t(raw: str) -> datetime:
    return datetime.fromisoformat(raw)


def phase_of(item: dict) -> str:
    seen = sorted((p for p in item["phases"] if p["phase"] in PHASES), key=lambda p: _t(p["at"]))
    return seen[-1]["phase"] if seen else "working"


def _key(item: dict, now: datetime) -> tuple:
    """(档位, -活跃秒数, -最近转入)：升序即最前。"""
    phase = phase_of(item)
    ended, lost = item["endAt"] is not None, item["lost"]
    tier = 4 if ended else 3.5 if lost else 0 if phase in WAITING else 1 if phase == "working" else 2 if phase == "error" else 3
    start = _t(item["startAt"])
    end = _t(item["endAt"]) if ended else _t(item["lastSeenAt"]) if lost and item["lastSeenAt"] else now
    cuts = [(start, "working")] + sorted(
        ((min(max(_t(p["at"]), start), end), p["phase"]) for p in item["phases"] if p["phase"] in PHASES),
        key=lambda c: c[0])
    lo, hi = now - RANK_WINDOW, now
    active = 0.0
    for i, (at, ph) in enumerate(cuts):
        stop = cuts[i + 1][0] if i + 1 < len(cuts) else end
        if ph != "idle":
            active += max(0.0, (min(stop, hi) - max(at, lo)).total_seconds())
    last = max((_t(p["at"]) for p in item["phases"] if p["phase"] in PHASES), default=start)
    return (tier, -active, -last.timestamp())


def arrange(items: list[dict], prefs: dict, now: datetime, running_extra: frozenset | set = frozenset()) -> tuple[list[dict], list[dict], int, list[dict]]:
    """``items`` = ``_run_item`` 的结果。返回 (去掉藏起来的、加了 pinned / manualOrder / rank 的行，hiddenAgents，hiddenWaiting，
    stalePinned)。行的先后不变（仍按开始时间），先后在 ``rank``。失联的运行不算「在等你」（hiddenAgents.phase 与 hiddenWaiting 一致）。
    ``running_extra`` = 另外还在跑、但被封顶挤出 items 的身份键。
    stalePinned = 置顶着、但现在没有任何在跑的运行对得上的身份（改名后旧置顶就是这样留下的），页面列出来让人移除。"""
    hidden = {a["key"]: a for a in prefs["agents"] if a["hidden"]}
    pinned = {a["key"]: a["pinnedAt"] for a in prefs["agents"] if a["pinned"] and not a.get("unverified")}
    slots = {o["runId"]: o["slot"] for o in prefs["order"]}

    shown, summary, waiting = [], {k: {"agent": a["agent"], "label": a["label"], "unverified": a.get("unverified", False),
                                       "live": False, "phase": None}
                                   for k, a in hidden.items()}, 0
    for item in items:
        key = ident(item["agent"], item["label"], item["unverified"])
        if key not in hidden:
            shown.append((key, item))
            continue
        if item["endAt"] is None:  # 在跑（含失联）：藏起来的列表要说它还活着、在什么相位；等你的数进 hiddenWaiting
            row, phase = summary[key], "idle" if item["lost"] else phase_of(item)   # 失联的按空闲算，不抢「在等你」
            row["live"] = True
            if row["phase"] is None or phase in WAITING:
                row["phase"] = phase
            waiting += phase in WAITING

    # 未验证（匿名）的运行自成一组，永远排在已验证的后面：不继承置顶、不能手动排位
    ranked = sorted((s for s in shown if not s[1]["unverified"]), key=lambda s: _key(s[1], now))
    anon = sorted((s for s in shown if s[1]["unverified"]), key=lambda s: _key(s[1], now))
    live = [s for s in ranked if s[1]["endAt"] is None]
    top = sorted((s for s in live if s[0] in pinned), key=lambda s: pinned[s[0]])
    top_ids = {id(s) for s in top}
    rest = [s for s in ranked if id(s) not in top_ids]
    movers = sorted((s for s in rest if s[1]["endAt"] is None and s[1]["runId"] in slots),
                    key=lambda s: slots[s[1]["runId"]])  # 同 slot 的保持原先后（sorted 稳定）
    base = [s for s in rest if s not in movers]
    for s in movers:  # 已结束的永远在后：slot 夹在在跑的个数之内
        base.insert(min(slots[s[1]["runId"]], sum(1 for b in base if b[1]["endAt"] is None)), s)
    final = top + base + anon

    rank = {id(s[1]): i for i, s in enumerate(final)}
    placed = {s[1]["runId"] for s in movers}
    out = [{**item, "pinned": key in pinned, "manualOrder": slots[item["runId"]] if item["runId"] in placed else None,
            "rank": rank[id(item)]} for key, item in shown]
    running = {ident(i["agent"], i["label"], i["unverified"]) for i in items if i["endAt"] is None} | set(running_extra)
    stale = [{"agent": a["agent"], "label": a["label"]} for a in prefs["agents"]
             if a["pinned"] and not a.get("unverified") and a["key"] not in running]
    return out, list(summary.values()), waiting, stale
