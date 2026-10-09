"""调用方范围与匿名上报（契约 v2.19，``app/scope.py`` + agents 的匿名隔离）。

网关转来 ``X-Nexus-Scope`` / ``X-Nexus-Anonymous``；这里验 nexus-core 这一道：report / 匿名只有四个上报端点，
read 再加 GET，其余 403；匿名开的运行与验证过的运行互相够不着，响应里不漏别人的东西。
"""

from __future__ import annotations

import pytest

API = "/api/core"
AGENTS = f"{API}/agents"
REPORT = {"X-Nexus-Scope": "report", "Authorization": "Bearer t"}
READ = {"X-Nexus-Scope": "read", "Authorization": "Bearer t"}
WRITE = {"X-Nexus-Scope": "write", "Authorization": "Bearer t"}
ANON = {"X-Nexus-Scope": "report", "X-Nexus-Anonymous": "1"}
RUN = {"agent": "claude-code", "tool": "Bash"}
NOW = "2026-10-09T10:00:00+00:00"


def _db():
    from app.repo import get_db  # noqa: PLC0415

    return get_db()


def _start(client, headers, expect=201, **body):
    resp = client.post(f"{AGENTS}/start", json={**RUN, **body}, headers=headers)
    assert resp.status_code == expect, resp.text
    return resp.json()


# ─────────────────────────────────────────── 放行表


@pytest.mark.parametrize("headers", [REPORT, ANON])
def test_report_scope_and_anonymous_only_reach_the_report_endpoints(client, seeded, headers):
    run = _start(client, headers)
    assert set(run) == {"runId", "startedAt"}
    phase = client.post(f"{AGENTS}/{run['runId']}/phase", json={"phase": "working", "at": run["startedAt"]},
                        headers=headers)
    assert phase.status_code == 200 and set(phase.json()) == {"runId", "phase", "applied", "reason"}
    stop = client.post(f"{AGENTS}/{run['runId']}/stop", json={"outcome": "done"}, headers=headers)
    assert stop.status_code == 200
    assert set(stop.json()) == {"runId", "duplicate", "outcome", "durationSeconds", "event"}
    # 什么都读不到：每一类 GET、流式也好普通也好
    for path in ("/views/tree", "/views/current", "/views/lanes", "/views/agent-time", "/events", "/export",
                 "/planner/zones", "/detector/rules", "/activity/suggestions", f"/agents/{run['runId']}/phase",
                 "/agents/start", "/agents"):
        assert client.get(API + path, headers=headers).status_code == 403, path
    # 什么别的都写不了
    for method, path in (("POST", "/events"), ("POST", "/timer/start"), ("POST", "/timer/stop"),
                         ("POST", "/activity/presence"), ("POST", "/activity/suggestions"),
                         ("POST", "/planner/tasks"), ("DELETE", f"/agents/{run['runId']}/stop"),
                         ("PUT", "/agents/start"), ("PATCH", "/agents/start"), ("POST", "/restore"),
                         ("POST", "/agents/start/"), ("POST", f"/agents/{run['runId']}/stop/"),
                         ("POST", f"/agents/{run['runId']}/cancel"), ("POST", f"/agents/{run['runId']}/phase/x"),
                         ("HEAD", "/agents/start"), ("OPTIONS", "/agents/start"), ("HEAD", "/views/tree")):
        resp = client.request(method, API + path, json={}, headers=headers)
        assert resp.status_code == 403, (method, path, resp.status_code)


def test_heartbeat_is_in_the_table_even_before_the_endpoint_exists(client):
    """上报面里给 heartbeat 留了位：端点没实现时是路由自己的 404 / 405，不是范围的 403。"""
    resp = client.post(f"{AGENTS}/run_x/heartbeat", json={}, headers=REPORT)
    assert resp.status_code != 403


def test_read_scope_reads_everything_and_writes_only_reports(client, seeded):
    assert client.get(f"{API}/views/tree", headers=READ).status_code == 200
    assert client.get(f"{API}/views/lanes", headers=READ).status_code == 200
    run = _start(client, READ)
    assert client.post(f"{AGENTS}/{run['runId']}/stop", json={"outcome": "done"}, headers=READ).status_code == 200
    task = next(iter(seeded["tasks"].values()))["id"]
    for method, path, body in (("POST", "/events", {"events": []}), ("POST", "/timer/start", {"taskId": task}),
                               ("POST", "/activity/presence", {}), ("DELETE", "/views/tree", None),
                               ("HEAD", "/views/tree", None)):
        resp = client.request(method, API + path, json=body, headers=READ)
        assert resp.status_code == 403, (method, path)
    # 没写成：计时器没被开起来
    assert client.get(f"{API}/views/current", headers=READ).json()["running"] is False


