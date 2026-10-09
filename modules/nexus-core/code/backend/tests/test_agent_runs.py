"""AI 代理运行 + 人类计时模式（契约 v2.1「AI 代理运行」「人类计时模式」两节）。

要害只有一句：**人是一条泳道，AI 代理是很多条泳道，两边永不混账。**
所以最重的断言都是「看不见」——代理时长不许出现在 proj_daily_stats / proj_current /
甘特 / 回顾 / views/current 的人部分；人的计时器不许因为代理的 start/stop 而动一下。
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta, timezone

import pytest

API = "/api/core"
AGENTS = f"{API}/agents"
A = {"X-Nexus-Tenant": "ch_aaaa"}
B = {"X-Nexus-Tenant": "ch_bbbb"}


def _db():
    from app.repo import get_db  # noqa: PLC0415

    return get_db()


def _agent_events(user: str = "u_local") -> list[dict]:
    return list(_db()["events"].find({"type": "agent.run.completed", "user": user}, {"_id": 0}))


def _start(client, headers=None, **body):
    body = {"agent": "claude-code", "tool": "Bash", **body}
    resp = client.post(f"{AGENTS}/start", json=body, headers=headers or {})
    assert resp.status_code == 201, resp.text
    return resp.json()


def _stop(client, run_id, headers=None, **body):
    resp = client.post(f"{AGENTS}/{run_id}/stop", json={"outcome": "done", **body}, headers=headers or {})
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.fixture()
def shift_clock(monkeypatch):
    """把 timer 子边界的服务端时钟往后拨（代理运行与人的计时共用 ``service._now``）。"""
    from app.modules.timer import service  # noqa: PLC0415

    real = service._now

    def shift(**delta):
        monkeypatch.setattr(service, "_now", lambda: real() + timedelta(**delta))

    return shift


# ─────────────────────────────────────────── 并发与人的计时器互不影响


def test_three_concurrent_runs_and_human_timer_are_independent(client, seeded):
    task = seeded["tasks"]["示例任务三"]
    other = seeded["tasks"]["示例任务一"] if "示例任务一" in seeded["tasks"] else task
    client.post(f"{API}/timer/start", json={"taskId": task["id"]})
    human_before = _db()["timer_state"].find_one({}, {"_id": 0})

    runs = [_start(client, taskId=t["id"], agent=f"a{i}") for i, t in enumerate((task, other, task))]
    assert len({r["runId"] for r in runs}) == 3
    assert _db()["agent_runs"].count_documents({}) == 3

    # 人的计时器一个字节都没动，也没因为代理 start 被 stop 掉
    assert _db()["timer_state"].find_one({}, {"_id": 0}) == human_before
    assert _db()["events"].count_documents({}) == 0

    body = client.get(f"{API}/views/current").json()
    assert body["running"] is True and body["task"]["id"] == task["id"]
    assert [a["runId"] for a in body["agents"]] == [r["runId"] for r in runs]

    # 停一个代理：其余两个照跑，人照跑
    _stop(client, runs[1]["runId"])
    assert _db()["agent_runs"].count_documents({}) == 2
    assert _db()["timer_state"].find_one({}, {"_id": 0}) == human_before

    # 人 stop：代理不受影响
    client.post(f"{API}/timer/stop")
    assert _db()["agent_runs"].count_documents({}) == 2


def test_views_current_agents_shape_and_idle_human(client, seeded):
    """只有代理在跑：人的部分仍是空闲态（null 不是 0），agents 列出运行，键齐全。"""
    assert client.get(f"{API}/views/current").json()["agents"] == []
    task = seeded["tasks"]["示例任务三"]
    run = _start(client, taskId=task["id"], model="opus")
    inbox = _start(client)

    body = client.get(f"{API}/views/current").json()
    assert body["running"] is False
    for key in ("zone", "project", "task", "sessionStartAt"):
        assert body[key] is None
    assert all(0 <= a.pop("elapsedSeconds") < 60 for a in body["agents"])  # v2.21：服务端算的（口径另见 test_agent_heartbeat）
    assert body["agents"] == [
        {"runId": run["runId"], "taskId": task["id"], "agent": "claude-code", "tool": "Bash",
         "model": "opus", "startedAt": run["startedAt"], "phase": None, "label": None},
        {"runId": inbox["runId"], "taskId": None, "agent": "claude-code", "tool": "Bash",
         "model": None, "startedAt": inbox["startedAt"], "phase": None, "label": None},
    ]


# ─────────────────────────────────────────── stop：一条事件，重复不重


def test_stop_writes_exactly_one_event_and_double_stop_does_not_duplicate(client, seeded):
    task = seeded["tasks"]["示例任务三"]
    project = seeded["projects"]["示例项目三"]
    zone = seeded["zones"]["示例分区二"]
    run = _start(client, taskId=task["id"], model="opus")

    first = _stop(client, run["runId"], output="PR 已开")
    assert first["duplicate"] is False and first["outcome"] == "done"
    assert first["event"]["dedupeKey"] == f"agent:{run['runId']}"
    assert first["event"]["type"] == "agent.run.completed"

    events = _agent_events()
    assert len(events) == 1
    event = events[0]
    assert event["source"] == "agent-hook"
    assert event["subject"] == {"zone": zone["id"], "project": project["id"], "task": task["id"]}
    assert event["data"] == {
        "agent": "claude-code", "tool": "Bash", "model": "opus", "startAt": run["startedAt"],
        "durationSeconds": first["durationSeconds"], "outcome": "done", "output": "PR 已开",
    }
    assert _db()["agent_runs"].count_documents({}) == 0

    # 第二次 stop（哪怕换了 outcome）：200、duplicate、回原来那条，不写第二条
    second = _stop(client, run["runId"], outcome="failed")
    assert second == {**first, "duplicate": True}
    assert len(_agent_events()) == 1


def test_stop_unknown_run_is_404(client):
    resp = client.post(f"{AGENTS}/run_nope/stop", json={"outcome": "done"})
    assert resp.status_code == 404
    assert "run_nope" in resp.json()["detail"]


def test_optional_fields_absent_from_data_when_not_given(client, seeded):
    run = _start(client, taskId=seeded["tasks"]["示例任务三"]["id"])
    _stop(client, run["runId"], outcome="cancelled")
    data = _agent_events()[0]["data"]
    assert "model" not in data and "output" not in data
    assert data["outcome"] == "cancelled"


# ─────────────────────────────────────────── 永不计入人的时间


def test_agent_time_only_in_agent_projection(client, seeded, shift_clock):
    task = seeded["tasks"]["示例任务三"]
    project = seeded["projects"]["示例项目三"]
    run = _start(client, taskId=task["id"])
    shift_clock(hours=2)
    stopped = _stop(client, run["runId"])
    assert 7200 <= stopped["durationSeconds"] <= 7210

    db = _db()
    assert db["proj_daily_stats"].count_documents({}) == 0
    assert db["proj_current"].count_documents({}) == 0
    gantt = client.get(f"{API}/views/gantt").json()
    assert all(not p.get("actual") for p in gantt["projects"])
    assert all(not t.get("actual") for p in gantt["projects"] for t in p.get("tasks", []))

    rows = list(db["proj_agent_daily_stats"].find({}, {"_id": 0, "appliedKeys": 0}))
    assert len(rows) == 1
    row = rows[0]
    assert (row["projectId"], row["taskId"], row["agent"]) == (project["id"], task["id"], "claude-code")
    assert (row["seconds"], row["runs"]) == (stopped["durationSeconds"], 1)


def test_human_daily_total_unaffected_by_concurrent_agents(client, seeded, shift_clock):
    """人计 1h，同一时段 3 个代理各跑 1h：人的日统计仍是 1h，不是 4h。"""
    task = seeded["tasks"]["示例任务三"]
    client.post(f"{API}/timer/start", json={"taskId": task["id"]})
    runs = [_start(client, taskId=task["id"], agent=f"a{i}") for i in range(3)]
    shift_clock(hours=1)
    for r in runs:
        _stop(client, r["runId"])
    client.post(f"{API}/timer/stop")

    human = sum(r["seconds"] for r in _db()["proj_daily_stats"].find())
    assert 3600 <= human <= 3610
    agent = sum(r["seconds"] for r in _db()["proj_agent_daily_stats"].find())
    assert 3 * 3600 <= agent <= 3 * 3610


def test_agent_projection_concurrent_first_upsert_not_lost(monkeypatch):
    """两条不同事件并发建同一聚合行：后到者撞唯一索引，不能被当成「已应用」丢掉。
    用一层包装模拟竞态：首次 upsert 前先让「另一请求」建好行，再抛 DuplicateKeyError。"""
    from pymongo.errors import DuplicateKeyError  # noqa: PLC0415

    from app.modules.projector import repo  # noqa: PLC0415

    real = repo._agent_daily_col
    key = {"user": "u_local", "date": "2026-09-28", "projectId": "p1", "taskId": "t1", "agent": "a"}

    class Racy:
        raced = False

        def update_one(self, query, update, upsert=False):
            if upsert and not Racy.raced:
                Racy.raced = True
                real().insert_one({**key, "seconds": 10, "runs": 1, "appliedKeys": ["agent:first"]})
                raise DuplicateKeyError("E11000 模拟竞态")
            return real().update_one(query, update, upsert=upsert)

    monkeypatch.setattr(repo, "_agent_daily_col", Racy)
    assert repo.apply_agent_daily_stat("u_local", "agent:second", "2026-09-28", "p1", "t1", "a", 5)
    row = real().find_one({}, {"_id": 0})
    assert (row["seconds"], row["runs"]) == (15, 2)
    # 真·重复仍是 False、不重复累计
    assert not repo.apply_agent_daily_stat("u_local", "agent:second", "2026-09-28", "p1", "t1", "a", 5)
    assert real().find_one({}, {"_id": 0})["seconds"] == 15


def _agent_envelope(source: str, dedupe_key: str, seconds) -> dict:
    return {
        "spec": "yq-event/v1", "id": f"evt_{source}", "dedupeKey": dedupe_key,
        "type": "agent.run.completed", "user": "u_local", "source": source,
        "time": "2026-09-28T10:00:00+00:00",
        "subject": {"zone": "z1", "project": "p1", "task": "t1"},
        "data": {"agent": "a", "tool": "t", "startAt": "2026-09-28T09:00:00+00:00",
                 "durationSeconds": seconds, "outcome": "done"},
    }


def test_agent_projection_identity_includes_source(client):
    """同 dedupeKey、不同 source 是两条合法事件（入口防重身份含 source），投影不许并成一条。"""
    for source in ("hook-a", "hook-b"):
        resp = client.post(f"{API}/events", json=_agent_envelope(source, "same-key", 60))
        assert resp.json()["accepted"] == 1
    row = _db()["proj_agent_daily_stats"].find_one()
    assert (row["seconds"], row["runs"]) == (120, 2)

    from app.modules.projector.rebuild import rebuild  # noqa: PLC0415

    rebuild(only="proj_agent_daily_stats")  # 重建同样不并
    row = _db()["proj_agent_daily_stats"].find_one()
    assert (row["seconds"], row["runs"]) == (120, 2)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), 1e308, 10**400, 32 * 86400])
def test_agent_projection_skips_non_finite_or_absurd_duration(bad):
    """坏载荷静默跳过、不炸投影（NaN/Inf 经 HTTP 进不来——JSON 不认，这里直接喂 handler）。"""
    from app.modules.projector.handlers import agent_daily_stats  # noqa: PLC0415

    agent_daily_stats.handle(_agent_envelope("x", f"k-{bad}", bad))
    assert _db()["proj_agent_daily_stats"].count_documents({}) == 0


# ─────────────────────────────────────────── 超时惰性关闭


def test_forgotten_run_times_out_capped_on_current_read(client, seeded, shift_clock):
    run = _start(client, taskId=seeded["tasks"]["示例任务三"]["id"])
    shift_clock(hours=30)  # 默认上限 12h

    assert client.get(f"{API}/views/current").json()["agents"] == []
    events = _agent_events()
    assert len(events) == 1
    data = events[0]["data"]
    assert data["outcome"] == "timeout" and data["durationSeconds"] == 12 * 3600
    started = datetime.fromisoformat(run["startedAt"])
    assert datetime.fromisoformat(events[0]["time"]) == started + timedelta(hours=12)

    # 超时后 hook 迟到的 stop：回那条 timeout 事件，不写第二条
    late = _stop(client, run["runId"])
    assert late["duplicate"] is True and late["outcome"] == "timeout"
    assert len(_agent_events()) == 1


def test_timeout_is_configurable_and_swept_on_start(client, seeded, shift_clock, monkeypatch):
    from app import config  # noqa: PLC0415

    monkeypatch.setattr(config, "settings", dataclasses.replace(config.settings, agent_run_timeout_hours=1))
    task_id = seeded["tasks"]["示例任务三"]["id"]
    old = _start(client, taskId=task_id)
    shift_clock(minutes=90)
    new = _start(client, taskId=task_id)  # start 也会收超时
    assert [r["runId"] for r in _db()["agent_runs"].find()] == [new["runId"]]
    assert _agent_events()[0]["data"]["durationSeconds"] == 3600
    assert _agent_events()[0]["dedupeKey"] == f"agent:{old['runId']}"


def test_timeout_config_rejects_garbage():
    from app.config import ConfigError, load_settings  # noqa: PLC0415

    env = {"NEXUS_MONGO_URI": "mongodb://x", "NEXUS_DB_NAME": "d_test", "NEXUS_TZ": "UTC"}
    assert load_settings(env).agent_run_timeout_hours == 12
    assert load_settings({**env, "NEXUS_AGENT_RUN_TIMEOUT_HOURS": "3"}).agent_run_timeout_hours == 3
    for bad in ("0", "-1", "abc", "1.5"):
        with pytest.raises(ConfigError):
            load_settings({**env, "NEXUS_AGENT_RUN_TIMEOUT_HOURS": bad})


# ─────────────────────────────────────────── 归属：收件箱 / 未知任务 / 校验


def test_no_task_goes_to_inbox(client):
    run = _start(client)
    _stop(client, run["runId"])
    # 信封模型落库时把缺省的 task 规整成 null（既有 ingest 行为，所有无任务事件都一样）
    assert _agent_events()[0]["subject"] == {"zone": "z_inbox", "project": "p_inbox", "task": None}
    row = _db()["proj_agent_daily_stats"].find_one()
    assert (row["projectId"], row["taskId"]) == ("p_inbox", None)


def test_unknown_task_rejected_like_timer(client):
    resp = client.post(f"{AGENTS}/start", json={"taskId": "t_ghost", "agent": "x", "tool": "y"})
    assert resp.status_code == 404
    assert resp.json()["detail"].startswith("任务不存在：'t_ghost'")
    assert _db()["agent_runs"].count_documents({}) == 0


@pytest.mark.parametrize("body", [
    {"agent": "", "tool": "Bash"},
    {"agent": "a" * 65, "tool": "Bash"},
    {"agent": "x", "tool": "t" * 65},
    {"agent": "x", "tool": "Bash", "model": "m" * 65},
    {"tool": "Bash"},
])
def test_start_body_validation(client, body):
    assert client.post(f"{AGENTS}/start", json=body).status_code == 422
    assert _db()["agent_runs"].count_documents({}) == 0


def test_stop_body_validation(client):
    run = _start(client)
    assert client.post(f"{AGENTS}/{run['runId']}/stop", json={"outcome": "maybe"}).status_code == 422
    assert client.post(
        f"{AGENTS}/{run['runId']}/stop", json={"outcome": "done", "output": "o" * 513}
    ).status_code == 422
    assert _agent_events() == [] and _db()["agent_runs"].count_documents({}) == 1


# ─────────────────────────────────────────── 重建 / 恢复 / 租户


def test_rebuild_reproduces_agent_projection(client, seeded):
    from app.modules.projector.rebuild import rebuild  # noqa: PLC0415

    task_id = seeded["tasks"]["示例任务三"]["id"]
    for _ in range(2):
        _stop(client, _start(client, taskId=task_id)["runId"])
    _stop(client, _start(client)["runId"])
    col = _db()["proj_agent_daily_stats"]
    before = sorted(col.find({}, {"_id": 0, "appliedKeys": 0}), key=lambda r: r["projectId"])

    col.delete_many({})
    assert rebuild(only="proj_agent_daily_stats") == {"proj_agent_daily_stats": 3}
    assert sorted(col.find({}, {"_id": 0, "appliedKeys": 0}), key=lambda r: r["projectId"]) == before
    rebuild()  # 幂等
    assert sorted(col.find({}, {"_id": 0, "appliedKeys": 0}), key=lambda r: r["projectId"]) == before


def test_restore_rebuilds_agent_projection(client, seeded):
    task_id = seeded["tasks"]["示例任务三"]["id"]
    _stop(client, _start(client, taskId=task_id)["runId"])
    in_flight = _start(client, taskId=task_id)  # 在跑的运行不是事实，不进导出
    col = _db()["proj_agent_daily_stats"]
    before = list(col.find({}, {"_id": 0}))
    snapshot = client.get(f"{API}/export").json()
    assert "agent_runs" not in snapshot
    assert all(in_flight["runId"] not in e["dedupeKey"] for e in snapshot["events"])

    db = _db()
    for name in db.list_collection_names():
        db[name].delete_many({})
    checksum = client.post(f"{API}/restore", json=snapshot).json()["checksum"]
    resp = client.post(f"{API}/restore?dryRun=false&checksum={checksum}", json=snapshot)
    assert resp.status_code == 200, resp.text
    assert resp.json()["rebuilt"]["proj_agent_daily_stats"] == 1
    assert list(col.find({}, {"_id": 0})) == before


def test_tenant_isolation(client):
    zone = client.post(f"{API}/planner/zones", json={"name": "甲"}, headers=A).json()
    project = client.post(f"{API}/planner/projects", json={"zoneId": zone["id"], "name": "甲"}, headers=A).json()
    task = client.post(f"{API}/planner/tasks", json={"projectId": project["id"], "name": "甲"}, headers=A).json()

    # 乙拿甲的任务开运行：404（同 timer）
    resp = client.post(f"{AGENTS}/start", json={"taskId": task["id"], "agent": "x", "tool": "y"}, headers=B)
    assert resp.status_code == 404

    run = _start(client, headers=A, taskId=task["id"])
    assert client.get(f"{API}/views/current", headers=B).json()["agents"] == []
    assert len(client.get(f"{API}/views/current", headers=A).json()["agents"]) == 1
    # 乙停不了甲的运行（与不存在同形状）
    assert client.post(f"{AGENTS}/{run['runId']}/stop", json={"outcome": "done"}, headers=B).status_code == 404

    _stop(client, run["runId"], headers=A)
    assert len(_agent_events("ch_aaaa")) == 1 and _agent_events("ch_bbbb") == []
    assert _db()["proj_agent_daily_stats"].find_one()["user"] == "ch_aaaa"
    # 甲的事件乙也按 runId 探不到
    assert client.post(f"{AGENTS}/{run['runId']}/stop", json={"outcome": "done"}, headers=B).status_code == 404


# ─────────────────────────────────────────── 人类计时模式 mode


def _session_events() -> list[dict]:
    return list(_db()["events"].find({"type": "session.completed"}, {"_id": 0}))


def test_mode_persisted_from_start_to_stop(client, seeded):
    task_id = seeded["tasks"]["示例任务三"]["id"]
    resp = client.post(f"{API}/timer/start", json={"taskId": task_id, "mode": "review"})
    assert resp.status_code == 200 and resp.json()["mode"] == "review"
    client.post(f"{API}/timer/stop")
    assert _session_events()[0]["data"]["mode"] == "review"


def test_mode_default_do_leaves_data_unchanged(client, seeded):
    """缺省 do：data 与 v1.8 完全同形（不写 mode 键，读方 data.mode ?? 'do'）。"""
    task_id = seeded["tasks"]["示例任务三"]["id"]
    resp = client.post(f"{API}/timer/start", json={"taskId": task_id})
    assert resp.json()["mode"] == "do"
    client.post(f"{API}/timer/stop")
    assert set(_session_events()[0]["data"]) == {"durationSeconds", "startAt"}


def test_legacy_timer_state_without_mode_stops_as_do(client, seeded):
    """v2.1 之前存下的活状态没有 mode 键：stop 照常，按 do。"""
    task_id = seeded["tasks"]["示例任务三"]["id"]
    client.post(f"{API}/timer/start", json={"taskId": task_id, "mode": "prompt"})
    _db()["timer_state"].update_one({}, {"$unset": {"mode": ""}})
    assert client.post(f"{API}/timer/stop").status_code == 200
    assert "mode" not in _session_events()[0]["data"]


def test_auto_stop_on_restart_keeps_previous_mode(client, seeded):
    task_id = seeded["tasks"]["示例任务三"]["id"]
    client.post(f"{API}/timer/start", json={"taskId": task_id, "mode": "prompt"})
    client.post(f"{API}/timer/start", json={"taskId": task_id, "mode": "review"})  # 自动关上一段
    client.post(f"{API}/timer/stop")
    assert sorted(e["data"].get("mode", "do") for e in _session_events()) == ["prompt", "review"]


def test_backfill_mode(client, seeded):
    task_id = seeded["tasks"]["示例任务三"]["id"]
    start_at = (datetime.now(timezone.utc) - timedelta(days=2)).replace(microsecond=0).isoformat()
    body = {"taskId": task_id, "startAt": start_at, "durationSeconds": 600, "mode": "prompt"}
    resp = client.post(f"{API}/timer/backfill", json=body)
    assert resp.status_code == 200, resp.text
    assert _session_events()[0]["data"]["mode"] == "prompt"
    # 默认 do：不写键；mode 不参与 dedupeKey（同一段换标签仍是重复）
    again = client.post(f"{API}/timer/backfill", json={**body, "mode": "do"}).json()
    assert again["duplicate"] is True


@pytest.mark.parametrize("path", ["start", "backfill"])
def test_invalid_mode_rejected(client, seeded, path):
    task_id = seeded["tasks"]["示例任务三"]["id"]
    body = {"taskId": task_id, "mode": "sleep"}
    if path == "backfill":
        body.update(startAt="2026-01-01T10:00:00+00:00", durationSeconds=60)
    resp = client.post(f"{API}/timer/{path}", json=body)
    assert resp.status_code == 422
    assert "detail" in resp.json()
    assert _db()["timer_state"].count_documents({}) == 0 and _session_events() == []
