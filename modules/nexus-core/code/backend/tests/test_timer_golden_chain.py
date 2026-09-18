"""timer → events → projector 金链路的单测（S6–S8 + R1/R2 + start 自动关闭）。

J10 后任务 id 由系统生成：一律从 ``seeded`` 夹具拿真实 id，不写死编号。
整合级回归（review/regression/）归 reviewer；这里是 pytest 级、进程内 TestClient。
"""

from __future__ import annotations

API = "/api/core"


def _events(**query) -> list[dict]:
    from app.repo import get_db

    return list(get_db()["events"].find(query or {}, {"_id": 0}))


def _current_projection() -> dict | None:
    from app.modules.projector import repo

    return repo.read_current("u_local")


def test_s6_r1_r2_start_stop_produces_event_and_updates_projection(client, seeded):
    """S6/R1/R2：无分类字段的种子跑 start→stop 全程 2xx；恰好一条 session.completed，
    其 subject 键集合 = {zone, project, task}（无 tier2/tier3）；proj_current 被更新。"""
    task_id = seeded["tasks"]["示例任务三"]["id"]
    zone_id = seeded["zones"]["示例分区二"]["id"]
    project_id = seeded["projects"]["示例项目三"]["id"]

    start = client.post(f"{API}/timer/start", json={"taskId": task_id})
    assert start.status_code == 200, f"R1：start 不再 500，实际 {start.status_code} {start.text}"
    body = start.json()
    assert body["running"] is True and body["taskId"] == task_id

    stop = client.post(f"{API}/timer/stop")
    assert stop.status_code == 200, f"R1：stop 全程 2xx，实际 {stop.status_code} {stop.text}"
    stop_body = stop.json()
    assert stop_body["running"] is False
    assert stop_body["event"]["type"] == "session.completed"
    assert stop_body["event"]["dedupeKey"].startswith("timer:sess_")

    events = _events(type="session.completed")
    assert len(events) == 1
    event = events[0]
    # R2：subject 只有三个 opaque id，旧分类字段一个都不许有
    assert set(event["subject"]) == {"zone", "project", "task"}
    assert event["subject"] == {"zone": zone_id, "project": project_id, "task": task_id}
    assert event["data"]["durationSeconds"] >= 1
    assert event["recordedAt"], "recordedAt 由事件入口盖章——证明 stop 走的是 POST /events 的路"

    projection = _current_projection()
    assert projection is not None, "S6 断言的是投影**曾被更新**，不是碰巧读到旧值"
    assert projection["projects"][project_id] == event["data"]["durationSeconds"]
    assert projection["tasks"][task_id] == event["data"]["durationSeconds"]


def test_s7_repeated_stop_is_idempotent(client, seeded):
    """S7：重复 stop → 幂等，只产生一条事件。"""
    client.post(f"{API}/timer/start", json={"taskId": seeded["tasks"]["示例任务三"]["id"]})
    first = client.post(f"{API}/timer/stop")
    second = client.post(f"{API}/timer/stop")

    assert first.json()["event"] is not None
    assert second.status_code == 200
    assert second.json() == {"running": False, "event": None}
    assert len(_events(type="session.completed")) == 1


def test_s8_stop_without_running_timer_is_calm(client):
    """S8：未在计时时 stop → 200 且 running:false，不报错、不产生事件。"""
    resp = client.post(f"{API}/timer/stop")
    assert resp.status_code == 200
    assert resp.json() == {"running": False, "event": None}
    assert _events() == []


def test_start_auto_closes_previous_session(client, seeded):
    """contract.md：start 自动关闭上一个未结束的 session，不返回错误。"""
    first_id = seeded["tasks"]["示例任务三"]["id"]
    second_id = seeded["tasks"]["示例任务四"]["id"]
    client.post(f"{API}/timer/start", json={"taskId": first_id})
    second = client.post(f"{API}/timer/start", json={"taskId": second_id})

    assert second.status_code == 200
    assert second.json()["taskId"] == second_id
    closed = _events(type="session.completed")
    assert len(closed) == 1, "上一段计时应被自动收尾成一条事件"
    assert closed[0]["subject"]["task"] == first_id

    from app.modules.timer import service

    state = service.get_running_state()
    assert state is not None and state["taskId"] == second_id


def test_projection_handler_is_idempotent_on_same_event(client, seeded):
    """handler 幂等——同一事件（同 dedupeKey）投两次，投影结果相同。"""
    from app.modules.projector.handlers import current

    client.post(f"{API}/timer/start", json={"taskId": seeded["tasks"]["示例任务三"]["id"]})
    client.post(f"{API}/timer/stop")
    event = _events(type="session.completed")[0]
    before = _current_projection()

    current.handle(event)  # 直接再投一次同一事件
    assert _current_projection() == before


def test_start_unknown_task_is_404(client, seeded):
    resp = client.post(f"{API}/timer/start", json={"taskId": "t_missing"})
    assert resp.status_code == 404
    assert "t_missing" in resp.json()["detail"]
