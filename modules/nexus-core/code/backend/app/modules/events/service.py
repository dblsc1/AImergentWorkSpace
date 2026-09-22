"""事实唯一写入口：校验 → 盖 ``recordedAt`` → 防重 → 落库 → 触发 projector。

全系统只有这一条写事件的路——timer.stop 也从这里走，
不许绕过校验与防重直写 ``events`` 集合。**唯一例外**是快照恢复的
``restore_bulk``（契约 v1.9）：它搬的是另一个实例的同一份台账，信封校验挪到
``check_restore_envelopes`` 整批先过，防重照旧走唯一索引。

四条规范性语义（contract.md IngestOut，逐条对应实现）：

1. 防重按 ``dedupeKey`` 不是 ``id`` —— 见 ``repo.py`` 的唯一索引。
2. 重复不是错误 —— 命中防重计入 ``duplicate``，HTTP 层照样 200。
3. ``recordedAt`` 服务端盖章 —— 客户端填了也**覆盖**（B6）；``time`` 用客户端的。
4. 部分失败不整批回滚 —— 校验不过的进 ``rejected``，合法的照常落库。

未知 ``type`` 静默忽略（B8/Postel）：**照常落库**（它是别人订阅的开放标准），
只是 DISPATCH 表没有它的 handler，投影不动。

档案读端（``list_events``，contract.md v0.6）与全量读出（``iter_all_events``，
契约「投影重建」v0.9）是本文件仅有的两个读函数：只读、不碰 ``ingest``、
不碰 DISPATCH 表——人类要求「events 不准动」指的是写入侧。
"""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import ValidationError

from ..projector import registry
from . import repo
from .schemas import Envelope, IngestOut, RejectedItem

_ARCHIVE_DEFAULT_LIMIT = 100
_ARCHIVE_MAX_LIMIT = 1000


class InvalidQueryError(ValueError):
    """档案读端的 ``from``/``to`` 不是合法 ISO8601 → 400。消息必须指名道姓。"""


def _now_iso() -> str:
    """服务端时钟，ISO8601 带时区。``recordedAt`` 的唯一来源。"""
    return datetime.now(timezone.utc).isoformat()


def _reason(exc: ValidationError) -> str:
    """把 pydantic 错误压成一行人话：缺什么、哪个字段错在哪。"""
    parts = []
    for err in exc.errors()[:3]:
        loc = ".".join(str(p) for p in err["loc"]) or "<root>"
        if err["type"] == "missing":
            parts.append(f"缺少必填字段 {loc}")
        else:
            parts.append(f"{loc}: {err['msg']}")
    return "；".join(parts)


def ingest(payload: dict | list) -> IngestOut:
    """单条或数组。返回 IngestOut；HTTP 状态码永远由 router 给 200——
    拒绝逐条写进 ``rejected``，重复逐条计入 ``duplicate``，都不是 4xx。
    """
    items = payload if isinstance(payload, list) else [payload]
    accepted = 0
    duplicate = 0
    rejected: list[RejectedItem] = []

    for index, raw in enumerate(items):
        try:
            envelope = Envelope.model_validate(raw)
        except ValidationError as exc:
            rejected.append(RejectedItem(index=index, reason=_reason(exc)))
            continue

        doc = envelope.model_dump()  # extra="allow"：未知字段原样保留（B9）
        doc["recordedAt"] = _now_iso()  # 服务端盖章；客户端给的值在这里被覆盖（B6）

        if repo.append_if_absent(doc):
            accepted += 1
            # 落库成功后**同请求内**按 DISPATCH 表更新投影（contract.md）。
            # handler 幂等、只写自己的投影集合、禁止发新事件。
            registry.dispatch(doc)
        else:
            duplicate += 1

    return IngestOut(accepted=accepted, duplicate=duplicate, rejected=rejected)


def find_by_dedupe(user: str, source: str, dedupe_key: str) -> dict | None:
    """按 ``(user, source, dedupeKey)`` 取回已落库的那一条——供调用方在
    ``ingest`` 命中防重后回显「原来那条」（如 timer.backfill 的
    ``duplicate:true`` 响应）。**只读**，不碰 ``ingest``、不碰 DISPATCH 表。

    跨子边界只准调本文件的公开函数：本函数是 ``repo.
    find_by_dedupe`` 的窄封装，理由与 ``list_events``/``iter_all_events``
    这两个既有只读函数一致——``events/repo.py`` 是唯一碰 mongo 的地方。
    """
    return repo.find_by_dedupe(user, source, dedupe_key)


# ------------------------------------------------------------- 档案读端（v0.6）


