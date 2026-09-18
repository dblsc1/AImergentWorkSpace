"""``session.completed`` → ``proj_current``（贡献圆环的数据源）。

三条铁则（reviewer 会脚本化核验）：

- **幂等**：同一事件投两次，投影结果相同——靠 ``repo.apply_session`` 的
  ``dedupeKey`` 幂等守卫，不靠调用方自觉。
- **只写自己的投影集合**（``proj_current``），别的集合一个都不碰。
- **禁止发新事件**：本文件不 import ``events`` 子边界的任何东西。

对外公开（rules.md §7.4）：``handle(envelope)`` / ``read_current(user)``。
``read_current`` 是 views 的指定读路径——views 无 repo，不自己碰投影集合。
"""

from __future__ import annotations

from .. import repo


def handle(envelope: dict) -> None:
    """吃一条已落库的 ``session.completed``，累计到 ``proj_current``。

    外部事件可以没有 ``project``/``task``（B5）——没有内部 ID 就没有可累计的
    圆环条目，静默返回；这不是错误，是开放标准的正常形态。
    ``flags`` 与本 handler 无关：``no_growth`` 管的是成长树，不管时间统计。
    """
    subject = envelope.get("subject") or {}
    project_id = subject.get("project")
    task_id = subject.get("task")
    if not project_id and not task_id:
        return

    data = envelope.get("data") or {}
    seconds = data.get("durationSeconds")
    if not isinstance(seconds, (int, float)) or isinstance(seconds, bool) or seconds <= 0:
        return  # 没有正时长就没有可累计的东西；坏载荷不炸投影（事件本身已落库）

    repo.apply_session(
        user=envelope["user"],
        dedupe_key=envelope["dedupeKey"],
        project_id=project_id,
        task_id=task_id,
        seconds=int(seconds),
    )


def read_current(user: str) -> dict | None:
    """views 的指定读路径（rules.md §7.4 公开接口）。"""
    return repo.read_current(user)
