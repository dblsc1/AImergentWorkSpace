"""HTTP 层：JSON 一键导入编辑端点（契约 v1.7「JSON 一键导入编辑」）。

挂在 `/api/core/import`（`main.py` 直接 `include_router`，不在 `/planner`
前缀下）——它与 `export/router.py` 是同一对兄弟：导出给你一份 JSON，改完
从这里导回去，路径上刻意对称。之所以仍然放在 `planner/` 包内（不像
`export/` 那样另立子边界）：本端点重的是复用 `guard.py`/`audit.py`/
`service.py` 这几个 planner 内部件，跨边界只准调 `service.py` 的规矩在这里
不适用——它本身就是 planner 的一部分,不是外人。

**两段式**：默认 dry-run（零写入，只回 diff 计划 + 计划的 checksum）；真正
写入（`dryRun:false`）必须带上 dry-run 那份计划的 checksum，服务端拿同一份
payload 对**当前**库重新算一遍计划、重新算一遍 checksum，两者不一致就拒——
这就是"过时计划"的判据，无状态、纯函数可复现，不需要服务端缓存上一次的计划。

**红线**：`events`/`projections` 出现在请求体里（哪怕值是 `null`）直接
400 并点名——import 只碰 planner（zones/projects/tasks），不许静默忽略事实
台账字段（events 是 append-only 事实台账，不容绕过）。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from .errors import InvalidInputError, StalePlanError
from .import_apply import apply_plan
from .import_diff import build_plan
from .schemas import ImportRequest, ImportResultOut

router = APIRouter(tags=["import"])

#: 出现即 400 并点名——不是"未知字段"意义上的多余字段，是"这两个字段专门
#: 声明出来就是为了被本函数抓住"（见 `schemas.ImportRequest` 的类文档）。
_FORBIDDEN_FIELDS = ("events", "projections")


def _reject_forbidden_fields(body: ImportRequest) -> None:
    present = [name for name in _FORBIDDEN_FIELDS if name in body.model_fields_set]
    if not present:
        return
    raise InvalidInputError(
        "import 请求体不接受字段 "
        + "、".join(repr(name) for name in present)
        + "——它们是只读事实台账/派生投影，本端点只碰 planner"
          "（zones/projects/tasks）；请从导出的 JSON 里删除这些键后再提交，"
          "不许静默忽略（events 是 append-only 事实台账，不容绕过）"
    )


@router.post("/import", response_model=ImportResultOut)
def import_planner(body: ImportRequest, request: Request) -> dict[str, Any]:
    _reject_forbidden_fields(body)
    payload = {"zones": body.zones, "projects": body.projects, "tasks": body.tasks}
    plan = build_plan(payload, allow_delete=body.allowDelete)

    if body.dryRun:
        return {
            "dryRun": True,
            "checksum": plan.checksum,
            "allowDelete": plan.allow_delete,
            "plan": plan.ops,
            "applied": None,
            "summary": plan.summary,
            "skippedDeletes": plan.skipped_deletes,
        }

    if not body.checksum:
        raise InvalidInputError(
            "apply（dryRun=false）缺少 checksum：必须先调一次 dry-run 拿到这份"
            "计划的 checksum，再原样带着它 apply——这是防止拿一份过时计划写库"
            "的唯一凭据（契约「JSON 一键导入编辑」节）"
        )
    if body.checksum != plan.checksum:
        raise StalePlanError(
            f"计划已过期：提供的 checksum {body.checksum!r} 与对当前库重新算出的"
            f" {plan.checksum!r} 不一致——库在 dry-run 之后被改动过（或者"
            f" payload/allowDelete 变了），请重新对同一份 JSON 跑一次 dry-run"
            f" 拿到最新计划与 checksum 后再 apply"
        )

    applied = apply_plan(plan, request)
    return {
        "dryRun": False,
        "checksum": plan.checksum,
        "allowDelete": plan.allow_delete,
        "plan": None,
        "applied": applied,
        "summary": plan.summary,
        "skippedDeletes": None,
    }