def test_method_override_headers_are_not_honoured(client, seeded):
    task = next(iter(seeded["tasks"].values()))["id"]
    for name in ("X-HTTP-Method-Override", "X-Method-Override", "X-HTTP-Method"):
        resp = client.post(f"{API}/timer/start", json={"taskId": task}, headers={**READ, name: "GET"})
        assert resp.status_code == 403, name
        resp = client.get(f"{API}/views/tree", headers={**REPORT, name: "POST"})
        assert resp.status_code == 403, name
    assert client.get(f"{API}/views/current").json()["running"] is False


def test_write_scope_and_no_header_behave_as_before(client, seeded):
    task = next(iter(seeded["tasks"].values()))["id"]
    for headers in (WRITE, {"Authorization": "Bearer legacy"}, {}):
        assert client.get(f"{API}/views/tree", headers=headers).status_code == 200
        assert client.post(f"{API}/timer/start", json={"taskId": task}, headers=headers).status_code == 200
        assert client.post(f"{API}/timer/stop", headers=headers).status_code == 200
    # 只许人的端点对 write 照旧 403（既有守卫），对人照旧可用
    body = '{"rules":[]}'
    put = {"If-Match": '"0"', "Content-Type": "application/json"}
    assert client.put(f"{API}/detector/rules", content=body, headers={**WRITE, **put}).status_code == 403
    assert client.put(f"{API}/detector/rules", content=body, headers=put).status_code == 200


def test_unknown_or_doubled_scope_is_rejected_and_health_is_exempt(client):
    assert client.get(f"{API}/views/tree", headers={"X-Nexus-Scope": "admin"}).status_code == 403
    assert client.get(f"{API}/views/tree", headers={"X-Nexus-Scope": "WRITE"}).status_code == 403
    doubled = [("X-Nexus-Scope", "report"), ("X-Nexus-Scope", "write")]
    assert client.get(f"{API}/views/tree", headers=doubled).status_code == 403
    # 匿名标记赢过它自带的范围头：标了匿名就只有上报面
    assert client.get(f"{API}/views/tree", headers={"X-Nexus-Scope": "write", "X-Nexus-Anonymous": "1"}).status_code == 403
    assert client.get(f"{API}/views/tree", headers={"X-Nexus-Anonymous": "yes"}).status_code == 403
    assert client.get(f"{API}/health", headers=ANON).status_code == 200  # 容器健康检查


# ─────────────────────────────────────────── 匿名运行的隔离


def test_anonymous_run_is_unverified_in_lanes_and_in_the_fact(client, seeded):
    anon = _start(client, ANON, label="sandbox")
    mine = _start(client, {}, label="mine")
    live = {a["runId"]: a for a in client.get(f"{API}/views/lanes").json()["agents"]}
    assert live[anon["runId"]]["unverified"] is True and live[mine["runId"]]["unverified"] is False
    for run in (anon, mine):
        assert client.post(f"{AGENTS}/{run['runId']}/stop", json={"outcome": "done"}).status_code == 200
    closed = {a["runId"]: a for a in client.get(f"{API}/views/lanes").json()["agents"]}
    assert closed[anon["runId"]]["unverified"] is True and closed[mine["runId"]]["unverified"] is False
    facts = {e["dedupeKey"]: e["data"] for e in _db()["events"].find({"type": "agent.run.completed"})}
    assert facts[f"agent:{anon['runId']}"]["unverified"] is True
    assert "unverified" not in facts[f"agent:{mine['runId']}"]  # 验证过的运行写出的事实与以前逐字节相同


def test_anonymous_cannot_touch_or_tell_apart_authenticated_runs(client, seeded):
    mine = _start(client, WRITE)
    missing = "run_000000000000"
    for run_id in (mine["runId"], missing):
        phase = client.post(f"{AGENTS}/{run_id}/phase", json={"phase": "error", "at": NOW}, headers=ANON)
        stop = client.post(f"{AGENTS}/{run_id}/stop", json={"outcome": "failed"}, headers=ANON)
        assert (phase.status_code, stop.status_code) == (404, 404)
        # 「不是你的」与「不存在」是同一句话
        assert phase.json() == stop.json() == {"detail": f"代理运行不存在：{run_id!r}"}
    doc = _db()["agent_runs"].find_one({"runId": mine["runId"]})
    assert "closing" not in doc and not doc.get("phases")
    # 结束之后也一样：匿名够不着那条已落账的事实（不回 duplicate + outcome）
    assert client.post(f"{AGENTS}/{mine['runId']}/stop", json={"outcome": "done"}, headers=WRITE).status_code == 200
    stop = client.post(f"{AGENTS}/{mine['runId']}/stop", json={"outcome": "failed"}, headers=ANON)
    assert stop.status_code == 404 and "outcome" not in stop.json()
    # 匿名自己的运行结束后重复 stop 照旧回原事件
    anon = _start(client, ANON)
    assert client.post(f"{AGENTS}/{anon['runId']}/stop", json={"outcome": "done"}, headers=ANON).status_code == 200
    again = client.post(f"{AGENTS}/{anon['runId']}/stop", json={"outcome": "failed"}, headers=ANON)
    assert again.status_code == 200 and again.json()["duplicate"] is True and again.json()["outcome"] == "done"


