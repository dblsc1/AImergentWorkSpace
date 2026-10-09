"""在场心跳（契约 v2.4「在场心跳」节 + v2.17「串行的注意力时间线」节，唯一事实源）。

桌面检测程序约每 5 秒报一次「人此刻前台是什么」，v2.17 起每拍带上一拍以来人依次在过的窗口与各自的停留
（``spans``）。**人的注意力是严格串行的**：每台设备一条时间线，段与段不重叠；这里只存不合计，不按窗口归组。
**活状态，不是事实**：住 ``activity_presence``，只留最近 2 小时，不进台账 / 投影 / 导出 / 快照恢复，
也与活动建议无关（心跳不会变成建议，建议也不读心跳）。

- 时间由服务端盖：不带 ``spans`` 的心跳 = 收到的时刻；带的按 ``sentAt`` 把各段平移到服务端的时钟上（不需要对时）。
- 不带 ``spans``（老检测程序）：相邻心跳 ``(app, title, afk)`` 相同且间隔 ≤ 45 秒 → 延长上一段；否则开新段。
  带 ``spans``：各段原样接到时间线末尾（早于已有末尾的部分裁掉——同一段不会记两遍），与上一段同窗口且间隔 ≤ 2 秒才并。
  v2.14：段可带规则的猜测 ``guess``，目标变了也开新段。v2.17：段对上了在跑的代理会话就记 ``runId``。
- 清理只在心跳写入时做（本设备的旧段 + 该租户 2 小时没心跳的设备）；读端只过滤。
- 每租户至多 20 台设备：``deviceId`` 是客户端自报的，不设上限就是一个无界写入口。

ponytail: 同一设备的两次心跳并发时后写覆盖先写（最多丢一拍的段），同设备串行发，不为它加乐观锁。
ponytail: 每拍整份文档读一遍、写一遍（至多 MAX_SPANS 段）；单人自托管够用，真嫌重再把段拆成独立集合。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ...tenant import current as current_tenant
from ..timer import service as timer_service
from . import repo, session_link

WINDOW = timedelta(hours=2)
MERGE_GAP = timedelta(seconds=45)
#: v2.17 带 spans 的心跳：与时间线上一段同窗口、间隔不超过它才算「接着待」，否则是另一段（切走再回来 = 新的一段）
SPAN_JOIN = timedelta(seconds=2)
#: 一拍里的段最早能比 ``sentAt`` 早这么久（检测程序自己只往回 60 秒）；更早 → 422
SPAN_LOOKBACK = timedelta(seconds=120)
MAX_BEAT_SPANS = 32  #: 一拍至多带这么多段（检测程序自己限 12）
#: 每设备的段数上限：每 3 秒切一次窗口约 100 分钟；切得更快的人，时间线短于 2 小时
MAX_SPANS = 2000
MAX_DEVICES = 20
_MAX_APP, _MAX_TITLE = 128, 512  # 码点，超了截断（同活动建议）


def _now() -> datetime:
    return datetime.now(timezone.utc)


def clip(app: str, title: str) -> tuple[str, str]:
    return app[:_MAX_APP], title[:_MAX_TITLE]


def heartbeat(device_id: str, app: str, title: str, afk: bool, guess: dict | None = None,
              spans: list[dict] | None = None, sent_at: datetime | None = None) -> dict:
    """``guess``（v2.14）= ``{taskId | projectId, confidence}``，调用方已校验；离开时不存。
    ``spans``（v2.17）= ``[{app, title, from, seconds, guess?}]``，调用方已校验（按时间排、不重叠、不晚于 ``sent_at``）。"""
    user, now = current_tenant(), _now()
    cutoff = now - WINDOW
    repo.presence_purge(user, cutoff)
    doc = repo.presence_get(user, device_id)
    app, title = clip(app, title)
    line: list[dict] = doc["spans"] if doc else []
    runs: list[dict] | None = None
    attended: dict[str, list] = {}  # runId → [(from, to)]，按时间排

    def target(g: dict | None) -> tuple:
        return (g.get("taskId"), g.get("projectId")) if g else (None, None)

    def add(start: datetime, end: datetime, app: str, title: str, afk: bool, guess: dict | None,
            join: timedelta, exact: bool) -> None:
        nonlocal runs
        run_id = None
        if not afk and title:  # 这个窗口是不是某个在跑的代理会话（v2.13 同一条相等规则；对上不止一个 → 不认）
            if runs is None:
                runs = timer_service.list_lane_runs(user)[1]  # 一拍只读一遍
            run_id = session_link.watched(runs, app, title)
        last = line[-1] if line else None
        if (last and (last["app"], last["title"], last["afk"]) == (app, title, afk) and start - last["to"] <= join
                and target(last.get("guess")) == target(guess) and last.get("runId") == run_id):
            last["to"] = max(last["to"], end)
            if guess:
                last["guess"] = guess  # 把握取最新的
        else:
            line.append({"from": start, "to": end, "app": app, "title": title, "afk": afk,
                         **({"guess": guess} if guess else {}), **({"runId": run_id} if run_id else {}),
                         **({"exact": True} if exact else {})})
        if run_id:
            attended.setdefault(run_id, []).append((start, end))

    if spans is not None:
        shift = now - sent_at  # 设备的时钟 → 服务端的时钟
        floor = line[-1]["to"] if line else cutoff  # 串行：新的段不早于时间线的末尾
        for span in spans:
            start = max(span["from"] + shift, floor)
            end = min(span["from"] + timedelta(seconds=span["seconds"]) + shift, now)
            if end > start:
                add(start, end, *clip(span["app"], span["title"]), False, span.get("guess"), SPAN_JOIN, True)
                floor = end
    if spans is None or afk:  # 老检测程序的心跳；离开那一下
        add(now, now, app, title, afk, None if afk else guess, MERGE_GAP, False)
    line = [{**s, "from": max(s["from"], cutoff)} for s in line if s["to"] >= cutoff][-MAX_SPANS:]
    repo.presence_put({"user": user, "deviceId": device_id, "lastAt": now,
                       "app": app, "title": title, "afk": afk, "spans": line})
    # 每次写入**之后**修剪到上限（自己不删）：并发的几次写入各修剪一次，最后一次一定看得见全部写入，
    # 所以请求都结束后上限必然成立（被挤掉的设备下一次心跳会把自己写回来并挤掉别人）。
    repo.presence_delete(user, repo.presence_evictable(user, device_id, MAX_DEVICES))

    join = MERGE_GAP if spans is None else SPAN_JOIN
    for run_id, intervals in attended.items():
        timer_service.record_attend(user, run_id, intervals, join)  # 连线 attend：跨子边界只走 service
    return {"ok": True}


def list_spans(user: str, now: datetime, start: datetime, end: datetime) -> list[dict]:
    """``views/lanes`` 的 ``human.presence``：最近 2 小时内、与窗口重叠的段，按 from 升序。**不写**。"""
    cutoff = now - WINDOW
    clipped = (
        # 只裁返回的副本，不写；guess（v2.14）不回出；runId（v2.17）= 这一段人在看的那条代理运行，没有为 null
        {"deviceId": doc["deviceId"], **{k: span[k] for k in ("to", "app", "title", "afk")},
         "runId": span.get("runId"), "from": max(span["from"], cutoff)}
        for doc in repo.presence_list(user)
        for span in doc.get("spans") or []
        if span["to"] >= cutoff
    )
    out = [s for s in clipped if s["from"] < end and s["to"] > start]  # 先裁再判重叠
    return sorted(out, key=lambda s: s["from"])
