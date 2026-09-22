"""快照恢复（契约 v1.9「快照恢复」）。

要害是**对称性**：`GET /export` 的输出原样喂进 `POST /restore`，空库上恢复完再
导出一次，除 `exportedAt` 外逐字段相同。其余用例守几条护栏：dry-run 零写入、
非空实例 409、checksum 两段式、引用闭包与信封校验在任何写入之前拦下、AI 来源
403，以及「名字 → 号」登记表跟着恢复（否则新建对象会和恢复进来的对象撞 key）。
"""

from __future__ import annotations

import dataclasses

import pytest

API = "/api/core"
PLANNER = f"{API}/planner"
EXPORT = f"{API}/export"
RESTORE = f"{API}/restore"
EVENTS = f"{API}/events"
AUDIT = f"{PLANNER}/audit"

AI_TOKEN = "test-only-ai-0987654321abcdef"
AI_HEAD = {"X-Nexus-Client-Token": AI_TOKEN}

#: 恢复会写到的全部集合（含登记表与审计），「零写入」按这张表判。
_COLLECTIONS = (
    "events", "timer_state", "proj_current", "proj_daily_stats",
    "zones", "projects", "tasks", "name_registry", "counters", "planner_audit",
)


def _db():
    from app.repo import get_db  # noqa: PLC0415

    return get_db()


def _counts() -> dict[str, int]:
    db = _db()
    return {name: db[name].count_documents({}) for name in _COLLECTIONS}


def _wipe() -> None:
    """模拟「换到一个新实例」。delete_many 而不是 drop：索引要留着（同 conftest）。"""
    db = _db()
    for name in _COLLECTIONS:
        db[name].delete_many({})


@pytest.fixture()
def ai_token(monkeypatch):
    from app import config  # noqa: PLC0415

    monkeypatch.setattr(config, "settings", dataclasses.replace(config.settings, ai_client_token=AI_TOKEN))


