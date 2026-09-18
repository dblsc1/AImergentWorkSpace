"""planner CRUD 业务语义的契约级单测（contract.md「planner CRUD」；验收 P1–P11）。

删除拒级联返 409、id 不可变不复用、校验指名道姓——三条硬语义逐条代码化。

**v0.6 起走统一入口** ``/api/core/planner/{type}``——contract.md v0.6 已删除十二条
分离旧端点（`/api/core/{zones,projects,tasks}`），本文件原样保留全部断言，只把
调用路径从旧端点改到 `/api/core/planner/{type}`（R3：新路径覆盖不许因此变少）。
路由归一层面的回归测试（U1/U6/U7 等）在 ``test_planner_unified.py``，不在本文件重复。
"""

from __future__ import annotations

API = "/api/core"
PLANNER = f"{API}/planner"


def _mk_zone(client, name="示例分区一", **extra) -> dict:
    resp = client.post(f"{PLANNER}/zones", json={"name": name, **extra})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _mk_project(client, zone_id, name="示例项目一", **extra) -> dict:
    resp = client.post(f"{PLANNER}/projects", json={"zoneId": zone_id, "name": name, **extra})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _mk_task(client, project_id, name="示例任务一", **extra) -> dict:
    resp = client.post(f"{PLANNER}/tasks", json={"projectId": project_id, "name": name, **extra})
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_p1_p2_all_twelve_endpoints_and_triplet(client):
    """P1/P2：四动词×三类型各成功一次；GET 与 POST 响应都带 id/key/name 三件套。"""
    zone = _mk_zone(client, color="#ff9d45", order=0)
    project = _mk_project(client, zone["id"], plannedWeight=100,
                          plan={"start": "2026-08-01", "end": "2026-08-31"})
    task = _mk_task(client, project["id"], kind="normal", plannedWeight=50)

    for obj in (zone, project, task):  # POST 响应三件套
        assert obj["id"] and obj["key"] and obj["name"]

    # GET 列表 ×3（三件套照样都在）
    for path, params, expect_id in (
        ("/zones", {}, zone["id"]),
        ("/projects", {"zoneId": zone["id"]}, project["id"]),
        ("/tasks", {"projectId": project["id"]}, task["id"]),
    ):
        resp = client.get(f"{PLANNER}{path}", params=params)
        assert resp.status_code == 200
        listed = {o["id"]: o for o in resp.json()}
        assert expect_id in listed
        got = listed[expect_id]
        assert got["id"] and got["key"] and got["name"]

    # PATCH ×3
    assert client.patch(f"{PLANNER}/zones/{zone['id']}", json={"color": "#000000"}).status_code == 200
    assert client.patch(f"{PLANNER}/projects/{project['id']}", json={"status": "done"}).status_code == 200
    assert client.patch(f"{PLANNER}/tasks/{task['id']}", json={"done": True}).status_code == 200

    # DELETE ×3（叶到根，全 204 = P6 空对象可删）
    assert client.delete(f"{PLANNER}/tasks/{task['id']}").status_code == 204
    assert client.delete(f"{PLANNER}/projects/{project['id']}").status_code == 204
    assert client.delete(f"{PLANNER}/zones/{zone['id']}").status_code == 204


def test_p4_key_is_hierarchical_prefix(client):
    """P4：建分区→项目→任务，key 分别为 N、N-M、N-M-K-1（逐级前缀）。"""
    zone = _mk_zone(client, name="前缀区")
    project = _mk_project(client, zone["id"], name="前缀项目")
    task = _mk_task(client, project["id"], name="前缀任务")

    assert zone["key"].isdigit(), f"分区 key 应为单段号，实际 {zone['key']!r}"
    assert project["key"].startswith(f"{zone['key']}-"), "项目 key 前缀必须是分区号"
    assert len(project["key"].split("-")) == 2
    assert task["key"].startswith(f"{project['key']}-"), "任务 key 前缀必须是项目 key"
    assert len(task["key"].split("-")) == 4
    assert task["key"].endswith("-1")


def test_p5_p6_delete_refuses_cascade_with_count(client):
    """P5：有子对象删父 → 409 且说明还剩几个；P6：清空后可删 → 204。"""
    zone = _mk_zone(client, name="待删区")
    project = _mk_project(client, zone["id"], name="待删项目")
    _mk_task(client, project["id"], name="任务甲")
    _mk_task(client, project["id"], name="任务乙")

    resp = client.delete(f"{PLANNER}/zones/{zone['id']}")
    assert resp.status_code == 409
    assert "1" in resp.json()["detail"], "409 必须说明还剩几个项目"

    resp = client.delete(f"{PLANNER}/projects/{project['id']}")
    assert resp.status_code == 409
    assert "2" in resp.json()["detail"], "409 必须说明还剩几个任务"

    # 清空子对象后逐级可删（P6）
    for t in client.get(f"{PLANNER}/tasks", params={"projectId": project["id"]}).json():
        assert client.delete(f"{PLANNER}/tasks/{t['id']}").status_code == 204
    assert client.delete(f"{PLANNER}/projects/{project['id']}").status_code == 204
    assert client.delete(f"{PLANNER}/zones/{zone['id']}").status_code == 204


