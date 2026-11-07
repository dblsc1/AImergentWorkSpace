"""无项目计时与白条（hive.stage.v1「三·1」，nexus-core v2.44）。"""

from __future__ import annotations

from datetime import timedelta

import pytest

from test_activity_suggestions import BEARER, _recent
from test_restore import AI_HEAD, ai_token  # noqa: F401 —— ai_token 是 fixture
from test_session_reassign import API, PLANNER, _post, _world

TIMER = f"{API}/timer"


def _db():
    from app.repo import get_db  # noqa: PLC0415

    return get_db()


def _events(**q) -> list[dict]:
    return list(_db()["events"].find(q, {"_id": 0}))


def _bars(client) -> list[dict]:
    resp = client.get(f"{TIMER}/unplaced")
    assert resp.status_code == 200, resp.text
    return resp.json()["items"]


def _bar(minutes_ago: int, seconds: int, bar_id: str) -> dict:
    start = _recent(minutes_ago)
    doc = {"user": "u_local", "id": bar_id, "startAt": start.isoformat(),
           "endAt": (start + timedelta(seconds=seconds)).isoformat(), "createdAt": start.isoformat()}
    _db()["timer_unplaced"].insert_one(dict(doc))
    return doc


def _stop_bar(client) -> str:
    assert client.post(f"{TIMER}/start-unplaced").status_code == 200
    return client.post(f"{TIMER}/stop").json()["unplacedId"]


# ------------------------------------------------------------ start-unplaced / stop


def test_start_unplaced_has_no_target_and_second_start_is_409(client):
    out = client.post(f"{TIMER}/start-unplaced")
    assert out.status_code == 200, out.text
    body = out.json()
    assert body["running"] is True and body["taskId"] is None and body["projectId"] is None and body["startAt"]
    assert client.post(f"{TIMER}/start-unplaced").status_code == 409
    cur = client.get(f"{API}/views/current").json()
    assert cur["running"] is True and cur["task"] is None


def test_stop_unplaced_keeps_a_bar_and_writes_no_fact(client):
    bar_id = _stop_bar(client)
    assert bar_id
    assert _events() == []
    items = _bars(client)
    assert [i["id"] for i in items] == [bar_id]
    assert items[0]["durationSeconds"] >= 1 and items[0]["startAt"] and items[0]["endAt"]
    assert client.get(f"{API}/views/current").json()["running"] is False


def test_stop_with_target_has_null_unplaced_id(client):
    w = _world(client)
    _post(client, f"{TIMER}/start", {"taskId": w["a"]})
    out = client.post(f"{TIMER}/stop").json()
    assert out["unplacedId"] is None and out["event"]["type"] == "session.completed"
    assert "unplacedId" not in client.post(f"{TIMER}/stop").json()  # 没在计时：形状同从前
    assert _bars(client) == []


def test_start_task_closes_running_unplaced_into_a_bar(client):
    w = _world(client)
    client.post(f"{TIMER}/start-unplaced")
    _post(client, f"{TIMER}/start", {"taskId": w["a"]})
    assert len(_bars(client)) == 1 and _events() == []


def test_cancel_unplaced_records_nothing(client):
    client.post(f"{TIMER}/start-unplaced")
    resp = client.post(f"{TIMER}/cancel")
    assert resp.status_code == 200, resp.text
    assert resp.json()["cancelled"]["taskId"] is None
    assert _bars(client) == [] and _events() == []


def test_bars_sorted_by_start_ascending(client):
    _bar(10, 60, "unp_late")
    _bar(50, 60, "unp_early")
    _bar(30, 60, "unp_mid")
    assert [i["id"] for i in _bars(client)] == ["unp_early", "unp_mid", "unp_late"]


# ------------------------------------------------------------ timer/place


def test_place_running_to_task_keeps_same_run(client):
    w = _world(client)
    started = client.post(f"{TIMER}/start-unplaced").json()
    out = _post(client, f"{TIMER}/place", {"taskId": w["a"]})
    assert out["taskId"] == w["a"] and out["projectId"] == w["p"] and out["startAt"] == started["startAt"]
    # 已有目标时再换，不停不新开
    out = _post(client, f"{TIMER}/place", {"taskId": w["c"]})
    assert out["taskId"] == w["c"] and out["projectId"] == w["q"] and out["startAt"] == started["startAt"]
    stop = client.post(f"{TIMER}/stop").json()
    assert stop["unplacedId"] is None
    ev = _events(type="session.completed")
    assert len(ev) == 1 and ev[0]["subject"]["task"] == w["c"] and ev[0]["data"]["startAt"] == started["startAt"]
    assert _bars(client) == []


def test_place_running_to_project_goes_to_unclassified_bucket(client):
    w = _world(client)
    client.post(f"{TIMER}/start-unplaced")
    out = _post(client, f"{TIMER}/place", {"projectId": w["q"]})
    bucket = _post(client, f"{PLANNER}/projects/{w['q']}/unclassified")["taskId"]
    assert out["taskId"] == bucket and out["projectId"] == w["q"]
    client.post(f"{TIMER}/stop")
    assert _events(type="session.completed")[0]["subject"]["task"] == bucket


def test_place_rejections(client):
    w = _world(client)
    assert client.post(f"{TIMER}/place", json={"taskId": w["a"]}).status_code == 409  # 没在计时
    client.post(f"{TIMER}/start-unplaced")
    for body in ({}, {"taskId": w["a"], "projectId": w["p"]}):
        assert client.post(f"{TIMER}/place", json=body).status_code == 422
    assert client.post(f"{TIMER}/place", json={"taskId": "t_nope"}).status_code == 404
    assert client.post(f"{TIMER}/place", json={"projectId": "p_nope"}).status_code == 404
    assert client.get(f"{API}/views/current").json()["task"] is None  # 失败什么都没改


