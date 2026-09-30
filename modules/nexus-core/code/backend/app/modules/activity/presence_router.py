"""HTTP 层（v2.4 在场心跳）：路径、入参、响应模型。**不许有业务判断。**"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field, StrictBool, StrictStr

from . import presence

router = APIRouter(prefix="/activity", tags=["activity"])


class PresenceIn(BaseModel):
    """形状以 ai-detector 契约「在场心跳」节为准。app/title 超长不拒、截断（service 里）。"""

    deviceId: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,64}$")  # 同活动建议
    app: StrictStr
    title: StrictStr
    afk: StrictBool


class PresenceOut(BaseModel):
    ok: bool


@router.post("/presence", response_model=PresenceOut)
def post_presence(body: PresenceIn) -> dict:
    return presence.heartbeat(body.deviceId, body.app, body.title, body.afk)
