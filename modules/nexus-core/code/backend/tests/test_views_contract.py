"""两条读端的契约级单测（v0.4：三类对象带 id/key/name 三件套）。

口径全部来自 contract.md：占比 0–100 一位小数、空闲态 null 不是 0、
默认过滤 ephemeral、manual 进度原样返回、computed 按已完成/总数。
对着契约核，不对着实现核——测试照着实现写就只是把 bug 抄了一遍。
"""

from __future__ import annotations

from app.config import settings

API = "/api/core"

IDLE_NULL_KEYS = ("zone", "project", "task", "sessionStartAt")


def test_health(client):
    """契约「健康检查暴露库名」v1.0（H4/H6）：db 字段存在且等于当前配置的库名——
    让 E2E 能跨进程机械核验『我打的是不是生产库』（同单测护栏同一判据形状）。"""
    resp = client.get(f"{API}/health")
    assert resp.status_code == 200
    assert resp.json() == {
        "status": "ok",
        "db": settings.db_name,
        # v1.6：设防姿态跨进程可判（同 db 字段的理由，见契约「健康检查暴露库名」）。
        "actorGuard": settings.actor_guard,
    }


def test_idle_current_is_null_not_zero(client):
    """空闲态：running=false，四个键**都在**且都是 null——0 会被圆环画成零弧。"""
    body = client.get(f"{API}/views/current").json()
    assert body["running"] is False
    for key in IDLE_NULL_KEYS:
        assert key in body, f"键 {key} 不许消失——键消失 ≠ 值为 null"
        assert body[key] is None, f"{key} 期望 null，实际 {body[key]!r}"


def test_r9_p3_running_current_reads_projection_names_and_keys(client, seeded):
    """R9/P3 金链路读端：真会话后 current 返回真名字、真累计、0–100 占比，
    且三类对象都带 `key`（v0.4 补齐）。"""
    zone = seeded["zones"]["示例分区二"]
    project = seeded["projects"]["示例项目三"]
    task = seeded["tasks"]["示例任务三"]
    client.post(f"{API}/timer/start", json={"taskId": task["id"]})
    client.post(f"{API}/timer/stop")
    client.post(f"{API}/timer/start", json={"taskId": task["id"]})

    body = client.get(f"{API}/views/current").json()
    assert body["running"] is True
    assert body["zone"] == {"id": zone["id"], "key": zone["key"], "name": "示例分区二"}
    assert body["project"]["id"] == project["id"] and body["project"]["name"] == "示例项目三"
    assert body["project"]["key"] == project["key"]
    assert body["task"]["id"] == task["id"] and body["task"]["name"] == "示例任务三"
    assert body["task"]["key"] == task["key"]
    assert body["sessionStartAt"] is not None

    assert body["project"]["totalSeconds"] >= 1, "totalSeconds 来自 proj_current，不是假数据"
    for node, field in (("project", "shareOfPlan"), ("task", "shareOfProject")):
        value = body[node][field]
        assert isinstance(value, (int, float))
        assert 0 <= value <= 100
        assert not (0 < value < 1), f"{field}={value} 落在 (0,1)——疑似 0–1 小数，契约要求百分数"
    # 只有一个项目/一个任务在计：占比应为满盘 100
    assert body["project"]["shareOfPlan"] == 100.0
    assert body["task"]["shareOfProject"] == 100.0


def test_tree_filters_ephemeral_by_default(client, seeded):
    default = client.get(f"{API}/views/tree").json()
    full = client.get(f"{API}/views/tree", params={"includeEphemeral": "true"}).json()

    default_kinds = [t["kind"] for p in default["projects"] for t in p["tasks"]]
    assert "ephemeral" not in default_kinds
    full_ids = [t["id"] for p in full["projects"] for t in p["tasks"]]
    assert seeded["tasks"]["示例临时任务"]["id"] in full_ids, "includeEphemeral=true 时临时任务必须出现"

    assert [p["id"] for p in default["projects"]] == [p["id"] for p in full["projects"]], (
        "过滤只影响 tasks，不影响 project 是否出现"
    )


