"""代理去向 timeFrom（契约 nexus-core v2.48）：「只算以后的时间」在跑的运行按切点分段计到各去向。"""

from __future__ import annotations

import pytest

from test_agent_place import API, AGENTS, _db, _finished, _place, _start, _stats, _task
from test_agent_place import projects  # noqa: F401  (fixture)


@pytest.fixture()
def clock(monkeypatch):
    """服务端时钟往后拨：``clock(seconds=600)`` 之后的 start/place/stop 都发生在「开始 + 600 秒」。"""
    from datetime import timedelta  # noqa: PLC0415

    from app.modules.timer import service  # noqa: PLC0415

    real = service._now

    def shift(seconds):
        monkeypatch.setattr(service, "_now", lambda: real() + timedelta(seconds=seconds))

    return shift


def _lane_project_today(client, run_id):
    return _db()["proj_lanes"].find_one({"kind": "run", "runId": run_id})["projectId"]


def _stop(client, run):
    r = client.post(f"{AGENTS}/{run}/stop", json={"outcome": "done"})
    assert r.status_code == 200, r.text
    return r.json()


def _stats_by_target(client):
    body = client.get(f"{API}/views/agent-time").json()
    return {(t["projectId"], t["taskId"]): t["seconds"] for t in body["tasks"]}, body["totalSeconds"], body["runs"]


def test_now_splits_running_run_at_place_time(client, projects, clock):
    p, _ = projects
    run = _start(client)
    clock(600)
    r = _place(client, run, projectId=p, remember=False, timeFrom="now")
    assert r.status_code == 200, r.text
    assert r.json()["projectId"] == p
    assert client.get(f"{API}/views/agent-time").json()["open"][-1]["projectId"] == p  # 当前去向 = 新的
    clock(1800)
    assert _stop(client, run)["durationSeconds"] == 1800
    assert _stats_by_target(client) == ({("p_inbox", None): 600, (p, None): 1200}, 1800, 1)
    assert _lane_project_today(client, run) == p


def test_two_successive_now_places_make_three_segments(client, projects, clock):
    p, q = projects
    t = _task(client, q)
    run = _start(client)
    clock(600)
    assert _place(client, run, projectId=p, remember=False, timeFrom="now").status_code == 200
    clock(1200)
    assert _place(client, run, taskId=t, remember=False, timeFrom="now").status_code == 200
    clock(1800)
    _stop(client, run)
    assert _stats_by_target(client) == ({("p_inbox", None): 600, (p, None): 600, (q, t): 600}, 1800, 1)
    assert _db()["events"].count_documents({"type": "agent.run.placed"}) == 2


def test_back_to_earlier_target_merges_rows_not_double_counted(client, projects, clock):
    p, q = projects
    run = _start(client, projectId=p)
    clock(300)
    _place(client, run, projectId=q, remember=False, timeFrom="now")
    clock(900)
    _place(client, run, projectId=p, remember=False, timeFrom="now")
    clock(1200)
    _stop(client, run)
    assert _stats_by_target(client) == ({(p, None): 600, (q, None): 600}, 1200, 1)


def test_same_target_now_is_noop_split(client, projects, clock):
    p, _ = projects
    run = _start(client, projectId=p)
    clock(600)
    assert _place(client, run, projectId=p, remember=False, timeFrom="now").status_code == 200
    clock(900)
    _stop(client, run)
    assert _stats_by_target(client) == ({(p, None): 900}, 900, 1)


def test_now_on_finished_run_422(client, projects):
    p, _ = projects
    msg = _place(client, _finished(client), projectId=p, timeFrom="now")
    assert msg.status_code == 422 and "只算以后" in msg.text and "已结束" in msg.text
    assert _stats(client) == ({"p_inbox": 1800}, 1)
    assert _db()["events"].count_documents({"type": "agent.run.placed"}) == 0


def test_now_on_run_stopped_through_api_422(client, projects):
    run = _start(client)
    _stop(client, run)
    assert _place(client, run, projectId=projects[0], timeFrom="now").status_code == 422


