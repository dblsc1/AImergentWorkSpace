"""``views/lanes`` 的封顶（契约 v2.23）。纯函数，不读库。

封顶是为了保护响应体积，**不许让一条吵闹的代理挤掉别的代理，也不许挤掉它自己更早的时间**：

- 活状态里的运行（调用方标 ``open``：在跑的、刚标了结束还没落账的）永远保留——它们个数本来就有界；
- 已结束的按代理身份（``prefs.service.ident``，与 ``lane_order.arrange`` 同一个口径）分组，
  只留最近活动的 ``MAX_AGENTS`` 个身份，每个身份只留最新的 ``MAX_RUNS_PER_LANE`` 条；
- 全部加起来再超 ``MAX_RUNS_TOTAL`` 就从最旧的开始丢（最后兜底）；
- 丢掉的不是消失：按身份汇成 ``dropped``（条数 + 裁到窗口的秒数），总数照样对得上。
"""

from __future__ import annotations

from datetime import datetime

from ..prefs.service import ident

MAX_AGENTS = 200
MAX_RUNS_PER_LANE = 100
MAX_RUNS_TOTAL = 2000


def lane_key(run: dict) -> str:
    return ident(run.get("agent"), run.get("label"), bool(run.get("unverified")))


def cap(runs: list[dict], start: datetime, end: datetime, now: datetime) -> tuple[list[dict], list[dict]]:
    """返回 (保留的运行，按开始时间升序；dropped 聚合，最近活动的身份在前，每项带内部键 ``key``)。"""
    lanes: dict[str, list[dict]] = {}
    for r in sorted(runs, key=lambda r: r["startTs"], reverse=True):
        lanes.setdefault(lane_key(r), []).append(r)
    order = sorted(lanes, key=lambda k: max(r["endTs"] or now for r in lanes[k]), reverse=True)

    keep = [r for r in runs if r.get("open")]
    closed, gone = [], []
    for i, k in enumerate(order):
        rs = [r for r in lanes[k] if not r.get("open")]
        room = MAX_RUNS_PER_LANE if i < MAX_AGENTS else 0
        closed += rs[:room]
        gone += rs[room:]
    closed.sort(key=lambda r: r["startTs"], reverse=True)
    room = max(0, MAX_RUNS_TOTAL - len(keep))
    keep += closed[:room]
    gone += closed[room:]

    agg: dict[str, dict] = {}
    for r in gone:
        k = lane_key(r)
        row = agg.setdefault(k, {"key": k, "agent": r.get("agent"), "label": r.get("label"),
                                 "unverified": bool(r.get("unverified")), "runs": 0, "elapsedSeconds": 0})
        row["runs"] += 1
        row["elapsedSeconds"] += max(0, int((min(r["endTs"], end) - max(r["startTs"], start)).total_seconds()))
    rank = {k: i for i, k in enumerate(order)}
    return sorted(keep, key=lambda r: r["startTs"]), sorted(agg.values(), key=lambda a: rank[a["key"]])