def test_p7_rename_keeps_id_recomputes_key_history_untouched(client, seeded):
    """P7：改名 → id 不变、key 变；已落库的历史事件一个字都不动。"""
    task = seeded["tasks"]["示例任务三"]
    client.post(f"{API}/timer/start", json={"taskId": task["id"]})
    client.post(f"{API}/timer/stop")
    from app.repo import get_db

    event_before = get_db()["events"].find_one({"type": "session.completed"}, {"_id": 0})

    resp = client.patch(f"{PLANNER}/tasks/{task['id']}", json={"name": "背单词"})
    assert resp.status_code == 200
    updated = resp.json()
    assert updated["id"] == task["id"], "改名不动 id"
    assert updated["name"] == "背单词"
    assert updated["key"] != task["key"], "改名 key 必须重算"

    event_after = get_db()["events"].find_one({"type": "session.completed"}, {"_id": 0})
    assert event_after == event_before, "历史事件只存 id，改名不许污染历史"
    assert event_after["subject"]["task"] == task["id"]


def test_p8_move_keeps_id_recomputes_key(client, seeded):
    """P8：换归属 → 只动 projectId，id 不变、key 重算（前缀=重算时的归属链）。"""
    task = seeded["tasks"]["示例任务三"]
    target = seeded["projects"]["示例项目二"]

    resp = client.patch(f"{PLANNER}/tasks/{task['id']}", json={"projectId": target["id"]})
    assert resp.status_code == 200
    moved = resp.json()
    assert moved["id"] == task["id"], "换归属不动 id"
    assert moved["projectId"] == target["id"]
    assert moved["key"] != task["key"], "换归属 key 必须重算"
    assert moved["key"].startswith(f"{target['key']}-"), "重算后的前缀是目标项目的 key"


def test_p9_id_never_reused_after_delete(client):
    """P9：删掉一个任务再建同名的 → 新 id ≠ 旧 id（历史事件不会张冠李戴）。"""
    zone = _mk_zone(client, name="复用试验区")
    project = _mk_project(client, zone["id"], name="复用试验项目")
    old = _mk_task(client, project["id"], name="重生任务")
    assert client.delete(f"{PLANNER}/tasks/{old['id']}").status_code == 204

    reborn = _mk_task(client, project["id"], name="重生任务")
    assert reborn["id"] != old["id"], "id 不许复用——uuid 生成，与名字无关"


def test_p10_validation_names_the_offender(client):
    """P10：校验指名道姓——错误信息必须点出是哪个 id / 哪个字段。"""
    resp = client.post(f"{PLANNER}/projects", json={"zoneId": "z_ghost", "name": "孤儿项目"})
    assert resp.status_code == 400
    assert "z_ghost" in resp.json()["detail"], "400 必须点出不存在的是哪个 id"

    resp = client.post(f"{PLANNER}/tasks", json={"projectId": "p_ghost", "name": "孤儿任务"})
    assert resp.status_code == 400
    assert "p_ghost" in resp.json()["detail"]

    zone = _mk_zone(client, name="校验区")
    resp = client.post(f"{PLANNER}/projects", json={"zoneId": zone["id"], "name": "   "})
    assert resp.status_code == 400
    assert "name" in resp.json()["detail"]

    resp = client.post(
        f"{PLANNER}/projects", json={"zoneId": zone["id"], "name": "负权重", "plannedWeight": -1}
    )
    assert resp.status_code == 400
    assert "plannedWeight" in resp.json()["detail"]

    project = _mk_project(client, zone["id"], name="kind 校验")
    resp = client.post(
        f"{PLANNER}/tasks", json={"projectId": project["id"], "name": "坏kind", "kind": "weird"}
    )
    assert resp.status_code == 400
    assert "weird" in resp.json()["detail"]

    # PATCH/DELETE 目标不存在 → 404（与 400 是两类错，不许混）
    assert client.patch(f"{PLANNER}/tasks/t_ghost", json={"name": "x"}).status_code == 404
    assert client.delete(f"{PLANNER}/zones/z_ghost").status_code == 404


def test_p11_tree_reflects_crud(client):
    """P11：建完出现在 tree，删掉后消失。"""
    zone = _mk_zone(client, name="即时区")
    project = _mk_project(client, zone["id"], name="即时项目")
    task = _mk_task(client, project["id"], name="即时任务")

    tree = client.get(f"{API}/views/tree").json()
    assert any(z["id"] == zone["id"] for z in tree["zones"])
    shaped = next(p for p in tree["projects"] if p["id"] == project["id"])
    assert any(t["id"] == task["id"] for t in shaped["tasks"])

    client.delete(f"{PLANNER}/tasks/{task['id']}")
    client.delete(f"{PLANNER}/projects/{project['id']}")
    client.delete(f"{PLANNER}/zones/{zone['id']}")

    tree = client.get(f"{API}/views/tree").json()
    assert not any(z["id"] == zone["id"] for z in tree["zones"])
    assert not any(p["id"] == project["id"] for p in tree["projects"])


