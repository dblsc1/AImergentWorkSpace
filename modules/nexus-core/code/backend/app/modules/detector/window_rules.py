"""服务端代写的单条窗口规则：v2.14 ``activity/choice`` 的 remember；v2.15 AI 认窗口写的、人说「不对」删的。

``rules.py`` 贴着 300 行预算，拆出来；**不是新的子边界**，仍走 detector.rules.v1 的整套替换（``version`` +1，撞版本重读重试）。
"""

from __future__ import annotations

import re

from ...tenant import current as current_tenant
from . import repo
from .rules import MAX_RULES, RulesError, _now, _validate

_TRIES = 3  # 撞版本重读重试这么多次


def prepend(rule: dict, yield_to_human: bool = False) -> bool:
    """往最前面加一条窗口规则，同 ``app`` / ``title``（或同 ``id``）的旧规则去掉（一个窗口至多一条）。
    True = 已在最前面；False = 放不下（不合规 / 满了）或连续撞版本。只校验新的这一条——别的规则指着已删的任务不该挡住它。
    ``yield_to_human``（AI 写的那条用）：这个窗口已有一条不带 ``auto`` 的规则（人写的）→ 不动它，False。
    判断与写入在同一次带版本的替换里，所以人的规则不会在两者之间被换掉。"""
    user = current_tenant()
    try:
        rule = _validate([rule])[0]
    except RulesError:
        return False
    for _ in range(_TRIES):
        doc = repo.get_rules(user) or {}
        old = doc.get("rules", [])
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
        old = doc.get("rules", [])
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


def _derived(rule: object, hit) -> bool:
    """这条规则是不是 AI 代写的、且能证明出自 ``hit`` 命中的窗口。任何意外的形状 → False，不抛。"""
    try:
        if not isinstance(rule, dict) or rule.get("author") != "assistant":
            return False
        app = _literal(rule.get("app"))
        if app is None:
            return False
        title = "" if rule.get("title") is None else _literal(rule.get("title"))  # 只有程序名的规则：窗口标题为空
        return title is not None and bool(hit(app, title))
    except Exception:  # noqa: BLE001  清理路径不许因为一份怪文档而抛
        return False


def drop_ignored(hit, text_hit) -> int:
    """「忽略并记住」建规则 / 补清时：AI 代写的（``author: "assistant"``）窗口规则，若是服务端生成的 ``^转义原文$`` 形状、
    且原文（程序、标题）过 ``hit(app, title)`` 命中，删掉；待批准的 AI 草稿里有这样的规则、或 ``summary`` / 规则 ``note`` 带着被忽略的文字
    （``text_hit(text)``），整份草稿作废。别的形状（人写的、没有 ``author`` 的老规则、任意正则、缺字段）一概不动、也不抛。
    返回删掉的规则数 + 作废的草稿数。"""
    user, n = current_tenant(), 0
    for _ in range(_TRIES):
        doc = repo.get_rules(user) or {}
        old = doc.get("rules") or []
        rules = [r for r in old if not _derived(r, hit)]
        if len(rules) == len(old) or repo.replace_rules(user, doc["version"], rules, _now()) is not None:
            n += len(old) - len(rules)
            break
    else:
        raise RuntimeError("AI 规则清理连续撞版本")
    d = (repo.get_rules(user) or {}).get("draft")
    if isinstance(d, dict) and d.get("author") == "assistant":
        try:
            def says(text: object) -> bool:
                return isinstance(text, str) and bool(text_hit(text))

            drafted = [r for r in (d.get("rules") or [])[:MAX_RULES] if isinstance(r, dict)]
            if says(d.get("summary")) or any(_derived({**r, "author": "assistant"}, hit) or says(r.get("note")) for r in drafted):
                repo.drop_draft(user, d["id"])
                n += 1
        except Exception:  # noqa: BLE001
            pass
    return n


def has_ignored(hit) -> bool:
    """校验用（只读）：还有没有出自被忽略窗口的 AI 规则（``drop_ignored`` 删的同一判据）。"""
    return any(_derived(r, hit) for r in ((repo.get_rules(current_tenant()) or {}).get("rules") or []))
