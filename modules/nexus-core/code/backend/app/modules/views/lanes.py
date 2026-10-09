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
from ..activity import service as activity_service
from ..planner.errors import UnprocessableError
from ..prefs import service as prefs_service
from ..projector.handlers import lanes as lanes_projection
from ..timer import service as timer_service
from .lane_active import split
from .lane_cap import MAX_LIVE, cap, lane_key
from .lane_order import arrange
from .queries import _today
from .schemas import LanesOut

MAX_SESSIONS = 1000
MAX_CLOSED_READ = 5000  # 已结束的运行一次最多读这么多条「轻」记录（只有封顶用得到的几列），再在内存里按身份封顶
_LIGHT = ("runId", "startAt", "endAt", "durationSeconds", "agent", "label", "unverified")
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


def _pipeline(light: list[dict], live: list[dict], prefs: dict, start: datetime, end: datetime, now: datetime,
              today: bool) -> dict:
    """泳道管线（唯一一份）：藏起来的在跑运行瘦身 → 封顶 → 只显示在干活的（``today``）→ 排序。
    ``get_lanes`` 与 ``live_order`` 共用，所以服务端数的「第几条」与页面上显示的一一对应。"""
    hidden_keys = {a["key"] for a in prefs["agents"] if a["hidden"]}  # 藏起来的身份：折叠摘要也不露
    # 藏起来的身份永远不显示：它们已结束的运行不占封顶的名额，也不算「丢了东西」；在跑的照样进 arrange（hiddenAgents / hiddenWaiting）
    light = [r for r in light if lane_key(r) not in hidden_keys]
    # 藏起来的在跑运行也要有界：只留开始最晚的 MAX_LIVE 条，其余静默丢（藏起来的，不进 dropped / truncated）；
    # 它们只为 hiddenAgents / hiddenWaiting 服务，所以不走 _run_item，只带 arrange 读的那几列；也不进 split（藏起来的本来就被它跳过）
    hidden_live = sorted((r for r in live if lane_key(r) in hidden_keys), key=lambda r: r["startTs"], reverse=True)[:MAX_LIVE]
    hidden_items = [{"runId": r["runId"], "agent": r.get("agent"), "label": r.get("label"),
                     "unverified": bool(r.get("unverified")), "lost": bool(r.get("lost")),
                     "endAt": _iso(r["endTs"]) if r["endTs"] is not None else None,
                     "phases": [{"at": p["at"], "phase": p["phase"]} for p in r["phases"]]} for r in hidden_live]
    kept, gone = cap(light + [r for r in live if lane_key(r) not in hidden_keys], start, end, now)
    kept_ids = {r["runId"] for r in kept}
    # 被封顶折进 dropped 的在跑运行，其身份仍然「在跑」（stalePinned 要看到）
    gone_live = {lane_key(r) for r in live if r["runId"] not in kept_ids and lane_key(r) not in hidden_keys
                 and r["endTs"] is None}
    return {"kept": kept, "gone": gone, "hidden_keys": hidden_keys, "hidden_items": hidden_items,
            "gone_live": gone_live, "start": start, "end": end, "now": now, "prefs": prefs, "today": today}


def _finish(p: dict, runs: list[dict]) -> tuple:
    """``runs`` = 保留下来的全文运行 → (agents, hiddenAgents, hiddenWaiting, stalePinned, inactive, dropped)。"""
    prefs, now, gone = p["prefs"], p["now"], p["gone"]
    items = [_run_item(r, p["start"], p["end"]) for r in runs]
    for g in gone:  # 封顶丢掉的在跑运行也要让「是否在干活」看见（不进 agents，只参与判定）
        g["live"] = [_run_item(r, p["start"], p["end"]) for r in g.pop("openRuns")]
    inactive: list[dict] = []
    if p["today"]:  # v2.24：只在含「现在」的窗口里按当前活动过滤；过去的日子整天原样
        items, inactive, gone = split(items, gone, prefs, now)
    agents, hidden, waiting, stale = arrange(items + p["hidden_items"], prefs, now, p["gone_live"])
    dropped = [{k: v for k, v in d.items() if k not in ("key", "live")} for d in gone if d["key"] not in p["hidden_keys"]]
    return agents, hidden, waiting, stale, inactive, dropped


