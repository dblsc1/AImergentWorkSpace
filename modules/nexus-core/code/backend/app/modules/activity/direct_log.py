"""直接施加日志 ``activity_direct_log``（契约 v2.38「五」）：闸门、记账、读、撤回。

记 ``{id, at, actor, kind, payload, before}``；另有内部字段 ``after``（落地后的状态，撤回时比对「before 有没有被后来的改动覆盖」）与 ``undoneAt``。
``before`` / ``after`` 不外露。保留 90 天（``at`` 上的 TTL 索引，读时也按 90 天过滤）。
契约待定：``kind: "change"``（v2.36 直接施加）不能撤回（改名 / 搬移 / 删除没有安全的反操作）→ 409。
契约待定：窗口规则的撤回比对整套规则集（任何后来的规则改动都算覆盖 → 409），写回整套 ``before``。
"""

from __future__ import annotations

import secrets
from datetime import date as Date, datetime, timedelta, timezone

from ... import config
from ...repo import get_db
from ...scope import caller
from ...tenant import current as current_tenant
from ..detector import repo as detector_repo
from ..detector.rules import RulesError
from ..planner.errors import NotFoundError
from . import activity_settings, blocks_repo, reconcile
from .proposals import ConflictError

_COLL, RETAIN_DAYS = "activity_direct_log", 90
_ready = False
_NEEDS = {"windowRule": "windowsClassify", "blockRule": "windowsClassify", "blockAction": "windowsClassify", "change": "honeycombRecords"}


def _col():
    global _ready
    col = get_db()[_COLL]
    if not _ready:
        col.create_index([("user", 1), ("id", 1)], unique=True, name="uniq_user_id")
        col.create_index([("at", 1)], expireAfterSeconds=RETAIN_DAYS * 86400, name="ttl_at")
        _ready = True
    return col


def _now() -> datetime:
    return datetime.now(timezone.utc)


def gate(switch: str) -> None:
    """直接施加的闸门：总开关、分开关、调用方范围，缺哪条 403 并带 ``reason``。"""
    s = activity_settings.get()["agentDirect"]
    if not s["enabled"]:
        raise RulesError(403, "账号设置里 AI 直接施加的总开关没打开（agentDirect.enabled），请让用户打开，或不带 directApply 改为提议", reason="agentDirectOff")
    if not s[switch]:
        raise RulesError(403, f"账号设置里 agentDirect.{switch} 没打开，请让用户打开，或不带 directApply 改为提议", reason=f"{switch}Off")
    if caller().scope != "write":
        raise RulesError(403, "直接施加只接受 write 范围的非人调用方（人请直接用对应的写端点）", reason="needWriteScope")


def run(kind: str, payload: dict, fn) -> dict:
    """先写一条 ``pending`` 的记录、再落地 ``fn() -> (结果, before, after)``，成了标完成；落地失败就删掉记录（列表不显示没成的）。"""
    user, lid = current_tenant(), "dl_" + secrets.token_hex(8)
    _col().insert_one({"user": user, "id": lid, "at": _now(), "actor": "ai", "kind": kind, "payload": payload, "state": "pending",
                       "before": None, "after": None, "undoneAt": None})
    try:
        result, before, after = fn()
    except Exception:
        _col().delete_one({"user": user, "id": lid})
        raise
    _col().update_one({"user": user, "id": lid}, {"$set": {"state": "done", "before": before, "after": after}})
    return {"applied": True, "directLogId": lid, "result": result}


def _out(d: dict, human: bool = True) -> dict:
    u = d.get("undoneAt")
    payload = d["payload"]
    if not human and isinstance(payload.get("rule"), dict) and "title" in payload["rule"]:  # 窗口标题正则可能含隐私：只有人看得到（同 v2.36 ignore）
        payload = {**payload, "rule": {**{k: v for k, v in payload["rule"].items() if k != "title"}, "hasTitle": bool(payload["rule"]["title"])}}
    return {"id": d["id"], "at": d["at"].replace(tzinfo=timezone.utc).isoformat(), "actor": d["actor"], "kind": d["kind"],
            "payload": payload, "undoneAt": u.replace(tzinfo=timezone.utc).isoformat() if u else None}


def listing(date: Date | None, limit: int, human: bool = True) -> dict:
    q: dict = {"user": current_tenant(), "state": {"$ne": "pending"}, "at": {"$gte": _now() - timedelta(days=RETAIN_DAYS)}}
    if date is not None:
        lo = datetime.combine(date, datetime.min.time(), tzinfo=config.settings.tz)
        q["at"] = {**q["at"], "$gte": max(q["at"]["$gte"], lo.astimezone(timezone.utc)), "$lt": (lo + timedelta(days=1)).astimezone(timezone.utc)}
    return {"items": [_out(d, human) for d in _col().find(q, {"_id": 0}).sort("at", -1).limit(limit)]}


