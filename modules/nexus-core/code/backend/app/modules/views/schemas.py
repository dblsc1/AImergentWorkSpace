"""契约响应模型，与 ``module_docs/contract.md`` 的 JSON 示例一一对应。

两条硬约束，写在这里免得后来者踩：

1. ``shareOfPlan`` / ``shareOfProject`` 是 **0–100 的百分数**，不是 0–1 小数。
   ``0.475`` 与 ``47.5`` 都是合法 float，**类型检查抓不到**——只会在联调时
   表现为消费方 ``ring`` 抛「数据格式错误」。
2. 空闲态四个字段是 ``null``，**不是 0**，且必须**真的出现在 JSON 里**。
   字段消失 ≠ 字段为 null，所以任何 ``exclude_none`` / ``exclude_unset``
   都不许加在这几条响应上。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

#: 占比取值区间。契约「占比语义」节为规范性条款。
SHARE_MIN = 0.0
SHARE_MAX = 100.0


class _Strict(BaseModel):
    """契约模型基类：拒绝契约外字段。

    M5 是「无缺**无多**」——项目级 ``color``、项目 ``icon`` 是**有意排除**的，
    不是遗漏。``extra="forbid"`` 让「顺手多带一个字段」在构造期就炸，
    而不是等审核脚本去 diff 响应。
    """

    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------- CurrentOut


class CurrentZone(_Strict):
    id: str
    key: str
    name: str


class CurrentProject(_Strict):
    id: str
    key: str
    name: str
    totalSeconds: int
    shareOfPlan: float = Field(ge=SHARE_MIN, le=SHARE_MAX)


class CurrentTask(_Strict):
    id: str
    key: str
    name: str
    totalSeconds: int
    shareOfProject: float = Field(ge=SHARE_MIN, le=SHARE_MAX)


class CurrentOut(_Strict):
    """``GET /api/core/views/current``。

    ``running: false`` 时下面四个字段全为 ``None``，序列化后是 JSON ``null``。
    """

    running: bool
    zone: CurrentZone | None = None
    project: CurrentProject | None = None
    task: CurrentTask | None = None
    #: ISO8601 **带时区**字符串；``null`` = 未在计时。
    sessionStartAt: str | None = None


# ------------------------------------------------------------------- TreeOut


class Zone(_Strict):
    id: str
    key: str
    name: str
    #: 分区配色是用户设定、是持久化字段，所以留在 API 内（contract.md）。
    color: str
    order: int


class Task(_Strict):
    """``TreeOut`` 的任务节点。``plan``/``dependsOn``（v1.2）是**只读附带**——
    本端不校验、不改写，与 ``planner.crud.v1`` 的 ``TaskOut`` 同形状，唯一写
    路径仍是 ``PATCH /api/core/planner/tasks/{id}``。用的是 ``GanttPlan``
    这个类名（本文件下方定义，靠 ``from __future__ import annotations`` 的
    延迟求值解析前向引用）——形状与 gantt 那份完全一致，不重新定义一份同形状模型。
    """

    id: str
    key: str
    name: str
    done: bool
    #: ``normal`` | ``ephemeral``。契约未穷举取值，故不用 Literal 收死。
    kind: str
    #: 前端只读不解释——贴纸决定数据性质，代码决定系统行为。
    flags: list[str]
    #: ``null`` = 未排期（契约「排期与依赖」v1.1；本端 v1.2 补齐）。
    plan: GanttPlan | None = None
    #: 前置任务 id 列表，纯表达不排程；默认 []。
    dependsOn: list[str] = Field(default_factory=list)


class Project(_Strict):
    id: str
    key: str
    zoneId: str
    name: str
    status: Literal["active", "done", "archived"]
    #: 0–100 整数。``computed`` 由后端按「已完成任务数 / 任务总数」算；
    #: ``manual`` 是人工覆盖值，**原样返回不重算**。
    progress: int = Field(ge=0, le=100)
    progressSource: Literal["computed", "manual"]
    #: ``projects.plan.end`` 的投影，``YYYY-MM-DD``；无计划则 ``null``。
    deadline: str | None = None
    tasks: list[Task]


class TreeOut(_Strict):
    """``GET /api/core/views/tree``。"""

    zones: list[Zone]
    projects: list[Project]


# ------------------------------------------------------------------ GanttOut


class GanttPlan(_Strict):
    """计划图层：**可拖拽改期**，走 ``PATCH /api/core/planner/projects/{id}``
    （契约「甘特读端」——不为甘特开专用写端点）。"""

    start: str
    end: str


class GanttActualDay(_Strict):
    """事实图层的一天：**锁死，绝不可编辑**——来自 append-only 的事件流水账。"""

    date: str
    seconds: int


class GanttTask(_Strict):
    """v1.1 新增：甘特的任务层（契约「甘特读端」O1）。

    ``actual`` 与项目层的 ``actual`` 来自同一张 ``proj_daily_stats``——项目层按
    ``date`` 求和，这里按 ``(date, taskId)`` 分组，两者不需要对账（任务层求和
    ≤ 项目层同日数字，差额是「有项目无具体任务」的那部分事件，contract B5）。
    """

    id: str
    key: str
    name: str
    done: bool
    #: ``null`` = 未排期。与 ``GanttProject.plan`` 同一个形状，同一份校验。
    plan: GanttPlan | None = None
    #: 前置任务 id 列表，纯表达不排程；默认 []。
    dependsOn: list[str] = Field(default_factory=list)
    actual: list[GanttActualDay]


class GanttProject(_Strict):
    id: str
    key: str
    name: str
    #: ``null`` = 未排期（契约「甘特读端」）。
    plan: GanttPlan | None = None
    #: 按天聚合，可为空数组；跨任务在同一天的时长已在 queries.py 里求和。
    actual: list[GanttActualDay]
    #: v1.1 新增：本项目下的任务层，见 ``GanttTask``。
    tasks: list[GanttTask]


class GanttOut(_Strict):
    """``GET /api/core/views/gantt``（contract.md v0.8「甘特读端」）。

    ``today`` 由服务端给，前端画红线用——不许用客户端本地时钟（契约明文理由：
    客户端时区/时钟不准会让红线飘，而「今天」是判断基准，不能各人一个答案）。
    """

    projects: list[GanttProject]
    today: str


# ------------------------------------------------------------ NextActionsOut


class NextActionBlocker(_Strict):
    """「等待」列表 ``blockedBy`` 里的一项：点名**直接**未完成前置。"""

    id: str
    key: str
    name: str


class NextActionTask(_Strict):
    """``actionable``/``waiting`` 共用同一个形状（契约 v1.5「下一步行动读端」）。
    唯一的语义区别是 ``blockedBy``——``actionable`` 恒为空数组。"""

    id: str
    key: str
    name: str
    projectId: str
    projectName: str
    plannedWeight: float
    #: ``null`` = 未排期。无 plan 的任务仍可能是"可做"（GTD：无 due 也是 next action）。
    plan: GanttPlan | None = None
    dependsOn: list[str] = Field(default_factory=list)
    #: ``plan.end < today``。无 plan 恒 ``false``。
    overdue: bool
    #: ``plan.end == today``。与 ``overdue`` 互斥，两者任一为真都参与"置顶"排序。
    dueToday: bool
    #: 该任务位于依赖图的某个环上（A8d 环防御）。为真时强制归入 ``actionable``。
    cycleWarning: bool
    blockedBy: list[NextActionBlocker] = Field(default_factory=list)


class NextActionZone(_Strict):
    id: str
    key: str
    name: str
    actionable: list[NextActionTask]
    waiting: list[NextActionTask]


class NextActionsOut(_Strict):
    """``GET /api/core/views/next-actions``（contract.md v1.5「下一步行动读端」，
    PRD F-TODO-1）。``zones`` 只含至少有一条 actionable/waiting 的分区。"""

    today: str
    zones: list[NextActionZone]


# ------------------------------------------------------------------ ReviewOut


class ReviewPlanVsActual(_Strict):
    projectId: str
    key: str
    name: str
    plan: GanttPlan | None = None
    #: ``plan`` 区间与本周是否有交集，供前端筛选/高亮，后端不据此过滤列表。
    scheduledThisWeek: bool
    actualSecondsThisWeek: int


class ReviewOverdueProject(_Strict):
    id: str
    key: str
    name: str
    plan: GanttPlan
    status: str


class ReviewStaleTask(_Strict):
    id: str
    key: str
    name: str
    projectId: str
    #: 最近一次有计时的日期；从未计时过为 ``null``（不是"很久以前"，是"压根没有过"）。
    lastActiveDate: str | None = None


class ReviewOut(_Strict):
    """``GET /api/core/views/review``（contract.md v1.5「每周回顾读端」，
    PRD F-REVIEW-1）。四块聚合，全部复用既有只读函数，零新集合零新投影。"""

    today: str
    weekStart: str
    weekEnd: str
    planVsActual: list[ReviewPlanVsActual]
    overdueProjects: list[ReviewOverdueProject]
    staleTasks: list[ReviewStaleTask]
    inboxPendingCount: int