def _post(client, path: str, body: dict) -> dict:
    resp = client.post(path, json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _event(zone_id: str, project_id: str, task_id: str, dedupe_key: str, start: str) -> dict:
    return {
        "spec": "yq-event/v1", "id": f"evt_{dedupe_key}", "dedupeKey": dedupe_key,
        "type": "session.completed", "user": "u_local", "source": "restore-test",
        "time": start, "subject": {"zone": zone_id, "project": project_id, "task": task_id},
        "data": {"durationSeconds": 600, "startAt": start},
    }


def _build(client) -> dict:
    """一棵带依赖、排期、事件和两张投影的小树，返回它的导出。"""
    zone = _post(client, f"{PLANNER}/zones", {"name": "恢复分区", "color": "#123456"})
    project = _post(client, f"{PLANNER}/projects", {"zoneId": zone["id"], "name": "恢复项目"})
    first = _post(client, f"{PLANNER}/tasks", {"projectId": project["id"], "name": "前置"})
    _post(client, f"{PLANNER}/tasks", {
        "projectId": project["id"], "name": "后续", "dependsOn": [first["id"]],
        "plan": {"start": "2026-09-01", "end": "2026-09-05"},
    })
    for key, start in (("dk-r1", "2026-09-01T09:00:00+00:00"), ("dk-r2", "2026-09-02T09:00:00+00:00")):
        _post(client, EVENTS, _event(zone["id"], project["id"], first["id"], key, start))
    resp = client.get(EXPORT)
    assert resp.status_code == 200
    return resp.json()


def _restore(client, snapshot: dict, *, dry_run=True, checksum=None, headers=None):
    params: dict = {"dryRun": str(dry_run).lower()}
    if checksum is not None:
        params["checksum"] = checksum
    return client.post(RESTORE, json=snapshot, params=params, headers=headers or {})


def _dry_run_checksum(client, snapshot: dict) -> str:
    resp = _restore(client, snapshot)
    assert resp.status_code == 200, resp.text
    return resp.json()["checksum"]


# --------------------------------------------------------------- 对称性（本节要害）


def test_export_restore_export_round_trip_is_identical(client):
    before = _build(client)
    _wipe()

    checksum = _dry_run_checksum(client, before)
    resp = _restore(client, before, dry_run=False, checksum=checksum)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["summary"] == {"zones": 1, "projects": 1, "tasks": 2, "events": 2}
    assert body["rebuilt"] == {"proj_current": 2, "proj_daily_stats": 2}

    after = client.get(EXPORT).json()
    before.pop("exportedAt")
    after.pop("exportedAt")
    assert after == before


def test_restore_is_audited_as_one_high_risk_write(client):
    snapshot = _build(client)
    _wipe()
    _restore(client, snapshot, dry_run=False, checksum=_dry_run_checksum(client, snapshot))

    items = client.get(AUDIT).json()["items"]
    assert len(items) == 1
    assert items[0]["op"] == "restore"
    assert items[0]["outcome"] == "applied"
    assert items[0]["highRisk"] is True
    assert items[0]["changes"] == {"zones": 1, "projects": 1, "tasks": 2}


def test_name_registry_follows_so_new_objects_do_not_reuse_numbers(client):
    snapshot = _build(client)
    _wipe()
    _restore(client, snapshot, dry_run=False, checksum=_dry_run_checksum(client, snapshot))

    used = {int(n) for doc in snapshot["zones"] + snapshot["projects"] + snapshot["tasks"]
            for n in doc["key"].split("-")[:3]}
    fresh = _post(client, f"{PLANNER}/zones", {"name": "恢复后新建"})
    assert int(fresh["key"]) > max(used), "登记表没恢复：新名字从 1 重新发号，撞上恢复进来的 key"

    same = _post(client, f"{PLANNER}/zones", {"name": "恢复分区"})
    assert same["key"] == snapshot["zones"][0]["key"], "同名必须复用同号（号永不改）"


# --------------------------------------------------------------- 两段式


def test_dry_run_writes_nothing(client):
    snapshot = _build(client)
    _wipe()
    resp = _restore(client, snapshot)
    assert resp.status_code == 200, resp.text
    assert resp.json()["dryRun"] is True
    assert resp.json()["rebuilt"] is None
    assert not any(_counts().values())


def test_apply_without_checksum_is_400(client):
    snapshot = _build(client)
    _wipe()
    resp = _restore(client, snapshot, dry_run=False)
    assert resp.status_code == 400, resp.text
    assert "checksum" in resp.json()["detail"]
    assert not any(_counts().values())


def test_apply_with_checksum_of_another_snapshot_is_409(client):
    snapshot = _build(client)
    _wipe()
    checksum = _dry_run_checksum(client, snapshot)
    snapshot["zones"][0]["name"] = "dry-run 之后改过"
    resp = _restore(client, snapshot, dry_run=False, checksum=checksum)
    assert resp.status_code == 409, resp.text
    assert not any(_counts().values())


# --------------------------------------------------------------- 只对空实例开放


@pytest.mark.parametrize("dry_run", [True, False])
def test_non_empty_instance_is_409_restore_is_not_merge(client, dry_run):
    snapshot = _build(client)
    before = _counts()
    resp = _restore(client, snapshot, dry_run=dry_run, checksum="任意")
    assert resp.status_code == 409, resp.text
    assert "恢复通道，不是合并通道" in resp.json()["detail"]
    assert _counts() == before


def test_events_alone_make_the_instance_non_empty(client):
    snapshot = _build(client)
    for name in ("zones", "projects", "tasks"):
        _db()[name].delete_many({})
    resp = _restore(client, snapshot)
    assert resp.status_code == 409, resp.text
    assert "events 2" in resp.json()["detail"]


# --------------------------------------------------------------- 写入之前拦下


@pytest.mark.parametrize("breaks", ["zoneId", "projectId", "dependsOn"])
def test_dangling_reference_inside_snapshot_is_400(client, breaks):
    snapshot = _build(client)
    _wipe()
    if breaks == "zoneId":
        snapshot["projects"][0]["zoneId"] = "z_nowhere"
    elif breaks == "projectId":
        snapshot["tasks"][0]["projectId"] = "p_nowhere"
    else:
        snapshot["tasks"][1]["dependsOn"] = ["t_nowhere"]
    resp = _restore(client, snapshot)
    assert resp.status_code == 400, resp.text
    assert "nowhere" in resp.json()["detail"]


@pytest.mark.parametrize("field", [None, "", 42])
def test_object_without_real_id_is_400(client, field):
    snapshot = _build(client)
    _wipe()
    snapshot["tasks"][0]["id"] = field
    resp = _restore(client, snapshot)
    assert resp.status_code == 400, resp.text
    assert "tasks[0]" in resp.json()["detail"]


def test_bad_envelope_is_400_and_nothing_is_written(client):
    snapshot = _build(client)
    _wipe()
    checksum_before_break = _dry_run_checksum(client, snapshot)
    del snapshot["events"][1]["dedupeKey"]
    resp = _restore(client, snapshot, dry_run=False, checksum=checksum_before_break)
    assert resp.status_code == 400, resp.text
    assert "events[1]" in resp.json()["detail"]
    assert not any(_counts().values())


def test_duplicate_dedupe_key_inside_snapshot_is_400(client):
    snapshot = _build(client)
    _wipe()
    snapshot["events"][1]["dedupeKey"] = snapshot["events"][0]["dedupeKey"]
    resp = _restore(client, snapshot)
    assert resp.status_code == 400, resp.text
    assert "events[1]" in resp.json()["detail"]


def test_truncated_snapshot_missing_an_array_is_422(client):
    snapshot = _build(client)
    _wipe()
    del snapshot["events"]
    assert _restore(client, snapshot).status_code == 422


# --------------------------------------------------------------- 设防


def test_ai_source_is_403_and_nothing_is_written(client, ai_token):
    snapshot = _build(client)
    _wipe()
    checksum = _dry_run_checksum(client, snapshot)
    resp = _restore(client, snapshot, dry_run=False, checksum=checksum, headers=AI_HEAD)
    assert resp.status_code == 403, resp.text

    counts = _counts()
    assert counts.pop("planner_audit") == 1, "被拒的尝试本身要留痕"
    assert counts.pop("counters") == 1, "审计 seq 的发号器"
    assert not any(counts.values())
    denied = client.get(AUDIT, params={"outcome": "denied"}).json()["items"][0]
    assert denied["op"] == "restore"
