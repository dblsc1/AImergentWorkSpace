"""任务 plan/dependsOn（contract.md v1.1「排期与依赖」）：验收 D1–D8。

不重复 test_planner_crud.py 里已覆盖的既有 CRUD 语义（三件套、id 不变、
删除拒级联的通用形状），只测本轮新增的 plan/dependsOn 行为。
"""

from __future__ import annotations

API = "/api/core"
PLANNER = f"{API}/planner"


def _mk_zone(client, name="依赖测试区", **extra) -> dict:
    resp = client.post(f"{PLANNER}/zones", json={"name": name, **extra})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _mk_project(client, zone_id, name="依赖测试项目", **extra) -> dict:
    resp = client.post(f"{PLANNER}/projects", json={"zoneId": zone_id, "name": name, **extra})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _mk_task(client, project_id, name="依赖测试任务", **extra) -> dict:
    resp = client.post(f"{PLANNER}/tasks", json={"projectId": project_id, "name": name, **extra})
    assert resp.status_code == 200, resp.text
    return resp.json()

# --------------------------------------------------------------- D1：默认值与形状


def test_d1_task_out_has_plan_and_depends_on_defaults(client):
    """D1：新建任务不传 plan/dependsOn → plan=null、dependsOn=[]（不是缺字段）。"""
    zone = _mk_zone(client)
    project = _mk_project(client, zone["id"])
    task = _mk_task(client, project["id"], name="裸任务")
    assert task["plan"] is None
    assert task["dependsOn"] == []

    got = next(
        t for t in client.get(f"{PLANNER}/tasks", params={"projectId": project["id"]}).json()
        if t["id"] == task["id"]
    )
    assert got["plan"] is None and got["dependsOn"] == []


def test_d1_create_and_patch_with_plan_and_depends_on(client):
    """建任务时可直接传 plan/dependsOn；PATCH 可单独改其一，互不影响。"""
    zone = _mk_zone(client)
    project = _mk_project(client, zone["id"])
    prereq = _mk_task(client, project["id"], name="前置")
    task = _mk_task(
        client, project["id"], name="正题",
        plan={"start": "2026-08-01", "end": "2026-08-05"},
        dependsOn=[prereq["id"]],
    )
    assert task["plan"] == {"start": "2026-08-01", "end": "2026-08-05"}
    assert task["dependsOn"] == [prereq["id"]]

    # 只改 plan，dependsOn 不受影响
    patched = client.patch(
        f"{PLANNER}/tasks/{task['id']}",
        json={"plan": {"start": "2026-08-02", "end": "2026-08-06"}},
    ).json()
    assert patched["plan"] == {"start": "2026-08-02", "end": "2026-08-06"}
    assert patched["dependsOn"] == [prereq["id"]]

    # plan:null 清空
    cleared = client.patch(f"{PLANNER}/tasks/{task['id']}", json={"plan": None}).json()
    assert cleared["plan"] is None
    assert cleared["dependsOn"] == [prereq["id"]], "清 plan 不许连带清掉 dependsOn"

    # dependsOn:null 清空为 []
    no_dep = client.patch(f"{PLANNER}/tasks/{task['id']}", json={"dependsOn": None}).json()
    assert no_dep["dependsOn"] == []
    assert no_dep["plan"] is None, "清 dependsOn 不许连带把 plan 变回非空"

# --------------------------------------------------------------- D2：plan 校验


def test_d2_task_plan_end_before_start_rejected(client):
    """D2：plan.end < plan.start → 400，点名 plan.start/plan.end（复用项目那份校验）。"""
    zone = _mk_zone(client)
    project = _mk_project(client, zone["id"])
    resp = client.post(
        f"{PLANNER}/tasks",
        json={
            "projectId": project["id"], "name": "倒序计划",
            "plan": {"start": "2026-08-10", "end": "2026-08-01"},
        },
    )
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert "plan.start" in detail and "plan.end" in detail