# ------------------------------------------------------------ unplaced/{id}/place


def test_place_bar_writes_session_with_bar_times_and_deletes_it(client):
    w = _world(client)
    bar = _bar(30, 600, "unp_one")
    out = _post(client, f"{TIMER}/unplaced/unp_one/place", {"taskId": w["a"]})
    assert out == {"recorded": True, "taskId": w["a"], "projectId": w["p"]}
    ev = _events(type="session.completed")
    assert len(ev) == 1 and ev[0]["source"] == "timer-backend" and ev[0]["subject"]["task"] == w["a"]
    assert ev[0]["data"]["startAt"] == bar["startAt"] and ev[0]["data"]["durationSeconds"] == 600
    assert _bars(client) == []
    assert client.post(f"{TIMER}/unplaced/unp_one/place", json={"taskId": w["a"]}).status_code == 404


def test_place_bar_to_project_uses_bucket_and_may_overlap(client):
    w = _world(client)
    _bar(30, 600, "unp_a")
    _bar(30, 600, "unp_b")  # 与上一条同一段时间
    _post(client, f"{TIMER}/unplaced/unp_a/place", {"projectId": w["p"]})
    out = _post(client, f"{TIMER}/unplaced/unp_b/place", {"projectId": w["p"]})
    assert out["taskId"] == w["bucket"] and out["projectId"] == w["p"]
    assert len(_events(type="session.completed")) == 2


def test_place_bar_rejections_change_nothing(client):
    w = _world(client)
    _bar(30, 60, "unp_x")
    assert client.post(f"{TIMER}/unplaced/nope/place", json={"taskId": w["a"]}).status_code == 404
    assert client.post(f"{TIMER}/unplaced/unp_x/place", json={"taskId": "t_nope"}).status_code == 404
    assert client.post(f"{TIMER}/unplaced/unp_x/place", json={"projectId": "p_nope"}).status_code == 404
    for body in ({}, {"taskId": w["a"], "projectId": w["p"]}):
        assert client.post(f"{TIMER}/unplaced/unp_x/place", json=body).status_code == 422
    assert [i["id"] for i in _bars(client)] == ["unp_x"] and _events() == []


def test_bars_are_per_tenant(client):
    _bar(30, 60, "unp_mine")
    assert client.get(f"{TIMER}/unplaced", headers={"X-Nexus-Tenant": "ch_bbbb"}).json()["items"] == []


# ------------------------------------------------------------ 只许人


@pytest.mark.parametrize("headers", [BEARER, AI_HEAD])
def test_new_endpoints_refuse_device_token_and_ai(client, ai_token, headers):  # noqa: F811
    w = _world(client)
    _bar(30, 60, "unp_g")
    calls = [("post", "start-unplaced", {}), ("post", "place", {"taskId": w["a"]}),
             ("get", "unplaced", None), ("post", "unplaced/unp_g/place", {"taskId": w["a"]})]
    for method, path, body in calls:
        kw = {} if body is None else {"json": body}
        resp = getattr(client, method)(f"{TIMER}/{path}", headers=headers, **kw)
        assert resp.status_code == 403, (path, resp.text)
    assert [i["id"] for i in _bars(client)] == ["unp_g"] and _events() == []
    assert client.get(f"{API}/views/current").json()["running"] is False


# ------------------------------------------------------------ 导出 / 恢复


def test_bars_round_trip_through_export_and_restore(client):
    _bar(30, 60, "unp_r")
    snap = client.get(f"{API}/export").json()
    assert [b["id"] for b in snap["timerUnplaced"]] == ["unp_r"]
    _db()["timer_unplaced"].delete_many({})
    url = f"{API}/restore"
    chk = client.post(url, json=snap, params={"dryRun": True}).json()["checksum"]
    assert client.post(url, json=snap, params={"dryRun": False, "checksum": chk}).status_code == 200
    again = client.get(f"{API}/export").json()
    snap.pop("exportedAt")
    again.pop("exportedAt")
    assert again == snap
    assert _db()["timer_unplaced"].count_documents({}) == 1


def test_stop_does_not_swallow_a_place_that_lands_after_it_read_state(client, monkeypatch):
    from app.modules.timer import repo, service  # noqa: PLC0415

    w = _world(client)
    client.post(f"{TIMER}/start-unplaced")
    real, raced = repo.get_running, []

    def stale_read(user):
        state = real(user)
        if not raced:  # stop 读完 state 之后，place 的效果落地
            raced.append(1)
            assert repo.retarget_running(user, state["sessionId"], {"taskId": w["a"], "projectId": w["p"], "zoneId": w["zone"]})
        return state

    monkeypatch.setattr(service.repo, "get_running", stale_read)
    out = client.post(f"{TIMER}/stop").json()
    assert out["unplacedId"] is None and out["event"]["type"] == "session.completed"
    assert _events(type="session.completed")[0]["subject"]["task"] == w["a"] and _bars(client) == []


def test_restore_keeps_only_the_four_bar_keys(client):
    _bar(30, 60, "unp_k")
    snap = client.get(f"{API}/export").json()
    snap["timerUnplaced"][0]["extra"] = "x"
    _db()["timer_unplaced"].delete_many({})
    url = f"{API}/restore"
    chk = client.post(url, json=snap, params={"dryRun": True}).json()["checksum"]
    assert client.post(url, json=snap, params={"dryRun": False, "checksum": chk}).status_code == 200
    assert "extra" not in _db()["timer_unplaced"].find_one({"id": "unp_k"})
