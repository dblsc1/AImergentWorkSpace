"""HTTP 层（v2.22 泳道偏好）：路径、入参、响应。**不许有业务判断。**

四个端点都只许人：``human`` 依赖在看请求体之前就把带 Bearer 的挡成 403（同 activity 的 ``human_body``）。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator

from ..activity.service import forbid_device_token
from . import service

MAX_INDEX = 1000
_DATE = r"^\d{4}-\d{2}-\d{2}$"


def human(authorization: Annotated[str | None, Header()] = None) -> None:
    forbid_device_token(authorization, "改泳道偏好")


router = APIRouter(prefix="/lanes/prefs", tags=["lanes"], dependencies=[Depends(human)])


class AgentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent: str = Field(min_length=1, max_length=service.MAX_AGENT)
    label: str = Field("", max_length=service.MAX_LABEL)  # 没有 label 的运行身份只有 agent
    hidden: StrictBool | None = None
    pinned: StrictBool | None = None
    unverified: StrictBool = False  # 这个身份是匿名（未验证）的那一类：和同名的已验证会话互不串

    @model_validator(mode="after")
    def _something(self):
        if self.hidden is None and self.pinned is None:
            raise ValueError("hidden 与 pinned 至少给一个")
        return self


class OrderIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runId: str = Field(min_length=1, max_length=128)
    index: StrictInt = Field(ge=0, le=MAX_INDEX)
    # 页面读 views/lanes 时用的窗口（同名同校验）；缺省 = 今天
    date: str | None = Field(None, pattern=_DATE)
    from_: str | None = Field(None, alias="from", pattern=_DATE)
    to: str | None = Field(None, pattern=_DATE)


@router.get("")
def get_prefs() -> dict:
    return service.get()


@router.put("/agent")
def put_agent(body: AgentIn) -> dict:
    return service.set_agent(body.agent, body.label, body.hidden, body.pinned, body.unverified)


@router.put("/order")
def put_order(body: OrderIn) -> dict:
    return service.set_order(body.runId, body.index, body.date, body.from_, body.to)


@router.delete("/order")
def delete_order() -> dict:
    return service.clear_order(None)


@router.delete("/order/{run_id}")
def delete_order_one(run_id: str) -> dict:
    return service.clear_order(run_id)
