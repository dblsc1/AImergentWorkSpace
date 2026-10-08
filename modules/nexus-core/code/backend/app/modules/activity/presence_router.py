"""HTTP 层（v2.4 在场心跳；v2.14 自动跟踪的三个端点）：路径、入参、响应模型。**不许有业务判断。**"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Request
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    model_validator,
)
from starlette.concurrency import run_in_threadpool

from . import auto, auto_ai
from .router import human_body

router = APIRouter(prefix="/activity", tags=["activity"])

_Id = Annotated[StrictStr, Field(min_length=1, max_length=128)]


class Guess(BaseModel):
    """v2.14：检测程序用规则对当前窗口的猜测。目标恰好一个；多出的键忽略（检测程序可能比服务端新）。"""

    taskId: _Id | None = None
    projectId: _Id | None = None
    confidence: Annotated[StrictFloat | StrictInt, Field(ge=0, le=1)]
    classifier: Literal["rules"]

    @model_validator(mode="after")
    def _one_target(self):
        if (self.taskId is None) == (self.projectId is None):
            raise ValueError("taskId 与 projectId 必须给一个、且只能给一个")
        return self


class PresenceIn(BaseModel):
    """形状以 ai-detector 契约「在场心跳」节为准。app/title 超长不拒、截断（service 里）。"""

    deviceId: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,64}$")  # 同活动建议
    app: StrictStr
    title: StrictStr
    afk: StrictBool
    guess: Guess | None = None


class PresenceOut(BaseModel):
    ok: bool


@router.post("/presence", response_model=PresenceOut)
def post_presence(body: PresenceIn) -> dict:
    guess = body.guess and {**({"taskId": body.guess.taskId} if body.guess.taskId else
                               {"projectId": body.guess.projectId}), "confidence": float(body.guess.confidence)}
    return auto.heartbeat(body.deviceId, body.app, body.title, body.afk, guess)


# ------------------------------------------------ v2.14 自动跟踪（契约「自动跟踪进行中的任务」节）


class _Key(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: StrictStr = Field(pattern=auto.KEY_PATTERN)


class ChoiceIn(_Key):
    taskId: _Id | None = None
    projectId: _Id | None = None
    remember: StrictBool = False


class ChoiceOut(BaseModel):
    key: str
    taskId: str | None
    projectId: str
    remembered: bool
    pseudonymized: bool


class DismissOut(BaseModel):
    key: str
    dismissedUntil: str


class AutoSessionsOut(BaseModel):
    """今天自动记下的段；条目的键见契约。"""

    items: list[dict]


@router.post("/choice", response_model=ChoiceOut)
async def choose(request: Request) -> dict:
    _auth, body = await human_body(request, ChoiceIn, "替人选项目 / 任务")  # 设备令牌先 403，再看请求体
    return await run_in_threadpool(auto.choose, body.key, body.taskId, body.projectId, body.remember)


@router.post("/choice/dismiss", response_model=DismissOut)
async def dismiss_choice(request: Request) -> dict:
    _auth, body = await human_body(request, _Key, "替人说「这次不选」")
    return await run_in_threadpool(auto.dismiss, body.key)


@router.get("/auto", response_model=AutoSessionsOut)
def auto_sessions() -> dict:
    return auto_ai.recorded_today()  # v2.15：每条追加 ai


# ------------------------------------------------ v2.15 让 AI 认窗口（契约同名节）


class SuggestIn(_Key):
    """AI 的回答：给目标（taskId / projectId 恰好一个 + confidence），或 ``none: true``（认不出）。都要一句 reason。"""

    taskId: _Id | None = None
    projectId: _Id | None = None
    confidence: Annotated[StrictFloat | StrictInt, Field(gt=0, le=1)] | None = None
    reason: StrictStr = Field(min_length=1, max_length=200)
    none: Literal[True] | None = None

    @model_validator(mode="after")
    def _shape(self):
        given = [self.taskId, self.projectId, self.confidence]
        if self.none and any(v is not None for v in given):
            raise ValueError("none: true 时不要带 taskId / projectId / confidence")
        if not self.none and ((self.taskId is None) == (self.projectId is None) or self.confidence is None):
            raise ValueError("taskId 与 projectId 必须给一个、且只能给一个，并带 confidence；认不出就给 none: true")
        return self


class _Empty(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ClaimOut(BaseModel):
    window: dict | None  # {key, app, title, claimedAt, answerBy}


class SuggestOut(BaseModel):
    key: str
    outcome: Literal["suggested", "none"]
    taskId: str | None
    projectId: str | None
    confidence: float | None
    autoRecord: bool
    ruleWritten: bool


class RejectOut(BaseModel):
    key: str
    app: str
    title: str
    ruleRemoved: bool


# AI 这两个端点只经 MCP（对内直连、不带 Bearer）调：设备令牌直连一律 403，同 v2.7 的 matches。
@router.post("/ai/claim", response_model=ClaimOut)
async def ai_claim(request: Request) -> dict:
    await human_body(request, _Empty, "直接认领等 AI 认的窗口（请经 MCP 的 get_window_awaiting_target）")
    return await run_in_threadpool(auto_ai.claim)


@router.post("/ai/suggest", response_model=SuggestOut)
async def ai_suggest(request: Request) -> dict:
    _auth, b = await human_body(request, SuggestIn, "直接替 AI 认窗口（请经 MCP 的 suggest_window_target）")
    return await run_in_threadpool(auto_ai.suggest, b.key, b.taskId, b.projectId, b.confidence, b.reason, bool(b.none))


@router.post("/choice/reject", response_model=RejectOut)
async def reject_choice(request: Request) -> dict:
    _auth, body = await human_body(request, _Key, "替人说「不对」")
    return await run_in_threadpool(auto_ai.reject, body.key)
