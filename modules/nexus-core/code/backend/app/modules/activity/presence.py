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

v2.17.1：同一设备的两次心跳并发时按版本号 ``v`` 条件写，不中就重读重算（不再后写覆盖先写）。
v2.17.2：**注意力只跟着已提交的时间线**——这一拍要记的 attend 先作为 ``pendingAttend``（outbox）和时间线**同一份文档**
一起条件写；写中之后才逐条 ``record_attend``（并集、幂等）并清掉。写不中的一拍什么 attend 都没写过；写中之后
记失败 / 进程崩了，outbox 留在文档里，同设备的下一拍先补上。时间线只增不倒：一拍只算已提交末尾之后的部分，
更早的一拍晚到（并发 / 乱序）不插进去；当前状态（``lastAt`` / ``app`` / ``title`` / ``afk``）只会往新走。
ponytail: 每拍整份文档读一遍、写一遍（至多 MAX_SPANS 段）；单人自托管够用，真嫌重再把段拆成独立集合。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from ...tenant import current as current_tenant
from ..timer import service as timer_service
from . import ignore, repo, session_link

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
#: 同一设备并发的心跳只会是重发的那一两拍，重试这么多次还不中 = 有 bug，响亮失败
_CAS_RETRIES = 20
_MAX_APP, _MAX_TITLE = 128, 512  # 码点，超了截断（同活动建议）
#: outbox 的条数上限：正常只装一拍的量（≤ MAX_BEAT_SPANS + 1 条），留着的旧拍没补上才会叠加
MAX_PENDING = 64
#: 同一组 outbox 条目连续这么多拍记不上就丢掉（有日志）
MAX_ATTEND_FAILS = 5
_log = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def clip(app: str, title: str) -> tuple[str, str]:
    return app[:_MAX_APP], title[:_MAX_TITLE]


def _apply(user: str, pending: list[dict]) -> list[dict]:
    """outbox → ``record_attend``（并集、幂等：重复补不多算）。按 (runId, gap) 分组、**各组独立**试：返回没记上的条目
    （留着下次补；条目上的 ``fails`` 是这组连续失败的次数）。一组连续失败 ``MAX_ATTEND_FAILS`` 次就丢掉并记一行日志——
    一条永远记不上的运行（如已被清掉）不能挡住排在它后面的健康运行、也不能把 outbox 撑到上限而挤掉别人的注意力。"""
    groups: dict[tuple, list] = {}
    for p in pending:  # 旧的先记
        groups.setdefault((p["runId"], p["gap"]), []).append(p)
    left: list[dict] = []
    for (run_id, gap), items in groups.items():
        try:
            timer_service.record_attend(user, run_id, [(p["from"], p["to"]) for p in items], timedelta(seconds=gap))  # 连线 attend：跨子边界只走 service
        except Exception:  # noqa: BLE001 — 时间线已提交，注意力留在 outbox
            fails = max(p.get("fails", 0) for p in items) + 1
            if fails >= MAX_ATTEND_FAILS:
                _log.warning("在场心跳：运行 %s 的 attend 连续 %d 次没记上，丢弃 %d 条", run_id, fails, len(items), exc_info=True)
                continue
            _log.warning("在场心跳：运行 %s 的 attend 没记上，留在 outbox 等下一拍", run_id, exc_info=True)
            left += [{**p, "fails": fails} for p in items]
    return left


