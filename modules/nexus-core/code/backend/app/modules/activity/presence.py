"""在场心跳（契约 v2.4「在场心跳」节，唯一事实源）。

桌面检测程序约每 15 秒报一次「人此刻前台是什么」，给页面画实时的人那条线、并认「人在看哪个代理」。
**活状态，不是事实**：住 ``activity_presence``，只留最近 2 小时，不进台账 / 投影 / 导出 / 快照恢复，
也与活动建议无关（心跳不会变成建议，建议也不读心跳）。

- 时间由服务端盖（收到的时刻）：心跳说的就是「现在」，不需要对时。
- 相邻心跳 ``(app, title, afk)`` 相同且间隔 ≤ 45 秒 → 延长上一段；否则开新段。
- 清理只在心跳写入时做（本设备的旧段 + 该租户 2 小时没心跳的设备）；读端只过滤。
- 每租户至多 20 台设备：``deviceId`` 是客户端自报的，不设上限就是一个无界写入口。

ponytail: 同一设备的两次心跳并发时后写覆盖先写（最多丢一次段的延长），心跳 15 秒一次、
同设备串行发，不为它加乐观锁。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ...tenant import current as current_tenant
from ..timer import service as timer_service
from . import repo

WINDOW = timedelta(hours=2)
MERGE_GAP = timedelta(seconds=45)
MAX_SPANS = 500
MAX_DEVICES = 20
_MAX_APP, _MAX_TITLE = 128, 512  # 码点，超了截断（同活动建议）


def _now() -> datetime:
    return datetime.now(timezone.utc)


def heartbeat(device_id: str, app: str, title: str, afk: bool) -> dict:
    user, now = current_tenant(), _now()
    cutoff = now - WINDOW
    repo.presence_purge(user, cutoff)
    doc = repo.presence_get(user, device_id)
    app, title = app[:_MAX_APP], title[:_MAX_TITLE]

    spans = doc["spans"] if doc else []
    last = spans[-1] if spans else None
    if last and (last["app"], last["title"], last["afk"]) == (app, title, afk) and now - last["to"] <= MERGE_GAP:
        last["to"] = now
    else:
        spans.append({"from": now, "to": now, "app": app, "title": title, "afk": afk})
    spans = [{**s, "from": max(s["from"], cutoff)} for s in spans if s["to"] >= cutoff][-MAX_SPANS:]
    repo.presence_put({"user": user, "deviceId": device_id, "lastAt": now,
                       "app": app, "title": title, "afk": afk, "spans": spans})
    # 每次写入**之后**修剪到上限（自己不删）：并发的几次写入各修剪一次，最后一次一定看得见全部写入，
    # 所以请求都结束后上限必然成立（被挤掉的设备下一次心跳会把自己写回来并挤掉别人）。
    repo.presence_delete(user, repo.presence_evictable(user, device_id, MAX_DEVICES))

    if not afk and title:
        timer_service.record_attend(user, title, now)  # 连线 attend：跨子边界只走 service
    return {"ok": True}


def list_spans(user: str, now: datetime, start: datetime, end: datetime) -> list[dict]:
    """``views/lanes`` 的 ``human.presence``：最近 2 小时内、与窗口重叠的段，按 from 升序。**不写**。"""
    cutoff = now - WINDOW
    clipped = (
        {"deviceId": doc["deviceId"], **span, "from": max(span["from"], cutoff)}  # 只裁返回的副本，不写
        for doc in repo.presence_list(user)
        for span in doc.get("spans") or []
        if span["to"] >= cutoff
    )
    out = [s for s in clipped if s["from"] < end and s["to"] > start]  # 先裁再判重叠
    return sorted(out, key=lambda s: s["from"])
