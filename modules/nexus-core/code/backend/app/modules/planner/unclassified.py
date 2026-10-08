"""项目的「未分类」时间桶（契约 v2.9「项目未分类时间」节，唯一事实源）。

仓主 2026-10-08：不再快捷新建只有日期时间的占位任务，时间直接记进项目的「未分类」。
桶是一个**系统任务**（`kind=unclassified`、id 由项目 id 派生、每个项目至多一个、用到才建）——
计时 / 补登 / 确认 / 台账 / 投影全都按 taskId 归，做成任务它们一行不用改。

拆成独立文件的理由同 `inbox.py`：`service.py` 贴着 300 行预算。**不是新的子边界**。
"""

from __future__ import annotations

from . import repo
from .errors import HasChildrenError, NotFoundError

KIND = "unclassified"
NAME = "未分类"


def task_id(project_id: str) -> str:
    """桶的 id。带上完整的项目 id（不去 `p_` 前缀）：恢复进来的项目 id 不保证有前缀，去了会撞。"""
    return f"t_unc_{project_id}"


def is_bucket(task: dict | None) -> bool:
    return bool(task) and task.get("kind") == KIND


def protect(task: dict, verb: str) -> None:
    """桶不能经任务端点改 / 删。409：请求合法，冲突的是「这是系统单例」（同 `p_inbox` 禁删）。"""
    if is_bucket(task):
        raise HasChildrenError(f"任务 {task['id']!r} 是项目的「未分类」时间桶（系统任务），不能{verb}")


def find(project_id: str) -> dict | None:
    return repo.get_task(task_id(project_id))


def changes(project_id: str) -> dict:
    """审计里记的变更摘要（给 `guard.run_write`）。"""
    return {"projectId": project_id, "name": NAME, "kind": KIND}


def create(project_id: str, actor: str | None) -> dict:
    """建桶——`guard.run_write` 的 action（调用方先 `find`，没有才来这里）。并发首建时后到者撞
    `(user, id)` 唯一索引，那就是「建好了」：同一项目结构上不可能有两个桶。"""
    from . import service  # noqa: PLC0415 —— service 也 import 本文件（保护判据），模块级会成环

    if repo.get_project(project_id) is None:
        raise NotFoundError(f"项目不存在：{project_id!r}")
    try:
        return service.create_task(NAME, project_id, kind=KIND, actor=actor, task_id=task_id(project_id))
    except repo.DuplicateKeyError:
        task = find(project_id)
        if task is None:  # 撞了又没了：项目刚被并发删掉
            raise NotFoundError(f"项目不存在：{project_id!r}") from None
        return task
