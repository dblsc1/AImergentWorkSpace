"""HTTP 层（v2.22「忽略并记住」）：路径、入参、响应。**不许有业务判断。**

读不设限，但只有人拿得到 ``titleContains``（MCP 经 ``get_detector_rules`` 读同一份，只拿 ``hasTitleFilter``）；建 / 删只许人：``human`` 依赖在看请求体之前把带 Bearer 的挡成 403。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Response
from pydantic import BaseModel, ConfigDict, Field

from ...scope import caller
from . import ignore
from .service import forbid_device_token


def human(authorization: Annotated[str | None, Header()] = None) -> None:
    forbid_device_token(authorization, "改忽略规则")


router = APIRouter(prefix="/activity/ignores", tags=["activity"])


class IgnoreIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    app: str = Field(min_length=1, max_length=ignore.MAX_APP * 4)  # 超长在 service 里 422，这里只拦乱发的
    titleContains: str | None = Field(None, max_length=ignore.MAX_TITLE * 4)


@router.get("")
def list_ignores(authorization: Annotated[str | None, Header()] = None) -> dict:
    # 人（网页会话：没有 Bearer、没有范围头）看到完整规则；其余调用方只拿 hasTitleFilter，不给匹配文字（见 ignore._out）
    return ignore.listing(human=caller().scope is None and not (authorization or "").strip().lower().startswith("bearer "))


@router.post("", status_code=201, dependencies=[Depends(human)])
def create_ignore(body: IgnoreIn) -> dict:
    return ignore.add(body.app, body.titleContains)


@router.delete("/{rule_id}", status_code=204, dependencies=[Depends(human)])
def delete_ignore(rule_id: str) -> Response:
    ignore.remove(rule_id)
    return Response(status_code=204)