def _gather(user: str, start: datetime, end: datetime, now: datetime, open_runs: list[dict], prefs: dict) -> tuple:
    """读窗口里已结束的运行（先轻读、封顶、再取保留下来的全文）并与在跑的并起来过 ``_pipeline``。
    ``get_lanes`` 与 ``live_order`` 共用这一份输入构造 → (管线状态 p, 保留的全文运行 runs, 轻读是否超限)。"""
    light = lanes_projection.read_lanes(user, "run", start, end, MAX_CLOSED_READ + 1, fields=_LIGHT)
    over = len(light) > MAX_CLOSED_READ  # 超出的最旧部分连聚合都没有：只能如实标 truncated
    light = [{**r, "startTs": r["startAt"], "endTs": r["endAt"]} for r in light[:MAX_CLOSED_READ]]
    seen = {r["runId"] for r in light}
    live = [{**r, "open": True} for r in open_runs if r["runId"] not in seen   # 刚落账、活状态还没删的那一刻两边都有：以事实为准
            and r["startTs"] < end and (r["endTs"] or now) > start]
    p = _pipeline(light, live, prefs, start, end, now, end > now)
    kept = p["kept"]
    full = {r["runId"]: r for r in lanes_projection.read_lanes(
        user, "run", start, end, len(kept), run_ids=[r["runId"] for r in kept if not r.get("open")])}
    runs = [r if r.get("open") else
            {**full[r["runId"]], "startTs": r["startTs"], "endTs": r["endTs"],
             "elapsedSeconds": full[r["runId"]]["durationSeconds"], "overdue": False}
            for r in kept if r.get("open") or r["runId"] in full]  # 轻读到全文读之间被清走的已结束运行：静默跳过（竞态，无害）
    return p, runs, over


def live_order(user: str, prefs: dict) -> list[str]:
    """页面上**显示着**的、未置顶、已验证的在跑运行当前的先后（runId）——手动排位的底，拖拽的下标就按它数。
    与 ``get_lanes`` 同一条管线（同一份输入：含已结束的兄弟运行；封顶 → 只显示在干活的 → 排序），默认（今天）窗口；
    被折进 ``inactiveAgents`` 的泳道不占位。"""
    first, last = _window(None, None, None)
    start = datetime.combine(first, time(), settings.tz)
    end = datetime.combine(last + timedelta(days=1), time(), settings.tz)
    now, open_runs = timer_service.list_lane_runs(user)
    p, runs, _ = _gather(user, start, end, now, open_runs, prefs)
    agents = _finish(p, runs)[0]
    opened = {r["runId"] for r in runs if r.get("open")}
    return [a["runId"] for a in sorted(agents, key=lambda a: a["rank"])
            if a["runId"] in opened and not a["pinned"] and not a["unverified"] and a["endAt"] is None]


def get_lanes(day: str | None = None, date_from: str | None = None, date_to: str | None = None) -> LanesOut:
    user = current_tenant()
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
    inactive: list[dict] = []
    hidden_waiting = 0
    stale_pinned: list[dict] = []
    dropped: list[dict] = []
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

        # v2.23：不按全局最新截——先轻读窗口里的已结束运行，按代理身份封顶（lane_cap），再只取保留下来的全文
        prefs = prefs_service.load(user)
        p, runs, over = _gather(user, start, end, now, open_runs, prefs)
        truncated = truncated or over or bool(p["gone"])
        # v2.22：藏起来的代理不出现（时间照旧记在账上）；其余加 pinned / manualOrder / rank
        agents, hidden, hidden_waiting, stale_pinned, inactive, dropped = _finish(p, runs)
        if caller().scope == "report":  # 藏起来的摘要只给能读泳道的调用方（report / 匿名本来就读不到，这里再保一道）
            hidden, hidden_waiting, stale_pinned, dropped = [], 0, [], []
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
        inactiveAgents=inactive, hiddenAgents=hidden, hiddenWaiting=hidden_waiting, stalePinned=stale_pinned, dropped=dropped,
    )
