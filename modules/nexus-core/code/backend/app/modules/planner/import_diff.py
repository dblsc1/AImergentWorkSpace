"""JSON 一键导入编辑：diff 计划的纯函数计算（契约 v1.7「JSON 一键导入编辑」）。

**本文件不写库、不调 guard、不留审计**——它只读 `repo.py` 的既有只读函数，
把「payload 对着当前库应该建/改/删什么」算成一份计划，并给这份计划盖一个
确定性的 checksum。dry-run 与 apply 都调 `build_plan`，唯一区别是 apply
之后拿**重新算出来**的 checksum 与客户端带来的那个比对——这就是"过时计划"
的判据，不需要任何服务端会话状态。

**创建/更新字段的白名单直接从 `schemas.py` 的 `*Create`/`*Update` 模型反射**
（排除 `actor`），不在这里另写一份字段名列表——`handoff.md` 记过的教训是
"加字段要同一批过完 Create/Update 两个模型"，反射复用让 import 自动跟随
那两个模型未来的任何增减，不需要有人记得同步第三份清单。

**同一批导入不支持"父子两级都新建"**：新建项目的 `zoneId`、新建任务的
`projectId` 必须指向**已存在于库中**的对象——新建的父对象在 payload 里没有
真实 id（这正是"新建"的判据本身），没有占位符解析机制去把它们串起来。
这是有意的范围收窄，不是遗漏，见契约正文「已知限制」小节。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from . import repo
from .errors import InvalidInputError
from .schemas import (
    ProjectCreate, ProjectUpdate,
    TaskCreate, TaskUpdate,
    ZoneCreate, ZoneUpdate,
)

TYPES: tuple[str, ...] = ("zones", "projects", "tasks")

_LABEL = {"zones": "分区", "projects": "项目", "tasks": "任务"}

_LIST_ALL = {
    "zones": repo.list_zones,
    "projects": repo.list_projects,
    "tasks": repo.list_tasks,
}

#: 新建对象的父引用字段（无父的类型不出现在这两张表里）。
_PARENT_FIELD = {"projects": "zoneId", "tasks": "projectId"}
_PARENT_TYPE = {"projects": "zones", "tasks": "projects"}


def _fields_of(model_cls) -> tuple[str, ...]:
    return tuple(name for name in model_cls.model_fields if name != "actor")


#: 建对象时可传的字段（与对应 `*Create` schema 的字段集合逐字同步，反射得来）。
CREATE_FIELDS: dict[str, tuple[str, ...]] = {
    "zones": _fields_of(ZoneCreate),
    "projects": _fields_of(ProjectCreate),
    "tasks": _fields_of(TaskCreate),
}
#: 改对象时可能出现在 diff 里的字段（与对应 `*Update` schema 字段集合同步）。
UPDATE_FIELDS: dict[str, tuple[str, ...]] = {
    "zones": _fields_of(ZoneUpdate),
    "projects": _fields_of(ProjectUpdate),
    "tasks": _fields_of(TaskUpdate),
}


@dataclass(frozen=True)
class PlanResult:
    """`build_plan` 的返回值——dry-run 与 apply 共用同一个结构。"""

    ops: dict[str, list[dict]]
    skipped_deletes: dict[str, list[str]]
    summary: dict[str, int]
    checksum: str
    allow_delete: bool


def _extract_id(type_: str, item: dict) -> str | None:
    raw = item.get("id")
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise InvalidInputError(
            f"{_LABEL[type_]} 的 id 必须是字符串，收到 {type(raw).__name__}"
        )
    stripped = raw.strip()
    return stripped or None


def _plan_create(type_: str, item: dict, current_by_type: dict[str, dict[str, dict]]) -> dict:
    fields = {key: item[key] for key in CREATE_FIELDS[type_] if key in item}
    parent_type = _PARENT_TYPE.get(type_)
    if parent_type is not None:
        parent_field = _PARENT_FIELD[type_]
        parent_id = fields.get(parent_field)
        if not parent_id:
            raise InvalidInputError(f"新建{_LABEL[type_]}缺少 {parent_field}")
        if parent_id not in current_by_type[parent_type]:
            raise InvalidInputError(
                f"新建{_LABEL[type_]}的 {parent_field} 引用了不存在的"
                f"{_LABEL[parent_type]}：{parent_id!r}——同一批导入不支持"
                f"「父子两级都新建」，请先单独导入建好{_LABEL[parent_type]}，"
                f"拿到真实 id 后再在下一次导入里建这个{_LABEL[type_]}"
            )
    return {"op": "create", "id": None, "fields": fields}


def _plan_update(
    type_: str, existing: dict, item: dict, current_by_type: dict[str, dict[str, dict]]
) -> dict | None:
    changed: dict = {}
    for field in UPDATE_FIELDS[type_]:
        if field not in item:
            continue
        new_value = item[field]
        if new_value != existing.get(field):
            changed[field] = new_value
    if not changed:
        return None
    parent_type = _PARENT_TYPE.get(type_)
    if parent_type is not None:
        parent_field = _PARENT_FIELD[type_]
        if parent_field in changed:
            parent_id = changed[parent_field]
            if not parent_id or parent_id not in current_by_type[parent_type]:
                raise InvalidInputError(
                    f"{_LABEL[type_]} {existing['id']!r} 改的 {parent_field} 引用了"
                    f"不存在的{_LABEL[parent_type]}：{parent_id!r}"
                )
    return {"op": "update", "id": existing["id"], "fields": changed}


def _checksum(ops: dict[str, list[dict]], allow_delete: bool) -> str:
    """计划的确定性 sha256——checksum 算法（契约「JSON 一键导入编辑」节）。

    canonical 化：`sort_keys=True` + 固定分隔符，同一份计划任何时候算出的
    字符串逐字节相同。`allowDelete` 并进被哈希的内容里——同一份 payload 换
    个 `allowDelete` 会得到不同的计划（删除条目有无），也理应得到不同的
    checksum，不需要额外记一遍这个标志位。
    """
    canonical = json.dumps(
        {"allowDelete": allow_delete, **ops},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_plan(payload: dict, *, allow_delete: bool) -> PlanResult:
    """把 `payload`（`{zones,projects,tasks}`）对着**当前**库算成一份计划。

    **纯读**：只调 `repo.list_*`/`repo.get_*` 这类只读函数，不写任何集合。
    dry-run 与 apply 调的是同一个函数——"过时计划"就是"对当前库重新跑一遍
    这个函数，checksum 变了"，不需要服务端缓存/会话来记住上一次算出的计划。
    """
    ops: dict[str, list[dict]] = {}
    skipped: dict[str, list[str]] = {}
    current_by_type: dict[str, dict[str, dict]] = {}

    for type_ in TYPES:
        items = payload.get(type_) or []
        if not isinstance(items, list):
            raise InvalidInputError(f"{type_} 必须是数组，收到 {type(items).__name__}")

        current = {doc["id"]: doc for doc in _LIST_ALL[type_]()}
        current_by_type[type_] = current

        seen_ids: set[str] = set()
        type_ops: list[dict] = []
        for item in items:
            if not isinstance(item, dict):
                raise InvalidInputError(
                    f"{type_} 数组里的元素必须是对象，收到 {type(item).__name__}"
                )
            entity_id = _extract_id(type_, item)
            if entity_id is None:
                type_ops.append(_plan_create(type_, item, current_by_type))
                continue
            if entity_id in seen_ids:
                raise InvalidInputError(f"{type_} 里 id {entity_id!r} 重复出现")
            seen_ids.add(entity_id)
            existing = current.get(entity_id)
            if existing is None:
                raise InvalidInputError(
                    f"{type_} 里的 id {entity_id!r} 在库中不存在——"
                    f"新建对象请去掉 id 字段（或设为 null），不要自己编一个"
                )
            op = _plan_update(type_, existing, item, current_by_type)
            if op is not None:
                type_ops.append(op)

        missing_ids = sorted(set(current) - seen_ids)
        if allow_delete:
            type_ops.extend({"op": "delete", "id": mid, "fields": {}} for mid in missing_ids)
            skipped[type_] = []
        else:
            skipped[type_] = missing_ids
        ops[type_] = type_ops

    summary = {
        op_name: sum(1 for t in TYPES for o in ops[t] if o["op"] == op_name)
        for op_name in ("create", "update", "delete")
    }
    checksum = _checksum(ops, allow_delete)
    return PlanResult(
        ops=ops, skipped_deletes=skipped, summary=summary, checksum=checksum,
        allow_delete=allow_delete,
    )
