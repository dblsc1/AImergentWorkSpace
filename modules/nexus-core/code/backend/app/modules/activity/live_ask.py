"""实时询问：同一窗口只问一次（契约 onboarding.v1「三」）。

读在场心跳时间线（``presence``，与 ``focus`` 同一份），不另存；写 ``activity_asked``（``asked_repo``）。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from ... import config
from ...tenant import current as current_tenant
from ..planner.errors import NotFoundError
from . import activity_settings, asked_repo, auto, block_display, blocks, blocks_repo, focus, presence, records_repo, repo

log = logging.getLogger(__name__)

#: 相邻两段该块时间的间隔不超过它算连续
CONTINUE_GAP = timedelta(seconds=60)
ANSWERS = ("assigned", "split", "ignored", "unmanaged")


def _continuous(spans: list[dict], now: datetime, floor: datetime) -> tuple[int, datetime]:
    """当前窗口最近一段连续停留：``(在该块上的秒数合计, 第一拍时刻)``；离开 / 切走的时间不计，只看 ``floor`` 之后。"""
    key = focus._run_key(spans[-1])  # noqa: SLF001
    mine = [(max(s["from"], floor), min(s["to"], now)) for s in spans if focus._run_key(s) == key]  # noqa: SLF001
    mine = [(a, b) for a, b in mine if b > a]
    if not mine:
        return 0, now
    i = len(mine) - 1
    while i > 0 and mine[i][0] - mine[i - 1][1] <= CONTINUE_GAP:
        i -= 1
    return round(sum((b - a).total_seconds() for a, b in mine[i:])), mine[i][0]


def ask() -> dict:
    user, now = current_tenant(), auto._now()  # noqa: SLF001
    cfg = activity_settings.get()
    if not cfg["tiers"] or not cfg["liveAskEnabled"]:
        return {"ask": None}
    fresh = [d for d in repo.presence_list(user) if now - d["lastAt"] <= auto.FRESH and d.get("spans")]
    if not fresh:
        return {"ask": None}
    dev = max(fresh, key=lambda d: d["lastAt"])  # 只看最近报心跳的那台设备（同 focus）
    cur = dev["spans"][-1]
    if cur["afk"]:
        return {"ask": None}
    today = now.astimezone(config.settings.tz).date()  # 「今天」与 date 一律取 NEXUS_TZ 本地日；0 点处断开
    seconds, since = _continuous(dev["spans"], now, max(now - presence.WINDOW, blocks._midnight(today)))  # noqa: SLF001
    if seconds < cfg["liveAskMinutes"] * 60:
        return {"ask": None}
    key, src, recs = focus.current_block(user, now, dev.get("deviceId"), cur["app"], cur["title"])
    shown = block_display.describe(key, src, recs, block_display.domains(user))
    if not shown["legible"] or asked_repo.get(user, key):
        return {"ask": None}
    item = blocks.block_item(user, today, key)
    if item["status"] != "pending":
        return {"ask": None}
    return {"ask": {"blockKey": key, "displayName": shown["displayName"], "focusedSeconds": seconds,
                    "since": since.astimezone(config.settings.tz).isoformat(), "date": today.isoformat(), "seq": item["seq"]}}


def mark_asked(key: str, answer: str) -> dict:
    """人对这个块名做了成功的操作：写一条（已有不动）。blocks/claim（NB-5）对 ``claimed`` 里的块名也走这里。"""
    return asked_repo.add_if_absent(current_tenant(), key, answer, datetime.now(timezone.utc))


def mark_asked_quietly(key: str, answer: str) -> None:
    """块操作已经成功之后调：记账失败只记日志，不让已成功的操作变 500。"""
    try:
        mark_asked(key, answer)
    except Exception:  # noqa: BLE001
        log.warning("activity_asked 没写成：%r", key, exc_info=True)


def _iso(doc: dict) -> dict:
    return {**doc, "askedAt": doc["askedAt"].replace(tzinfo=timezone.utc).isoformat()}


def export_asked() -> list[dict]:
    return [_iso(a) for a in asked_repo.list_all(current_tenant())]


def restore_asked(rows: list[dict]) -> int:
    """``rows`` 已由恢复校验过；只落规定的三个键。"""
    return asked_repo.restore(current_tenant(), [{"blockKey": r["blockKey"], "answer": r["answer"],
                                                  "askedAt": datetime.fromisoformat(r["askedAt"])} for r in rows])


def answer_unmanaged(key: str) -> dict:
    user = current_tenant()
    if not asked_repo.get(user, key):
        blocks.housekeeping(user)  # 先清过期记录，「留存期内」才算数
        if not (records_repo.has_block(user, key) or blocks_repo.has_block_doc(user, key)):
            raise NotFoundError(f"窗口块不存在：{key!r}")
    return _iso(mark_asked(key, "unmanaged"))