def test_tree_progress_semantics(client, seeded):
    """manual 原样返回、不随过滤重算；computed = round(已完成/总数*100) 按全量任务。"""
    p_computed = seeded["projects"]["示例项目三"]["id"]
    p_manual = seeded["projects"]["示例项目二"]["id"]
    default = client.get(f"{API}/views/tree").json()
    full = client.get(f"{API}/views/tree", params={"includeEphemeral": "true"}).json()

    def project(tree: dict, pid: str) -> dict:
        return next(p for p in tree["projects"] if p["id"] == pid)

    # 示例项目二 manual=40（种子里故意 ≠ 计算值），两种视图都原样返回
    assert project(default, p_manual)["progress"] == 40
    assert project(full, p_manual)["progress"] == 40
    # 示例项目三 computed：单词未完成、口语完成 → round(1/2*100) = 50，不随过滤漂移
    assert project(default, p_computed)["progress"] == 50
    assert project(full, p_computed)["progress"] == 50


def test_p3_tree_shape_matches_contract_with_keys(client, seeded):
    """P3 + 字段集无缺无多：三类对象都带 key（M5 语义在单测层的最小保障）。"""
    tree = client.get(f"{API}/views/tree", params={"includeEphemeral": "true"}).json()
    assert set(tree) == {"zones", "projects"}
    assert set(tree["zones"][0]) == {"id", "key", "name", "color", "order"}
    project = tree["projects"][0]
    assert set(project) == {
        "id", "key", "zoneId", "name", "status", "progress", "progressSource",
        "deadline", "tasks",
    }
    assert set(project["tasks"][0]) == {
        "id", "key", "name", "done", "kind", "flags", "plan", "dependsOn",
    }  # plan/dependsOn 是 v1.2 补的集成缝（只读附带，与 TaskOut 同形状）
    # key 是真值不是占位：与库里系统生成的一致
    zone = seeded["zones"]["示例分区二"]
    assert tree["zones"][0]["key"] == zone["key"]


def test_tree_task_plan_and_depends_on_defaults_and_values(client, seeded):
    """契约 v1.2「views/tree 补集成缝」T1–T3：任务节点带 plan/dependsOn；
    未排期/无依赖时是 null/[]（不是缺字段、不是 undefined）；写入后 tree 读出
    的值与 planner 写入的值一致——这是 table 控件 feature-detect 依赖的真实数据源。
    """
    task_a = seeded["tasks"]["示例任务三"]
    task_b = seeded["tasks"]["示例任务四"]

    # T2：种子任务未设置 plan/dependsOn → tree 里必须是 null/[]
    tree = client.get(f"{API}/views/tree").json()
    project = next(
        p for p in tree["projects"] if p["id"] == seeded["projects"]["示例项目三"]["id"]
    )
    bare = next(t for t in project["tasks"] if t["id"] == task_a["id"])
    assert bare["plan"] is None
    assert bare["dependsOn"] == []

    # T3：通过 planner 写入后，tree 读出的值必须与写入的值一致（同一份数据，不是
    # tree 自己另算了一遍）
    plan = {"start": "2026-08-01", "end": "2026-08-05"}
    client.patch(
        f"{API}/planner/tasks/{task_a['id']}",
        json={"plan": plan, "dependsOn": [task_b["id"]]},
    )
    tree_after = client.get(f"{API}/views/tree").json()
    project_after = next(
        p for p in tree_after["projects"] if p["id"] == seeded["projects"]["示例项目三"]["id"]
    )
    updated = next(t for t in project_after["tasks"] if t["id"] == task_a["id"])
    assert updated["plan"] == plan
    assert updated["dependsOn"] == [task_b["id"]]

    # 没被改过的任务仍是默认值——不许因为改了一个任务就串味到别的任务
    unaffected = next(t for t in project_after["tasks"] if t["id"] == task_b["id"])
    assert unaffected["plan"] is None
    assert unaffected["dependsOn"] == []
