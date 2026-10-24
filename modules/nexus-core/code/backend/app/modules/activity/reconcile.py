"""对账：把分好的窗口块写进台账（契约 v2.38「三级分类」四）。

每个块有「应该入账什么」（``reconcile_calc.plan``）与「已经入账什么」（块文档的 ``runs``），不一致就补写 / 改目标 / 作废。
一个块的一轮：先把上次没做完的事件写完（``_flush``）→ 算差 → 在块文档上**先写意图**（``reconcile_repo.commit``：``seq`` 与 ``rv`` 都没变才提交，
变了重来）→ 再写事件 → 收尾（``finish``）。意图写了、事件没写完时文档带 ``reconciling: true``，下一轮从 ``pend`` / ``voids`` 接着做；事件 id 与防重键确定，重做不重复。
触发：``run_all``（后台循环，每 60 秒）与 ``kick``（事件触发，同步、尽力而为、从不抛）。读端点不碰这里。
有界：每轮每个租户至多 ``MAX_BLOCKS`` 个块（超了按游标轮着来）、``MAX_EVENTS`` 个事件。

契约待定：作废 / 改目标 / 补写的事件 ``actor``：人的块写操作触发 = ``human``，其余（规则变化、后台循环、记录上传、手动计时、tiers 变化）= ``system``。
契约待定：对账建的文档（``via: "rule"``）在最近 14 天里仍跟着第二级规则走，更早的才按文档（``blocks.resolve``）。
契约待定：``runs`` 的条目另带 ``ivs``（段内时间并集的区间）、``win``（所属 part 的窗口）、``task``（落进的任务 id）、``via``，``pend``（事件还没写完）。
"""

from __future__ import annotations

import hashlib
import logging
import threading
from datetime import date as Date, datetime, timedelta, timezone

from ...tenant import as_tenant, current as current_tenant
from ..planner import unclassified
from ..projector.handlers import lanes as lanes_projection
from ..timer import service as timer_service
from . import block_rules, blocks, blocks_repo, reconcile_calc as rc, reconcile_events as ev, reconcile_repo, records_repo

log = logging.getLogger("uvicorn.error")
MAX_BLOCKS, MAX_EVENTS = 500, 2000
OTHER_SOURCES = ("timer-backend", "manual-backfill", "activity-confirmed")  # 契约「四」2：别的来源已记过的时间
_PAGE, _PAGES, _MAX_RECS, _TRIES, FULL_EVERY = 5000, 20, 20000, 8, 10
_locks: dict[str, threading.Lock] = {}
_cursor: dict[str, tuple] = {}
_mark: dict[str, datetime] = {}  # 租户 → 上次完整做完一轮的起点（只重访这以后脏了的块）
_force_full: set[str] = set()  # kick 没拿到锁被跳过的租户：下一轮循环全量
_rounds = 0
_guard = threading.Lock()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ms(t: datetime) -> int:
    return round(t.timestamp() * 1000)


def _load_others(user: str, since: datetime) -> list | None:
    """别的来源已记 / 正在跑的时间（已 merge）。来源在查询里过滤、分页取全；取不全（页数用尽 / 翻不动）→ None，调用方本轮放弃，不拿截断的数据算。"""
    end, start, seen, ivs = _now() + timedelta(days=1), since - timedelta(days=1), set(), []
    for _ in range(_PAGES):
        rows = lanes_projection.read_lanes(user, "session", start, end, _PAGE, sources=list(OTHER_SOURCES))
        for r in rows:
            if r["key"] not in seen:
                seen.add(r["key"])
                ivs.append((_ms(r["startAt"]), _ms(r["endAt"])))
        if len(rows) < _PAGE:
            break
        nxt = rows[-1]["startAt"] + timedelta(microseconds=1)  # 按 startAt 倒序翻页；同刻的靠 key 去重
        if nxt >= end:
            return None
        end = nxt
    else:
        return None
    running = timer_service.get_running_state(user)
    if running:
        ivs.append((_ms(datetime.fromisoformat(running["startAt"])), rc.FOREVER))
    return rc.merge(ivs)


