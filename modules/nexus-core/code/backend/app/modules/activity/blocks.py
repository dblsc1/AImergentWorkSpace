"""窗口块的读（契约 v2.38「三级分类」二、窗口块）。

块 = ``(user, date, blockKey)``，不单独存时长，**每次读时由原始记录聚合**（跨设备取时间并集）；分类状态存在 ``activity_blocks``。
生效状态：文档 ``status`` 非 null → 用它；否则通用窗口（``date ≥ since``）→ ``generic``；否则有可用的第二级规则 → ``assigned`` / ``via: "rule"`` / 一段全天；否则 ``pending``。
第二级规则只管最近 14 天（契约「三」影响范围）：更早的没文档的块不按规则算，是 ``pending``。对账写段时给没有文档的块建文档（``via: "rule"``），之后读时按文档（见 ``resolve``）；写台账见 ``reconcile.py``。
"""

from __future__ import annotations

import base64
import json
from datetime import date as Date, datetime, time, timedelta, timezone

from ... import config
from ...tenant import current as current_tenant
from . import block_key, block_rules, blocks_repo, records_repo
from ..planner.errors import UnprocessableError
from .proposals import ConflictError

MAX_RECORDS, RULE_DAYS = 5000, block_key.RECOMPUTE_DAYS
EXPIRED = "这一天的原始记录已过期，不能再改分类"


class BlockConflict(RuntimeError):
    """``ifSeq`` 与现状不符；``current`` = 该块现在的样子（409 体里给）。"""

    def __init__(self, current: dict):
        super().__init__(f"ifSeq 与现状不符（现在是 {current['seq']}）：请重新读取后再改")
        self.current = current


def today() -> Date:
    return datetime.now(timezone.utc).astimezone(config.settings.tz).date()


def expired(d: Date) -> bool:
    return d < today() - timedelta(days=config.settings.record_ttl_days)


def check_writable(d: Date) -> None:
    if expired(d):
        raise ConflictError(EXPIRED)


def housekeeping(user: str) -> None:
    now, tz = datetime.now(timezone.utc), config.settings.tz
    ttl = timedelta(days=config.settings.record_ttl_days)
    records_repo.purge(user, now - ttl, (now.astimezone(tz) - ttl).date().isoformat())
    block_key.recompute_stale(user)


def _midnight(d: Date) -> datetime:
    return datetime.combine(d, time(0), tzinfo=config.settings.tz)


def _union(spans: list[tuple[datetime, datetime]]) -> float:
    """时间区间并集的总秒数（多台设备同时在同一个块上，同一秒只算一次）。"""
    total, cur_end = 0.0, None
    for s, e in sorted(spans):
        if cur_end is None or s > cur_end:
            total, cur_end = total + (e - s).total_seconds(), e
        elif e > cur_end:
            total, cur_end = total + (e - cur_end).total_seconds(), e
    return total


def _part_seconds(d: Date, part: dict, spans: list[tuple[datetime, datetime]]) -> int:
    """记录与这一段时间窗（UTC 毫秒，null = 当天开头 / 结尾）的重叠，跨拆分点的记录在拆分点切开。"""
    lo = _midnight(d) if part["fromTs"] is None else datetime.fromtimestamp(part["fromTs"] / 1000, timezone.utc)
    hi = _midnight(d + timedelta(days=1)) if part["toTs"] is None else datetime.fromtimestamp(part["toTs"] / 1000, timezone.utc)
    return round(_union([(max(s, lo), min(e, hi)) for s, e in spans if min(e, hi) > max(s, lo)]))


