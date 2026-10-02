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
)

from ... import config, timeutil
from ...tenant import current as current_tenant
from ..events import service as events_service
from ..planner import service as planner_service
from ..planner.errors import ForbiddenError, InvalidInputError, NotFoundError
from ..timer import service as timer_service
from . import presence, repo

SOURCE = "activity-confirmed"
#: 单段上限同补登（契约「拒绝规则」）：拦单位填错。
_MAX_SEGMENT_SECONDS = 86400
#: 检测程序与服务器的时钟误差容忍：endAt 比服务器「现在」晚这么多以内不算未来。
# 被拒的段不重发，所以宁宽勿严：设备时钟快一两分钟就丢掉每一段，代价远大于收下一段「略超前」的
_CLOCK_SKEW = timedelta(seconds=300)
_DEFAULT_LIMIT, _MAX_LIMIT = 100, 1000
_MAX_APP, _MAX_TITLE = 128, 512  # 码点数（Python str 长度即码点）


class ConflictError(RuntimeError):
    """已确认的再忽略 / 已忽略的再确认。main.py 映射成 409。"""


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

    reason_bytes = field_validator("reason")(_reason_bytes)


class _Match(BaseModel):
    """v2.7 助理配的一条。confidence 整数 0 / 1 也收（模型常这么写），布尔不收。"""

    id: Annotated[StrictStr, Field(min_length=1, max_length=64)]
    taskId: Annotated[StrictStr, Field(min_length=1, max_length=128)]
    confidence: Annotated[StrictFloat | StrictInt, Field(ge=0, le=1)]
    reason: StrictStr = ""

    reason_bytes = field_validator("reason")(_reason_bytes)


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


def upload(device_id: str, segments: list[Any]) -> dict:
    user, now = current_tenant(), _now()
    _purge(user, now)
    accepted = duplicates = 0
    rejected: list[dict] = []
    for index, raw in enumerate(segments):
        try:
            seg = _Segment.model_validate(raw)
            start = _check_times(seg, now)
        except ValidationError as exc:
            rejected.append({"index": index, "reason": _reason(exc)})
            continue
        except ValueError as exc:
            rejected.append({"index": index, "reason": str(exc)})
            continue
        suggestion = seg.suggestion.model_dump()
        if suggestion["taskId"] is not None and planner_service.get_task(suggestion["taskId"]) is None:
            # 建议错了不等于活动没发生：照收，任务留给人挑
            suggestion.update(taskId=None, confidence=0.0)
        dedupe_key = f"aw:{device_id}:{start.astimezone(timezone.utc).isoformat()}"
        doc = {
            "user": user, "id": _sug_id(dedupe_key), "dedupeKey": dedupe_key, "deviceId": device_id,
            "startAt": seg.startAt, "endAt": seg.endAt, "startTs": start,
            "durationSeconds": seg.durationSeconds, "app": seg.app[:_MAX_APP], "title": seg.title[:_MAX_TITLE],
            "suggestion": suggestion, "idle": seg.idle, "status": "pending", "receivedAt": now,
        }
        if repo.insert_if_absent(doc):
            accepted += 1
        else:
            duplicates += 1
    return {"accepted": accepted, "duplicates": duplicates, "rejected": rejected}


_ITEM_KEYS = ("id", "deviceId", "startAt", "endAt", "durationSeconds", "app", "title", "suggestion", "status")


def _item(d: dict) -> dict:
    # v2.5 之前存下的建议没有 idle 字段：回 false；没被人否过的没有 rejectedTaskIds：回 []
    return {**{k: d[k] for k in _ITEM_KEYS}, "idle": d.get("idle", False),
            "rejectedTaskIds": d.get("rejectedTaskIds", [])}


def last_uploads(user: str) -> dict:
    """{deviceId: 最近一次收到它上传的时刻}——detector 子边界的设备列表用（跨子边界只走 service）。"""
    return repo.last_received(user)


def list_suggestions(status: str, limit: int, offset: int) -> dict:
    user = current_tenant()
    _purge(user, _now())
    limit = _DEFAULT_LIMIT if limit <= 0 else min(limit, _MAX_LIMIT)  # 同档案读端口径
    total, docs = repo.page(user, status, limit, max(offset, 0))
    return {"total": total, "items": [_item(d) for d in docs]}


def _get(user: str, sug_id: str) -> dict:
    doc = repo.get(user, sug_id)
    if doc is None:
        raise NotFoundError(f"活动建议不存在：{sug_id!r}")
    return doc


