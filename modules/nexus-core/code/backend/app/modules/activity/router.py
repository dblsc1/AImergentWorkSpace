"""HTTP 层：路径、入参、响应模型。**不许有业务判断。**

整批形状（deviceId、segments 是数组且 ≤200）由这里的 pydantic 拦成 422；
每一段的校验在 ``service.upload``——在这里按段建模会把「坏段进 rejected」变成整批 422，
而 ai-detector 只在 2xx 后推进游标，整批拒 = 永远卡住（同 events/router.py 的理由）。
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

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
    status: str


class ListOut(BaseModel):
    total: int
    items: list[Item]


class ConfirmIn(BaseModel):
    taskId: str | None = None  # 缺省 = 用建议里的任务
    mode: Mode = "do"


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


class DismissOut(BaseModel):
    id: str
    status: str


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
def confirm(sugId: str, body: ConfirmIn | None = None) -> dict:  # noqa: N803 —— 路径参数名即契约
    body = body or ConfirmIn()
    return service.confirm(sugId, body.taskId, body.mode)


@router.post("/{sugId}/dismiss", response_model=DismissOut)
def dismiss(sugId: str) -> dict:  # noqa: N803
    return service.dismiss(sugId)