def _group(recs: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for r in recs:
        g = out.setdefault(r["blockKey"], {"spans": [], "apps": set(), "src": "auto"})
        g["spans"].append((r["startTs"], r["endTs"]))
        g["apps"].add(r["app"])
        if r.get("keySource") == "rule":
            g["src"] = "rule"
    return out


def resolve(d: Date, doc: dict | None, since: str | None, rule: dict | None, recent: bool) -> tuple[str, str | None, str | None, list]:
    """生效状态 ``(status, via, ruleId, parts)``。``rule`` 只在 ``recent``（最近 14 天）时才被调用方给出。
    对账建的文档（``via: "rule"``）在最近 14 天里仍跟着第二级规则走（规则改了、删了块跟着变）；更早的就按文档记下的入账。"""
    status = (doc or {}).get("status")
    if status is not None and doc.get("via") == "rule" and recent:
        status = None
    if status is not None:
        return status, doc["via"], doc.get("ruleId"), doc["parts"]
    if since is not None and since <= d.isoformat():
        return "generic", None, None, []
    if rule is not None:
        return "assigned", "rule", rule["id"], [{"fromTs": None, "toTs": None, "taskId": rule.get("taskId"), "projectId": rule.get("projectId")}]
    return "pending", None, None, []


def _item(d: Date, key: str, g: dict, doc: dict | None, since: str | None, rule: dict | None, recent: bool = True) -> dict:
    spans = g["spans"]
    total = round(_union(spans))
    generic = since is not None and since <= d.isoformat()
    status, via, rule_id, parts = resolve(d, doc, since, rule, recent)
    tz = config.settings.tz
    first, last = min(s for s, _ in spans) if spans else _midnight(d), max(e for _, e in spans) if spans else _midnight(d)
    return {"blockKey": key, "keySource": g["src"], "status": status, "via": via, "ruleId": rule_id, "generic": generic,
            "seq": (doc or {}).get("seq", 0), **({"reconciling": True} if (doc or {}).get("reconciling") else {}),
            "parts": [{**p, "seconds": total if len(parts) == 1 else _part_seconds(d, p, spans)} for p in parts],
            "seconds": total, "records": len(spans), "firstAt": first.astimezone(tz).isoformat(),
            "lastAt": last.astimezone(tz).isoformat(), "apps": sorted(g["apps"]), "expired": expired(d)}


def _items(user: str, d: Date, only: str | None = None) -> list[dict]:
    ds = d.isoformat()
    groups = _group(records_repo.day_records(user, ds))
    if only is not None:
        groups = {only: groups.get(only, {"spans": [], "apps": set(), "src": "auto"})}
    docs, generic = blocks_repo.blocks_of_day(user, ds), blocks_repo.generic_since(user)
    recent = d >= today() - timedelta(days=RULE_DAYS)
    need = [k for k in groups if ((docs.get(k) or {}).get("status") is None or docs[k].get("via") == "rule")
            and not (k in generic and generic[k] <= ds)]
    rules = block_rules.effective(user, need) if recent else {}
    return [_item(d, k, g, docs.get(k), generic.get(k), rules.get(k), recent) for k, g in groups.items()]


def _decode(cursor: str) -> tuple[int, str]:
    try:
        sec, key = json.loads(base64.urlsafe_b64decode(cursor.encode("ascii")))
        if isinstance(sec, bool) or not isinstance(sec, int) or not isinstance(key, str):
            raise ValueError
        return sec, key
    except (ValueError, TypeError, UnicodeError):
        raise UnprocessableError("cursor 不合法：请用上一页返回的 nextCursor") from None


def list_blocks(d: Date | None, status: str, limit: int, cursor: str | None) -> dict:
    """按 ``(-seconds, blockKey)`` 排；``cursor`` 编码上一页最后一项的 ``(seconds, blockKey)``（keyset：翻页途中块变了也不重不漏）。
    ponytail: 每页都重新聚合整天的记录再过滤（一天万条以内）；不够用再把聚合下推到 Mongo。"""
    user, d = current_tenant(), d or today()
    after = _decode(cursor) if cursor else None  # 先验游标：坏的 422，不做任何事
    housekeeping(user)
    items = sorted(_items(user, d), key=lambda i: (-i["seconds"], i["blockKey"]))
    if status != "all":
        items = [i for i in items if i["status"] == status]
    if after:
        items = [i for i in items if (-i["seconds"], i["blockKey"]) > (-after[0], after[1])]
    page = items[:limit]
    nxt = base64.urlsafe_b64encode(json.dumps([page[-1]["seconds"], page[-1]["blockKey"]]).encode()).decode() if len(items) > limit else None
    return {"date": d.isoformat(), "nextCursor": nxt, "items": page}


def block_item(user: str, d: Date, key: str) -> dict:
    return _items(user, d, key)[0]


def block_records(d: Date, key: str) -> dict:
    user = current_tenant()
    housekeeping(user)
    rows = records_repo.block_records(user, d.isoformat(), key, MAX_RECORDS + 1)
    return {"items": [{k: r[k] for k in ("startAt", "endAt", "seconds", "app", "title", "deviceId")} for r in rows[:MAX_RECORDS]],
            "truncated": len(rows) > MAX_RECORDS}
