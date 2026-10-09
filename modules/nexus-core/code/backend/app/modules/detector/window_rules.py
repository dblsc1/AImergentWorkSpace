"""服务端代写的单条窗口规则：v2.14 ``activity/choice`` 的 remember；v2.15 AI 认窗口写的、人说「不对」删的。

``rules.py`` 贴着 300 行预算，拆出来；**不是新的子边界**，仍走 detector.rules.v1 的整套替换（``version`` +1，撞版本重读重试）。
"""

from __future__ import annotations

import logging
import re

from ...tenant import current as current_tenant
from . import repo
from .rules import MAX_RULES, RulesError, _now, _validate

_TRIES = 3  # 撞版本重读重试这么多次
log = logging.getLogger(__name__)
#: 服务端生成的窗口规则带 ``src: {app, title}``（生成它的那个窗口的原文，各自不超过存储上限）：**只存库，任何接口 / MCP 都不输出**
#: （``rules.py`` 的序列化去掉它）；「忽略并记住」清理按它走同一个 ``hit``，不用从正则里反推。
SRC = "src"
_SRC_APP, _SRC_TITLE = 128, 512
#: 服务端 / 页面按窗口生成的规则的 ``note`` 前缀：计时页「记住」选择（auto.py）、待确认建议页勾「以后这个窗口都记到…」（suggestions.js）
_GEN_NOTES = ("计时页选的：", "待确认里勾的：")


def with_src(rule: dict, app: str, title: str) -> dict:
    return {**rule, SRC: {"app": (app or "")[:_SRC_APP], "title": (title or "")[:_SRC_TITLE]}}


def rules_of(doc: dict | None) -> list:
    """文档里的规则数组；怪形状（不是数组）当作没有，记一笔日志——一份坏文档不能让「忽略并记住」永远建不成。"""
    old = (doc or {}).get("rules")
    if old is None or isinstance(old, list):
        return old or []
    log.warning("detector_rules 文档的 rules 不是数组（%s），按没有处理", type(old).__name__)
    return []


def prepend(rule: dict, yield_to_human: bool = False) -> bool:
    """往最前面加一条窗口规则，同 ``app`` / ``title``（或同 ``id``）的旧规则去掉（一个窗口至多一条）。
    True = 已在最前面；False = 放不下（不合规 / 满了）或连续撞版本。只校验新的这一条——别的规则指着已删的任务不该挡住它。
    ``yield_to_human``（AI 写的那条用）：这个窗口已有一条不带 ``auto`` 的规则（人写的）→ 不动它，False。
    判断与写入在同一次带版本的替换里，所以人的规则不会在两者之间被换掉。"""
    user = current_tenant()
    src = rule.get(SRC)
    try:
        rule = _validate([{k: v for k, v in rule.items() if k != SRC}])[0]   # src 不是请求体字段，校验之外带上
    except RulesError:
        return False
    if src:
        rule[SRC] = src
    for _ in range(_TRIES):
        doc = repo.get_rules(user) or {}
        old = rules_of(doc)
        if old and {**old[0], "id": rule["id"]} == rule:  # 同样的规则已是第一条：不写，version 不动
            return True
        if yield_to_human and any((r["app"], r["title"]) == (rule["app"], rule["title"]) and not r.get("auto")
                                  for r in old):
            return False
        rules = [rule] + [r for r in old
                          if (r["app"], r["title"]) != (rule["app"], rule["title"]) and r["id"] != rule["id"]]
        if len(rules) > MAX_RULES:
            return False
        if repo.replace_rules(user, doc.get("version", 0), rules, _now()) is not None:
            return True
    return False


def remove_auto(rule_id: str) -> bool:
    """删掉 ``id`` 是它、且带 ``auto: true`` 的那条（AI 直接写下的窗口规则）。True = 删了；没有这条 / 连续撞版本 → False。
    人后来改过它的目标也照删：它仍是那个窗口的规则，而人刚说了「不对」。"""
    user = current_tenant()
    for _ in range(_TRIES):
        doc = repo.get_rules(user) or {}
        old = rules_of(doc)
        rules = [r for r in old if not (r["id"] == rule_id and r.get("auto"))]
        if len(rules) == len(old):
            return False
        if repo.replace_rules(user, doc["version"], rules, _now()) is not None:
            return True
    return False


