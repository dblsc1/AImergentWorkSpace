"""忽略并记住（契约 v2.22「忽略并记住」节，唯一事实源）。

人对一个窗口说「忽略并记住」：以后匹配它的窗口**不当作工作**——

- **活动建议**：上传时直接丢掉，不存（标题不落库），规则上的计数器 +1 段 / +秒；
- **在场心跳**：窗口换成「没有窗口」（程序名与标题都清空、不带 guess）再存——人还是「在电脑前」，但它不会成为计时页 / 顶栏的
  焦点、自动跟踪的目标、「请你选」的窗口，也不会对上代理会话（泳道「你在看」的蓝条不会因它而长）；
- 说的当下，已存下的匹配项一并清掉（purge）：待确认 / 已忽略的建议、在场时间线里的窗口、人的临时选择、AI 问询；已确认记下的事实不动。

匹配：程序名**不分大小写相等**；``titleContains`` 给了就还要标题**包含**它（不分大小写），不给 = 这个程序的所有窗口。
不用正则：规则是人点一下写出来的，子串够用，也没有回溯的隐患。检测程序若开了标题换代号，标题规则中不了（程序规则不受影响）。
规则存在检测程序之外、服务端说了算；检测程序不需要知道它。写（建 / 删）只许人：带 Bearer 一律 403。
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from ...tenant import current as current_tenant
from ..planner.errors import InvalidInputError, UnprocessableError
from . import ignore_repo, repo

MAX_IGNORES = 200  #: 每租户至多这么多条规则
MAX_APP, MAX_TITLE = 128, 200
_PENDING_SCAN = 5000  #: 建规则时最多扫这么多条待确认的找命中的（待确认本身有 TTL，通常远小于此）


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _clean(text: str | None) -> str:
    return " ".join((text or "").split())


def rule_id(app: str, title: str) -> str:
    return "ig_" + hashlib.sha256(f"{app.casefold()}\n{title.casefold()}".encode()).hexdigest()[:16]


def _out(d: dict) -> dict:
    return {"id": d["id"], "app": d["app"], "titleContains": d["titleContains"],
            "createdAt": d["createdAt"].isoformat(), "hits": d["hits"], "seconds": d["seconds"],
            "lastHitAt": d["lastHitAt"].isoformat() if d.get("lastHitAt") else None}


def rules(user: str) -> list[dict]:
    return ignore_repo.all_rules(user)


def matches(rule: dict, app: str, title: str) -> bool:
    return (rule["app"].casefold() == app.casefold()
            and (not rule["titleContains"] or rule["titleContains"].casefold() in title.casefold()))


def find(rules_: list[dict], app: str, title: str) -> dict | None:
    return next((r for r in rules_ if matches(r, app, title)), None)


def listing() -> dict:
    items = [_out(d) for d in rules(current_tenant())]
    return {"total": len(items), "items": items}


def add(app: str, title_contains: str | None) -> dict:
    """幂等：同一条规则再建回已有的那条（``created: false``）。顺手删掉已在待确认里的命中项（``removed``）。"""
    user = current_tenant()
    app, title = _clean(app), _clean(title_contains)
    if not app:
        raise InvalidInputError("app 不能为空")
    if len(app) > MAX_APP or len(title) > MAX_TITLE:
        raise UnprocessableError(f"app 至多 {MAX_APP}、titleContains 至多 {MAX_TITLE} 个字符")
    doc = {"user": user, "id": rule_id(app, title), "app": app, "titleContains": title or None,
           "createdAt": _now(), "hits": 0, "seconds": 0}
    created = ignore_repo.insert_if_absent(doc) if ignore_repo.count(user) < MAX_IGNORES else False
    stored = next((r for r in rules(user) if r["id"] == doc["id"]), None)
    if stored is None:
        raise UnprocessableError(f"忽略规则最多 {MAX_IGNORES} 条")
    hit = [p for p in repo.pending_windows(user, _PENDING_SCAN) if matches(stored, p["app"], p["title"])]
    removed = repo.delete_pending(user, [p["id"] for p in hit]) if hit else 0
    purge(user, stored)
    if removed:
        ignore_repo.hit(user, stored["id"], removed, sum(p["durationSeconds"] for p in hit), _now())
    return {**_out(next(r for r in rules(user) if r["id"] == doc["id"])), "created": created, "removed": removed}


def purge(user: str, rule: dict) -> None:
    """建规则的当下，把已存下的、带被忽略窗口 app / title 的活状态抹掉（和上传、心跳的入口过滤是同一个判据 ``matches``）：
    在场时间线（段、当前窗口、guess、对上的会话 runId）、人的临时选择、AI 问询。已确认的建议和台账里的事实不动（见契约）。"""
    hit = lambda app, title: matches(rule, app or "", title or "")  # noqa: E731
    ignore_repo.mask_presence(user, hit)
    ignore_repo.drop_windows(user, "activity_choices", hit)
    ignore_repo.drop_windows(user, "activity_ai_asks", hit)


def remove(rule_id_: str) -> None:
    """幂等：没有这条也算成功。取消后以后的窗口照常产生建议；已经丢掉的不会回来。"""
    ignore_repo.delete(current_tenant(), rule_id_)


def drop(user: str, items: list, window) -> tuple[list, int]:
    """上传：把命中忽略规则的项丢掉，计数器记上（一条 +1 段、+秒）。``window(item)`` = (app, title, 秒)。返回 (留下的, 丢了几个)。"""
    rules_ = rules(user)
    if not rules_:
        return items, 0
    kept, hits = [], {}
    for item in items:
        app, title, seconds = window(item)
        if (r := find(rules_, app, title)) is None:
            kept.append(item)
        else:
            n, s_ = hits.get(r["id"], (0, 0))
            hits[r["id"]] = (n + 1, s_ + seconds)
    now = _now()
    for rid, (records, seconds) in hits.items():
        ignore_repo.hit(user, rid, records, seconds, now)
    return kept, len(items) - len(kept)


def mask_beat(user: str, app: str, title: str, guess: dict | None, spans: list[dict] | None) -> tuple:
    """在场心跳：命中的窗口换成「没有窗口」（app / title 空、不带 guess）。返回 (app, title, guess, spans, 顶层是否命中)。
    人仍然在电脑前；空程序名是已有的「标题被隐私设置整个去掉了」那一种，自动跟踪 / 请人选 / 对会话都会略过它。"""
    rules_ = rules(user)
    if not rules_:
        return app, title, guess, spans, False
    top = find(rules_, app, title) is not None
    if spans is not None:
        spans = [{**{k: v for k, v in sp.items() if k != "guess"}, "app": "", "title": ""}
                 if find(rules_, sp["app"], sp["title"]) else sp for sp in spans]
    return ("", "", None, spans, True) if top else (app, title, guess, spans, False)
