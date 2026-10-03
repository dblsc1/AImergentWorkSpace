"""HTTP 层：路径、入参、响应模型。**不许有业务判断。**

整批形状（deviceId、segments 是数组且 ≤200）由这里的 pydantic 拦成 422；
每一段的校验在 ``service.upload``——在这里按段建模会把「坏段进 rejected」变成整批 422，
而 ai-detector 只在 2xx 后推进游标，整批拒 = 永远卡住（同 events/router.py 的理由）。
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.concurrency import run_in_threadpool

from ..timer.router import Mode
from . import service

router = APIRouter(prefix="/activity/suggestions", tags=["activity"])


class UploadIn(BaseModel):
    # 不含 ':'：它是防重键 aw:<deviceId>:<startAt> 的分隔符
    deviceId: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,64}$")
    segments: list[Any] = Field(max_length=200)


class Rejected(BaseModel):
    index: int
    reason: str


class UploadOut(BaseModel):
    accepted: int
    duplicates: int
    rejected: list[Rejected]


class Suggestion(BaseModel):
    model_config = ConfigDict(extra="allow")  # v2.8 newTask {proposalId, projectId, name}：只在有提议时出现

    taskId: str | None
    confidence: float
    reason: str
    classifier: str


class Item(BaseModel):
    id: str
    deviceId: str
    startAt: str
    endAt: str
    durationSeconds: int
    app: str
    title: str
    suggestion: Suggestion
    idle: bool = False  # v2.5
    rejectedTaskIds: list[str] = []  # v2.7：人否掉过的任务
    status: str


class ListOut(BaseModel):
    total: int
    items: list[Item]


class ConfirmIn(BaseModel):
    taskId: str | None = None  # 缺省 = 用建议里的任务
    mode: Mode = "do"
    name: str | None = None  # v2.8：确认 AI 提议的新任务时人改过的名字


class EventRef(BaseModel):
    id: str
    dedupeKey: str
    type: str


class ConfirmOut(BaseModel):
    """``duplicate:true`` = 早已确认过，本次什么都没写，``event`` 是原来那条。"""

    id: str
    status: str
    duplicate: bool
    date: str
    event: EventRef
    taskId: str  # v2.8：这次确认记到的任务


class DismissOut(BaseModel):
    id: str
    status: str


class MatchesIn(BaseModel):
    matches: list[Any] = Field(max_length=200)  # 逐条校验在 service.match（坏的进 rejected，同上传）


class MatchesOut(BaseModel):
    matched: int
    rejected: list[Rejected]


class UnmatchIn(BaseModel):
    taskId: str | None = None  # 人否掉的是哪个任务；给了且与当前建议不同 → 409
    proposalId: str | None = None  # v2.8 人否掉的是哪条新任务提议；同上


class UnmatchOut(BaseModel):
    id: str
    status: str
    rejectedTaskIds: list[str]


@router.post("", response_model=UploadOut)
def upload(body: UploadIn) -> dict:
    return service.upload(body.deviceId, body.segments)


@router.get("", response_model=ListOut)
def list_suggestions(
    status: Literal["pending", "confirmed", "dismissed"] = "pending",
    limit: int = 100,
    offset: int = 0,
) -> dict:
    return service.list_suggestions(status, limit, offset)


@router.post("/{sugId}/confirm", response_model=ConfirmOut)
def confirm(sugId: str, request: Request, body: ConfirmIn | None = None) -> dict:  # noqa: N803 —— 路径参数名即契约
    body = body or ConfirmIn()
    # request：v2.8 确认提议的新任务时要判设备令牌、经 planner 写入口建任务
    return service.confirm(sugId, body.taskId, body.mode, body.name, request)


@router.post("/{sugId}/dismiss", response_model=DismissOut)
def dismiss(sugId: str) -> dict:  # noqa: N803
    return service.dismiss(sugId)


# v2.7 的两个端点自己读请求体：设备令牌要在**看请求体之前**就 403（让 FastAPI 先解析的话，
# 带 Bearer 的坏请求体会得到 422 而不是 403）。请求体不合形状仍是标准的 422。
async def _body(request: Request, model: type[BaseModel]):
    auth = request.headers.get("authorization")
    service.forbid_device_token(auth)
    raw = (await request.body()).strip()
    try:
        return auth, model() if raw in (b"", b"null") else model.model_validate_json(raw)
    except ValidationError as exc:
        raise RequestValidationError(exc.errors(include_url=False, include_context=False)) from exc


@router.post("/matches", response_model=MatchesOut)
async def match(request: Request) -> dict:
    auth, body = await _body(request, MatchesIn)
    return await run_in_threadpool(service.match, auth, body.matches)


@router.post("/{sugId}/unmatch", response_model=UnmatchOut)
async def unmatch(sugId: str, request: Request) -> dict:  # noqa: N803
    auth, body = await _body(request, UnmatchIn)
    return await run_in_threadpool(service.unmatch, auth, sugId, body.taskId, body.proposalId)
