"""检测程序分类规则与 AI 草稿（契约 v2.6；形状、校验、状态码以 contracts/detector.rules.v1 为准）。

关键取舍：

- **整套替换 + 版本号**：规则是一整套（顺序决定哪条先命中），写入一律整套；``If-Match`` 带读到的 version，
  对不上 412——两个页面、或页面与「应用草稿」不会互相静默覆盖。
- **AI 只写草稿**：每租户至多一份草稿，建草稿顶掉旧的；应用是一次条件更新（version 与草稿 id 都对得上）。
- **设备令牌只读**：PUT、建草稿、应用、丢弃带 ``Authorization: Bearer`` 一律 403（同 detector.settings.v1）。
  MCP 走对内地址不带 Bearer，所以能建草稿；它按固定映射只调建草稿（mcp.tools.v1 v1.2）。
- 规则不是事实：不进台账、投影、导出、快照恢复。
"""

from __future__ import annotations

import json
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictStr, ValidationError, field_validator, model_validator

from ...tenant import current as current_tenant
from ..planner import service as planner_service
from . import repo
from .service import ForbiddenError, _is_device_token, _iso, re2_error

MAX_BODY = 256 * 1024
MAX_RULES = 500
MAX_ERRORS = 50
DRAFT_TTL = timedelta(days=14)
_VERSION = re.compile(r'^(?:W/)?(?:"([0-9]{1,15})"|([0-9]{1,15}))$')


class RulesError(Exception):
    """带状态码与附加字段的错误；main.py 映射成 ``{detail, **extra}``。"""

    def __init__(self, status: int, detail: str, **extra: Any):
        super().__init__(detail)
        self.status, self.detail, self.extra = status, detail, extra


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


_Regex = Annotated[StrictStr, Field(min_length=1, max_length=200)]