def _parse_bound(value: str, field: str) -> datetime:
    """把 ``from``/``to`` 解析成带时区的 datetime；裸日期/裸时间戳按 UTC 处理
    （信封本身的 ``time`` 强制带时区，见 schemas.py，但查询边界是用户手填，
    不能同等要求，所以在这里兜底而不是直接拒绝）。
    """
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise InvalidQueryError(f"{field} 不是合法 ISO8601：{value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _event_time(doc: dict) -> datetime:
    """事件的 ``time`` 在写入时已由信封校验保证带时区（yq-event/v1 §2），
    这里只解析，不再校验——档案里不会有不合规的 time。
    """
    return datetime.fromisoformat(doc["time"])


def _normalize_limit(limit: int) -> int:
    """默认 100、上限 1000（contract.md「档案读端」表）。非正数落回默认值——
    这是「limit 没给 / 给了 0 或负数」时唯一合理的读法，不是校验失败，不必 400。
    """
    if limit <= 0:
        return _ARCHIVE_DEFAULT_LIMIT
    return min(limit, _ARCHIVE_MAX_LIMIT)


def list_events(
    type_: str | None = None,
    from_: str | None = None,
    to: str | None = None,
    limit: int = _ARCHIVE_DEFAULT_LIMIT,
    offset: int = 0,
) -> tuple[int, list[dict]]:
    """``GET /api/core/events`` 档案读端（contract.md v0.6）。

    只读，不改变事实的产生方式：不碰 ``ingest``、不碰 DISPATCH 表。
    按 ``time`` 倒序（最近的在前，R7）；``total`` 是过滤后、分页前的总数（R9 空结果 total=0）。
    **不在这里 join 任务/项目/分区名字**——事件里只有 opaque id 是有意设计（R8），
    消费方拿 id 去 planner 查当前名字。
    """
    docs = repo.query_events(type_)

    lo = _parse_bound(from_, "from") if from_ else None
    hi = _parse_bound(to, "to") if to else None
    if lo is not None or hi is not None:
        docs = [
            d
            for d in docs
            if (lo is None or _event_time(d) >= lo) and (hi is None or _event_time(d) <= hi)
        ]

    docs.sort(key=_event_time, reverse=True)  # R7：按 time 倒序

    total = len(docs)
    offset = max(offset, 0)
    limit = _normalize_limit(limit)
    return total, docs[offset : offset + limit]


# ------------------------------------------------------------- 投影重建（v0.9）


def iter_all_events() -> list[dict]:
    """全量、不分页地读出 ``events`` 集合——供 ``projector.rebuild`` 使用。

    与 ``list_events`` 不同：那个是给 HTTP 档案端点用的，默认 100 条、上限 1000 条
    （契约「档案读端」）；重建必须看到**全部**历史事实，用 1000 的上限截断会
    重演 v0.8 的坑（新投影错过部分历史）。**只读**，不改变 events 集合，
    不触发 DISPATCH（谁来重放、重放几遍是调用方 ``rebuild`` 的事，不是本函数的事）。

    跨子边界只准调 ``service.py`` 的公开函数（rules.md §7.3 红线 2）——
    ``projector/rebuild.py`` 拿事实走这里，不许直接 import ``events/repo.py``。
    """
    return repo.query_events()


# ------------------------------------------------------------- 快照恢复（v1.9）


def check_restore_envelopes(docs: list[dict]) -> list[RejectedItem]:
    """快照恢复的预检：逐条过 ``Envelope`` + 快照内防重键不重复。**只读**。

    与 ``ingest`` 用同一个信封模型——恢复不因为「搬的是自己的台账」就放宽信封。
    不同的是结果怎么用：``ingest`` 逐条收、坏的进 ``rejected``；恢复是整份搬家，
    有一条不合格调用方就整批 400、一条都不写。
    """
    rejected: list[RejectedItem] = []
    seen: set[tuple[str, str, str]] = set()
    for index, raw in enumerate(docs):
        try:
            Envelope.model_validate(raw)
        except ValidationError as exc:
            rejected.append(RejectedItem(index=index, reason=_reason(exc)))
            continue
        key = (raw["user"], raw["source"], raw["dedupeKey"])
        if key in seen:
            rejected.append(RejectedItem(index=index, reason=f"防重键 {key} 在快照里重复出现"))
        seen.add(key)
    return rejected


def restore_bulk(docs: list[dict]) -> int:
    """快照恢复：信封**原样**落台账，返回新写入的条数。调用方须先过
    ``check_restore_envelopes``。

    与 ``ingest`` 有意不同的两处：
    - **不重盖 ``recordedAt``**——盖章时刻是原实例收到它的时刻，重盖等于改写历史。
    - **不逐条 dispatch**——投影由调用方落完之后整体 ``rebuild``，与「投影重建」
      同一个口径。
    """
    return sum(repo.append_if_absent(doc) for doc in docs)
