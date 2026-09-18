"""``plan`` 字段（``{start,end}``）的格式与区间校验。

拆成独立文件的理由同 `deps.py`/`errors.py`——项目与任务的 `plan` 共用同一份
校验（契约「排期与依赖」明文：不是照抄一份），拆出来给 `service.py` 减负，
把它拉回契约钉死的 300 行预算以内。**不是新的子边界**，仍在 `planner/`
内部，只服务于 `service.py`。
"""

from __future__ import annotations

import re
from datetime import datetime

from .errors import InvalidInputError

_PLAN_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _parse_plan_date(value: object, owner: str, field: str) -> datetime:
    """``plan.start``/``plan.end`` 必须是 ``YYYY-MM-DD``（契约「甘特读端」G6）：
    正则先卡格式（拒没补零的/非字符串），``strptime`` 再卡真实日历（拒 2-30）。"""
    if not isinstance(value, str) or not _PLAN_DATE_RE.match(value):
        raise InvalidInputError(
            f"{owner} 的 plan.{field} 必须是 YYYY-MM-DD 格式的日期字符串，实际 {value!r}"
        )
    try:
        return datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        raise InvalidInputError(f"{owner} 的 plan.{field} 不是合法日期：{value!r}") from None


def validate_plan(plan: dict | None, owner: str) -> dict | None:
    """``plan=None`` 是清空计划的合法输入（契约「甘特读端」G5），不校验直接放行。
    非 None 时校验 start/end 格式，并要求 **end 不得早于 start**——
    错误消息点名具体字段与取值（契约 v0.7 的 ``{detail}`` 形状）。
    """
    if plan is None:
        return None
    start_raw, end_raw = plan.get("start"), plan.get("end")
    start = _parse_plan_date(start_raw, owner, "start")
    end = _parse_plan_date(end_raw, owner, "end")
    if end < start:
        raise InvalidInputError(
            f"{owner} 的 plan.end 不得早于 plan.start：start={start_raw!r}, end={end_raw!r}"
        )
    return {"start": start_raw, "end": end_raw}