def test_task_done_patch_stamps_done_at(client):
    """done→doneAt 联动：完成盖时间戳、取消完成清掉（TaskOut 含 doneAt 字段）。"""
    zone = _mk_zone(client, name="完成区")
    project = _mk_project(client, zone["id"], name="完成项目")
    task = _mk_task(client, project["id"], name="完成任务")
    assert task["doneAt"] is None

    done = client.patch(f"{PLANNER}/tasks/{task['id']}", json={"done": True}).json()
    assert done["done"] is True and done["doneAt"] is not None

    undone = client.patch(f"{PLANNER}/tasks/{task['id']}", json={"done": False}).json()
    assert undone["done"] is False and undone["doneAt"] is None


def test_g5_g6_plan_write_side_via_patch(client):
    """G5：PATCH .../projects/{id} 支持 plan（含 plan:null 清空）；
    G6：start/end 必须 YYYY-MM-DD 且 end 不得早于 start，违反 400 且消息点名字段。
    不为甘特开专用写端点——用的就是这条既有 PATCH。"""
    zone = _mk_zone(client, name="计划区")
    project = _mk_project(client, zone["id"], name="计划项目")
    assert project["plan"] is None, "未排期默认 null"

    ok = client.patch(
        f"{PLANNER}/projects/{project['id']}",
        json={"plan": {"start": "2026-08-01", "end": "2026-08-10"}},
    )
    assert ok.status_code == 200
    assert ok.json()["plan"] == {"start": "2026-08-01", "end": "2026-08-10"}

    cleared = client.patch(f"{PLANNER}/projects/{project['id']}", json={"plan": None})
    assert cleared.status_code == 200
    assert cleared.json()["plan"] is None, "plan:null 是合法的清空操作"

    bad_order = client.patch(
        f"{PLANNER}/projects/{project['id']}",
        json={"plan": {"start": "2026-08-10", "end": "2026-08-01"}},
    )
    assert bad_order.status_code == 400
    detail = bad_order.json()["detail"]
    assert "plan.end" in detail and "plan.start" in detail, "必须点名 plan.end/plan.start"

    bad_format = client.patch(
        f"{PLANNER}/projects/{project['id']}",
        json={"plan": {"start": "2026-8-1", "end": "2026-08-10"}},
    )
    assert bad_format.status_code == 400
    assert "plan.start" in bad_format.json()["detail"], "没补零的格式必须拒绝，不是宽松解析"

    bad_calendar = client.patch(
        f"{PLANNER}/projects/{project['id']}",
        json={"plan": {"start": "2026-02-30", "end": "2026-03-01"}},
    )
    assert bad_calendar.status_code == 400, "日历上不存在的日期必须拒绝"


def test_task_patch_planned_weight_success_and_rejects_negative(client):
    """任务侧 plannedWeight 补线（对称 ProjectUpdate）：PATCH 成功改权重、
    负数 400 且消息点名"任务"（与项目侧共用 `_check_weight`，owner 参数不同）；
    改权重不动其他字段（局部更新，不许顺手覆盖）。"""
    zone = _mk_zone(client, name="任务权重区")
    project = _mk_project(client, zone["id"], name="任务权重项目")
    task = _mk_task(client, project["id"], name="任务权重任务", plannedWeight=30)
    assert task["plannedWeight"] == 30.0

    ok = client.patch(f"{PLANNER}/tasks/{task['id']}", json={"plannedWeight": 75})
    assert ok.status_code == 200, ok.text
    updated = ok.json()
    assert updated["plannedWeight"] == 75.0
    assert updated["name"] == task["name"], "只改 plannedWeight，name 不受影响"
    assert updated["kind"] == task["kind"], "只改 plannedWeight，kind 不受影响"
    assert updated["done"] == task["done"], "只改 plannedWeight，done 不受影响"

    bad = client.patch(f"{PLANNER}/tasks/{task['id']}", json={"plannedWeight": -5})
    assert bad.status_code == 400
    assert bad.json()["detail"] == "任务 的 plannedWeight 不能为负：-5.0"

    # 负数校验不许把已落库的权重悄悄改掉（拒绝必须是全有全无，不是部分生效）
    unchanged = client.get(f"{PLANNER}/tasks", params={"projectId": project["id"]}).json()
    stored = next(t for t in unchanged if t["id"] == task["id"])
    assert stored["plannedWeight"] == 75.0


def test_create_project_plan_shares_same_validation(client):
    """建项目时的 plan 与 PATCH 共用同一份校验——不是两套校验各管一半。"""
    zone = _mk_zone(client, name="建时计划区")
    resp = client.post(
        f"{PLANNER}/projects",
        json={
            "zoneId": zone["id"],
            "name": "建时坏计划",
            "plan": {"start": "2026-08-10", "end": "2026-08-01"},
        },
    )
    assert resp.status_code == 400
    assert "plan.end" in resp.json()["detail"]
