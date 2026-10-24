"""v2.8 AI 提议新任务（契约「活动建议」节「AI 提议新任务」，唯一事实源）。

仓主 2026-10-03：「AI 应该能自动加新任务」，定为**草稿 + 一键确认**——助理只能提议「在项目 P 下建任务 N」，
人点「是」才建。本文件管两件事：

- ``propose``：助理交来的 ``newTask`` 校验、按（项目, 归一化名字）去重成一条提议；
- ``task_for``：人确认时取任务——同一提议**只建一次**任务（预留 id + 持久的「建」占位），并发的确认复用它。

建任务走 planner **既有的写入口**（``guard.run_write`` → ``create_task``，同 ``POST /api/core/planner/tasks``）：
来源判定、二次设防、审计流水一样不少，没有为这里另开一个更宽的口子。
"""

from __future__ import annotations

import hashlib
import time
from datetime import datetime, timezone

from ..planner import audit, guard
from ..planner import service as planner_service
from ..planner.errors import NotFoundError
from . import repo

MAX_NAME = 64  # 码点
#: 没占到「建」的确认最多等这么久让任务出现（只在同一提议并发确认时才会等）
WAIT_SECONDS = 10.0


class ConflictError(RuntimeError):
    """建议 / 提议的当前状态与请求冲突。main.py 映射成 409（经 service.ConflictError，同一个类）。"""


def clean_name(name: str, what: str = "新任务的 name") -> str:
    """去首尾空白，1–64 码点。不合规抛 ValueError（matches 进 rejected；confirm 由调用方转 400）。"""
    cleaned = (name or "").strip()
    if not cleaned:
        raise ValueError(f"{what} 为空或全空白")
    if len(cleaned) > MAX_NAME:
        raise ValueError(f"{what} 超过 {MAX_NAME} 个字：{cleaned[:20]!r}…")
    return cleaned


def _norm(name: str) -> str:
    return " ".join(name.split()).casefold()


def collection(name: str) -> dict:
    """v2.10 集合标签 ``{key, name}``：名字的判据同新任务名；key = 归一化名字（写法略有出入的同名进同一个集合）。"""
    name = clean_name(name, "collection.name")
    return {"key": _norm(name), "name": name}


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
    """人确认带提议的建议：返回要记到的任务 id。同一提议**结构上**只建一次任务：

    1. 任务 id 在提议第一次提出时就预留好（存在提议上）；
    2. 提议 pending → accepted（条件更新），同时定下名字——第一位确认的人改的名字为准；
       与「否」（pending → rejected，同样是条件更新）二选一，已否掉的提议永远建不出任务；
    3. 任务已在 → 直接用。不在 → 先在提议上**持久地**占「建」（``createClaimedAt`` 空 → 现在，条件更新），
       只有占到的那一次去建。planner 的删除是硬删除，任务表里不留痕，所以「这个 id 建过没有」只能记在这里：
       占过之后任务不在 = 正在建，或建过又被删了 / 建的那一次崩了——等一会儿还不出现就 404，**绝不再建**
       （planner「id 不可复用」）。占位**永不放掉**：建的那一次报错（项目已删等）也一样——报错时可能其实已经插进去、
       又被并发地删掉了，放掉就可能建回来。代价：这条提议再也建不出任务，人改选现成任务（安全失败）。
    """
    repo.proposal_accept(user, proposal_id, name, datetime.now(timezone.utc))
    p = repo.proposal_get(user, proposal_id)
    if p is None:
        raise ConflictError("这条新任务提议已过期，请刷新后自己选任务")
    if p["status"] != "accepted":
        raise ConflictError("这个新任务提议已被否掉，请刷新后自己选任务")
    tid = p["taskId"]
    if planner_service.get_task(tid) is not None:
        return tid
    won = repo.proposal_claim_create(user, proposal_id, tid, datetime.now(timezone.utc))
    if won is not None:
        # 只用占到的那份文档里的 id / 名字 / 项目，不用之前读到的
        guard.run_write(
            request, op=audit.OP_CREATE, object_type="tasks",
            changes={"projectId": won["projectId"], "name": won["name"]},
            action=lambda actor: planner_service.create_task(
                won["name"], won["projectId"], actor=actor, task_id=won["taskId"]),
        )
        return won["taskId"]
    # 别人占着「建」：等它建好（同一组几段并发确认时）；等不到 = 建过又删了 / 那一次崩了
    deadline = time.monotonic() + WAIT_SECONDS
    while time.monotonic() < deadline:
        time.sleep(0.1)
        if planner_service.get_task(tid) is not None:
            return tid
    raise NotFoundError(f"这个新任务（{tid!r}）已不存在：建过又被删掉，或建的时候出了错。请自己选任务")
