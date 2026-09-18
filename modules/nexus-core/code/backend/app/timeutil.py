"""共享的「本地日期」换算（契约「日界与时区」v0.9）。

**只此一个函数**：`proj_daily_stats` 的归日（``daily_stats.py``）与
``views/gantt`` 的 ``today``（``views/queries.py``）都调它，不许各自算一遍——
两处各写一份迟早在 DST/闰年边界上打架，而这类错最难发现（图不会看着崩，
两边用同一个错基准，彼此自洽，只有用户对着自己的记忆纳闷时才会暴露）。

事件本身不动：``startAt``/``time`` 仍是带偏移的绝对时刻。本函数只把一个
已知时刻换算成「它落在某个时区的哪一天」——**归日**和**今天**都是这同一个
问题的两个特例（"今天" = "此刻这个时刻落在哪一天"）。
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo


def local_date(moment: datetime, tz: ZoneInfo) -> str:
    """把一个带时区的 ``moment`` 换算到 ``tz``，返回该时区下的日期（``YYYY-MM-DD``）。

    ``moment`` 必须是 aware datetime（带 tzinfo）——事件的 ``startAt``/``time``
    按契约永远带偏移；调用方若拿到 naive datetime，那是坏载荷，应在调用前
    自行判断是否要跳过，不该糊里糊涂传进来（``astimezone`` 对 naive 值的行为
    是「假设它是系统本地时间」，那会引入本函数刻意要消灭的那种环境依赖）。
    """
    return moment.astimezone(tz).date().isoformat()
