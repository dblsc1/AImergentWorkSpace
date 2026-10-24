"""窗口块的写（契约 v2.38「三级分类」二、窗口块 · 写）：assign / split / generic / ignore / reset。
只许人（HTTP 层挡）；每次写带 ``ifSeq``，不符 → ``BlockConflict``（409 并给现状）；目标不存在 → 404（``block_rules.check_target``）；
该日原始记录已过留存期 → 409。本期只改 ``activity_blocks`` / ``activity_generic`` / ``activity_block_rules`` / ``activity_ignores``，
**不写台账**（契约「四」对账，第二部分）：``gen`` 只在目标 / 拆分点变时 +1，``runs`` 只建字段。
契约待定：块里没有记录时 ``ignore`` 的 ``forever`` 没法定「时长最多的程序」→ 422。
契约待定：``generic`` 标 / 取消无日期、无 ``ifSeq``（块名级）；已是通用窗口再标一次不改 ``since``。
契约待定：通用窗口的块被 assign（整块记到一处）后状态也是 ``generic``（带一段 parts），与「拆分后是 generic」一致。
"""

from __future__ import annotations

from datetime import date as Date, datetime, timedelta, timezone

from ...tenant import current as current_tenant
from ..planner.errors import UnprocessableError
from . import block_rules, blocks, blocks_repo, ignore, records_repo

MIN_PARTS, MAX_PARTS = 2, 20
_SEP = " · "


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _write(user: str, d: Date, key: str, if_seq: int, status: str, parts: list[dict]) -> dict:
    blocks.check_writable(d)
    fields = {"status": status, "parts": parts, "via": "human", "ruleId": None, "updatedAt": _now()}
    old = blocks_repo.get_block(user, d.isoformat(), key)
    if (old or {}).get("seq", 0) != if_seq or blocks_repo.put_block(user, d.isoformat(), key, fields, if_seq, old) is None:
        raise blocks.BlockConflict(blocks.block_item(user, d, key))
    return blocks.block_item(user, d, key)


def _assigned_status(user: str, d: Date, key: str) -> str:
    """通用窗口的块分完仍是 ``generic``（契约「二」）；别的是 ``assigned``。"""
    since = blocks_repo.generic_since(user).get(key)
    return "generic" if since is not None and since <= d.isoformat() else "assigned"


def assign(d: Date, key: str, task_id: str | None, project_id: str | None, remember: bool, if_seq: int) -> dict:
    user = current_tenant()
    blocks.check_writable(d)  # 过期 409 在目标 404 之前
    block_rules.check_target(task_id, project_id)
    status = _assigned_status(user, d, key)
    if remember and status != "generic":
        block_rules.check_room(key)  # 先问规则放不放得下，再写块
    out = _write(user, d, key, if_seq, status, [{"fromTs": None, "toTs": None, "taskId": task_id, "projectId": project_id}])
    if remember and status != "generic":
        block_rules.create(key, task_id, project_id, via="assign")
    return out


def _ms(t: datetime) -> int:
    return round(t.timestamp() * 1000)


def split(d: Date, key: str, parts: list[dict], if_seq: int) -> dict:
    """``parts`` 的 ``fromTs`` / ``toTs`` 是 UTC 毫秒：相邻处 ``toTs == 下一段 fromTs``，首段 ``fromTs`` 与末段 ``toTs`` 可省（= 当天开头 / 结尾），
    中间的拆分点必须在当天之内、严格递增。"""
    user = current_tenant()
    blocks.check_writable(d)
    if not MIN_PARTS <= len(parts) <= MAX_PARTS:
        raise UnprocessableError(f"parts 须有 {MIN_PARTS}..{MAX_PARTS} 段，现在 {len(parts)} 段")
    for p in parts:
        block_rules.check_target(p.get("taskId"), p.get("projectId"))
    day_lo, day_hi = _ms(blocks._midnight(d)), _ms(blocks._midnight(d + timedelta(days=1)))  # noqa: SLF001
    cuts = []  # 拆分点：第 i 个 = 第 i 段的结束 = 第 i+1 段的开始
    for i, p in enumerate(parts[:-1]):
        a, b = p.get("toTs"), parts[i + 1].get("fromTs")
        if a is None and b is None or (a is not None and b is not None and a != b):
            raise UnprocessableError(f"parts[{i}].toTs 与 parts[{i + 1}].fromTs 须相接（相同，且至少给一个）")
        cuts.append(a if a is not None else b)
    for name, v, want in (("parts[0].fromTs", parts[0].get("fromTs"), day_lo), (f"parts[{len(parts) - 1}].toTs", parts[-1].get("toTs"), day_hi)):
        if v is not None and v != want:
            raise UnprocessableError(f"{name} 给了就必须是当天开头 / 结尾（{want}），parts 须覆盖全天")
    edges = [day_lo, *cuts, day_hi]
    if any(not lo < hi for lo, hi in zip(edges, edges[1:])):
        raise UnprocessableError("拆分点须在当天之内、严格按时间递增")
    out = [{"fromTs": None if i == 0 else edges[i], "toTs": None if i == len(parts) - 1 else edges[i + 1],
            "taskId": p.get("taskId"), "projectId": p.get("projectId")} for i, p in enumerate(parts)]
    return _write(user, d, key, if_seq, _assigned_status(user, d, key), out)


def set_generic(key: str, on: bool) -> dict:
    user = current_tenant()
    since = blocks.today().isoformat()
    blocks_repo.set_generic(user, key, on, since)
    return {"blockKey": key, "generic": on, **({"since": blocks_repo.generic_since(user).get(key, since)} if on else {})}


def ignore_block(d: Date, key: str, forever: bool, if_seq: int) -> dict:
    user = current_tenant()
    blocks.check_writable(d)
    rule, scope = None, None
    if forever:
        item = blocks.block_item(user, d, key)
        if item["keySource"] == "rule":
            raise UnprocessableError("这个块的名字来自第一级规则，不能永远忽略；请改第一级规则")
        app = records_repo.top_app(user, d.isoformat(), key)
        if app is None:
            raise UnprocessableError("这个块当天没有记录，定不出要永远忽略的程序")
        if key != app and not key.startswith(app + _SEP):
            raise UnprocessableError("块名既不是「程序 · 标题」也不是程序名，不能永远忽略；请改第一级规则")
        title = key[len(app) + len(_SEP):][:ignore.MAX_TITLE] if key != app else None
        rule, scope = (app, title), (None if title else "app")
    out = _write(user, d, key, if_seq, "ignored", [])
    if rule:
        ignore.add(*rule)
        if scope:
            out = {**out, "scope": scope}  # 提示「整个程序都忽略了」
    return out


def reset(d: Date, key: str, if_seq: int) -> dict:
    user = current_tenant()
    blocks.check_writable(d)
    if not blocks_repo.reset_block(user, d.isoformat(), key, if_seq, _now()):
        raise blocks.BlockConflict(blocks.block_item(user, d, key))
    return blocks.block_item(user, d, key)


def remember(key: str, task_id: str | None, project_id: str | None) -> tuple[dict, bool]:
    """``POST block-rules``：校验目标后建 / 改第二级规则。"""
    block_rules.check_target(task_id, project_id)
    return block_rules.create(key, task_id, project_id)
