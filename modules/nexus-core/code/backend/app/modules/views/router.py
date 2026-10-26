"""HTTP 层：路径、查询参数、响应模型。**不许有业务判断。**

``mockState`` 已随 fixtures.py 一起删除（rules.md §4：真实现落地时一起删）。
``includeEphemeral`` 是契约的东西，留着。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from . import agent_time, arrangements, collab_history, lanes, next_actions, queries, review
from .schemas import AgentTimeOut, CurrentOut, GanttOut, LanesOut, NextActionsOut, ReviewOut, TreeOut

_DATE = r"^\d{4}-\d{2}-\d{2}$"

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


@router.get("/agent-time", response_model=AgentTimeOut)
def read_agent_time(
    from_: Annotated[
        str | None, Query(alias="from", pattern=_DATE, description="起始日期（含），YYYY-MM-DD")
    ] = None,
    to: Annotated[str | None, Query(pattern=_DATE, description="结束日期（含），YYYY-MM-DD")] = None,
) -> AgentTimeOut:
    return agent_time.get_agent_time(from_, to)


@router.get("/lanes", response_model=LanesOut)
def read_lanes(
    date: Annotated[str | None, Query(pattern=_DATE, description="某一天，YYYY-MM-DD；与 from/to 互斥")] = None,
    from_: Annotated[
        str | None, Query(alias="from", pattern=_DATE, description="起始日期（含），YYYY-MM-DD")
    ] = None,
    to: Annotated[str | None, Query(pattern=_DATE, description="结束日期（含），YYYY-MM-DD")] = None,
) -> LanesOut:
    """v2.4 时间线读端。**不写**。互斥 / 跨度超 7 天 → 422（UnprocessableError，映射在 main.py）。"""
    return lanes.get_lanes(date, from_, to)


@router.get("/history")
def read_history(
    from_: Annotated[str | None, Query(alias="from", description="起始日期（含），YYYY-MM-DD；缺省今天")] = None,
    to: Annotated[str | None, Query(description="结束日期（含），YYYY-MM-DD；缺省今天")] = None,
    who: Annotated[str, Query(description="human | agent | both")] = "both",
    detail: Annotated[str, Query(description="summary | segments | windows")] = "summary",
    scope: Annotated[str | None, Query(description="分区 / 项目 / 任务 id，只过滤人的已入账部分")] = None,
    runs_cursor: Annotated[str | None, Query(alias="runsCursor", description="detail=segments：Agent 运行的下一页，取自上一页的 agents.nextRunsCursor")] = None,
) -> dict:
    """v2.39 协作历史。**不写**；取值 / 跨度不合法 → 422（UnprocessableError，detail 点名参数）。"""
    return collab_history.get_history(from_, to, who, detail, scope, runs_cursor)


@router.get("/arrangements")
def read_arrangements(
    range: Annotated[str, Query(description="today | week")] = "today",  # noqa: A002 —— 查询参数名即契约
    scope: Annotated[str | None, Query(description="分区 / 项目 id")] = None,
) -> dict:
    """v2.39 安排。**不写**。"""
    return arrangements.get_arrangements(range, scope)
