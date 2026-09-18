"""O1：`/views/gantt` 任务层（contract.md v1.1「甘特读端」O1 子节；验收 D9）。

**扩现有 proj_daily_stats 读端，不新开投影**——不测投影/DISPATCH（那些零改动，
仍归 test_gantt.py 的既有断言管），只测本轮新增的读端塑形：project 之下嵌套
tasks[]，每个任务自带 plan/dependsOn/done/按天 actual。
"""

from __future__ import annotations

API = "/api/core"
PLANNER = f"{API}/planner"


def test_d9_gantt_project_has_tasks_layer_with_own_plan_and_depends_on(client, seeded):
    """D9a：project 层之下出现 tasks[]，每个任务自带 plan/dependsOn（不是项目层字段）。"""
    project = seeded["projects"]["示例项目三"]
    task = seeded["tasks"]["示例任务三"]

    client.patch(
        f"{PLANNER}/tasks/{task['id']}",
        json={"plan": {"start": "2026-08-01", "end": "2026-08-03"}, "dependsOn": []},
    )

    resp = client.get(f"{API}/views/gantt")
    assert resp.status_code == 200
    gp = next(p for p in resp.json()["projects"] if p["id"] == project["id"])
    assert "tasks" in gp

    gt = next(t for t in gp["tasks"] if t["id"] == task["id"])
    assert set(gt) == {"id", "key", "name", "done", "plan", "dependsOn", "actual"}
    assert gt["plan"] == {"start": "2026-08-01", "end": "2026-08-03"}
    assert gt["dependsOn"] == []
    assert gt["done"] is False


def test_d9_gantt_task_actual_is_per_task_daily_minutes(client, seeded):
    """D9b：核心断言——「任务 × 日 → 分钟」。两个任务同一天各计时不同时长，
    任务层 actual 必须能把它们分开（不是像项目层那样合并成一条）。"""
    from app.modules.projector.handlers import daily_stats

    project = seeded["projects"]["示例项目三"]
    task_a = seeded["tasks"]["示例任务三"]["id"]
    task_b = seeded["tasks"]["示例任务四"]["id"]

    def envelope(task_id: str, seconds: int, dedupe: str) -> dict:
        return {
            "spec": "yq-event/v1", "id": f"evt_{dedupe}", "dedupeKey": dedupe,
            "type": "session.completed", "user": "u_local", "source": "test",
            "time": "2026-08-05T10:00:00+00:00",
            "subject": {"zone": "z_x", "project": project["id"], "task": task_id},
            "data": {"durationSeconds": seconds, "startAt": "2026-08-05T10:00:00+00:00"},
            "flags": [],
        }

    daily_stats.handle(envelope(task_a, 600, "dk_gt_a"))
    daily_stats.handle(envelope(task_b, 900, "dk_gt_b"))

    resp = client.get(f"{API}/views/gantt")
    gp = next(p for p in resp.json()["projects"] if p["id"] == project["id"])

    # 项目层仍是全项目当天总量（既有语义不变）
    assert gp["actual"] == [{"date": "2026-08-05", "seconds": 1500}]

    gt_a = next(t for t in gp["tasks"] if t["id"] == task_a)
    gt_b = next(t for t in gp["tasks"] if t["id"] == task_b)
    assert gt_a["actual"] == [{"date": "2026-08-05", "seconds": 600}]
    assert gt_b["actual"] == [{"date": "2026-08-05", "seconds": 900}]


def test_d9_events_without_task_id_count_toward_project_not_any_task(client, seeded):
    """无具体任务的事件（B5）计入项目层总量，但不出现在任何任务的 actual 里——
    项目层求和 ≥ 全部任务层求和之和，差额正是这部分「无任务」事件。"""
    from app.modules.projector.handlers import daily_stats

    project = seeded["projects"]["示例项目三"]
    envelope = {
        "spec": "yq-event/v1", "id": "evt_notask", "dedupeKey": "dk_notask",
        "type": "session.completed", "user": "u_local", "source": "test",
        "time": "2026-08-06T10:00:00+00:00",
        "subject": {"zone": "z_x", "project": project["id"]},  # 无 task
        "data": {"durationSeconds": 300, "startAt": "2026-08-06T10:00:00+00:00"},
        "flags": [],
    }
    daily_stats.handle(envelope)

    resp = client.get(f"{API}/views/gantt")
    gp = next(p for p in resp.json()["projects"] if p["id"] == project["id"])
    assert gp["actual"] == [{"date": "2026-08-06", "seconds": 300}]
    for task in gp["tasks"]:
        assert all(row["date"] != "2026-08-06" for row in task["actual"]), (
            "无任务的事件不许出现在任何任务的 actual 里"
        )


def test_d9_task_with_no_facts_has_empty_actual_array(client, seeded):
    """没被计时过的任务：actual=[]，不是缺字段、不是 null（同项目层既有口径）。"""
    project = seeded["projects"]["示例项目二"]
    resp = client.get(f"{API}/views/gantt")
    gp = next(p for p in resp.json()["projects"] if p["id"] == project["id"])
    assert gp["tasks"], "示例项目二下应有种子任务"
    for task in gp["tasks"]:
        assert task["actual"] == []


def test_d9_from_to_filters_task_actual_same_as_project_actual(client, seeded):
    """from/to 对任务层 actual 的过滤口径与项目层一致（同一个查询参数生效）。"""
    from app.modules.projector.handlers import daily_stats

    project = seeded["projects"]["示例项目三"]
    task_id = seeded["tasks"]["示例任务三"]["id"]

    def envelope(date_str: str, dedupe: str) -> dict:
        return {
            "spec": "yq-event/v1", "id": f"evt_{dedupe}", "dedupeKey": dedupe,
            "type": "session.completed", "user": "u_local", "source": "test",
            "time": f"{date_str}T10:00:00+00:00",
            "subject": {"zone": "z_x", "project": project["id"], "task": task_id},
            "data": {"durationSeconds": 100, "startAt": f"{date_str}T10:00:00+00:00"},
            "flags": [],
        }

    daily_stats.handle(envelope("2026-08-01", "dk_ft_a"))
    daily_stats.handle(envelope("2026-08-09", "dk_ft_b"))

    filtered = client.get(f"{API}/views/gantt", params={"from": "2026-08-05", "to": "2026-08-31"})
    gp = next(p for p in filtered.json()["projects"] if p["id"] == project["id"])
    gt = next(t for t in gp["tasks"] if t["id"] == task_id)
    assert [row["date"] for row in gt["actual"]] == ["2026-08-09"]
