"""统一入口 `/api/core/planner/{type}` 的验收测试
（contract.md v0.5「统一 CRUD 入口」+ v0.6 変更记录；任务单 U1/U5/U6/U7/U8）。

**v0.6 起这是 planner 的唯一写路径**：十二条分离旧端点
（`/api/core/{zones,projects,tasks}`）已删除，统一入口不再有「旧路径」可比对。
本文件 v0.5 时期靠「同一输入打新旧两条路径、断言响应逐字节相同」证明路由归一
没有破坏校验保证；旧端点删除后这套「成对比对」的可比对象没有了，
**但它验的那件事本身依然要验**——错误消息措辞、404/409 的精确文案，
这些都是契约明文点名的保证（`modules/hive` 原样展示），必须继续留断言防回归。
所以这些用例从「新旧成对比对」改写成「统一入口本身产出 = 契约点名的精确文案」，
断言内容与力度不变，只是不再需要一个已经不存在的旧路径做对照组。

测试分三类：

1. **U1**：四个动词对三种 type 都通（`/api/core/planner/{type}` 本身）。
2. **精确文案回归**：错误消息、404/409 响应体逐字与契约点名的措辞一致
   （U5 在 v0.5 时靠成对比对验证的内容，现在直接对契约文案断言）。
3. **U6**：未知 `{type}` 返 404 并点名收到值与合法取值；且不会误吞已知 type。
4. **U7**：`zoneId`/`projectId` 查询参数经统一入口正常过滤。
"""

from __future__ import annotations

API = "/api/core"
NEW = f"{API}/planner"


def _mk_zone(client, name="示例分区一", **extra) -> dict:
    resp = client.post(f"{NEW}/zones", json={"name": name, **extra})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _mk_project(client, zone_id, name="示例项目一", **extra) -> dict:
    resp = client.post(f"{NEW}/projects", json={"zoneId": zone_id, "name": name, **extra})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _mk_task(client, project_id, name="示例任务一", **extra) -> dict:
    resp = client.post(f"{NEW}/tasks", json={"projectId": project_id, "name": name, **extra})
    assert resp.status_code == 200, resp.text
    return resp.json()


# ------------------------------------------------------------- U1：四端点 × 三类型


def test_u1_all_four_verbs_all_three_types_via_unified_entry(client):
    """U1：`/api/core/planner/{type}` 的 GET/POST/PATCH/DELETE 对三类对象都通。"""
    zone = _mk_zone(client, name="统一入口区")
    project = _mk_project(client, zone["id"], name="统一入口项目")
    task = _mk_task(client, project["id"], name="统一入口任务")

    for obj in (zone, project, task):  # 三件套齐全
        assert obj["id"] and obj["key"] and obj["name"]

    for path, params, expect_id in (
        ("zones", {}, zone["id"]),
        ("projects", {"zoneId": zone["id"]}, project["id"]),
        ("tasks", {"projectId": project["id"]}, task["id"]),
    ):
        resp = client.get(f"{NEW}/{path}", params=params)
        assert resp.status_code == 200
        assert expect_id in {o["id"] for o in resp.json()}

    assert client.patch(f"{NEW}/zones/{zone['id']}", json={"color": "#111111"}).status_code == 200
    assert client.patch(f"{NEW}/projects/{project['id']}", json={"status": "done"}).status_code == 200
    assert client.patch(f"{NEW}/tasks/{task['id']}", json={"done": True}).status_code == 200

    assert client.delete(f"{NEW}/tasks/{task['id']}").status_code == 204
    assert client.delete(f"{NEW}/projects/{project['id']}").status_code == 204
    assert client.delete(f"{NEW}/zones/{zone['id']}").status_code == 204


def test_u2_unified_entry_is_the_only_planner_write_path(client):
    """v0.6 回归：十二条旧端点必须真的不存在了（不是「返回但坏了」，是压根没有这条路由）。

    FastAPI 对未注册路径统一给 404（不是 405），这条断言证明的是「路由表里没有它」，
    不是「它存在但报错」——两者对消费方观感不同，必须是前者。
    """
    for method, path in (
        ("get", f"{API}/zones"),
        ("post", f"{API}/zones"),
        ("get", f"{API}/projects"),
        ("post", f"{API}/projects"),
        ("get", f"{API}/tasks"),
        ("post", f"{API}/tasks"),
    ):
        kwargs = {"json": {}} if method == "post" else {}
        resp = getattr(client, method)(path, **kwargs)
        assert resp.status_code == 404, f"{method} {path} 应该已经没有这条路由，实际 {resp.status_code}"


# ------------------------------------------------------------- 精确文案回归（原 U5 成对比对）


