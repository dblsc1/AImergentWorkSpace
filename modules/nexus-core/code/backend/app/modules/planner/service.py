"""planner 的公开函数——**跨子边界只准调这里**。

切片 2 起是完整 CRUD（contract.md v0.4）。三条硬语义：

- **删除拒绝级联，409**：还有子对象就拒绝并说明还剩几个——
  一次误操作抹掉一棵树是不可逆的，把不可逆动作拆成可见的几步。
- **id 不可变、不可复用**：改名只动 name、换归属只动归属字段，key 重算；
  新建对象永远拿新 id（uuid），历史事件因此永远指向"曾经存在过的东西"。
- **校验指名道姓**：错误里点出是哪个字段、哪个 id，禁止用默认值静默继续。

key 重算的口径（J10「出生地」原则）：只重算**被操作对象自己**的 key，
不级联重算子对象——key 前缀本就不承诺表示当前归属，归属永远看 id 字段。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from . import deps, inbox, repo
from .actor import apply_actor_update, normalize_actor
# 异常类真身在 errors.py（给 deps.py 用，避免互相 import 成环）；这里 import 顺带
# 让 service.HasChildrenError 等历史调用点（main.py、测试）不用跟着改。
from .errors import HasChildrenError, InvalidInputError, NotFoundError, UnknownProjectError
from .planvalidate import validate_plan as _validate_plan

#: well-known 收件箱项目 id（契约 v1.5「收件箱」节，F-INBOX-1）。跨边界读端
#: （如 `views/review.py`）判断"这是不是 inbox 项目"用这个常量，不猜字符串——
#: 真相住在 `inbox.py`，这里只转发（同 errors.py 的转发先例）。种子脚本要建
#: 收件箱则直接 `from .planner import inbox`——`ensure()` 不经 service.py
#: 转发（省一层薄包装，把本文件拉回 300 行预算），种子脚本本就已经直接用
#: `planner.repo`/`planner.service` 两层，多认一个 `planner.inbox` 不算新越界。
INBOX_PROJECT_ID = inbox.PROJECT_ID

_VALID_KINDS = ("normal", "ephemeral")


def _clean_name(name: str, owner: str) -> str:
    cleaned = (name or "").strip()
    if not cleaned:
        raise InvalidInputError(f"{owner} 的 name 为空或全空白")
    return cleaned


def _check_weight(weight: float | None, owner: str) -> float:
    if weight is None:
        return 100.0  # 契约未定默认值；取满权重，这一决定已记录在 worklog 备查
    if weight < 0:
        raise InvalidInputError(f"{owner} 的 plannedWeight 不能为负：{weight}")
    return float(weight)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ------------------------------------------------------------------ 读


def get_zone(zone_id: str) -> dict | None:
    return repo.get_zone(zone_id)


def get_project(project_id: str) -> dict | None:
    return repo.get_project(project_id)


def get_task(task_id: str) -> dict | None:
    return repo.get_task(task_id)


def list_zones() -> list[dict]:
    return repo.list_zones()


def list_projects(zone_id: str | None = None) -> list[dict]:
    return repo.list_projects(zone_id)


def list_tasks(project_id: str | None = None) -> list[dict]:
    return repo.list_tasks(project_id)


def list_tree() -> tuple[list[dict], list[dict]]:
    """(zones, projects-with-tasks) 原始文档。塑形归 views/queries。"""
    projects = repo.list_projects()
    for project in projects:
        project["tasks"] = repo.list_tasks_by_project(project["id"])
    return repo.list_zones(), projects


# ------------------------------------------------------------------ 建


def create_zone(
    name: str, color: str | None = None, order: int | None = None,
    actor: str | None = None,
) -> dict:
    name = _clean_name(name, "分区")
    zone = {
        "id": f"z_{uuid.uuid4().hex[:6]}",
        "key": str(repo.name_num(name)),  # 分区 key = <分区号>（v0.4 逐级前缀）
        "name": name,
        "color": color if color is not None else "#999999",  # 契约未定默认，见 worklog
        "order": order if order is not None else len(repo.list_zones()),
        "lastWriter": normalize_actor(actor),
    }
    repo.insert_one("zones", zone)
    return zone


def create_project(
    zone_id: str,
    name: str,
    planned_weight: float | None = None,
    plan: dict | None = None,
    status: str = "active",
    actor: str | None = None,
) -> dict:
    zone = repo.get_zone(zone_id)
    if zone is None:
        raise InvalidInputError(f"zoneId 不存在：{zone_id!r}")
    name = _clean_name(name, "项目")
    project = {
        "id": f"p_{uuid.uuid4().hex[:6]}",
        "key": f"{repo.name_num(zone['name'])}-{repo.name_num(name)}",
        "zoneId": zone_id,
        "name": name,
        "status": status,
        "plannedWeight": _check_weight(planned_weight, "项目"),
        "plan": _validate_plan(plan, "项目"),
        "progress": 0,
        "progressSource": "computed",
        "lastWriter": normalize_actor(actor),
    }
    repo.insert_one("projects", project)
    return project


def create_task(
    name: str,
    project_id: str,
    *,
    kind: str = "normal",
    done: bool = False,
    flags: list[str] | None = None,
    planned_weight: float | None = None,
    plan: dict | None = None,
    depends_on: list[str] | None = None,
    actor: str | None = None,
) -> dict:
    """建任务：id 与 key 都由系统生成（契约「建任务时」）。``plan``/``dependsOn``
    是 v1.1 新增字段，校验与 PATCH 共用同一份函数，不是两套各管一半。"""
    project = repo.get_project(project_id)
    if project is None:
        raise UnknownProjectError(f"projectId 不存在：{project_id!r}，无法在其下建任务")
    zone = repo.get_zone(project["zoneId"]) if project.get("zoneId") else None
    if zone is None:
        raise UnknownProjectError(
            f"项目 {project_id!r} 的归属分区 {project.get('zoneId')!r} 不存在——数据链断裂"
        )
    name = _clean_name(name, "任务")
    if kind not in _VALID_KINDS:
        raise InvalidInputError(f"任务 kind 非法：{kind!r}，合法取值 {'/'.join(_VALID_KINDS)}")

    task = {
        "id": f"t_{uuid.uuid4().hex[:6]}",
        "key": _task_key(zone["name"], project["name"], name,
                         repo.count_same_name_in_project(project_id, name) + 1),
        "name": name,
        "projectId": project_id,
        "done": done,
        "doneAt": _now_iso() if done else None,
        "kind": kind,
        "flags": list(flags or []),
        "plannedWeight": _check_weight(planned_weight, "任务"),
        "plan": _validate_plan(plan, "任务"),
        "dependsOn": deps.validate_depends_on(None, depends_on, "任务"),
        "lastWriter": normalize_actor(actor),
    }
    repo.insert_task(task)
    return task


def _task_key(zone_name: str, project_name: str, task_name: str, serial: int) -> str:
    return (
        f"{repo.name_num(zone_name)}-{repo.name_num(project_name)}"
        f"-{repo.name_num(task_name)}-{serial}"
    )


# ------------------------------------------------------------------ 改


def update_zone(zone_id: str, fields: dict) -> dict:
    zone = repo.get_zone(zone_id)
    if zone is None:
        raise NotFoundError(f"分区不存在：{zone_id!r}")
    updates = apply_actor_update(dict(fields))  # actor（若有）→ lastWriter
    if "name" in updates:
        updates["name"] = _clean_name(updates["name"], "分区")
        updates["key"] = str(repo.name_num(updates["name"]))  # 改名 → key 重算，id 不动
    return repo.update_by_id("zones", zone_id, updates)


def update_project(project_id: str, fields: dict) -> dict:
    project = repo.get_project(project_id)
    if project is None:
        raise NotFoundError(f"项目不存在：{project_id!r}")
    updates = apply_actor_update(dict(fields))  # actor（若有）→ lastWriter
    if "name" in updates:
        updates["name"] = _clean_name(updates["name"], "项目")
    if "zoneId" in updates and repo.get_zone(updates["zoneId"]) is None:
        raise InvalidInputError(f"zoneId 不存在：{updates['zoneId']!r}")
    if "plannedWeight" in updates:
        updates["plannedWeight"] = _check_weight(updates["plannedWeight"], "项目")
    if "plan" in updates:  # 甘特「计划」图层写侧（契约 G5/G6）；plan=null 合法，是清空
        updates["plan"] = _validate_plan(updates["plan"], "项目")
    if "name" in updates or "zoneId" in updates:  # 改名/搬移 → key 重算（只算自己）
        zone = repo.get_zone(updates.get("zoneId", project["zoneId"]))
        name = updates.get("name", project["name"])
        updates["key"] = f"{repo.name_num(zone['name'])}-{repo.name_num(name)}"
    return repo.update_by_id("projects", project_id, updates)


def update_task(task_id: str, fields: dict) -> dict:
    task = repo.get_task(task_id)
    if task is None:
        raise NotFoundError(f"任务不存在：{task_id!r}")
    updates = apply_actor_update(dict(fields))  # actor（若有）→ lastWriter
    if "name" in updates:
        updates["name"] = _clean_name(updates["name"], "任务")
    if "projectId" in updates and repo.get_project(updates["projectId"]) is None:
        raise InvalidInputError(f"projectId 不存在：{updates['projectId']!r}")
    if "kind" in updates and updates["kind"] not in _VALID_KINDS:
        raise InvalidInputError(
            f"任务 kind 非法：{updates['kind']!r}，合法取值 {'/'.join(_VALID_KINDS)}"
        )
    if "plannedWeight" in updates:
        updates["plannedWeight"] = _check_weight(updates["plannedWeight"], "任务")
    if "done" in updates:  # done→doneAt 联动：完成盖时间戳，取消完成清掉
        updates["doneAt"] = _now_iso() if updates["done"] else None
    if "plan" in updates:  # 任务自己的排期（契约 v1.1）；plan=null 合法，是清空
        updates["plan"] = _validate_plan(updates["plan"], "任务")
    if "dependsOn" in updates:  # 前置依赖（契约 v1.1）；dependsOn=null 视同清空为 []
        updates["dependsOn"] = deps.validate_depends_on(task_id, updates["dependsOn"], "任务")
    if "name" in updates or "projectId" in updates:  # 改名/换归属 → key 重算，id 不动
        project = repo.get_project(updates.get("projectId", task["projectId"]))
        zone = repo.get_zone(project["zoneId"])
        name = updates.get("name", task["name"])
        serial = repo.count_same_name_in_project(project["id"], name, exclude_id=task_id) + 1
        updates["key"] = _task_key(zone["name"], project["name"], name, serial)
    return repo.update_by_id("tasks", task_id, updates)


# ------------------------------------------------------------------ 删


def delete_zone(zone_id: str) -> None:
    if repo.get_zone(zone_id) is None:
        raise NotFoundError(f"分区不存在：{zone_id!r}")
    remaining = repo.count_children("projects", "zoneId", zone_id)
    if remaining:
        raise HasChildrenError(f"分区 {zone_id!r} 下还有 {remaining} 个项目——不做级联删除，先清空再删")
    repo.delete_by_id("zones", zone_id)


def delete_project(project_id: str) -> None:
    """删除项目：存在性 → **系统单例保护** → 拒绝级联，三道判据依次过，
    互不混淆（v1.5 新增第二道：`p_inbox` 恒 409，触发条件是"这是收件箱"，
    不是"下面还有任务"——两种 409 复用同一个异常类，但别把判据合并进一个
    `if`，语义不同，只是外部表现相同）。"""
    if repo.get_project(project_id) is None:
        raise NotFoundError(f"项目不存在：{project_id!r}")
    if inbox.is_protected_project(project_id):
        raise HasChildrenError(f"项目 {project_id!r} 是系统收件箱（捕捉落点），禁止删除")
    remaining = repo.count_children("tasks", "projectId", project_id)
    if remaining:
        raise HasChildrenError(f"项目 {project_id!r} 下还有 {remaining} 个任务——不做级联删除，先清空再删")
    repo.delete_by_id("projects", project_id)


def delete_task(task_id: str) -> None:
    """删除任务：**被别的任务依赖时拒绝**（契约 v1.1，与「拒绝级联」同语义，
    复用同一个 ``HasChildrenError`` → 409 映射，报文点名依赖者）。"""
    if repo.get_task(task_id) is None:
        raise NotFoundError(f"任务不存在：{task_id!r}")
    dependents = repo.find_dependents(task_id)
    if dependents:
        names = "、".join(f"{d['name']!r}({d['id']})" for d in dependents)
        raise HasChildrenError(
            f"任务 {task_id!r} 被 {len(dependents)} 个任务依赖（{names}）——先解除依赖再删"
        )
    repo.delete_by_id("tasks", task_id)