def heartbeat(device_id: str, app: str, title: str, afk: bool, guess: dict | None = None,
              spans: list[dict] | None = None, sent_at: datetime | None = None) -> dict:
    """``guess``（v2.14）= ``{taskId | projectId, confidence}``，调用方已校验；离开时不存。
    ``spans``（v2.17）= ``[{app, title, from, seconds, guess?}]``，调用方已校验（按时间排、不重叠、不晚于 ``sent_at``）。"""
    user, now = current_tenant(), _now()
    cutoff = now - WINDOW
    repo.presence_purge(user, cutoff)
    app, title = clip(app, title)
    runs: list[dict] | None = None

    def target(g: dict | None) -> tuple:
        return (g.get("taskId"), g.get("projectId")) if g else (None, None)

    for _ in range(_CAS_RETRIES):
        doc = repo.presence_get(user, device_id)
        line: list[dict] = doc["spans"] if doc else []
        # 上一拍写中了时间线、但没来得及（或没能）记的 attend：先补上（幂等），补不上就带着走，不丢
        carried = doc.get("pendingAttend") or [] if doc else []
        if carried:
            carried = _apply(user, carried)  # 没记上的带着走（各组独立，记不上的不挡别的）
        attended: list[dict] = []  # 这一拍要记的 attend（outbox 条目）
        # 老的一拍晚到（先收到的后提交）：不改写当前状态，也不往时间线上补点
        older = bool(doc) and doc["lastAt"] > now

        def add(start: datetime, end: datetime, app: str, title: str, afk: bool, guess: dict | None,
                join: timedelta, exact: bool) -> None:
            nonlocal runs
            watching: list[str] = []
            if not afk and title:  # 这个窗口是不是在跑的代理会话
                if runs is None:
                    runs = timer_service.list_lane_runs(user)[1]  # 一拍只读一遍
                if exact:  # 带停留的段：v2.13 的相等规则，恰好一条才认
                    watching = [hit] if (hit := session_link.watched(runs, app, title)) else []
                else:  # 老心跳：v2.4 的包含规则，命中几条记几条（v2.17.1）
                    watching = session_link.contained(runs, title)
            run_id = watching[0] if len(watching) == 1 else None  # 时间线上只写分得清的那一条
            last = line[-1] if line else None
            if (last and (last["app"], last["title"], last["afk"]) == (app, title, afk) and start - last["to"] <= join
                    and target(last.get("guess")) == target(guess) and last.get("runId") == run_id):
                if exact:
                    start = last["to"]  # 接着待：注意力从上一段的末尾接上，与时间线一致
                last["to"] = max(last["to"], end)
                if guess:
                    last["guess"] = guess  # 把握取最新的
            else:
                line.append({"from": start, "to": end, "app": app, "title": title, "afk": afk,
                             **({"guess": guess} if guess else {}), **({"runId": run_id} if run_id else {}),
                             **({"exact": True} if exact else {})})
            for hit in watching:
                attended.append({"runId": hit, "from": start, "to": end, "gap": 0 if exact else join.total_seconds()})

        if spans is not None:
            shift = now - sent_at  # 设备的钟 → 服务端的钟
            floor = line[-1]["to"] if line else cutoff  # 串行 + 只增不倒：新的段不早于已提交的末尾
            for span in spans:
                start = max(span["from"] + shift, floor)
                end = min(span["from"] + timedelta(seconds=span["seconds"]) + shift, now)
                if end > start:
                    add(start, end, *clip(span["app"], span["title"]), False, span.get("guess"), SPAN_JOIN, True)
                    floor = end
        if (spans is None or afk) and not older:  # 老检测程序的心跳；离开那一下
            add(now, now, app, title, afk, None if afk else guess, MERGE_GAP, False)
        line = [{**s, "from": max(s["from"], cutoff)} for s in line if s["to"] >= cutoff][-MAX_SPANS:]

        # 时间线和 outbox 同一份文档一起条件写：写不中 = 什么都没发生，下面的 attend 从没写过
        pending = [*carried, *attended][-MAX_PENDING:]
        cur = doc if older else {"lastAt": now, "app": app, "title": title, "afk": afk}
        committed = repo.presence_cas({"user": user, "deviceId": device_id, "lastAt": cur["lastAt"],
                                       "app": cur["app"], "title": cur["title"], "afk": cur["afk"],
                                       "spans": line, "pendingAttend": pending}, doc)
        if committed:
            break
    else:
        raise RuntimeError(f"在场心跳写入争用未决：{device_id!r}")
    # 时间线已落盘：现在才记注意力。记失败不让这一拍失败（时间线是持久的），outbox 留着等下一拍
    # 只试这一拍新产生的（带过来的上面刚试过）；记上的从 outbox 清掉，没记上的带着失败次数留下
    if attended:
        left = _apply(user, attended)
        if left != attended:
            repo.presence_cas({k: v for k, v in committed.items() if k not in ("v", "gen")}
                              | {"pendingAttend": [*carried, *left][-MAX_PENDING:]}, committed)
    # 每次写入**之后**修剪到上限（自己不删）：并发的几次写入各修剪一次，最后一次一定看得见全部写入，
    # 所以请求都结束后上限必然成立（被挤掉的设备下一次心跳会把自己写回来并挤掉别人）。
    repo.presence_delete(user, repo.presence_evictable(user, device_id, MAX_DEVICES))
    return {"ok": True}


def list_spans(user: str, now: datetime, start: datetime, end: datetime) -> list[dict]:
    """``views/lanes`` 的 ``human.presence``：最近 2 小时内、与窗口重叠的段，按 from 升序。**不写**。"""
    cutoff = now - WINDOW
    clipped = (
        # 只裁返回的副本，不写；guess（v2.14）不回出；runId（v2.17）= 这一段人在看的那条代理运行，没有为 null
        {"deviceId": doc["deviceId"], **{k: span[k] for k in ("to", "app", "title", "afk")},
         "runId": span.get("runId"), "from": max(span["from"], cutoff)}
        for doc in ignore.presence_docs(user)
        for span in doc.get("spans") or []
        if span["to"] >= cutoff
    )
    out = [s for s in clipped if s["from"] < end and s["to"] > start]  # 先裁再判重叠
    return sorted(out, key=lambda s: s["from"])
