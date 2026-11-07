"""代理去向（契约 hive.stage.v1「三」，nexus-core v2.44）：place、place-rules、agents/start 查表、投影重算。"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta
from urllib.parse import quote

import pytest

from app import config

API = "/api/core"
AGENTS = f"{API}/agents"
RULES = f"{AGENTS}/place-rules"
BEARER = {"Authorization": "Bearer tok_device"}
AI = {config.CLIENT_TOKEN_HEADER: "ai-secret"}
B = {"X-Nexus-Tenant": "ch_bbbb"}


def _db():
    from app.repo import get_db  # noqa: PLC0415

    return get_db()


@pytest.fixture()
def ai_token(monkeypatch):
    monkeypatch.setattr(config, "settings", dataclasses.replace(config.settings, ai_client_token="ai-secret"))


@pytest.fixture()
def projects(client):
    zone = client.post(f"{API}/planner/zones", json={"name": "去向区"}).json()["id"]
    return [client.post(f"{API}/planner/projects", json={"zoneId": zone, "name": n}).json()["id"] for n in ("P", "Q")]


def _task(client, project):
    r = client.post(f"{API}/planner/tasks", json={"projectId": project, "name": "t"})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _start(client, agent="garden", headers=None, **body):
    r = client.post(f"{AGENTS}/start", json={"agent": agent, "tool": "x", **body}, headers=headers or {})
    assert r.status_code == 201, r.text
    return r.json()["runId"]


def _place(client, run_id, headers=None, **body):
    return client.post(f"{AGENTS}/{run_id}/place", json=body, headers=headers or {})


def _last_open(client):
    return client.get(f"{API}/views/agent-time").json()["open"][-1]


def _slot():
    return datetime.now(config.settings.tz).replace(hour=10, minute=0, second=0, microsecond=0) - timedelta(days=1)


def _finished(client, run_id="run_fin", agent="garden", seconds=1800, subject=None):
    start = _slot()
    env = {"spec": "yq-event/v1", "id": f"evt_{run_id}", "dedupeKey": f"agent:{run_id}", "type": "agent.run.completed",
           "source": "agent-hook", "user": "u_local", "time": (start + timedelta(seconds=seconds)).isoformat(),
           "subject": subject or {"zone": "z_inbox", "project": "p_inbox"},
           "data": {"agent": agent, "tool": "x", "startAt": start.isoformat(), "durationSeconds": seconds,
                    "outcome": "done", "workingSeconds": seconds,
                    "days": [{"date": start.date().isoformat(), "seconds": seconds}]}}
    r = client.post(f"{API}/events", json=env)
    assert r.json()["accepted"] == 1, r.text
    return run_id


def _stats(client):
    body = client.get(f"{API}/views/agent-time").json()
    return {t["projectId"]: t["seconds"] for t in body["tasks"]}, body["runs"]


def _lane_project(client, run_id):
    runs = client.get(f"{API}/views/lanes", params={"date": _slot().date().isoformat()}).json()["agents"]
    return next(r["projectId"] for r in runs if r["runId"] == run_id)


def test_place_project_with_rule_default_remember(client, projects):
    p, _ = projects
    run = _start(client)
    r = _place(client, run, projectId=p)
    assert r.status_code == 200, r.text
    assert r.json() == {"runId": run, "taskId": None, "projectId": p, "rule": {"agent": "garden", "projectId": p}}
    assert client.get(RULES).json()["items"][0]["projectId"] == p
    assert _last_open(client)["projectId"] == p


def test_place_task_returns_its_project_and_rule_has_task(client, projects):
    p, _ = projects
    t = _task(client, p)
    body = _place(client, _start(client), taskId=t).json()
    assert body["taskId"] == t and body["projectId"] == p and body["rule"] == {"agent": "garden", "taskId": t}
    assert _db()["agent_place_rules"].find_one({"agent": "garden"})["taskId"] == t


def test_remember_false_writes_no_rule(client, projects):
    body = _place(client, _start(client), projectId=projects[0], remember=False).json()
    assert body["rule"] is None
    assert _db()["agent_place_rules"].count_documents({}) == 0


def test_replace_rule_keeps_one_row(client, projects):
    p, q = projects
    _place(client, _start(client), projectId=p)
    _place(client, _start(client), projectId=q)
    rows = list(_db()["agent_place_rules"].find({"agent": "garden"}))
    assert len(rows) == 1 and rows[0]["projectId"] == q


@pytest.mark.parametrize("body", [{}, {"taskId": "t", "projectId": "p"}, {"projectId": "p_inbox", "remember": "yes"}])
def test_bad_body_422(client, body):
    assert _place(client, _start(client), **body).status_code == 422


def test_missing_run_and_target_404(client, projects):
    assert _place(client, "run_nope", projectId=projects[0]).status_code == 404
    run = _start(client)
    assert _place(client, run, projectId="p_nope").status_code == 404
    assert _place(client, run, taskId="t_nope").status_code == 404
    assert _db()["agent_place_rules"].count_documents({}) == 0
    assert _db()["events"].count_documents({"type": "agent.run.placed"}) == 0


def test_only_human_can_place(client, projects, ai_token):
    run = _start(client)
    assert _place(client, run, headers=BEARER, projectId=projects[0]).status_code == 403
    assert _place(client, run, headers=AI, projectId=projects[0]).status_code == 403
    assert _db()["events"].count_documents({"type": "agent.run.placed"}) == 0


def test_public_ingest_cannot_forge_placement(client):
    env = {"spec": "yq-event/v1", "id": "evt_f", "dedupeKey": "place:run_x:1", "type": "agent.run.placed",
           "source": "agent-place", "user": "u_local", "time": "2026-01-01T00:00:00+00:00",
           "subject": {"zone": "z", "project": "p"}, "data": {"runId": "run_x", "seq": 1}}
    assert client.post(f"{API}/events", json=env).json()["rejected"]


def test_finished_run_whole_duration_moves_and_rebuild_agrees(client, projects):
    p, q = projects
    run = _finished(client)
    assert _stats(client)[0] == {"p_inbox": 1800}
    assert _place(client, run, projectId=p, remember=False).status_code == 200
    assert _stats(client) == ({p: 1800}, 1)
    assert _lane_project(client, run) == p
    assert _place(client, run, projectId=q, remember=False).status_code == 200
    assert _stats(client) == ({q: 1800}, 1)
    assert _lane_project(client, run) == q
    assert _db()["events"].count_documents({"type": "agent.run.placed"}) == 2
    from app.modules.projector.rebuild import rebuild  # noqa: PLC0415

    rebuild()
    assert _stats(client) == ({q: 1800}, 1)
    assert _lane_project(client, run) == q


def test_rule_cap_500(client, projects):
    p, _ = projects
    _db()["agent_place_rules"].insert_many(
        [{"user": "u_local", "agent": f"a{i}", "projectId": p, "createdAt": "x"} for i in range(500)])
    run = _finished(client, agent="new")
    assert _place(client, run, projectId=p).status_code == 422
    assert _db()["events"].count_documents({"type": "agent.run.placed"}) == 0
    assert _db()["agent_place_rules"].count_documents({}) == 500
    assert _place(client, _finished(client, "run_b", agent="a7"), projectId=p).status_code == 200
    assert _place(client, run, projectId=p, remember=False).status_code == 200
    assert _db()["agent_place_rules"].count_documents({}) == 500


def test_empty_agent_name_cannot_be_remembered(client, projects):
    run = _finished(client, agent="")
    assert _place(client, run, projectId=projects[0]).status_code == 422
    assert _place(client, run, projectId=projects[0], remember=False).status_code == 200


def test_start_uses_rule_exact_agent_only(client, projects):
    p, q = projects
    t = _task(client, p)
    _place(client, _start(client), projectId=p)
    _start(client)
    assert _last_open(client)["projectId"] == p
    for other in ("Garden", "garden-2", "gard"):
        _start(client, agent=other)
        assert _last_open(client)["projectId"] == "p_inbox"
    _start(client, projectId=q)
    assert _last_open(client)["projectId"] == q
    _start(client, taskId=t)
    assert _last_open(client)["taskId"] == t


def test_start_with_task_rule_and_deleted_target(client, projects):
    p, _ = projects
    t = _task(client, p)
    _place(client, _start(client), taskId=t)
    _start(client)
    assert _last_open(client)["taskId"] == t
    _db()["tasks"].delete_many({"id": t})
    _start(client)
    assert _last_open(client)["taskId"] is None and _last_open(client)["projectId"] == "p_inbox"


def test_no_rule_old_behaviour(client):
    _start(client)
    assert _last_open(client)["projectId"] == "p_inbox"


def test_list_rules_human_and_mcp_not_device(client, projects, ai_token):
    p, _ = projects
    _place(client, _start(client, agent="a"), projectId=p)
    _place(client, _start(client, agent="b"), projectId=p)
    items = client.get(RULES).json()["items"]
    assert {i["agent"] for i in items} == {"a", "b"}
    assert set(items[0]) == {"agent", "taskId", "projectId", "createdAt", "broken"} and items[0]["broken"] is False
    assert client.get(RULES, headers=AI).status_code == 200
    assert client.get(RULES, headers=BEARER).status_code == 403
    _db()["projects"].delete_many({"id": p})
    assert all(i["broken"] for i in client.get(RULES).json()["items"])


def test_delete_rule(client, projects, ai_token):
    p, _ = projects
    _place(client, _start(client), projectId=p)
    assert client.delete(f"{RULES}/garden", headers=BEARER).status_code == 403
    assert client.delete(f"{RULES}/garden", headers=AI).status_code == 403
    assert client.delete(f"{RULES}/garden").status_code in (200, 204)
    assert client.get(RULES).json()["items"] == []
    assert client.delete(f"{RULES}/garden").status_code == 404
    assert _last_open(client)["projectId"] == p


def test_odd_agent_names_url_encoded(client, projects):
    p, _ = projects
    for name in ("my agent", "a/b", "花园"):
        _place(client, _start(client, agent=name), projectId=p)
        assert client.delete(f"{RULES}/{quote(name, safe='')}").status_code in (200, 204), name
    assert client.get(RULES).json()["items"] == []


def test_export_restore_and_tenant_isolation(client, projects):
    p, _ = projects
    for name in ("a", "b", "c"):
        _place(client, _start(client, agent=name), projectId=p)
    assert client.get(RULES, headers=B).json()["items"] == []
    snap = client.get(f"{API}/export").json()
    assert {r["agent"] for r in snap["agentPlaceRules"]} == {"a", "b", "c"}
    before = client.get(RULES).json()["items"]
    for name in _db().list_collection_names():
        _db()[name].delete_many({})
    dry = client.post(f"{API}/restore", json=snap, params={"dryRun": "true"}).json()
    r = client.post(f"{API}/restore", json=snap, params={"dryRun": "false", "checksum": dry["checksum"]})
    assert r.status_code == 200, r.text
    assert client.get(RULES).json()["items"] == before
    assert client.get(RULES, headers=B).json()["items"] == []


def test_place_with_no_old_project_only_adds_to_new_row(client, projects):
    p, _ = projects
    start = _slot()
    env = {"user": "u_local", "dedupeKey": "place:run_x:1", "source": "agent-place", "subject": {"project": p},
           "data": {"fromProjectId": None, "fromTaskId": None, "run": {
               "source": "agent-hook", "dedupeKey": "agent:run_x", "agent": "garden", "startAt": start.isoformat(),
               "durationSeconds": 1800, "days": [{"date": start.date().isoformat(), "seconds": 1800}]}}}
    from app.modules.projector.handlers import agent_daily_stats  # noqa: PLC0415

    agent_daily_stats.handle_place(env)
    body = client.get(f"{API}/views/agent-time").json()
    assert _stats(client) == ({p: 1800}, 1) and body["days"][0]["seconds"] == 1800


def test_place_projection_is_idempotent_on_redelivery(client, projects):
    p, _ = projects
    run = _finished(client)
    _place(client, run, projectId=p, remember=False)
    from app.modules.projector import registry  # noqa: PLC0415

    ev = _db()["events"].find_one({"type": "agent.run.placed"}, {"_id": 0})
    registry.dispatch(ev)
    assert _stats(client) == ({p: 1800}, 1)


def _restore_with(client, rules):
    snap = client.get(f"{API}/export").json()
    snap["agentPlaceRules"] = rules
    return client.post(f"{API}/restore", json=snap, params={"dryRun": "true"})


@pytest.mark.parametrize("rules", [
    [{"agent": "", "projectId": "p", "createdAt": "x"}],
    [{"agent": "a", "projectId": "p", "createdAt": "x"}, {"agent": "a", "projectId": "p", "createdAt": "x"}],
    [{"agent": "a", "taskId": "t", "projectId": "p", "createdAt": "x"}],
    [{"agent": "a", "createdAt": "x"}],
    [{"agent": "a", "projectId": "p", "createdAt": 5}],
    [{"agent": f"a{i}", "projectId": "p", "createdAt": "x"} for i in range(501)],
])
def test_restore_rejects_bad_place_rules_before_writing(client, rules):
    assert _restore_with(client, rules).status_code == 400
