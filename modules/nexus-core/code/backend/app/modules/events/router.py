"""HTTP 层：路径、入参、响应模型。**不许有业务判断。**

入参声明成「信封对象或其数组」的**原始 JSON**，逐条校验在 ``service.py``——
让 FastAPI 在这里按 Envelope 整批校验会把「部分失败不整批回滚」变成 422 整批拒，
那正是契约明文禁止的。

``GET`` 是 v0.6 新增的档案读端，与 ``POST`` 共用 ``/events`` 前缀但互不相关：
过滤/排序/分页口径全在 ``service.list_events``，本文件只透传查询参数。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query

from . import service
from .schemas import ArchiveOut, IngestOut

router = APIRouter(prefix="/events", tags=["events"])


@router.post("", response_model=IngestOut)
def ingest(payload: dict[str, Any] | list[dict[str, Any]]) -> IngestOut:
    return service.ingest(payload)


@router.get("", response_model=ArchiveOut)
def list_events(
    type: str | None = None,  # noqa: A002 —— 与查询参数名 `type` 保持一致（unified_router.py 同例）
    from_: Annotated[str | None, Query(alias="from")] = None,
    to: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> ArchiveOut:
    total, items = service.list_events(type_=type, from_=from_, to=to, limit=limit, offset=offset)
    return ArchiveOut(total=total, items=items)
