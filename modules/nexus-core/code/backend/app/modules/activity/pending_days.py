"""v2.26 待确认建议按天汇总（契约「待确认时间的按天汇总」）：``GET /activity/suggestions/pending-days``。

给「人今天干了多久」这类问题补上**还没确认**的那部分：已记账的时间在 ``views/gantt``，待确认的建议
只在列表里，只看前者会把「有 111 条待确认」读成「0 分钟」。本文件**纯只读**，不碰任何投影，只给数字
（没有 app / title，所以忽略规则与脱敏都不受影响）。

- 范围：``status=pending`` 的建议，按 ``startTs``（段开始时刻）归日，日界是 ``NEXUS_TZ`` 的本地日
  （与 ``proj_daily_stats`` 按 ``data.startAt`` 归日同一条规则，由 mongo 的 ``$dateToString`` 带时区换算）。
- 这是**上界估计**，不是工时：待确认的段之间、与已记账的时间之间都可能重叠，也含 ``idle`` 段。
- 工作量有界：待确认的建议 14 天（``suggestion_ttl_days``）后清掉，用 ``(user,status,startTs)`` 索引；
  区间最多 92 天（MCP 侧上限），聚合每天一行，响应至多 92 行。
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from ... import config
from ...tenant import current as current_tenant
from ..planner.errors import UnprocessableError
from . import repo

MAX_DAYS = 92  # 与 MCP 的区间上限一致


def pending_days(date_from: date, date_to: date) -> dict:
    span = (date_to - date_from).days + 1
    if not 1 <= span <= MAX_DAYS:
        raise UnprocessableError(f"from..to 须在 1..{MAX_DAYS} 天内（含两端），收到 {span} 天")
    tz = config.settings.tz
    # 本地日 [from 0 点, to+1 日 0 点) 换成绝对时刻再查：DST 当天的日长不是 24 小时也对
    lo = datetime.combine(date_from, time.min, tzinfo=tz)
    hi = datetime.combine(date_to + timedelta(days=1), time.min, tzinfo=tz)
    rows = repo._col().aggregate([  # noqa: SLF001
        {"$match": {"user": current_tenant(), "status": "pending", "startTs": {"$gte": lo, "$lt": hi}}},
        {"$group": {"_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$startTs", "timezone": tz.key}},
                    "seconds": {"$sum": "$durationSeconds"}, "count": {"$sum": 1}}},
        {"$sort": {"_id": 1}},
    ])
    days = [{"date": r["_id"], "seconds": r["seconds"], "count": r["count"]} for r in rows]
    return {"totalSeconds": sum(d["seconds"] for d in days), "count": sum(d["count"] for d in days), "days": days}
