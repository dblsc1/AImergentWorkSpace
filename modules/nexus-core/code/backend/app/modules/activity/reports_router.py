"""HTTP 层：AI 报告（契约 v2.20）。**不许有业务判断**，全在 ``reports.py``。

整个路由只收人：带 ``Authorization: Bearer`` 的（设备令牌）在**看请求体之前**就 403，同 ``matches``。
提交的整体形状（summary 长度、条数）由这里的 pydantic 拦成 422；每一条的校验在 ``reports.submit``（坏条进 rejected）。
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, StrictStr

from . import reports, service


def _human_only(request: Request) -> None:
    service.forbid_device_token(request.headers.get("authorization"), "提交或处理 AI 报告")


router = APIRouter(prefix="/activity/reports", tags=["activity"], dependencies=[Depends(_human_only)])


class SubmitIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: StrictStr = Field(max_length=reports.MAX_SUMMARY)
    author: StrictStr = Field(reports.DEFAULT_AUTHOR, min_length=1, max_length=reports.MAX_AUTHOR)
    items: list[Any] = Field(max_length=reports.MAX_ITEMS)  # 逐条校验在 reports.submit（坏的进 rejected）


class RetargetIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    taskId: Annotated[StrictStr, Field(min_length=1, max_length=128)] | None = None
    projectId: Annotated[StrictStr, Field(min_length=1, max_length=128)] | None = None


@router.post("")
def submit(body: SubmitIn) -> dict:
    return reports.submit(body.summary, body.author, body.items)


@router.get("")
def list_reports(status: Literal["pending", "all"] = "pending", limit: int = 20, items: bool = False) -> dict:
    return reports.listing(status == "pending", limit, items)


@router.get("/{reportId}")
def get_report(reportId: str, resolve: bool = True) -> dict:  # noqa: N803 —— 路径参数名即契约
    return reports.get(reportId, resolve)


@router.post("/{reportId}/approve")
def approve(reportId: str, request: Request) -> dict:  # noqa: N803
    return reports.approve_all(reportId, request)


@router.post("/{reportId}/reject")
def reject(reportId: str) -> dict:  # noqa: N803
    return reports.reject_report(reportId)


@router.post("/{reportId}/items/{itemId}/approve")
def approve_item(reportId: str, itemId: str, request: Request, body: RetargetIn | None = None) -> dict:  # noqa: N803
    body = body or RetargetIn()
    return reports.approve_item(reportId, itemId, body.taskId, body.projectId, request)


@router.post("/{reportId}/items/{itemId}/reject")
def reject_item(reportId: str, itemId: str) -> dict:  # noqa: N803
    return reports.reject_item(reportId, itemId)
