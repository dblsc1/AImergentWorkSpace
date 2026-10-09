"""忽略并记住（契约 v2.22「忽略并记住」节，唯一事实源）。

人对一个窗口说「忽略并记住」：以后匹配它的窗口**不当作工作**——

- **活动建议**：上传时直接丢掉，不存（标题不落库），规则上的计数器 +1 段 / +秒；
- **在场心跳**：窗口换成「没有窗口」（程序名与标题都清空、不带 guess）再存——人还是「在电脑前」，但它不会成为计时页 / 顶栏的
  焦点、自动跟踪的目标、「请你选」的窗口，也不会对上代理会话（泳道「你在看」的蓝条不会因它而长）；
- 说的当下，已存下的匹配项一并清掉（purge）：待确认 / 已忽略的建议、在场时间线里的窗口、人的临时选择、AI 问询；已确认记下的事实不动。

- **AI 代写的分类规则 / 待批准的 AI 草稿**：同样清掉（``window_rules.drop_ignored``），``auto_ai.suggest`` 对被忽略的窗口拒绝写规则；
  人自己写的规则不动。

匹配（两边都过 ``textfold.fold``：NFKC、去零宽字符、折叠所有空白、strip、casefold）：程序名归一化后**相等**（``code`` 与 ``code.exe``
不是同一个，不猜后缀）；``titleContains`` 给了就还要归一化后的标题**包含**它，不给 = 这个程序的所有窗口。
不用正则：规则是人点一下写出来的，子串够用，也没有回溯的隐患。检测程序若开了标题换代号，标题规则中不了（程序规则不受影响）。
规则存在检测程序之外、服务端说了算；检测程序不需要知道它。写（建 / 删）只许人：带 Bearer 一律 403。

隐私：**被忽略窗口的标题不再保存**；规则本身保存的是人填的「匹配文字」（``titleContains``），只对登录的人可见（非人的调用方
只拿到 ``hasTitleFilter``）。清理没做完的规则（``purged: false``）在每次写入、以及读 ``views/current`` / ``views/lanes`` / 建议列表 / 规则列表时按退避重试，
读路径在副本里把它们命中的窗口抹掉；清理失败不拖垮别的写入（见 ``guarded``）。
"""

from __future__ import annotations

import functools
import hashlib
import logging
import re
from datetime import datetime, timedelta, timezone

from ...tenant import current as current_tenant
from ...textfold import fold
from ..planner.errors import InvalidInputError, UnprocessableError
from . import ignore_repo, repo

log = logging.getLogger(__name__)

MAX_IGNORES = 200  #: 每租户至多这么多条规则
MAX_APP, MAX_TITLE = 128, 200
#: 闸门最多看一个 app / title 的前这么多个码点：请求模型把收到的字符串截到这里（v2.4：超长截断照收、不拒），
#: 归一化与匹配都只在这个有界的串上做（每串 O(GATE_CHARS)）；随后存储再按各自的上限（presence 128 / 512，建议 128 / 512）截——
#: 所以**存下来的一定是闸门看过的串的前缀**。只出现在 GATE_CHARS 之后的片段看不到（存下的前缀也装不下它）。
GATE_CHARS = 4096
PURGE_BACKOFF = timedelta(minutes=1)  #: 清理失败后，同一条规则至多这么久才自动再试一次（写入路径上；不是每次写入都试）
STRICT_TRIES = 3  #: 写入后严格清理遇到「清完到校验之间又有别的写入落进来」时，重清重验这么多次；还脏就留给退避重试，不 5xx
PURGE_GIVE_UP = 5  #: 失败这么多次就放弃自动重试，规则标 ``purgeFailed``（人在规则列表里看得到；再建一次同一条规则会重清）
SCAN_BUDGET = 50_000  #: 一次清理至多扫这么多条待确认建议；超了就留给下一次重试（分批），不在一个请求里无限干活


class PurgeIncomplete(RuntimeError):
    """规则已经存下，但清理没做完 → 503（建规则的接口；会自动重试）。"""


