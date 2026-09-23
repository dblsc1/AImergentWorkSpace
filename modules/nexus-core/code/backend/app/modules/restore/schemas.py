"""``POST /api/core/restore`` 的请求/响应模型（契约 v1.9「快照恢复」）。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class RestoreRequest(BaseModel):
    """请求体 = ``GET /api/core/export`` 的**逐字节**输出，一个键都不用加减。

    所以 ``dryRun``/``checksum`` 走查询参数而不是请求体（与 import 不同）：
    ``curl -d @export.json`` 能直接喂进来，就是本端点存在的全部理由。

    四个数组**必填**：缺一个说明文件被截断或手改过，按「少了就是空」恢复会
    静默丢数据。多出来的键 → 422（``extra="forbid"``，同其余端点）。
    """

    model_config = ConfigDict(extra="forbid")

    zones: list[dict[str, Any]]
    projects: list[dict[str, Any]]
    tasks: list[dict[str, Any]]
    events: list[dict[str, Any]]
    #: 读进来但**不落库**——投影是派生物，落完台账现场重建。
    projections: dict[str, Any] | None = None
    #: 原样接受、忽略。
    exportedAt: str | None = None


class RestoreResultOut(BaseModel):
    """dry-run 与 apply 共用同一个形状，靠 ``dryRun`` 判断该看哪一半。"""

    dryRun: bool
    checksum: str
    #: 各类将要（dry-run）/ 已经（apply）写入的条数。
    summary: dict[str, int]
    #: apply 时非 null：``{投影名: 重放的事件数}``；dry-run 时恒为 null。
    rebuilt: dict[str, int] | None = None
