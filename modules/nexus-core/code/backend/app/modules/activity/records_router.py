"""HTTP 层（v2.38 三级分类）：原始记录上传与键盘鼠标活动。**不许有业务判断。**

整批形状（deviceId、records 是数组且 1..500）由这里的 pydantic 拦成 422；每一条的校验在 ``records.upload``——
同建议上传：按条建模会把「坏条进 rejected」变成整批 422，而检测程序只在 2xx 后推进游标，整批拒 = 永远卡住。
键盘鼠标活动的 ``bins`` 是整数数组（≤ 8640，每个是 10 的倍数）；不合形状整批 422（契约没有 ``rejected``）。
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, StrictInt, field_validator

from . import gaps, input_activity, records
from .ignore_router import need_bearer, no_bearer

router = APIRouter(prefix="/activity", tags=["activity"])
_DEVICE = r"^[A-Za-z0-9_.-]{1,64}$"  # 不含 ':'：它是防重键 rec:<deviceId>:<startAt> 的分隔符


class RecordsIn(BaseModel):
    deviceId: str = Field(pattern=_DEVICE)
    records: list[Any] = Field(min_length=1, max_length=500)


class RecordsOut(BaseModel):
    accepted: int
    duplicates: int
    ignored: int
    rejected: list[dict]


class InputIn(BaseModel):
    deviceId: str = Field(pattern=_DEVICE)
    bins: list[Annotated[StrictInt, Field(ge=0)]] = Field(max_length=input_activity.MAX_BINS)

    @field_validator("bins")
    @classmethod
    def _tens(cls, v: list[int]) -> list[int]:
        if any(b % input_activity.BIN_SECONDS for b in v):
            raise ValueError(f"每个格起点须是 {input_activity.BIN_SECONDS} 的倍数")
        return v


@router.post("/records", response_model=RecordsOut, dependencies=[Depends(need_bearer)])
def upload_records(body: RecordsIn) -> dict:
    return records.upload(body.deviceId, body.records)


@router.post("/input", dependencies=[Depends(need_bearer)])
def upload_input(body: InputIn) -> dict:
    return input_activity.upload(body.deviceId, body.bins)


@router.get("/input", dependencies=[Depends(no_bearer)])
def get_input(date: date) -> dict:  # noqa: A002 —— 查询参数名即契约
    return input_activity.day(date.isoformat())


@router.get("/gaps", dependencies=[Depends(no_bearer)])
def get_gaps(date: date | None = None) -> dict:  # noqa: A002
    return gaps.gaps(date)


@router.get("/blocks/history", dependencies=[Depends(no_bearer)])
def blocks_history(block: Annotated[str | None, Query(min_length=1, max_length=1000)] = None, app: str | None = None,
                   title: str | None = None, days: Annotated[int, Query(ge=1, le=90)] = 30) -> dict:
    return gaps.match_history(block, app, title, days)


@router.get("/windows/history", dependencies=[Depends(no_bearer)])
def windows_history(app: Annotated[str, Query(min_length=1)], title: str | None = None,
                    titleContains: Annotated[str | None, Query(min_length=1, max_length=128)] = None,  # noqa: N803 —— 查询参数名即契约
                    days: Annotated[int, Query(ge=1, le=90)] = 30) -> dict:
    return gaps.windows_history(app, title, titleContains, days)