class Rule(_Strict):
    id: Annotated[StrictStr, Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")] | None = None
    app: _Regex | None = None
    title: _Regex | None = None
    # v1.1：目标是任务或项目，恰好一个（只到项目 = 记到该项目的「未分类」）
    taskId: Annotated[StrictStr, Field(min_length=1, max_length=128)] | None = None
    projectId: Annotated[StrictStr, Field(min_length=1, max_length=128)] | None = None
    confidence: float = Field(0.9, gt=0, le=1)
    note: Annotated[StrictStr, Field(max_length=120)] | None = None
    enabled: bool = True
    # v1.2：出处——谁写的、是不是服务端替 AI 直接写下的窗口规则（nexus-core v2.15）。不影响匹配；没有就不带这两个键
    author: Literal["assistant", "human"] | None = None
    auto: bool | None = None

    @field_validator("app", "title")
    @classmethod
    def _re2(cls, v: str | None) -> str | None:
        if v is not None and (err := re2_error(v)):
            raise ValueError(err)
        return v

    @model_validator(mode="after")
    def _one_of(self) -> Rule:
        if self.app is None and self.title is None:
            raise ValueError("app 与 title 至少写一个")
        if (self.taskId is None) == (self.projectId is None):
            raise ValueError("taskId 与 projectId 必须给一个、且只能给一个")
        return self


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(t: datetime) -> datetime:
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _forbid(authorization: str | None) -> None:
    if _is_device_token(authorization):
        raise ForbiddenError("设备令牌只能读分类规则；请在 Cockpit「AI助理 → 规则」登录后修改")


def _invalid(errors: list[dict]) -> RulesError:
    e = errors[0]
    where = "" if e["index"] is None else f"rules[{e['index']}]"
    if e["field"]:
        where = f"{where}.{e['field']}" if where else e["field"]
    return RulesError(422, f"{len(errors)} 处不合规，第一处：{where or '请求体'}：{e['message']}",
                      errors=errors[:MAX_ERRORS])


def _err(index: int | None, field: str | None, message: str) -> dict:
    return {"index": index, "field": field, "message": message}


def _json(body: bytes, allowed: set[str], required: set[str]) -> dict:
    if len(body) > MAX_BODY:
        raise RulesError(413, f"请求体超过 {MAX_BODY} 字节")
    try:
        raw = json.loads(body)
    except ValueError as exc:
        raise _invalid([_err(None, None, f"请求体不是合法 JSON：{exc}")]) from exc
    if not isinstance(raw, dict):
        raise _invalid([_err(None, None, "请求体须是 JSON 对象")])
    errors = [_err(None, k, "未知键") for k in sorted(set(raw) - allowed)]
    errors += [_err(None, k, "必填") for k in sorted(required - set(raw))]
    if errors:
        raise _invalid(errors)
    return raw


def _if_match(value: str | None) -> int:
    m = _VERSION.fullmatch((value or "").strip())
    if not m:
        raise RulesError(428, '须带 If-Match: "<version>"（先 GET 规则拿 version / ETag）')
    return int(m.group(1) or m.group(2))


def _missing_tasks(rules: list[dict]) -> list[dict]:
    """目标（任务；v1.1 起也可以是项目）已不在的规则。"""
    tasks = {t["id"] for t in planner_service.list_tasks()}
    projects = {p["id"] for p in planner_service.list_projects()}
    errors = []
    for i, r in enumerate(rules):
        if r.get("projectId"):
            if r["projectId"] not in projects:
                errors.append(_err(i, "projectId", f"项目不存在：{r['projectId']}"))
        elif r["taskId"] not in tasks:
            errors.append(_err(i, "taskId", f"任务不存在：{r['taskId']}"))
    return errors


def _validate(raw: Any) -> list[dict]:
    """规则数组 → 补齐后的完整规则；不合规收集全部错误（带下标）一次 422。"""
    if not isinstance(raw, list):
        raise _invalid([_err(None, "rules", "须是数组")])
    if len(raw) > MAX_RULES:
        raise _invalid([_err(None, "rules", f"最多 {MAX_RULES} 条，现在 {len(raw)} 条")])
    errors: list[dict] = []
    out: list[dict] = []
    seen: set[str] = set()
    for i, r in enumerate(raw):
        try:
            rule = Rule.model_validate(r).model_dump()
        except ValidationError as exc:
            errors += [_err(i, ".".join(str(p) for p in e["loc"]) or None, e["msg"]) for e in exc.errors()]
            continue
        for k in ("projectId", "author", "auto"):  # 没给就不带这个键：与 v1 存下的逐字节相同，草稿 diff 不误报「修改」
            if not rule[k]:
                del rule[k]
        if rule["id"] is not None:
            if rule["id"] in seen:
                errors.append(_err(i, "id", f"id 重复：{rule['id']}"))
            seen.add(rule["id"])
        out.append(rule)
    if not errors:
        errors = _missing_tasks(out)
    if errors:
        raise _invalid(errors)
    for rule in out:
        if rule["id"] is None:
            while (new := "r_" + secrets.token_hex(6)) in seen:  # 撞上已有的 id（几乎不可能）就再抽
                pass
            rule["id"] = new
            seen.add(new)
    return out


def _state(user: str) -> dict:
    """当前文档（没有 = 空）；过期草稿顺手清掉，当作没有。"""
    doc = repo.get_rules(user) or {"version": 0, "rules": []}
    d = doc.get("draft")
    if d and _aware(d["expiresAt"]) <= _now():
        repo.drop_draft(user, d["id"])
        doc.pop("draft")
    return doc


def _rules_out(doc: dict) -> dict:
    return {"version": doc.get("version", 0), "updatedAt": _iso(doc.get("updatedAt")), "rules": doc.get("rules", [])}


def _diff(current: list[dict], draft: list[dict]) -> dict:
    cur = {r["id"]: r for r in current}
    new = {r["id"]: r for r in draft}
    changed = [i for i in new if i in cur and cur[i] != new[i]]
    common_cur = [r["id"] for r in current if r["id"] in new]
    common_new = [r["id"] for r in draft if r["id"] in cur]
    return {"added": [i for i in new if i not in cur], "removed": [i for i in cur if i not in new],
            "changed": changed, "unchanged": len(common_cur) - len(changed), "reordered": common_cur != common_new}


def _draft_out(doc: dict) -> dict | None:
    d = doc.get("draft")
    if not d:
        return None
    return {"id": d["id"], "status": "pending", "author": d["author"], "summary": d["summary"],
            "createdAt": _iso(d["createdAt"]), "expiresAt": _iso(d["expiresAt"]),
            "baseVersion": d["baseVersion"], "currentVersion": doc.get("version", 0),
            "rules": d["rules"], "diff": _diff(doc.get("rules", []), d["rules"])}


# ── 端点 ───────────────────────────────────────────────────────────


def get_rules() -> dict:
    return _rules_out(_state(current_tenant()))


def put_rules(authorization: str | None, if_match: str | None, body: bytes) -> dict:
    _forbid(authorization)
    raw = _json(body, {"rules"}, {"rules"})
    version = _if_match(if_match)
    rules = _validate(raw["rules"])
    user = current_tenant()
    if (doc := repo.replace_rules(user, version, rules, _now())) is None:
        raise _stale(user, version)
    return _rules_out(doc)


def _stale(user: str, version: int) -> RulesError:
    cur = (repo.get_rules(user) or {}).get("version", 0)
    return RulesError(412, f"规则已被改过（你基于 v{version}，现在是 v{cur}）：请重新读取后再改", currentVersion=cur)


def create_draft(authorization: str | None, body: bytes) -> dict:
    _forbid(authorization)
    raw = _json(body, {"rules", "summary", "author"}, {"rules", "summary"})
    summary, author = raw["summary"], raw.get("author", "assistant")
    errors = []
    if not isinstance(summary, str) or not 1 <= len(summary) <= 500:
        errors.append(_err(None, "summary", "须是 1–500 个字符的字符串"))
    if author not in ("assistant", "human"):
        errors.append(_err(None, "author", '只能是 "assistant" 或 "human"'))
    if errors:
        raise _invalid(errors)
    rules = _validate(raw["rules"])
    user, now = current_tenant(), _now()
    doc = _state(user)
    return _draft_out(repo.put_draft(user, {"id": "drf_" + secrets.token_hex(6), "author": author, "summary": summary,
                                            "createdAt": now, "expiresAt": now + DRAFT_TTL,
                                            "baseVersion": doc.get("version", 0), "rules": rules}))


def current_draft() -> dict:
    return {"draft": _draft_out(_state(current_tenant()))}


def apply_draft(authorization: str | None, draft_id: str, if_match: str | None) -> dict:
    _forbid(authorization)
    version = _if_match(if_match)
    user = current_tenant()
    doc = _state(user)
    d = doc.get("draft")
    if not d or d["id"] != draft_id:
        raise RulesError(404, f"没有待应用的草稿 {draft_id[:80]!r}（已过期、已应用、已丢弃或被新草稿顶掉）")
    if doc.get("version", 0) != version:
        raise _stale(user, version)
    if errors := _missing_tasks(d["rules"]):  # 建草稿之后任务可能被删了
        raise _invalid(errors)
    if (done := repo.replace_rules(user, version, d["rules"], _now(), draft_id=draft_id)) is None:
        # 期间有人改了规则或换了草稿：按此刻的状态分辨
        again = _state(user)
        if (again.get("draft") or {}).get("id") != draft_id:
            raise RulesError(404, f"草稿 {draft_id[:80]!r} 刚被顶掉或丢弃")
        raise _stale(user, version)
    return _rules_out(done)


def discard_draft(authorization: str | None, draft_id: str) -> None:
    _forbid(authorization)
    repo.drop_draft(current_tenant(), draft_id)
