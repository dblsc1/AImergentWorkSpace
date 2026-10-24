"""对账的纯计算（契约 v2.38「四、写进台账」）：时间区间运算与「该入账什么 vs 已入账什么」的差。
全部用 UTC 毫秒整数，没有 IO。区间是左闭右开的 ``(start, end)``。
"""

from __future__ import annotations

GAP_MS = 300_000  # 相邻间隔 ≤ 300 秒连成一段；段收口 = 段末 + 300 秒 ≤ 现在
FOREVER = 1 << 62  # 正在跑的手动计时没有结束


def merge(ivs) -> list[tuple[int, int]]:
    out: list[list[int]] = []
    for s, e in sorted((int(s), int(e)) for s, e in ivs):
        if e <= s:
            continue
        if out and s <= out[-1][1]:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return [(s, e) for s, e in out]


def clip(ivs, lo: int, hi: int) -> list[tuple[int, int]]:
    return [(max(s, lo), min(e, hi)) for s, e in ivs if min(e, hi) > max(s, lo)]


def subtract(ivs, cuts) -> list[tuple[int, int]]:
    """``ivs`` 去掉 ``cuts``（两边都已 merge）。"""
    out = []
    for s, e in ivs:
        cur = s
        for cs, ce in cuts:
            if ce <= cur:
                continue
            if cs >= e:
                break
            if cs > cur:
                out.append((cur, cs))
            cur = max(cur, ce)
            if cur >= e:
                break
        if cur < e:
            out.append((cur, e))
    return out


def total(ivs) -> int:
    return sum(e - s for s, e in ivs)


def overlaps(ivs, cuts) -> bool:
    return total(subtract(merge(ivs), cuts)) < total(merge(ivs))


def seconds(ms: int) -> int:
    """四舍五入到整秒。"""
    return (ms + 500) // 1000


def group(ivs, cover) -> list[list[tuple[int, int]]]:
    """相邻间隔 ≤ 300 秒连成一段；已入账的段（``cover``）夹在中间时不跨过去。"""
    segs: list[list[tuple[int, int]]] = []
    for iv in ivs:
        if segs:
            end = segs[-1][-1][1]
            if iv[0] - end <= GAP_MS and not any(cs < iv[0] and ce > end for cs, ce in cover):
                segs[-1].append(iv)
                continue
        segs.append([iv])
    return segs


def plan(*, live: bool, reason: str, parts: list[dict], runs: list[dict], recs, others, now_ms: int,
         day_lo: int, day_hi: int, tgt, budget: int, blocked=frozenset()) -> dict:
    """``voids`` ``[(run, reason)]``、``reassigns`` ``[(run, partIdx)]``、``writes`` ``[段]``、``truncated``。
    ``recs`` 是已按 ``tiersSince`` 筛过的记录区间；``others`` 是别的来源已记 / 正在跑的时间（已 merge）；``tgt(part)`` 给 part 的目标任务 id；``blocked`` = 写不出的目标任务 id（已被删等），不再为它补写 / 改目标。
    不够预算（事件数）的动作留到下一轮：作废没做完就不改挂也不补写（覆盖要先让出来）。"""
    voids, reassigns, keep = [], [], []
    for r in runs:
        i = r["partIdx"]
        if not live:
            voids.append((r, reason))
        elif i >= len(parts) or [parts[i]["fromTs"], parts[i]["toTs"]] != list(r["win"]):
            voids.append((r, "block-split"))
        elif overlaps(r["ivs"], others):
            voids.append((r, "manual-overlap"))
        else:
            keep.append(r)
            if tgt(parts[i]) != r["task"] and tgt(parts[i]) not in blocked:
                reassigns.append((r, i))
    cover = merge(iv for r in keep for iv in r["ivs"])
    writes = []
    for i, p in enumerate(parts if live else []):
        if tgt(p) in blocked:
            continue
        lo = day_lo if p["fromTs"] is None else p["fromTs"]
        hi = day_hi if p["toTs"] is None else p["toTs"]
        left = subtract(subtract(merge(clip(recs, lo, hi)), others), cover)
        for seg in group(left, cover):
            s, e, secs = seg[0][0], seg[-1][1], seconds(total(seg))
            if e + GAP_MS <= now_ms and secs >= 1:
                writes.append({"partIdx": i, "s": s, "e": e, "ivs": seg, "seconds": secs})
    truncated = False
    if len(voids) > budget:
        voids, reassigns, writes, truncated = voids[:budget], [], [], True
    else:
        budget -= len(voids)
        if len(reassigns) > budget:
            reassigns, writes, truncated = reassigns[:budget], [], True
        else:
            budget -= len(reassigns)
            if len(writes) > budget:
                writes, truncated = writes[:budget], True
    return {"voids": voids, "reassigns": reassigns, "writes": writes, "truncated": truncated}
