"""HTTP 层：路径、查询参数、响应模型。**不许有业务判断。**

``mockState`` 已随 fixtures.py 一起删除（rules.md §4：真实现落地时一起删）。
``includeEphemeral`` 是契约的东西，留着。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from . import next_actions, queries, review
from .schemas import CurrentOut, GanttOut, NextActionsOut, ReviewOut, TreeOut

router = APIRouter(prefix="/views", tags=["views"])


@router.get("/current", response_model=CurrentOut)
def read_current() -> CurrentOut:
    return queries.get_current()


@router.get("/tree", response_model=TreeOut)
def read_tree(
    includeEphemeral: Annotated[
        bool,
        Query(description="是否返回 kind=ephemeral 的临时任务；默认过滤"),
    ] = False,
) -> TreeOut:
    return queries.get_tree(includeEphemeral)


@router.get("/gantt", response_model=GanttOut)
def read_gantt(
    from_: Annotated[
        str | None,
        Query(alias="from", description="过滤 actual[] 的日期起点，YYYY-MM-DD"),
    ] = None,
    to: Annotated[
        str | None,
        Query(description="过滤 actual[] 的日期终点，YYYY-MM-DD"),
    ] = None,
) -> GanttOut:
    return queries.get_gantt(from_, to)


@router.get("/next-actions", response_model=NextActionsOut)
def read_next_actions() -> NextActionsOut:
    return next_actions.get_next_actions()


@router.get("/review", response_model=ReviewOut)
def read_review() -> ReviewOut:
    return review.get_review()
