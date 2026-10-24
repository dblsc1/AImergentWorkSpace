"""HTTP 层（v2.38 三级分类）：窗口块读写、第二级规则、账号设置。**不许有业务判断**（目标存在、``ifSeq``、时间相接都在 service）。

写端点只许人：同 v2.7 的 ``human_body``——在**看请求体之前**就把设备令牌挡成 403（带 Bearer 的坏请求体也是 403 不是 422）；
再加 ``ignore_router.is_human`` 的判据（带范围头 / 匿名的也不行）。
"""

from __future__ import annotations

from datetime import date as Date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr
from starlette.concurrency import run_in_threadpool

from ..planner.errors import ForbiddenError
from . import activity_settings, block_rules, blocks, blocks_write
from .ignore_router import human, is_human, no_bearer
from .router import human_body

router = APIRouter(prefix="/activity", tags=["activity"])
_Key = Annotated[StrictStr, Field(min_length=1, max_length=1000)]
_Id = Annotated[StrictStr, Field(min_length=1, max_length=128)]
_Seq = Annotated[StrictInt, Field(ge=0)]


async def _human(request: Request, model: type[BaseModel], what: str):
    auth, body = await human_body(request, model, what)
    if not is_human(auth):
        raise ForbiddenError(f"只有登录的人能{what}；设备令牌、带范围的令牌和匿名上报都不行")
    return body


# ── 窗口块读 ──


@router.get("/blocks", dependencies=[Depends(no_bearer)])
def list_blocks(date: Date | None = None,  # noqa: A002 —— 查询参数名即契约
                status: Literal["pending", "assigned", "generic", "ignored", "all"] = "all",
                limit: Annotated[int, Query(ge=1, le=1000)] = 200, cursor: str | None = None) -> dict:
    return blocks.list_blocks(date, status, limit, cursor)


@router.get("/blocks/records", dependencies=[Depends(no_bearer)])
def block_records(date: Date, blockKey: Annotated[str, Query(min_length=1, max_length=1000)]) -> dict:  # noqa: A002, N803
    return blocks.block_records(date, blockKey)


# ── 窗口块写 ──


class AssignIn(BaseModel):
    date: Date
    blockKey: _Key
    taskId: _Id | None = None
    projectId: _Id | None = None
    remember: StrictBool = False
    ifSeq: _Seq


class PartIn(BaseModel):
    fromTs: StrictInt | None = None
    toTs: StrictInt | None = None
    taskId: _Id | None = None
    projectId: _Id | None = None


class SplitIn(BaseModel):
    date: Date
    blockKey: _Key
    parts: list[PartIn] = Field(max_length=100)  # 2..20 的判据在 service（422）；这里只拦乱发的
    ifSeq: _Seq


class GenericIn(BaseModel):
    blockKey: _Key
    generic: StrictBool


class IgnoreIn(BaseModel):
    date: Date
    blockKey: _Key
    forever: StrictBool = False
    ifSeq: _Seq


class ResetIn(BaseModel):
    date: Date
    blockKey: _Key
    ifSeq: _Seq


@router.post("/blocks/assign")
async def assign(request: Request) -> dict:
    b = await _human(request, AssignIn, "改窗口块")
    return await run_in_threadpool(blocks_write.assign, b.date, b.blockKey, b.taskId, b.projectId, b.remember, b.ifSeq)


@router.post("/blocks/split")
async def split(request: Request) -> dict:
    b = await _human(request, SplitIn, "改窗口块")
    parts = [{"fromTs": p.fromTs, "toTs": p.toTs, "taskId": p.taskId, "projectId": p.projectId} for p in b.parts]
    return await run_in_threadpool(blocks_write.split, b.date, b.blockKey, parts, b.ifSeq)


@router.post("/blocks/generic")
async def generic(request: Request) -> dict:
    b = await _human(request, GenericIn, "改窗口块")
    return await run_in_threadpool(blocks_write.set_generic, b.blockKey, b.generic)


@router.post("/blocks/ignore")
async def ignore_block(request: Request) -> dict:
    b = await _human(request, IgnoreIn, "改窗口块")
    return await run_in_threadpool(blocks_write.ignore_block, b.date, b.blockKey, b.forever, b.ifSeq)


@router.post("/blocks/reset")
async def reset(request: Request) -> dict:
    b = await _human(request, ResetIn, "改窗口块")
    return await run_in_threadpool(blocks_write.reset, b.date, b.blockKey, b.ifSeq)


# ── 第二级规则 ──


class BlockRuleIn(BaseModel):
    blockKey: _Key
    taskId: _Id | None = None
    projectId: _Id | None = None


@router.get("/block-rules", dependencies=[Depends(no_bearer)])
def list_block_rules() -> dict:
    return block_rules.listing()


@router.post("/block-rules")
async def create_block_rule(request: Request, response: Response) -> dict:
    b = await _human(request, BlockRuleIn, "改第二级规则")
    out, created = await run_in_threadpool(blocks_write.remember, b.blockKey, b.taskId, b.projectId)
    response.status_code = 201 if created else 200
    return out


@router.delete("/block-rules/{rule_id}", status_code=204, dependencies=[Depends(human)])
def delete_block_rule(rule_id: str) -> Response:
    block_rules.remove(rule_id)
    return Response(status_code=204)


# ── 账号设置 ──


class _AgentDirect(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: StrictBool
    windowsClassify: StrictBool
    honeycombRecords: StrictBool


class SettingsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tiers: StrictBool
    agentDirect: _AgentDirect


def _etag(response: Response, out: dict) -> dict:
    response.headers["ETag"] = f'"{out["version"]}"'
    return out


@router.get("/settings")
def get_settings(response: Response) -> dict:
    return _etag(response, activity_settings.get())


@router.put("/settings")
async def put_settings(request: Request, response: Response) -> dict:
    b = await _human(request, SettingsIn, "改账号设置")
    return _etag(response, await run_in_threadpool(activity_settings.put, request.headers.get("if-match"),
                                                   b.tiers, b.agentDirect.model_dump()))