def confirm(sug_id: str, task_id: str | None, mode: str) -> dict:
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
            "event": {k: stored[k] for k in ("id", "dedupeKey", "type")},
        }
    suggested = doc["suggestion"].get("taskId")
    # 请求体没给任务 = 用建议里的：占位时要求建议的任务没变（v2.7：读到之后可能刚被人否掉 / 被助理换掉）
    only_task = suggested if not task_id else None
    task_id = task_id or suggested
    if not task_id:
        raise InvalidInputError("没有可确认的任务：请求体与建议里都没有 taskId，请先选一个任务")
    # 先占位再写事实：pending→confirmed 是条件更新，与忽略（同样只从 pending 转）二选一，
    # 不会出现「忽略回了 200，事实却照样落库」。占位后崩在写事实之前 → 状态已确认、台账没有，
    # 重试走到这里（占位不中但状态是 confirmed）照样补写，防重键兜底不重。
    claimed = repo.set_status(user, sug_id, "confirmed", _now(), only_from="pending", only_task=only_task)
    if not claimed:
        doc = _get(user, sug_id)  # 重读：下面补写用的任务、把握都以占位成功那一刻的建议为准
        if doc["status"] == "dismissed":
            raise ConflictError(f"活动建议 {sug_id!r} 已忽略，不能再确认")
        stale = "的任务刚被改动，请刷新后再确认"
        if doc["status"] == "pending":  # 建议的任务在读与占位之间变了
            raise ConflictError(f"活动建议 {sug_id!r} {stale}")
        if only_task is not None:
            # 已被另一次确认占位（或上次占位后崩了）：用建议里的任务时，补写只认**现在**建议里的那个——
            # 自己早先读到的可能已被否掉 / 换掉，拿它补写会抢在对方前面把时间记到被否掉的任务上
            task_id = doc["suggestion"].get("taskId")
            if not task_id:
                raise ConflictError(f"活动建议 {sug_id!r} {stale}")
    try:
        out = timer_service.record_session(
            task_id, doc["startAt"], doc["endAt"], doc["durationSeconds"],
            source=SOURCE, dedupe_key=dedupe_key, mode=mode,
            ai={"generated": True, "confidence": doc["suggestion"]["confidence"], "confirmed": True},
        )
    except Exception:
        # 任务不存在等：事实没写成，放回待确认（并发的另一次确认若已写成，就别放回）。
        # 尽力而为：放回本身出错也要把原来的错误原样抛出去
        if claimed:
            try:
                if events_service.find_by_dedupe(user, SOURCE, dedupe_key) is None:
                    repo.set_status(user, sug_id, "pending", _now(), only_from="confirmed")
            except Exception:  # noqa: BLE001, S110
                pass
        raise
    return {"id": sug_id, "status": "confirmed", "duplicate": out["duplicate"],
            "date": out["date"], "event": out["event"]}


def dismiss(sug_id: str) -> dict:
    user = current_tenant()
    _get(user, sug_id)
    if not repo.set_status(user, sug_id, "dismissed", _now(), only_from="pending"):
        if _get(user, sug_id)["status"] == "confirmed":
            raise ConflictError(f"活动建议 {sug_id!r} 已确认、事实已写，不能再忽略")
    return {"id": sug_id, "status": "dismissed"}


# ------------------------------------------------ v2.7 AI 匹配（契约「AI 匹配」）


def forbid_device_token(authorization: str | None) -> None:
    # auth.gate：带了 Bearer 就是设备令牌（网页会话走 cookie，MCP 对内直连不带）。同 detector 的写。
    if (authorization or "").strip().lower().startswith("bearer "):
        raise ForbiddenError("设备令牌不能给活动建议配任务；请在 Cockpit「AI助理」页登录后操作")


def match(authorization: str | None, matches: list[Any]) -> dict:
    """助理给待确认的建议配任务。逐条校验，坏的进 rejected；**不确认任何东西**。"""
    forbid_device_token(authorization)
    user = current_tenant()
    matched, rejected = 0, []
    for index, raw in enumerate(matches):
        try:
            m = _Match.model_validate(raw)
        except ValidationError as exc:
            rejected.append({"index": index, "reason": _reason(exc)})
            continue
        doc = repo.get(user, m.id)
        why = None
        if doc is None:
            why = f"活动建议不存在：{m.id!r}"
        elif doc["status"] != "pending":
            why = f"活动建议 {m.id!r} 已{'确认' if doc['status'] == 'confirmed' else '忽略'}，不能再配"
        elif m.taskId in doc.get("rejectedTaskIds", []):
            why = f"用户已经否掉过任务 {m.taskId!r}，不要再配同一个"
        elif doc["suggestion"].get("taskId") and doc["suggestion"].get("classifier") != "assistant":
            why = "这条已有分类规则给的任务，助理不覆盖"
        elif planner_service.get_task(m.taskId) is None:
            why = f"任务不存在：{m.taskId!r}"
        elif not repo.set_match(user, m.id, {"taskId": m.taskId, "confidence": float(m.confidence),
                                             "reason": m.reason, "classifier": "assistant"}):
            why = "这条建议刚被改动（确认 / 忽略 / 否），没有写入"
        if why:
            rejected.append({"index": index, "reason": why})
        else:
            matched += 1
    return {"matched": matched, "rejected": rejected}


def unmatch(authorization: str | None, sug_id: str, task_id: str | None) -> dict:
    """人说「否」：清掉建议的任务并记进 rejectedTaskIds；状态仍 pending。"""
    forbid_device_token(authorization)
    user = current_tenant()
    doc = _get(user, sug_id)
    current = doc["suggestion"].get("taskId")
    if doc["status"] != "pending":
        raise ConflictError(f"活动建议 {sug_id!r} 已处理，不能再否")
    if task_id is not None and current is not None and task_id != current:
        raise ConflictError("这条建议的任务已经变了，请刷新后再定")
    if current is not None and not repo.clear_match(user, sug_id, current):
        raise ConflictError("这条建议刚被改动，请刷新后再定")
    return {"id": sug_id, "status": "pending", "rejectedTaskIds": _get(user, sug_id).get("rejectedTaskIds", [])}


def list_presence(user: str, now: datetime, start: datetime, end: datetime) -> list[dict]:
    """v2.4 在场心跳的公开读路径（``views/lanes``），真身在 ``presence.py``。**不写**。"""
    return presence.list_spans(user, now, start, end)
