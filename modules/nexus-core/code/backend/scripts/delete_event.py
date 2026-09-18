#!/usr/bin/env python3
"""nexus-core · **单条**事实删除 —— 核心逻辑。

入口是同目录的 ``delete-event.sh``（它管闸门：库名白名单、重打库名确认、
删前自动备份）。本文件管定位、对照打印、删除与投影重建。
**不要绕过 .sh 直接在生产库上跑本文件** —— 那样等于把四道闸全拆了。

════════ 它与 prune-orphan-events 的分工（别混）════════

``prune-orphan-events`` 删的是**一类**事实，判据写死在代码里（指向已删实体的孤儿），
人只能选「删还是不删」，不能选「删哪些」。

本工具删的是**一条**事实，由人给出精确的事件 id。
它存在的理由是那类**判据抓不到、但确实不该存在**的事实——
实证 2026-08-03：一个 agent 测顶栏计时态时留了个计时没停，跑了 6h54m，
当时契约只有 start/stop，用户要停表**只能**往自己档案里写一条 6h54m 的假事实
（``evt_2c5e32cc04d6``，24850 秒）。它指向的任务真实存在，**不是孤儿**，
``prune-orphan-events`` 抓不到它。

════════ 只接受精确 id，不接受任何查询条件（写死，不可配置）════════

**这是有意的限制，不是没做完。** 批量删事实的能力已经由 ``prune-orphan-events``
提供且判据写死；再给一个「按条件删」等于把那道判据绕过去——
到那时「删哪些事实」就又变回一句话的事了，而事实是 append-only、
无软删除、无回收站（2026-08-01 已经用用户的全部计时历史付过一次学费）。

════════ 为什么 id 匹配到多条时**拒绝执行** ════════

⚠️ **事件的 ``id`` 字段不唯一。** 防重唯一索引建在 ``(user, source, dedupeKey)`` 上
（见 ``events/repo.py``），契约 B2 明确要求「同 ``id`` 不同 ``dedupeKey`` 两条都要落库」。

所以 ``{"id": <给定 id>}`` 可能匹配到多条。此时本工具**响亮拒绝**并列出全部候选，
要求用 ``--dedupe-key`` 把范围收敛到恰好一条。
「一条命令删掉了两条事实」是这个工具最容易犯、也最难挽回的错——
宁可让人多打一个参数，也不替他猜哪条才是他要的。

（``--dedupe-key`` 不是「查询条件」：它不能单独选出事实，只能在给定 id 内**消歧**。）

════════ 定位用 id，删除用 ``_id`` ════════

同 ``prune_orphan_events``：选谁删看 ``id``（业务身份，J10 三段式），
**执行删除必须用 ``_id``**（Mongo 主键，逐条精确），否则会误伤同 id 的邻居。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# app 包在 backend 根下；本文件在 backend/scripts/ 里，按路径回溯一层。
_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

#: 业务身份字段（J10 三段式）。**定位按它，不是 ``_id``。**
IDENTITY_FIELD = "id"


class EventNotFound(LookupError):
    """给定 id 在库里查无此事实。**必须响亮失败**（T5），不许静默退 0。"""


class AmbiguousEventId(LookupError):
    """给定 id 匹配到多条。拒绝执行，要求 ``--dedupe-key`` 消歧。"""


def _json(doc: dict) -> str:
    """把整条事实原样打印出来（``default=str`` 兜住 ObjectId / datetime）。

    删之前把**完整内容**摆在人眼前，是这个工具唯一的「你确定吗」——
    库名和 id 都可能打错，而一条事实长什么样人是认得出来的。
    """
    return json.dumps(doc, ensure_ascii=False, indent=2, default=str, sort_keys=True)


def locate(db, event_id: str, dedupe_key: str | None = None) -> dict:
    """按 ``id``（可选 ``dedupeKey`` 消歧）定位**恰好一条**事实。

    查无 → ``EventNotFound``；多于一条 → ``AmbiguousEventId``。
    两种情况都不返回、不猜、不「取第一条」。
    """
    query: dict = {IDENTITY_FIELD: event_id}
    if dedupe_key is not None:
        query["dedupeKey"] = dedupe_key

    matches = list(db["events"].find(query))

    if not matches:
        hint = f"（且 dedupeKey={dedupe_key!r}）" if dedupe_key else ""
        raise EventNotFound(
            f"库里没有 {IDENTITY_FIELD}={event_id!r}{hint} 的事实。什么都没动。\n"
            f"   核对一下：id 打全了吗？打的是不是**这个库**？\n"
            f"   （本工具只按精确 id 定位，不做前缀/模糊匹配——"
            f"删事实这件事上「差不多是它」不够。）"
        )

    if len(matches) > 1:
        lines = [
            f"{IDENTITY_FIELD}={event_id!r} 匹配到 {len(matches)} 条事实，拒绝执行。",
            "   事件 id **不唯一**（防重键是 (user, source, dedupeKey)，契约 B2 允许同 id 并存）。",
            "   本工具一次只删一条，不替你猜是哪条。候选：",
        ]
        for doc in matches:
            lines.append(
                f"     · dedupeKey={doc.get('dedupeKey')!r}  "
                f"time={doc.get('time')}  "
                f"durationSeconds={(doc.get('data') or {}).get('durationSeconds')}"
            )
        lines.append("   用 --dedupe-key <上面某一个> 把范围收敛到恰好一条再来。")
        raise AmbiguousEventId("\n".join(lines))

    return matches[0]


def render_plan(db, doc: dict, db_name: str, *, apply_mode: bool) -> str:
    """删除前的对照：模式、库、前后条数预告、**被删那条的完整内容**。"""
    total = db["events"].count_documents({})
    mode = "APPLY（真删）" if apply_mode else "DRY-RUN（只看不动）"

    lines = [
        "──────────────────────────────────────────────────────────",
        f"  库：{db_name}    模式：{mode}",
        "──────────────────────────────────────────────────────────",
        f"  定位方式：{IDENTITY_FIELD} == {doc.get(IDENTITY_FIELD)!r}"
        "（精确匹配，不接受任何查询条件）",
        "",
        f"  events 集合条数：{total} → {total - 1}（本次删 1 条）",
        "",
        "  ▼ 将删除的这一条（完整内容，逐字段核）",
    ]
    lines.extend(f"     {line}" for line in _json(doc).splitlines())
    lines.append("──────────────────────────────────────────────────────────")
    return "\n".join(lines)


def delete_one(db, doc: dict) -> int:
    """按 ``_id`` 精确删除这一条（见模块 docstring：定位用 id，删除用 ``_id``）。"""
    return db["events"].delete_one({"_id": doc["_id"]}).deleted_count


def rebuild_projections() -> dict[str, int]:
    """删完必须重建投影，否则 ``proj_current`` / ``proj_daily_stats`` 还带着
    已删事实的累计值，投影与事实**对不上**（同 prune 工具的 P5）。"""
    from app.modules.projector.rebuild import rebuild  # noqa: PLC0415

    return rebuild()


def run(db, db_name: str, event_id: str, *, apply_mode: bool,
        dedupe_key: str | None = None, out=None) -> dict:
    """完整流程。返回被（将）删除的那条事实，供调用方/测试断言。"""
    stream = out if out is not None else sys.stdout

    doc = locate(db, event_id, dedupe_key)  # 查无/歧义都在这里响亮抛出
    print(render_plan(db, doc, db_name, apply_mode=apply_mode), file=stream)

    if not apply_mode:
        print(
            "\nℹ️  DRY-RUN：一条都没动。确认无误后加 --apply（会重打库名 + 先备份）。",
            file=stream,
        )
        return doc

    before = db["events"].count_documents({})
    deleted = delete_one(db, doc)
    after = db["events"].count_documents({})

    print(f"\n🗑  已删除 {deleted} 条", file=stream)
    print(f"   events 条数：{before} → {after}", file=stream)

    # 对数：只许删掉 1 条。删多了或没删掉都必须响亮失败，
    # 不能「删完了但数量不对」还报成功——那正是「以为有保险其实没有」的形状。
    if deleted != 1 or after != before - 1:
        raise RuntimeError(
            f"对数不一致：计划删 1 实删 {deleted}；条数 {before} → {after}。"
            f"数据可能被并发改动，**请立刻用 restore.sh 还原刚才的备份**。"
        )
    print("   ✅ 对数一致（恰好删 1 条）", file=stream)

    counts = rebuild_projections()
    print(
        "\n🔄 投影已重建："
        + "，".join(f"{name} 重放 {n} 条" for name, n in sorted(counts.items())),
        file=stream,
    )
    return doc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="delete_event.py",
        description="按精确事件 id 删除单条事实（默认 dry-run）",
    )
    parser.add_argument("--event-id", required=True, help="要删除的事实的 id（精确匹配）")
    parser.add_argument(
        "--dedupe-key", default=None,
        help="同 id 有多条时用它消歧；它不能单独选出事实，只在给定 id 内收敛",
    )
    parser.add_argument("--apply", action="store_true", help="真删；不给则只打印")
    parser.add_argument(
        "--expect-db", required=True,
        help="期望连到的库名。与实际 NEXUS_DB_NAME 不符立即退出——"
        "这是 .sh 校过库名之后的**第二道锁**，防止 env 在中途被换掉",
    )
    args = parser.parse_args(argv)

    from app.config import settings  # noqa: PLC0415
    from app.repo import get_db  # noqa: PLC0415

    if settings.db_name != args.expect_db:
        print(
            f"❌ 库名不符：--expect-db={args.expect_db!r} 但 NEXUS_DB_NAME="
            f"{settings.db_name!r}。已中止，什么都没动。",
            file=sys.stderr,
        )
        return 2

    try:
        run(
            get_db(), settings.db_name, args.event_id,
            apply_mode=args.apply, dedupe_key=args.dedupe_key,
        )
    except (EventNotFound, AmbiguousEventId) as exc:
        # 响亮失败：非 0 退出码 + 说清原因。**绝不静默退 0**——
        # 「什么都没找到但报成功」会让人以为删干净了（T5）。
        print(f"❌ {exc}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
