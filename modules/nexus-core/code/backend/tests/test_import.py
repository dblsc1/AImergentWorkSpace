"""JSON 一键导入编辑（契约 v1.7「JSON 一键导入编辑」）。

覆盖任务单六条验收标准：dry-run 零写入、checksum 过期拦截（真造一次「dry-run
后库被改动」的场景）、`events`/`projections` 出现即 400（反向验证去掉能过）、
不带 `allowDelete` 时缺失对象不被删、带 `allowDelete` 时按计划删且级联保护
仍生效、改出的对象 `lastWriter="human"`。另覆盖若干结构性校验（未知 id、
重复 id、跨批建父子两级、缺 checksum、AI 凭据被拒）。
"""

from __future__ import annotations

import dataclasses

import pytest

API = "/api/core"
PLANNER = f"{API}/planner"
IMPORT = f"{API}/import"
EXPORT = f"{API}/export"

AI_TOKEN = "test-only-ai-0987654321abcdef"
HEADER = "X-Nexus-Client-Token"
AI_HEAD = {HEADER: AI_TOKEN}

_OMIT = object()


def _db():
    from app.repo import get_db  # noqa: PLC0415

    return get_db()


@pytest.fixture()
def ai_token(monkeypatch):
    from app import config  # noqa: PLC0415

    patched = dataclasses.replace(config.settings, ai_client_token=AI_TOKEN)
    monkeypatch.setattr(config, "settings", patched)
    return patched


def _mk_zone(client, name="导入分区", **extra) -> dict:
    resp = client.post(f"{PLANNER}/zones", json={"name": name, **extra})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _mk_project(client, zone_id, name="导入项目", **extra) -> dict:
    resp = client.post(f"{PLANNER}/projects", json={"zoneId": zone_id, "name": name, **extra})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _mk_task(client, project_id, name="导入任务", **extra) -> dict:
    resp = client.post(f"{PLANNER}/tasks", json={"projectId": project_id, "name": name, **extra})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _import(
    client, *, zones=None, projects=None, tasks=None,
    dry_run=True, allow_delete=False, checksum=None,
    events=_OMIT, projections=_OMIT, headers=None,
):
    body: dict = {
        "zones": zones or [], "projects": projects or [], "tasks": tasks or [],
        "dryRun": dry_run, "allowDelete": allow_delete,
    }
    if checksum is not None:
        body["checksum"] = checksum
    if events is not _OMIT:
        body["events"] = events
    if projections is not _OMIT:
        body["projections"] = projections
    return client.post(IMPORT, json=body, headers=headers or {})


def _collection_counts() -> dict[str, int]:
    db = _db()
    return {
        name: db[name].count_documents({})
        for name in ("zones", "projects", "tasks", "events")
    }


def _ids(client, type_: str) -> set[str]:
    return {item["id"] for item in client.get(f"{PLANNER}/{type_}").json()}


# ------------------------------------------------------------- ① dry-run 零写入


