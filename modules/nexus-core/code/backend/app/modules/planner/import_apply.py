"""JSON 一键导入编辑：把一份已算好的 `PlanResult` 真正落库（契约 v1.7）。

**每一条动作都过 `guard.run_write`**——与统一 CRUD 入口九条写路由用同一个
执行通道（判来源 → 高风险设防 → 执行 → 留审计）。本文件对每条动作固定传
`body_actor="human"`（契约「actor=human，人的路径」）：这不是绕过 v1.6 的
设防，恰恰相反——带 AI 凭据的调用方在这里自报 "human" 会被 `guard` 判成
伪装（`ActorForgeryError` → 403），v1.6"自称 human 必须有凭据"那条不对称
判据不需要 import 这边另写一遍就自动生效；严格模式下未携人路径凭据的高
风险动作同样会被拒——import 与统一入口共用同一份设防，不是另一套。

**apply 不是事务**：Mongo 没有跨对象事务，本文件按「创建（zones→projects→
tasks）→ 更新（同顺序）→ 删除（tasks→projects→zones，子先于父）」的顺序
逐条执行，**遇到第一个异常就中止、已成功的动作不回滚**——与既有
`guard.run_write` 的哲学一致：批量写崩在半路，靠 `GET /api/core/planner/audit`
按 `seq` 查"做到第几步"，import 批量写崩在半路同样靠它，不需要另建一套
"事务日志"。
"""

from __future__ import annotations

from typing import Any

from . import audit, guard, service
from .actor import with_actor
from .import_diff import TYPES, PlanResult

_DELETE_ORDER = tuple(reversed(TYPES))  # tasks, projects, zones——子先于父


def _create_zone(fields: dict, actor: str | None) -> dict:
    return service.create_zone(
        fields.get("name"), color=fields.get("color"), order=fields.get("order"), actor=actor,
    )


def _create_project(fields: dict, actor: str | None) -> dict:
    return service.create_project(
        fields.get("zoneId"), fields.get("name"),
        planned_weight=fields.get("plannedWeight"), plan=fields.get("plan"), actor=actor,
    )


def _create_task(fields: dict, actor: str | None) -> dict:
    kind = fields.get("kind")
    return service.create_task(
        fields.get("name"), fields.get("projectId"),
        kind=kind if kind is not None else "normal",
        flags=fields.get("flags"), planned_weight=fields.get("plannedWeight"),
        plan=fields.get("plan"), depends_on=fields.get("dependsOn"), actor=actor,
    )


_CREATE_FN = {"zones": _create_zone, "projects": _create_project, "tasks": _create_task}
_UPDATE_FN = {
    "zones": service.update_zone,
    "projects": service.update_project,
    "tasks": service.update_task,
}
_DELETE_FN = {
    "zones": service.delete_zone,
    "projects": service.delete_project,
    "tasks": service.delete_task,
}


def apply_plan(plan: PlanResult, request: Any) -> dict[str, list[dict]]:
    """真正执行 `plan.ops` 里的动作，返回按类型分组的执行结果（`create` 已
    回填真实 id）。**调用方必须已经核对过 checksum**——本函数不再重复核对。
    """
    applied: dict[str, list[dict]] = {type_: [] for type_ in TYPES}

    for type_ in TYPES:
        for op in plan.ops[type_]:
            if op["op"] != "create":
                continue
            fields = op["fields"]
            result = guard.run_write(
                request, op=audit.OP_CREATE, object_type=type_,
                body_actor="human", changes=fields,
                action=lambda actor, t=type_, f=fields: _CREATE_FN[t](f, actor),
            )
            applied[type_].append({"op": "create", "id": result["id"], "fields": fields})

    for type_ in TYPES:
        for op in plan.ops[type_]:
            if op["op"] != "update":
                continue
            fields = op["fields"]
            entity_id = op["id"]
            guard.run_write(
                request, op=audit.OP_UPDATE, object_type=type_, entity_id=entity_id,
                body_actor="human", changes=fields,
                action=lambda actor, t=type_, i=entity_id, f=fields: _UPDATE_FN[t](
                    i, with_actor(f, actor),
                ),
            )
            applied[type_].append({"op": "update", "id": entity_id, "fields": fields})

    for type_ in _DELETE_ORDER:
        for op in plan.ops[type_]:
            if op["op"] != "delete":
                continue
            entity_id = op["id"]
            guard.run_write(
                request, op=audit.OP_DELETE, object_type=type_, entity_id=entity_id,
                body_actor="human",
                action=lambda _actor, t=type_, i=entity_id: _DELETE_FN[t](i),
            )
            applied[type_].append({"op": "delete", "id": entity_id, "fields": {}})

    return applied