def test_client_key_namespaces_do_not_cross(client, seeded):
    mine = _start(client, WRITE, clientKey="session-1", label="mine", match="my-window")
    # 匿名拿同一个 clientKey：开出自己的新运行，认领不到、也改不了我的
    anon = _start(client, ANON, clientKey="session-1", label="evil", match="my-window")
    assert anon["runId"] != mine["runId"]
    doc = _db()["agent_runs"].find_one({"runId": mine["runId"]})
    assert (doc["label"], doc["match"], doc["clientKey"]) == ("mine", "my-window", "session-1")
    # 匿名自己的重试回它自己的那个（200，不是新建）
    again = client.post(f"{AGENTS}/start", json={**RUN, "clientKey": "session-1"}, headers=ANON)
    assert again.status_code == 200 and again.json() == anon
    # 反过来：验证过的调用方用匿名名字空间的前缀 → 400；它的重试也认领不到匿名的运行
    resp = client.post(f"{AGENTS}/start", json={**RUN, "clientKey": "anon:session-1"}, headers=WRITE)
    assert resp.status_code == 400
    assert _start(client, WRITE, expect=200, clientKey="session-1") == mine


def test_anonymous_cannot_attach_to_tasks_projects_or_windows(client, seeded):
    task = next(iter(seeded["tasks"].values()))
    # 存在的任务、不存在的任务、不存在的项目：一律同一个 201，落收件箱——没有「这个 id 存在吗」的探针
    for body in ({"taskId": task["id"]}, {"taskId": "t_does_not_exist"}, {"projectId": "p_does_not_exist"},
                 {"taskId": task["id"], "projectId": "p_does_not_exist"}):
        run = _start(client, ANON, match="window title", **body)
        doc = _db()["agent_runs"].find_one({"runId": run["runId"]})
        assert doc["taskId"] is None and doc["projectId"] == "p_inbox" and "match" not in doc, body
        assert doc["unverified"] is True
    # 对照：带 report 令牌的可以挂任务，给错了照旧 404
    assert _db()["agent_runs"].find_one({"runId": _start(client, REPORT, taskId=task["id"])["runId"]})["taskId"] == task["id"]
    _start(client, REPORT, expect=404, taskId="t_does_not_exist")


def test_live_anonymous_runs_are_capped_per_tenant(client, seeded, monkeypatch):
    from app.modules.timer import agents  # noqa: PLC0415

    monkeypatch.setattr(agents, "MAX_ANONYMOUS_RUNS", 3)
    runs = [_start(client, ANON, clientKey=f"k{i}") for i in range(3)]
    resp = client.post(f"{AGENTS}/start", json=RUN, headers=ANON)
    assert resp.status_code == 429 and "上限" in resp.json()["detail"]
    assert _db()["agent_runs"].count_documents({"unverified": True}) == 3
    # 同 clientKey 的重试不占名额；验证过的调用方不受这个上限管
    assert client.post(f"{AGENTS}/start", json={**RUN, "clientKey": "k0"}, headers=ANON).status_code == 200
    _start(client, REPORT)
    _start(client, {})
    # 结束一个，名额就回来
    assert client.post(f"{AGENTS}/{runs[0]['runId']}/stop", json={"outcome": "done"}, headers=ANON).status_code == 200
    _start(client, ANON)


def test_length_limits_still_apply_to_anonymous(client):
    for body in ({"agent": "a" * 65}, {"label": "l" * 65}, {"clientKey": "k" * 129}, {"model": "m" * 65}):
        resp = client.post(f"{AGENTS}/start", json={**RUN, **body}, headers=ANON)
        assert resp.status_code == 422, body


def test_unverified_runs_cannot_claim_or_scramble_the_humans_attention(client, seeded):
    """v2.17 的注意力按「窗口标题 == 会话名」认运行；匿名起一个同名的运行，既认不到自己头上，也搅不乱真的那条。"""
    from app.modules.activity import session_link  # noqa: PLC0415
    from app.modules.timer import service as timer_service  # noqa: PLC0415

    mine = _start(client, WRITE, label="cockpit-dev")
    _now, rows = timer_service.list_lane_runs("u_local")
    assert session_link.watched(rows, "kitty", "cockpit-dev") == mine["runId"]
    anon = _start(client, ANON, label="cockpit-dev")
    lone = _start(client, ANON, label="only-anonymous")
    _now, rows = timer_service.list_lane_runs("u_local")
    assert {r["runId"]: r["unverified"] for r in rows} == {mine["runId"]: False, anon["runId"]: True, lone["runId"]: True}
    assert session_link.watched(rows, "kitty", "cockpit-dev") == mine["runId"]  # 不是「对上不止一条 → None」
    assert session_link.watched(rows, "kitty", "only-anonymous") is None

