"""v2.8 AI 提议新任务（契约「活动建议」节「AI 提议新任务」，唯一事实源）。

仓主 2026-10-03：「AI 应该能自动加新任务」，定为**草稿 + 一键确认**——助理只能提议「在项目 P 下建任务 N」，
人点「是」才建。本文件管两件事：

- ``propose``：助理交来的 ``newTask`` 校验、按（项目, 归一化名字）去重成一条提议；
- ``task_for``：人确认时取任务——同一提议**只建一个**任务（预留 id + 条件更新 + 唯一索引），并发的确认复用它。

建任务走 planner **既有的写入口**（``guard.run_write`` → ``create_task``，同 ``POST /api/core/planner/tasks``）：
来源判定、二次设防、审计流水一样不少，没有为这里另开一个更宽的口子。
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from pymongo.errors import DuplicateKeyError

from ..planner import audit, guard
from ..planner import service as planner_service
from . import repo

MAX_NAME = 64  # 码点


class ConflictError(RuntimeError):
    """建议 / 提议的当前状态与请求冲突。main.py 映射成 409（经 service.ConflictError，同一个类）。"""


def clean_name(name: str) -> str:
    """去首尾空白，1–64 码点。不合规抛 ValueError（matches 进 rejected；confirm 由调用方转 400）。"""
    cleaned = (name or "").strip()
    if not cleaned:
        raise ValueError("新任务的 name 为空或全空白")
    if len(cleaned) > MAX_NAME:
        raise ValueError(f"新任务的 name 超过 {MAX_NAME} 个字：{cleaned[:20]!r}…")
    return cleaned


def _norm(name: str) -> str:
    return " ".join(name.split()).casefold()


def _same_name_task(project_id: str, name: str) -> dict | None:
    n = _norm(name)
    return next((t for t in planner_service.list_tasks(project_id) if _norm(t["name"]) == n), None)


def propose(user: str, doc: dict, project_id: str, name: str, now: datetime) -> tuple[dict | None, str | None]:
    """校验并取（或新建）提议。返回 ``(suggestion.newTask, None)`` 或 ``(None, 拒绝理由)``。"""
    try:
        name = clean_name(name)
    except ValueError as exc:
        return None, str(exc)
    if planner_service.get_project(project_id) is None:
        return None, f"项目不存在：{project_id!r}"
    same = _same_name_task(project_id, name)
    if same is not None:
        return None, f"项目里已有同名任务 {same['id']!r}，请用 taskId 配它"
    pid = "tp_" + hashlib.sha256(f"{project_id}\n{_norm(name)}".encode()).hexdigest()[:20]
    if pid in doc.get("rejectedProposalIds", []):
        return None, "用户已经否掉过这个新任务，不要再提"
    p = repo.proposal_upsert(user, pid, project_id, name, now)
    if p["status"] == "accepted":
        return None, f"这个新任务已经建好了（{p['taskId']!r}），请用 taskId 配它"
    if p["status"] == "rejected":
        return None, "用户已经否掉过这个新任务，不要再提"
    return {"proposalId": pid, "projectId": project_id, "name": p["name"]}, None


def is_open(user: str, proposal_id: str) -> bool:
    """提议还能用（待定 / 已接受）。已否掉、已过期的不能再挂到建议上，也不能再建。"""
    p = repo.proposal_get(user, proposal_id)
    return p is not None and p["status"] in ("pending", "accepted")


def statuses(user: str, proposal_ids: list[str]) -> dict:
    return repo.proposal_statuses(user, proposal_ids)


def task_for(user: str, proposal_id: str, name: str | None, request) -> str:
    """人确认带提议的建议：返回要记到的任务 id。同一提议**结构上**只建一个任务，不靠锁：

    1. 任务 id 在提议第一次提出时就预留好（存在提议上）；
    2. 先把提议 pending → accepted（条件更新），同时定下名字——第一位确认的人改的名字为准；
       与「否」（pending → rejected，同样是条件更新）二选一，已否掉的提议永远建不出任务；
    3. 再用预留的 id 建任务：撞 ``(user, id)`` 唯一索引 = 别的确认刚建好，直接用。
       建好记 ``created``——之后人把任务删了也不会用同一个 id 再建（id 不复用）。

    在第 2、3 步之间崩掉，重试走到第 3 步照样补建，名字用已定下的那个。
    """
    repo.proposal_accept(user, proposal_id, name, datetime.now(timezone.utc))
    p = repo.proposal_get(user, proposal_id)
    if p is None:
        raise ConflictError("这条新任务提议已过期，请刷新后自己选任务")
    if p["status"] != "accepted":
        raise ConflictError("这个新任务提议已被否掉，请刷新后自己选任务")
    if not p.get("created"):
        if planner_service.get_task(p["taskId"]) is None:
            try:
                guard.run_write(
                    request, op=audit.OP_CREATE, object_type="tasks",
                    changes={"projectId": p["projectId"], "name": p["name"]},
                    action=lambda actor: planner_service.create_task(
                        p["name"], p["projectId"], actor=actor, task_id=p["taskId"]),
                )
            except DuplicateKeyError:
                pass  # 并发的另一次确认刚用同一个 id 建好（审计里留一条 failed，如实）
        repo.proposal_created(user, proposal_id)
    return p["taskId"]
