"""服务端代写的单条窗口规则：v2.14 ``activity/choice`` 的 remember；v2.15 AI 认窗口写的、人说「不对」删的。

``rules.py`` 贴着 300 行预算，拆出来；**不是新的子边界**，仍走 detector.rules.v1 的整套替换（``version`` +1，撞版本重读重试）。
"""

from __future__ import annotations

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
