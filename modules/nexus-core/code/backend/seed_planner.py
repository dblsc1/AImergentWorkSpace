"""一次性种子：给 planner 填最小可用数据（contract.md 明文允许 seed 脚本）。

切片 2 起**三类对象全部走系统生成路径**（create_zone / create_project / create_task）：
id 与 key 都由系统发，种子不许手编——手编就是第二套标识路径，迟早与登记表打架。

幂等：一律按「名字在父级下是否已存在」判断，存在即复用不重建。
用法（env 缺失会立即失败）：

    NEXUS_MONGO_URI=... NEXUS_DB_NAME=... .venv/bin/python seed_planner.py
"""

from __future__ import annotations

from app.modules.planner import inbox, repo, service

#: (name, color, order)
ZONES = [("示例分区二", "#4a90d9", 0)]

#: (name, zone名, 手动进度覆盖 None=computed)
PROJECTS = [
    ("示例项目三", "示例分区二", None),      # progressSource=computed
    ("示例项目二", "示例分区二", 40),        # manual，故意 ≠ 计算值（检测鉴别力）
]

#: (name, project名, 属性)
TASKS = [
    ("示例任务三", "示例项目三", {"kind": "normal", "done": False, "flags": []}),
    ("示例任务四", "示例项目三", {"kind": "normal", "done": True, "flags": []}),
    ("示例临时任务", "示例项目二", {"kind": "ephemeral", "done": False, "flags": ["no_growth"]}),
]


def seed() -> dict:
    """种数据并返回 {"zones"|"projects"|"tasks": {名字: 文档}}——
    id/key 全是系统生成的，测试从这里拿，不写死编号。

    **不在这里调 `inbox.ensure()`**：`tests/conftest.py` 的 ``seeded`` 夹具
    直接调本函数，多个既有测试对返回形状/顺序有假设（如 `test_p3_tree_shape_
    matches_contract_with_keys` 假设 `tree["projects"][0]` 一定带任务）——
    混进一个恒无任务的系统单例项目会撞碎那些假设。收件箱种子走
    `ensure_inbox()`（下面），由 `__main__` 入口或需要它的测试显式调用。
    """
    zones: dict[str, dict] = {}
    for name, color, order in ZONES:
        existing = next((z for z in repo.list_zones() if z["name"] == name), None)
        zones[name] = existing or service.create_zone(name, color=color, order=order)

    projects: dict[str, dict] = {}
    for name, zone_name, manual_progress in PROJECTS:
        zone_id = zones[zone_name]["id"]
        existing = next(
            (p for p in repo.list_projects(zone_id) if p["name"] == name), None
        )
        project = existing or service.create_project(zone_id, name)
        if manual_progress is not None and project.get("progressSource") != "manual":
            project = repo.update_by_id(
                "projects", project["id"],
                {"progress": manual_progress, "progressSource": "manual"},
            )
        projects[name] = project

    tasks: dict[str, dict] = {}
    for name, project_name, attrs in TASKS:
        project_id = projects[project_name]["id"]
        existing = next(
            (t for t in repo.list_tasks(project_id) if t["name"] == name), None
        )
        tasks[name] = existing or service.create_task(name, project_id, **attrs)

    print(f"seeded zones={len(zones)} projects={len(projects)} tasks={len(tasks)}")
    return {"zones": zones, "projects": projects, "tasks": tasks}


def ensure_inbox() -> dict:
    """well-known 收件箱幂等确保存在（契约 v1.5「收件箱」节，F-INBOX-1）。
    转发 `inbox.ensure()`——独立于 `seed()`，见上面的理由。"""
    return inbox.ensure()


if __name__ == "__main__":
    seed()
    ensure_inbox()
