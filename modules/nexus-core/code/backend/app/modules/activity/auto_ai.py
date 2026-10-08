"""让 AI 认窗口（契约 v2.15「让 AI 认窗口」节，唯一事实源）——自动跟踪的第二步，插在 ``auto.next_step`` 的 ``"ask_ai"`` 上。

仓主 2026-10-08：「认不出，让 AI 出来写规则；写不出、也没有现成任务对得上，提醒人选」「cockpit 能不能主动调 opencode 做匹配」。
nexus-core 够不着聊天后端（gateway.v1 第八节），所以是**对方来取**：聊天后端的后台工人经 MCP 定时调 ``claim``，
有窗口在等就跑一轮模型、经 MCP 调 ``suggest``。状态全在这里，读端（``Asks``）**不写**：

- 没有问询 + 最近 ``AI_CLAIM_GRACE`` 里有人来认领过（= 这条路活着）+ 这一小时没问满 + 写得出规则 → ``ask_ai``（等认领）；
- 认领了、没回答、没超过 ``AI_ANSWER_WAIT`` → ``ask_ai``；
- 其余（没人来认领、超时、AI 说认不出、``AI_RETRY`` 内问过）→ ``ask_human``，就是第一步那张卡。
没装 / 没配聊天后端 = 从来没人认领 = 与 v2.14 完全相同，一秒也不多等。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from ...tenant import current as current_tenant
from ..detector import service as detector_service
from ..detector import window_rules
from ..planner import service as planner_service
from ..planner.errors import InvalidInputError, NotFoundError
from ..timer import service as timer_service
from . import ask_repo, auto, repo
from .service import ConflictError

#: 最近这么久里有人来认领过，才算「AI 这条路活着」（聊天后端约 25 秒来一次）
AI_CLAIM_GRACE = timedelta(seconds=60)
AI_ANSWER_WAIT = timedelta(seconds=120)  #: 认领后这么久没回答 → 请人选
AI_RETRY = timedelta(hours=6)  #: 同一个窗口问过一次后，这么久不再问
AI_MAX_PER_HOUR = 12  #: 每租户每小时至多问这么多次
AI_KEEP = timedelta(days=30)  #: 问询留这么久（「AI 认的」与「不对」靠它认）
#: AI 自报的把握 ≥ 它，规则才给到自动记录的门槛；低于它按自报的存（规则照样指路，但不直接记成时间）
AI_TRUST = 0.8


def _ids(ref: dict) -> tuple:
    return ref.get("taskId"), ref.get("projectId")


def _rule_id(key: str) -> str:
    return "r_ai_" + key[3:]  # 一个窗口至多一条 AI 写的规则，id 由窗口的键定


def _writable(user: str, device: str, app: str, title: str) -> bool:
    """给这个窗口写得出规则吗（标题是代号 / 这台设备换代号上传 / 正则放不下 → 写不出，不必问 AI）。"""
    if auto._PSEUDONYM.fullmatch(title) or auto._window_rule(app, title, {"projectId": "p"}) is None:  # noqa: SLF001
        return False
    return not detector_service.device_flags(user, device)["pseudonymize"]


class Asks:
    """一次读里各个窗口与 AI 的关系（带缓存）。**不写**。"""

    def __init__(self, user: str, now: datetime):
        self.user, self.now, self.device = user, now, ""
        self._recs: dict[str, dict | None] = {}
        self._alive: bool | None = None

    def on(self, device: str) -> Asks:
        self.device = device
        return self

    def _rec(self, key: str) -> dict | None:
        if key not in self._recs:
            self._recs[key] = ask_repo.get(self.user, key)
        return self._recs[key]

    def guess(self, key: str, span: dict) -> dict | None:
        """心跳带的规则猜测。人说过「不对」的那个 AI 目标不算——检测程序拉到新规则之前还会带着它。"""
        g = span.get("guess")
        rec = self._rec(key) if g else None
        return None if rec and rec.get("outcome") == "rejected" and _ids(rec) == _ids(g) else g

    def source(self, key: str, ref: dict, source: str) -> str:
        """这个目标是 AI 认的吗（问询里记着同一个目标）→ ``"ai"``；否则原样。"""
        rec = self._rec(key)
        return "ai" if rec and rec.get("outcome") == "suggested" and _ids(rec) == _ids(ref) else source

    def wants(self, key: str, span: dict) -> bool:
        """这个没有目标的窗口此刻归 AI 认吗（``next_step`` 的 ``"ask_ai"``）。"""
        rec = self._rec(key)
        if rec and self.now - rec["claimedAt"] < AI_RETRY:
            return rec.get("answeredAt") is None and self.now - rec["claimedAt"] <= AI_ANSWER_WAIT
        if self._alive is None:
            t = ask_repo.tenant(self.user)
            recent = [c for c in t.get("claims") or [] if self.now - c < timedelta(hours=1)]
            self._alive = (t.get("polledAt") is not None and self.now - t["polledAt"] <= AI_CLAIM_GRACE
                           and len(recent) < AI_MAX_PER_HOUR)
        return self._alive and _writable(self.user, self.device, span["app"], span["title"])


def _window(rec: dict) -> dict:
    return {"key": rec["key"], "app": rec["app"], "title": rec["title"], "claimedAt": rec["claimedAt"].isoformat(),
            "answerBy": (rec["claimedAt"] + AI_ANSWER_WAIT).isoformat()}


def claim() -> dict:
    """聊天后端来取：此刻等 AI 认的那个窗口（并认领），没有 → ``{"window": null}``。同一时刻每租户至多一个在等；
    已认领、还没答、没超时的那个原样再给（工人重启、模型自己再读一遍都拿到同一个）。"""
    user, now = current_tenant(), auto._now()  # noqa: SLF001
    ask_repo.polled(user, now, now - AI_KEEP)
    rec = ask_repo.live(user, now - AI_ANSWER_WAIT)
    if rec is None:
        running = timer_service.get_running_state(user) is not None
        w = auto.state(user, now, running, Asks(user, now))["aiThinking"]
        if not w or not ask_repo.count_claim(user, now, now - timedelta(hours=1), AI_MAX_PER_HOUR):
            return {"window": None}
        rec = {"user": user, "key": w["key"], "app": w["app"], "title": w["title"], "claimedAt": now}
        ask_repo.claim(rec)
    return {"window": _window(rec)}


def suggest(key: str, task_id: str | None, project_id: str | None, confidence: float | None, reason: str,
            none: bool) -> dict:
    """AI 的回答。只在这个窗口**此刻被认领着等回答**时收；写下的是只认这个窗口的一条规则 + 按窗口的临时选择。"""
    user, now = current_tenant(), auto._now()  # noqa: SLF001
    rec = ask_repo.get(user, key)
    if not rec or rec.get("answeredAt") is not None or now - rec["claimedAt"] > AI_ANSWER_WAIT:
        raise ConflictError(f"窗口 {key!r} 现在没在等 AI 认（没认领过、已经答过或超时了）。什么都没写")
    ask_repo.polled(user, now, now - AI_KEEP)  # 在回答 = 这条路活着
    done = {"answeredAt": now, "reason": reason}
    if none:
        if not ask_repo.answer(user, key, rec["claimedAt"], {**done, "outcome": "none"}):
            raise ConflictError(f"窗口 {key!r} 刚被别的回答抢先了。什么都没写")
        return {"key": key, "outcome": "none", "taskId": None, "projectId": None, "confidence": None,
                "autoRecord": False, "ruleWritten": False}
    span, device = auto._find(user, key)  # noqa: SLF001
    if (not detector_service.device_flags(user, device)["autoTrack"]
            or not _writable(user, device, span["app"], span["title"])):
        raise ConflictError("这台设备没开「允许 AI 管理进行中的任务」，或这个窗口写不出规则。什么都没写")
    if key in repo.choices(user, now):
        raise ConflictError("人已经对这个窗口做了选择（或说了这次不选），不覆盖。什么都没写")
    found = auto._lookup(task_id, project_id)  # noqa: SLF001
    if found is None:
        raise NotFoundError((f"任务不存在：{task_id!r}" if task_id else f"项目不存在：{project_id!r}") + "。什么都没写")
    task = planner_service.get_task(task_id) if task_id else None
    if task and (task.get("done") or task.get("kind", "normal") != "normal"):
        raise InvalidInputError("只能记到没完成的普通任务（定不了任务就只给 projectId）。什么都没写")
    target = {"taskId": task_id} if task_id else {"projectId": project_id}
    stored = auto.AUTO_CONFIDENCE if confidence >= AI_TRUST else float(confidence)
    if not ask_repo.answer(user, key, rec["claimedAt"], {**done, "outcome": "suggested", **target, "confidence": stored}):
        raise ConflictError(f"窗口 {key!r} 刚被别的回答抢先了。什么都没写")
    rule = {**auto._window_rule(span["app"], span["title"], target), "id": _rule_id(key),  # noqa: SLF001
            "confidence": stored, "author": "assistant", "auto": True, "note": ("AI 认的：" + reason)[:120]}
    written = window_rules.prepend(rule)
    repo.choice_put({"user": user, "key": key, "kind": "choice", "app": span["app"], "title": span["title"],
                     **target, "at": now, "expiresAt": now + auto.CHOICE_AWAY})
    return {"key": key, "outcome": "suggested", "taskId": found["taskId"], "projectId": found["projectId"],
            "confidence": stored, "autoRecord": written and stored >= auto.AUTO_CONFIDENCE, "ruleWritten": written}


def reject(key: str) -> dict:
    """人说「不对」：删掉 AI 给这个窗口写的规则、清掉临时选择；之后这个窗口照常请人选，``AI_RETRY`` 内不再问 AI。"""
    user, now = current_tenant(), auto._now()  # noqa: SLF001
    rec = ask_repo.get(user, key)
    if not rec or not ask_repo.reject(user, key, now):
        raise NotFoundError(f"窗口 {key!r} 不是 AI 认的（或已经说过不对了）")
    removed = window_rules.remove_auto(_rule_id(key))
    # 临时选择：盖成一份立刻过期的（repo 没有单删；过期的在下一次心跳清掉）
    repo.choice_put({"user": user, "key": key, "kind": "rejected", "app": rec["app"], "title": rec["title"],
                     "at": now, "expiresAt": now})
    return {"key": key, "app": rec["app"], "title": rec["title"], "ruleRemoved": removed}


def recorded_today() -> dict:
    """``GET activity/auto``：每条追加 ``ai``——这一段是按 AI 认的那条规则记下的（窗口与目标都对得上，且没被改挂）。"""
    out = auto.recorded_today()
    keys = {id(i): auto.window_key(i["app"], i["title"]) for i in out["items"]}
    recs = ask_repo.by_keys(current_tenant(), sorted(set(keys.values())))
    for i in out["items"]:
        rec = recs.get(keys[id(i)]) or {}
        i["ai"] = (rec.get("outcome") == "suggested" and not i["reassigned"]
                   and (rec.get("taskId") == i["taskId"] or rec.get("projectId") == i["projectId"]))
    return out
