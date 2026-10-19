"""检测程序分类规则与 AI 草稿（契约 v2.6；形状、校验、状态码以 contracts/detector.rules.v1 为准）。

关键取舍：

- **整套替换 + 版本号**：规则是一整套（顺序决定哪条先命中），写入一律整套；``If-Match`` 带读到的 version，
  对不上 412——两个页面、或页面与「应用草稿」不会互相静默覆盖。
- **AI 只写草稿**：每租户至多一份草稿，建草稿顶掉旧的；应用是一次条件更新（version 与草稿 id 都对得上）。
- **v2.29 例外**：账号开关 ``aiDirectRules`` 开着时，write 范围的非人调用方可经 ``ai-apply`` 直接写；撤回只许人。
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

from ...scope import caller
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
    # v1.3：AI 经 ai-apply 直接写下 / 改过这条的时间；只由服务端盖章（客户端给的会被覆盖或丢掉，见 _stamp）
    aiAt: StrictStr | None = None
    # v1.4：mode "ai" = 命中的窗口不硬分，交给 AI 软分类；这种规则不带 taskId / projectId。没给 = 普通规则，不带这个键
    mode: Literal["ai"] | None = None

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
        if self.mode == "ai":
            if self.taskId is not None or self.projectId is not None:
                raise ValueError('mode 是 "ai" 的规则不能带 taskId / projectId（窗口交给 AI 判断，不指定去向）')
        elif (self.taskId is None) == (self.projectId is None):
            raise ValueError("taskId 与 projectId 必须给一个、且只能给一个")
        return self


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(t: datetime) -> datetime:
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _human(authorization: str | None) -> None:
    """只许人：除了 Bearer，还拒绝带范围头 / 匿名标记的调用方（同 activity.ignore_router.is_human 的判据）。"""
    _forbid(authorization)
    c = caller()
    if c.scope is not None or c.anonymous:
        raise ForbiddenError("只有登录的人能做这件事；带范围的令牌和匿名上报都不行")


def _forbid(authorization: str | None) -> None:
    if _is_device_token(authorization):
        raise ForbiddenError("设备令牌只能读分类规则；请在 Cockpit「自动记录 → 分类规则」登录后修改")


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
        if r.get("mode") == "ai":
            continue
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
        for k in ("projectId", "author", "auto", "aiAt", "mode"):  # 没给就不带这个键：与 v1 存下的逐字节相同，草稿 diff 不误报「修改」
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


_AI_KEYS = ("aiAt",)


def _core(r: dict) -> dict:
    return {k: v for k, v in r.items() if k not in _AI_KEYS}


def _stamp(new: list[dict], old: list[dict], at: str | None) -> list[dict]:
    """盖 AI 章：与旧规则（按 id）相同 = 沿用旧规则（含它的章）；否则丢掉客户端自带的章，at 给了（AI 写）就盖新章。
    AI 写（at 给了）时「相同」不看出处键 author / auto（那是服务端管的，AI 重发一条没动的规则不算改动）。"""
    prev = {r["id"]: r for r in old}
    skip = (*_AI_KEYS, "author", "auto") if at is not None else _AI_KEYS
    out = []
    for r in new:
        o = prev.get(r["id"])
        if o is not None and {k: v for k, v in o.items() if k not in skip} == {k: v for k, v in r.items() if k not in skip}:
            out.append(o if at is not None else {**_core(r), **{k: o[k] for k in _AI_KEYS if k in o}})
        elif at is not None:  # AI 新增 / 改动的：盖章，author 固定 assistant，客户端自带的 auto 丢掉
            out.append({**{k: v for k, v in _core(r).items() if k != "auto"}, "aiAt": at, "author": "assistant"})
        else:  # 人写的：新的或改过的规则，出处键（author / auto）一并清掉——人接手了它（v2.34：否则被人改成 mode ai 的规则仍算「AI 写的」）
            out.append({k: v for k, v in _core(r).items() if k not in ("author", "auto")})
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
    snaps = doc.get("snapshots") or []
    return {"version": doc.get("version", 0), "updatedAt": _iso(doc.get("updatedAt")), "rules": doc.get("rules", []),
            "aiDirectRules": doc.get("aiDirectRules") is True, "snapshotCount": len(snaps),
            "snapshotAt": _iso(snaps[-1]["at"]) if snaps else None}


def _diff(current: list[dict], draft: list[dict]) -> dict:
    cur = {r["id"]: _core(r) for r in current}  # AI 章不算改动
    new = {r["id"]: _core(r) for r in draft}
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
    rules = _stamp(rules, (repo.get_rules(user) or {}).get("rules", []), None)  # 人改过的规则不带 AI 章
    # 人动过规则：撤回历史作废（snapshots=[]），撤回永远不会丢掉人的改动
    if (doc := repo.replace_rules(user, version, rules, _now(), snapshots=[])) is None:
        raise _stale(user, version)
    return _rules_out(doc)


def _stale(user: str, version: int) -> RulesError:
    cur = (repo.get_rules(user) or {}).get("version", 0)
    return RulesError(412, f"规则已被改过（你基于 v{version}，现在是 v{cur}）：请重新读取后再改", currentVersion=cur)


def _draft_body(body: bytes) -> tuple[list[dict], str, str]:
    """建草稿与 ai-apply 共用的请求体解析 + 校验 → (规则, summary, author)。"""
    raw = _json(body, {"rules", "summary", "author"}, {"rules", "summary"})
    summary, author = raw["summary"], raw.get("author", "assistant")
    errors = []
    if not isinstance(summary, str) or not 1 <= len(summary) <= 500:
        errors.append(_err(None, "summary", "须是 1–500 个字符的字符串"))
    if author not in ("assistant", "human"):
        errors.append(_err(None, "author", '只能是 "assistant" 或 "human"'))
    if errors:
        raise _invalid(errors)
    return _validate(raw["rules"]), summary, author


def create_draft(authorization: str | None, body: bytes) -> dict:
    _forbid(authorization)
    rules, summary, author = _draft_body(body)
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
    if (done := repo.replace_rules(user, version, _stamp(d["rules"], doc.get("rules", []), None), _now(),
                                   draft_id=draft_id, snapshots=[])) is None:
        # 期间有人改了规则或换了草稿：按此刻的状态分辨
        again = _state(user)
        if (again.get("draft") or {}).get("id") != draft_id:
            raise RulesError(404, f"草稿 {draft_id[:80]!r} 刚被顶掉或丢弃")
        raise _stale(user, version)
    return _rules_out(done)


def discard_draft(authorization: str | None, draft_id: str) -> None:
    _forbid(authorization)
    repo.drop_draft(current_tenant(), draft_id)


# ── v2.29：AI 直接写规则（账号开关 aiDirectRules 开着才行）与撤回 ──
SWITCH_OFF = "账号没有开启「允许 AI 直接改分类规则」；请用户在 Cockpit「自动记录 → 分类规则」页打开它，或先建草稿（propose_detector_rules）"
MAX_SNAPSHOTS = 5


def _switch_off() -> RulesError:
    return RulesError(403, SWITCH_OFF, reason="aiDirectOff")  # reason：机器可读，MCP 只认它，不从裸 403 推断


def set_ai_direct(authorization: str | None, body: bytes) -> dict:
    """只许人：打开 / 关闭账号开关。体 {"enabled": 布尔}。"""
    _human(authorization)
    raw = _json(body, {"enabled"}, {"enabled"})
    if not isinstance(raw["enabled"], bool):
        raise _invalid([_err(None, "enabled", "须是布尔")])
    user = current_tenant()
    repo.set_ai_direct(user, raw["enabled"])
    return _rules_out(_state(user))


def _human_ai_lost(old: list[dict], new: list[dict]) -> list[str]:
    """人写的（author 不是 assistant；人写入时出处键已清）mode "ai" 规则，在新的一套里不在、被改动、
    或前面多了/换了别的固定目标规则（先命中先赢，会把它架空）的 id。前面只许是：它在旧的一套里本来就在它前面且没改的规则，或 mode ai 的规则。"""
    cur, before = {r["id"]: r for r in new}, {}
    for i, r in enumerate(old):
        before[r["id"]] = {x["id"] for x in old[:i]}
    lost = []
    for r in old:
        if r.get("mode") != "ai" or r.get("author") == "assistant":
            continue
        if cur.get(r["id"]) != r:
            lost.append(r["id"])
            continue
        pos = new.index(r)
        if any(x.get("mode") != "ai" and not (x["id"] in before[r["id"]] and x == next(o for o in old if o["id"] == x["id"]))
               for x in new[:pos]):
            lost.append(r["id"])
    return lost


def ai_apply(authorization: str | None, body: bytes) -> dict:
    """非人的调用方（write 范围的令牌、MCP 对内直连）整套写入、立即生效；先把旧的一套压进快照栈。
    范围门（read / report 的 POST 进不来）在 scope.py；这里管开关与校验。body 里的 author 只校验、不采用。"""
    if _is_device_token(authorization) and caller().scope != "write":  # 纵深：令牌必须明确是 write（普通 403，不带 reason）
        raise RulesError(403, "只有 write 范围的令牌能直接改分类规则")
    user = current_tenant()
    if _state(user).get("aiDirectRules") is not True:
        raise _switch_off()
    rules, summary, _author = _draft_body(body)
    doc, now = _state(user), _now()
    old, version = doc.get("rules", []), doc.get("version", 0)
    if lost := _human_ai_lost(old, _stamp(rules, old, "x")):  # v1.4：人写的「交给 AI 判断」规则 AI 不能删 / 改
        raise _invalid([_err(None, "rules", f"人写的「交给 AI 判断」规则不能被 AI 删除、改动，前面也不能插入或挪上来别的固定目标规则（会把它架空）：{', '.join(lost)}")])
    if _stamp(rules, old, "x") == old:  # 与现有规则（除章外）一模一样：什么都不做，不占快照、不涨版本
        return _rules_out(doc)
    snaps = [*(doc.get("snapshots") or []), {"rules": old, "version": version, "at": now, "summary": summary}]
    if len(snaps) > MAX_SNAPSHOTS:
        del snaps[1]  # 满了丢第二老的：最老的（AI 动手之前的那套）永远留着，撤到底能回到原样
    # 条件更新同时要求 version 没变、开关仍开着：检查之后开关被关掉的并发请求赢
    if (done := repo.replace_rules(user, version, _stamp(rules, old, _iso(now)), now, snapshots=snaps,
                                   require_ai_direct=True)) is None:
        if _state(user).get("aiDirectRules") is not True:
            raise _switch_off()
        raise _stale(user, version)
    return _rules_out(done)


def ai_revert(authorization: str | None, if_match: str | None) -> dict:
    """只许人：回到最近一个快照（一步一撤，用掉它）。须带 If-Match（同 PUT rules）；没有快照 404。"""
    _human(authorization)
    expected = _if_match(if_match)
    user = current_tenant()
    doc = _state(user)
    version = doc.get("version", 0)
    if version != expected:
        raise _stale(user, expected)
    snaps = doc.get("snapshots") or []
    if not snaps:
        raise RulesError(404, "没有可撤回的 AI 改动")
    if (done := repo.replace_rules(user, version, snaps[-1]["rules"], _now(), snapshots=snaps[:-1])) is None:
        raise _stale(user, version)
    return _rules_out(done)
