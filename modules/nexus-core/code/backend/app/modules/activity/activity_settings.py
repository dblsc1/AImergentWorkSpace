"""账号设置 ``activity_settings``（契约 v2.38「六、账号设置」）与第一次打开 ``tiers`` 时的旧规则迁移。

没有文档 = 缺省（``version: 0``，``tiers: false``，``tiersSince: null``，``agentDirect`` 全 false）。``PUT`` 整份替换（严格校验在 HTTP 层，422），
只许人，须带 ``If-Match: "<version>"``（缺 428、不符 412，同 detector 规则）；``tiersSince`` 与 ``version`` 由服务端写，客户端给的忽略。
``tiers`` 从关到开：``tiersSince`` = 此刻。本期不改 ``auto_entry`` / 确认建议的行为，也不对账（第二部分）。

迁移（第一次打开，幂等，按旧规则 id）：每条生效的旧式规则（``enabled`` 不为 false、带 ``taskId`` / ``projectId``、``mode`` 不是 ai、还没 ``migrated``）
紧挨着它后面插一条第一级规则（``kind: "block"``，同 ``app`` / ``title``，``block`` = ``note`` 截到 64 字符，没有就用「旧规则 <id>」，重名加「 (2)」「 (3)」；
规则 id 由旧规则 id 派生，重跑不会重复）；``confidence ≥ 0.9`` 的再写一条第二级规则（该块名 → 原目标，``via: "migration"``，同块名已有就不动）；
旧规则原地标 ``migrated: true``。经 detector 规则仓库整套替换写入，``version`` +1；没有要迁的就不写。迁移后总数超过 500 → 整个 PUT 409，什么都不改。
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from ...tenant import current as current_tenant
from . import block_rules, blocks_repo
from .proposals import ConflictError

AGENT_OFF = {"enabled": False, "windowsClassify": False, "honeycombRecords": False}


def get() -> dict:
    doc = blocks_repo.get_settings(current_tenant()) or {}
    return {"version": doc.get("version", 0), "tiers": doc.get("tiers", False), "tiersSince": doc.get("tiersSince"),
            "agentDirect": {**AGENT_OFF, **doc.get("agentDirect", {})}}


def put(if_match: str | None, tiers: bool, agent_direct: dict) -> dict:
    from ..detector.rules import RulesError, _if_match  # noqa: PLC0415 —— detector 反过来 import activity，顶层 import 成环

    expected = _if_match(if_match)
    user, now = current_tenant(), datetime.now(timezone.utc)
    doc = blocks_repo.get_settings(user) or {}
    fields = {"tiers": tiers, "agentDirect": agent_direct, "updatedAt": now,
              "tiersSince": now if tiers and not doc.get("tiers") else doc.get("tiersSince")}
    if doc.get("version", 0) != expected:
        raise RulesError(412, f"设置已被改过（你基于 v{expected}，现在是 v{doc.get('version', 0)}）：请重新读取后再改", currentVersion=doc.get("version", 0))
    if tiers and not doc.get("migratedAt"):
        # 版本已核对（412 的请求到不了这里，没有副作用）；迁移失败（含 409）就整个 PUT 失败：设置不变。
        # 已知窗口：核对到下面的设置 CAS 之间若别处也改了设置，PUT 报 412，但迁移（幂等）已做完；重试时 migratedAt 还没有，会再跑一遍什么也不改。
        migrate(user)
        fields["migratedAt"] = now
    if not blocks_repo.put_settings(user, expected, fields):
        raise RulesError(412, "设置刚被别处改过：请重新读取后再改", currentVersion=(blocks_repo.get_settings(user) or {}).get("version", 0))
    return get()


def _unique(name: str, taken: set[str]) -> str:
    out, n = name, 1
    while out in taken:
        n += 1
        suffix = f" ({n})"
        out = name[:64 - len(suffix)] + suffix
    taken.add(out)
    return out


def migrate(user: str) -> int:
    """返回迁了几条旧规则。顺序：预检上限 → 规则集 CAS 写入 → 第二级规则（按 rid 幂等；也补上之前崩在 CAS 与此之间留下的）。"""
    from ..detector import repo as detector_repo  # noqa: PLC0415
    from ..detector import rules as detector_rules  # noqa: PLC0415
    from ..detector import window_rules  # noqa: PLC0415

    for _ in range(window_rules._TRIES):  # noqa: SLF001
        doc = detector_repo.get_rules(user) or {}
        old = doc.get("rules", [])
        todo = [r for r in old if not r.get("kind") and not r.get("migrated") and r.get("enabled") is not False
                and r.get("mode") != "ai" and (r.get("taskId") or r.get("projectId"))]
        if len(old) + len(todo) > detector_rules.MAX_RULES:
            raise ConflictError(f"迁移后规则会有 {len(old) + len(todo)} 条，超过 {detector_rules.MAX_RULES} 条上限 "
                                f"{len(old) + len(todo) - detector_rules.MAX_RULES} 条：请先删掉一些规则再打开三级分类")
        by_id = {r["id"]: r for r in old}
        taken = {r["block"] for r in old if r.get("kind") == "block"}
        rules, pairs = [], []  # pairs：要有第二级规则的 (块名, 旧规则)
        for r in old:
            rid = "rm_" + hashlib.sha256(r["id"].encode()).hexdigest()[:12]
            if r not in todo:
                rules.append(r)
                if r.get("migrated") and rid in by_id:  # 之前迁过的：第二级规则可能没写成，补
                    pairs.append((by_id[rid]["block"], r))
                continue
            name = _unique((r.get("note") or "").strip()[:64].strip() or f"旧规则 {r['id']}"[:64].strip(), taken)
            rules.append({**r, "migrated": True})
            if rid not in by_id:
                rules.append(detector_rules._validate([{"id": rid, "kind": "block", "app": r.get("app"), "title": r.get("title"),  # noqa: SLF001
                                                        "block": name, "enabled": True}])[0])
            pairs.append((name, r))
        pairs = [(n, r) for n, r in pairs if r.get("confidence", 0.9) >= 0.9]
        fresh = len(pairs) - len(blocks_repo.rules_by_keys(user, [n for n, _ in pairs]))
        if blocks_repo.rule_count(user) + fresh > blocks_repo.MAX_RULES:
            raise ConflictError(f"迁移会让第二级规则超过 {blocks_repo.MAX_RULES} 条上限：请先删掉一些第二级规则再打开三级分类")
        if todo and detector_repo.replace_rules(user, doc.get("version", 0), rules, datetime.now(timezone.utc)) is None:
            continue  # 撞版本：重读重来
        for name, r in pairs:  # CAS 之后再写（同块名已有的不动）
            block_rules.create(name, r.get("taskId"), r.get("projectId"), via="migration",
                               author="ai" if r.get("author") == "assistant" else "human", keep_existing=True)
        return len(todo)
    raise RuntimeError("分类规则一直被别处改动，迁移没写成；请重试")
