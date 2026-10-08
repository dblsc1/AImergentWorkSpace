"""自动跟踪进行中的任务（契约 v2.14「自动跟踪进行中的任务」节，唯一事实源）。

仓主 2026-10-08：「'我'的操作自动替代进行中计时」「保留开关：允许 / 不允许 AI 管理进行中的任务。人手动开始计时时，
不要 AI 计时。人在操作但没有计时：先试死规则；认不出，让 AI 写规则；写不出、也没有现成任务对得上，提醒人选项目和任务；
到这段活动结束人还没选，走碎片流程一起归类。」
本文件是**第一步**：除了「让 AI 写规则」之外的全部。下一步由 ``next_step`` 一个函数定——第二步把 ``"ask_ai"``
插在「规则没认出」与「请人选」之间，别处不用动。全部挂在设备的 ``autoTrack`` 开关下（detector.settings.v1 v1.3，缺省关）。

- ``state``：``views/lanes`` 的 ``human.auto`` / ``human.needsChoice``（``views/current`` 顶层同一份）。**不写**。
- ``heartbeat`` / ``upload``：心跳、上传的入口（路由调这里）——先走原来的路径，再做本版追加的那一步。
- ``choose`` / ``dismiss``：人答「记到哪」/「这次不选」。``recorded_today``：今天自动记下的段。
规则只在检测程序里匹配（服务端没有原始标题）：这里看到的「规则认得出」就是心跳带来的 ``guess``。
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, time, timedelta, timezone
from typing import Any, Literal

from ... import config
from ...tenant import current as current_tenant
from ..detector import rules as detector_rules
from ..detector import service as detector_service
from ..events import service as events_service
from ..planner import service as planner_service
from ..planner import unclassified
from ..planner.errors import InvalidInputError, NotFoundError
from ..projector.handlers import lanes as lanes_projection
from ..timer import service as timer_service
from . import presence, repo, service
from .history import norm_title

#: 最新一次心跳距今不超过它才算「人在电脑前」（页面画「在电脑前」同一个口径）
FRESH = timedelta(seconds=90)
#: 规则的把握 ≥ 它才直接记（= 「以后这个窗口都记到…」写出的规则的把握）
AUTO_CONFIDENCE = 0.9
#: 规则认不出的窗口，在最近 LOOKBACK 里累计在前台 DWELL 才请人选（来回切标签页不清零）
DWELL = timedelta(seconds=60)
LOOKBACK = timedelta(minutes=5)
#: 人的临时选择：那个窗口离开这么久后失效（每次心跳续期）
CHOICE_AWAY = timedelta(minutes=30)
#: 「这次不选」：这么久不再提醒
DISMISS = timedelta(hours=4)

KEY_PATTERN = r"^wk_[0-9a-f]{20}$"
_MAX_AUTO = 200
# ponytail: 一批上传的时间窗里最多看最新 1000 条人的段找手动计时；补传几天积压时更早的手动段看不到，真遇到再分页
_MAX_SESSIONS = 1000
_PSEUDONYM = re.compile(r"^(窗口名|路径)\d+$")  # ai-detector privacy.go 的 token()
_RE_SPECIAL = re.compile(r"[.*+?^${}()|\[\]\\]")  # 同 assistant suggestions.js 的 reEscape
#: 标题开头的状态符号 / 计数（norm_title 去掉的那些）：规则里放过它们，转圈符号在变的标签页才对得上
_DECOR = r"^(?:[^\pL\pN]|\(\d+\)|\[\d+\])*"
_FOREVER = datetime.max.replace(tzinfo=timezone.utc)

log = logging.getLogger("uvicorn.error")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def window_key(app: str, title: str) -> str:
    """窗口的键：程序 + 归一化标题（与「匹配历史」同一个 norm_title），不分大小写。"""
    raw = f"{app.lower()}\n{norm_title(title).lower()}"
    return "wk_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def next_step(window: dict) -> Literal["rules", "ask_ai", "ask_human"]:
    """这个窗口下一步找谁——**全系统只有这一处决定**。

    ``"rules"``：已经有目标（规则的猜测，或人对这个窗口的临时选择）；``"ask_human"``：请人选。
    ``"ask_ai"``（让自带的 AI 给这个窗口写一条规则）留给第二步：插在下面两行之间，本版永不返回。
    """
    if window["target"]:
        return "rules"
    return "ask_human"  # 第二步：先看能不能 "ask_ai"


def _lookup(task_id: Any, project_id: Any) -> dict | None:
    """目标此刻还在吗：在 → ``{taskId, projectId, taskName, projectName}``（桶 = 只到项目），不在 → None。"""
    task = planner_service.get_task(task_id) if isinstance(task_id, str) else None
    project_id = task["projectId"] if task else project_id
    project = planner_service.get_project(project_id) if isinstance(project_id, str) else None
    if project is None or (task_id is not None and task is None):
        return None
    named = task is not None and not unclassified.is_bucket(task)
    return {"taskId": task["id"] if named else None, "projectId": project["id"],
            "taskName": task["name"] if named else None, "projectName": project["name"]}


class _Windows:
    """一次读里把在场的段变成窗口：键、目标（规则的猜测优先，其次人的临时选择）、有没有「这次不选」。带缓存。"""

    def __init__(self, choices: dict[str, dict]):
        self._choices = choices
        self._targets: dict[tuple, dict | None] = {}

    def _target(self, ref: dict, source: str) -> dict | None:
        ids = (ref.get("taskId"), ref.get("projectId"))
        if ids not in self._targets:
            self._targets[ids] = _lookup(*ids)
        return {**self._targets[ids], "source": source} if self._targets[ids] else None

    def __call__(self, span: dict) -> dict:
        key = window_key(span["app"], span["title"])
        choice = self._choices.get(key) or {}
        target = self._target(span["guess"], "rules") if span.get("guess") else None
        if target is None and choice.get("kind") == "choice":
            target = self._target(choice, "choice")
        return {"key": key, "app": span["app"], "title": span["title"], "target": target,
                "dismissed": choice.get("kind") == "dismiss"}


def _same(a: dict | None, b: dict) -> bool:
    return a is not None and (a["taskId"], a["projectId"]) == (b["taskId"], b["projectId"])


def state(user: str, now: datetime, timer_running: bool) -> dict:
    """``{auto, needsChoice}``（契约同名两小节；``views/lanes`` 与 ``views/current`` 同一份）。"""
    out: dict = {"auto": None, "needsChoice": None}
    docs = [] if timer_running else [d for d in repo.presence_list(user) if now - d["lastAt"] <= FRESH]
    if not docs:
        return out
    doc = max(docs, key=lambda d: d["lastAt"])  # 只看最近报心跳的那台设备
    spans = doc.get("spans") or []
    if doc["afk"] or not spans or not detector_service.device_flags(user, doc["deviceId"])["autoTrack"]:
        return out
    window = _Windows(repo.choices(user, now))

    current = window(spans[-1])
    if next_step(current) == "rules":
        target, i = current["target"], len(spans) - 1
        # ponytail: 中途切到别的窗口再切回来就重新起算；要抗抖动时改成容忍短暂的别的目标
        while (i > 0 and not spans[i - 1]["afk"] and spans[i]["from"] - spans[i - 1]["to"] <= presence.MERGE_GAP
               and _same(window(spans[i - 1])["target"], target)):
            i -= 1
        # since 不早于这段时间里最近一条手动计时段的结束（刚停表：不把表测过的那段算进来）
        ends = [stop for _start, stop in _manual_spans(user, spans[i]["from"], now) if stop <= now]
        out["auto"] = {**target, "since": max([spans[i]["from"], *ends]), "app": current["app"],
                       "title": current["title"]}

    horizon = now - LOOKBACK
    asked: dict[str, dict] = {}  # 从新往旧走：先碰到的是这个窗口最近的一段
    for i in range(len(spans) - 1, -1, -1):
        span = spans[i]
        end = min(span["to"] + presence.MERGE_GAP, spans[i + 1]["from"] if i + 1 < len(spans) else now)
        if end <= horizon:
            break
        if span["afk"] or not span["app"]:
            continue
        w = window(span)
        if w["dismissed"] or next_step(w) != "ask_human":
            continue
        start = max(span["from"], horizon)
        a = asked.setdefault(w["key"], {"seconds": 0.0, "last": i, "window": w})
        a["seconds"] += (end - start).total_seconds()
        a["since"] = start
    waiting = [a for a in asked.values() if a["seconds"] >= DWELL.total_seconds()]
    if waiting:
        a = max(waiting, key=lambda a: (a["seconds"], a["last"]))
        out["needsChoice"] = {"key": a["window"]["key"], "app": a["window"]["app"], "title": a["window"]["title"],
                              "since": a["since"]}
    return out


# ------------------------------------------------ 心跳：续期人的临时选择


def heartbeat(device_id: str, app: str, title: str, afk: bool, guess: dict | None) -> dict:
    out = presence.heartbeat(device_id, app, title, afk, guess)
    if not afk:
        now = _now()
        repo.choice_seen(current_tenant(), window_key(*presence.clip(app, title)), now, now + CHOICE_AWAY)
    return out


# ------------------------------------------------ 人答 / 这次不选


def _find(user: str, key: str) -> tuple[dict, str]:
    """在场记录（最近 2 小时）里这个窗口最近的一段与报它的设备；没有 → 404。写规则用的程序名 / 标题从这里取，不信请求。"""
    best = None
    for doc in repo.presence_list(user):
        for span in reversed(doc.get("spans") or []):
            if not span["afk"] and window_key(span["app"], span["title"]) == key:
                if best is None or span["to"] > best[0]["to"]:
                    best = (span, doc["deviceId"])
                break
    if best is None:
        raise NotFoundError(f"没有这个窗口：{key!r}（最近 2 小时的在场记录里没有它）")
    return best


def _escape(text: str) -> str:
    return _RE_SPECIAL.sub(lambda m: "\\" + m.group(), text)


def _window_rule(app: str, title: str, target: dict) -> dict | None:
    """只认这个窗口的规则（与「待确认建议」页勾「以后这个窗口都记到…」写的同形）；正则放不下 → None。
    标题开头带状态符号 / 计数时不钉死它们（``_DECOR``）：转圈符号每秒在变，钉死了规则时中时不中。"""
    core = norm_title(title)
    pattern = _DECOR + _escape(core) + "$" if core != title and title.endswith(core) else "^" + _escape(title) + "$"
    rule = {"app": "^" + _escape(app) + "$", "title": pattern, **target, "confidence": AUTO_CONFIDENCE,
            "note": ("计时页选的：" + app + (" · " + title if title else ""))[:120], "enabled": True}
    return rule if len(rule["app"]) <= 200 and len(pattern) <= 200 else None


def choose(key: str, task_id: str | None, project_id: str | None, remember: bool) -> dict:
    if (task_id is None) == (project_id is None):
        raise InvalidInputError("taskId 与 projectId 必须给一个、且只能给一个")
    user, now = current_tenant(), _now()
    span, device = _find(user, key)
    found = _lookup(task_id, project_id)
    if found is None:
        raise NotFoundError(f"任务不存在：{task_id!r}" if task_id else f"项目不存在：{project_id!r}")
    target = {"taskId": task_id} if task_id else {"projectId": project_id}
    repo.choice_put({"user": user, "key": key, "kind": "choice", "app": span["app"], "title": span["title"],
                     **target, "at": now, "expiresAt": now + CHOICE_AWAY})
    # 规则认的是换代号之前的标题：标题是代号（或这台设备设成了换代号）就写不出能中的规则
    pseudonymized = remember and bool(span["title"]) and (
        _PSEUDONYM.fullmatch(span["title"]) is not None
        or detector_service.device_flags(user, device)["pseudonymize"])
    remembered = False
    if remember and not pseudonymized:
        rule = _window_rule(span["app"], span["title"], target)
        remembered = rule is not None and detector_rules.prepend(rule)
    return {"key": key, "taskId": found["taskId"], "projectId": found["projectId"],
            "remembered": remembered, "pseudonymized": pseudonymized}


def dismiss(key: str) -> dict:
    user, now = current_tenant(), _now()
    span, _device = _find(user, key)
    until = now + DISMISS
    repo.choice_put({"user": user, "key": key, "kind": "dismiss", "app": span["app"], "title": span["title"],
                     "at": now, "expiresAt": until})
    return {"key": key, "dismissedUntil": until.isoformat()}


# ------------------------------------------------ 自动记录：规则高把握命中的段直接记成事实


def _manual_spans(user: str, start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
    """与 [start, end) 有重叠的手动计时：已落账的段（读 ``proj_lanes``，同 views/lanes）+ 此刻在跑的。"""
    out = [(r["startAt"], r["endAt"]) for r in lanes_projection.read_lanes(user, "session", start, end, _MAX_SESSIONS)
           if r.get("source") == timer_service.SOURCE]
    running = timer_service.get_running_state(user)
    if running:
        out.append((datetime.fromisoformat(running["startAt"]), _FOREVER))
    return out


def record(device_id: str, docs: list[dict], request: Any) -> None:
    """对新写入的建议做自动记录（契约「自动记录」五个条件）。记不成的留在待确认，**从不抛**——
    检测程序只在 2xx 后推进游标，这一步不能让上传失败。"""
    user = current_tenant()
    eligible = []
    for d in docs:
        sug = d["suggestion"]
        to_project = sug.get("projectId") if "projectSource" not in sug else None
        if (not d["idle"] and sug["classifier"] == "rules" and sug["confidence"] >= AUTO_CONFIDENCE
                and (sug["taskId"] or to_project)):
            eligible.append((d, datetime.fromisoformat(d["endAt"]), None if sug["taskId"] else to_project))
    if not eligible or not detector_service.device_flags(user, device_id)["autoTrack"]:
        return
    manual = _manual_spans(user, min(d["startTs"] for d, _, _ in eligible), max(end for _, end, _ in eligible))
    for d, end, project_id in eligible:
        if any(start < end and stop > d["startTs"] for start, stop in manual):
            continue  # 人自己掐着表的那段时间，AI 不插手
        try:
            # task_id 不给 = 用建议里的任务（占位时要求它没变）；只到项目 = 记到它的「未分类」
            service.confirm(d["id"], None, "do", request=request, project_id=project_id, auto=True)
        except Exception as exc:  # noqa: BLE001 —— 任务刚被删、桶的 id 被占……：留在待确认
            log.warning("自动记录没记成，留在待确认：%s（%s: %s）", d["id"], type(exc).__name__, exc)


def upload(device_id: str, segments: list[Any], request: Any) -> dict:
    fresh: list[dict] = []
    out = service.upload(device_id, segments, fresh)
    record(device_id, fresh, request)
    return out


def recorded_today() -> dict:
    """今天（NEXUS_TZ，按 startAt）自动记下的段，新的在前；归属取台账里的当前归属。只读。"""
    user = current_tenant()
    tz = config.settings.tz
    start = datetime.combine(_now().astimezone(tz).date(), time(), tz)
    docs = repo.auto_between(user, start, start + timedelta(days=1), _MAX_AUTO)
    current = events_service.current_sessions(service.SOURCE, [f"activity:{d['id']}" for d in docs])
    items = []
    for d in docs:
        hit = current.get(f"activity:{d['id']}")  # 占了位、事实还没写成的没有
        if hit:
            items.append({**{k: d[k] for k in ("id", "startAt", "endAt", "durationSeconds", "app", "title")},
                          "eventId": hit["id"], "taskId": hit["task"], "projectId": hit["project"],
                          "reassigned": hit["moved"]})
    return {"items": items}
