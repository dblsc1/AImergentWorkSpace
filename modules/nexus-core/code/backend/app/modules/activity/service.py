"""活动建议（契约 v2.2「活动建议」节，唯一事实源）。

**自动检测到的活动只是建议，人确认了才是事实。** 本子边界只管「草稿」：收、列、忽略；
确认时把「哪个任务、哪段时间」交给 timer 子边界的 ``record_session``——
``session.completed`` 的组装只住在那里，与补登同一个函数，信封形状不许分叉。

关键取舍：

- **逐段校验，坏段进 rejected**：ai-detector 只在 2xx 后推进游标，一段坏数据让整批 4xx
  就会永远重发、永远卡住（同 events 入口「部分失败不整批回滚」）。
- **id 由防重键派生**：过期清掉又被重传的同一段拿到同一个 id，``activity:<id>`` 照样防重——
  同一段活动全系统至多一条事实。
- **确认先占位（pending→confirmed 条件更新）再写事实**：与忽略二选一，不会出现「忽略成功、
  事实照写」；写事实失败放回 pending；占位后崩掉，重试照样补写，防重键兜底不重。
- **过期惰性清理**，无调度器（同代理运行的遗忘超时）。
- **v2.7 AI 匹配**：助理（经 MCP）给待确认的建议配任务（``match``），人说「否」清掉并记住（``unmatch``），
  人说「是」就是 ``confirm``。两者都不确认任何东西；设备令牌（Bearer）一律 403。
- **v2.8 AI 提议新任务**：match 可以给 ``newTask`` 代替 ``taskId``；人确认时才建任务（只建一个），
  提议的校验、去重、建任务都在 ``proposals.py``。
- **v2.10 集合与项目**：match 的每条还可以带 ``collection {name}``（同类窗口的集合标签）与 ``projectId``
  （只到项目的建议）；只带这两样时不动建议的任务。两者只是给页面看的提示，不进台账。
- **v2.14 自动跟踪**：上传的 ``suggestion`` 可带规则给的 ``projectId``；``confirm(auto=True)`` 是自动记录走的
  同一条确认路径（只有出处不同）。判定与编排都在 ``auto.py``，本文件只留这两处入口。
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    ValidationError,
    field_validator,
    model_validator,
)

from ... import config, timeutil
from ...tenant import current as current_tenant
from ..events import service as events_service
from ..planner import audit as planner_audit
from ..planner import guard as planner_guard
from ..planner import service as planner_service
from ..planner import unclassified
from ..planner.errors import ForbiddenError, InvalidInputError, NotFoundError
from ..timer import service as timer_service
from . import presence, proposals, repo, session_link
from .proposals import ConflictError  # noqa: F401 —— 真身在 proposals.py（v2.8 也要抛它），main.py 照旧从这里取

SOURCE = "activity-confirmed"
#: 单段上限同补登（契约「拒绝规则」）：拦单位填错。
_MAX_SEGMENT_SECONDS = 86400
#: 检测程序与服务器的时钟误差容忍：endAt 比服务器「现在」晚这么多以内不算未来。
# 被拒的段不重发，所以宁宽勿严：设备时钟快一两分钟就丢掉每一段，代价远大于收下一段「略超前」的
_CLOCK_SKEW = timedelta(seconds=300)
_DEFAULT_LIMIT, _MAX_LIMIT = 100, 1000
_MAX_APP, _MAX_TITLE = 128, 512  # 码点数（Python str 长度即码点）


def _reason_bytes(v: str) -> str:
    # 契约按字节限长：检测程序按 UTF-8 字节截断，中文一字三字节，按字符数会放进 600 字节
    if len(v.encode("utf-8")) > 200:
        raise ValueError("reason 超过 200 字节")
    return v


class _Suggestion(BaseModel):
    taskId: Annotated[StrictStr, Field(min_length=1, max_length=128)] | None
    confidence: Annotated[float, Field(ge=0, le=1, strict=True)]
    reason: StrictStr
    classifier: Literal["rules", "service"]  # "assistant" 只由 match 写，上传不能自称
    # v2.14：规则只到项目（detector.rules.v1 v1.1）。与 taskId 不能同给。
    projectId: Annotated[StrictStr, Field(min_length=1, max_length=128)] | None = None

    reason_bytes = field_validator("reason")(_reason_bytes)

    @model_validator(mode="after")
    def _one_target(self):
        if self.taskId is not None and self.projectId is not None:
            raise ValueError("taskId 与 projectId 不能同给")
        return self


class _NewTask(BaseModel):
    projectId: Annotated[StrictStr, Field(min_length=1, max_length=128)]
    name: Annotated[StrictStr, Field(max_length=1024)]  # 1–64 码点的判据在 proposals.clean_name（去空白之后）


class _Collection(BaseModel):
    name: Annotated[StrictStr, Field(max_length=1024)]  # 1–64 码点的判据在 proposals.collection（去空白之后）


class _Match(BaseModel):
    """v2.7 助理配的一条。confidence 整数 0 / 1 也收（模型常这么写），布尔不收。
    v2.8：``taskId`` 与 ``newTask``（提议新任务）二选一。
    v2.10：给了 ``collection`` / ``projectId`` 时两者可以都不给（只贴标签，不动任务），这时 ``confidence`` 也可省。"""

    id: Annotated[StrictStr, Field(min_length=1, max_length=64)]
    taskId: Annotated[StrictStr, Field(min_length=1, max_length=128)] | None = None
    newTask: _NewTask | None = None
    confidence: Annotated[StrictFloat | StrictInt, Field(ge=0, le=1)] | None = None
    reason: StrictStr = ""
    collection: _Collection | None = None
    projectId: Annotated[StrictStr, Field(min_length=1, max_length=128)] | None = None

    reason_bytes = field_validator("reason")(_reason_bytes)

    @model_validator(mode="after")
    def _one_target(self):
        both = self.taskId is not None and self.newTask is not None
        none = self.taskId is None and self.newTask is None
        if both or (none and self.collection is None and self.projectId is None):
            raise ValueError("taskId 与 newTask 必须二选一（只能配到任务，或提议一个新任务）；"
                             "都不给时至少要有 collection 或 projectId")
        if not none and self.confidence is None:
            raise ValueError("配任务 / 提议新任务必须给 confidence")
        return self


class _Segment(BaseModel):
    startAt: Annotated[StrictStr, Field(max_length=64)]
    endAt: Annotated[StrictStr, Field(max_length=64)]
    durationSeconds: StrictInt
    # 超长不拒、截断（按码点）：标题是展示用的，为几个多余字符丢掉一整段真实活动不划算
    app: Annotated[StrictStr, Field(min_length=1)]
    title: StrictStr
    suggestion: _Suggestion
    idle: StrictBool = False  # v2.5：检测程序认为这段「前台没换、但无操作」


def _parse(raw: str, field: str) -> datetime:
    """必须带时区偏移，不许猜（同补登 ``_parse_start_at``）。"""
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError(f"{field} 不是合法 ISO8601：{raw!r}") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} 缺少时区偏移：{raw!r}")
    return parsed


def _check_times(seg: _Segment, now: datetime) -> datetime:
    """时间与时长的相互约束。返回 startAt（已解析）。不合规抛 ValueError（进 rejected）。"""
    start, end = _parse(seg.startAt, "startAt"), _parse(seg.endAt, "endAt")
    span = (end - start).total_seconds()
    if span <= 0:
        raise ValueError("endAt 必须晚于 startAt")
    if not 1 <= seg.durationSeconds <= min(span, _MAX_SEGMENT_SECONDS):
        raise ValueError(
            f"durationSeconds={seg.durationSeconds} 越界：须 ≥1、≤ endAt-startAt（{int(span)}）、"
            f"≤{_MAX_SEGMENT_SECONDS}"
        )
    if end > now + _CLOCK_SKEW:
        raise ValueError(f"endAt 在未来：{seg.endAt}——只收已经发生的活动")
    return start


def _reason(exc: ValidationError) -> str:
    err = exc.errors()[0]
    loc = ".".join(str(p) for p in err["loc"]) or "<root>"
    return f"{loc}: {err['msg']}"


def _sug_id(dedupe_key: str) -> str:
    return "sug_" + hashlib.sha256(dedupe_key.encode("utf-8")).hexdigest()[:20]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _purge(user: str, now: datetime) -> None:
    repo.purge(user, now - timedelta(days=config.settings.suggestion_ttl_days))  # 调用时读，测试可换 settings


def upload(device_id: str, segments: list[Any], inserted: list[dict] | None = None) -> dict:
    """``inserted`` 给了就把**新写入**的建议文档追加进去（v2.14 自动记录只看这些，防重命中的不看）。"""
    user, now = current_tenant(), _now()
    _purge(user, now)
    accepted = duplicates = 0
    rejected: list[dict] = []
    valid: list[tuple[_Segment, datetime, datetime]] = []
    for index, raw in enumerate(segments):
        try:
            seg = _Segment.model_validate(raw)
            valid.append((seg, _check_times(seg, now), _parse(seg.endAt, "endAt")))
        except ValidationError as exc:
            rejected.append({"index": index, "reason": _reason(exc)})
        except ValueError as exc:
            rejected.append({"index": index, "reason": str(exc)})
    # v2.13 窗口 ↔ 代理会话：这批段的总时间窗里的代理运行，只查一次
    runs = session_link.runs(user, min(s for _, s, _ in valid), max(e for _, _, e in valid)) if valid else []
    for seg, start, end in valid:
        suggestion = seg.suggestion.model_dump()
        project = suggestion.pop("projectId")
        if suggestion["taskId"] is not None and planner_service.get_task(suggestion["taskId"]) is None:
            # 建议错了不等于活动没发生：照收，任务留给人挑
            suggestion.update(taskId=None, confidence=0.0)
        if project is not None and planner_service.get_project(project) is None:
            project = None
            suggestion.update(confidence=0.0)
        if project is not None:
            suggestion["projectId"] = project  # v2.14 规则只到项目：规则是人定的，不再按代理会话去对
        elif suggestion["taskId"] is None:
            suggestion.update(session_link.link(runs, seg.app, seg.title, start, end))
        dedupe_key = f"aw:{device_id}:{start.astimezone(timezone.utc).isoformat()}"
        doc = {
            "user": user, "id": _sug_id(dedupe_key), "dedupeKey": dedupe_key, "deviceId": device_id,
            "startAt": seg.startAt, "endAt": seg.endAt, "startTs": start,
            "durationSeconds": seg.durationSeconds, "app": seg.app[:_MAX_APP], "title": seg.title[:_MAX_TITLE],
            "suggestion": suggestion, "idle": seg.idle, "status": "pending", "receivedAt": now,
        }
        if repo.insert_if_absent(doc):
            accepted += 1
            if inserted is not None:
                inserted.append(doc)
        else:
            duplicates += 1
    return {"accepted": accepted, "duplicates": duplicates, "rejected": rejected}


_ITEM_KEYS = ("id", "deviceId", "startAt", "endAt", "durationSeconds", "app", "title", "suggestion", "status")


def _item(d: dict, open_ids: set | None = None) -> dict:
    # v2.5 之前存下的建议没有 idle 字段：回 false；没被人否过的没有 rejectedTaskIds：回 []
    # v2.8 suggestion.newTask 只在有提议时出现；提议已否掉 / 过期 → 当作没有建议（不出 newTask，把握 0）
    out = {**{k: d[k] for k in _ITEM_KEYS}, "idle": d.get("idle", False),
           "rejectedTaskIds": d.get("rejectedTaskIds", []), "auto": d.get("auto", False)}  # auto：v2.14
    nt = d["suggestion"].get("newTask")
    if nt and open_ids is not None and nt["proposalId"] not in open_ids:
        out["suggestion"] = {**{k: v for k, v in d["suggestion"].items() if k != "newTask"},
                             "confidence": 0.0, "reason": ""}
    return out


def last_uploads(user: str) -> dict:
    """{deviceId: 最近一次收到它上传的时刻}——detector 子边界的设备列表用（跨子边界只走 service）。"""
    return repo.last_received(user)


def list_suggestions(status: str, limit: int, offset: int) -> dict:
    user = current_tenant()
    _purge(user, _now())
    limit = _DEFAULT_LIMIT if limit <= 0 else min(limit, _MAX_LIMIT)  # 同档案读端口径
    total, docs = repo.page(user, status, limit, max(offset, 0))
    pids = [d["suggestion"]["newTask"]["proposalId"] for d in docs if d["suggestion"].get("newTask")]
    st = proposals.statuses(user, pids) if pids else {}
    open_ids = {k for k, v in st.items() if v in ("pending", "accepted")}
    return {"total": total, "items": [_item(d, open_ids) for d in docs]}


def _get(user: str, sug_id: str) -> dict:
    doc = repo.get(user, sug_id)
    if doc is None:
        raise NotFoundError(f"活动建议不存在：{sug_id!r}")
    return doc


def _bucket(project_id: str, request) -> str:
    """v2.9：项目的「未分类」时间桶，取或建（同 ``POST /planner/projects/{id}/unclassified``，同一条写入口）。"""
    task = unclassified.find(project_id) or planner_guard.run_write(
        request, op=planner_audit.OP_CREATE, object_type="tasks", changes=unclassified.changes(project_id),
        action=lambda actor: unclassified.create(project_id, actor),
    )
    return task["id"]


def confirm(sug_id: str, task_id: str | None, mode: str, name: str | None = None, request=None,
            proposal_id: str | None = None, project_id: str | None = None, auto: bool = False) -> dict:
    """v2.8：``proposal_id`` = 人在页面上看到并点「是」的那条新任务提议（占位时要求建议的提议仍是它）；
    ``name`` = 人改过的名字；``request`` 给建任务要经的 planner 写入口（判来源、留审计）。
    v2.9：``project_id`` = 只指定项目，记到它的「未分类」时间桶——先取或建出桶的 id，之后与带 ``taskId`` 的确认同一条路
    （桶是懒建的系统任务，占位没成功多建一个空桶也无妨）。
    v2.14：``auto`` = 不是人点的，是自动记录（``auto.record``）——同一条路径，只有出处不同（信封 ``ai``、建议的 ``auto``）。"""
    user = current_tenant()
    doc = _get(user, sug_id)
    if doc["status"] == "dismissed":
        raise ConflictError(f"活动建议 {sug_id!r} 已忽略，不能再确认")
    dedupe_key = f"activity:{sug_id}"
    stored = events_service.find_by_dedupe(user, SOURCE, dedupe_key)
    if stored is not None:
        # 重复确认（哪怕换了任务）：回原来那条，不写第二条（同代理运行重复 stop）
        repo.set_status(user, sug_id, "confirmed", _now(), only_from="pending")  # 补齐上次没改成的状态
        return {
            "id": sug_id, "status": "confirmed", "duplicate": True,
            "date": timeutil.local_date(datetime.fromisoformat(doc["startAt"]), config.settings.tz),
            "event": {k: stored[k] for k in ("id", "dedupeKey", "type")}, "taskId": stored["subject"]["task"],
        }
    suggested = doc["suggestion"].get("taskId")
    if project_id is not None:
        if task_id or proposal_id is not None or name is not None:
            raise InvalidInputError("projectId 与 taskId / proposalId / name 不能同给：只指定项目 = 记到它的「未分类」")
        task_id = _bucket(project_id, request)  # 项目不存在 404，建议原样留在待确认
    # v2.8：带 proposalId = 确认 AI 提议的新任务，人点「是」才建（同一提议只建一个，见 proposals.task_for）
    if task_id and (proposal_id is not None or name is not None):
        raise InvalidInputError("taskId 与 proposalId / name 不能同给：要么记到现成任务，要么确认新任务")
    if name is not None and proposal_id is None:
        raise InvalidInputError("name 只用于确认 AI 提议的新任务，须同时带 proposalId")
    proposal = proposal_id
    if name is not None:
        try:
            name = proposals.clean_name(name)
        except ValueError as exc:
            raise InvalidInputError(str(exc)) from exc
    if proposal is not None:
        forbid_device_token(request.headers.get("authorization") if request is not None else None, "建任务")
    # 请求体没给任务 = 用建议里的：占位时要求建议的任务没变（v2.7：读到之后可能刚被人否掉 / 被助理换掉）
    only_task = suggested if not task_id and proposal is None else None
    task_id = task_id or (None if proposal is not None else suggested)
    if not task_id and proposal is None:
        raise InvalidInputError("没有可确认的任务：请求体与建议里都没有 taskId，请先选一个任务")
    # 先占位再写事实：pending→confirmed 是条件更新，与忽略（同样只从 pending 转）二选一，
    # 不会出现「忽略回了 200，事实却照样落库」。占位后崩在写事实之前 → 状态已确认、台账没有，
    # 重试走到这里（占位不中但状态是 confirmed）加入占位再补写，防重键兜底不重。
    if not repo.claim(user, sug_id, _now(), only_task=only_task, only_proposal=proposal, auto=auto):
        cur = _get(user, sug_id)
        if cur["status"] == "dismissed":
            raise ConflictError(f"活动建议 {sug_id!r} 已忽略，不能再确认")
        if only_task is not None:
            # 用建议里的任务：只认**现在**建议里的那个——自己早先读到的可能已被否掉 / 换掉
            only_task = task_id = cur["suggestion"].get("taskId")
        # 还是 pending = 建议的任务在读与占位之间变了；confirmed = 别的确认占着位（或占位后崩了）：
        # 加入占位再写。只要还有一个确认占着，谁写失败都不会把状态放回 pending——
        # 于是没人能趁机否掉 / 换掉这个任务，也不会出现「事实写成了、状态却是待确认」
        if ((not task_id and proposal is None) or cur["status"] != "confirmed"
                or not repo.claim(user, sug_id, _now(), takeover=True, only_task=only_task, only_proposal=proposal)):
            raise ConflictError(f"活动建议 {sug_id!r} 刚被改动，请刷新后再确认")
    # 占着位重读：已确认的建议不会再被配 / 否（那两个只动 pending），这份就是定稿，把握取它
    doc = _get(user, sug_id)
    try:
        if proposal is not None:
            task_id = proposals.task_for(user, proposal, name, request)
        out = timer_service.record_session(
            task_id, doc["startAt"], doc["endAt"], doc["durationSeconds"],
            source=SOURCE, dedupe_key=dedupe_key, mode=mode,
            ai={"generated": True, "confidence": doc["suggestion"]["confidence"], "confirmed": not auto,
                **({"auto": True} if auto else {})},
        )
    except Exception:
        # 任务不存在等：事实没写成，退出占位；没有别的确认还占着、台账里也没有才放回待确认。
        # 尽力而为：放回本身出错也要把原来的错误原样抛出去
        try:
            if events_service.find_by_dedupe(user, SOURCE, dedupe_key) is None:
                repo.release(user, sug_id, _now())
        except Exception:  # noqa: BLE001, S110
            pass
        raise
    # taskId 取台账里真正落下的那条（并发确认带了不同的任务时，可能不是本次的）
    stored = events_service.find_by_dedupe(user, SOURCE, dedupe_key)
    return {"id": sug_id, "status": "confirmed", "duplicate": out["duplicate"],
            "date": out["date"], "event": out["event"], "taskId": stored["subject"]["task"]}


def dismiss(sug_id: str) -> dict:
    user = current_tenant()
    _get(user, sug_id)
    if not repo.set_status(user, sug_id, "dismissed", _now(), only_from="pending"):
        if _get(user, sug_id)["status"] == "confirmed":
            raise ConflictError(f"活动建议 {sug_id!r} 已确认、事实已写，不能再忽略")
    return {"id": sug_id, "status": "dismissed"}


# ------------------------------------------------ v2.7 AI 匹配（契约「AI 匹配」）


def forbid_device_token(authorization: str | None, what: str = "给活动建议配任务") -> None:
    # auth.gate：带了 Bearer 就是设备令牌（网页会话走 cookie，MCP 对内直连不带）。同 detector 的写。
    if (authorization or "").strip().lower().startswith("bearer "):
        raise ForbiddenError(f"设备令牌不能{what}；请在 Cockpit「AI助理」页登录后操作")


def match(authorization: str | None, matches: list[Any]) -> dict:
    """助理给待确认的建议配任务。逐条校验，坏的进 rejected；**不确认任何东西**。"""
    forbid_device_token(authorization)
    user = current_tenant()
    matched, rejected = 0, []
    for index, raw in enumerate(matches):
        try:
            m = _Match.model_validate(raw)
            # v2.10 标签：集合（名字归一成 key）与只到项目的建议
            labels = {"collection": proposals.collection(m.collection.name)} if m.collection is not None else {}
        except ValidationError as exc:
            rejected.append({"index": index, "reason": _reason(exc)})
            continue
        except ValueError as exc:
            rejected.append({"index": index, "reason": str(exc)})
            continue
        if m.projectId is not None:
            labels["projectId"] = m.projectId
        label_only = m.taskId is None and m.newTask is None  # 只贴标签，不动建议的任务
        doc = repo.get(user, m.id)
        why = None
        if doc is None:
            why = f"活动建议不存在：{m.id!r}"
        elif doc["status"] != "pending":
            why = f"活动建议 {m.id!r} 已{'确认' if doc['status'] == 'confirmed' else '忽略'}，不能再配"
        elif m.taskId is not None and m.taskId in doc.get("rejectedTaskIds", []):
            why = f"用户已经否掉过任务 {m.taskId!r}，不要再配同一个"
        elif not label_only and doc["suggestion"].get("taskId") and doc["suggestion"].get("classifier") != "assistant":
            why = "这条已有分类规则给的任务，助理不覆盖（只贴 collection / projectId 可以：别带 taskId / newTask）"
        elif m.taskId is not None and (task := planner_service.get_task(m.taskId)) is None:
            why = f"任务不存在：{m.taskId!r}"
        elif m.projectId is not None and planner_service.get_project(m.projectId) is None:
            why = f"项目不存在：{m.projectId!r}"
        elif not label_only and m.projectId not in (
                None, m.newTask.projectId if m.newTask else task["projectId"]):
            why = f"projectId {m.projectId!r} 不是这个任务 / 新任务所在的项目"
        elif label_only:
            if not repo.set_labels(user, m.id, labels):
                why = "这条建议刚被改动（确认 / 忽略），没有写入"
        else:
            sug = {"taskId": m.taskId, "confidence": float(m.confidence), "reason": m.reason, "classifier": "assistant"}
            if "collection" in doc["suggestion"]:  # 换任务不丢集合标签
                sug["collection"] = doc["suggestion"]["collection"]
            sug.update(labels)
            if m.newTask is not None:  # v2.8：提议新任务（校验 + 去重在 proposals.py）
                sug["newTask"], why = proposals.propose(user, doc, m.newTask.projectId, m.newTask.name, _now())
            if not why and not repo.set_match(user, m.id, sug):
                why = "这条建议刚被改动（确认 / 忽略 / 否），没有写入"
            elif not why and m.newTask is not None and not proposals.is_open(user, sug["newTask"]["proposalId"]):
                # 挂上之后再看一眼：提议恰好在这期间被否掉了 → 摘下来。仍有极窄的窗口（这一眼之后才被否掉），
                # 但安全：列表把否掉的提议当没有建议，确认也会 409，建不出任务
                repo.clear_proposal(user, m.id, sug["newTask"]["proposalId"])
                why = "用户已经否掉过这个新任务，不要再提"
        if why:
            rejected.append({"index": index, "reason": why})
        else:
            matched += 1
    return {"matched": matched, "rejected": rejected}


def unmatch(authorization: str | None, sug_id: str, task_id: str | None, proposal_id: str | None = None) -> dict:
    """人说「否」：清掉建议的任务并记进 rejectedTaskIds；状态仍 pending。
    v2.8 建议是提议的新任务时：清掉提议、记进 rejectedProposalIds，没人再指着它就把提议标为已否掉。"""
    forbid_device_token(authorization)
    user = current_tenant()
    doc = _get(user, sug_id)
    current = doc["suggestion"].get("taskId")
    offer = (doc["suggestion"].get("newTask") or {}).get("proposalId")
    if doc["status"] != "pending":
        raise ConflictError(f"活动建议 {sug_id!r} 已处理，不能再否")
    if offer is not None:
        if task_id is not None or proposal_id not in (None, offer):
            raise ConflictError("这条建议已经变了，请刷新后再定")
        if not repo.clear_proposal(user, sug_id, offer):
            raise ConflictError("这条建议刚被改动，请刷新后再定")
        repo.proposal_reject_if_unused(user, offer, _now())
        return {"id": sug_id, "status": "pending", "rejectedTaskIds": doc.get("rejectedTaskIds", [])}
    if proposal_id is not None and current is not None:
        raise ConflictError("这条建议已经变了，请刷新后再定")
    if task_id is not None and current is not None and task_id != current:
        raise ConflictError("这条建议的任务已经变了，请刷新后再定")
    if current is not None and not repo.clear_match(user, sug_id, current):
        raise ConflictError("这条建议刚被改动，请刷新后再定")
    return {"id": sug_id, "status": "pending", "rejectedTaskIds": _get(user, sug_id).get("rejectedTaskIds", [])}


def list_presence(user: str, now: datetime, start: datetime, end: datetime) -> list[dict]:
    """v2.4 在场心跳的公开读路径（``views/lanes``），真身在 ``presence.py``。**不写**。"""
    return presence.list_spans(user, now, start, end)


def auto_state(user: str, now: datetime, timer_running: bool, manual_end: datetime | None) -> dict:
    """v2.14 ``views/lanes`` 的 ``human.auto`` / ``human.needsChoice``，真身在 ``auto.py``。**不写**。"""
    from . import auto  # noqa: PLC0415 —— auto 也 import 本文件（confirm），模块级会成环

    return auto.state(user, now, timer_running, manual_end)