class _Ctx:
    def __init__(self, user: str, since: datetime | None, actor: str):
        self.user, self.actor, self.left = user, actor, MAX_EVENTS
        self.since_ms, self.now_ms = (_ms(since) if since else 0), _ms(_now())
        self.others = _load_others(user, since) if since else []  # since 为 None = 只收口（tiers 关了），不算计划


def _tgt(part: dict) -> str:
    return part["taskId"] or unclassified.task_id(part["projectId"])


def _flush(ctx: _Ctx, ds: str, key: str, doc: dict) -> bool:
    """把意图里的事件写完（幂等）。预算不够返回 False。"""
    for v in doc.get("voids") or []:
        if ctx.left <= 0:
            return False
        ctx.left -= ev.write_void(ctx.user, v, ctx.actor)
        records_repo.clear_ledger(ctx.user, v["eventId"])
    out, blocked = [], [b for b in doc.get("blocked") or [] if b["seq"] == doc["seq"]]
    for r in doc["runs"]:
        pend = r.get("pend")
        if pend:
            if ctx.left <= 0:
                return False
            try:
                if pend == "w":
                    ev.write_completed(ctx.user, ds, key, r)
                    records_repo.mark_ledger(ctx.user, ds, key, r["eventId"], r["ivs"], ev.to_dt)
                else:
                    ev.write_reassign(ctx.user, r, ctx.actor)
                ctx.left -= 1
            except ev.UNWRITABLE as exc:  # 目标刚被删：这一条写不成。补写的丢掉（时间由下一轮重新算），改目标的保持原处
                log.warning("对账写不成（%s %s），目标先标 blocked：%s: %s", ds, key, type(exc).__name__, exc)
                blocked.append({"task": r["task"], "seq": doc["seq"]})  # 目标变了 / 块被改（seq 变）之前不再试，不再每分钟告警
                if pend == "w":
                    continue
                r = {**r, "task": r["prevTask"]}
            r = {k: v for k, v in r.items() if k not in ("pend", "prevTask")}
        out.append(r)
    if not reconcile_repo.finish(ctx.user, ds, key, doc, out, blocked):
        log.warning("对账收尾没成（%s %s）：块文档刚被别处改了，下一轮接着做", ds, key)
    return True


def _effective(user: str, d: Date, key: str, doc: dict | None) -> tuple:
    recent = d >= blocks.today() - timedelta(days=blocks.RULE_DAYS)
    rule = block_rules.effective(user, [key]).get(key) if recent else None
    return (*blocks.resolve(d, doc, blocks_repo.generic_since(user).get(key), rule, recent), recent)


def _classification(doc: dict | None, status: str, via: str | None, rule_id: str | None, parts: list, live: bool, recent: bool, writes: bool) -> dict:
    """对账建 / 同步的文档分类字段（``via: "rule"``）。第二级规则管着的块写段时建文档；之后规则变了跟着变，规则没了 / 变成通用窗口就清回缺省。"""
    if via == "rule" and live:
        want = {"status": "assigned", "via": "rule", "ruleId": rule_id, "parts": parts}
        if writes or (doc and doc.get("via") == "rule" and any(doc.get(k) != v for k, v in want.items())):
            return want
    elif doc and doc.get("via") == "rule" and recent:
        return {"status": None, "via": None, "ruleId": None, "parts": None}
    return {}