def test_d2_task_plan_bad_format_rejected_via_patch(client):
    zone = _mk_zone(client)
    project = _mk_project(client, zone["id"])
    task = _mk_task(client, project["id"])
    resp = client.patch(
        f"{PLANNER}/tasks/{task['id']}",
        json={"plan": {"start": "2026-8-1", "end": "2026-08-10"}},
    )
    assert resp.status_code == 400
    assert "plan.start" in resp.json()["detail"]

# --------------------------------------------------------------- D3：不存在的 id


def test_d3_depends_on_nonexistent_id_rejected(client):
    """D3：dependsOn 含不存在的任务 id → 400，点名那个 id。"""
    zone = _mk_zone(client)
    project = _mk_project(client, zone["id"])
    resp = client.post(
        f"{PLANNER}/tasks",
        json={"projectId": project["id"], "name": "指鬼", "dependsOn": ["t_ghost"]},
    )
    assert resp.status_code == 400
    assert "t_ghost" in resp.json()["detail"]

    # PATCH 同样校验
    task = _mk_task(client, project["id"])
    resp = client.patch(f"{PLANNER}/tasks/{task['id']}", json={"dependsOn": ["t_ghost2"]})
    assert resp.status_code == 400
    assert "t_ghost2" in resp.json()["detail"]

# --------------------------------------------------------------- D4：自指


def test_d4_depends_on_self_rejected(client):
    """D4：dependsOn 含自身 → 400（PATCH 才可能出现——建任务时自己的 id 还不存在）。"""
    zone = _mk_zone(client)
    project = _mk_project(client, zone["id"])
    task = _mk_task(client, project["id"], name="自恋任务")

    resp = client.patch(f"{PLANNER}/tasks/{task['id']}", json={"dependsOn": [task["id"]]})
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert task["id"] in detail
    assert "自身" in detail

# --------------------------------------------------------------- D5：成环


def test_d5_two_node_cycle_rejected_with_path(client):
    """D5a：2 节点环——A dependsOn B，再把 B 改成 dependsOn A → 400 给出环路径。"""
    zone = _mk_zone(client)
    project = _mk_project(client, zone["id"])
    a = _mk_task(client, project["id"], name="A")
    b = _mk_task(client, project["id"], name="B", dependsOn=[a["id"]])

    resp = client.patch(f"{PLANNER}/tasks/{a['id']}", json={"dependsOn": [b["id"]]})
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert "成环" in detail
    assert a["id"] in detail and b["id"] in detail


def test_d5_three_node_cycle_rejected_with_path(client):
    """D5b：3+ 节点环——A→B→C，再把 C 改成 dependsOn A，成环。"""
    zone = _mk_zone(client)
    project = _mk_project(client, zone["id"])
    a = _mk_task(client, project["id"], name="A3")
    b = _mk_task(client, project["id"], name="B3", dependsOn=[a["id"]])
    c = _mk_task(client, project["id"], name="C3", dependsOn=[b["id"]])

    resp = client.patch(f"{PLANNER}/tasks/{a['id']}", json={"dependsOn": [c["id"]]})
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert "成环" in detail
    for node in (a["id"], b["id"], c["id"]):
        assert node in detail, f"环路径必须点名全部三个节点，缺 {node}"


def test_d5_longer_chain_without_cycle_is_accepted(client):
    """反向对照：A→B→C→D 是一条链不是环，必须放行——防止把「有依赖」误判成「成环」。"""
    zone = _mk_zone(client)
    project = _mk_project(client, zone["id"])
    d = _mk_task(client, project["id"], name="D4")
    c = _mk_task(client, project["id"], name="C4", dependsOn=[d["id"]])
    b = _mk_task(client, project["id"], name="B4", dependsOn=[c["id"]])
    a = client.post(
        f"{PLANNER}/tasks",
        json={"projectId": project["id"], "name": "A4", "dependsOn": [b["id"]]},
    )
    assert a.status_code == 200, a.text
    assert a.json()["dependsOn"] == [b["id"]]

# --------------------------------------------------------------- D6：跨项目依赖