def test_bad_time_from_422(client, projects):
    run = _start(client)
    for bad in ("later", "", None, 1, "NOW"):
        assert _place(client, run, projectId=projects[0], timeFrom=bad).status_code == 422, bad
    assert _db()["events"].count_documents({"type": "agent.run.placed"}) == 0
    assert _place(client, run, projectId=projects[0], timeFrom="start").status_code == 200


def test_default_and_explicit_start_move_whole_run(client, projects, clock):
    p, q = projects
    run_a, run_b = _start(client, "a"), _start(client, "b")
    clock(900)
    _place(client, run_a, projectId=p, remember=False)
    _place(client, run_b, projectId=p, remember=False, timeFrom="start")
    clock(1800)
    _stop(client, run_a)
    _stop(client, run_b)
    assert _stats_by_target(client) == ({(p, None): 3600}, 3600, 2)


def test_start_after_now_collapses_the_cuts(client, projects, clock):
    p, q = projects
    run = _start(client)
    clock(600)
    _place(client, run, projectId=p, remember=False, timeFrom="now")
    clock(900)
    _place(client, run, projectId=q, remember=False)
    clock(1800)
    _stop(client, run)
    assert _stats_by_target(client) == ({(q, None): 1800}, 1800, 1)


def test_finished_segmented_run_whole_move_moves_every_segment(client, projects, clock):
    p, q = projects
    run = _start(client)
    clock(600)
    _place(client, run, projectId=p, remember=False, timeFrom="now")
    clock(1800)
    _stop(client, run)
    assert _place(client, run, projectId=q, remember=False).status_code == 200
    assert _stats_by_target(client) == ({(q, None): 1800}, 1800, 1)
    assert _lane_project_today(client, run) == q
    from app.modules.projector.rebuild import rebuild  # noqa: PLC0415

    rebuild()
    assert _stats_by_target(client) == ({(q, None): 1800}, 1800, 1)


def test_rebuild_gives_same_result(client, projects, clock):
    p, q = projects
    t = _task(client, q)
    run = _start(client)
    clock(600)
    _place(client, run, projectId=p, remember=False, timeFrom="now")
    clock(1200)
    _place(client, run, taskId=t, remember=False, timeFrom="now")
    clock(1800)
    _stop(client, run)
    before = _stats_by_target(client)
    assert before == ({("p_inbox", None): 600, (p, None): 600, (q, t): 600}, 1800, 1)
    from app.modules.projector.rebuild import rebuild  # noqa: PLC0415

    rebuild()
    assert _stats_by_target(client) == before
    assert _lane_project_today(client, run) == q


def test_projection_redelivery_is_idempotent(client, projects, clock):
    p, _ = projects
    run = _start(client)
    clock(600)
    _place(client, run, projectId=p, remember=False, timeFrom="now")
    clock(1800)
    _stop(client, run)
    from app.modules.projector import registry  # noqa: PLC0415

    registry.dispatch(_db()["events"].find_one({"type": "agent.run.completed"}, {"_id": 0}))
    assert _stats_by_target(client) == ({("p_inbox", None): 600, (p, None): 1200}, 1800, 1)


def test_remember_unchanged_with_now(client, projects, clock):
    p, _ = projects
    run = _start(client)
    clock(600)
    assert _place(client, run, projectId=p, timeFrom="now").json()["rule"] == {"agent": "garden", "projectId": p}
    assert _place(client, run, projectId=p, remember=False, timeFrom="now").json()["rule"] is None
    assert _db()["agent_place_rules"].count_documents({}) == 1


def test_arrangements_agent_seconds_follow_segments(client, projects, clock):
    p, _ = projects
    ta, tb = _task(client, p), _task(client, p)
    run = _start(client, taskId=ta)
    clock(600)
    _place(client, run, taskId=tb, remember=False, timeFrom="now")
    clock(1800)
    _stop(client, run)

    def spent():
        body = client.get(f"{API}/views/arrangements").json()
        rows = [x for g in ("overdue", "blocked", "scheduled", "actionable") for x in body[g]]
        return {x["taskId"]: x["spentThisRange"]["agentWorkingSeconds"] for x in rows}

    assert spent() == {ta: 600, tb: 1200}
    from app.modules.projector.rebuild import rebuild  # noqa: PLC0415

    rebuild()
    assert spent() == {ta: 600, tb: 1200}