def test_dry_run_does_not_write_db(client):
    zone = _mk_zone(client)
    project = _mk_project(client, zone["id"])
    task = _mk_task(client, project["id"])

    before_counts = _collection_counts()
    before_ids = {t: _ids(client, t) for t in ("zones", "projects", "tasks")}

    # 一份会「建+改+删」都触发的计划，dry-run 一次都不能落地。
    resp = _import(
        client,
        zones=[dict(zone, name="改了名字的分区"), {"name": "新分区（无 id）"}],
        projects=[project],
        tasks=[],  # 缺 task → 若真删会少一个
        allow_delete=True,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["dryRun"] is True
    assert body["applied"] is None
    assert body["summary"] == {"create": 1, "update": 1, "delete": 1}

    after_counts = _collection_counts()
    after_ids = {t: _ids(client, t) for t in ("zones", "projects", "tasks")}
    assert before_counts == after_counts, "dry-run 前后集合计数必须完全一致"
    assert before_ids == after_ids, "dry-run 前后 id 集合必须完全一致（一个字节都不能写）"
    assert task["id"] not in after_ids["tasks"] or task["id"] in before_ids["tasks"]  # 未被删也未新增


def test_dry_run_default_is_true_when_omitted(client):
    """`dryRun` 缺省即 `true`——不传就是只看计划。"""
    zone = _mk_zone(client)
    resp = client.post(IMPORT, json={"zones": [dict(zone, name="改名")], "projects": [], "tasks": []})
    assert resp.status_code == 200, resp.text
    assert resp.json()["dryRun"] is True
    assert client.get(f"{PLANNER}/zones/{zone['id']}") is not None
    assert _db()["zones"].find_one({"id": zone["id"]})["name"] == zone["name"], "未真的改名"


# ------------------------------------------------------------- ② checksum 过期拦截


def test_apply_with_fresh_checksum_succeeds(client):
    zone = _mk_zone(client)
    resp = _import(client, zones=[dict(zone, name="新名字")], dry_run=True)
    checksum = resp.json()["checksum"]

    apply_resp = _import(
        client, zones=[dict(zone, name="新名字")], dry_run=False, checksum=checksum,
    )
    assert apply_resp.status_code == 200, apply_resp.text
    body = apply_resp.json()
    assert body["dryRun"] is False
    assert body["plan"] is None
    assert body["applied"]["zones"] == [{"op": "update", "id": zone["id"], "fields": {"name": "新名字"}}]
    assert _db()["zones"].find_one({"id": zone["id"]})["name"] == "新名字"


def test_stale_checksum_after_db_changed_between_dry_run_and_apply_is_rejected(client):
    """真造一次「dry-run 后库被改动」的场景，验证校验和真拦得住。"""
    zone = _mk_zone(client, name="不变的分区")

    # dry-run：payload 只提到这一个分区，且和当前状态完全一致 → 空计划。
    dry = _import(client, zones=[zone], allow_delete=True)
    assert dry.status_code == 200, dry.text
    stale_checksum = dry.json()["checksum"]
    assert dry.json()["summary"] == {"create": 0, "update": 0, "delete": 0}

    # dry-run 之后，库被别的调用改动：多建了一个 payload 里完全没提到的分区。
    extra_zone = _mk_zone(client, name="dry-run之后冒出来的分区")

    # 拿着「过时」的 checksum 去 apply：allowDelete=true 下，重新算的计划会
    # 多一条「删掉 extra_zone」——两次算出的计划不同，checksum 必然不同。
    apply_resp = _import(
        client, zones=[zone], dry_run=False, allow_delete=True, checksum=stale_checksum,
    )
    assert apply_resp.status_code == 409, apply_resp.text
    assert "过期" in apply_resp.json()["detail"]

    # 反向验证它真的拦住了：两个分区都还在，一个字节没被动。
    assert _db()["zones"].count_documents({}) == 2
    assert _db()["zones"].find_one({"id": extra_zone["id"]}) is not None
    assert _db()["zones"].find_one({"id": zone["id"]}) is not None


def test_apply_without_checksum_is_rejected(client):
    zone = _mk_zone(client)
    resp = _import(client, zones=[dict(zone, name="x")], dry_run=False)
    assert resp.status_code == 400, resp.text
    assert "checksum" in resp.json()["detail"]


# --------------------------------------------------------- ③ events/projections


def test_events_field_present_is_rejected_and_named(client):
    resp = _import(client, events=[])
    assert resp.status_code == 400, resp.text
    assert "events" in resp.json()["detail"]


def test_projections_field_present_is_rejected_and_named(client):
    resp = _import(client, projections=None)  # 哪怕值是 null 也要拒
    assert resp.status_code == 400, resp.text
    assert "projections" in resp.json()["detail"]


def test_both_events_and_projections_named_together(client):
    resp = _import(client, events=[], projections={})
    assert resp.status_code == 400, resp.text
    detail = resp.json()["detail"]
    assert "events" in detail and "projections" in detail


def test_removing_events_and_projections_lets_request_through(client):
    """反向验证：去掉这两个键，同一份其余内容原样能过。"""
    zone = _mk_zone(client)
    resp = _import(client, zones=[dict(zone, name="去掉了events字段就能过")])
    assert resp.status_code == 200, resp.text
    assert resp.json()["dryRun"] is True


# -------------------------------------------------- ④ 不带 allowDelete 不删


def test_missing_object_not_deleted_without_allow_delete(client):
    zone = _mk_zone(client)
    project = _mk_project(client, zone["id"])
    task = _mk_task(client, project["id"])

    dry = _import(client, zones=[zone], projects=[project], tasks=[], allow_delete=False)
    assert dry.status_code == 200, dry.text
    plan = dry.json()
    assert plan["plan"]["tasks"] == [], "allowDelete=false 时缺失对象不进计划"
    assert plan["skippedDeletes"]["tasks"] == [task["id"]]
    assert plan["summary"] == {"create": 0, "update": 0, "delete": 0}

    apply_resp = _import(
        client, zones=[zone], projects=[project], tasks=[],
        dry_run=False, allow_delete=False, checksum=plan["checksum"],
    )
    assert apply_resp.status_code == 200, apply_resp.text
    assert apply_resp.json()["applied"] == {"zones": [], "projects": [], "tasks": []}
    assert _db()["tasks"].find_one({"id": task["id"]}) is not None, "缺省不删——任务必须还在"


# --------------------------------------------- ⑤ allowDelete=true 按计划删 + 级联保护


def test_allow_delete_true_deletes_missing_objects(client):
    zone = _mk_zone(client, name="待删分区")
    another = _mk_zone(client, name="要保留的分区")

    dry = _import(client, zones=[another], allow_delete=True)
    assert dry.status_code == 200, dry.text
    plan = dry.json()
    assert plan["plan"]["zones"] == [{"op": "delete", "id": zone["id"], "fields": {}}]

    apply_resp = _import(
        client, zones=[another], dry_run=False, allow_delete=True, checksum=plan["checksum"],
    )
    assert apply_resp.status_code == 200, apply_resp.text
    assert apply_resp.json()["applied"]["zones"] == [
        {"op": "delete", "id": zone["id"], "fields": {}}
    ]
    assert _db()["zones"].find_one({"id": zone["id"]}) is None
    assert _db()["zones"].find_one({"id": another["id"]}) is not None


def test_cascade_protection_still_enforced_through_import(client):
    """既有 409（拒绝级联）语义不因为走 import 这条路而失效。"""
    zone = _mk_zone(client)
    project = _mk_project(client, zone["id"])
    task = _mk_task(client, project["id"])

    # payload 提到 zone 与 task（保留），但没提到 project → 计划删 project；
    # 但 task 仍指着这个 project，db 里它还活着——级联保护应当拦住这次删除。
    dry = _import(client, zones=[zone], projects=[], tasks=[task], allow_delete=True)
    assert dry.status_code == 200, dry.text
    plan = dry.json()
    assert plan["plan"]["projects"] == [{"op": "delete", "id": project["id"], "fields": {}}]
    assert plan["plan"]["tasks"] == [], "task 仍在 payload 里，不是删除对象"

    apply_resp = _import(
        client, zones=[zone], projects=[], tasks=[task],
        dry_run=False, allow_delete=True, checksum=plan["checksum"],
    )
    assert apply_resp.status_code == 409, apply_resp.text
    assert "任务" in apply_resp.json()["detail"] or "项目" in apply_resp.json()["detail"]

    # 拦住之后两者都还在——被拒的那一步之前没有任何删除动作。
    assert _db()["projects"].find_one({"id": project["id"]}) is not None
    assert _db()["tasks"].find_one({"id": task["id"]}) is not None


def test_allow_delete_removes_whole_subtree_children_first(client):
    """删掉整棵子树时，子先于父的顺序让级联保护不会拦住合法的整体删除。"""
    zone = _mk_zone(client)
    project = _mk_project(client, zone["id"])
    task = _mk_task(client, project["id"])

    dry = _import(client, allow_delete=True)  # 三个数组全空 → 三者都是删除候选
    assert dry.status_code == 200, dry.text
    plan = dry.json()
    assert {op["id"] for op in plan["plan"]["zones"]} == {zone["id"]}
    assert {op["id"] for op in plan["plan"]["projects"]} == {project["id"]}
    assert {op["id"] for op in plan["plan"]["tasks"]} == {task["id"]}

    apply_resp = _import(client, dry_run=False, allow_delete=True, checksum=plan["checksum"])
    assert apply_resp.status_code == 200, apply_resp.text
    assert _db()["zones"].find_one({"id": zone["id"]}) is None
    assert _db()["projects"].find_one({"id": project["id"]}) is None
    assert _db()["tasks"].find_one({"id": task["id"]}) is None


# ------------------------------------------------------------- ⑥ lastWriter=human


def test_import_created_and_updated_objects_are_last_writer_human(client, ai_token):
    # 先用 AI 凭据建一个项目——起点 lastWriter="ai"，证明 import 会真的改写它。
    zone = _mk_zone(client)
    project_resp = client.post(
        f"{PLANNER}/projects", json={"zoneId": zone["id"], "name": "AI建的项目"},
        headers=AI_HEAD,
    )
    assert project_resp.status_code == 200, project_resp.text
    project = project_resp.json()
    assert project["lastWriter"] == "ai"

    dry = _import(
        client,
        zones=[zone],
        projects=[dict(project, name="人在编辑器里改的名字")],
        tasks=[{"projectId": project["id"], "name": "新建的任务（无 id）"}],
    )
    assert dry.status_code == 200, dry.text
    checksum = dry.json()["checksum"]

    apply_resp = _import(
        client,
        zones=[zone],
        projects=[dict(project, name="人在编辑器里改的名字")],
        tasks=[{"projectId": project["id"], "name": "新建的任务（无 id）"}],
        dry_run=False, checksum=checksum,
    )
    assert apply_resp.status_code == 200, apply_resp.text

    stored_project = _db()["projects"].find_one({"id": project["id"]})
    assert stored_project["name"] == "人在编辑器里改的名字"
    assert stored_project["lastWriter"] == "human", "import 走人的路径，改过的对象必须是 human"

    new_task = _db()["tasks"].find_one({"projectId": project["id"]})
    assert new_task is not None
    assert new_task["lastWriter"] == "human", "import 新建的对象同样是 human"


# ------------------------------------------------------------------ 其余结构性校验


def test_unknown_id_in_payload_is_rejected_and_named(client):
    resp = _import(client, zones=[{"id": "z_压根没有这个", "name": "x"}])
    assert resp.status_code == 400, resp.text
    assert "z_压根没有这个" in resp.json()["detail"]


def test_duplicate_id_in_same_array_is_rejected(client):
    zone = _mk_zone(client)
    resp = _import(client, zones=[zone, dict(zone, name="改了名字但id重复")])
    assert resp.status_code == 400, resp.text
    assert zone["id"] in resp.json()["detail"]


def test_create_project_with_unknown_zone_id_is_rejected(client):
    resp = _import(client, projects=[{"zoneId": "z_没有这个", "name": "孤儿项目"}])
    assert resp.status_code == 400, resp.text
    assert "z_没有这个" in resp.json()["detail"]


def test_cannot_create_parent_and_child_in_same_batch(client):
    """同一批导入不支持"父子两级都新建"——新项目必须引用已存在的分区。"""
    resp = _import(
        client,
        zones=[{"name": "同批新建的分区（无 id）"}],
        projects=[{"zoneId": "任意占位符", "name": "同批新建的项目"}],
    )
    assert resp.status_code == 400, resp.text
    assert "父子两级都新建" in resp.json()["detail"] or "不存在" in resp.json()["detail"]


def test_ai_credential_cannot_apply_import(client, ai_token):
    """import 走人的路径，带 AI 凭据自报仍会被 v1.6 的伪装判据拒绝——与直连
    CRUD 用的是同一条防线，import 没有另开一个更宽松的口子。"""
    zone = _mk_zone(client)

    # dry-run 本身不写库，即便带着 AI 凭据也能看一眼计划——真正的拒绝点在 apply。
    dry = _import(client, zones=[dict(zone, name="AI想改的名字")], headers=AI_HEAD)
    assert dry.status_code == 200, dry.text
    checksum = dry.json()["checksum"]

    before = _db()["zones"].find_one({"id": zone["id"]})
    apply_resp = _import(
        client, zones=[dict(zone, name="AI想改的名字")],
        dry_run=False, checksum=checksum, headers=AI_HEAD,
    )
    assert apply_resp.status_code == 403, apply_resp.text
    assert "伪装" in apply_resp.json()["detail"]
    assert _db()["zones"].find_one({"id": zone["id"]}) == before, "被拒时一个字节都不能写"