def test_d6_cross_project_depends_on_allowed(client):
    """D6：dependsOn 不检查两端是否同一项目——跨项目依赖必须放行（PRD F-GANTT-4）。"""
    zone = _mk_zone(client)
    project_a = _mk_project(client, zone["id"], name="项目甲")
    project_b = _mk_project(client, zone["id"], name="项目乙")
    upstream = _mk_task(client, project_a["id"], name="上游")

    downstream = client.post(
        f"{PLANNER}/tasks",
        json={
            "projectId": project_b["id"], "name": "下游",
            "dependsOn": [upstream["id"]],
        },
    )
    assert downstream.status_code == 200, downstream.text
    assert downstream.json()["dependsOn"] == [upstream["id"]]
    assert downstream.json()["projectId"] == project_b["id"]

# --------------------------------------------------------------- D7：删除被依赖任务


def test_d7_delete_depended_task_rejected_names_dependents(client):
    """D7：删除被依赖的任务 → 409，报文列出依赖它的任务（同「拒绝级联」语义）。"""
    zone = _mk_zone(client)
    project = _mk_project(client, zone["id"])
    upstream = _mk_task(client, project["id"], name="被依赖")
    downstream = _mk_task(client, project["id"], name="依赖者", dependsOn=[upstream["id"]])

    resp = client.delete(f"{PLANNER}/tasks/{upstream['id']}")
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert downstream["id"] in detail
    assert "依赖者" in detail

    # 解除依赖后可删
    client.patch(f"{PLANNER}/tasks/{downstream['id']}", json={"dependsOn": []})
    assert client.delete(f"{PLANNER}/tasks/{upstream['id']}").status_code == 204


def test_d7_delete_task_with_multiple_dependents_names_all(client):
    """多个任务依赖同一个前置时，409 报文必须把它们都点出来，不是只报一个。"""
    zone = _mk_zone(client)
    project = _mk_project(client, zone["id"])
    upstream = _mk_task(client, project["id"], name="热门前置")
    dep1 = _mk_task(client, project["id"], name="依赖者一", dependsOn=[upstream["id"]])
    dep2 = _mk_task(client, project["id"], name="依赖者二", dependsOn=[upstream["id"]])

    resp = client.delete(f"{PLANNER}/tasks/{upstream['id']}")
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert dep1["id"] in detail and dep2["id"] in detail
    assert "2" in detail

# --------------------------------------------------------------- D8：不校验排期冲突/越界


def test_d8_overlapping_and_out_of_project_range_plans_are_accepted(client):
    """D8：排期冲突（两个依赖任务的 plan 时间倒序重叠）与越界（任务计划超出项目计划、
    项目本身无计划）均不校验、不阻止——用户裁决 F-API-3，后端不参与判断。"""
    zone = _mk_zone(client)
    project = _mk_project(client, zone["id"])  # 项目本身无 plan

    early = client.post(
        f"{PLANNER}/tasks",
        json={
            "projectId": project["id"], "name": "早",
            "plan": {"start": "2026-09-01", "end": "2026-09-10"},
        },
    )
    assert early.status_code == 200, early.text

    # 依赖“早”的任务，plan 却排在“早”之前，且完全跳出项目（本身无计划）的范围——
    # 冲突与越界同时出现，仍必须 200。
    late_but_first = client.post(
        f"{PLANNER}/tasks",
        json={
            "projectId": project["id"], "name": "晚但排更早",
            "plan": {"start": "2026-01-01", "end": "2026-01-05"},
            "dependsOn": [early.json()["id"]],
        },
    )
    assert late_but_first.status_code == 200, late_but_first.text
    assert late_but_first.json()["plan"] == {"start": "2026-01-01", "end": "2026-01-05"}


def test_d8_task_plan_can_exceed_project_plan_range(client):
    zone = _mk_zone(client)
    project = _mk_project(client, zone["id"])
    client.patch(
        f"{PLANNER}/projects/{project['id']}",
        json={"plan": {"start": "2026-08-01", "end": "2026-08-10"}},
    )
    resp = client.post(
        f"{PLANNER}/tasks",
        json={
            "projectId": project["id"], "name": "越界任务",
            "plan": {"start": "2026-07-01", "end": "2026-09-30"},
        },
    )
    assert resp.status_code == 200, resp.text
