"""planner CRUD 的入出参模型（contract.md v0.4「planner CRUD」）。

三类对象一律返回 ``id`` / ``key`` / ``name`` 三件套 + 各自业务字段。
入参模型只声明形状与静态约束；「指名道姓」的引用校验（zoneId/projectId
是否存在）在 ``service.py``——那是业务判断，不归 pydantic。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    """入参基类：客户端多给的字段直接 422——顺手塞字段在构造期就炸。"""

    model_config = ConfigDict(extra="forbid")


class _Out(BaseModel):
    """出参基类：**过滤**而不是拒绝——库文档里有内部字段（progress 口径、
    存量兼容位），响应模型负责把它们滤掉，线上形状仍与契约无缺无多。"""

    model_config = ConfigDict(extra="ignore")


class Plan(_Strict):
    """项目计划期。``YYYY-MM-DD``。"""

    start: str
    end: str


# ------------------------------------------------------------------ Zone


class ZoneCreate(_Strict):
    name: str
    color: str | None = None
    order: int | None = None
    #: 写者字段（契约 v1.5，F-ACTOR-1，仅字段）。缺省 "human"。
    actor: Literal["human", "ai"] | None = None


class ZoneUpdate(_Strict):
    name: str | None = None
    color: str | None = None
    order: int | None = None
    #: 不传则 `lastWriter` 不变（PATCH 局部更新语义）。
    actor: Literal["human", "ai"] | None = None


class ZoneOut(_Out):
    id: str
    key: str
    name: str
    color: str
    order: int
    #: v1.5 新增：最后一次写入这个对象的是谁。老文档缺字段时默认 "human"
    #: （迁移 `003_backfill_last_writer.py` 负责回填，见 `handoff.md`「两条纪律」）。
    lastWriter: str = "human"


# ---------------------------------------------------------------- Project


class ProjectCreate(_Strict):
    zoneId: str
    name: str
    plannedWeight: float | None = None
    plan: Plan | None = None
    actor: Literal["human", "ai"] | None = None


class ProjectUpdate(_Strict):
    name: str | None = None
    zoneId: str | None = None
    status: Literal["active", "done", "archived"] | None = None
    plannedWeight: float | None = None
    plan: Plan | None = None
    actor: Literal["human", "ai"] | None = None


class ProjectOut(_Out):
    id: str
    key: str
    zoneId: str
    name: str
    status: str
    plannedWeight: float
    plan: Plan | None = None
    lastWriter: str = "human"


# ------------------------------------------------------------------- Task


class TaskCreate(_Strict):
    projectId: str
    name: str
    kind: str | None = None
    flags: list[str] | None = None
    plannedWeight: float | None = None
    #: 任务自己的排期，契约 v1.1「排期与依赖」；校验与 Project.plan 共用同一函数。
    plan: Plan | None = None
    #: 前置任务 id 列表，默认 []（不传时 service 层落 []，不是 None）。
    dependsOn: list[str] | None = None
    actor: Literal["human", "ai"] | None = None


class TaskUpdate(_Strict):
    name: str | None = None
    projectId: str | None = None
    done: bool | None = None
    kind: str | None = None
    flags: list[str] | None = None
    plannedWeight: float | None = None
    plan: Plan | None = None
    dependsOn: list[str] | None = None
    actor: Literal["human", "ai"] | None = None


class TaskOut(_Out):
    id: str
    key: str
    projectId: str
    name: str
    done: bool
    doneAt: str | None = None
    kind: str
    flags: list[str] = Field(default_factory=list)
    plannedWeight: float
    plan: Plan | None = None
    dependsOn: list[str] = Field(default_factory=list)
    lastWriter: str = "human"


# ------------------------------------------------------------------ 审计流水


class AuditEntryOut(_Out):
    """一条审计记录（契约 v1.6「planner 审计流水」节，F-ACTOR-2）。"""

    auditId: str
    #: 单调递增全序号——**追溯顺序看它，不看时间戳**（同毫秒可以有十几条）。
    seq: int
    at: str
    #: 有效 actor：服务端按来源判定的结果，不是请求体自报的值。
    actor: str
    #: 凭据类别：ai / human / unverified。
    source: str
    op: str
    objectType: str
    #: create 被拒时为 null——那一刻还没有 id。
    objectId: str | None = None
    highRisk: bool = False
    outcome: str
    changes: dict[str, Any] = Field(default_factory=dict)
    reason: str | None = None


class AuditOut(_Out):
    total: int
    items: list[AuditEntryOut] = Field(default_factory=list)


# ------------------------------------------------------------ JSON 一键导入


class ImportRequest(BaseModel):
    """``POST /api/core/import`` 的请求体（契约 v1.7「JSON 一键导入编辑」）。

    与既有 ``_Strict`` 模型的取舍不同：``events``/``projections`` 必须先被
    **声明**出来，才能被路由层的自定义校验抓到并给出「400 + 点名字段」——
    如果整体套 ``_Strict`` 却不声明它们，pydantic 会在这两个字段上直接产生
    通用的 422，达不到契约要求的措辞。真正意料之外的字段（既不是这两个、
    也不是下面任何一个）仍然落进 ``extra="forbid"`` 的 422，与其余端点的
    校验纪律一致，不需要额外定制。
    """

    model_config = ConfigDict(extra="forbid")

    #: 与 export 的 zones/projects/tasks 同形状；元素**不带 `id`（或 `id:null`）
    #: 即新建**，带已存在的 `id` 即改，库里有而数组里没有的 id 即候选删除
    #: （见 contract.md「JSON 一键导入编辑」节）。
    zones: list[dict[str, Any]] = Field(default_factory=list)
    projects: list[dict[str, Any]] = Field(default_factory=list)
    tasks: list[dict[str, Any]] = Field(default_factory=list)
    #: export 原样带的时间戳。接受但忽略——用户大概率整份 JSON 改完直接传
    #: 回来，不该逼他们手动删掉这个字段。
    exportedAt: str | None = None
    #: 默认 `True`：零写入，只回 diff 计划 + 计划的 checksum。
    dryRun: bool = True
    #: 默认 `False`：缺省不删——导出的 JSON 里少一个对象最可能是手滑。
    allowDelete: bool = False
    #: apply（`dryRun=false`）必填：dry-run 那份计划的 checksum，服务端拿它
    #: 与"对当前库重新算一遍"的结果比对，不一致就拒。
    checksum: str | None = None
    #: 声明出来只是为了被路由层的 `_reject_forbidden_fields` 抓到——本端点
    #: 永远不读这两个字段的值，也不会因为它们是 `None` 而放行「压根没传」。
    events: Any | None = None
    projections: Any | None = None


class ImportOpOut(_Out):
    """计划/执行结果里的一条动作（契约「JSON 一键导入编辑」节）。"""

    op: Literal["create", "update", "delete"]
    #: `create` 在 dry-run 阶段恒为 `null`（还没有 id）；apply 后回填真实 id。
    id: str | None = None
    fields: dict[str, Any] = Field(default_factory=dict)


class ImportResultOut(_Out):
    """``POST /api/core/import`` 的响应（dry-run 与 apply 共用同一个形状）。"""

    dryRun: bool
    checksum: str
    allowDelete: bool
    #: dry-run 时非 null（要做什么）；apply 时恒为 `null`（看 `applied`）。
    plan: dict[str, list[ImportOpOut]] | None = None
    #: apply 时非 null（真做了什么，`create` 的 `id` 已回填）；dry-run 时恒为 `null`。
    applied: dict[str, list[ImportOpOut]] | None = None
    summary: dict[str, int] = Field(default_factory=dict)
    #: 只在 dry-run 且 `allowDelete=false` 时有内容：库里有、payload 没提到、
    #: 但因为没开 `allowDelete` 而不会被删的 id，仅供参考，不参与 checksum。
    skippedDeletes: dict[str, list[str]] | None = None