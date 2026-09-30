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
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, StrictInt, StrictStr, ValidationError, field_validator

from ... import config, timeutil
from ...tenant import current as current_tenant
from ..events import service as events_service
from ..planner import service as planner_service
from ..planner.errors import InvalidInputError, NotFoundError
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


class _Suggestion(BaseModel):
    taskId: Annotated[StrictStr, Field(min_length=1, max_length=128)] | None
    confidence: Annotated[float, Field(ge=0, le=1, strict=True)]
    reason: StrictStr
    classifier: Literal["rules", "service"]

    @field_validator("reason")
    @classmethod
    def _reason_bytes(cls, v: str) -> str:
        # 契约按字节限长：检测程序按 UTF-8 字节截断，中文一字三字节，按字符数会放进 600 字节
        if len(v.encode("utf-8")) > 200:
            raise ValueError("reason 超过 200 字节")
        return v


class _Segment(BaseModel):
    startAt: Annotated[StrictStr, Field(max_length=64)]
    endAt: Annotated[StrictStr, Field(max_length=64)]
    durationSeconds: StrictInt
    # 超长不拒、截断（按码点）：标题是展示用的，为几个多余字符丢掉一整段真实活动不划算
    app: Annotated[StrictStr, Field(min_length=1)]
    title: StrictStr
    suggestion: _Suggestion


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
            "suggestion": suggestion, "status": "pending", "receivedAt": now,
        }
        if repo.insert_if_absent(doc):
            accepted += 1
        else:
            duplicates += 1
    return {"accepted": accepted, "duplicates": duplicates, "rejected": rejected}


_ITEM_KEYS = ("id", "deviceId", "startAt", "endAt", "durationSeconds", "app", "title", "suggestion", "status")


def list_suggestions(status: str, limit: int, offset: int) -> dict:
    user = current_tenant()
    _purge(user, _now())
    limit = _DEFAULT_LIMIT if limit <= 0 else min(limit, _MAX_LIMIT)  # 同档案读端口径
    total, docs = repo.page(user, status, limit, max(offset, 0))
    return {"total": total, "items": [{k: d[k] for k in _ITEM_KEYS} for d in docs]}


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
    task_id = task_id or doc["suggestion"].get("taskId")
    if not task_id:
        raise InvalidInputError("没有可确认的任务：请求体与建议里都没有 taskId，请先选一个任务")
    # 先占位再写事实：pending→confirmed 是条件更新，与忽略（同样只从 pending 转）二选一，
    # 不会出现「忽略回了 200，事实却照样落库」。占位后崩在写事实之前 → 状态已确认、台账没有，
    # 重试走到这里（占位不中但状态是 confirmed）照样补写，防重键兜底不重。
    claimed = repo.set_status(user, sug_id, "confirmed", _now(), only_from="pending")
    if not claimed and _get(user, sug_id)["status"] == "dismissed":
        raise ConflictError(f"活动建议 {sug_id!r} 已忽略，不能再确认")
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


def list_presence(user: str, now: datetime, start: datetime, end: datetime) -> list[dict]:
    """v2.4 在场心跳的公开读路径（``views/lanes``），真身在 ``presence.py``。**不写**。"""
    return presence.list_spans(user, now, start, end)
