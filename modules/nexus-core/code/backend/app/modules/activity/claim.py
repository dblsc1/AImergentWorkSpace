"""一次性补记（契约 onboarding.v1「五」）：候选、claim、claim/undo。

ponytail: 候选逐日聚合最近 ``days`` 天的记录（≤ 35 次读）；claim 对每个块名逐日走 ``blocks_write.assign``（每次 assign 触发一轮对账），
一次最多 100 个块名 × 35 天，真嫌慢再批量对账。
"""

from __future__ import annotations

import re
from datetime import date as Date, datetime, timedelta, timezone

from ...tenant import current as current_tenant
from ..planner import inbox, service as planner_service
from ..planner.errors import UnprocessableError
from ..timer import service as timer_service
from . import activity_settings, asked_repo, block_display, block_rules, blocks, blocks_repo, blocks_write, live_ask, new_items, records_repo
from .proposals import ConflictError

WINDOW_DAYS, MAX_ITEMS, TRIES = 35, 100, 3
_REASONS = ("notFound", "expired", "beforeTiers", "notPending")  # 优先级


def _tiers_on() -> dict:
    cfg = activity_settings.get()
    if not cfg["tiers"]:
        raise ConflictError("未打开三级分类")
    return cfg


def _aware(t: datetime) -> datetime:
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _target(task_id: str | None, project_id: str | None) -> dict:
    block_rules.check_target(task_id, project_id)
    task = planner_service.get_task(task_id) if task_id else None
    pid = task["projectId"] if task else project_id
    project = planner_service.get_project(pid)
    zone = planner_service.get_zone(project["zoneId"]) if project else None
    names = [(zone or {}).get("name") or inbox.ZONE_NAME, (project or {}).get("name") or inbox.PROJECT_NAME]
    return {"taskId": task_id, "projectId": pid, "name": task["name"] if task else names[1],
            "path": " / ".join([*names, *([task["name"]] if task else [])]),
            "words": _words([n for n in (task["name"] if task else None, names[1]) if n])}


def _words(names: list[str]) -> list[str]:
    """按空白与标点切；字母数字词与汉字词都要 ≥ 2 个字符（连续汉字是一个词）。"""
    out: list[str] = []
    for name in names:
        out += [w for w in re.split(r"[\W_]+", name.casefold()) if len(w) >= 2 and w not in out]
    return out


# ── 候选 ──


def _scan(user: str, days: int) -> dict[str, dict]:
    """块名 → 合并多天的 pending、legible 块。"""
    today, out = blocks.today(), {}
    for i in range(days):
        d = today - timedelta(days=i)
        recs = records_repo.day_records(user, d.isoformat())
        if not recs:
            continue
        groups = blocks._group(recs)  # noqa: SLF001
        for it in blocks._items(user, d, recs=recs):  # noqa: SLF001
            if it["status"] != "pending" or not it["legible"] or it["expired"]:
                continue
            g = groups[it["blockKey"]]
            e = out.setdefault(it["blockKey"], {"displayName": it["displayName"], "days": [], "seconds": 0, "haystack": [], "runs": set()})
            e["days"].append(d.isoformat())
            e["seconds"] += it["seconds"]
            e["haystack"] += [it["displayName"], *(r["folder"] for r in g["recs"] if r.get("folder"))]
            e["haystack"] += [x for x in (block_display.domain_of(it["blockKey"], g["src"], g["recs"]),) if x]
            e["runs"] |= {r["agentRunId"] for r in g["recs"] if r.get("agentRunId")}
    return out


def candidates(task_id: str | None, project_id: str | None, days: int) -> dict:
    user = current_tenant()
    tgt = _target(task_id, project_id)
    head = {"target": {k: tgt[k] for k in ("taskId", "projectId", "name", "path")}}
    if not activity_settings.get()["tiers"]:
        return {**head, "totalSeconds": 0, "items": []}
    found = _scan(user, days)
    runs = timer_service.agent_run_targets(user, sorted({r for e in found.values() for r in e["runs"]}))
    proposed: dict[str, list] = {}
    for p in new_items.pending_block_targets(user):
        proposed.setdefault(p["blockKey"], []).append(p)

    def hits_target(task: str | None, project: str | None) -> bool:
        if task_id:
            return task == task_id
        return project == project_id or (task is not None and (planner_service.get_task(task) or {}).get("projectId") == project_id)

    items = []
    for key, e in found.items():
        text = " ".join(e["haystack"]).casefold()
        match = ("agent" if any(hits_target(*runs[r]) for r in e["runs"] if r in runs) else
                 "ai" if any(hits_target(p.get("taskId"), p.get("projectId")) for p in proposed.get(key, [])) else
                 "name" if any(w in text for w in tgt["words"]) else None)
        if match:
            items.append({"blockKey": key, "displayName": e["displayName"], "match": match, "days": len(e["days"]), "seconds": e["seconds"],
                          "firstDate": min(e["days"]), "lastDate": max(e["days"])})
    items = sorted(items, key=lambda i: (-i["seconds"], i["blockKey"]))[:MAX_ITEMS]
    return {**head, "totalSeconds": sum(i["seconds"] for i in items), "items": items}


