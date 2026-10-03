"""v2.8 AI 提议新任务（契约「活动建议」节「AI 提议新任务」，唯一事实源）。

仓主 2026-10-03：「AI 应该能自动加新任务」，定为**草稿 + 一键确认**——助理只能提议「在项目 P 下建任务 N」，
人点「是」才建。本文件管两件事：

- ``propose``：助理交来的 ``newTask`` 校验、按（项目, 归一化名字）去重成一条提议；
- ``task_for``：人确认时取任务——同一提议**只建一个**任务（建任务锁 + 同名任务兜底），并发的确认复用它。

建任务走 planner **既有的写入口**（``guard.run_write`` → ``create_task``，同 ``POST /api/core/planner/tasks``）：
来源判定、二次设防、审计流水一样不少，没有为这里另开一个更宽的口子。
"""

from __future__ import annotations

import hashlib
import time
import uuid
from datetime import datetime, timedelta, timezone

from ..planner import audit, guard
from ..planner import service as planner_service
from . import repo

MAX_NAME = 64  # 码点
#: 建任务锁的过期：建的那一次崩了，后来的确认可以接手（接手先找同名任务，不会建出第二个）
_LOCK_STALE = timedelta(seconds=30)
#: 没抢到锁的确认最多等这么久（对方建完就复用）；等不到回 409 让人重试
_WAIT_SECONDS = 10.0


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


def task_for(user: str, proposal_id: str, name: str | None, request) -> str:
    """人确认带提议的建议：返回要记到的任务 id。提议已建成就复用；否则抢锁建（同一提议只建一个）。

    ``name`` = 人改过的名字（已校验），缺省用提议的名字。抢到锁之后先找项目里的同名任务：人刚手建的、
    或上一次建完崩在记账之前的，都直接用——锁过期接手也不会建出第二个。
    """
    token, deadline = uuid.uuid4().hex, time.monotonic() + _WAIT_SECONDS
    while True:
        p = repo.proposal_get(user, proposal_id)
        if p is None:
            raise ConflictError("这条新任务提议已过期，请刷新后自己选任务")
        if p["status"] == "accepted":
            return p["taskId"]
        now = datetime.now(timezone.utc)
        if repo.proposal_lock(user, proposal_id, token, now, now - _LOCK_STALE):
            want = name or p["name"]
            try:
                task = _same_name_task(p["projectId"], want) or guard.run_write(
                    request, op=audit.OP_CREATE, object_type="tasks",
                    changes={"projectId": p["projectId"], "name": want},
                    action=lambda actor: planner_service.create_task(want, p["projectId"], actor=actor),
                )
            except Exception:
                repo.proposal_unlock(user, proposal_id, token)
                raise
            repo.proposal_accept(user, proposal_id, task["id"], now)
            return task["id"]
        if time.monotonic() > deadline:
            raise ConflictError("这个新任务正在由另一次确认建，请稍后重试")
        # ponytail: 轮询等对方建完；同一提议同时确认的只有同一组的几段，0.1 秒一轮足够
        time.sleep(0.1)