_SPECIAL = re.compile(r"[.*+?^${}()|\[\]\\]")  # 同 activity/auto.py 的 _escape
_DECOR = "^(?:[^\\pL\\pN]|\\(\\d+\\)|\\[\\d+\\])*"  # 同 activity/auto.py 的 _DECOR
_MAX_PATTERN = 400  #: 比这长的不是服务端生成的（生成的 ≤ 200）：不碰


def _esc(text: str) -> str:
    return _SPECIAL.sub(lambda m: "\\" + m.group(), text)


def _unesc(core: str) -> str | None:
    """``^转义后的原文$`` 的中间部分 → 原文；不是「原文转义」的形状（含没转义的正则元字符）→ None。只做字符串比较，不编译、不执行任何正则。"""
    lit = re.sub(r"\\(.)", r"\1", core)
    return lit if _esc(lit) == core else None


def _literal(pattern: object) -> str | None:
    """服务端代写的窗口规则字段（``^转义后的原文$``，标题可能带 ``_DECOR`` 前缀）→ 当初的原文。
    不是这个形状（别的正则、None、非字符串、过长）→ None（调用方当作「不能证明出自被忽略的窗口」，留着）。"""
    if not isinstance(pattern, str) or len(pattern) > _MAX_PATTERN or not pattern.endswith("$"):
        return None
    if pattern.startswith(_DECOR):
        return _unesc(pattern[len(_DECOR):-1])
    return _unesc(pattern[1:-1]) if pattern.startswith("^") else None


def _generated(rule: dict) -> str | None:
    """规则的 ``note`` 是不是「按窗口生成」的那两种前缀 → 前缀；否则 None。"""
    note = rule.get("note")
    return next((p for p in _GEN_NOTES if isinstance(note, str) and note.startswith(p)), None)


def _note_title(rule: dict, prefix: str) -> str | None:
    """生成规则的 ``note``（``<前缀><程序> · <标题>``，≤120）里的原始标题；没有标题 → None。"""
    return rule["note"][len(prefix):].partition(" · ")[2] if " · " in rule["note"] else None


def derive_src(rule: dict) -> dict:
    """页面经 ``PUT`` 写进来的、按窗口生成的规则（note 前缀 + ``^转义原文$`` 形状）：写入时补上 ``src``，往后和服务端写的一样按 ``src`` 清。
    认不出形状就原样返回（人手写的不动）。"""
    try:
        prefix = None if rule.get(SRC) else _generated(rule)
        app = _literal(rule.get("app")) if prefix else None
        title = "" if rule.get("title") is None else _literal(rule.get("title"))
        if app is None or title is None:
            return rule
        raw = _note_title(rule, prefix)
        return with_src(rule, app, raw if raw is not None and len(rule["note"]) < 120 else title)
    except Exception:  # noqa: BLE001
        return rule


def _derived(rule: object, hit, hit_core=None) -> bool:
    """这条规则是不是服务端按某个窗口生成的、且能证明出自 ``hit`` 命中的窗口。任何意外的形状 → False，不抛。
    新的规则带 ``src``（原始窗口）：直接 ``hit(src.app, src.title)``。没有 ``src`` 的老规则：AI 代写的（``author: assistant``）与「记住」写的
    （``note`` 以 ``计时页选的：`` / ``待确认里勾的：`` 开头）只有生成的 ``^转义原文$`` 形状可认——从正则里恢复原文，带装饰前缀的那种再用 ``hit_core``
    （匹配文字去装饰后）比核心标题，这两种规则还有 ``note`` 里的原始标题可试。人手写的（没有 ``src``、不是这两种形状）一概不动（声明过的例外）。"""
    try:
        if not isinstance(rule, dict):
            return False
        src = rule.get(SRC)
        if isinstance(src, dict):
            return bool(hit(src.get("app") or "", src.get("title") or ""))
        prefix = _generated(rule)
        if rule.get("author") != "assistant" and not prefix:
            return False
        app = _literal(rule.get("app"))
        if app is None:
            return False
        title = "" if rule.get("title") is None else _literal(rule.get("title"))  # 只有程序名的规则：窗口标题为空
        if title is None:
            return False
        decorated = isinstance(rule.get("title"), str) and rule["title"].startswith(_DECOR)
        if hit(app, title) or (decorated and (hit_core or hit)(app, title)):
            return True
        raw = _note_title(rule, prefix) if prefix else None
        return raw is not None and bool(hit(app, raw))
    except Exception:  # noqa: BLE001  清理路径不许因为一份怪文档而抛
        return False


