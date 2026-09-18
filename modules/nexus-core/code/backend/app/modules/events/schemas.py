"""yq-event/v1 信封模型 + 入口响应模型。

信封唯一事实是仓根 ``contracts/yq-event-v1.md``，
本文件按它逐字段建模，**不发明字段、不收紧它没收紧的**：

- ``extra="allow"``：未知信封字段**接受并原样保留**（边界样例 B9，
  「只增不改不删」的另一面）。
- ``dedupeKey`` 必填（B3：缺了拒绝，**不许回退成用 id**——校验层缺字段直接拒，
  根本走不到防重层，也就无从回退）。
- ``subject`` 只存三个 opaque id（J10 标识三分）：``zone``/``project`` 必填、
  ``task`` 选填（B4/B5）；旧模型的 ``tier2``/``tier3`` **显式拒绝**（B5b）——
  接受它等于让两套模型并存，历史会自相矛盾。
- ``recordedAt`` 在输入侧选填：**客户端填了也会被服务端覆盖**（B6），覆盖发生在
  ``service.py``，本模型不管。
- ``time`` 必须是 ISO8601 **带时区**；可以是过去（B7 补交）。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SPEC = "yq-event/v1"

#: J10 已废的旧模型字段。出现即拒（契约 B5b），不是静默忽略——
#: 静默忽略会让老客户端以为分类还生效，历史悄悄分叉。
_RETIRED_SUBJECT_FIELDS = ("tier2", "tier3")


class Subject(BaseModel):
    """三个 opaque id，只存 id 不存名（契约 §5：改名/搬移不污染历史）。"""

    model_config = ConfigDict(extra="allow")

    zone: str = Field(min_length=1)
    project: str = Field(min_length=1)
    #: 建任务时由系统生成；无具体任务的事件（如通用成长点）可省。
    task: str | None = None

    @model_validator(mode="after")
    def _reject_retired_fields(self) -> "Subject":
        extras = self.__pydantic_extra__ or {}
        for field in _RETIRED_SUBJECT_FIELDS:
            if field in extras:
                raise ValueError(
                    f"subject.{field} 是 J10 已废弃的旧模型字段（契约 B5b）：拒绝，"
                    f"subject 只存 zone/project/task 三个 opaque id"
                )
        return self


class Envelope(BaseModel):
    """事件信封。**信封字段只增不改不删**——扩展 = 新增 type，不改这里。"""

    model_config = ConfigDict(extra="allow")

    spec: str
    id: str = Field(min_length=1)
    dedupeKey: str = Field(min_length=1)
    type: str = Field(min_length=1)
    user: str = Field(min_length=1)
    source: str = Field(min_length=1)
    time: str
    #: 输入侧选填；服务端盖章覆盖（B6）。放在模型里只为「字段存在」，值不作数。
    recordedAt: str | None = None
    subject: Subject
    data: dict | None = None
    flags: list[str] = Field(default_factory=list)
    ai: dict | None = None

    @field_validator("spec")
    @classmethod
    def _spec_fixed(cls, v: str) -> str:
        if v != SPEC:
            raise ValueError(f"spec 必须是 {SPEC!r}，实际 {v!r}")
        return v

    @field_validator("time")
    @classmethod
    def _time_iso_with_tz(cls, v: str) -> str:
        """ISO8601 且**带时区**。可以是过去（B7）——只验格式，不验先后。"""
        try:
            parsed = datetime.fromisoformat(v)
        except ValueError as exc:
            raise ValueError(f"time 不是合法 ISO8601：{v!r}") from exc
        if parsed.tzinfo is None:
            raise ValueError(f"time 缺少时区：{v!r}（契约要求 ISO8601 带时区）")
        return v


class RejectedItem(BaseModel):
    index: int
    reason: str


class IngestOut(BaseModel):
    """``POST /api/core/events`` 响应（contract.md IngestOut）。

    重复不是错误：命中防重键计入 ``duplicate`` 并返回 200，不是 4xx。
    部分失败不整批回滚：校验不过的进 ``rejected``，合法的照常落库。
    """

    accepted: int
    duplicate: int
    rejected: list[RejectedItem]


class ArchiveOut(BaseModel):
    """``GET /api/core/events`` 响应（contract.md v0.6「档案读端」）。

    ``items`` 是事件信封原样（剔除 ``_id``），字段随 ``type`` 变化，不建专门模型
    ——那会变成信封的第二份定义，唯一事实仍是仓根 ``contracts/yq-event-v1.md``。
    """

    total: int
    items: list[dict]