# ── claim ──


def _claim_day(user: str, d: Date, key: str, task_id: str | None, project_id: str | None) -> bool:
    for _ in range(TRIES):
        item = blocks.block_item(user, d, key)
        if item["status"] != "pending":
            return False
        try:
            blocks_write.assign(d, key, task_id, project_id, False, item["seq"], claim=True)
            return True
        except blocks.BlockConflict:
            continue  # 重读再试
        except ConflictError:  # 读到写之间过期了
            return False
    return False


def claim(task_id: str | None, project_id: str | None, keys: list[str], remember: bool) -> dict:
    user = current_tenant()
    block_rules.check_target(task_id, project_id)
    since = _aware(_tiers_on()["tiersSince"] or datetime.now(timezone.utc))
    keys = list(dict.fromkeys(keys))
    if remember and blocks_repo.rule_count(user) + len(set(keys) - set(blocks_repo.rules_by_keys(user, keys))) > blocks_repo.MAX_RULES:
        raise UnprocessableError(f"第二级规则最多 {blocks_repo.MAX_RULES} 条")  # 整个请求不动
    by_key: dict[str, dict[str, list]] = {}
    for r in records_repo.key_records(user, keys):
        by_key.setdefault(r["blockKey"], {}).setdefault(r["date"], []).append((_aware(r["startTs"]), _aware(r["endTs"])))
    lo, claimed, skipped = blocks.today() - timedelta(days=WINDOW_DAYS), [], []
    for key in keys:
        why, secs = ({"notFound"} if key not in by_key else set()), []
        for ds, spans in sorted(by_key.get(key, {}).items(), reverse=True):
            d = Date.fromisoformat(ds)
            live = [s for s in spans if s[0] >= since]
            if blocks.expired(d) or d < lo:
                why.add("expired")
            elif not live:
                why.add("beforeTiers")
            elif _claim_day(user, d, key, task_id, project_id):
                secs.append(round(blocks._union(live)))  # noqa: SLF001
            else:
                why.add("notPending")
        if not secs:
            skipped.append({"blockKey": key, "reason": next(r for r in _REASONS if r in why)})
            continue
        claimed.append({"blockKey": key, "days": len(secs), "seconds": sum(secs)})
        if remember:
            block_rules.create(key, task_id, project_id, via="assign")  # 日子都记完再写，免得规则抢先把它们变成「按规则」
        live_ask.mark_asked_quietly(key, "assigned")
    return {"claimed": claimed, "totalSeconds": sum(c["seconds"] for c in claimed), "skipped": skipped}


def undo(keys: list[str]) -> dict:
    user = current_tenant()
    _tiers_on()
    lo, undone = (blocks.today() - timedelta(days=WINDOW_DAYS)).isoformat(), []
    for key in dict.fromkeys(keys):
        n, part = 0, None
        for doc in blocks_repo.claimed_days(user, key, lo):
            for _ in range(TRIES):
                try:
                    blocks_write.reset(Date.fromisoformat(doc["date"]), key, doc["seq"])
                    n, part = n + 1, doc["parts"][0]
                    break
                except blocks.BlockConflict:
                    doc = blocks_repo.get_block(user, doc["date"], key) or {}
                    if not (doc.get("claim") and doc.get("via") == "human" and doc.get("status")):
                        break
                except ConflictError:  # 过期的日子改不了
                    break
        if n:
            rule = blocks_repo.rules_by_keys(user, [key]).get(key)
            if rule and (rule.get("taskId"), rule.get("projectId")) == (part["taskId"], part["projectId"]):
                block_rules.remove(rule["id"])
            asked_repo.delete(user, key, "assigned")
            undone.append({"blockKey": key, "days": n})
    return {"undone": undone}