def _draft_ignored(d: object, hit, text_hit, hit_core=None) -> bool:
    """待批准的 AI 草稿要不要作废：规则里有出自被忽略窗口的，或 ``summary`` / 规则 ``note`` 带着被忽略的文字。人写的草稿不碰。"""
    if not isinstance(d, dict) or d.get("author") != "assistant":
        return False

    def says(text: object) -> bool:
        return isinstance(text, str) and bool(text_hit(text))

    rules = d.get("rules")
    drafted = [r for r in (rules if isinstance(rules, list) else [])[:MAX_RULES] if isinstance(r, dict)]
    return says(d.get("summary")) or any(_derived({**r, "author": "assistant"}, hit, hit_core) or says(r.get("note")) for r in drafted)


def drop_ignored(hit, text_hit, hit_core=None) -> int:
    """「忽略并记住」建规则 / 补清时：服务端按窗口生成的规则（AI 代写的、「记住」选择写的；判据见 ``_derived``）出自被忽略窗口的删掉；
    待批准的 AI 草稿里有这样的规则、或 ``summary`` / 规则 ``note`` 带着被忽略的文字（``text_hit``），整份草稿作废。
    人手写的规则一概不动。**写库出错就抛**（草稿也一样：不能在草稿还在的时候让调用方标 purged）。返回删掉的规则数 + 作废的草稿数。"""
    user, n = current_tenant(), 0
    for _ in range(_TRIES):
        doc = repo.get_rules(user) or {}
        old = rules_of(doc)
        rules = [r for r in old if not _derived(r, hit, hit_core)]
        if len(rules) == len(old) or repo.replace_rules(user, doc["version"], rules, _now()) is not None:
            n += len(old) - len(rules)
            break
    else:
        raise RuntimeError("AI 规则清理连续撞版本")
    d = (repo.get_rules(user) or {}).get("draft")
    if _draft_ignored(d, hit, text_hit, hit_core):
        repo.drop_draft(user, d.get("id"))
        n += 1
    return n


def has_ignored(hit, text_hit, hit_core=None) -> bool:
    """校验用（只读）：还有没有出自被忽略窗口的规则或草稿（``drop_ignored`` 删的同一判据）。"""
    doc = repo.get_rules(current_tenant()) or {}
    return any(_derived(r, hit, hit_core) for r in rules_of(doc)) or _draft_ignored(doc.get("draft"), hit, text_hit, hit_core)


def masked(doc: dict, pending: list[dict]) -> dict:
    """读路径用（不写库）：有忽略规则清理没做完时，从规则文档的副本里去掉出自被忽略窗口的规则与草稿。"""
    from ..activity import ignore  # noqa: PLC0415  延迟导入：避免循环

    out = doc
    for r in pending:
        hit, hit_core, text_hit = ignore.judges(r)
        rules = [x for x in rules_of(out) if not _derived(x, hit, hit_core)]
        out = {**out, "rules": rules}
        if _draft_ignored(out.get("draft"), hit, text_hit, hit_core):
            out = {k: v for k, v in out.items() if k != "draft"}
    return out


def shown(doc: dict) -> dict:
    """读路径（GET /detector/rules、drafts/current）的入口：有忽略规则清理没做完才抹；读不到忽略规则 → ``RulesUnavailable``（503）。"""
    from ..activity import ignore  # noqa: PLC0415

    pending = ignore.unpurged(current_tenant())
    return masked(doc, pending) if pending else doc


def keep_src(old: dict, version: int, rules: list[dict]) -> list[dict]:
    """人整套替换规则时（请求体里没有服务端的 ``src``）：按 id 把旧规则的 ``src`` 带过去。版本对不上的话替换本来就 412，不必带。"""
    if old.get("version", 0) != version:
        return rules
    srcs = {r["id"]: r[SRC] for r in rules_of(old) if isinstance(r, dict) and r.get(SRC) and "id" in r}
    return [derive_src({**r, SRC: srcs[r["id"]]} if r["id"] in srcs else r) for r in rules]


def refuses(draft: dict) -> bool:
    """建草稿的闸门：这份草稿是不是出自某条忽略规则命中的窗口（和清理同一判据）。
    ponytail: 每条忽略规则各比一遍草稿（规则数 × 草稿规则数，都有上限、草稿很少）；要更快时把忽略规则预归一化成一个集合。"""
    from ..activity import ignore  # noqa: PLC0415

    for r in ignore.rules(current_tenant()):
        hit, hit_core, text_hit = ignore.judges(r)
        if _draft_ignored(draft, hit, text_hit, hit_core):
            return True
    return False
