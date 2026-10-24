"""键盘鼠标活动（契约 v2.38「键盘鼠标活动」）：只存有活动的 10 秒格（UNIX 秒数，10 的倍数），没有任何按键内容。
按 ``(user, deviceId, 本地日)`` 存成有序去重的整数数组；GET 把多台设备合并去重。留存同原始记录。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ... import config
from ...tenant import current as current_tenant
from ..planner.errors import UnprocessableError
from . import records_repo

BIN_SECONDS, MAX_BINS = 10, 8640


def upload(device_id: str, bins: list[int]) -> dict:
    user, now, tz = current_tenant(), datetime.now(timezone.utc), config.settings.tz
    lo, hi = now - timedelta(days=7), now + timedelta(seconds=60)
    if any(not lo.timestamp() <= b <= hi.timestamp() for b in bins):
        raise UnprocessableError("bins 须在 [现在 − 7 天, 现在 + 60 秒] 内")
    ttl = timedelta(days=config.settings.record_ttl_days)
    records_repo.purge(user, now - ttl, (now.astimezone(tz) - ttl).date().isoformat())
    by_day: dict[str, list[int]] = {}
    for b in set(bins):
        by_day.setdefault(datetime.fromtimestamp(b, tz).date().isoformat(), []).append(b)
    for day, vals in by_day.items():
        records_repo.add_bins(user, device_id, day, sorted(vals), now)
    return {"accepted": len(set(bins))}


def day(date: str) -> dict:
    return {"date": date, "binSeconds": BIN_SECONDS, "bins": records_repo.day_bins(current_tenant(), date)}
