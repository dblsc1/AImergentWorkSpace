"""任务 ``dependsOn`` 的图校验（契约 v1.1「排期与依赖」）。

拆成独立文件的理由：`service.py` 是 zones/projects/tasks 三类对象共用的 CRUD
逻辑，`dependsOn` 的存在性/自指/成环校验是**任务专属**的一小块自成一体的算法
（图 + DFS），拆出来两边都更好读；也顺带把 `service.py` 拉回契约「内部子边界」
钉死的 300 行以内（比单文件 500 行的上限更严）。**不是新的子边界**——本文件仍在
`planner/` 内部，仍只服务于 `service.py`，不被别的子模块直接调用。
"""

from __future__ import annotations

from . import repo
from .errors import InvalidInputError


def find_cycle(graph: dict[str, list[str]], start: str) -> list[str] | None:
    """从 ``start`` 出发按 ``dependsOn`` 边做 DFS，找一条经过 ``start`` 的环。

    只需要从 ``start`` 出发就够（不必扫全任务图找任意环）：这条校验在**每一次**
    写入时都会跑，所以图在任何时刻都是无环的——新环只可能通过"这一次改动的边"
    产生，而改动的边全部挂在 ``start``（正在被创建/更新的那个任务）身上。
    返回完整路径（如 ``[t_a, t_b, t_c, t_a]``），供错误消息指名道姓；无环返回 ``None``。
    """

    def dfs(node: str, path: list[str], on_path: set[str]) -> list[str] | None:
        for nxt in graph.get(node, ()):
            if nxt in on_path:
                return path + [nxt]
            found = dfs(nxt, path + [nxt], on_path | {nxt})
            if found is not None:
                return found
        return None

    return dfs(start, [start], {start})


def validate_depends_on(task_id: str | None, depends_on: list[str] | None, owner: str) -> list[str]:
    """``dependsOn`` 三条校验：引用存在、不含自身、不成环。

    ``task_id`` 为 ``None`` 时是建任务（新任务还没有 id，不可能被别人引用，
    也不可能自指），仍复用同一份校验——存在性检查照样要跑。
    """
    if depends_on is None:
        return []
    node = task_id if task_id is not None else "__pending__"
    for dep_id in depends_on:
        if task_id is not None and dep_id == task_id:
            raise InvalidInputError(f"{owner} 的 dependsOn 不能包含自身：{dep_id!r}")
        if repo.get_task(dep_id) is None:
            raise InvalidInputError(f"{owner} 的 dependsOn 引用了不存在的任务：{dep_id!r}")
    graph = {t["id"]: list(t.get("dependsOn") or []) for t in repo.list_tasks()}
    graph[node] = list(depends_on)
    cycle = find_cycle(graph, node)
    if cycle is not None:
        raise InvalidInputError(f"{owner} 的 dependsOn 成环：{' → '.join(cycle)}")
    return list(depends_on)
