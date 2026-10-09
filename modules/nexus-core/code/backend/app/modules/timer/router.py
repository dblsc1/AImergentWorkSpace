"""HTTP 层：路径、入参、响应模型。**不许有业务判断。**

``UnknownTaskError → 404`` 的映射在 ``main.py`` 的 exception handler 里——
那是组装层的接线，不是这里的 if。
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from . import reassign as reassign_impl
from . import service

router = APIRouter(prefix="/timer", tags=["timer"])
#: v2.1「AI 代理运行」：与人的计时同住 timer 子边界（同一类活状态），路径另起前缀。
#: v2.19：匿名调用方只能动匿名开的运行——对这个路由上每个带 {runId} 的端点生效（判断在 service 里）。
agents_router = APIRouter(prefix="/agents", tags=["agents"], dependencies=[Depends(service.anonymous_run_guard)])
#: v2.11「改挂未分类时间」：对象是一段已落账的 session.completed，路径按它起前缀。
sessions_router = APIRouter(prefix="/sessions", tags=["sessions"])

#: v2.1 人类计时模式。取值不在枚举内 → 422（pydantic 先拦，同 actor 字段口径）。
Mode = Literal["do", "prompt", "review"]


class StartIn(BaseModel):
    taskId: str
    mode: Mode = "do"


class TimerOut(BaseModel):
    running: bool
    taskId: str
    startAt: str
    mode: Mode = "do"  # v2.1 回显


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
    return service.start(body.taskId, mode=body.mode)


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
    mode: Mode = "do"  # v2.1


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
    return service.backfill(body.taskId, body.startAt, body.durationSeconds, mode=body.mode)


# ------------------------------------------------ AI 代理运行（v2.1，契约「AI 代理运行」节）

#: v2.4 代理相位（契约「人一条线、代理多条线的时间线」）。不在枚举内 → 422。
Phase = Literal["working", "waiting_input", "waiting_permission", "idle", "error"]


#: v2.18：谁在发心跳（``companion`` / ``monitor`` / 适配器自己起的短名字）。只是标签，服务端不解释。
BeatSource = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,31}$")]


class AgentStartIn(BaseModel):
    taskId: str | None = None  # 缺省 = 挂收件箱
    projectId: str | None = Field(default=None, min_length=1, max_length=128)  # v2.13：不带 taskId 时只挂项目
    agent: str = Field(min_length=1, max_length=64)
    tool: str = Field(min_length=1, max_length=64)
    model: str | None = Field(default=None, min_length=1, max_length=64)
    # v2.4 选填（长度按码点）：缺省都不记
    phase: Phase | None = None
    label: str | None = Field(default=None, min_length=1, max_length=64)
    match: str | None = Field(default=None, min_length=3, max_length=128)
    clientKey: str | None = Field(default=None, min_length=1, max_length=128)
    heartbeat: bool = False  # v2.18：true = 这个适配器会发心跳，运行受活性规则管（契约「心跳与失联」）
    beatSource: BeatSource | None = None  # v2.18：选填


class AgentStartOut(BaseModel):
    runId: str
    startedAt: str
    heartbeatSeconds: int  # v2.18：建议的心跳间隔（适配器不必写死）


class AgentStopIn(BaseModel):
    outcome: Literal["done", "failed", "cancelled", "timeout"]
    output: str | None = Field(default=None, max_length=512)


class AgentStopOut(BaseModel):
    """``duplicate:true`` = 这个运行早已结束（重复 stop / 已被超时关闭），本次什么都没写；
    ``outcome``/``durationSeconds``/``event`` 回显的是**原来那条**事件。"""

    runId: str
    duplicate: bool
    outcome: str
    durationSeconds: int
    event: StopEvent


@agents_router.post("/start", response_model=AgentStartOut, status_code=201)
def agent_start(body: AgentStartIn, response: Response) -> dict:
    """不碰人的计时器；可与任意多个运行并发。taskId / projectId 不存在 → 404，两者矛盾 → 400（映射在 main.py）。
    v2.4：同 clientKey 的运行还在跑 → 200 回原运行（不是新建，所以不是 201）。"""
    out, created = service.agent_start(
        body.taskId, body.agent, body.tool, body.model,
        phase=body.phase, label=body.label, match=body.match, client_key=body.clientKey,
        project_id=body.projectId, heartbeat=body.heartbeat, beat_source=body.beatSource,
    )
    if not created:
        response.status_code = 200
    return out


class AgentPhaseIn(BaseModel):
    """v2.4。多了未知字段 → 422（extra=forbid）。"""

    model_config = ConfigDict(extra="forbid")

    phase: Phase
    at: str = Field(max_length=64)
    detail: str | None = Field(default=None, max_length=64)
    reply: bool = False

    @field_validator("at")
    @classmethod
    def _at_with_offset(cls, v: str) -> str:
        try:
            parsed = datetime.fromisoformat(v)
        except ValueError as exc:
            raise ValueError(f"at 不是合法 ISO8601：{v!r}") from exc
        if parsed.tzinfo is None:
            raise ValueError(f"at 缺少时区偏移：{v!r}")
        return v


class AgentPhaseOut(BaseModel):
    """``phase`` = 按 at 排最后的那条相位（从没报过为 null）；``reason`` 只在 applied:false 时有值。"""

    runId: str
    phase: Phase | None
    applied: bool
    reason: Literal["duplicate", "closed", "capped"] | None


@agents_router.post("/{runId}/phase", response_model=AgentPhaseOut)
def agent_phase(runId: str, body: AgentPhaseIn) -> dict:  # noqa: N803 —— 路径参数名即契约
    """at 超前 300s → 422（UnprocessableError）；runId 不存在 → 404（映射都在 main.py）。"""
    return service.agent_phase(runId, body.phase, body.at, body.detail, body.reply)


class AgentHeartbeatIn(BaseModel):
    """v2.18。空体或 ``{}`` 都收；只有一个选填字段，多了未知字段 → 422。"""

    model_config = ConfigDict(extra="forbid")
    beatSource: BeatSource | None = None


class AgentHeartbeatOut(BaseModel):
    """``applied:false, reason:"closed"`` = 运行已结束（同相位的 closed）：适配器带原 clientKey 再 start。"""

    runId: str
    applied: bool
    reason: Literal["closed"] | None
    heartbeatSeconds: int


@agents_router.post("/{runId}/heartbeat", response_model=AgentHeartbeatOut)
def agent_heartbeat(runId: str, body: AgentHeartbeatIn | None = None) -> dict:  # noqa: N803 —— 路径参数名即契约
    """「我还活着」。runId 不存在 → 404（映射在 main.py）。"""
    return service.agent_heartbeat(runId, body.beatSource if body else None)


@agents_router.post("/{runId}/stop", response_model=AgentStopOut)
def agent_stop(runId: str, body: AgentStopIn) -> dict:  # noqa: N803 —— 路径参数名即契约
    """runId 不存在 → 404（NotFoundError，映射在 main.py）。"""
    return service.agent_stop(runId, body.outcome, body.output)


# ------------------------------------------------ 改挂未分类时间（v2.11，契约「改挂未分类时间」节）


class ReassignIn(BaseModel):
    """``taskId``（归到这个任务）与 ``projectId``（放回这个项目的未分类）二选一；多了未知字段 → 422。"""

    model_config = ConfigDict(extra="forbid")

    taskId: str | None = Field(default=None, min_length=1, max_length=128)
    projectId: str | None = Field(default=None, min_length=1, max_length=128)


class SessionReassignOut(BaseModel):
    """``duplicate:true`` = 这一段此刻已经在目标上，什么都没追加；``event`` 是决定当前归属的那条
    ``session.reassigned``（从没改挂过时为 null）。"""

    sessionEventId: str
    duplicate: bool
    fromTaskId: str
    taskId: str
    projectId: str
    seq: int
    event: StopEvent | None


@sessions_router.post("/{eventId}/reassign", response_model=SessionReassignOut)
def reassign_session(eventId: str, body: ReassignIn, request: Request) -> dict:  # noqa: N803 —— 路径参数名即契约
    """带 Bearer / actor=ai → 403；段或目标不存在 → 404；不是未分类的段 → 409（映射都在 main.py）。"""
    return reassign_impl.reassign(eventId, body.taskId, body.projectId, request)
