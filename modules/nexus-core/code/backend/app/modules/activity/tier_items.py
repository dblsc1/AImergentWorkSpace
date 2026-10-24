"""v2.38「五」AI 提议第二级以上的分类：``windowRule``（第一级规则增改删）、``blockRule``（第二级规则增改删）、``blockAction``（对某天某块 assign / split / generic / ignore）。

存在 ``activity_new_items``（同 v2.30 / v2.36）；提交、去重、不要过的 409、上限 429 复用 ``new_items._put_edit``。
批准（只许人）与直接施加走同一份 ``apply``：块的写走 ``blocks_write`` 的同一条代码路径，规则集走 ``replace_rules``（服务端代 If-Match）。
契约待定：窗口规则 ``add`` 加到规则集末尾；``edit`` 没给的字段保留原值；只能改第一级规则（``kind: "block"``），``delete`` 只认 ``rule.id``。
契约待定：``blockTitle`` = 块名（块没有单独的标题）；``blockAction`` 的 ``generic`` 是把这个块名标成通用窗口。
契约待定：批准失败回到 ``pending`` 时，批准时的更正一并撤销。
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import date as Date, datetime, timezone

from ...tenant import current as current_tenant
from ..detector import repo as detector_repo
from ..detector import rules as detector_rules
from ..planner import service as planner_service
from ..planner.errors import InvalidInputError, NotFoundError, UnprocessableError
from . import blocks, blocks_repo, blocks_write, block_rules, direct_log, new_items, proposals, reconcile
from .proposals import ConflictError

_PREFIX = {"windowRule": "nw_", "blockRule": "nb_", "blockAction": "na_"}
_TRIES = 3
_FIELDS = {"windowRule": ("op", "rule"), "blockRule": ("op", "blockKey", "taskId", "projectId"),
           "blockAction": ("action", "date", "blockKey", "taskId", "projectId", "parts")}


def _iso(t: datetime) -> str:
    return t.replace(tzinfo=timezone.utc).isoformat()


def mask_rule(rule: dict | None, human: bool) -> dict | None:
    """窗口标题的正则可能带隐私：非人调用方拿不到 ``title``，只拿 ``hasTitle``（同 v2.36 的 ignore）。"""
    if rule is None or human or "title" not in rule:
        return rule
    return {**{k: v for k, v in rule.items() if k != "title"}, "hasTitle": bool(rule["title"])}


def view(d: dict, human: bool = True) -> dict:
    out = {"id": d["id"], "kind": d["kind"], "status": d["status"], "createdAt": _iso(d["createdAt"]), "reason": d["reason"],
           "corrected": bool(d.get("corrected")), **{k: d.get(k) for k in _FIELDS[d["kind"]]}}
    if d["kind"] == "windowRule":
        out["rule"] = mask_rule(d["rule"], human)
    if d["kind"] == "windowRule":
        out["createdId"] = d.get("createdId")
    if d["kind"] == "blockAction":
        out["blockTitle"] = d["blockKey"]
    return out


# ── 校验 ──

def _check_rule(rule: dict, exc=InvalidInputError) -> dict:
    try:
        return detector_rules._validate([rule])[0]  # noqa: SLF001
    except detector_rules.RulesError as e:
        raise exc(f"规则不合规：{e.detail} {e.extra.get('errors', '')}") from None


def _one_target(task_id, project_id) -> None:
    if (task_id is None) == (project_id is None):
        raise InvalidInputError("taskId 与 projectId 须恰好给一个")
    try:
        block_rules.check_target(task_id, project_id)
    except NotFoundError as e:
        raise InvalidInputError(str(e)) from None


def _id(kind: str, params: dict) -> str:
    return _PREFIX[kind] + hashlib.sha256(json.dumps({"k": kind, **params}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:20]


def _submit(kind: str, params: dict, reason: str, direct: bool, human: bool = True) -> dict:
    user, item_id = current_tenant(), _id(kind, params)
    if direct:
        d = new_items._get(user, item_id)  # noqa: SLF001
        if d and d["status"] == "rejected":
            raise ConflictError("用户已经否掉过这个提议，不要再提")

        if kind == "blockAction" and params["action"] == "assign":  # v2.39：同目标的 assign 是空操作，不写日志
            item = _same_target(user, params)
            if item is not None:
                return {"applied": False, "directLogId": None, "result": item}

        def go():
            return apply(kind, params, "ai-direct", "system")

        def finish():  # 同样的待批准提议不能再被批一次：落地后记成已批准
            new_items._col().update_many({"user": user, "id": item_id, "status": "pending"},  # noqa: SLF001
                                         {"$set": {"status": "accepted", "decidedAt": datetime.now(timezone.utc), "applyClaimedAt": None}})

        return direct_log.run(kind, {**params, "reason": new_items._clean_reason(reason)}, go, finish)  # noqa: SLF001
    d = new_items._put_edit(user, kind, item_id, {**params, "corrected": False, "createdId": None}, new_items._clean_reason(reason))  # noqa: SLF001
    return view(d, human)


def propose_window_rule(op: str, rule: dict, reason: str, direct: bool, human: bool = True) -> dict:
    if direct:
        direct_log.gate("windowsClassify")
    rule = {k: v.strip() for k, v in rule.items() if v is not None}
    old = (detector_repo.get_rules(current_tenant()) or {}).get("rules", [])
    if op == "add":
        if "id" in rule:
            raise InvalidInputError("add 不带 rule.id")
        if "block" not in rule:
            raise InvalidInputError("add 须给 rule.block")
        _check_rule(_new_rule(rule))
    else:
        cur = next((r for r in old if r["id"] == rule.get("id")), None)
        if cur is None:
            raise InvalidInputError(f"规则不存在：{rule.get('id')!r}（edit / delete 须给已有规则的 rule.id）")
        if cur.get("kind") != "block":
            raise InvalidInputError("只能改第一级规则（kind 是 block 的）")
        if op == "delete":
            rule = {"id": rule["id"]}
        else:
            if len(rule) < 2:
                raise InvalidInputError("edit 须至少给一个要改的字段（app / title / block）")
            _check_rule({**cur, **rule})
    return _submit("windowRule", {"op": op, "rule": rule}, reason, direct, human)


def propose_block_rule(op: str, key: str, task_id, project_id, reason: str, direct: bool) -> dict:
    if direct:
        direct_log.gate("windowsClassify")
    key = key.strip()
    if not key:
        raise InvalidInputError("blockKey 不能为空")
    exists = key in blocks_repo.rules_by_keys(current_tenant(), [key])
    if op == "delete":
        if task_id is not None or project_id is not None:
            raise InvalidInputError("delete 不带目标")
        if not exists:
            raise InvalidInputError(f"没有这个块名的第二级规则：{key!r}")
    else:
        _one_target(task_id, project_id)
        if op == "edit" and not exists:
            raise InvalidInputError(f"没有这个块名的第二级规则：{key!r}（要新建请用 add）")
    return _submit("blockRule", {"op": op, "blockKey": key, "taskId": task_id, "projectId": project_id}, reason, direct)


def propose_block_action(action: str, date: Date, key: str, task_id, project_id, parts, forever: bool, reason: str, direct: bool) -> dict:
    if direct:
        direct_log.gate("windowsClassify")
    user = current_tenant()
    if forever:
        raise InvalidInputError("AI 提议的 ignore 不许 forever")
    if action == "assign":
        _one_target(task_id, project_id)
    elif task_id is not None or project_id is not None:
        raise InvalidInputError("只有 assign 带 taskId / projectId（split 的目标写在 parts 里）")
    if (action == "split") != (parts is not None):
        raise InvalidInputError("只有 split 带 parts，且 split 必须带")
    blocks.check_writable(date)
    if blocks.block_item(user, date, key)["records"] == 0:
        raise InvalidInputError(f"这一天没有这个块：{key!r}")
    if parts is not None:
        parts = _norm_parts(date, parts, InvalidInputError)
    return _submit("blockAction", {"action": action, "date": date.isoformat(), "blockKey": key, "taskId": task_id,
                                   "projectId": project_id, "parts": parts}, reason, direct)


def _norm_parts(date: Date, parts: list, exc) -> list:
    try:
        return blocks_write.normalize_parts(date, parts)
    except (UnprocessableError, NotFoundError) as e:
        raise exc(str(e)) from None


# ── 落地（批准与直接施加共用）：返回 (结果, before, after) ──

def _new_rule(given: dict) -> dict:
    return {"kind": "block", "app": given.get("app"), "title": given.get("title"), "block": given["block"], "enabled": True}


def _win_result(version: int, rule: dict | None, rule_id: str | None) -> dict:
    return {"version": version, "ruleId": rule_id, "rule": mask_rule(rule, False)}


def _same_rule(r: dict, given: dict) -> bool:
    return r.get("kind") == "block" and r.get("enabled") is not False and (r.get("app"), r.get("title"), r.get("block")) == (
        given.get("app"), given.get("title"), given["block"])


def _apply_window(p: dict, actor: str):
    """返回 ``(结果, before 规则集, after 版本)``。规则盖 AI 章、旧的一套压进快照栈（同 ``ai-apply``，``ai-revert`` 能撤）；同样的写两次不重复。"""
    user, op, given = current_tenant(), p["op"], p["rule"]
    for _ in range(_TRIES):  # 服务端代 If-Match：读版本、写时带版本，撞了重读重试
        doc = detector_repo.get_rules(user) or {}
        old, version = doc.get("rules", []), doc.get("version", 0)
        i = next((i for i, r in enumerate(old) if r["id"] == given.get("id")), None)
        shown = None
        if op == "add":
            dup = next((r for r in old if _same_rule(r, given)), None)
            if dup:  # 已经有一模一样的规则：当作已经加上了
                return _win_result(version, dup, dup["id"]), old, version
            if len(old) >= detector_rules.MAX_RULES:
                raise ConflictError(f"检测规则已满 {detector_rules.MAX_RULES} 条")
            shown = _check_rule(_new_rule(given), ConflictError)
            new = [*old, shown]
        elif i is None:
            raise ConflictError("要改的规则已不存在，请选「不要」")
        elif op == "delete":
            new = [r for j, r in enumerate(old) if j != i]
        else:
            shown = _check_rule({**old[i], **given}, ConflictError)
            if shown == old[i]:  # 已经是这个样子
                return _win_result(version, old[i], old[i]["id"]), old, version
            new = [*old[:i], shown, *old[i + 1:]]
        now = datetime.now(timezone.utc)
        snaps = [*(doc.get("snapshots") or []), {"rules": old, "version": version, "at": now, "summary": "AI 提议的窗口规则"}]
        if len(snaps) > detector_rules.MAX_SNAPSHOTS:
            del snaps[1]
        got = detector_repo.replace_rules(user, version, detector_rules._stamp(new, old, detector_rules._iso(now)), now, snapshots=snaps)  # noqa: SLF001
        if got is not None:
            reconcile.kick(actor=actor)
            return _win_result(got["version"], shown, (shown or old[i])["id"]), old, got["version"]
    raise ConflictError("检测规则一直被别处改动，没写成；提议仍在待批准，请稍后再批")


def _apply_rule(p: dict, via: str):
    user, key = current_tenant(), p["blockKey"]
    before = direct_log.rule_state(user, key)
    if p["op"] == "delete":
        cur = blocks_repo.rules_by_keys(user, [key]).get(key)
        if cur is None:
            raise ConflictError("这条第二级规则已不存在，请选「不要」")
        block_rules.remove(cur["id"])
        result = {"id": cur["id"], "blockKey": key, "deleted": True}
    else:
        try:
            block_rules.check_target(p["taskId"], p["projectId"])
        except NotFoundError:
            raise ConflictError("目标已不存在，请选「不要」") from None
        result, _ = block_rules.create(key, p["taskId"], p["projectId"], via=via, author="ai")  # add 遇同块名 = edit（upsert）
    return result, before, direct_log.rule_state(user, key)


def _same_target(user: str, p: dict) -> dict | None:
    """v2.39「五」：块已分类（assigned）且已是这个目标 → 返回块现状（assign 此时是空操作）；否则 None。"""
    item = blocks.block_item(user, Date.fromisoformat(p["date"]), p["blockKey"])
    if item["status"] != "assigned":
        return None
    parts = (direct_log.block_state(user, p["date"], p["blockKey"])["doc"]["parts"]) or []
    return item if [(x.get("taskId"), x.get("projectId")) for x in parts] == [(p["taskId"], p["projectId"])] else None


def _apply_action(p: dict, via: str, actor: str):
    user, key, d = current_tenant(), p["blockKey"], Date.fromisoformat(p["date"])
    item = blocks.block_item(user, d, key)
    act = p["action"]
    if p.get("_seq") is not None and item["seq"] != p["_seq"]:
        raise ConflictError("这个块在批准期间被人动过")
    # v2.39「五」：assign 也接受已分类（assigned）的块 = 改去向；已是同一目标 = 空操作（提议记 accepted）
    if item["status"] not in ("pending", "generic") and not (act == "assign" and item["status"] == "assigned"):
        raise ConflictError("这个块已经处理过")
    before, seq = direct_log.block_state(user, p["date"], key), item["seq"]
    if act == "assign" and item["status"] == "assigned" and _same_target(user, p) is not None:
        return item, before, before
    if act == "assign":
        result = blocks_write.assign(d, key, p["taskId"], p["projectId"], False, seq, via, actor)
    elif act == "split":
        result = blocks_write.split(d, key, p["parts"], seq, via, actor)
    elif act == "ignore":
        result = blocks_write.ignore_block(d, key, False, seq, via, actor)
    else:
        result = blocks_write.set_generic(key, True, actor)
    new_items.supersede(user, p["date"], key, p.get("id") or _id("blockAction", {k: p.get(k) for k in _FIELDS["blockAction"]}))  # AI 也动了这个块：同块上别的待批准提议作废（自己除外）
    return result, before, direct_log.block_state(user, p["date"], key)


def apply(kind: str, p: dict, via: str, actor: str):
    """``via``：``ai``（人批准）/ ``ai-direct``；``actor``：``human``（批准触发的对账）/ ``system``。"""
    if kind == "windowRule":
        return _apply_window(p, actor)
    return _apply_rule(p, via) if kind == "blockRule" else _apply_action(p, via, actor)


# ── 批准时更正 ──

def _edited(d: dict, edit: dict) -> dict:
    """校验 ``edit``，返回要覆盖到提议上的字段；不合规 422。不许动 kind / op / blockKey（以及 action / date / id）。"""
    kind = d["kind"]
    if not isinstance(edit, dict):
        raise UnprocessableError("edit 须是对象")
    locked = {"kind", "op", "blockKey", "action", "date", "id"} & set(edit)
    if locked:
        raise UnprocessableError(f"批准时不能更正 {'/'.join(sorted(locked))}，只能改目标、块名、拆分点")
    if kind == "windowRule":
        rule = edit.get("rule")
        if set(edit) != {"rule"} or not isinstance(rule, dict) or set(rule) != {"block"} or not isinstance(rule["block"], str) or d["op"] == "delete":
            raise UnprocessableError('windowRule 的 edit 形如 {"rule": {"block": "新块名"}}，且 delete 不能更正')
        merged = {**d["rule"], "block": rule["block"].strip()}
        if d["op"] == "add":
            _check_rule(_new_rule(merged), UnprocessableError)
        else:  # edit：按合并后的整条规则校验（现有规则不在了就留给批准时 409）
            cur = next((r for r in (detector_repo.get_rules(current_tenant()) or {}).get("rules", []) if r["id"] == merged["id"]), None)
            if cur is not None:
                _check_rule({**cur, **merged}, UnprocessableError)
        return {"rule": merged}
    if kind == "blockRule":
        if d["op"] == "delete" or not edit or set(edit) - {"taskId", "projectId"}:
            raise UnprocessableError("blockRule 的 edit 形如 {taskId} 或 {projectId}，且 delete 不能更正")
        t, p = edit.get("taskId"), edit.get("projectId")
        try:
            block_rules.check_target(t, p)
        except NotFoundError as e:
            raise InvalidInputError(str(e)) from None
        return {"taskId": t, "projectId": p}
    if d["action"] == "assign" and edit and set(edit) <= {"taskId", "projectId"}:
        t, p = edit.get("taskId"), edit.get("projectId")
        try:
            block_rules.check_target(t, p)
        except NotFoundError as e:
            raise InvalidInputError(str(e)) from None
        return {"taskId": t, "projectId": p}
    if d["action"] == "split" and set(edit) == {"parts"} and isinstance(edit["parts"], list):
        return {"parts": _norm_parts(Date.fromisoformat(d["date"]), edit["parts"], UnprocessableError)}
    raise UnprocessableError("blockAction 的 edit：assign 给 {taskId} 或 {projectId}，split 给 {parts}，其余不能更正")


def _snap(kind: str, p: dict):
    """落地前后比较用的状态：变了 = 已经落地。窗口规则的落地是单次条件写，不需要。"""
    user = current_tenant()
    if kind == "blockRule":
        return direct_log.rule_state(user, p["blockKey"])
    return direct_log.block_state(user, p["date"], p["blockKey"]) if kind == "blockAction" else None


def _landed(kind: str, p: dict, pre) -> bool:
    """这次批准的写已经落地了吗（只认自己写下的痕迹：``via: "ai"``，别人在期间的写不算）。"""
    now = _snap(kind, p)
    if kind == "blockRule":
        return now is None if p["op"] == "delete" and pre is not None else bool(now and now["via"] == "ai" and now != pre)
    if kind == "blockAction":
        if p["action"] == "generic":
            return pre["generic"] is None and now["generic"] is not None
        return now != pre and now["doc"]["via"] == "ai"
    return False


def _wait(user: str, item_id: str) -> dict:
    deadline = time.monotonic() + proposals.WAIT_SECONDS
    cur = new_items._get(user, item_id)  # noqa: SLF001
    while cur and cur["status"] == "accepted" and cur.get("applyClaimedAt") is not None and time.monotonic() < deadline:
        time.sleep(0.1)
        cur = new_items._get(user, item_id)  # noqa: SLF001
    if cur is None:
        raise NotFoundError(f"没有这条提议：{item_id!r}")
    return cur


def _recheck(user: str, d: dict) -> None:
    """批准没落地、放回待批准之后再看一眼：期间人已经动了这个块（人的写没看见占位中的提议），就记 superseded。"""
    if d["kind"] != "blockAction":
        return
    item = blocks.block_item(user, Date.fromisoformat(d["date"]), d["blockKey"])
    doc = blocks_repo.get_block(user, d["date"], d["blockKey"]) or {}
    if item["status"] not in ("pending", "generic") and doc.get("via") == "human":
        new_items._col().update_one({"user": user, "id": d["id"], "status": "pending"},  # noqa: SLF001
                                    {"$set": {"status": "superseded", "decidedAt": datetime.now(timezone.utc)}})


def approve(user: str, d: dict, request, edit: dict | None) -> dict:  # noqa: ARG001 —— request：保持与别的 kind 同形
    col = new_items._col()  # noqa: SLF001
    if d["status"] == "accepted":
        claimed = d.get("applyClaimedAt")
        if claimed is None:
            return view(d)
        if not new_items._aged(claimed):  # noqa: SLF001
            return view(_wait(user, d["id"]))
        # 上一个批准崩在半路（占位过期）：放回待批准，这次接着批；apply 是幂等的（同样的规则不重复加，块已处理会 409）
        col.update_one({"user": user, "id": d["id"], "status": "accepted", "applyClaimedAt": claimed},
                       {"$set": {"status": "pending", "applyClaimedAt": None}, "$unset": {"decidedAt": ""}})
        d = new_items._get(user, d["id"])  # noqa: SLF001
        if d is None:
            raise NotFoundError("这条提议已不存在")
        if d["status"] != "pending":
            return view(d)
    st = d["status"]
    if st != "pending":
        raise ConflictError("这条提议已被否掉" if st == "rejected" else "这个块已经被人处理过，这条提议已作废")
    fields = _edited(d, edit) if edit else {}
    mark = datetime.now(timezone.utc)
    claim = {"status": "accepted", "decidedAt": mark, "applyClaimedAt": mark, **fields, **({"corrected": True} if fields else {})}
    if col.update_one({"user": user, "id": d["id"], "status": "pending"}, {"$set": claim}).modified_count != 1:  # 别人先批了 / 否了：等它落地，回实际视图
        return view(_wait(user, d["id"]))
    p = {**d, **fields}
    pre = _snap(d["kind"], p)
    if d["kind"] == "blockAction":  # 占位之后人动了这个块（seq 变了）→ 不覆盖人的决定（assign 现在也能改已分类的块，必须防）
        p["_seq"] = blocks.block_item(user, Date.fromisoformat(p["date"]), p["blockKey"])["seq"]
    try:
        result = apply(d["kind"], p, "ai", "human")[0]
    except Exception:
        if pre is not None and _landed(d["kind"], p, pre):  # 已经落地了、后面的步骤出错：不回待批准，当作批准成功
            result = {}
        else:  # 写之前就失败：放回待批准（更正一并撤销）
            col.update_one({"user": user, "id": d["id"], "applyClaimedAt": mark},
                           {"$set": {"status": "pending", "applyClaimedAt": None, "corrected": bool(d.get("corrected")), **{k: d.get(k) for k in fields}},
                            "$unset": {"decidedAt": ""}})
            _recheck(user, d)
            raise
    created = result.get("ruleId") if d["kind"] == "windowRule" and d["op"] == "add" else None
    col.update_one({"user": user, "id": d["id"], "applyClaimedAt": mark}, {"$set": {"applyClaimedAt": None, "createdId": created}})
    return view(new_items._get(user, d["id"]))  # noqa: SLF001