def run_block(ctx: _Ctx, d: Date, key: str) -> None:
    user, ds = ctx.user, d.isoformat()
    day_lo, day_hi = _ms(blocks._midnight(d)), _ms(blocks._midnight(d + timedelta(days=1)))  # noqa: SLF001
    for _ in range(_TRIES):
        doc = blocks_repo.get_block(user, ds, key)
        if ctx.others is None and not (doc and (doc.get("voids") or any(r.get("pend") for r in doc.get("runs") or []))):
            if doc:
                reconcile_repo.mark_reconciling(user, ds, key)  # 别的来源已记的时间没取全：这个块本轮放弃，带 reconciling
            return
        if doc and (doc.get("voids") or any(r.get("pend") for r in doc.get("runs") or [])):
            if not _flush(ctx, ds, key, doc):
                return
            continue
        status, via, rule_id, parts, recent = _effective(user, d, key, doc)
        live = status == "assigned" or (status == "generic" and bool(parts))
        reason = {"ignored": "block-ignored", "generic": "block-generic"}.get(status, "block-rule" if (doc or {}).get("via") == "rule" else "block-reset")
        recs = [(_ms(r["startTs"]), _ms(r["endTs"])) for r in records_repo.block_records(user, ds, key, _MAX_RECS) if _ms(r["startTs"]) >= ctx.since_ms]
        runs = list((doc or {}).get("runs") or [])
        plan = rc.plan(live=live, reason=reason, parts=parts, runs=runs, recs=recs, others=ctx.others, now_ms=ctx.now_ms,
                       day_lo=day_lo, day_hi=day_hi, tgt=_tgt, budget=ctx.left,
                       blocked={b["task"] for b in (doc or {}).get("blocked") or [] if b["seq"] == doc["seq"]})
        acts = plan["voids"] or plan["reassigns"] or plan["writes"]
        sync = _classification(doc, status, via, rule_id, parts, live, recent, bool(plan["writes"]))
        if not acts and not sync:
            if doc and doc.get("reconciling"):
                reconcile_repo.clear_flag(user, ds, key, doc)
            return
        if ctx.left <= 0:
            return
        gen_inc = int(bool(plan["voids"]) or doc is None or ("parts" in sync and sync["parts"] != (doc or {}).get("parts")))
        gen = ((doc or {}).get("gen") or 0) + gen_inc
        gone = {r["eventId"] for r, _ in plan["voids"]}
        move = {r["eventId"]: parts[i] for r, i in plan["reassigns"]}
        new_runs = [({**r, "prevTask": r["task"], "task": _tgt(move[r["eventId"]]), "bucket": move[r["eventId"]]["projectId"] if not move[r["eventId"]]["taskId"] else None, "pend": "r"}
                     if r["eventId"] in move else r) for r in runs if r["eventId"] not in gone]
        for w in plan["writes"]:
            p, dk = parts[w["partIdx"]], f"block:{ds}:{hashlib.sha256(key.encode()).hexdigest()[:12]}:{gen}:{w['s']}"
            new_runs.append({"eventId": ev.det_id(dk), "dedupeKey": dk, "startTs": w["s"], "endTs": w["e"], "partIdx": w["partIdx"],
                             "seconds": w["seconds"], "win": [p["fromTs"], p["toTs"]], "task": _tgt(p), "bucket": p["projectId"] if not p["taskId"] else None,
                             "via": via or "human", "ivs": [list(iv) for iv in w["ivs"]], "pend": "w"})
        fields = {**sync, "runs": new_runs, "voids": [*((doc or {}).get("voids") or []), *({"eventId": r["eventId"], "dedupeKey": r["dedupeKey"], "reason": why}
                                                                                           for r, why in plan["voids"])],
                  "reconciling": bool(acts), "more": plan["truncated"], "updatedAt": _now()}
        if reconcile_repo.commit(user, ds, key, doc, fields, int(bool(sync)), gen_inc) is None:
            continue  # 人刚改了这个块 / 别处刚对账：重读重来
    log.warning("对账 %s %s 连续 %d 次没做完（块一直在被改？），下一轮接着做", ds, key, _TRIES)


def _candidates(user: str, since: datetime, only: set | None, key: str | None, dirty_from: datetime | None) -> list[tuple[str, str]]:
    if only is not None:
        pairs = set(only)
    elif dirty_from is not None:  # 只重访脏块：有新记录 / 上次没收口 / 动作没做完
        pairs = set(records_repo.dirty_blocks(user, since, dirty_from, timedelta(milliseconds=rc.GAP_MS + 1000))) | set(reconcile_repo.reconciling_blocks(user))
    else:
        pairs = set(records_repo.touched_blocks(user, since)) | set(reconcile_repo.work_blocks(user))
    return sorted((p for p in pairs if key is None or p[1] == key), reverse=True)


