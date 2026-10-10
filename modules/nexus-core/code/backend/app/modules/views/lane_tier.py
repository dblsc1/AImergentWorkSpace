"""泳道的权重档位（契约 v2.25）。纯函数，不读库；所有数字都是本文件里的命名常量，别处不写字面量。

三档（按先后第一个命中）：

- ``high``：窗口里人的注意力 ≥ ``ATTN_HIGH_SECONDS``；或窗口里干活时间 ≥ ``WORK_HIGH_SECONDS``（自己一直在运行）。
  （人置顶的泳道永远显示、根本不进折叠列表，所以档位里不看置顶。）
- ``low``：注意力 < ``ATTN_LOW_SECONDS`` 且窗口里干活时间 < ``EPHEMERAL_SECONDS``（临时拉起、没人看）。
  用**干活时间**而不是时长：在跑运行的时长会一直长到现在，开着不管的临时会话 5 分钟后就不再是 low，``IDLE_KEEP['low']`` 永远到不了。
- ``normal``：其余。

注意力取 ``_run_item`` 已裁到窗口的 ``attention``（不重算在场；每条运行至多最新 500 段）。档位按**本次查询的窗口**算：只查今天时长跑泳道的权重从 00:00 重新起算、昨天结束的运行不在列，环面板 03:00 前用的两天窗口则带过来（契约 v2.25 有记）。
被封顶丢掉的**已结束**运行只有轻读记录，
没有注意力 / 相位（有界、不多读一次库）：保守起见，有这种运行的泳道**不是临时的**，至少 ``normal``（宁可多留，也不因缺数据而过期）；
轻读被截断（``over``）时调用方同样按「有被丢的已结束运行」算（至少 normal）。超上限（``overdue``）的运行同理（早已放弃、没有相位，干活无从算起）；
被封顶丢掉的**在跑**运行有全文，照常计入注意力与干活。
"""

from __future__ import annotations

from datetime import datetime

from .lane_order import PHASES, _t

ATTN_HIGH_SECONDS = 600
WORK_HIGH_SECONDS = 3600
ATTN_LOW_SECONDS = 30
EPHEMERAL_SECONDS = 300  #: 窗口里干活时间不到这么久（且没人看）才算临时、low
#: 有在跑但空闲的泳道，最后干活之后还显示多久
IDLE_KEEP = {"high": 7200, "normal": 3600, "low": 600}
#: 没有在跑的运行（全结束 / 失联 / 超上限）、或出错之后，还显示多久
ENDED_KEEP = {"high": 1800, "normal": 0, "low": 0}
#: 折叠列表里留多久（从「不再显示」那一刻算起）；只有 low 会过期，缺的档位 = 窗口内一直留着
FOLD_EXPIRE = {"low": 7200}


def seen(item: dict) -> datetime:
    return _t(item.get("lastSeenAt") or item["startAt"])


def stop_of(item: dict, now: datetime) -> datetime | None:
    """这条运行「算到哪一刻」：已结束 = 结束；失联 / 不发心跳的在跑 = 最后一次信号（静默不算干活）；在跑 = 现在；超上限 = None（早已放弃）。"""
    start = _t(item["startAt"])
    if item["endAt"] is not None:
        return _t(item["endAt"])
    if item.get("overdue"):
        return None
    if item["lost"] or not item.get("beatCount"):
        return min(max(seen(item), start), now)
    return now


def segments(item: dict, now: datetime) -> list[tuple[datetime, datetime, str]]:
    """相位切成的段 [(起, 止, 相位)]，从开始到 ``stop_of``；没报过相位 = 从开始就在干活。超上限的 = []。"""
    start, stop = _t(item["startAt"]), stop_of(item, now)
    if stop is None:
        return []
    cuts = [(start, "working")] + sorted(
        ((min(max(_t(p["at"]), start), stop), p["phase"]) for p in item["phases"] if p["phase"] in PHASES),
        key=lambda c: c[0])
    return [(at, cuts[i + 1][0] if i + 1 < len(cuts) else stop, ph) for i, (at, ph) in enumerate(cuts)]


def _overlap(a: datetime, b: datetime, start: datetime, end: datetime) -> float:
    return max(0.0, (min(b, end) - max(a, start)).total_seconds())


def run_stats(item: dict, now: datetime, start: datetime, end: datetime) -> tuple[float, float]:
    """一条运行在窗口 [start, end) 里的 (注意力秒, 干活秒)。"""
    attn = sum(_overlap(_t(a["from"]), _t(a["to"]), start, end) for a in item.get("attention") or [])
    work = sum(_overlap(a, b, start, end) for a, b, ph in segments(item, now) if ph == "working")
    return attn, work


def tier_of(attn: float, work: float, dropped_closed: int = 0) -> str:
    if attn >= ATTN_HIGH_SECONDS or work >= WORK_HIGH_SECONDS:
        return "high"
    if attn < ATTN_LOW_SECONDS and work < EPHEMERAL_SECONDS and not dropped_closed:
        return "low"
    return "normal"


def lane_tier(rs: list[dict], extra: list[dict], dropped_closed: int, now: datetime, start: datetime, end: datetime) -> str:
    """``rs`` = 这条泳道保留下来的全文运行；``extra`` = 被封顶丢掉的在跑运行（只贡献注意力 / 干活）；``dropped_closed`` = 该身份被封顶丢掉的
    **已结束**运行条数（只有轻读记录，无相位）；>0 则不会是 low。"""
    stats = [run_stats(it, now, start, end) for it in rs]
    attn = sum(s[0] for s in stats) + sum(run_stats(it, now, start, end)[0] for it in extra)
    work = sum(s[1] for s in stats) + sum(run_stats(it, now, start, end)[1] for it in extra)
    unknown = dropped_closed + sum(1 for it in rs + extra if it.get("overdue"))  # 超上限的运行也没有相位可算干活，同样不是临时的
    return tier_of(attn, work, unknown)
