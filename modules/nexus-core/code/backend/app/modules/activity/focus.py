"""此刻的焦点（契约 v2.16「此刻的焦点」节，唯一事实源）。

仓主 2026-10-09：「蜂巢页和计时页还是没有实时显示人类当前焦点所在窗口或者任务」「要通用，得走 MCP」。
在场心跳一直有，只是没有一个地方把「人此刻在哪个窗口、它多半属于哪个项目 / 任务」说成一句话。
``state`` 就是那一句话：``views/current``、``views/lanes`` 的 ``human``、MCP 的 ``get_current_timer`` 读的都是它，
页面与 AI 客户端**不各自再认一遍项目 / 任务**。

**只是显示**：本文件一行写都没有，不记时间、不改自动记录的任何行为；与 ``autoTrack`` 开关无关（开着时沿用它的目标）。
目标按顺序认，先中先用：自动跟踪的目标 → 窗口 ↔ 代理会话（v2.13）→ 匹配历史（v2.12）。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from ..events import service as events_service
from ..planner import service as planner_service
from ..planner import unclassified
from . import presence, repo, session_link
from .auto import FRESH, window_key
from .service import SOURCE

#: 按以往认：只看这个程序最近确认的这么多条（走 ``user_status_app_decided`` 索引，有界）
# ponytail: 同一个程序下最近 200 条已确认的建议里没有这个窗口就认不出；真不够再给建议存窗口键、按键查
HISTORY_SCAN = 200
#: 同一个窗口的「按以往」多久重查一次。顶栏每个页面约 10 秒读一次 views/current；查一次要读台账里的改挂
#: （``events_service.current_sessions``，随改挂条数增长），所以按（租户, 窗口）记 30 秒——
#: 记的只是台账里的去向，任务 / 项目还在不在每次现查，所以删掉的目标不会多显示 30 秒。
HISTORY_TTL = timedelta(seconds=30)
_HISTORY_CACHE_MAX = 512
_history_cache: dict[tuple[str, str], tuple[datetime, list[tuple]]] = {}

_NO_TARGET = {"projectId": None, "projectName": None, "taskId": None, "taskName": None, "source": None}


def _run_key(span: dict) -> tuple:
    return (True, "") if span["afk"] else (False, window_key(span["app"], span["title"]))


def _since(spans: list[dict]) -> datetime:
    """当前窗口这一轮的起点：往回并掉同一个窗口（同一个 ``window_key``，标题开头的转圈符号在变也算同一个）、
    中间没断过（间隔 ≤ ``MERGE_GAP``）的段。离开同理（一轮离开）。"""
    key, i = _run_key(spans[-1]), len(spans) - 1
    while i > 0 and _run_key(spans[i - 1]) == key and spans[i]["from"] - spans[i - 1]["to"] <= presence.MERGE_GAP:
        i -= 1
    return spans[i]["from"]


def _dwell(spans: list[dict]) -> int:
    """时间线（最近 2 小时）里人在**当前这个窗口**上一共待了多少秒：只数同一个窗口键的段，别的窗口一秒不算。"""
    keys: dict[tuple, tuple] = {}

    def key(span: dict) -> tuple:
        raw = (span["afk"], span["app"], span["title"])
        if raw not in keys:
            keys[raw] = _run_key(span)  # 窗口键要算哈希：同一个（程序, 标题）只算一次
        return keys[raw]

    cur = key(spans[-1])
    return int(sum((s["to"] - s["from"]).total_seconds() for s in spans if key(s) == cur))


def _project_only(project_id: str, source: str) -> dict | None:
    project = planner_service.get_project(project_id)
    return {**_NO_TARGET, "projectId": project["id"], "projectName": project["name"], "source": source} if project else None


def _by_session(user: str, app: str, title: str) -> dict | None:
    """标题（归一化后）等于**在跑的**代理运行的 label / match，且它们指向恰好一个真项目。"""
    key = session_link.norm(title, app)
    projects = {r["projectId"] for r in session_link.live(user) if key in r["keys"]}
    return _project_only(projects.pop(), session_link.SOURCE) if len(projects) == 1 else None


def _history_refs(user: str, app: str, title: str, now: datetime) -> list[tuple]:
    """这个窗口以前被人确认到的去向 ``(taskId, projectId)``，最近定的在前（台账里的当前归属；自动记下的不算）。"""
    key = (user, window_key(app, title))
    hit = _history_cache.get(key)
    if hit and timedelta(0) <= now - hit[0] < HISTORY_TTL:
        return hit[1]
    docs = [d for d in repo.confirmed_for_app(user, app, HISTORY_SCAN) if window_key(d["app"], d["title"]) == key[1]]
    current = events_service.current_sessions(SOURCE, [f"activity:{d['id']}" for d in docs])
    refs = [(s["task"], s["project"]) for d in docs if (s := current.get(f"activity:{d['id']}"))]
    if len(_history_cache) >= _HISTORY_CACHE_MAX:
        _history_cache.clear()
    _history_cache[key] = (now, refs)
    return refs


def _by_history(user: str, app: str, title: str, now: datetime) -> dict | None:
    if not title:  # 标题被隐私设置整个去掉了：只剩程序名，不够认
        return None
    for task_id, project_id in _history_refs(user, app, title, now):
        task = planner_service.get_task(task_id)
        project_id = task["projectId"] if task else project_id
        found = _project_only(project_id, "history") if isinstance(project_id, str) else None
        if found is None:
            continue  # 项目已删：看更早的一次
        if task and not unclassified.is_bucket(task) and not task.get("done"):
            found.update(taskId=task["id"], taskName=task["name"])
        return found
    return None


def state(user: str, now: datetime, docs: list[dict], auto: dict | None) -> dict | None:
    """``focus``：最新心跳新鲜（≤ ``FRESH``）时非 null。``docs`` = 该租户的在场文档，``auto`` = 同一次读里算出的自动跟踪目标。"""
    fresh = [d for d in docs if now - d["lastAt"] <= FRESH and d.get("spans")]
    if not fresh:
        return None
    spans = max(fresh, key=lambda d: d["lastAt"])["spans"]  # 只看最近报心跳的那台设备（同自动跟踪）
    cur = spans[-1]
    out = {"state": "afk" if cur["afk"] else "present", "app": cur["app"], "title": cur["title"],
           "since": _since(spans), "dwellSeconds": None if cur["afk"] else _dwell(spans), **_NO_TARGET}
    if cur["afk"]:
        return out
    if auto:
        target = {k: auto[k] for k in _NO_TARGET}
    else:
        target = _by_session(user, cur["app"], cur["title"]) or _by_history(user, cur["app"], cur["title"], now)
    return {**out, **(target or {})}