def _settle(user: str, actor: str) -> int:
    """tiers 关了：已写的意图（pend / voids）照样收口，不做新计划；已入账的段不动。"""
    ctx = _Ctx(user, None, actor)
    for date_s, k in reconcile_repo.pending_blocks(user):
        doc = blocks_repo.get_block(user, date_s, k)
        if doc and ctx.left > 0:
            _flush(ctx, date_s, k, doc)
    return MAX_EVENTS - ctx.left


def run_round(user: str | None = None, *, only: set | None = None, key: str | None = None, actor: str = "system",
              full: bool = True, blocking: bool = True) -> int:
    """一个租户一轮，返回写了多少事件。``only`` = 只看这些 ``(date, blockKey)``；``key`` = 只看这个块名（各天）。
    ``full=False``（后台循环的常态）只重访脏块；``blocking=False`` 拿不到锁就跳过（循环会接手）。没开三级分类 → 只收口没写完的意图。"""
    user = user or current_tenant()
    with _guard:
        lock = _locks.setdefault(user, threading.Lock())
    if not lock.acquire(blocking=blocking):
        _force_full.add(user)
        return 0
    try:
        with as_tenant(user):
            st = blocks_repo.get_settings(user) or {}
            if not st.get("tiers") or not st.get("tiersSince"):
                return _settle(user, actor)
            started = _now()
            targeted = only is not None or key is not None
            dirty_from = None if (full or targeted or user in _force_full or user not in _mark) else _mark[user]
            cands = _candidates(user, st["tiersSince"], only, key, dirty_from)
            if not targeted:
                _force_full.discard(user)
            if not cands:
                if not targeted:
                    _mark[user] = started
                return 0
            ctx = _Ctx(user, st["tiersSince"], actor)
            if not targeted and _cursor.get(user):  # 轮着来：从上次停下的下一个开始
                at = next((i for i, c in enumerate(cands) if c < _cursor[user]), 0)
                cands = cands[at:] + cands[:at]
            batch, done = cands[:MAX_BLOCKS], None
            for date_s, k in batch:
                if ctx.left <= 0:
                    break
                run_block(ctx, Date.fromisoformat(date_s), k)
                done = (date_s, k)
            unfinished = len(cands) > len(batch) or ctx.left <= 0
            _cursor[user] = done if unfinished and not targeted else None
            if not targeted and not unfinished:
                _mark[user] = started
            elif not targeted:
                _mark.pop(user, None)  # 没做完：下一轮仍按全量轮着做
            return MAX_EVENTS - ctx.left
    finally:
        lock.release()


def run_all() -> int:
    """后台循环的一轮：逐租户（一个租户出错只记日志，不拖垮别的），常态只重访脏块，每 ``FULL_EVERY`` 轮全量扫一遍（规则 / 目标失效这类没有触发点的变化）。"""
    global _rounds  # noqa: PLW0603
    _rounds += 1
    total = 0
    for u in sorted(set(reconcile_repo.tier_users()) | set(reconcile_repo.pending_users())):
        try:
            total += run_round(u, full=_rounds % FULL_EVERY == 1)
        except Exception:  # noqa: BLE001
            log.exception("对账租户 %s 这一轮失败，其余租户照常", u)
    return total


def kick(*, only: set | None = None, key: str | None = None, actor: str = "system") -> None:
    """事件触发尽快一轮（同步、尽力而为）：对账出错不许让触发它的请求失败；循环正占着锁就不等（跳过，循环下一轮全量接手）。"""
    try:
        run_round(only=only, key=key, actor=actor, blocking=False)
    except Exception:  # noqa: BLE001
        log.exception("对账失败（触发处照常返回，后台循环稍后重来）")
