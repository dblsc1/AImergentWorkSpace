"""代理去向（契约 hive.stage.v1「三」，nexus-core v2.44）：整条运行改挂 + 代理去向规则。

只追加：每次 place 追加一条 ``agent.run.placed``（``dedupeKey = place:<runId>:<seq>``，同 v2.11 改挂的链），
运行当前算在谁头上 = ``seq`` 最大的那条。在跑的运行直接改活文档（结束时按它组装事实）；已结束的运行
把那条事实的时长抄进 placed 事件，投影的 handler 据此把整条秒数从旧去向搬到新去向。
ponytail: 与运行恰好同时关闭的 place 可能漏搬一次投影，重建会纠正。
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime

from ..events import service as events_service
from ..events.schemas import SPEC
from ..planner import service as planner_service
from ..planner.errors import NotFoundError, UnprocessableError
from . import agents as agents_impl
from . import repo

MAX_RULES = 500
_MAX_TRIES = 5


def _target(task_id: str | None, project_id: str | None, resolve_chain: Callable) -> tuple[str | None, str, str]:
    """→ (taskId, 项目, 分区)；目标不存在抛 404 类错误。"""
    if task_id is not None:
        _task, project, zone = resolve_chain(task_id, action="拒绝代理去向")
        return task_id, project, zone
    project = planner_service.get_project(project_id)
    if project is None or not project.get("zoneId"):
        raise NotFoundError(f"项目不存在：{project_id!r}")
    return None, project_id, project["zoneId"]


def rule_target(user: str, agent: str, resolve_chain: Callable) -> tuple[str | None, str | None] | None:
    """``agents/start`` 查表：完全相同的 agent 有规则且目标还在 → (taskId, projectId)，否则 None（目标已删当没规则）。"""
    rule = repo.get_place_rule(user, agent)
    if rule is None:
        return None
    try:
        if rule.get("taskId"):
            return rule["taskId"], _target(rule["taskId"], None, resolve_chain)[1]
        return None, _target(None, rule["projectId"], resolve_chain)[1]
    except (NotFoundError, LookupError):  # UnknownTaskError 也是 LookupError
        return None


def _finished_event(user: str, run_id: str) -> dict | None:
    return events_service.find_by_dedupe(user, agents_impl.SOURCE, agents_impl._dedupe_key(run_id))  # noqa: SLF001


def place(run_id: str, task_id: str | None, project_id: str | None, remember: bool, user: str, *,
          resolve_chain: Callable, now: Callable[[], datetime]) -> dict:
    run = repo.get_agent_run(user, run_id)
    if run is not None and "closing" in run:
        agents_impl._finish(run)  # noqa: SLF001 —— 标记后未落账的先收尾，下面按已结束处理
        run = None
    stored = None if run else _finished_event(user, run_id)
    if run is None and stored is None:
        raise NotFoundError(f"代理运行不存在：{run_id!r}")
    agent = run["agent"] if run else (stored.get("data") or {}).get("agent")
    task_id, project, zone = _target(task_id, project_id, resolve_chain)
    if remember:
        if not isinstance(agent, str) or not agent:
            raise UnprocessableError("运行没有代理名，不能记成去向规则（remember:false 可以）")
        if repo.get_place_rule(user, agent) is None and repo.count_place_rules(user) >= MAX_RULES:
            raise UnprocessableError(f"代理去向规则最多 {MAX_RULES} 条")

    if run is not None and not repo.set_open_run_subject(user, run_id, task_id, project, zone):
        stored = _finished_event(user, run_id)  # 刚好结束了
    subject = {"zone": zone, "project": project, **({"task": task_id} if task_id else {})}
    at = now().isoformat()
    for _ in range(_MAX_TRIES):
        chain = events_service.placements_of(run_id)
        seq = chain[-1]["data"]["seq"] + 1 if chain else 1
        data = {"runId": run_id, "projectId": project, "at": at, "seq": seq, **({"taskId": task_id} if task_id else {})}
        if stored is not None:
            before = (chain[-1] if chain else stored).get("subject") or {}
            src = stored.get("data") or {}
            data.update(fromTaskId=before.get("task"), fromProjectId=before.get("project"), run={
                "source": stored["source"], "dedupeKey": stored["dedupeKey"], "agent": agent,
                "startAt": src.get("startAt"), "durationSeconds": src.get("durationSeconds"),
                **({"days": src["days"]} if "days" in src else {}),
                **({"unverified": True} if src.get("unverified") else {})})
        result = events_service.ingest({
            "spec": SPEC, "id": f"evt_{uuid.uuid4().hex[:12]}", "dedupeKey": f"place:{run_id}:{seq}",
            "type": events_service.PLACED_TYPE, "user": user, "source": events_service.PLACE_SOURCE,
            "time": at, "subject": subject, "data": data, "flags": [],
        }, internal=True)
        if result.rejected:
            raise RuntimeError(f"代理去向信封未过事件校验：{result.rejected[0].reason}")
        if not result.duplicate:
            break
    else:
        raise RuntimeError(f"代理去向争用未决：{run_id!r}")

    rule = None
    if remember:
        repo.upsert_place_rule(user, agent, task_id, project_id, at)
        rule = {"agent": agent, **({"taskId": task_id} if task_id else {"projectId": project_id})}
    return {"runId": run_id, "taskId": task_id, "projectId": project, "rule": rule}


def list_rules(user: str, resolve_chain: Callable) -> list[dict]:
    """规则列表；目标已删的标 broken。"""
    out = []
    for r in repo.place_rules(user):
        try:
            _target(r.get("taskId"), r.get("projectId"), resolve_chain)
            broken = False
        except (NotFoundError, LookupError):
            broken = True
        out.append({"agent": r["agent"], "taskId": r.get("taskId"), "projectId": r.get("projectId"),
                    "createdAt": r["createdAt"], "broken": broken})
    return out


def delete_rule(user: str, agent: str) -> None:
    if not repo.delete_place_rule(user, agent):
        raise NotFoundError(f"代理去向规则不存在：{agent!r}")