# ── 状态快照（记账与撤回比对共用） ──

def rule_state(user: str, key: str) -> dict | None:
    r = blocks_repo.rules_by_keys(user, [key]).get(key)
    return None if r is None else {"taskId": r.get("taskId"), "projectId": r.get("projectId"), "via": r["via"], "author": r["author"]}


def block_state(user: str, date: str, key: str) -> dict:
    d = blocks_repo.get_block(user, date, key) or {}
    return {"doc": {k: d.get(k) for k in ("status", "parts", "via", "ruleId")}, "generic": blocks_repo.generic_since(user).get(key)}  # generic = 标记的 since 日期或 None


def _same_rule(a: dict | None, b: dict | None) -> bool:
    return (a and (a["taskId"], a["projectId"])) == (b and (b["taskId"], b["projectId"]))


# ── 撤回 ──

def _undo_window(user: str, d: dict) -> None:
    doc = detector_repo.get_rules(user) or {}
    if doc.get("version", 0) != d["after"]:  # 用规则集版本比：之后任何人改过规则集都算覆盖
        raise ConflictError("窗口规则在直接施加之后又被改过，不能撤回")
    snaps = doc.get("snapshots") or []
    keep = snaps[:-1] if snaps and snaps[-1]["rules"] == d["before"] else snaps  # 用掉施加时压进去的那份快照
    if detector_repo.replace_rules(user, doc.get("version", 0), d["before"], _now(), snapshots=keep) is None:
        raise ConflictError("窗口规则刚被别处改动，请重试")
    reconcile.kick()


def _undo_rule(user: str, d: dict) -> None:
    key, before = d["payload"]["blockKey"], d["before"]
    if not _same_rule(rule_state(user, key), d["after"]):
        raise ConflictError("第二级规则在直接施加之后又被改过，不能撤回")
    cur = blocks_repo.rules_by_keys(user, [key]).get(key)
    if before is None:
        if cur:
            blocks_repo.delete_rule(user, cur["id"])
    else:
        blocks_repo.upsert_rule(user, key, {"user": user, "blockKey": key, "taskId": before["taskId"], "projectId": before["projectId"],
                                            "author": before["author"], "via": before["via"], "updatedAt": _now()})
    reconcile.kick(key=key)


def _undo_block(user: str, d: dict) -> None:
    """先还原块文档（比较并交换，失败什么都没动），再还原通用窗口标记（还原成原来的 since）。"""
    date, key, before = d["payload"]["date"], d["payload"]["blockKey"], d["before"]
    if block_state(user, date, key) != d["after"]:
        raise ConflictError("这个块在直接施加之后又被改过，不能撤回")
    old = blocks_repo.get_block(user, date, key)
    if old is None and before["doc"]["status"] is not None:
        raise ConflictError("这个块的文档已不在，不能撤回")
    if old is not None and blocks_repo.put_block(user, date, key, {**before["doc"], "updatedAt": _now()}, old["seq"], old) is None:
        raise ConflictError("这个块刚被别处改动，请重试")
    if before["generic"] != d["after"]["generic"]:
        blocks_repo.set_generic(user, key, False, "")
        if before["generic"] is not None:
            blocks_repo.set_generic(user, key, True, before["generic"])
    reconcile.kick(only={(date, key)}, actor="human")
    if before["generic"] != d["after"]["generic"]:
        reconcile.kick(key=key, actor="human")


def undo(log_id: str) -> dict:
    user = current_tenant()
    d = _col().find_one({"user": user, "id": log_id}, {"_id": 0})
    if d is None:
        raise NotFoundError(f"没有这条直接施加记录：{log_id!r}")
    fn = {"windowRule": _undo_window, "blockRule": _undo_rule, "blockAction": _undo_block}.get(d["kind"])
    if fn is None:
        raise ConflictError("这类直接施加不能撤回，请在蜂巢里手动改回")
    mark = _now()
    if _col().update_one({"user": user, "id": log_id, "undoneAt": None}, {"$set": {"undoneAt": mark}}).modified_count != 1:
        raise ConflictError("这条直接施加已经撤回过")
    try:
        with reconcile.voiding("direct-undo"):
            fn(user, d)
    except Exception:
        _col().update_one({"user": user, "id": log_id}, {"$set": {"undoneAt": None}})
        raise
    return _out({**d, "undoneAt": mark})
