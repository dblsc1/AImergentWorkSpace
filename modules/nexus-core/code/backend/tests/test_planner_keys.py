"""标识三分与 key 生成的单测（J10；契约「标识三分与 `key` 生成」节；验收 R6–R8）。

三样各管各的：id 永不变且唯一约束压它；key 四段、登记表制、不做唯一约束；
name 用户输入什么就是什么。
"""

from __future__ import annotations

import pytest

from app.modules.planner import service


def _indexes(collection: str) -> dict:
    from app.repo import get_db

    return get_db()[collection].index_information()


def test_r6_same_name_same_project_serial_increments(seeded):
    """R6：同一项目下建两个同名任务 → key 尾号 -1/-2，两者 id 不同。"""
    first = service.create_task("示例任务一", seeded["projects"]["示例项目三"]["id"])
    second = service.create_task("示例任务一", seeded["projects"]["示例项目三"]["id"])

    assert first["id"] != second["id"], "id 是唯一标识，同名也必须各自有 id"
    assert first["key"].endswith("-1") and second["key"].endswith("-2")
    prefix = first["key"].rsplit("-", 1)[0]
    assert second["key"].rsplit("-", 1)[0] == prefix, (
        "同项目同名：前三段（分区号-项目号-名字号）必须相同，只差同名序号"
    )


def test_r7_unique_index_on_id_none_on_key(seeded):
    """R7：唯一索引建在 id 上；key 无任何唯一索引（搬移重算期可短暂重复）。"""
    service.create_task("示例任务一", seeded["projects"]["示例项目三"]["id"])
    info = _indexes("tasks")

    id_unique = [
        spec for spec in info.values()
        if spec.get("unique") and [k for k, _ in spec["key"]] == ["id"]
    ]
    assert id_unique, f"tasks 必须有 id 唯一索引，实际索引：{list(info)}"

    key_indexed = [
        name for name, spec in info.items()
        if any(field == "key" for field, _ in spec["key"])
    ]
    assert not key_indexed, f"key 不许建索引（更不许唯一），实际发现：{key_indexed}"


def test_r8_chinese_name_stored_verbatim_no_transliteration(seeded):
    """R8：中文名原样存——name 就是那几个字；key 全是登记表的号，无音译无转码。"""
    task = service.create_task("示例任务一", seeded["projects"]["示例项目三"]["id"])

    assert task["name"] == "示例任务一"
    segments = task["key"].split("-")
    assert len(segments) == 4, f"key 应为四段，实际 {task['key']!r}"
    assert all(seg.isdigit() for seg in segments), (
        f"key 每段都是登记表发的号（纯数字），出现别的东西即疑似音译/转码：{task['key']!r}"
    )
    # 反塌陷实证：同音不同字必须拿到不同的名字号（音译方案会把它们撞成一个）
    ta_tower = service.create_task("塔", seeded["projects"]["示例项目三"]["id"])
    ta_she = service.create_task("她", seeded["projects"]["示例项目三"]["id"])
    assert ta_tower["key"].split("-")[2] != ta_she["key"].split("-")[2], (
        "塔/她 同音不同名，名字号必须不同——相同即发生了音译塌陷"
    )


def test_name_registry_reuses_number_across_projects(seeded):
    """登记表语义：同名复用同号（跨项目），同名序号只在项目内计数。"""
    in_p1 = service.create_task("示例任务一", seeded["projects"]["示例项目三"]["id"])
    in_p2 = service.create_task("示例任务一", seeded["projects"]["示例项目二"]["id"])

    assert in_p1["key"].split("-")[2] == in_p2["key"].split("-")[2], (
        "同一个名字在登记表里只有一个号，跨项目复用"
    )
    assert in_p1["key"].endswith("-1") and in_p2["key"].endswith("-2") is False, (
        "同名序号按「同一项目下」计数：各自项目里都是第一个，都应以 -1 结尾"
    )
    assert in_p2["key"].endswith("-1")
    # 但分区/项目段不同项目自然不同（p_1 vs p_2 的项目号不同）
    assert in_p1["key"].split("-")[1] != in_p2["key"].split("-")[1]


def test_create_task_only_needs_name_and_project(seeded):
    """契约「建任务时」：调用方只提供 name 与 projectId，id/key 系统生成。"""
    task = service.create_task("新任务", seeded["projects"]["示例项目三"]["id"])
    assert task["id"].startswith("t_") and task["key"]
    assert task["done"] is False and task["kind"] == "normal" and task["flags"] == []


def test_create_task_in_unknown_project_fails_loud(seeded):
    with pytest.raises(service.UnknownProjectError, match="p_ghost"):
        service.create_task("孤儿任务", "p_ghost")