def test_error_message_exact_wording_bad_weight(client):
    """契约点名的错误消息必须逐字一致（table 原样展示，不许后端悄悄改措辞）。"""
    zone = _mk_zone(client, name="负权重区")
    resp = client.post(
        f"{NEW}/projects",
        json={"zoneId": zone["id"], "name": "负权重项目", "plannedWeight": -5.0},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "项目 的 plannedWeight 不能为负：-5.0"


def test_error_message_exact_wording_bad_kind(client):
    zone = _mk_zone(client, name="kind区")
    project = _mk_project(client, zone["id"], name="kind项目")

    resp = client.post(
        f"{NEW}/tasks", json={"projectId": project["id"], "name": "x", "kind": "不合法的kind"}
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "任务 kind 非法：'不合法的kind'，合法取值 normal/ephemeral"


def test_error_message_exact_wording_unknown_zoneid(client):
    resp = client.post(f"{NEW}/projects", json={"zoneId": "z_ghost", "name": "孤儿项目"})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "zoneId 不存在：'z_ghost'"


def test_404_not_found_exact_shape(client):
    resp = client.patch(f"{NEW}/tasks/t_ghost", json={"name": "x"})
    assert resp.status_code == 404
    assert resp.json() == {"detail": "任务不存在：'t_ghost'"}

    resp = client.delete(f"{NEW}/zones/z_ghost")
    assert resp.status_code == 404
    assert resp.json() == {"detail": "分区不存在：'z_ghost'"}


def test_409_has_children_exact_wording(client):
    """409 的「还剩 N 个」文案：抹掉 id 后与契约点名的措辞逐字相同。"""
    zone = _mk_zone(client, name="409区")
    project = _mk_project(client, zone["id"], name="409项目")
    _mk_task(client, project["id"], name="子任务1")
    _mk_task(client, project["id"], name="子任务2")

    resp = client.delete(f"{NEW}/projects/{project['id']}")
    assert resp.status_code == 409
    msg = resp.json()["detail"].replace(project["id"], "<ID>")
    assert msg == "项目 '<ID>' 下还有 2 个任务——不做级联删除，先清空再删"


def test_create_response_shape_matches_zoneout_exactly(client):
    """POST 响应字段集合恰好是 ZoneOut 的字段——不多不少（`_Out` 基类过滤内部字段）。"""
    resp = client.post(f"{NEW}/zones", json={"name": "同形区", "color": "#abcdef", "order": 5})
    assert resp.status_code == 200
    body = resp.json()
    # v1.5 起 ZoneOut 新增 lastWriter（契约「写者字段 actor/lastWriter」）——
    # 这里跟着补，不是本用例语义变了，是"恰好是 ZoneOut 的字段"这句话本身
    # 的字段集合被契约变更过。
    assert body.keys() == {"id", "key", "name", "color", "order", "lastWriter"}
    assert body["name"] == "同形区" and body["color"] == "#abcdef" and body["order"] == 5
    assert body["lastWriter"] == "human"


# ------------------------------------------------------------- U6：未知 type


def test_u6_unknown_type_404_names_offender_and_legal_values(client):
    for method, path, kwargs in (
        ("get", f"{NEW}/bogus", {}),
        ("post", f"{NEW}/bogus", {"json": {"name": "x"}}),
        ("patch", f"{NEW}/bogus/some_id", {"json": {"name": "x"}}),
        ("delete", f"{NEW}/bogus/some_id", {}),
    ):
        resp = getattr(client, method)(path, **kwargs)
        assert resp.status_code == 404, f"{method} {path} 应 404，实际 {resp.status_code}"
        detail = resp.json()["detail"]
        assert "bogus" in detail, "必须点名收到的是什么"
        assert "zones" in detail and "projects" in detail and "tasks" in detail, "必须列出合法取值"


def test_u6_unknown_type_does_not_swallow_known_types(client):
    """回归：兜底路由不许把 zones/projects/tasks 吞成「未知」——路由注册顺序对了。"""
    assert client.get(f"{NEW}/zones").status_code == 200
    assert client.get(f"{NEW}/projects").status_code == 200
    assert client.get(f"{NEW}/tasks").status_code == 200


# ------------------------------------------------------------- U7：查询参数过滤


def test_u7_query_params_filter_zoneid_and_projectid(client):
    zone_a = _mk_zone(client, name="过滤区A")
    zone_b = _mk_zone(client, name="过滤区B")
    project_a = _mk_project(client, zone_a["id"], name="过滤项目A")
    _mk_project(client, zone_b["id"], name="过滤项目B")

    resp = client.get(f"{NEW}/projects", params={"zoneId": zone_a["id"]})
    assert {p["id"] for p in resp.json()} == {project_a["id"]}

    task_a = _mk_task(client, project_a["id"], name="过滤任务A")
    resp = client.get(f"{NEW}/tasks", params={"projectId": project_a["id"]})
    assert {t["id"] for t in resp.json()} == {task_a["id"]}
