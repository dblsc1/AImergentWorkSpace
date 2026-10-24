"""``session.voided``（契约 v2.38「四」、yq-event.v1 §6）：公开入口拒收、投影与 views 跳过作废的段、重建一致、档案读端标 voided。"""

from __future__ import annotations

import pytest
from _reconcile_common import assign, ev, live_sessions, tiers
from _tier_common import BLK, day, db, rec, upload
from test_session_reassign import API
from test_session_reassign_views import _rebuild, _views

D = day(-1)
K = "chrome · 周报"


@pytest.fixture()
def tid(seeded):
    return seeded["tasks"]["示例任务三"]["id"]


def _envelope(tid, **over):
    base = {"spec": "yq-event/v1", "id": "evt_ext1", "dedupeKey": "ext:1", "type": "session.completed", "user": "u_local",
            "source": "ext-tool", "time": "2026-10-23T10:00:00+00:00", "subject": {"zone": "z", "project": "p", "task": tid},
            "data": {"durationSeconds": 60, "startAt": "2026-10-23T09:59:00+00:00"}}
    return {**base, **over}


def test_public_entry_rejects_voided_and_activity_block(client, tid):
    voided = _envelope(tid, id="evt_v", dedupeKey="void:evt_x", type="session.voided", data={"voidedEventId": "evt_x", "actor": "human", "reason": "block-split"})
    block = _envelope(tid, dedupeKey="block:x", source="activity-block")
    r = client.post(f"{API}/events", json=[voided, block]).json()
    assert r["accepted"] == 0 and [x["index"] for x in r["rejected"]] == [0, 1]
    assert db().events.count_documents({}) == 0
    # 换别的 source 写 session.voided 一样不行（类型本身只由对账写）
    assert client.post(f"{API}/events", json=_envelope(tid, type="session.voided", source="whatever", dedupeKey="v2")).json()["rejected"]


def test_external_ai_block_is_not_stored_but_other_ai_keys_are(client, tid):
    ai = {"generated": True, "confidence": 0.5, "confirmed": False, "block": {"date": D, "blockKey": K}}
    assert client.post(f"{API}/events", json=_envelope(tid, ai=ai)).json()["accepted"] == 1
    stored = db().events.find_one({"dedupeKey": "ext:1"})
    assert stored["ai"] == {"generated": True, "confidence": 0.5, "confirmed": False}


def _world(client, tid):
    tiers(client)
    upload(client, [rec(D, "09:00:00", "09:10:00", title="周报")])
    b = assign(client, D, K, tid)
    r = client.post(f"{API}/timer/backfill", json={"taskId": tid, "startAt": f"{D}T20:00:00+08:00", "durationSeconds": 300})
    assert r.status_code == 200, r.text
    return b


def test_projections_and_views_skip_voided_and_rebuild_agrees(client, tid):
    b = _world(client, tid)
    live = _views(client)
    before = [t for p in live["gantt"]["projects"] for t in p.get("tasks", [])] if "projects" in live["gantt"] else []
    assert live["projections"]["proj_current"]["totalSeconds"] == 900  # 600 秒窗口块 + 300 秒补登
    assert len(live["sessions"]) == 2 and before is not None
    r = client.post(f"{BLK}/ignore", json={"date": D, "blockKey": K, "ifSeq": b["seq"]})
    assert r.status_code == 200 and live_sessions() == [] and len(ev("session.voided")) == 1
    after = _views(client)
    assert after["projections"]["proj_current"]["totalSeconds"] == 300 and len(after["sessions"]) == 1
    assert [x for x in after["projections"]["proj_daily_stats"]] and sum(x["seconds"] for x in after["projections"]["proj_daily_stats"]) == 300
    assert after != live
    _rebuild()
    assert _views(client) == after  # 重建不重放被作废的事实：与实时投影一致
    _rebuild()
    assert _views(client) == after


def test_void_after_reassign_subtracts_from_current_owner(client, tid, seeded):
    other = next(i["id"] for n, i in seeded["tasks"].items() if i["id"] != tid)
    tiers(client)
    upload(client, [rec(D, "09:00:00", "09:10:00", title="周报")])
    b = assign(client, D, K, tid)
    b = assign(client, D, K, other, seq=b["seq"])  # 改挂到 other
    mid = _views(client)
    assert mid["projections"]["proj_current"]["tasks"] == {other: 600}
    client.post(f"{BLK}/reset", json={"date": D, "blockKey": K, "ifSeq": b["seq"]})
    after = _views(client)
    assert after["projections"]["proj_current"] is None  # 当前归属（other）那一份被减掉，不是原来的 tid
    _rebuild()
    again = _views(client)
    assert again["gantt"] == after["gantt"] and again["sessions"] == after["sessions"] == []
    assert again["projections"] == after["projections"] and again["projections"]["proj_daily_stats"] == []
    assert again["projections"]["proj_current"] is None


def test_events_archive_marks_voided_sessions(client, tid):
    b = _world(client, tid)
    client.post(f"{BLK}/ignore", json={"date": D, "blockKey": K, "ifSeq": b["seq"]})
    items = client.get(f"{API}/events", params={"type": "session.completed", "limit": 100}).json()["items"]
    assert sorted((i["source"], i.get("voided", False)) for i in items) == [("activity-block", True), ("timer-backend", False)] or \
        sorted((i["source"], i.get("voided", False)) for i in items) == [("activity-block", True), ("manual-backfill", False)]
    allv = client.get(f"{API}/events", params={"limit": 100}).json()["items"]
    assert {i["type"] for i in allv} >= {"session.voided", "session.reassigned"} or {"session.voided"} <= {i["type"] for i in allv}
    assert all("voided" not in i for i in allv if i["source"] != "activity-block" or i["type"] != "session.completed")
    voided = [i for i in allv if i.get("voided")]
    assert len(voided) == 1 and voided[0]["type"] == "session.completed"
