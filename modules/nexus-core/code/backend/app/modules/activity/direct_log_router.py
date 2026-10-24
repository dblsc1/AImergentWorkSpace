"""HTTP 层（契约 v2.38「五」）：直接施加日志的读（人与 MCP，设备令牌不行）与撤回（只许人）。**不许有业务判断**，全在 ``direct_log.py``。"""

from __future__ import annotations

from datetime import date as Date
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query

from . import direct_log
from .ignore_router import human, is_human, no_bearer

router = APIRouter(prefix="/activity/direct-log", tags=["activity"])


@router.get("", dependencies=[Depends(no_bearer)])
def listing(date: Date | None = None, limit: Annotated[int, Query(ge=1, le=200)] = 50,  # noqa: A002
            authorization: Annotated[str | None, Header()] = None) -> dict:
    return direct_log.listing(date, limit, is_human(authorization))


@router.post("/{log_id}/undo", dependencies=[Depends(human)])
def undo(log_id: str) -> dict:
    return direct_log.undo(log_id)
