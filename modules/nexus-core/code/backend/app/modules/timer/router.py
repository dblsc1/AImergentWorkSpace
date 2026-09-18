"""HTTP 层：路径、入参、响应模型。**不许有业务判断。**

``UnknownTaskError → 404`` 的映射在 ``main.py`` 的 exception handler 里——
那是组装层的接线，不是这里的 if。
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from . import service

router = APIRouter(prefix="/timer", tags=["timer"])


class StartIn(BaseModel):
    taskId: str


class TimerOut(BaseModel):
    running: bool
    taskId: str
    startAt: str


class StopEvent(BaseModel):
    id: str
    dedupeKey: str
    type: str


class TimerStopOut(BaseModel):
    """stop 响应。未在计时时 ``running:false`` 且 ``event:null``（S8，不报错）。"""

    running: bool
    event: StopEvent | None = None


@router.post("/start", response_model=TimerOut)
def start(body: StartIn) -> dict:
    return service.start(body.taskId)


class CancelledSession(BaseModel):
    """被丢弃那段的摘要。``discardedSeconds`` 是**回显用的派生值，任何地方都没存**。"""

    taskId: str
    startAt: str
    discardedSeconds: int


class TimerCancelOut(BaseModel):
    """cancel 响应。没有 ``event`` 字段——**因为 cancel 从不产生事件**。

    与 ``TimerStopOut`` 的差别是有意的：stop 的出参里有 ``event``（可能为 null），
    cancel 的出参里连这个字段都不该存在。让「取消不记账」在**类型上**就成立，
    而不是靠调用方记得别去读它。
    """

    running: bool
    cancelled: CancelledSession


@router.post("/stop", response_model=TimerStopOut)
def stop() -> dict:
    return service.stop()


@router.post("/cancel", response_model=TimerCancelOut)
def cancel() -> dict:
    """取消当前计时，不记账。没在计时 → ``NoRunningTimerError`` → 409（映射在 main.py）。"""
    return service.cancel()


class BackfillIn(BaseModel):
    """契约 v1.8「补登」：任务 + 日期 + 开始时刻 + 时长，必须挂具体任务。"""

    taskId: str
    startAt: str  # 必须带时区偏移，不带即 400（service 层校验，不在这里猜）
    durationSeconds: int


class BackfillEvent(BaseModel):
    id: str
    dedupeKey: str
    type: str


class TimerBackfillOut(BaseModel):
    """补登响应（contract-schemas.md ``TimerBackfillOut``）。

    ``duplicate:true`` 时前端要显示「这段已经补过了」，**不是「已记录」**——
    已经落库的是上一次那条，这一次什么都没发生；``recorded`` 两种情形都是
    ``true``（同 IngestOut 的 accepted/duplicate 是"有没有一条事件对应这次
    请求"这一件事的两种取值）。
    """

    recorded: bool
    duplicate: bool
    date: str
    event: BackfillEvent


@router.post("/backfill", response_model=TimerBackfillOut)
def backfill(body: BackfillIn) -> dict:
    """补登。**不碰 ``timer_state``**——不 stop 当前计时、不被其阻塞（契约「与
    活状态计时的关系」）。拒绝规则见 ``service.backfill``：404/400 的映射在
    ``main.py``（``UnknownTaskError``/``InvalidInputError``，router 不许有
    业务判断）。"""
    return service.backfill(body.taskId, body.startAt, body.durationSeconds)
