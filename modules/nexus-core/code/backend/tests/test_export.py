"""只读全量导出端点的契约级单测（contract.md v1.3「只读全量导出」）。

覆盖：响应结构完整（六个顶层键）、计数与库内实际计数一致、纯只读不改库、
`events` 原样返回（不分页、不过滤，与档案读端的 1000 条上限口径不同）。
不重复测 planner CRUD / events ingest / 投影本身的既有语义——那些已经在
test_planner_crud.py / test_events_contract.py / test_gantt.py 里覆盖。
"""

from __future__ import annotations

API = "/api/core"
PLANNER = f"{API}/planner"
EXPORT = f"{API}/export"
EVENTS = f"{API}/events"

_COLLECTIONS = (
    "events", "timer_state", "proj_current", "proj_daily_stats",
    "zones", "projects", "tasks", "name_registry", "counters",
    # 2026-09-17 补：这张表漏了 planner_audit，而 conftest 那份有。
    # 漏的后果具体而隐蔽：导出**按契约是只读的**，本文件靠前后计数快照来证明这一点；
    # 表里缺一个集合，就等于那个集合上的写入**不在证明范围内** ——
    # 万一哪次改动让导出顺手写了条审计流水，这个测试照样绿。
    # 同一形状的登记表在仓里不止一份，漏的那份不会报错，只会静默少守一块。
    "planner_audit",
)


def _mk_zone(client, name="导出分区", **extra) -> dict:
    resp = client.post(f"{PLANNER}/zones", json={"name": name, **extra})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _mk_project(client, zone_id, name="导出项目", **extra) -> dict:
    resp = client.post(f"{PLANNER}/projects", json={"zoneId": zone_id, "name": name, **extra})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _mk_task(client, project_id, name="导出任务", **extra) -> dict:
    resp = client.post(f"{PLANNER}/tasks", json={"projectId": project_id, "name": name, **extra})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _post_event(client, *, dedupe_key: str, project_id: str, task_id: str) -> dict:
    doc = {
        "spec": "yq-event/v1",
        "id": f"evt_{dedupe_key}",
        "dedupeKey": dedupe_key,
        "type": "session.completed",
        "user": "u_local",
        "source": "export-test",
        "time": "2026-08-09T08:00:00+00:00",
        "subject": {"zone": "z_x", "project": project_id, "task": task_id},
        "data": {"durationSeconds": 900, "startAt": "2026-08-09T07:45:00+00:00"},
    }
    resp = client.post(EVENTS, json=doc)
    assert resp.status_code == 200 and resp.json()["accepted"] == 1, resp.text
    return doc


def _collection_counts() -> dict[str, int]:
    from app.repo import get_db  # noqa: PLC0415 —— 只在测试内按需 import，避开模块顶层 env 时序

    db = get_db()
    return {name: db[name].count_documents({}) for name in _COLLECTIONS}


# --------------------------------------------------------------- 结构


def test_export_shape_on_empty_db_has_all_top_level_keys(client):
    resp = client.get(EXPORT)
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {
        "zones", "projects", "tasks", "events", "projections", "exportedAt",
    }
    assert body["zones"] == []
    assert body["projects"] == []
    assert body["tasks"] == []
    assert body["events"] == []
    assert set(body["projections"].keys()) == {"proj_current", "proj_daily_stats"}
    assert body["projections"]["proj_current"] is None
    assert body["projections"]["proj_daily_stats"] == []
    assert isinstance(body["exportedAt"], str) and body["exportedAt"]


def test_export_exported_at_is_iso8601_with_timezone(client):
    from datetime import datetime  # noqa: PLC0415

    resp = client.get(EXPORT)
    exported_at = resp.json()["exportedAt"]
    parsed = datetime.fromisoformat(exported_at)
    assert parsed.tzinfo is not None, "exportedAt 必须带时区（服务端生成，非裸时间）"


# --------------------------------------------------------------- 计数与库一致


def test_export_counts_match_db(client):
    zone = _mk_zone(client)
    project = _mk_project(client, zone["id"])
    task_a = _mk_task(client, project["id"], name="导出任务A")
    task_b = _mk_task(client, project["id"], name="导出任务B")
    _post_event(client, dedupe_key="dk-export-1", project_id=project["id"], task_id=task_a["id"])
    _post_event(client, dedupe_key="dk-export-2", project_id=project["id"], task_id=task_b["id"])

    resp = client.get(EXPORT)
    assert resp.status_code == 200
    body = resp.json()

    counts = _collection_counts()
    assert len(body["zones"]) == counts["zones"] == 1
    assert len(body["projects"]) == counts["projects"] == 1
    assert len(body["tasks"]) == counts["tasks"] == 2
    assert len(body["events"]) == counts["events"] == 2
    assert body["projections"]["proj_current"] is not None
    assert len(body["projections"]["proj_daily_stats"]) == counts["proj_daily_stats"]

    exported_ids = {z["id"] for z in body["zones"]}
    assert exported_ids == {zone["id"]}
    exported_task_ids = {t["id"] for t in body["tasks"]}
    assert exported_task_ids == {task_a["id"], task_b["id"]}


def test_export_events_are_raw_not_paginated_or_filtered(client):
    """`events` 走 `iter_all_events`（无 1000 条上限），不是档案读端 `list_events`
    ——本用例只需确认原样字段齐全、`_id` 已剔除，不重复造 1000+ 条来撞分页上限
    （那条口径已由 `iter_all_events` 自身的既有用途——`projector.rebuild`——覆盖）。
    """
    zone = _mk_zone(client)
    project = _mk_project(client, zone["id"])
    task = _mk_task(client, project["id"])
    doc = _post_event(client, dedupe_key="dk-export-raw", project_id=project["id"], task_id=task["id"])

    resp = client.get(EXPORT)
    items = resp.json()["events"]
    assert len(items) == 1
    item = items[0]
    assert "_id" not in item
    for field in ("id", "type", "time", "subject", "data", "dedupeKey", "recordedAt"):
        assert field in item, f"事件原样导出缺字段 {field}"
    assert item["id"] == doc["id"]
    assert item["type"] == doc["type"]
    assert item["subject"] == doc["subject"]
    assert item["data"] == doc["data"]


# --------------------------------------------------------------- 只读不改库


def test_export_is_read_only(client):
    zone = _mk_zone(client)
    project = _mk_project(client, zone["id"])
    task = _mk_task(client, project["id"])
    _post_event(client, dedupe_key="dk-export-ro", project_id=project["id"], task_id=task["id"])

    before = _collection_counts()
    client.get(EXPORT)
    client.get(EXPORT)  # 调两次，确认不是「第一次只读、第二次才炸」
    after = _collection_counts()

    assert before == after, "导出端点不许改变任何集合的文档数"
