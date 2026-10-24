"""第二级规则：块名 → 任务或项目（契约 v2.38「三、第二级规则」）。精确匹配块名，不是正则；没有把握度，存在即生效。
同块名至多一条（``POST`` 同块名 = 改目标，保留原 id，``author`` / ``via`` / ``updatedAt`` 换成这次的）；目标任务 / 项目被删 → 规则失效（读时视为没有规则），不自动删，``GET`` 标 ``broken: true``。
"""

from __future__ import annotations

from datetime import datetime, timezone

from ...tenant import current as current_tenant
from ..planner import service as planner_service
from ..planner.errors import NotFoundError, UnprocessableError
from . import blocks_repo


def check_target(task_id: str | None, project_id: str | None) -> None:
    """``taskId`` 与 ``projectId`` 恰好给一个（否则 422）；目标不存在 → 404（``p_inbox`` 允许）。"""
    if (task_id is None) == (project_id is None):
        raise UnprocessableError("taskId 与 projectId 必须给一个、且只能给一个")
    if task_id is not None and planner_service.get_task(task_id) is None:
        raise NotFoundError(f"任务不存在：{task_id!r}")
    if project_id is not None and project_id != planner_service.INBOX_PROJECT_ID and planner_service.get_project(project_id) is None:
        raise NotFoundError(f"项目不存在：{project_id!r}")


def _alive(rules: list[dict]) -> dict[str, bool]:
    """规则 id → 目标还在不在（一次取任务、一次取项目，不逐条查）。"""
    tasks = {t["id"] for t in planner_service.tasks_by_ids(list({r["taskId"] for r in rules if r.get("taskId")}))}
    projects = {p["id"] for p in planner_service.projects_by_ids(list({r["projectId"] for r in rules if r.get("projectId")}))}
    return {r["id"]: (r["taskId"] in tasks if r.get("taskId") else r["projectId"] == planner_service.INBOX_PROJECT_ID
                      or r["projectId"] in projects) for r in rules}


def effective(user: str, keys: list[str]) -> dict[str, dict]:
    """块名 → 生效的第二级规则（目标已删的不算）。"""
    found = blocks_repo.rules_by_keys(user, keys) if keys else {}
    alive = _alive(list(found.values()))
    return {k: r for k, r in found.items() if alive[r["id"]]}


def _out(r: dict, broken: bool) -> dict:
    return {"id": r["id"], "blockKey": r["blockKey"], "taskId": r.get("taskId"), "projectId": r.get("projectId"),
            "author": r["author"], "via": r["via"], "createdAt": r["createdAt"].isoformat(), "updatedAt": r["updatedAt"].isoformat(),
            "broken": broken}


def listing() -> dict:
    rules = blocks_repo.rules_all(current_tenant())
    alive = _alive(rules)
    return {"items": [_out(r, not alive[r["id"]]) for r in rules]}


def check_room(block_key: str) -> None:
    """第二级规则满了（且这个块名还没有规则）→ 422。写块之前先问，免得块写了规则写不进。"""
    user = current_tenant()
    if blocks_repo.rule_count(user) >= blocks_repo.MAX_RULES and not blocks_repo.rules_by_keys(user, [block_key]):
        raise UnprocessableError(f"第二级规则最多 {blocks_repo.MAX_RULES} 条")


def create(block_key: str, task_id: str | None, project_id: str | None, via: str = "manual",
           author: str = "human", keep_existing: bool = False) -> tuple[dict, bool]:
    """建 / 替换。返回 ``(规则, 是不是新建)``。``check_target`` 由调用方先做（迁移不做：旧规则的目标可能已删，读时标 broken）。"""
    user = current_tenant()
    check_room(block_key)
    rule, created = blocks_repo.upsert_rule(user, block_key, {
        "user": user, "blockKey": block_key, "taskId": task_id, "projectId": project_id, "author": author,
        "updatedAt": datetime.now(timezone.utc), "via": via}, keep_existing)
    return _out(rule, False), created


def remove(rule_id: str) -> None:
    if not blocks_repo.delete_rule(current_tenant(), rule_id):
        raise NotFoundError(f"第二级规则不存在：{rule_id!r}")
