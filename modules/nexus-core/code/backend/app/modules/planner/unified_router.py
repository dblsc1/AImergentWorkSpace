"""统一 CRUD 入口 `/api/core/planner/{type}`（contract.md v0.5「统一 CRUD 入口」）。

**路由归一，校验不归一**：每个已知 `{type}` 有自己的静态路由，直接复用
`schemas.py` 里各自类型化的 pydantic 模型与 `service.py` 里与十二条旧端点
**完全相同**的函数——不复制业务逻辑，不写一个吃 dict 的动态处理器。
404/400/409 的状态码映射沿用 `main.py` 里已注册的域异常 handler
（`service.py` 抛的还是那几个异常类），本文件不新增映射、不许有业务判断。

**v1.6 起每条写路由都经 `guard.run_write`**（契约「actor 来源区分与高风险
二次设防」+「planner 审计流水」）：来源判定 → 高风险设防 → 执行 → 留审计。
这不是"路由里加了业务判断"——安全判定与留痕是横切关注点，业务判断仍全在
`service.py`；把它摊到十二条路由里逐条手写才是真的会漏。**漏接一条的后果
不是少了条审计，是那条路径没有二次设防**，因此有 AST 断言盯着（见
`tests/test_actor_source_guard.py`）。

未知 `{type}`（不是 zones/projects/tasks）由本文件末尾的兜底路由处理，
返回 404 并点名收到的值与合法取值——不许静默当成某个默认类型。

**路由注册顺序要紧**：Starlette/FastAPI 按“路由添加顺序”做首个匹配命中，
不是按“更具体优先”。所以本文件把 zones/projects/tasks 的静态路由（以及
v1.6 新增的只读伪 type `audit`）写在前面，`{type}` 兜底路由必须放在文件
最后——挪到前面会把 `/planner/zones` 这类合法路径也吞成“未知 type”。
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request, Response

from . import audit, guard, service
from .actor import with_actor
from .schemas import (
    AuditOut,
    ProjectCreate, ProjectOut, ProjectUpdate,
    TaskCreate, TaskOut, TaskUpdate,
    ZoneCreate, ZoneOut, ZoneUpdate,
)

router = APIRouter(prefix="/planner", tags=["planner-unified"])

_NO_CONTENT = Response(status_code=204)
_VALID_TYPES = ("zones", "projects", "tasks")

#: DELETE 没有请求体，自报 actor 只能走查询参数。**AI 的受控层根本没有删除
#: 工具**，所以这个参数不是给它用的——它是给"绕过受控层但仍诚实自报"的调用方
#: 用的：诚实自报 ai 的删除同样会被 403 拒掉（契约 v1.6 高风险表）。
_ActorParam = Literal["human", "ai"] | None


def _reject_unknown_type(type_: str) -> None:
    raise HTTPException(
        status_code=404,
        detail=f"未知 type：{type_!r}，合法取值 {'/'.join(_VALID_TYPES)}",
    )


# ------------------------------------------------------------------ zones


@router.get("/zones", response_model=list[ZoneOut])
def list_zones_unified() -> list[dict]:
    return service.list_zones()


@router.post("/zones", response_model=ZoneOut)
def create_zone_unified(body: ZoneCreate, request: Request) -> dict:
    return guard.run_write(
        request, op=audit.OP_CREATE, object_type="zones",
        body_actor=body.actor, changes=body.model_dump(exclude_unset=True),
        action=lambda actor: service.create_zone(
            body.name, color=body.color, order=body.order, actor=actor,
        ),
    )


@router.patch("/zones/{entity_id}", response_model=ZoneOut)
def update_zone_unified(entity_id: str, body: ZoneUpdate, request: Request) -> dict:
    fields = body.model_dump(exclude_unset=True)
    return guard.run_write(
        request, op=audit.OP_UPDATE, object_type="zones", entity_id=entity_id,
        body_actor=body.actor, changes=fields,
        action=lambda actor: service.update_zone(entity_id, with_actor(fields, actor)),
    )


@router.delete("/zones/{entity_id}", status_code=204)
def delete_zone_unified(
    entity_id: str, request: Request, actor: _ActorParam = Query(default=None),
) -> Response:
    guard.run_write(
        request, op=audit.OP_DELETE, object_type="zones", entity_id=entity_id,
        body_actor=actor, action=lambda _actor: service.delete_zone(entity_id),
    )
    return _NO_CONTENT


# ---------------------------------------------------------------- projects


@router.get("/projects", response_model=list[ProjectOut])
def list_projects_unified(zoneId: str | None = None) -> list[dict]:
    return service.list_projects(zoneId)


@router.post("/projects", response_model=ProjectOut)
def create_project_unified(body: ProjectCreate, request: Request) -> dict:
    return guard.run_write(
        request, op=audit.OP_CREATE, object_type="projects",
        body_actor=body.actor, changes=body.model_dump(exclude_unset=True),
        action=lambda actor: service.create_project(
            body.zoneId,
            body.name,
            planned_weight=body.plannedWeight,
            plan=body.plan.model_dump() if body.plan else None,
            actor=actor,
        ),
    )


@router.patch("/projects/{entity_id}", response_model=ProjectOut)
def update_project_unified(entity_id: str, body: ProjectUpdate, request: Request) -> dict:
    fields = body.model_dump(exclude_unset=True)
    return guard.run_write(
        request, op=audit.OP_UPDATE, object_type="projects", entity_id=entity_id,
        body_actor=body.actor, changes=fields,
        action=lambda actor: service.update_project(entity_id, with_actor(fields, actor)),
    )


@router.delete("/projects/{entity_id}", status_code=204)
def delete_project_unified(
    entity_id: str, request: Request, actor: _ActorParam = Query(default=None),
) -> Response:
    guard.run_write(
        request, op=audit.OP_DELETE, object_type="projects", entity_id=entity_id,
        body_actor=actor, action=lambda _actor: service.delete_project(entity_id),
    )
    return _NO_CONTENT


# ------------------------------------------------------------------- tasks


@router.get("/tasks", response_model=list[TaskOut])
def list_tasks_unified(projectId: str | None = None) -> list[dict]:
    return service.list_tasks(projectId)


@router.post("/tasks", response_model=TaskOut)
def create_task_unified(body: TaskCreate, request: Request) -> dict:
    return guard.run_write(
        request, op=audit.OP_CREATE, object_type="tasks",
        body_actor=body.actor, changes=body.model_dump(exclude_unset=True),
        action=lambda actor: service.create_task(
            body.name,
            body.projectId,
            kind=body.kind if body.kind is not None else "normal",
            flags=body.flags,
            planned_weight=body.plannedWeight,
            plan=body.plan.model_dump() if body.plan else None,
            depends_on=body.dependsOn,
            actor=actor,
        ),
    )


@router.patch("/tasks/{entity_id}", response_model=TaskOut)
def update_task_unified(entity_id: str, body: TaskUpdate, request: Request) -> dict:
    fields = body.model_dump(exclude_unset=True)
    return guard.run_write(
        request, op=audit.OP_UPDATE, object_type="tasks", entity_id=entity_id,
        body_actor=body.actor, changes=fields,
        action=lambda actor: service.update_task(entity_id, with_actor(fields, actor)),
    )


@router.delete("/tasks/{entity_id}", status_code=204)
def delete_task_unified(
    entity_id: str, request: Request, actor: _ActorParam = Query(default=None),
) -> Response:
    guard.run_write(
        request, op=audit.OP_DELETE, object_type="tasks", entity_id=entity_id,
        body_actor=actor, action=lambda _actor: service.delete_task(entity_id),
    )
    return _NO_CONTENT


# ---------------------------------------------- 审计流水读端（v1.6，只读伪 type）
# 必须在下面的 `{type}` 兜底之前注册；`audit` 只有 GET，POST/PATCH/DELETE
# 照常落进兜底的 404（它不是 zones/projects/tasks）。


@router.get("/audit", response_model=AuditOut)
def list_audit_unified(
    limit: int = Query(default=audit.DEFAULT_LIMIT),
    objectId: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    outcome: str | None = Query(default=None),
) -> dict:
    return audit.query(limit=limit, object_id=objectId, actor=actor, outcome=outcome)


# --------------------------------------------------------- 未知 type 兜底
# 必须放在上面所有 zones/projects/tasks/audit 静态路由之后（见文件头注释）。


@router.get("/{type}")
def list_unknown_type(type: str) -> None:
    _reject_unknown_type(type)


@router.post("/{type}")
def create_unknown_type(type: str) -> None:
    _reject_unknown_type(type)


@router.patch("/{type}/{entity_id}")
def update_unknown_type(type: str, entity_id: str) -> None:
    _reject_unknown_type(type)


@router.delete("/{type}/{entity_id}")
def delete_unknown_type(type: str, entity_id: str) -> None:
    _reject_unknown_type(type)