class RulesUnavailable(RuntimeError):
    """读不到忽略规则：读窗口文字的接口宁可 503 也不交出没遮过的数据（契约 v2.22）。"""


class _Gone(Exception):
    """清理期间规则被删了（或删了又建回来）：这一次清理到此为止。"""


class _Dirty(Exception):
    """清完一遍后校验，还有命中的残留：不能标 purged。"""


class _OverBudget(Exception):
    """本次扫描超出预算：不算失败，下次接着清。"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _clean(text: str | None) -> str:
    return " ".join((text or "").split())


def rule_id(app: str, title: str) -> str:
    return "ig_" + hashlib.sha256(f"{fold(app)}\n{fold(title)}".encode()).hexdigest()[:16]


def _out(d: dict, human: bool = True) -> dict:
    """非人的调用方（任何 Bearer、MCP）拿不到 ``titleContains``——那段文字可能就是隐私窗口标题的一部分。"""
    title = {"titleContains": d["titleContains"]} if human else {"hasTitleFilter": bool(d["titleContains"])}
    return {"id": d["id"], "app": d["app"], **title,
            "createdAt": d["createdAt"].isoformat(), "hits": d["hits"], "seconds": d["seconds"],
            "lastHitAt": d["lastHitAt"].isoformat() if d.get("lastHitAt") else None,
            "purgeFailed": bool(d.get("purgeFailed"))}   # 清理多次失败、放弃自动重试：旧数据可能还留着，人要知道


def rules(user: str) -> list[dict]:
    return ignore_repo.all_rules(user)


class Prepared:
    """一次请求内的规则：每条规则的 app / 匹配文字只归一化一次，进来的每个串也只归一化一次（标题懒归一化，app 对不上就不碰它）——
    总量 O(串数 + 规则数)，不是 O(串数 × 规则数)。**每次请求现建，不跨请求缓存。**"""

    def __init__(self, rules_: list[dict]):
        self.items = [(r, fold(r.get("app")), fold(r["titleContains"]) if r.get("titleContains") else "") for r in rules_]

    def __bool__(self) -> bool:
        return bool(self.items)

    def find(self, app: str, title: str) -> dict | None:
        app, title = (app or "")[:GATE_CHARS], (title or "")[:GATE_CHARS]   # 任何路径都不归一化无界的串
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
    return Prepared(rules(user))


def find(rules_: list[dict] | Prepared, app: str, title: str) -> dict | None:
    return (rules_ if isinstance(rules_, Prepared) else Prepared(rules_)).find(app, title)


def matches(rule: dict, app: str, title: str) -> bool:
    return find([rule], app, title) is not None


def listing(human: bool = True) -> dict:
    retry_pending()
    try:
        rows = rules(current_tenant())
    except Exception as exc:
        raise RulesUnavailable("忽略规则暂时读不到") from exc
    items = [_out(d, human) for d in rows]
    return {"total": len(items), "items": items}


def add(app: str, title_contains: str | None) -> dict:
    """幂等：同一条规则再建回已有的那条（``created: false``）。顺手删掉已在待确认里的命中项（``removed``）。"""
    user = current_tenant()
    app, title = _clean(app), _clean(title_contains)
    if not fold(app):
        raise InvalidInputError("app 不能为空")
    if len(app) > MAX_APP or len(title) > MAX_TITLE:
        raise UnprocessableError(f"app 至多 {MAX_APP}、titleContains 至多 {MAX_TITLE} 个字符")
    title = title if fold(title) else ""
    doc = {"user": user, "id": rule_id(app, title), "app": app, "titleContains": title or None,
           "createdAt": _now(), "hits": 0, "seconds": 0, "purged": False}
    # 先插、再数、超了撤回（并发的创建者各自插完再数：留下的总是 ≤ MAX_IGNORES；同时顶线时可能一起撤回，宁可少收，调用方重试）
    created = ignore_repo.insert_if_absent(doc)
    if created and ignore_repo.count(user) > MAX_IGNORES:
        ignore_repo.delete(user, doc["id"])
        created = False
    stored = next((r for r in rules(user) if r["id"] == doc["id"]), None)
    if stored is None:
        raise UnprocessableError(f"忽略规则最多 {MAX_IGNORES} 条")
    try:
        removed = purge(user, stored, count=True)
    except _Gone:  # 清理期间被删了：没有什么要清的了
        removed = 0
    except Exception as exc:  # 规则已经存下（此后的写入者在写入时都会看到它）、没标 purged，之后按退避补清；读路径先遮住
        ignore_repo.note_purge_attempt(user, stored["id"], _now(), not isinstance(exc, _OverBudget), PURGE_GIVE_UP)
        raise PurgeIncomplete("规则已保存，清理未完成，会自动重试") from exc
    return {**_out(next(r for r in rules(user) if r["id"] == doc["id"])), "created": created, "removed": removed}


def _strip_decor(text: str) -> str:
    """去掉标题开头的装饰（状态符号 / 转圈字符 / ``(3)`` ``[3]`` 计数）：同 ``auto._DECOR``。全是装饰就原样返回。"""
    return re.sub(r"^(?:\(\d+\)|\[\d+\]|[\W_])*", "", text) or text


def judges(rule: dict):
    """这条规则对窗口 / 文字的三个判据：``hit(app, title)``；``hit_core``（匹配文字去掉开头装饰后再比：旧的、带装饰前缀的 AI 规则只存了
    去装饰的核心标题，而页面把整个标题带装饰填进了匹配文字）；``text_hit(text)``（草稿的摘要 / 备注）。"""
    one = Prepared([rule])
    needle = fold(rule["titleContains"]) if rule.get("titleContains") else ""
    core = _strip_decor(needle)
    one_core = Prepared([{**rule, "titleContains": core}]) if needle else one
    return (lambda app, title: one.find(app or "", title or "") is not None,
            lambda app, title: one_core.find(app or "", title or "") is not None,
            lambda text: bool(needle) and (needle in fold(text) or core in fold(text)))   # app 整个忽略时不按文字清草稿，只按窗口


def leftovers_exist(user: str, rule: dict) -> bool:
    """这条规则命中的东西现在还有没有残留：清理（``purge``）动过的每一处——待确认建议、在场、临时选择、AI 问询、AI 规则**与草稿**。
    校验用的唯一一处（purge 标 purged 之前问它）；读库出错就抛，不当作「没有」。"""
    from ..detector import window_rules  # noqa: PLC0415  detector.rules 经 planner 回到 activity.service：延迟导入

    hit, hit_core, text_hit = judges(rule)
    scanned = 0
    for batch in repo.pending_windows(user):
        scanned += len(batch)
        if scanned > SCAN_BUDGET:
            raise _OverBudget
        if any(hit(p["app"], p["title"]) for p in batch):
            return True
    return (any(hit(d.get("app"), d.get("title")) or any(hit(sp.get("app"), sp.get("title")) for sp in d.get("spans") or [])
                for d in repo.presence_list(user))
            or ignore_repo.any_window(user, "activity_choices", hit) or ignore_repo.any_window(user, "activity_ai_asks", hit)
            or window_rules.has_ignored(hit, text_hit, hit_core))


def purge(user: str, rule: dict, count: bool = False) -> int:
    """建规则的当下，把已存下的、带被忽略窗口 app / title 的活状态抹掉（和上传、心跳的入口过滤是同一个判据）：
    待确认 / 已忽略的建议（**全部**扫，分批；任何一步出错整个 purge 抛出、规则不标 purged）、在场时间线（段、当前窗口、guess、对上的会话 runId）、
    人的临时选择、AI 问询、AI 代写的规则与草稿、服务端按窗口生成的规则（「记住」的选择）。已确认的建议和台账里的事实不动（见契约）。"""
    from ..detector import window_rules  # noqa: PLC0415  detector.rules 经 planner 回到 activity.service：延迟导入

    hit, hit_core, text_hit = judges(rule)
    removed = seconds = 0

    def alive() -> None:
        if not ignore_repo.exists(user, rule["id"], rule["createdAt"]):
            raise _Gone

    scanned = 0
    for batch in repo.pending_windows(user):
        alive()
        scanned += len(batch)
        if scanned > SCAN_BUDGET:
            raise _OverBudget
        gone = [p for p in batch if hit(p["app"], p["title"])]
        if gone:
            removed += repo.delete_pending(user, [p["id"] for p in gone])
            seconds += sum(p["durationSeconds"] for p in gone)
    if count and removed:
        ignore_repo.hit(user, rule["id"], removed, seconds, _now())
    ignore_repo.mask_presence(user, hit)
    ignore_repo.drop_windows(user, "activity_choices", hit)
    ignore_repo.drop_windows(user, "activity_ai_asks", hit)
    alive()
    window_rules.drop_ignored(hit, text_hit, hit_core)  # 删规则 / 草稿出错就抛：不能在草稿还在的时候标 purged
    # 校验一遍确实没有残留，才标 purged（读路径的遮罩只看这个标志，标早了残留就露出来）；标的条件带 createdAt
    if leftovers_exist(user, rule):
        raise _Dirty
    ignore_repo.mark_purged(user, rule["id"], rule["createdAt"])
    return removed


def _due(rule: dict, now: datetime) -> bool:
    """没清完的规则现在该不该再试：放弃了的不试；刚试过不到 ``PURGE_BACKOFF`` 的不试。"""
    if rule.get("purged", True) or rule.get("purgeFailed"):
        return False
    last = rule.get("purgeLastTry")
    return last is None or now - last.replace(tzinfo=last.tzinfo or timezone.utc) >= PURGE_BACKOFF


def purge_leftovers(user: str, rule: dict) -> None:
    """（B）清理**规则存在之前**就存下的残留：尽力而为，**从不往调用方抛**（调用方是写入 / 读路径，别的窗口的数据不能因此 5xx）；失败记下时刻与次数。"""
    try:
        purge(user, rule)
    except _Gone:
        return
    except Exception as exc:  # noqa: BLE001
        try:
            ignore_repo.note_purge_attempt(user, rule["id"], _now(), not isinstance(exc, _OverBudget), PURGE_GIVE_UP)
        except Exception:  # noqa: BLE001
            pass


def retry_pending(user: str | None = None) -> None:
    """读路径 / 写路径：有规则清理没做完就按退避补清（不抛）。"""
    user = user or current_tenant()
    now = _now()
    try:
        pending = [r for r in rules(user) if _due(r, now)]
    except Exception:  # noqa: BLE001  读不到规则：补清这一步算了（读的遮罩那一步会 503，不会交出没遮过的数据）
        log.warning("读忽略规则失败，这次不补清", exc_info=True)
        return
    for r in pending:
        try:
            purge_leftovers(user, r)
        except Exception:  # noqa: BLE001  purge_leftovers 自己不抛；再兜一层，读路径不能因补清 5xx
            log.warning("补清忽略规则残留失败", exc_info=True)


def unpurged(user: str) -> list[dict]:
    """没清完的规则（读路径遮罩用）。读不到规则 → ``RulesUnavailable``（503）：读窗口文字的接口不交出没遮过的数据。"""
    try:
        return [r for r in rules(user) if not r.get("purged", True)]
    except Exception as exc:
        raise RulesUnavailable("忽略规则暂时读不到，窗口文字先不给") from exc


def _mask_doc(doc: dict, hit) -> dict:
    """在场文档里命中的段 / 当前窗口抹成「没有窗口」（读路径用，不写库）。"""
    spans = [{**{k: v for k, v in sp.items() if k not in ("guess", "runId")}, "app": "", "title": ""}
             if hit(sp.get("app"), sp.get("title")) else sp for sp in doc.get("spans") or []]
    top = hit(doc.get("app"), doc.get("title"))
    return {**doc, "spans": spans, **({"app": "", "title": ""} if top else {})}


def presence_docs(user: str) -> list[dict]:
    """读路径取在场文档的唯一入口：有规则清理没做完（含放弃了的）时，命中的窗口在读出来的副本里先抹掉——
    清理失败期间读也不会交出被忽略窗口的标题。先按退避补清一次（不抛）。"""
    retry_pending(user)
    docs = repo.presence_list(user)
    pend = unpurged(user)
    if not pend:
        return docs
    pp = Prepared(pend)
    hit = lambda app, title: pp.find(app or "", title or "") is not None  # noqa: E731
    return [_mask_doc(d, hit) for d in docs]


def visible(user: str, items: list[dict]) -> list[dict]:
    """建议列表读路径：清理没做完的规则命中的条目先不给（不写库）。"""
    retry_pending(user)
    pend = unpurged(user)
    pp = Prepared(pend)
    return [i for i in items if pp.find(i.get("app") or "", i.get("title") or "") is None] if pend else items


def _strict(user: str, rule: dict) -> None:
    """（A）写入后清一遍写的当中新出现的规则：出错照旧让这次请求失败（补不完就不能说这次写入是干净的）。唯一的例外是 ``_Dirty``——
    清完到校验之间**别的**请求又写进了命中的东西（本次请求自己的数据在 ``purge`` 之前就已经写完、被清掉了；它进来时规则还没出现，
    闸门拦不到）：重清重验 ``STRICT_TRIES`` 次，还脏就不 5xx，规则留 ``purged: false``，读路径继续遮、下一次写入按退避补清。"""
    for _ in range(STRICT_TRIES):
        try:
            purge(user, rule)
            return
        except _Gone:
            return  # 刚建又被删了：没有什么要守的了
        except _Dirty:
            continue


def guarded(fn):
    """写入口的包装。**不变式：被守卫的写入永远不会把命中任何已存规则的数据存下来，不管清理（purge）处在什么状态。**
    两个部分，故意分开：

    （A）写入闸门——**fail-closed，强度不因清理失败而降低**：写入开头现读规则（读不到 → 这次写入失败，什么都没存），
    进来的数据先由 ``gate_incoming`` / ``gate_beat`` 整个丢掉 / 抹掉（每个字段、每条路径：段的 app / title、心跳顶层的 app / title / guess 与每一个 span；
    拿不准的一项按「被忽略」处理，不存）。写完后再现读一次规则：写的当中**新出现**的规则（读了旧规则的写入者写在建规则的清理之后）
    由这里**严格**清一遍（``purge``，出错就让这次请求失败）——补不完就不能说这次写入是干净的。

    （B）残留清理——唯一被推迟的部分：规则存在**之前**就存下的旧数据，由 ``purge_leftovers`` 尽力清，失败不拖垮别的窗口的写入；
    记在规则上（``purgeTries`` / ``purgeLastTry``），按 ``PURGE_BACKOFF`` 退避、``PURGE_GIVE_UP`` 次后标 ``purgeFailed``，
    读路径另外遮住残留。记的是（规则 id, 创建时刻），所以「删了又同样建回来」不算已知规则。"""
    @functools.wraps(fn)
    def run(*args, **kwargs):
        user = current_tenant()
        before = {(r["id"], r["createdAt"]) for r in rules(user)}
        try:
            return fn(*args, **kwargs)
        finally:
            now = _now()
            for r in rules(user):
                if (r["id"], r["createdAt"]) not in before:
                    _strict(user, r)  # （A）写的当中新出现的规则：严格
                elif _due(r, now):
                    try:
                        purge_leftovers(user, r)  # （B）本来就有的规则的旧残留：尽力
                    except Exception:  # noqa: BLE001  purge_leftovers 自己不抛；这里再兜一层，别的窗口的写入不能因它 5xx
                        pass
    return run


def remove(rule_id_: str) -> None:
    """幂等：没有这条也算成功。取消后以后的窗口照常产生建议；已经丢掉的不会回来。"""
    ignore_repo.delete(current_tenant(), rule_id_)


def gate_incoming(user: str, items: list, window) -> tuple[list, int]:
    """上传：把命中忽略规则的项丢掉，计数器记上（一条 +1 段、+秒）。``window(item)`` = (app, title, 秒)。返回 (留下的, 丢了几个)。"""
    rules_ = prepared(user)
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
        ignore_repo.hit(user, rid, records, seconds, now)
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
