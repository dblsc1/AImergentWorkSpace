"""HTTP 层（v2.22「忽略并记住」）：路径、入参、响应。**不许有业务判断。**

读不设限（MCP 经 ``get_detector_rules`` 读同一份）；建 / 删只许人：``human`` 依赖在看请求体之前把带 Bearer 的挡成 403。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Response
from pydantic import BaseModel, ConfigDict, Field

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
def list_ignores() -> dict:
    return ignore.listing()


@router.post("", status_code=201, dependencies=[Depends(human)])
def create_ignore(body: IgnoreIn) -> dict:
    return ignore.add(body.app, body.titleContains)


@router.delete("/{rule_id}", status_code=204, dependencies=[Depends(human)])
def delete_ignore(rule_id: str) -> Response:
    ignore.remove(rule_id)
    return Response(status_code=204)
