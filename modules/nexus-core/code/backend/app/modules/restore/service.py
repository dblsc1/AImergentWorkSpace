"""快照恢复（契约 v1.9「快照恢复」）：``GET /api/core/export`` 的逆操作。

export 产出的快照此前没有任何端点能吃回去。import 是**编辑通道**：不认外来
id、拒收 events、不许父子同批新建——三条拒绝各守一件实事，一条都不该放宽。
本端点是第四条通道，吃 export 的逐字节输出：

- **id 原样保留**；``events`` **原样落台账**（唯一允许直写台账的地方：它搬的是
  同一份台账，不是编辑）；``projections`` 读进来但**不落库**，落完台账现场重建。
- **只对空实例开放**：zones/projects/tasks/events 任一非空 → 409。
- 两段式同 import：默认 dry-run 零写入，只回 summary + checksum；apply 须带
  checksum。

**红线**同 export：本文件不 import 任何子边界的 ``repo.py``，只调对方
``service.py``；projector 走 ``rebuild.py`` 的公开函数（同 export 走
``handlers/*.py`` 的先例），错误类型复用 ``planner/errors.py``（同
``timer/backfill.py`` 的先例），不另定义一套 400/409。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from ..events import service as events_service
from ..planner import service as planner_service
from ..planner.errors import InvalidInputError, StalePlanError
from ..projector.rebuild import rebuild

TYPES: tuple[str, ...] = ("zones", "projects", "tasks")
_LABEL = {"zones": "分区", "projects": "项目", "tasks": "任务"}
#: 父引用字段 → 父类型（引用闭包只在快照内部判，库是空的）。
_PARENT = {"projects": ("zoneId", "zones"), "tasks": ("projectId", "projects")}
#: 400 里最多点名几条坏信封——全列出来会把一份几千条的坏文件原样吐回去。
_MAX_REJECTED_SHOWN = 5


class NotEmptyError(RuntimeError):
    """目标实例非空 → 409。请求本身合法，冲突的是**当前状态**（同
    ``StalePlanError``/``HasChildrenError`` 的形状）。"""


def _checksum(snapshot: dict[str, list[dict]]) -> str:
    """对**将要写入的内容**（四个数组，不含 projections/exportedAt）取 sha256。

    与 import 不同，这里不需要把「当前库」并进哈希：计划只取决于快照本身，
    「dry-run 之后库变了」由 apply 时重判「库必须为空」承担。
    """
    canonical = json.dumps(
        {name: snapshot[name] for name in (*TYPES, "events")},
        sort_keys=True, ensure_ascii=False, separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _ids(type_: str, docs: list[dict]) -> set[str]:
    seen: set[str] = set()
    for index, doc in enumerate(docs):
        doc_id = doc.get("id")
        if not isinstance(doc_id, str) or not doc_id.strip():
            raise InvalidInputError(
                f"{type_}[{index}] 缺少 id 或 id 不是非空字符串——快照恢复原样保留 id，"
                f"没有 id 的对象不是 export 的输出（新建对象请走 POST /api/core/import）"
            )
        if doc_id in seen:
            raise InvalidInputError(f"{type_} 里 id {doc_id!r} 重复出现")
        seen.add(doc_id)
    return seen


def _check_planner(snapshot: dict[str, list[dict]]) -> None:
    """id 齐全不重复 + 引用在快照内闭合。在任何写入之前跑完——恢复是整份搬，
    写到一半才发现断链，留下的是一个既不空、也不完整的实例。"""
    ids = {type_: _ids(type_, snapshot[type_]) for type_ in TYPES}
    for type_, (field, parent_type) in _PARENT.items():
        for doc in snapshot[type_]:
            ref = doc.get(field)
            if not isinstance(ref, str) or ref not in ids[parent_type]:
                raise InvalidInputError(
                    f"{_LABEL[type_]} {doc['id']!r} 的 {field} 引用了快照里不存在的"
                    f"{_LABEL[parent_type]}：{ref!r}"
                )
    for task in snapshot["tasks"]:
        deps = task.get("dependsOn") or []
        if not isinstance(deps, list):
            raise InvalidInputError(f"任务 {task['id']!r} 的 dependsOn 必须是数组")
        for dep in deps:
            if not isinstance(dep, str) or dep not in ids["tasks"]:
                raise InvalidInputError(
                    f"任务 {task['id']!r} 的 dependsOn 引用了快照里不存在的任务：{dep!r}"
                )


def _check_events(events: list[dict]) -> None:
    rejected = events_service.check_restore_envelopes(events)
    if rejected:
        shown = "；".join(f"events[{r.index}]：{r.reason}" for r in rejected[:_MAX_REJECTED_SHOWN])
        raise InvalidInputError(
            f"快照里有 {len(rejected)} 条事件不是合法信封，一条都不写：{shown}"
        )


def _ensure_empty() -> None:
    counts = {
        "zones": len(planner_service.list_zones()),
        "projects": len(planner_service.list_projects()),
        "tasks": len(planner_service.list_tasks()),
        "events": len(events_service.iter_all_events()),
    }
    if any(counts.values()):
        raise NotEmptyError(
            f"目标实例不是空库（{', '.join(f'{k} {v}' for k, v in counts.items())}）——"
            f"这是恢复通道，不是合并通道：restore 只往空实例里搬一份完整快照。"
            f"要改现有数据请用 POST /api/core/import（编辑通道）"
        )


def restore(
    snapshot: dict[str, Any], request: Any, *, dry_run: bool, checksum: str | None,
) -> dict[str, Any]:
    """``POST /api/core/restore``。顺序：结构校验（400）→ 库必须为空（409）
    → checksum（400/409）→ 写 planner → 写台账 → 重建投影。"""
    _check_planner(snapshot)
    _check_events(snapshot["events"])
    _ensure_empty()

    expected = _checksum(snapshot)
    summary = {name: len(snapshot[name]) for name in (*TYPES, "events")}
    if dry_run:
        return {"dryRun": True, "checksum": expected, "summary": summary, "rebuilt": None}

    if not checksum:
        raise InvalidInputError(
            "apply（dryRun=false）缺少 checksum：先对同一份快照调一次 dry-run，"
            "再带着它返回的 checksum apply（契约「快照恢复」节）"
        )
    if checksum != expected:
        raise StalePlanError(
            f"checksum {checksum!r} 与这份快照算出的 {expected!r} 不一致——"
            f"apply 的快照不是 dry-run 过的那一份，请对它重新 dry-run"
        )

    # 设防（AI 来源 / 严格模式 → 403）在 planner 这一步、任何写入之前判定。
    planner_service.restore_snapshot(request, {t: snapshot[t] for t in TYPES})
    summary["events"] = events_service.restore_bulk(snapshot["events"])
    return {"dryRun": False, "checksum": expected, "summary": summary, "rebuilt": rebuild()}
