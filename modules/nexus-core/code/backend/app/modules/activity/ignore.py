"""忽略并记住（契约 v2.22「忽略并记住」节，唯一事实源）。

人对一个窗口说「忽略并记住」：从规则存在的那一刻起，匹配它的窗口**不当作工作**——

- **活动建议**：上传时直接丢掉，不存，规则上的计数器 +1 段 / +秒；
- **在场心跳**：窗口换成「没有窗口」（程序名与标题都清空、不带 guess）再存——人还是「在电脑前」，但它不会成为计时页 / 顶栏的
  焦点、自动跟踪的目标、「请你选」的窗口，也不会对上代理会话（泳道「你在看」的蓝条不会因它而长）；
- 说的当下，把已在待确认 / 已忽略里的匹配建议删掉（``add`` 里尽力而为、分批，一次、不重试）。

**这不是隐私擦除**：已经记下的在场历史、已确认的记录、已存的自动跟踪选择 / 问询、AI 写的或「记住」的分类规则与草稿、代理标签、AI 对话都不动。
匹配（两边都过 ``textfold.fold``）：程序名归一化后**相等**（``code`` 与 ``code.exe`` 不是同一个）；``titleContains`` 给了就还要归一化后的标题
**包含**它，不给 = 这个程序的所有窗口。不用正则。规则存在检测程序之外、服务端说了算。写（建 / 删）只许人（``ignore_router.is_human``：无 Bearer、无范围头、非匿名），其余一律 403。
失败方向：读不到规则 → 这次写入失败（5xx，检测程序会重试）；匹配某一项时出错 → 这一项当作被忽略。读接口不看忽略规则。
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from ...tenant import current as current_tenant
from ...textfold import fold
from ..planner.errors import InvalidInputError, UnprocessableError
from . import ignore_repo, presence, repo

MAX_IGNORES = 200  #: 每租户至多这么多条规则
MAX_APP, MAX_TITLE = 128, 200
#: 闸门最多看一个 app / title 的前这么多个码点：请求模型把收到的字符串截到这里（v2.4：超长截断照收、不拒），
#: 归一化与匹配都只在这个有界的串上做；随后存储再按各自的上限（presence 128 / 512，建议 128 / 512）截；匹配时收到的串与截后的串都要试（``Prepared.find``）。
GATE_CHARS = 4096
SCAN_BUDGET = 50_000  #: 建规则时清理至多扫这么多条待确认建议；超了就停，响应里的 ``removed`` 是实际删掉的


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _clean(text: str | None) -> str:
    return " ".join((text or "").split())


def rule_id(app: str, title: str) -> str:
    return "ig_" + hashlib.sha256(f"{fold(app)}\n{fold(title)}".encode()).hexdigest()[:16]


def _out(d: dict, human: bool = True) -> dict:
    """非人的调用方（任何 Bearer、MCP）拿不到 ``titleContains``——那段文字可能就是隐私窗口标题的一部分。"""
    out = {"id": d["id"], "app": d["app"], "createdAt": d["createdAt"].isoformat(), "hits": d["hits"], "seconds": d["seconds"]}
    if human:
        return {**out, "titleContains": d["titleContains"], "lastHitAt": d["lastHitAt"].isoformat() if d.get("lastHitAt") else None}
    return {**out, "hasTitleFilter": bool(d["titleContains"])}


class Prepared:
    """一次请求内的规则：每条规则的 app / 匹配文字只归一化一次，进来的每个串也只归一化一次（标题懒归一化，app 对不上就不碰它）。
    **每次请求现建，不跨请求缓存。**"""

    def __init__(self, rules_: list[dict]):
        self.items = [(r, fold(r.get("app")), fold(r["titleContains"]) if r.get("titleContains") else "") for r in rules_]

    def __bool__(self) -> bool:
        return bool(self.items)

    def find(self, app: str, title: str) -> dict | None:
        """收到的串与「将要存下的」串（按存储上限截断后）任一匹配都算命中：折掉的填充不能把规则文字挤出存储上限之外。"""
        app, title = (app or "")[:GATE_CHARS], (title or "")[:GATE_CHARS]   # 任何路径都不归一化无界的串
        clipped = presence.clip(app, title)
        return self._find(app, title) or (self._find(*clipped) if clipped != (app, title) else None)

    def _find(self, app: str, title: str) -> dict | None:
        fapp, ftitle = fold(app), None
        for rule, rapp, needle in self.items:
            if rapp != fapp:
                continue
            if needle:
                ftitle = fold(title) if ftitle is None else ftitle
                if needle not in ftitle:
                    continue
            return rule
        return None


def prepared(user: str) -> Prepared:
    """现读规则；读不到就抛——被守卫的写入因此失败（什么都没存）。"""
    return Prepared(ignore_repo.all_rules(user))


def find(rules_: Prepared, app: str, title: str) -> dict | None:
    return rules_.find(app, title)


def listing(human: bool = True) -> dict:
    items = [_out(d, human) for d in ignore_repo.all_rules(current_tenant())]
    return {"total": len(items), "items": items}


def add(app: str, title_contains: str | None) -> dict:
    """幂等：同一条规则再建回已有的那条（``created: false, removed: 0``，不再清理）。新建时顺手删掉已在待确认 / 已忽略里的命中项（``removed``，尽力而为）。"""
    user = current_tenant()
    app, title = _clean(app), _clean(title_contains)
    if not fold(app):
        raise InvalidInputError("app 不能为空")
    if len(app) > MAX_APP or len(title) > MAX_TITLE:
        raise UnprocessableError(f"app 至多 {MAX_APP}、titleContains 至多 {MAX_TITLE} 个字符")
    title = title if fold(title) else ""
    doc = {"user": user, "id": rule_id(app, title), "app": app, "titleContains": title or None,
           "createdAt": _now(), "hits": 0, "seconds": 0}
    # 先插、再数、超了撤回（并发的创建者各自插完再数：留下的总是 ≤ MAX_IGNORES；同时顶线时可能一起撤回，宁可少收，调用方重试）。
    # 插入与撤回之间进程崩了会留下第 201 条：已知、不加事务。数不出来 / 撤回出错 → 尽力把刚插的删掉再报错
    if not ignore_repo.insert_if_absent(doc):
        return {**_out(_stored(user, doc)), "created": False, "removed": 0}   # 重复建不再清理：想再扫就先删后建
    try:
        over = ignore_repo.count(user) > MAX_IGNORES
        if over:
            ignore_repo.delete(user, doc["id"])
    except Exception:
        try:
            ignore_repo.delete(user, doc["id"])
        except Exception:  # noqa: BLE001  尽力而为
            pass
        raise
    if over:
        raise UnprocessableError(f"忽略规则最多 {MAX_IGNORES} 条")
    removed = _sweep(user, doc)
    return {**_out(_stored(user, doc)), "created": True, "removed": removed}


def _stored(user: str, doc: dict) -> dict:
    """读回带最新计数器的那条；读不到就用手上这份（规则已存下，不因读回失败而报错）。"""
    try:
        return next((r for r in ignore_repo.all_rules(user) if r["id"] == doc["id"]), doc)
    except Exception:  # noqa: BLE001
        return doc


def _sweep(user: str, rule: dict) -> int:
    """建规则时唯一的清理：删掉命中的待确认 / 已忽略建议。分批、有扫描上限；出错就停在已删的数目上——规则已经存下，不重试。
    每批删完马上记计数器（秒数只算这批确实删掉的），后面出错不丢前面的。"""
    one, removed, scanned = Prepared([rule]), 0, 0
    try:
        for batch in repo.pending_windows(user):
            scanned += len(batch)
            ids = [p["id"] for p in batch if one.find(p["app"], p["title"])]
            if ids:
                n, seconds = repo.delete_pending(user, ids)
                removed += n
                if n:
                    ignore_repo.hit(user, rule["id"], n, seconds, _now())
            if scanned >= SCAN_BUDGET:
                break
    except Exception:  # noqa: BLE001  尽力而为：规则已存下，已删的照实报
        pass
    return removed


def remove(rule_id_: str) -> None:
    """幂等：没有这条也算成功。取消后以后的窗口照常产生建议；已经丢掉的不会回来。"""
    ignore_repo.delete(current_tenant(), rule_id_)


def gate_incoming(user: str, rules_: Prepared, items: list, window) -> tuple[list, int]:
    """上传：把命中忽略规则的项丢掉，计数器记上（一条 +1 段、+秒）。``window(item)`` = (app, title, 秒)。返回 (留下的, 丢了几个)。
    ``rules_`` 由调用方先读好（``prepared``）：读不到就在任何写入之前失败。"""
    if not rules_:
        return items, 0
    kept, hits = [], {}
    for item in items:
        try:
            app, title, seconds = window(item)
            r = find(rules_, app, title)
        except Exception:  # noqa: BLE001  拿不准这一项是不是被忽略的窗口 → 当作是：丢，不存（宁可少记，不存下来）
            r = {"id": None}
            seconds = 0
        if r is None:
            kept.append(item)
        elif r["id"] is None:
            continue
        else:
            n, s_ = hits.get(r["id"], (0, 0))
            hits[r["id"]] = (n + 1, s_ + seconds)
    now = _now()
    for rid, (records, seconds) in hits.items():
        try:
            ignore_repo.hit(user, rid, records, seconds, now)
        except Exception:  # noqa: BLE001  计数器只是统计，入库不能靠它
            pass
    return kept, len(items) - len(kept)


def gate_beat(user: str, app: str, title: str, guess: dict | None, spans: list[dict] | None) -> tuple:
    """在场心跳：命中的窗口换成「没有窗口」（app / title 空、不带 guess）。返回 (app, title, guess, spans, 顶层是否命中)。
    人仍然在电脑前；空程序名是已有的「标题被隐私设置整个去掉了」那一种，自动跟踪 / 请人选 / 对会话都会略过它。"""
    rules_ = prepared(user)
    if not rules_:
        return app, title, guess, spans, False
    def ignored(a: str, t: str) -> bool:
        try:
            return find(rules_, a, t) is not None
        except Exception:  # noqa: BLE001  拿不准 → 当作被忽略：整个窗口抹掉，不存
            return True

    top = ignored(app, title)
    if spans is not None:
        spans = [{**{k: v for k, v in sp.items() if k != "guess"}, "app": "", "title": ""}
                 if ignored(sp["app"], sp["title"]) else sp for sp in spans]
    return ("", "", None, spans, True) if top else (app, title, guess, spans, False)
