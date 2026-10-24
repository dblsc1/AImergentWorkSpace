"""HTTP 层：AI 提议新项目 / 新任务（契约 v2.30）。**不许有业务判断**，全在 ``new_items.py``。

提议（POST）的口径同 AI 报告：带 Bearer 的设备令牌在看请求体之前 403（MCP 对内直连不带）；读不设限；
批准 / 不要**只许人**（``ignore_router.human``：无 Bearer、无范围头、非匿名）。
"""

from __future__ import annotations

from typing import Annotated, Literal

from datetime import date as Date

from fastapi import APIRouter, Body, Depends, Header, Query, Request
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr

from ...scope import caller
from . import ignore, new_items, service, tier_items
from ..planner.errors import ForbiddenError
from .ignore_router import is_human
from .proposals import MAX_NAME

_Id = Annotated[StrictStr, Field(min_length=1, max_length=128)]


def _no_device_token(request: Request) -> None:
    service.forbid_device_token(request.headers.get("authorization"), "提议新项目或新任务")


def _no_device_token_unless_write(request: Request) -> None:
    """同 v2.30，但 ``write`` 范围的令牌（经网关转来的，带 Bearer）放行：直接施加只接它们（契约待定，见 report）。"""
    c = caller()
    if not (c.scope == "write" and not c.anonymous):
        _no_device_token(request)


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


class ChangeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: Literal["project", "task"]
    targetId: _Id
    op: Literal["rename", "move", "delete"]
    name: StrictStr | None = Field(None, max_length=MAX_NAME * 4)
    toId: _Id | None = None
    reason: StrictStr = Field("", max_length=new_items.MAX_REASON * 4)
    directApply: StrictBool = False


class IgnoreIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    app: StrictStr = Field(min_length=1, max_length=ignore.MAX_APP * 4)
    titleContains: StrictStr | None = Field(None, max_length=ignore.MAX_TITLE * 4)
    reason: StrictStr = Field("", max_length=new_items.MAX_REASON * 4)
    directApply: StrictBool = False


_Auth = Annotated[str | None, Header()]


@router.post("/changes", dependencies=[Depends(_no_device_token_unless_write)])
def propose_change(body: ChangeIn, request: Request) -> dict:
    return new_items.propose_change(body.target, body.targetId, body.op, body.name, body.toId, body.reason, body.directApply, request)


@router.post("/ignores", dependencies=[Depends(_no_device_token_unless_write)])
def propose_ignore(body: IgnoreIn, authorization: _Auth = None) -> dict:
    return new_items.propose_ignore(body.app, body.titleContains, body.reason, is_human(authorization), body.directApply)


class _RuleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: StrictStr | None = Field(None, max_length=64)
    app: StrictStr | None = Field(None, max_length=200)
    title: StrictStr | None = Field(None, max_length=200)
    block: StrictStr | None = Field(None, max_length=64 * 4)


class WindowRuleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    op: Literal["add", "edit", "delete"]
    rule: _RuleIn
    reason: StrictStr = Field("", max_length=new_items.MAX_REASON * 4)
    directApply: StrictBool = False


class BlockRuleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    op: Literal["add", "edit", "delete"]
    blockKey: _Id
    taskId: _Id | None = None
    projectId: _Id | None = None
    reason: StrictStr = Field("", max_length=new_items.MAX_REASON * 4)
    directApply: StrictBool = False


class PartIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fromTs: StrictInt | None = None
    toTs: StrictInt | None = None
    taskId: _Id | None = None
    projectId: _Id | None = None


class BlockActionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["assign", "split", "generic", "ignore"]
    date: Date
    blockKey: Annotated[StrictStr, Field(min_length=1, max_length=1000)]
    taskId: _Id | None = None
    projectId: _Id | None = None
    parts: list[PartIn] | None = Field(None, max_length=100)
    forever: StrictBool = False  # 给了 true → 400（AI 的 ignore 不许 forever）
    reason: StrictStr = Field("", max_length=new_items.MAX_REASON * 4)
    directApply: StrictBool = False


@router.post("/window-rules", dependencies=[Depends(_no_device_token_unless_write)])
def propose_window_rule(body: WindowRuleIn, authorization: _Auth = None) -> dict:
    return tier_items.propose_window_rule(body.op, body.rule.model_dump(), body.reason, body.directApply, is_human(authorization))


@router.post("/block-rules", dependencies=[Depends(_no_device_token_unless_write)])
def propose_block_rule(body: BlockRuleIn) -> dict:
    return tier_items.propose_block_rule(body.op, body.blockKey, body.taskId, body.projectId, body.reason, body.directApply)


@router.post("/block-actions", dependencies=[Depends(_no_device_token_unless_write)])
def propose_block_action(body: BlockActionIn) -> dict:
    parts = None if body.parts is None else [p.model_dump() for p in body.parts]
    return tier_items.propose_block_action(body.action, body.date, body.blockKey, body.taskId, body.projectId, parts,
                                           body.forever, body.reason, body.directApply)


class ApproveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    edit: dict | None = None


@router.get("")
def listing(status: Literal["pending", "all"] = "pending", limit: Annotated[int, Query(ge=1, le=200)] = 100,
            authorization: _Auth = None) -> dict:
    return new_items.listing(status == "pending", limit, is_human(authorization))


@router.post("/{itemId}/approve", dependencies=[Depends(human)])
def approve(itemId: str, request: Request, body: Annotated[ApproveIn | None, Body()] = None) -> dict:  # noqa: N803 —— 路径参数名即契约
    return new_items.approve(itemId, request, True, body.edit if body else None)


@router.post("/{itemId}/reject", dependencies=[Depends(human)])
def reject(itemId: str) -> dict:  # noqa: N803
    return new_items.reject(itemId, True)
