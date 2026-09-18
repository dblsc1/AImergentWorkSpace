"""planner 审计流水（契约 v1.6「planner 审计流水」节，PRD F-ACTOR-2）。

**红线：审计不是事件。** 记录落独立集合 `planner_audit`，与 `events` 零交集
——不进事件信封、不进投影、不进重建链路、不被 `/events` 或 `/export` 返回。
`events` 是跨模块开放标准（学生按它写脚本，信封只增不改不删）；
审计是本模块的运维追溯（字段会随需求长）。把运维记录塞进公开事实流，等于让
"我们内部想多记一列"变成"所有学生的脚本要改"；反过来让审计迁就信封的不变性，
则第一次要加字段时就会有人去改信封。**混在一起会同时毁掉两边。**

**三种 outcome 都记**：`applied`（库已改）、`denied`（被 v1.6 设防拒绝）、
`failed`（过了设防但被业务校验拒）。只记成功的审计等于把攻击痕迹和
"批量写崩在第几步"一起丢掉——而那两件正是这张表存在的理由。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from . import repo
from .errors import InvalidInputError

OP_CREATE = "create"
OP_UPDATE = "update"
OP_DELETE = "delete"
VALID_OPS = (OP_CREATE, OP_UPDATE, OP_DELETE)

OUTCOME_APPLIED = "applied"
OUTCOME_DENIED = "denied"
OUTCOME_FAILED = "failed"
VALID_OUTCOMES = (OUTCOME_APPLIED, OUTCOME_DENIED, OUTCOME_FAILED)

VALID_ACTORS = ("human", "ai")

#: 读端 `limit` 的默认值与上限（契约 v1.6）。
DEFAULT_LIMIT = 50
MAX_LIMIT = 500

#: `changes` 摘要的截断阈值——审计要能读，不是要能装下一切。
MAX_STR_LEN = 200
MAX_ITEMS = 20

#: 键名里出现这些片段就不记值。**审计流水本身不得成为密钥泄漏面**：
#: 今天的写入口不收凭据字段，但"今天不收"不是不变量，加个字段就破了。
_SECRET_HINTS = ("token", "secret", "password", "passwd", "credential")
_REDACTED = "<已脱敏>"


def _truncate(value: object) -> object:
    if isinstance(value, str) and len(value) > MAX_STR_LEN:
        return value[:MAX_STR_LEN] + "…"
    if isinstance(value, list) and len(value) > MAX_ITEMS:
        return [_truncate(item) for item in value[:MAX_ITEMS]] + ["…"]
    if isinstance(value, list):
        return [_truncate(item) for item in value]
    if isinstance(value, dict):
        return summarize_changes(value)
    return value


def summarize_changes(changes: dict | None) -> dict:
    """变更摘要：截断长值、脱敏疑似密钥键。**摘要不是快照**。"""
    summary: dict = {}
    for key, value in (changes or {}).items():
        lowered = str(key).lower()
        if any(hint in lowered for hint in _SECRET_HINTS):
            summary[key] = _REDACTED
            continue
        summary[key] = _truncate(value)
    return summary


def _now_iso() -> str:
    """服务端盖章的 UTC 时刻（同 events 的 `recordedAt` 纪律：客户端说了不算）。"""
    return datetime.now(timezone.utc).isoformat()


def record(
    *,
    actor: str,
    source: str,
    op: str,
    object_type: str,
    object_id: str | None,
    high_risk: bool,
    outcome: str,
    changes: dict | None = None,
    reason: str | None = None,
) -> dict:
    """追加一条审计记录，返回落库的那份（调用方一般不需要它，测试需要）。

    参数用关键字传：这是一张十列的表，位置参数迟早会串位，而串位后的审计
    **看起来仍然正常**——比不记更糟。
    """
    if op not in VALID_OPS:
        raise ValueError(f"审计 op 非法：{op!r}，合法取值 {'/'.join(VALID_OPS)}")
    if outcome not in VALID_OUTCOMES:
        raise ValueError(
            f"审计 outcome 非法：{outcome!r}，合法取值 {'/'.join(VALID_OUTCOMES)}"
        )
    entry = {
        "auditId": f"aud_{uuid.uuid4().hex[:12]}",
        "seq": repo.next_audit_seq(),
        "at": _now_iso(),
        "actor": actor,
        "source": source,
        "op": op,
        "objectType": object_type,
        "objectId": object_id,
        "highRisk": bool(high_risk),
        "outcome": outcome,
        "changes": summarize_changes(changes),
        "reason": reason,
    }
    repo.append_audit(entry)
    return entry


# ------------------------------------------------------------------ 读端


def _check_enum(value: str | None, valid: tuple[str, ...], name: str) -> str | None:
    if value is None:
        return None
    if value not in valid:
        raise InvalidInputError(f"{name} 非法：{value!r}，合法取值 {'/'.join(valid)}")
    return value


def query(
    limit: int = DEFAULT_LIMIT,
    object_id: str | None = None,
    actor: str | None = None,
    outcome: str | None = None,
) -> dict:
    """`GET /api/core/planner/audit` 的实现（契约 v1.6）。`seq` 降序。"""
    if limit <= 0 or limit > MAX_LIMIT:
        raise InvalidInputError(
            f"limit 非法：{limit}，取值范围 1–{MAX_LIMIT}（契约 v1.6 审计读端）"
        )
    _check_enum(actor, VALID_ACTORS, "actor")
    _check_enum(outcome, VALID_OUTCOMES, "outcome")

    selector: dict = {}
    if object_id is not None:
        selector["objectId"] = object_id
    if actor is not None:
        selector["actor"] = actor
    if outcome is not None:
        selector["outcome"] = outcome
    return {
        "total": repo.count_audit(selector),
        "items": repo.list_audit(selector, limit),
    }
