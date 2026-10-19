"""HTTP 层：AI 提议新项目 / 新任务（契约 v2.30）。**不许有业务判断**，全在 ``new_items.py``。

提议（POST）的口径同 AI 报告：带 Bearer 的设备令牌在看请求体之前 403（MCP 对内直连不带）；读不设限；
批准 / 不要**只许人**（``ignore_router.human``：无 Bearer、无范围头、非匿名）。
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field, StrictStr

from . import new_items, service
from ..planner.errors import ForbiddenError
from .ignore_router import is_human
from .proposals import MAX_NAME

_Id = Annotated[StrictStr, Field(min_length=1, max_length=128)]


def _no_device_token(request: Request) -> None:
    service.forbid_device_token(request.headers.get("authorization"), "提议新项目或新任务")


def human(request: Request) -> None:
    if not is_human(request.headers.get("authorization")):
        raise ForbiddenError("只有登录的人能批准或不要提议；设备令牌、带范围的令牌和匿名上报都不行")


router = APIRouter(prefix="/activity/proposals", tags=["activity"])


class ProjectIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    zoneId: _Id
    name: StrictStr = Field(max_length=MAX_NAME * 4)  # 超长在 clean_name 里拒，这里只拦乱发的
    reason: StrictStr = Field("", max_length=new_items.MAX_REASON * 4)


class TaskIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    projectId: _Id | None = None
    projectProposalId: _Id | None = None
    name: StrictStr = Field(max_length=MAX_NAME * 4)
    reason: StrictStr = Field("", max_length=new_items.MAX_REASON * 4)


@router.post("/projects", dependencies=[Depends(_no_device_token)])
def propose_project(body: ProjectIn) -> dict:
    return new_items.propose_project(body.zoneId, body.name, body.reason)


@router.post("/tasks", dependencies=[Depends(_no_device_token)])
def propose_task(body: TaskIn) -> dict:
    return new_items.propose_task(body.projectId, body.projectProposalId, body.name, body.reason)


@router.get("")
def listing(status: Literal["pending", "all"] = "pending", limit: Annotated[int, Query(ge=1, le=200)] = 100) -> dict:
    return new_items.listing(status == "pending", limit)


@router.post("/{itemId}/approve", dependencies=[Depends(human)])
def approve(itemId: str, request: Request) -> dict:  # noqa: N803 —— 路径参数名即契约
    return new_items.approve(itemId, request)


@router.post("/{itemId}/reject", dependencies=[Depends(human)])
def reject(itemId: str) -> dict:  # noqa: N803
    return new_items.reject(itemId)
