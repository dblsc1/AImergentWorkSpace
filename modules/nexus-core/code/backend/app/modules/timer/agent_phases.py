"""代理运行的相位与连线（契约 v2.4「人一条线、代理多条线的时间线」，唯一事实源）。

在跑时相位与连线是 ``agent_runs`` 文档上的活状态，结束时随那一条 ``agent.run.completed``
落账（组装在 ``agents.snapshot_data``）。拆成独立文件同 ``backfill.py``（300 行纪律）。

关键取舍：

- **每条观测原样存、按 (at, 到达先后) 排**，存的时候不合并——乱序到达时先合并会丢信息；
  合并是读方的事。
- **乐观锁（``v`` 版本号）+ 「未关闭」条件的单文档更新**：相位、reply、attend 在 Python 里
  算好整条数组再一次写回，条件不中就重读重算。关闭标记一打，这里的写入一律落空——
  于是「回了 200 却没进事实」不会发生（关闭边界见 ``agents._close``）。
- ``at`` 超前 300 秒以内**不钳**：钳到「现在」会让重试算出另一个 ``at``，去重就失效了。
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Callable
from datetime import datetime, timedelta

from ... import config
from ..planner.errors import NotFoundError, UnprocessableError
from . import agents, repo
from .agent_liveness import AGENT_HEARTBEAT_SECONDS, lost_at

MAX_PHASES = 1000
MAX_INTERACTIONS = 500  #: reply 的条数上限
#: attend 的条数上限（v2.17 起与 reply 分开数：每 3 秒切一次窗口的人，每次切回来都是新的一条）
MAX_ATTENDS = 2000
CLOCK_SKEW = timedelta(seconds=300)  # 同活动建议的时钟误差口径
#: 同一运行的并发写（异步钩子并行到达）只有几条，重试这么多次还不中 = 有 bug，响亮失败
_CAS_RETRIES = 50


def _insert_sorted(items: list[dict], item: dict) -> None:
    """按 at 升序插入；at 相同时后到的排后面（bisect_right）。"""
    keys = [agents.ts(x["at"]) for x in items]
    items.insert(bisect_right(keys, agents.ts(item["at"])), item)


def _closed_out(user: str, run_id: str, run: dict | None) -> dict:
    if run is None:
        # 活状态没了：有那条事实 = 已结束；没有 = 从来不存在（别的租户的同形状 404）
        stored = agents.events_service.find_by_dedupe(user, agents.SOURCE, agents._dedupe_key(run_id))
        if stored is None:
            raise NotFoundError(f"代理运行不存在：{run_id!r}")
        data = stored.get("data") or {}
    else:
        data, _ended = agents.snapshot_data(run)  # 已标记：按关闭裁剪后的那份，与将落账的事实一致
    return {"runId": run_id, "phase": agents.current_phase(data), "applied": False, "reason": "closed"}


def record_phase(
    run_id: str, phase: str, at_raw: str, detail: str | None, reply: bool, user: str,
    *, now: Callable[[], datetime],
) -> dict:
    right_now = now()
    at = agents.ts(at_raw)  # 入口已校验带偏移
    if at > right_now + CLOCK_SKEW:
        raise UnprocessableError(f"at 超前服务端时钟 300 秒以上：{at_raw!r}")
    agents._expire(user, right_now)  # 写端点：先收超时，超时的运行下面回 closed
    detail = agents.clean(detail)

    for _ in range(_CAS_RETRIES):
        run = repo.get_agent_run(user, run_id)
        if run is None or "closing" in run:
            return _closed_out(user, run_id, run)
        started = agents.ts(run["startedAt"])
        at_str = run["startedAt"] if at < started else at_raw  # 早于起点钳到起点
        at_eff = max(at, started)
        phases = list(run.get("phases") or [])
        interactions = list(run.get("interactions") or [])

        if any(agents.ts(p["at"]) == at_eff and p["phase"] == phase for p in phases):
            reason = "duplicate"
        elif len(phases) >= MAX_PHASES:
            reason = "capped"
        else:
            reason = None
            entry = {"at": at_str, "phase": phase}
            if detail:
                entry["detail"] = detail
            _insert_sorted(phases, entry)
        # reply：重复与已结束不记，其余（含 capped：人确实回话了）都记；同一 at 的 reply 只记一条
        add_reply = (
            reply and reason != "duplicate"
            and sum(i["kind"] == "reply" for i in interactions) < MAX_INTERACTIONS
            and not any(i["kind"] == "reply" and agents.ts(i["at"]) == at_eff for i in interactions)
        )
        if add_reply:
            _insert_sorted(interactions, {"kind": "reply", "at": at_str})
        if (reason is None or add_reply) and not repo.cas_agent_run(
            user, run_id, run.get("v"), {"phases": phases, "interactions": interactions},
        ):
            continue  # 并发写抢先或刚被关闭：重读重算
        repo.touch_agent_run(user, run_id, right_now.isoformat())  # v2.18：收下的相位（含重复 / 超上限）都是信号
        return {"runId": run_id, "phase": agents.current_phase({"phases": phases}),
                "applied": reason is None, "reason": reason}
    raise RuntimeError(f"相位写入争用未决：{run_id!r}")


def heartbeat(run_id: str, user: str, beat_source: str | None = None, *, now: Callable[[], datetime]) -> dict:
    """v2.18「我还活着」：记 ``lastSeenAt``、``beatCount`` 加一，并声明这个运行会发心跳。
    已结束回 closed（同相位），不存在 404。"""
    right_now = now()
    agents._expire(user, right_now)  # 写端点：先收超时 / 失联，迟到的心跳下面回 closed
    alive = repo.touch_agent_run(user, run_id, right_now.isoformat(), declare=True, beat_source=beat_source, beat=True)
    if not alive:
        _closed_out(user, run_id, repo.get_agent_run(user, run_id))  # 从来不存在 → 404
    return {"runId": run_id, "applied": alive, "reason": None if alive else "closed",
            "heartbeatSeconds": AGENT_HEARTBEAT_SECONDS}


def record_attend(user: str, run_id: str, intervals: list[tuple[datetime, datetime]], gap: timedelta) -> None:
    """人在看这条运行的几段时间（按时间排）→ 记 / 延长 attend。哪条运行由 activity 认（v2.17：窗口标题与会话名相等，
    ``activity/session_link.watched``），经 ``timer/service.py`` 调来（跨子边界只走 service）。
    与上一条 attend 的 ``until`` 相距 ≤ ``gap`` 就延长，否则开新的一条；早于运行起点的部分钳到起点。"""
    for _ in range(_CAS_RETRIES):
        run = repo.get_agent_run(user, run_id)
        if run is None or "closing" in run:
            return
        started = agents.ts(run["startedAt"])
        interactions = list(run.get("interactions") or [])
        changed = False
        for start, end in intervals:
            start = max(start, started)
            if end < start:
                continue
            last = next((i for i in reversed(interactions) if i["kind"] == "attend"), None)
            if last is not None and start - agents.ts(last["until"]) <= gap:
                if end <= agents.ts(last["until"]):
                    continue  # 已覆盖到这一刻
                interactions[interactions.index(last)] = {**last, "until": end.isoformat()}
            elif sum(i["kind"] == "attend" for i in interactions) >= MAX_ATTENDS:
                break  # 超了不再记，不报错
            else:
                _insert_sorted(interactions, {"kind": "attend", "at": start.isoformat(), "until": end.isoformat()})
            changed = True
        if not changed or repo.cas_agent_run(user, run_id, run.get("v"), {"interactions": interactions}):
            return


def lane_runs(user: str, *, now: Callable[[], datetime]) -> tuple[datetime, list[dict]]:
    """``views/lanes`` 的在跑运行。**不写**：不收超时（超过上限的标 overdue、elapsed 封顶）、
    不收失联（v2.18：标 lost、elapsed 止于最后一次信号；会发心跳的运行不看上限）。
    「已标记未删除」的运行按标记当已结束画（与它将要落账的那条事实同形）。"""
    right_now = now()
    cap = timedelta(hours=config.settings.agent_run_timeout_hours)
    out = []
    for run in repo.list_agent_runs(user):
        started = agents.ts(run["startedAt"])
        base = {k: run.get(k) for k in ("runId", "agent", "tool", "model", "label", "taskId", "projectId",
                                        "beatSource", "beatCount")}  # 后两个 v2.18
        base["match"] = run.get("match")  # v2.13：activity 的「窗口 ↔ 代理会话」要认它；views/lanes 不回出
        if "closing" in run:
            data, ended = agents.snapshot_data(run)
            out.append({**base, "startTs": started, "endTs": ended, "outcome": data["outcome"],
                        "elapsedSeconds": data["durationSeconds"], "overdue": False,
                        "phases": data.get("phases", []), "interactions": data.get("interactions", [])})
            continue
        seen = lost_at(run, right_now)
        beats = bool(run.get("heartbeat"))
        elapsed = (seen or right_now) - started
        out.append({**base, "startTs": started, "endTs": None, "outcome": None,
                    "elapsedSeconds": max(int((elapsed if beats else min(elapsed, cap)).total_seconds()), 0),
                    "overdue": not beats and elapsed > cap, "lost": seen is not None,
                    "lastSeenTs": agents.ts(run["lastSeenAt"]) if run.get("lastSeenAt") else None,
                    "phases": run.get("phases") or [], "interactions": run.get("interactions") or []})
    return right_now, out
