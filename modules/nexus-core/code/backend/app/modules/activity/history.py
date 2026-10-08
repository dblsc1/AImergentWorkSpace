"""v2.12 匹配历史（契约「活动建议」节「匹配历史」，唯一事实源）。

仓主 2026-10-08：「学历史是要的」「直接历史加入 AI 的上下文」——助理配任务前先看人以前是怎么定的，
不再每次从零猜。本文件**只读**：把已确认的建议按（程序, 归一化标题）去重成「这个窗口 → 这个项目 / 任务」。

- 去向取**台账里**那条 ``session.completed`` 的当前归属（确认时人可以改选任务，建议上存的不一定是它；
  v2.11 改挂过的取最后一次改挂的去向）；
- 确认到项目「未分类」时间桶的 → 只给项目；任务 / 项目已删的 → 不出；
- 拆成独立文件的理由：``service.py`` 管写路径（上传 / 确认 / 配），这里一行写都没有。
"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timedelta, timezone

from ... import config
from ...tenant import current as current_tenant
from ..events import service as events_service
from ..planner import service as planner_service
from ..planner import unclassified
from . import repo
from .service import SOURCE

_DEFAULT_LIMIT, _MAX_LIMIT = 60, 200
# ponytail: 只看最近处理的这么多条已确认建议（TTL 14 天内一般远小于它）；不够用再改成聚合管道
_SCAN = 5000
_MAX_COLLECTIONS, _MAX_REJECTED, _REJECTED_SCAN = 30, 30, 500
_COUNTER = re.compile(r"\([0-9]+\)|\[[0-9]+\]|\s")


def norm_title(title: str) -> str:
    """去掉标题开头的状态符号（✳ ⠂ ● * 之类）、计数「(3)」「[2]」，空白并成一个；去完是空的就用原样的。
    与 AI助理页没有集合标签时的归并键同一个算法（``modules/assistant`` suggestions.js 的 normTitle）。"""
    t = " ".join((title or "").split())
    i = 0
    while i < len(t):
        if t[i] in "*•·" or unicodedata.category(t[i]) == "So":
            i += 1
        elif m := _COUNTER.match(t, i):
            i = m.end()
        else:
            break
    return t[i:] or t


class _Targets:
    """任务 id → 去向 ``{projectId, projectPath[, taskId, taskName, taskDone]}``；任务 / 项目已删为 None。带缓存。"""

    def __init__(self):
        self._tasks: dict[str, dict | None] = {}
        self._projects: dict[str, str | None] = {}

    def project_path(self, project_id: str) -> str | None:
        if project_id not in self._projects:
            p = planner_service.get_project(project_id)
            zone = planner_service.get_zone(p["zoneId"]) if p and p.get("zoneId") else None
            self._projects[project_id] = f"{zone['name'] if zone else '?'} / {p['name']}" if p else None
        return self._projects[project_id]

    def __call__(self, task_id: str) -> dict | None:
        if task_id not in self._tasks:
            task = planner_service.get_task(task_id)
            path = self.project_path(task["projectId"]) if task else None
            out = {"projectId": task["projectId"], "projectPath": path} if path else None
            if out and not unclassified.is_bucket(task):  # 桶 = 只定到了项目
                out.update(taskId=task["id"], taskName=task["name"], taskDone=bool(task.get("done")))
            self._tasks[task_id] = out
        return self._tasks[task_id]


def history(limit: int = _DEFAULT_LIMIT) -> dict:
    user, now = current_tenant(), datetime.now(timezone.utc)
    repo.purge(user, now - timedelta(days=config.settings.suggestion_ttl_days))  # 同列表：读时惰性清过期
    limit = _DEFAULT_LIMIT if limit <= 0 else min(limit, _MAX_LIMIT)
    docs = repo.confirmed(user, _SCAN)  # 最近处理的在前
    current = events_service.current_tasks(SOURCE, [f"activity:{d['id']}" for d in docs])
    target = _Targets()
    rows: dict[tuple, dict] = {}   # dict 保序 = 最近定的在前
    colls: dict[str, dict] = {}
    for d in docs:
        hit = current.get(f"activity:{d['id']}")  # 占了位、事实还没写成的没有
        to = target(hit[0]) if hit else None
        if to is None:
            continue
        title = norm_title(d["title"])
        coll = d["suggestion"].get("collection") or {}
        row = rows.get((d["app"], title))
        if row is None:
            rows[(d["app"], title)] = {
                "app": d["app"], "title": title, **({"collection": coll["name"]} if coll.get("name") else {}),
                **to, "count": 1, "lastConfirmedAt": d["decidedAt"].isoformat(),
                "via": "reassign" if hit[1] else "confirm" if "taskId" in to else "project",
            }
        elif (row["projectId"], row.get("taskId")) == (to["projectId"], to.get("taskId")):
            row["count"] += 1  # 只数与最近那次去向相同的：人改过主意的旧去向不算
        if coll.get("key"):
            c = colls.setdefault(coll["key"], {"name": coll.get("name") or coll["key"], "projectId": to["projectId"],
                                               "projectPath": to["projectPath"], "count": 0})
            c["count"] += c["projectId"] == to["projectId"]
    return {"items": list(rows.values())[:limit], "collections": list(colls.values())[:_MAX_COLLECTIONS],
            "rejected": _rejected(user, target, rows)}


def _rejected(user: str, target: _Targets, rows: dict) -> list[dict]:
    """人否掉过的（窗口, 任务），新的在前、去重、任务已删的不出；这个窗口现在的去向就是它的也不出（人后来改了主意）。"""
    out: dict[tuple, dict] = {}
    for d in repo.with_rejections(user, _REJECTED_SCAN):
        title = norm_title(d["title"])
        for task_id in d.get("rejectedTaskIds") or []:
            to = target(task_id) if isinstance(task_id, str) else None
            if to and "taskId" in to and rows.get((d["app"], title), {}).get("taskId") != task_id:
                out.setdefault((d["app"], title, task_id),
                               {"app": d["app"], "title": title, "taskId": task_id, "taskName": to["taskName"]})
    return list(out.values())[:_MAX_REJECTED]
