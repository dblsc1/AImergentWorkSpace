"""HTTP 层：快照恢复端点。**不许有业务判断**（同其余模块红线）。

``restore/`` 与 ``export/`` 同构，是另立的薄子边界：它跨 planner/events/projector
三者写入，不属于其中任何一个——挂进 planner（像 import 那样）会让 planner
反过来依赖 events，分拆那天就不是「搬目录」了。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from . import service
from .schemas import RestoreRequest, RestoreResultOut

router = APIRouter(tags=["restore"])


@router.post("/restore", response_model=RestoreResultOut)
def restore_snapshot(
    body: RestoreRequest, request: Request,
    dryRun: bool = True, checksum: str | None = None,  # noqa: N803 —— 查询参数名即契约
) -> dict[str, Any]:
    return service.restore(body.model_dump(), request, dry_run=dryRun, checksum=checksum)
