"""v2.35：``GET /activity/suggestions?status=confirmed`` 每行带 ``recordedTo``（记到了哪，取改挂后的当前去向）。"""

from __future__ import annotations

from test_activity_suggestions import SUG, _pending, _recent, _seg, _upload
from test_session_reassign import PLANNER, _reassign, _world


def _confirm(client, title: str, minutes_ago: int, **body) -> dict:
    _upload(client, [_seg(_recent(minutes_ago), minutes=1, title=title)])
    sug_id = next(i["id"] for i in _pending(client)["items"] if i["title"] == title)
    resp = client.post(f"{SUG}/{sug_id}/confirm", json=body)
    assert resp.status_code == 200, resp.text
    return {"id": sug_id, "event": resp.json()["event"]["id"]}


def _rows(client) -> dict:
    return {r["id"]: r for r in _pending(client, status="confirmed")["items"]}


def test_confirmed_rows_carry_current_destination(client):
    w = _world(client)
    t = _confirm(client, "窗口 t", 40, taskId=w["a"])
    b = _confirm(client, "窗口 b", 30, projectId=w["p"])
    rows = _rows(client)
    assert rows[t["id"]]["recordedTo"] == {
        "projectId": w["p"], "projectName": "改挂项目 P", "taskId": w["a"], "taskName": "任务 a",
        "via": "confirm", "eventId": t["event"]}
    assert rows[b["id"]]["recordedTo"] == {
        "projectId": w["p"], "projectName": "改挂项目 P", "taskId": None, "taskName": None,
        "via": "project", "eventId": b["event"]}

    _reassign(client, b["event"], taskId=w["c"])  # 只有记在「未分类」桶上的才能改挂：改到别的项目的任务
    rows = _rows(client)
    assert rows[t["id"]]["recordedTo"]["via"] == "confirm"  # 没改挂的不变
    assert rows[b["id"]]["recordedTo"] == {
        "projectId": w["q"], "projectName": "改挂项目 Q", "taskId": w["c"], "taskName": "任务 c",
        "via": "reassign", "eventId": b["event"]}
    _reassign(client, b["event"], projectId=w["q"])  # 最后一次为准：落到 Q 的桶
    rows = _rows(client)
    to = rows[b["id"]]["recordedTo"]
    assert (to["via"], to["projectId"], to["taskId"], to["taskName"]) == ("reassign", w["q"], None, None)


def test_pending_have_no_recorded_to_and_deleted_target_is_null(client):
    w = _world(client)
    _upload(client, [_seg(_recent(50), minutes=1, title="还没确认")])
    assert all("recordedTo" not in r for r in _pending(client)["items"])
    gone = _confirm(client, "任务会被删", 20, taskId=w["a"])
    assert client.delete(f"{PLANNER}/tasks/{w['a']}").status_code == 204
    assert _rows(client)[gone["id"]]["recordedTo"] is None
    _upload(client, [_seg(_recent(10), minutes=1, title="不要它")])
    drop = next(i["id"] for i in _pending(client)["items"] if i["title"] == "不要它")
    assert client.post(f"{SUG}/{drop}/dismiss").status_code == 200
    dismissed = _pending(client, status="dismissed")["items"]
    assert dismissed and all("recordedTo" not in r for r in dismissed)
