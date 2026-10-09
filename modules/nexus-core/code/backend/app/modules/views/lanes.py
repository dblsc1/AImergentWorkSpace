"""``GET /api/core/views/lanes``（契约 v2.4「人一条线、代理多条线的时间线」，唯一事实源）。

人一条线（计时段 + 在计时 + 最近在场）、代理多条线（运行 + 相位）、两者之间的连线，一次给全。
口径（规范性）：

- **与窗口有重叠就列出，不归日、不裁剪**：跨零点的段由画图的人自己裁。响应里没有任何合计字段。
- **不写**：不收超时运行（「读时写」例外止于 agent-time），超过上限的标 ``overdue``；失联的标 ``lost``（v2.18）。
- 数据来源：已结束的读 ``proj_lanes``；在跑的运行与人的计时经 timer service；在场经 activity。
  **不读 events**（「内部子边界」红线）。
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from ...config import settings
from ...scope import caller
from ...tenant import current as current_tenant
from ..activity import ignore
from ..activity import service as activity_service
from ..planner.errors import UnprocessableError
from ..prefs import service as prefs_service
from ..projector.handlers import lanes as lanes_projection
from ..timer import service as timer_service
from .lane_order import arrange
from .queries import _today
from .schemas import LanesOut

MAX_SESSIONS = 1000
MAX_AGENTS = 200
MAX_SPAN_DAYS = 7
MAX_ATTENTION = 500  #: 每条运行至多回出这么多段「人在看」（取最新的）


def _iso(moment: datetime) -> str:
    return moment.astimezone(settings.tz).isoformat()


def _day(raw: str) -> date:
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise UnprocessableError(f"日期不合法：{raw!r}") from exc


def _window(day: str | None, date_from: str | None, date_to: str | None) -> tuple[date, date]:
    if day and (date_from or date_to):
        raise UnprocessableError("date 与 from/to 互斥，只能给一种")
    if day:
        return _day(day), _day(day)
    if date_from or date_to:  # 只给一头 = 就那一天
        return _day(date_from or date_to), _day(date_to or date_from)
    today = _day(_today())
    return today, today


def _attention(run: dict, start: datetime, end: datetime) -> list[dict]:
    """v2.17：人把注意力放在这条运行上的时间（运行上的 attend），裁到窗口、首尾相接 / 重叠的并掉。"""
    out: list[list[datetime]] = []
    for item in run.get("interactions") or []:  # 存的时候已按 at 排好
        if item.get("kind") != "attend":
            continue
        a, b = max(datetime.fromisoformat(item["at"]), start), min(datetime.fromisoformat(item["until"]), end)
        if b < a:
            continue
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [{"from": _iso(a), "to": _iso(b)} for a, b in out[-MAX_ATTENTION:]]


def _interactions(run: dict) -> list[dict]:
    """连线原样回出；attend 每条运行只回最新的 MAX_ATTENTION 条（v2.17 起一条运行能存 2000 条，响应不跟着涨）。"""
    items = run.get("interactions") or []
    attends = [i for i in items if i.get("kind") == "attend"]
    return [i for i in items if i.get("kind") != "attend"] + attends[-MAX_ATTENTION:]


def _run_item(run: dict, start: datetime, end: datetime) -> dict:
    return {
        **{k: run.get(k) for k in ("runId", "agent", "tool", "model", "label", "taskId", "projectId",
                                   "outcome", "elapsedSeconds", "overdue",
                                   "beatSource", "beatCount")},  # 后两个 v2.18：谁发的心跳、发了几下
        "unverified": bool(run.get("unverified")),  # v2.19：不带凭据的请求开的运行
        "startAt": _iso(run["startTs"]),
        "endAt": _iso(run["endTs"]) if run["endTs"] is not None else None,
        # v2.18：失联与最后一次信号的时刻只对在跑的运行有意义；已结束的 false / null
        "lost": bool(run.get("lost")),
        "lastSeenAt": _iso(run["lastSeenTs"]) if run.get("lastSeenTs") else None,
        "phases": [{"at": p["at"], "phase": p["phase"], "detail": p.get("detail")} for p in run["phases"]],
        "attention": _attention(run, start, end),
    }


def live_order(user: str, prefs: dict) -> list[str]:
    """未置顶、已验证、没藏起来、在跑的运行当前的先后（runId）——手动排位的底。与 ``get_lanes`` 同一个 ``arrange``。"""
    now, open_runs = timer_service.list_lane_runs(user)
    items = [_run_item(r, now, now) for r in open_runs if r["endTs"] is None]
    shown = arrange(items, prefs, now)[0]
    return [a["runId"] for a in sorted(shown, key=lambda a: a["rank"]) if not a["pinned"] and not a["unverified"]]


def get_lanes(day: str | None = None, date_from: str | None = None, date_to: str | None = None) -> LanesOut:
    user = current_tenant()
    ignore.ensure_purged(user)  # v2.22：忽略规则清理没做完就先补清（human.presence 来自在场记录）
    first, last = _window(day, date_from, date_to)
    if (last - first).days >= MAX_SPAN_DAYS:
        raise UnprocessableError(f"跨度超过 {MAX_SPAN_DAYS} 天：{first}…{last}")
    start = datetime.combine(first, time(), settings.tz)
    end = datetime.combine(last + timedelta(days=1), time(), settings.tz)
    now, open_runs = timer_service.list_lane_runs(user)
    empty = first > last  # from > to：空结果，不报错

    sessions: list[dict] = []
    agents: list[dict] = []
    hidden: list[dict] = []
    hidden_waiting = 0
    stale_pinned: list[dict] = []
    presence: list[dict] = []
    running = None
    auto = {"auto": None, "needsChoice": None, "aiThinking": None}
    truncated = False
    if not empty:
        rows = lanes_projection.read_lanes(user, "session", start, end, MAX_SESSIONS + 1)
        truncated = len(rows) > MAX_SESSIONS
        sessions = [
            {"startAt": _iso(r["startAt"]), "endAt": _iso(r["endAt"]), "durationSeconds": r["durationSeconds"],
             "taskId": r.get("taskId"), "projectId": r.get("projectId"), "mode": r.get("mode") or "do",
             "source": r.get("source")}
            for r in reversed(rows[:MAX_SESSIONS])
        ]

        closed = [
            {**r, "startTs": r["startAt"], "endTs": r["endAt"], "elapsedSeconds": r["durationSeconds"],
             "overdue": False}
            for r in lanes_projection.read_lanes(user, "run", start, end, MAX_AGENTS + 1)
        ]
        seen = {r["runId"] for r in closed}
        # 刚落账、活状态还没删的那一刻两边都有：以事实为准
        live = [r for r in open_runs if r["runId"] not in seen
                and r["startTs"] < end and (r["endTs"] or now) > start]
        runs = sorted(closed + live, key=lambda r: r["startTs"], reverse=True)
        truncated = truncated or len(runs) > MAX_AGENTS
        runs = runs[:MAX_AGENTS][::-1]
        # v2.22：藏起来的代理不出现（时间照旧记在账上）；其余加 pinned / manualOrder / rank
        agents, hidden, hidden_waiting, stale_pinned = arrange([_run_item(r, start, end) for r in runs],
                                                 prefs_service.load(user), now)
        if caller().scope == "report":  # 藏起来的摘要只给能读泳道的调用方（report / 匿名本来就读不到，这里再保一道）
            hidden, hidden_waiting, stale_pinned = [], 0, []
        shown = {a["runId"] for a in agents}
        interactions = [
            {"runId": r["runId"], **i} for r in runs if r["runId"] in shown for i in _interactions(r)
        ]

        state = timer_service.get_running_state(user)
        if state is not None:
            running = {"startAt": state["startAt"], "taskId": state.get("taskId"),
                       "projectId": state.get("projectId")}
        presence = [
            {**s, "from": _iso(s["from"]), "to": _iso(s["to"])}
            for s in activity_service.list_presence(user, now, start, end)
        ]
        # v2.14：没有手动计时时，「我」当前窗口对上的项目 / 任务，与请人选的窗口；v2.16：加 focus（在计时也有）
        auto = activity_service.auto_state(user, state is not None, now)
    else:
        interactions = []

    interactions.sort(key=lambda i: datetime.fromisoformat(i["at"]))
    return LanesOut(
        today=_today(), now=_iso(now), windowStart=_iso(start), windowEnd=_iso(end),
        human={"sessions": sessions, "running": running, "presence": presence, **auto},
        agents=agents, interactions=interactions, truncated=truncated,
        hiddenAgents=hidden, hiddenWaiting=hidden_waiting, stalePinned=stale_pinned,
    )
