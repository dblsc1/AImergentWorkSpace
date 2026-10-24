"""窗口块名 ``blockKey`` 与来源 ``keySource``（契约 v2.38「一、原始记录」）以及第一级规则变化后的重算。

上传时**不**匹配规则（检测程序已经按规则算过；``block: null`` 就是没命中）：``block`` 非空 → 用它（``"rule"``），否则 ``app · 归一化标题``（``"auto"``）。
**重算**：每条记录存下算它时的规则集版本 ``rv``。读块时（``blocks.py``）惰性检查：最近 14 天里标题不是代号、``rv`` 不是当前版本的记录，
若所在块还没分类（没文档或文档 ``status`` 为 null）就按当前规则集重算（含原本来自检测程序的），不管改没改都盖上新 ``rv``；
一次至多 5000 条，余下的下次读时继续。选「读时惰性」而不是「规则写入后触发」：规则有 PUT / 草稿 / ai-apply / 迁移四条写路，读时一个检查点就全盖住。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ... import config
from . import blocks_repo, history, records_repo

MAX_BLOCK, MAX_TITLE = 64, 512
RECOMPUTE_DAYS, RECOMPUTE_BATCH = 14, 5000


def default_key(app: str, title: str) -> str:
    """``app · 归一化标题``（归一化同匹配历史的 ``normTitle``）；标题为空就是 ``app``。"""
    t = history.norm_title(title)
    return f"{app} · {t}" if t else app


def _pending(user: str, rows: list[dict]) -> set[tuple[str, str]]:
    """候选记录所在的块里，生效状态（契约「二」）是待分类的：文档没有 / ``status`` 为 null，不是通用窗口，也没被可用的第二级规则分走。"""
    from . import block_rules  # noqa: PLC0415

    dates = {r["date"] for r in rows}
    docs = {(d, k): doc for d in dates for k, doc in blocks_repo.blocks_of_day(user, d).items()}
    generic = blocks_repo.generic_since(user)
    keys = list({r["blockKey"] for r in rows})
    ruled = set(block_rules.effective(user, keys))  # 第二级规则只管最近 14 天，候选本来就只有最近 14 天的
    return {(r["date"], r["blockKey"]) for r in rows
            if (docs.get((r["date"], r["blockKey"])) or {}).get("status") is None
            and not (r["blockKey"] in generic and generic[r["blockKey"]] <= r["date"]) and r["blockKey"] not in ruled}


def recompute_stale(user: str) -> None:
    """已知窗口：判断「待分类」到写回 ``blockKey`` 之间没有跨集合的原子性——这一瞬间并发的 assign 可能让一块里
    刚被重算的记录与同块别的记录分了家（只影响这次重算的记录，下一次规则版本变化时会再算；人可再 reset）。契约没有更强的要求。"""
    from ..detector import window_rules  # noqa: PLC0415 —— detector 反过来 import activity，顶层 import 成环

    version, matchers = window_rules.block_matchers(user)
    from_date = (datetime.now(timezone.utc).astimezone(config.settings.tz) - timedelta(days=RECOMPUTE_DAYS)).date().isoformat()
    rows = records_repo.stale(user, from_date, version, RECOMPUTE_BATCH)
    if not rows:
        return
    pending = _pending(user, rows)
    out = []
    for r in rows:
        key, src = r["blockKey"], r["keySource"]
        if (r["date"], key) in pending and len(r["title"]) < MAX_TITLE:  # 标题被截断过的记录规则是按全文写的，不重算
            hit = window_rules.block_hit(matchers, r["app"], r["title"])
            if hit:
                key, src = hit, "rule"
            elif src == "auto":  # 新规则没命中：原来来自检测程序（rule）的块名保留
                key = default_key(r["app"], r["title"])
        out.append((r["_id"], key, src, version))
    records_repo.apply_keys(out)
