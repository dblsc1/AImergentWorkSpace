"""GTD 待办区规则读端（contract.md v1.5「下一步行动读端」，PRD F-TODO-1）。

覆盖：可做/等待分类、过期/今天到期、排序（权重降序+今天到期/过期置顶+key
稳定序）、按 zone 分组、无 plan 默认可做、**环 fixture 不死循环**（A8d）+
反向验证。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from app.config import settings
from app.modules.views import next_actions
from app.repo import get_db
from app.timeutil import local_date

API = "/api/core"
PLANNER = f"{API}/planner"
NEXT_ACTIONS = f"{API}/views/next-actions"


def _today() -> str:
    return local_date(datetime.now(settings.tz), settings.tz)


def _mk_zone(client, name="示例分区一", **extra) -> dict:
    resp = client.post(f"{PLANNER}/zones", json={"name": name, **extra})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _mk_project(client, zone_id, name="项目", **extra) -> dict:
    resp = client.post(f"{PLANNER}/projects", json={"zoneId": zone_id, "name": name, **extra})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _mk_task(client, project_id, name="任务", **extra) -> dict:
    resp = client.post(f"{PLANNER}/tasks", json={"projectId": project_id, "name": name, **extra})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _zone_block(body: dict, zone_id: str) -> dict:
    return next(z for z in body["zones"] if z["id"] == zone_id)


# --------------------------------------------------------------- 基础分类


def test_task_with_no_deps_is_actionable(client):
    zone = _mk_zone(client)
    project = _mk_project(client, zone["id"])
    task = _mk_task(client, project["id"])

    body = client.get(NEXT_ACTIONS).json()
    zone_block = _zone_block(body, zone["id"])
    assert task["id"] in [t["id"] for t in zone_block["actionable"]]
    assert task["id"] not in [t["id"] for t in zone_block["waiting"]]


def test_task_with_undone_dependency_is_waiting_and_names_blocker(client):
    zone = _mk_zone(client)
    project = _mk_project(client, zone["id"])
    prereq = _mk_task(client, project["id"], name="前置")
    task = _mk_task(client, project["id"], name="后续", dependsOn=[prereq["id"]])

    body = client.get(NEXT_ACTIONS).json()
    zone_block = _zone_block(body, zone["id"])
    waiting_ids = [t["id"] for t in zone_block["waiting"]]
    assert task["id"] in waiting_ids
    assert task["id"] not in [t["id"] for t in zone_block["actionable"]]

    waiting_item = next(t for t in zone_block["waiting"] if t["id"] == task["id"])
    assert waiting_item["blockedBy"] == [
        {"id": prereq["id"], "key": prereq["key"], "name": "前置"}
    ]


def test_task_with_done_dependency_is_actionable(client):
    zone = _mk_zone(client)
    project = _mk_project(client, zone["id"])
    prereq = _mk_task(client, project["id"], name="前置")
    task = _mk_task(client, project["id"], name="后续", dependsOn=[prereq["id"]])
    client.patch(f"{PLANNER}/tasks/{prereq['id']}", json={"done": True})

    body = client.get(NEXT_ACTIONS).json()
    zone_block = _zone_block(body, zone["id"])
    assert task["id"] in [t["id"] for t in zone_block["actionable"]]


def test_done_task_does_not_appear_at_all(client):
    zone = _mk_zone(client)
    project = _mk_project(client, zone["id"])
    task = _mk_task(client, project["id"])
    client.patch(f"{PLANNER}/tasks/{task['id']}", json={"done": True})

    body = client.get(NEXT_ACTIONS).json()
    all_ids = {
        t["id"]
        for z in body["zones"]
        for t in z["actionable"] + z["waiting"]
    }
    assert task["id"] not in all_ids


def test_task_with_no_plan_defaults_to_actionable(client):
    """无 plan 任务默认进「可做」——GTD 无 due 也是 next action。"""
    zone = _mk_zone(client)
    project = _mk_project(client, zone["id"])
    task = _mk_task(client, project["id"])
    assert task["plan"] is None

    body = client.get(NEXT_ACTIONS).json()
    zone_block = _zone_block(body, zone["id"])
    item = next(t for t in zone_block["actionable"] if t["id"] == task["id"])
    assert item["overdue"] is False
    assert item["dueToday"] is False


# --------------------------------------------------------------- 过期/今天到期


def test_overdue_task_flagged_true(client):
    zone = _mk_zone(client)
    project = _mk_project(client, zone["id"])
    yesterday = (
        datetime.strptime(_today(), "%Y-%m-%d") - timedelta(days=1)
    ).strftime("%Y-%m-%d")
    task = _mk_task(
        client, project["id"],
        plan={"start": "2020-01-01", "end": yesterday},
    )
    body = client.get(NEXT_ACTIONS).json()
    item = next(
        t for t in _zone_block(body, zone["id"])["actionable"] if t["id"] == task["id"]
    )
    assert item["overdue"] is True
    assert item["dueToday"] is False


def test_due_today_task_flagged_true(client):
    zone = _mk_zone(client)
    project = _mk_project(client, zone["id"])
    today = _today()
    task = _mk_task(client, project["id"], plan={"start": "2020-01-01", "end": today})
    body = client.get(NEXT_ACTIONS).json()
    item = next(
        t for t in _zone_block(body, zone["id"])["actionable"] if t["id"] == task["id"]
    )
    assert item["dueToday"] is True
    assert item["overdue"] is False


def test_future_plan_is_neither_overdue_nor_due_today(client):
    zone = _mk_zone(client)
    project = _mk_project(client, zone["id"])
    tomorrow = (
        datetime.strptime(_today(), "%Y-%m-%d") + timedelta(days=1)
    ).strftime("%Y-%m-%d")
    task = _mk_task(client, project["id"], plan={"start": "2020-01-01", "end": tomorrow})
    body = client.get(NEXT_ACTIONS).json()
    item = next(
        t for t in _zone_block(body, zone["id"])["actionable"] if t["id"] == task["id"]
    )
    assert item["overdue"] is False
    assert item["dueToday"] is False


# --------------------------------------------------------------- 排序


def test_sort_by_planned_weight_descending_within_bucket(client):
    zone = _mk_zone(client)
    project = _mk_project(client, zone["id"])
    low = _mk_task(client, project["id"], name="低权重", plannedWeight=10)
    high = _mk_task(client, project["id"], name="高权重", plannedWeight=90)

    body = client.get(NEXT_ACTIONS).json()
    ids_in_order = [t["id"] for t in _zone_block(body, zone["id"])["actionable"]]
    assert ids_in_order.index(high["id"]) < ids_in_order.index(low["id"])


def test_overdue_task_placed_before_higher_weight_non_urgent_task(client):
    """排序：今天到期/过期置顶，优先级高于 plannedWeight。"""
    zone = _mk_zone(client)
    project = _mk_project(client, zone["id"])
    yesterday = (
        datetime.strptime(_today(), "%Y-%m-%d") - timedelta(days=1)
    ).strftime("%Y-%m-%d")
    overdue_low_weight = _mk_task(
        client, project["id"], name="过期低权重",
        plannedWeight=1, plan={"start": "2020-01-01", "end": yesterday},
    )
    fresh_high_weight = _mk_task(
        client, project["id"], name="不急高权重", plannedWeight=999,
    )

    body = client.get(NEXT_ACTIONS).json()
    ids_in_order = [t["id"] for t in _zone_block(body, zone["id"])["actionable"]]
    assert ids_in_order.index(overdue_low_weight["id"]) < ids_in_order.index(
        fresh_high_weight["id"]
    )


def test_same_weight_tiebreaks_by_key_stable_order(client):
    zone = _mk_zone(client)
    project = _mk_project(client, zone["id"])
    a = _mk_task(client, project["id"], name="甲", plannedWeight=50)
    b = _mk_task(client, project["id"], name="乙", plannedWeight=50)
    key_by_id = {a["id"]: a["key"], b["id"]: b["key"]}

    body = client.get(NEXT_ACTIONS).json()
    ids_in_order = [t["id"] for t in _zone_block(body, zone["id"])["actionable"]]
    relevant = [i for i in ids_in_order if i in key_by_id]
    assert relevant == sorted(relevant, key=lambda i: key_by_id[i])


# --------------------------------------------------------------- 按 zone 分组


def test_grouped_by_zone_and_empty_zones_excluded(client):
    zone_a = _mk_zone(client, name="有任务的区")
    zone_b = _mk_zone(client, name="空分区")
    project = _mk_project(client, zone_a["id"])
    _mk_task(client, project["id"])

    body = client.get(NEXT_ACTIONS).json()
    zone_ids = [z["id"] for z in body["zones"]]
    assert zone_a["id"] in zone_ids
    assert zone_b["id"] not in zone_ids, "全空的分区不该出现在响应里"


def test_today_field_matches_server_tz_date(client):
    body = client.get(NEXT_ACTIONS).json()
    assert body["today"] == _today()


# --------------------------------------------------------------- A8d：环防御


def _insert_cyclic_tasks_bypassing_validation(zone_id: str, project_id: str) -> tuple[str, str]:
    """模拟"写时校验加入之前"的历史脏数据：直接写 Mongo 造一个 A↔B 环，
    绕开 `planner/deps.py::validate_depends_on`（正常 API 路径会拒绝成环写入，
    契约「下一步行动读端」节明文这类历史脏数据是环防御要防的对象）。"""
    db = get_db()
    a_id, b_id = "t_cycle_a", "t_cycle_b"
    db["tasks"].insert_many([
        {
            "id": a_id, "key": "cyc-a", "name": "环A", "projectId": project_id,
            "done": False, "doneAt": None, "kind": "normal", "flags": [],
            "plannedWeight": 100.0, "plan": None, "dependsOn": [b_id],
            "lastWriter": "human",
        },
        {
            "id": b_id, "key": "cyc-b", "name": "环B", "projectId": project_id,
            "done": False, "doneAt": None, "kind": "normal", "flags": [],
            "plannedWeight": 100.0, "plan": None, "dependsOn": [a_id],
            "lastWriter": "human",
        },
    ])
    return a_id, b_id


def test_cycle_fixture_does_not_crash_and_downgrades_to_actionable_with_warning(client):
    zone = _mk_zone(client)
    project = _mk_project(client, zone["id"])
    a_id, b_id = _insert_cyclic_tasks_bypassing_validation(zone["id"], project["id"])

    resp = client.get(NEXT_ACTIONS)
    assert resp.status_code == 200, resp.text  # 绝不 500、绝不挂起

    zone_block = _zone_block(resp.json(), zone["id"])
    actionable_by_id = {t["id"]: t for t in zone_block["actionable"]}
    waiting_ids = {t["id"] for t in zone_block["waiting"]}

    for cid in (a_id, b_id):
        assert cid in actionable_by_id, "撞环的任务必须降级为可做，不是消失或留在等待"
        assert cid not in waiting_ids
        assert actionable_by_id[cid]["cycleWarning"] is True
        assert actionable_by_id[cid]["blockedBy"] == []


def test_self_loop_task_does_not_crash(client):
    """自环（A dependsOn A 自己）是最短的环，单独覆盖一次。"""
    zone = _mk_zone(client)
    project = _mk_project(client, zone["id"])
    db = get_db()
    db["tasks"].insert_one({
        "id": "t_self_loop", "key": "loop-1", "name": "自环", "projectId": project["id"],
        "done": False, "doneAt": None, "kind": "normal", "flags": [],
        "plannedWeight": 10.0, "plan": None, "dependsOn": ["t_self_loop"],
        "lastWriter": "human",
    })

    resp = client.get(NEXT_ACTIONS)
    assert resp.status_code == 200, resp.text
    zone_block = _zone_block(resp.json(), zone["id"])
    item = next(t for t in zone_block["actionable"] if t["id"] == "t_self_loop")
    assert item["cycleWarning"] is True


def test_three_node_cycle_all_downgraded(client):
    zone = _mk_zone(client)
    project = _mk_project(client, zone["id"])
    db = get_db()
    ids = ["t_c1", "t_c2", "t_c3"]
    deps = {"t_c1": ["t_c3"], "t_c2": ["t_c1"], "t_c3": ["t_c2"]}
    db["tasks"].insert_many([
        {
            "id": tid, "key": f"three-{i}", "name": f"三环{i}", "projectId": project["id"],
            "done": False, "doneAt": None, "kind": "normal", "flags": [],
            "plannedWeight": 10.0, "plan": None, "dependsOn": deps[tid],
            "lastWriter": "human",
        }
        for i, tid in enumerate(ids)
    ])

    resp = client.get(NEXT_ACTIONS)
    assert resp.status_code == 200, resp.text
    zone_block = _zone_block(resp.json(), zone["id"])
    actionable_ids = {t["id"] for t in zone_block["actionable"]}
    assert set(ids) <= actionable_ids


def test_reverse_validation_disabling_cycle_detection_changes_outcome(client, monkeypatch):
    """反向验证（任务单要求）：临时把环检测短路成"什么都不是环"
    （`_tasks_in_cycle` 恒返回空集合），同一个环 fixture 的分类结果必须
    **变得不同**——两个互相依赖又都未完成的任务会永远卡在"等待"互相指
    对方为 blocker（GTD 死锁：谁都排不到"可做"），证明本轮加的检测确实在
    生效，不是摆设。验完不需要复原代码（monkeypatch 自动撤销）。"""
    zone = _mk_zone(client)
    project = _mk_project(client, zone["id"])
    a_id, b_id = _insert_cyclic_tasks_bypassing_validation(zone["id"], project["id"])

    monkeypatch.setattr(next_actions, "_tasks_in_cycle", lambda graph: set())

    resp = client.get(NEXT_ACTIONS)
    assert resp.status_code == 200, resp.text  # 依然不崩（分类本身不递归）
    zone_block = _zone_block(resp.json(), zone["id"])
    waiting_ids = {t["id"] for t in zone_block["waiting"]}
    actionable_ids = {t["id"] for t in zone_block["actionable"]}

    assert a_id in waiting_ids and b_id in waiting_ids, (
        "关掉环检测后，两个互相依赖的未完成任务应双双卡进等待——"
        "这正是检测本该修正、而现在被短路掉的错误行为"
    )
    assert a_id not in actionable_ids and b_id not in actionable_ids
