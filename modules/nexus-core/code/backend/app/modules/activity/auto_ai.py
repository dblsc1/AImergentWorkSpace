"""让 AI 认窗口（契约 v2.15「让 AI 认窗口」节，唯一事实源）——自动跟踪的第二步，插在 ``auto.next_step`` 的 ``"ask_ai"`` 上。

仓主 2026-10-08：「认不出，让 AI 出来写规则；写不出、也没有现成任务对得上，提醒人选」「cockpit 能不能主动调 opencode 做匹配」。
nexus-core 够不着聊天后端（gateway.v1 第八节），所以是**对方来取**：聊天后端的后台工人经 MCP 定时调 ``claim``，
有窗口在等就跑一轮模型、经 MCP 调 ``suggest``。状态全在这里，读端（``Asks``）**不写**：

- 没有问询 + 最近 ``AI_CLAIM_GRACE`` 里有人来认领过（= 这条路活着）+ 这一小时没问满 + 写得出规则 → ``ask_ai``（等认领）；
- 认领了、没回答、没超过 ``AI_ANSWER_WAIT`` → ``ask_ai``；
- 其余（没人来认领、超时、AI 说认不出、``AI_RETRY`` 内问过）→ ``ask_human``，就是第一步那张卡。
没装 / 没配聊天后端 = 从来没人认领 = 与 v2.14 完全相同，一秒也不多等。

**人与 AI 抢同一个窗口时人总是赢**（2026-10-08 波次统一审核），靠的是条件写与次序，不是先查后写：
人的端点（``choose`` / ``dismiss`` / ``reject``）先写自己的，再在问询上留记号（``_decided`` / ``ask_repo.reject``）、撤 AI 的规则；
AI 的回答只在「没答过、人也没留记号」时写得进，它的规则不换掉人的规则、它的临时选择不盖活着的那一份，
全写完**再看一眼**问询——已经不是它刚写下的那个答案了就把自己写的撤回。两边无论怎么交错，最后都没有 AI 的东西留下。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from ...tenant import current as current_tenant
from ..detector import service as detector_service
from ..detector import window_rules
from ..planner import service as planner_service
from ..planner.errors import InvalidInputError, NotFoundError
from ..timer import service as timer_service
from . import ask_repo, auto, choice_repo, ignore
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


def stale(rec: dict | None, ref: dict) -> bool:
    """``ref`` 是人对这个窗口说过「不对」的目标吗（检测程序拉到新规则之前，心跳与上传的段还会带着它）。
    看问询的 ``rejected``：它不随再问 AI 清掉；人后来自己选了同一个目标就从里面拿掉了——那是人的决定。"""
    return any(_ids(r) == _ids(ref) for r in (rec or {}).get("rejected") or [])


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
        return None if g and stale(self._rec(key), g) else g

    def source(self, key: str, ref: dict, source: str) -> str:
        """这个目标是 AI 认的吗（问询里记着同一个目标）→ ``"ai"``；否则原样。"""
        rec = self._rec(key)
        return "ai" if rec and rec.get("outcome") == "suggested" and _ids(rec) == _ids(ref) else source

    def wants(self, key: str, span: dict) -> bool:
        """这个没有目标的窗口此刻归 AI 认吗（``next_step`` 的 ``"ask_ai"``）。"""
        rec = self._rec(key)
        if rec and self.now - rec["claimedAt"] < AI_RETRY:
            return (rec.get("answeredAt") is None and rec.get("humanAt") is None
                    and self.now - rec["claimedAt"] <= AI_ANSWER_WAIT)
        if self._alive is None:
            t = ask_repo.tenant(self.user)
            recent = [c for c in t.get("claims") or [] if c and self.now - c < timedelta(hours=1)]
            self._alive = (t.get("polledAt") is not None and self.now - t["polledAt"] <= AI_CLAIM_GRACE
                           and len(recent) < AI_MAX_PER_HOUR)
        return self._alive and _writable(self.user, self.device, span["app"], span["title"])


def _window(rec: dict) -> dict:
    return {"key": rec["key"], "app": rec["app"], "title": rec["title"], "claimedAt": rec["claimedAt"].isoformat(),
            "answerBy": (rec["claimedAt"] + AI_ANSWER_WAIT).isoformat()}


@ignore.guarded
def claim() -> dict:
    """聊天后端来取：此刻等 AI 认的那个窗口（并认领），没有 → ``{"window": null}``。
    已认领、还没答、没超时的那个原样再给（工人重启、模型自己再读一遍都拿到同一个）。
    **先占名额、再让问询露面**：这一小时的名额占到了才写问询（一次条件写，``ask_repo.claim``）——所以谁都取不到、
    答不了一份没占到名额的问询。几个认领同时来只有一个写得进，其余的把名额退回、重读、拿到同一份；答过的问询不会被盖掉。
    ponytail: 两个同时的认领各自看中**不同**的窗口时会有两份在等（之后只再给最新的那份，另一份超时转去请人）；
    要严格「每租户一份」就把在等的那份记到 ``_tenant`` 文档上一起条件更新。"""
    user, now = current_tenant(), auto._now()  # noqa: SLF001
    ask_repo.polled(user, now, now - AI_KEEP)
    rec = ask_repo.live(user, now - AI_ANSWER_WAIT)
    if rec is None:
        running = timer_service.get_running_state(user) is not None
        w = auto.state(user, now, running, Asks(user, now))["aiThinking"]
        if not w:
            return {"window": None}
        rec = {"user": user, "key": w["key"], "app": w["app"], "title": w["title"], "claimedAt": now}
        counted = ask_repo.count_claim(user, now, now - timedelta(hours=1), AI_MAX_PER_HOUR)
        try:
            written = counted and ask_repo.claim(rec, now - AI_RETRY)
        except Exception:  # 问询没写进去（库出错等）：名额不能白占，退回再抛
            ask_repo.uncount_claim(user, now)
            raise
        if not written:
            if counted:  # 别的认领抢先建了这个窗口的问询：不盖它，名额退回
                ask_repo.uncount_claim(user, now)
            rec = ask_repo.live(user, now - AI_ANSWER_WAIT)  # 给此刻在等的那一份（没有 / 已答完 → None）
    return {"window": _window(rec) if rec else None}


@ignore.guarded
def suggest(key: str, task_id: str | None, project_id: str | None, confidence: float | None, reason: str,
            none: bool) -> dict:
    """AI 的回答。只在这个窗口**此刻被认领着等回答**时收；写下的是只认这个窗口的一条规则 + 按窗口的临时选择。"""
    user, now = current_tenant(), auto._now()  # noqa: SLF001
    rec = ask_repo.get(user, key)
    if not rec or rec.get("answeredAt") is not None or now - rec["claimedAt"] > AI_ANSWER_WAIT:
        raise ConflictError(f"窗口 {key!r} 现在没在等 AI 认（没认领过、已经答过或超时了）。什么都没写")
    ask_repo.polled(user, now, now - AI_KEEP)  # 在回答 = 这条路活着
    done = {"answeredAt": now, "reason": reason}
    taken = ConflictError(f"窗口 {key!r} 刚被别的回答抢先了，或人刚对它做了决定。什么都没写")
    if none:
        if not ask_repo.answer(user, key, rec["claimedAt"], {**done, "outcome": "none"}):
            raise taken
        return {"key": key, "outcome": "none", "taskId": None, "projectId": None, "confidence": None,
                "autoRecord": False, "ruleWritten": False}
    span, device = auto._find(user, key)  # noqa: SLF001
    if ignore.find(ignore.rules(user), span["app"], span["title"]):
        raise ConflictError("这个窗口被用户设成了「忽略并记住」，不写规则。什么都没写")
    if (not detector_service.device_flags(user, device)["autoTrack"]
            or not _writable(user, device, span["app"], span["title"])):
        raise ConflictError("这台设备没开「允许 AI 管理进行中的任务」，或这个窗口写不出规则。什么都没写")
    if key in choice_repo.live(user, now):
        raise ConflictError("人已经对这个窗口做了选择（或说了这次不选），不覆盖。什么都没写")
    found = auto._lookup(task_id, project_id)  # noqa: SLF001
    if found is None:
        raise NotFoundError((f"任务不存在：{task_id!r}" if task_id else f"项目不存在：{project_id!r}") + "。什么都没写")
    task = planner_service.get_task(task_id) if task_id else None
    if task and (task.get("done") or task.get("kind", "normal") != "normal"):
        raise InvalidInputError("只能记到没完成的普通任务（定不了任务就只给 projectId）。什么都没写")
    target = {"taskId": task_id} if task_id else {"projectId": project_id}
    stored = auto.AUTO_CONFIDENCE if confidence >= AI_TRUST else float(confidence)
    # 上面的检查之后人还可能插进来，所以下面每一步都是条件写，写完再看一眼（见本文件头部「人总是赢」）
    if not ask_repo.answer(user, key, rec["claimedAt"], {**done, "outcome": "suggested", **target, "confidence": stored}):
        raise taken
    rule = {**auto._window_rule(span["app"], span["title"], target), "id": _rule_id(key),  # noqa: SLF001
            "confidence": stored, "author": "assistant", "auto": True, "note": ("AI 认的：" + reason)[:120]}
    written = window_rules.prepend(rule, yield_to_human=True)
    choice_repo.put({"user": user, "key": key, "kind": "choice", "by": "ai", "app": span["app"], "title": span["title"],
                     **target, "at": now, "expiresAt": now + auto.CHOICE_AWAY}, unless_live=now)
    if ((ask_repo.get(user, key) or {}).get("outcome") != "suggested"
            or not detector_service.device_flags(user, device)["autoTrack"]):
        window_rules.remove_auto(_rule_id(key))
        choice_repo.drop_ai(user, key)
        raise ConflictError("人刚对这个窗口做了决定（或关了开关），AI 写的已经撤回。什么都没写")
    return {"key": key, "outcome": "suggested", "taskId": found["taskId"], "projectId": found["projectId"],
            "confidence": stored, "autoRecord": written and stored >= auto.AUTO_CONFIDENCE, "ruleWritten": written}


@ignore.guarded
def reject(key: str) -> dict:
    """人说「不对」：删掉 AI 给这个窗口写的规则、清掉临时选择；之后这个窗口照常请人选，``AI_RETRY`` 内不再问 AI。"""
    user, now = current_tenant(), auto._now()  # noqa: SLF001
    rec = ask_repo.get(user, key)
    if not rec or not ask_repo.reject(user, key, now):
        raise NotFoundError(f"窗口 {key!r} 不是 AI 认的（或已经说过不对了）")
    removed = window_rules.remove_auto(_rule_id(key))
    # 临时选择：盖成一份立刻过期的（repo 没有单删；过期的在下一次心跳清掉）
    choice_repo.put({"user": user, "key": key, "kind": "rejected", "app": rec["app"], "title": rec["title"],
                     "at": now, "expiresAt": now})
    return {"key": key, "app": rec["app"], "title": rec["title"], "ruleRemoved": removed}


def _decided(key: str, target: dict | None) -> None:
    """人对这个窗口做了决定（``target`` = 选的目标；None = 这次不选）：在它的问询上留记号——还没答的那一次从此答不进
    （``ask_repo.answer`` 的条件）；AI 已经认到**别的**目标 → 等同人说了「不对」，撤掉它写的规则。"""
    user, now = current_tenant(), auto._now()  # noqa: SLF001
    rec = ask_repo.decided(user, key, now, target)
    if rec and rec.get("outcome") == "suggested" and _ids(rec) != _ids(target or {}) and ask_repo.reject(user, key, now):
        window_rules.remove_auto(_rule_id(key))


def choose(key: str, task_id: str | None, project_id: str | None, remember: bool) -> dict:
    """``POST activity/choice``：人答（``auto.choose``），再告诉问询「人定了」。次序要紧：人的先写完。"""
    out = auto.choose(key, task_id, project_id, remember)
    _decided(key, {"taskId": task_id, "projectId": project_id})
    return out


def dismiss(key: str) -> dict:
    out = auto.dismiss(key)
    _decided(key, None)
    return out


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
