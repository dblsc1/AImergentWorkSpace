"""服务端代写的单条窗口规则：v2.14 ``activity/choice`` 的 remember；v2.15 AI 认窗口写的、人说「不对」删的。

``rules.py`` 贴着 300 行预算，拆出来；**不是新的子边界**，仍走 detector.rules.v1 的整套替换（``version`` +1，撞版本重读重试）。
"""

from __future__ import annotations

import functools

import re2

from ...tenant import current as current_tenant
from . import repo
from .rules import MAX_RULES, RulesError, _now, _validate

_TRIES = 3  # 撞版本重读重试这么多次


@functools.lru_cache(maxsize=1024)
def _re2(pattern: str):
    """不分大小写的 RE2 编译（线性时间匹配，规则是用户 / AI 写的，不能用会回溯的 Python re）；编不过 → None。"""
    try:
        return re2.compile("(?i)" + pattern)
    except re2.error:
        return None


def ai_matchers(user: str, rules: list[dict] | None = None) -> list[tuple]:
    """v2.34：生效中的规则按顺序编成 ``(app 正则, title 正则, 是不是 mode ai)``（停用的跳过）。服务端没有检测程序那边的原始标题，
    只能拿收到的 app / title 照检测程序的口径（不分大小写、搜索而非整串、给了的字段都要中）再对一遍。
    用 google-re2，与检测程序的 Go regexp 同一套语法；RE2 编不过的那条跳过（服务端不认它，检测程序那边照旧）。"""
    out = []
    for r in (repo.get_rules(user) or {}).get("rules", []) if rules is None else rules:
        if r.get("enabled") is False or r.get("kind") == "block":  # 第一级规则不给建议（v1.5），不挡后面的 ai 规则
            continue
        pats = [_re2(r[k]) if r.get(k) else None for k in ("app", "title")]
        if any(r.get(k) and p is None for k, p in zip(("app", "title"), pats)):
            continue
        out.append((pats[0], pats[1], r.get("mode") == "ai"))
    return out


def ai_hit(matchers: list[tuple], app: str, title: str) -> bool:
    """这个窗口按规则顺序第一条命中的是 mode ai 的规则吗（先命中先赢，同检测程序）。"""
    for a, t, is_ai in matchers:
        if (a is None or a.search(app)) and (t is None or t.search(title)):
            return is_ai
    return False


def block_matchers(user: str) -> tuple[int, list[tuple]]:
    """v1.5：``(规则集版本, [(app 正则, title 正则, 块名)])``——生效中的第一级规则（``kind: "block"``）按顺序编好，给原始记录算 blockKey。
    RE2 编不过的跳过；一次读库同时给出版本号（重算块名靠它判断「第一级规则变了没有」）。"""
    doc = repo.get_rules(user) or {}
    out = []
    for r in doc.get("rules", []):
        if r.get("kind") != "block" or r.get("enabled") is False:
            continue
        pats = [_re2(r[k]) if r.get(k) else None for k in ("app", "title")]
        if any(r.get(k) and p is None for k, p in zip(("app", "title"), pats)):
            continue
        out.append((pats[0], pats[1], r["block"]))
    return doc.get("version", 0), out


def block_hit(matchers: list[tuple], app: str, title: str) -> str | None:
    """第一条命中的第一级规则的块名；都没命中 None（先命中先用，不分大小写、不锚定）。"""
    for a, t, block in matchers:
        if (a is None or a.search(app)) and (t is None or t.search(title)):
            return block
    return None


def prepend(rule: dict, yield_to_human: bool = False, window: tuple[str, str] | None = None) -> bool:
    """往最前面加一条窗口规则，同 ``app`` / ``title``（或同 ``id``）的旧规则去掉（一个窗口至多一条）。
    True = 已在最前面；False = 放不下（不合规 / 满了）或连续撞版本。只校验新的这一条——别的规则指着已删的任务不该挡住它。
    ``yield_to_human``（AI 写的那条用）：这个窗口已有一条不带 ``auto`` 的规则（人写的）→ 不动它，False。
    判断与写入在同一次带版本的替换里，所以人的规则不会在两者之间被换掉。
    ``window``（v2.34，``(app, title)``）：这个窗口按现有规则会被 mode ai 的规则先命中 → 不写，False。"""
    user = current_tenant()
    try:
        rule = _validate([rule])[0]
    except RulesError:
        return False
    for _ in range(_TRIES):
        doc = repo.get_rules(user) or {}
        old = doc.get("rules", [])
        if window and ai_hit(ai_matchers(user, old), *window):  # 对着这次要替换的那一版查：查完到写入之间人加的 ai 规则也算
            return False
        if old and {**old[0], "id": rule["id"]} == rule:  # 同样的规则已是第一条：不写，version 不动
            return True
        if yield_to_human and any((r["app"], r["title"]) == (rule["app"], rule["title"]) and not r.get("auto")
                                  and r.get("kind") != "block" for r in old):
            return False
        rules = [rule] + [r for r in old  # 同窗口的第一级规则（kind block）是另一类，不去重
                          if (r.get("kind") == "block" or (r["app"], r["title"]) != (rule["app"], rule["title"]))
                          and r["id"] != rule["id"]]
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
