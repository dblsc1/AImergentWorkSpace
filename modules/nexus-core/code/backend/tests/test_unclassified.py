"""项目的「未分类」时间桶（契约 v2.9「项目未分类时间」节）。

要害一句：**每个项目至多一个桶，它是个任务，但不是待办。** 所以最重的断言是：怎么取（重复、并发、导出再恢复）
都只有一个；任务端点动不了它；待办 / 进度里看不见它；项目的时间合计里有它。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from test_activity_suggestions import BEARER, SUG, _pending, _recent, _seg, _session_events, _upload
from test_restore import _dry_run_checksum, _restore, _wipe

API = "/api/core"
PLANNER = f"{API}/planner"


def _db():
    from app.repo import get_db  # noqa: PLC0415

    return get_db()


def _project(client, name="未分类测试项目") -> dict:
    zone = client.post(f"{PLANNER}/zones", json={"name": "未分类测试分区"}).json()
    return client.post(f"{PLANNER}/projects", json={"zoneId": zone["id"], "name": name}).json()


def _bucket(client, project_id: str, headers=None) -> str:
    resp = client.post(f"{PLANNER}/projects/{project_id}/unclassified", headers=headers or {})
    assert resp.status_code == 200, resp.text
    return resp.json()["taskId"]


def _buckets(project_id: str) -> list[dict]:
    return list(_db()["tasks"].find({"projectId": project_id, "kind": "unclassified"}, {"_id": 0}))


def _backfill(client, task_id: str, seconds: int = 600) -> None:
    resp = client.post(f"{API}/timer/backfill", json={
        "taskId": task_id, "startAt": (_recent(60)).isoformat(), "durationSeconds": seconds})
    assert resp.status_code == 200, resp.text


def _tree_project(client, project_id: str, **params) -> dict:
    tree = client.get(f"{API}/views/tree", params=params).json()
    return next(p for p in tree["projects"] if p["id"] == project_id)


# ------------------------------------------------------------------ 取或建


def test_get_or_create_is_idempotent_and_audited_once(client):
    pid = _project(client)["id"]
    assert _tree_project(client, pid)["unclassifiedTaskId"] is None  # 懒建：用到之前没有
    first = _bucket(client, pid)
    assert first == f"t_unc_{pid}" and _bucket(client, pid) == first and _bucket(client, pid, BEARER) == first
    (task,) = _buckets(pid)
    assert (task["id"], task["name"], task["done"], task["lastWriter"]) == (first, "未分类", False, "human")
    # 只有真的建的那一次留审计；之后是纯读取
    audits = list(_db()["planner_audit"].find({"objectId": first}))
    assert [(a["op"], a["outcome"], a["objectType"]) for a in audits] == [("create", "applied", "tasks")]


def test_concurrent_first_use_creates_one(client):
    pid = _project(client)["id"]
    with ThreadPoolExecutor(8) as pool:
        ids = list(pool.map(lambda _: _bucket(client, pid), range(16)))
    assert set(ids) == {f"t_unc_{pid}"} and len(_buckets(pid)) == 1


def test_unknown_project_is_404_and_writes_nothing(client):
    resp = client.post(f"{PLANNER}/projects/p_nope/unclassified")
    assert resp.status_code == 404 and "p_nope" in resp.json()["detail"]
    assert _db()["tasks"].count_documents({}) == 0


def test_ai_source_may_create_it(client, monkeypatch):
    # 建任务不是高风险写：带 AI 凭据也能建，lastWriter 如实记 ai
    import dataclasses  # noqa: PLC0415

    from app import config  # noqa: PLC0415

    token = "ai-token-for-unclassified-test-0001"
    monkeypatch.setattr(config, "settings", dataclasses.replace(config.settings, ai_client_token=token))
    pid = _project(client)["id"]
    _bucket(client, pid, {config.CLIENT_TOKEN_HEADER: token})
    assert _buckets(pid)[0]["lastWriter"] == "ai"


# ------------------------------------------------------------------ 保护


@pytest.mark.parametrize("patch", [
    {"name": "改个名"}, {"done": True}, {"kind": "normal"}, {"plannedWeight": 1},
    {"plan": {"start": "2026-10-01", "end": "2026-10-02"}}, {"flags": ["x"]},
])
def test_patch_is_409_and_changes_nothing(client, patch):
    pid = _project(client)["id"]
    tid = _bucket(client, pid)
    before = _buckets(pid)
    resp = client.patch(f"{PLANNER}/tasks/{tid}", json=patch)
    assert resp.status_code == 409 and "未分类" in resp.json()["detail"]
    assert _buckets(pid) == before


def test_move_and_delete_are_refused(client):
    pid, other = _project(client)["id"], _project(client, "另一个项目")["id"]
    tid = _bucket(client, pid)
    assert client.patch(f"{PLANNER}/tasks/{tid}", json={"projectId": other}).status_code == 409
    assert client.delete(f"{PLANNER}/tasks/{tid}").status_code == 409
    assert [t["id"] for t in _buckets(pid)] == [tid] and _buckets(other) == []


def test_kind_cannot_be_forged_and_bucket_cannot_be_depended_on(client):
    pid = _project(client)["id"]
    tid = _bucket(client, pid)
    assert client.post(f"{PLANNER}/tasks", json={"projectId": pid, "name": "假桶", "kind": "unclassified"}).status_code == 400
    task = client.post(f"{PLANNER}/tasks", json={"projectId": pid, "name": "真任务"}).json()
    assert client.patch(f"{PLANNER}/tasks/{task['id']}", json={"kind": "unclassified"}).status_code == 400
    resp = client.patch(f"{PLANNER}/tasks/{task['id']}", json={"dependsOn": [tid]})
    assert resp.status_code == 400 and "未分类" in resp.json()["detail"]
    assert len(_buckets(pid)) == 1


def test_deleted_with_its_project_but_other_tasks_still_block(client):
    pid = _project(client)["id"]
    tid = _bucket(client, pid)
    task = client.post(f"{PLANNER}/tasks", json={"projectId": pid, "name": "真任务"}).json()
    resp = client.delete(f"{PLANNER}/projects/{pid}")
    assert resp.status_code == 409 and "还有 1 个任务" in resp.json()["detail"]  # 桶不算数
    assert client.delete(f"{PLANNER}/tasks/{task['id']}").status_code == 204
    assert client.delete(f"{PLANNER}/projects/{pid}").status_code == 204
    assert _db()["tasks"].count_documents({"id": tid}) == 0


# ------------------------------------------------------------------ 读端


def test_not_a_todo_and_not_in_progress(client):
    pid = _project(client)["id"]
    done = client.post(f"{PLANNER}/tasks", json={"projectId": pid, "name": "做完的"}).json()
    client.patch(f"{PLANNER}/tasks/{done['id']}", json={"done": True})
    tid = _bucket(client, pid)

    for params in ({}, {"includeEphemeral": "true"}):
        project = _tree_project(client, pid, **params)
        assert project["unclassifiedTaskId"] == tid
        assert [t["id"] for t in project["tasks"]] == [done["id"]]
        assert project["progress"] == 100  # 桶永远不会 done，算进去项目就永远到不了 100

    actions = client.get(f"{API}/views/next-actions").json()
    assert tid not in {t["id"] for z in actions["zones"] for k in ("actionable", "waiting") for t in z[k]}
    assert tid not in {t["id"] for t in client.get(f"{API}/views/review").json()["staleTasks"]}
    # 原始列表与导出原样含它（kind 可辨）
    assert tid in {t["id"] for t in client.get(f"{PLANNER}/tasks", params={"projectId": pid}).json()}


def test_inbox_pending_count_ignores_the_bucket(client):
    from app.modules.planner import inbox  # noqa: PLC0415

    inbox.ensure()
    _bucket(client, inbox.PROJECT_ID)
    assert client.get(f"{API}/views/review").json()["inboxPendingCount"] == 0


def test_time_counts_in_project_totals(client):
    pid = _project(client)["id"]
    task = client.post(f"{PLANNER}/tasks", json={"projectId": pid, "name": "真任务"}).json()
    tid = _bucket(client, pid)
    _backfill(client, task["id"], 300)
    _backfill(client, tid, 600)

    gantt = next(p for p in client.get(f"{API}/views/gantt").json()["projects"] if p["id"] == pid)
    assert sum(d["seconds"] for d in gantt["actual"]) == 900
    rows = {t["id"]: t for t in gantt["tasks"]}
    assert rows[tid]["kind"] == "unclassified" and rows[task["id"]]["kind"] == "normal"
    assert sum(d["seconds"] for d in rows[tid]["actual"]) == 600
    review = next(p for p in client.get(f"{API}/views/review").json()["planVsActual"] if p["projectId"] == pid)
    assert review["actualSecondsThisWeek"] == 900

    assert client.post(f"{API}/timer/start", json={"taskId": tid}).status_code == 200
    current = client.get(f"{API}/views/current").json()
    assert current["project"]["totalSeconds"] == 900
    assert current["task"] == {**current["task"], "id": tid, "name": "未分类", "kind": "unclassified",
                               "totalSeconds": 600}
    client.post(f"{API}/timer/cancel")


def test_events_read_filters_by_task_id(client):
    # 「待分类」的读路径：某项目记在桶上的每一段
    pid = _project(client)["id"]
    task = client.post(f"{PLANNER}/tasks", json={"projectId": pid, "name": "真任务"}).json()
    tid = _bucket(client, pid)
    _backfill(client, task["id"], 300)
    _backfill(client, tid, 600)
    out = client.get(f"{API}/events", params={"type": "session.completed", "taskId": tid}).json()
    assert out["total"] == 1
    (event,) = out["items"]
    assert event["subject"] == {**event["subject"], "project": pid, "task": tid}
    assert event["data"]["durationSeconds"] == 600 and event["data"]["startAt"] and event["source"] and event["id"]
    assert client.get(f"{API}/events", params={"type": "session.completed"}).json()["total"] == 2
    assert client.get(f"{API}/events", params={"taskId": "t_nope"}).json() == {"total": 0, "items": []}


# ------------------------------------------------------------------ 活动建议：只指定项目


def test_confirm_with_project_records_to_the_bucket(client):
    pid = _project(client)["id"]
    _upload(client, [_seg(_recent(10)), _seg(_recent(20))])
    a, b = (i["id"] for i in _pending(client)["items"])
    out = client.post(f"{SUG}/{a}/confirm", json={"projectId": pid})
    assert out.status_code == 200, out.text
    tid = f"t_unc_{pid}"
    assert out.json()["taskId"] == tid and out.json()["duplicate"] is False
    # 设备令牌也行（同带 taskId 的确认）；第二段复用同一个桶
    assert client.post(f"{SUG}/{b}/confirm", json={"projectId": pid}, headers=BEARER).json()["taskId"] == tid
    events = _session_events()
    assert [e["subject"]["task"] for e in events] == [tid, tid] and events[0]["subject"]["project"] == pid
    assert len(_buckets(pid)) == 1 and _pending(client)["total"] == 0


@pytest.mark.parametrize("extra", [{"taskId": "t_x"}, {"proposalId": "tp_x"}, {"name": "新任务"}])
def test_confirm_project_is_exclusive_with_task_and_proposal(client, extra):
    pid = _project(client)["id"]
    _upload(client, [_seg(_recent(10))])
    sid = _pending(client)["items"][0]["id"]
    resp = client.post(f"{SUG}/{sid}/confirm", json={"projectId": pid, **extra})
    assert resp.status_code == 400 and "projectId" in resp.json()["detail"]
    assert _session_events() == [] and _buckets(pid) == [] and _pending(client)["total"] == 1


def test_confirm_unknown_project_is_404_and_stays_pending(client):
    _upload(client, [_seg(_recent(10))])
    sid = _pending(client)["items"][0]["id"]
    assert client.post(f"{SUG}/{sid}/confirm", json={"projectId": "p_nope"}).status_code == 404
    assert _session_events() == [] and _pending(client)["total"] == 1


# ------------------------------------------------------------------ 导出 / 恢复 / 导入


def test_export_restore_keeps_a_single_bucket(client):
    pid = _project(client)["id"]
    tid = _bucket(client, pid)
    _backfill(client, tid)
    before = client.get(f"{API}/export").json()
    assert [t["id"] for t in before["tasks"]] == [tid]
    _wipe()

    resp = _restore(client, before, dry_run=False, checksum=_dry_run_checksum(client, before))
    assert resp.status_code == 200, resp.text
    assert _bucket(client, pid) == tid and len(_buckets(pid)) == 1  # 恢复后取到的还是它，不另建
    after = client.get(f"{API}/export").json()
    assert after["tasks"] == before["tasks"] and after["projections"] == before["projections"]


def test_json_import_leaves_the_bucket_alone(client):
    pid = _project(client)["id"]
    tid = _bucket(client, pid)
    export = client.get(f"{API}/export").json()
    payload = {k: export[k] for k in ("zones", "projects", "tasks")}

    # 原样导回 = 没有动作；带着改过的桶也不算改
    payload["tasks"][0]["name"] = "想改名"
    plan = client.post(f"{API}/import", json=payload).json()
    assert plan["summary"] == {"create": 0, "update": 0, "delete": 0}
    # 少了它也不删、不进 skippedDeletes；allowDelete 时项目删得掉，桶随它走
    gone = {"zones": payload["zones"], "projects": [], "tasks": []}
    dry = client.post(f"{API}/import", json={**gone, "allowDelete": True}).json()
    assert dry["plan"]["tasks"] == [] and [o["op"] for o in dry["plan"]["projects"]] == ["delete"]
    done = client.post(f"{API}/import", json={**gone, "allowDelete": True, "dryRun": False,
                                              "checksum": dry["checksum"]})
    assert done.status_code == 200, done.text
    assert _db()["tasks"].count_documents({"id": tid}) == 0 and _db()["projects"].count_documents({"id": pid}) == 0
