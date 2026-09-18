#!/usr/bin/env python3
"""nexus-core · 孤儿 ``session.completed`` 事实清理 —— **核心逻辑**。

入口是同目录的 ``prune-orphan-events.sh``（它管闸门：库名白名单、重打库名确认、
删前自动备份）。本文件管判据、对照打印、删除与投影重建。**不要绕过 .sh 直接
在生产库上跑本文件** —— 那样等于把三道闸全拆了。

════════ 为什么删 events 不违反 append-only（原样保留，给下一个人看）════════

事实是 append-only，**这是设计**。但那 44 条**从来没有发生过** ——
它们不是用户的历史，是我们自己的工具注入的产物。

**append-only 保护的是真实发生过的事实不被改写，不是保护我们自己制造的污染。**
清掉它们是**恢复真相**，不是篡改历史。

（背景：E2E 夹具的 teardown 删掉了它自己造的 project/task 实体，而它注入的
``session.completed`` 事实里那几个 id 还指着它们，于是前端 join 不到名字，
档案里显示成 ``p_xxxxxx（项目已删除）``。**「造完自己清」这个模式在 append-only
系统里会制造孤儿。**）

════════ 判据（P4，写死，不可配置）════════

``type == "session.completed"`` 且（``subject.project`` 不在 projects 的 ``id``
集合 **或** ``subject.task`` 不在 tasks 的 ``id`` 集合）。

⚠️ **身份在 ``id`` 字段，不是 ``_id``**（J10 三段式）。``_id`` 是 Mongo 自己的
ObjectId，跟 ``subject.*`` 里的 ``p_``/``t_`` 前缀 id 根本不是一套东西；拿 ``_id``
去比会得到「**全部都是孤儿**」这种看着像结论、其实是 bug 的结果。
本模块的 ``planner/repo.py`` 全部按 ``{"id": ...}`` 查，唯一索引也建在 ``id`` 上。

一处刻意的收紧，**与任务单字面不同，理由在此**：``subject.task`` 在契约里是
**选填**（``schemas.Subject.task: str | None``）。若某条 ``session.completed``
压根没带 task，它不是「指向了一个不存在的任务」——它没指向任何任务，**没有悬空
引用可言**。按字面「task 不在 tasks 的 id 集合」会把这种**合法的无任务事实**
一起删掉，那才是真的篡改历史。所以本实现是：
``project 悬空 or (task 存在 and task 悬空)``。
对本次的 44 条无影响（它们 project 与 task 都带且都已悬空），但把「误删真实
事实」这条路堵死了。``tests/test_prune_orphan_events.py::test_p4_taskless_real_event_is_kept``
把这个行为钉住。

════════ 删除用 ``_id``，判据用 ``id`` —— 这不矛盾 ════════

选谁删（判据）看 ``id``；**执行删除必须用 ``_id``**。因为事件的 ``id`` 字段
**不唯一**（契约 B2：同 ``id`` 不同 ``dedupeKey`` 两条都要落库），拿它当删除条件
会误伤旁边那条。``_id`` 是 Mongo 主键，逐条精确。
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

# app 包在 backend 根下；本文件在 backend/scripts/ 里，按路径回溯一层。
_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

#: 只清这一个 type。写死，不做成参数——「能配的判据」等于「能配错的判据」。
TARGET_TYPE = "session.completed"

#: 实体身份字段（J10 三段式）。**这是本工具唯一正确的判据字段。**
#: 测试用它做反向验证：改成 "_id" 后断言必须变红（见 tests 里的 reverse 用例）。
IDENTITY_FIELD = "id"


@dataclass
class Plan:
    """一次清理的完整计划。dry-run 与 --apply 看到的是**同一个** Plan——
    「预览的和执行的不是同一份计算」是这类工具最经典的骗人方式。"""

    db_name: str
    orphans: list[dict] = field(default_factory=list)
    kept: list[dict] = field(default_factory=list)
    project_names: dict[str, str] = field(default_factory=dict)
    task_names: dict[str, str] = field(default_factory=dict)
    total_events: int = 0

    @property
    def target_total(self) -> int:
        return len(self.orphans) + len(self.kept)


def _live_ids(db, collection: str) -> set[str]:
    """取某集合现存实体的身份集合。**按 IDENTITY_FIELD 取，不是 _id。**"""
    return {
        doc[IDENTITY_FIELD]
        for doc in db[collection].find({}, {IDENTITY_FIELD: 1, "_id": 0})
        if doc.get(IDENTITY_FIELD)
    }


def is_orphan(event: dict, project_ids: set[str], task_ids: set[str]) -> bool:
    """判据本体（P4）。单独成函数，好让测试直接对它下断言。"""
    subject = event.get("subject") or {}
    project = subject.get("project")
    task = subject.get("task")

    project_dangling = project not in project_ids
    # task 缺省 = 没有指向，不是悬空指向（见模块 docstring 的收紧说明）
    task_dangling = task is not None and task not in task_ids
    return project_dangling or task_dangling


def collect(db, db_name: str) -> Plan:
    """扫一遍，分出孤儿与保留项。**只读，绝不写。**"""
    project_ids = _live_ids(db, "projects")
    task_ids = _live_ids(db, "tasks")

    plan = Plan(
        db_name=db_name,
        project_names={
            d[IDENTITY_FIELD]: d.get("name", "")
            for d in db["projects"].find({}, {IDENTITY_FIELD: 1, "name": 1, "_id": 0})
            if d.get(IDENTITY_FIELD)
        },
        task_names={
            d[IDENTITY_FIELD]: d.get("name", "")
            for d in db["tasks"].find({}, {IDENTITY_FIELD: 1, "name": 1, "_id": 0})
            if d.get(IDENTITY_FIELD)
        },
        total_events=db["events"].count_documents({}),
    )

    for event in db["events"].find({"type": TARGET_TYPE}):
        if is_orphan(event, project_ids, task_ids):
            plan.orphans.append(event)
        else:
            plan.kept.append(event)
    return plan


def _seconds(event: dict) -> int | None:
    data = event.get("data") or {}
    value = data.get("durationSeconds")
    return value if isinstance(value, int) else None


def _fmt_duration(seconds: int | None) -> str:
    if seconds is None:
        return "时长缺失"
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m{seconds % 60:02d}s"
    return f"{seconds // 3600}h{seconds % 3600 // 60:02d}m"


def _describe(event: dict, plan: Plan) -> str:
    """一行摘要：时间 + 时长 + 项目名（P6）。实体已删的显式标出来。"""
    subject = event.get("subject") or {}
    project = subject.get("project") or "<无>"
    task = subject.get("task")

    project_label = plan.project_names.get(project) or f"{project}（项目已删除）"
    if task is None:
        task_label = "—"
    else:
        task_label = plan.task_names.get(task) or f"{task}（任务已删除）"

    return (
        f"{event.get('time', '<无时间>'):<32} "
        f"{_fmt_duration(_seconds(event)):>8}  "
        f"{project_label} / {task_label}"
    )


def render_plan(plan: Plan, *, apply_mode: bool) -> str:
    """把计划渲染成人能一眼核的对照（P6）。dry-run 与 --apply 共用。"""
    lines: list[str] = []
    mode = "APPLY（真删）" if apply_mode else "DRY-RUN（只看不动）"
    lines.append("──────────────────────────────────────────────────────────")
    lines.append(f"  库：{plan.db_name}    模式：{mode}")
    lines.append("──────────────────────────────────────────────────────────")
    lines.append("  判据（写死）：")
    lines.append(f'    type == "{TARGET_TYPE}" 且')
    lines.append(f"    （subject.project 不在 projects 的 {IDENTITY_FIELD} 集合")
    lines.append(f"      或 subject.task 存在且不在 tasks 的 {IDENTITY_FIELD} 集合）")
    lines.append(f'    ⚠️ 身份字段 = "{IDENTITY_FIELD}"，不是 "_id"（J10 三段式）')
    lines.append("")
    lines.append(f"  events 集合总条数        : {plan.total_events}")
    lines.append(f"  其中 {TARGET_TYPE:<18}: {plan.target_total}")
    lines.append(f"    ├ 判为孤儿（将删除）    : {len(plan.orphans)}")
    lines.append(f"    └ 判为真实（保留）      : {len(plan.kept)}")
    lines.append("")

    lines.append(f"  ▼ 将删除的 {len(plan.orphans)} 条（实体已不存在）")
    if not plan.orphans:
        lines.append("     （无）")
    for event in sorted(plan.orphans, key=lambda e: e.get("time", "")):
        lines.append(f"     - {_describe(event, plan)}")
    lines.append("")

    lines.append(f"  ▲ 保留的 {len(plan.kept)} 条（时间 / 时长 / 项目名 · 逐条核）")
    if not plan.kept:
        lines.append("     （无）")
    for event in sorted(plan.kept, key=lambda e: e.get("time", "")):
        lines.append(f"     + {_describe(event, plan)}")
    lines.append("──────────────────────────────────────────────────────────")
    return "\n".join(lines)


def delete_orphans(db, plan: Plan) -> int:
    """按 ``_id`` 逐条精确删除（见模块 docstring：判据用 id，删除用 _id）。"""
    if not plan.orphans:
        return 0
    ids = [event["_id"] for event in plan.orphans]
    return db["events"].delete_many({"_id": {"$in": ids}}).deleted_count


def rebuild_projections() -> dict[str, int]:
    """删完必须重建投影（P5），否则 proj_current / proj_daily_stats 还带着
    已删事实的累计值，投影与事实**对不上**。"""
    from app.modules.projector.rebuild import rebuild  # noqa: PLC0415

    return rebuild()


def run(db, db_name: str, *, apply_mode: bool, out=None) -> Plan:
    """完整流程。返回 Plan 供调用方/测试断言。"""
    stream = out if out is not None else sys.stdout
    plan = collect(db, db_name)
    print(render_plan(plan, apply_mode=apply_mode), file=stream)

    if not apply_mode:
        print(
            "\nℹ️  DRY-RUN：一条都没动。确认无误后加 --apply（会重打库名 + 先备份）。",
            file=stream,
        )
        return plan

    if not plan.orphans:
        print("\n✅ 没有孤儿事实，无需删除。", file=stream)
        return plan

    before = db["events"].count_documents({"type": TARGET_TYPE})
    deleted = delete_orphans(db, plan)
    after = db["events"].count_documents({"type": TARGET_TYPE})

    print(f"\n🗑  已删除 {deleted} 条", file=stream)
    print(f"   {TARGET_TYPE} 条数：{before} → {after}", file=stream)

    # 对数：删了多少、剩多少，必须与计划严丝合缝。对不上就响亮失败，
    # 不能「删完了但数量不对」还报成功——那正是「以为有保险其实没有」的形状。
    if deleted != len(plan.orphans) or after != len(plan.kept):
        raise RuntimeError(
            f"对数不一致：计划删 {len(plan.orphans)} 实删 {deleted}；"
            f"计划留 {len(plan.kept)} 实剩 {after}。"
            f"数据可能被并发改动，**请立刻用 restore.sh 还原刚才的备份**。"
        )
    print("   ✅ 对数一致（实删 == 计划删，实剩 == 计划留）", file=stream)

    counts = rebuild_projections()
    print(
        "\n🔄 投影已重建："
        + "，".join(f"{name} 重放 {n} 条" for name, n in sorted(counts.items())),
        file=stream,
    )
    return plan


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="prune_orphan_events.py",
        description="清理指向已删除实体的孤儿 session.completed 事实（默认 dry-run）",
    )
    parser.add_argument("--apply", action="store_true", help="真删；不给则只打印")
    parser.add_argument(
        "--expect-db",
        required=True,
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

    run(get_db(), settings.db_name, apply_mode=args.apply)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
