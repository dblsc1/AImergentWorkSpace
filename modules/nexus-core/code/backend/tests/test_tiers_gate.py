"""打开三级分类（``tiers``）对旧路径的影响（契约 v2.38「六」）：自动记录停、确认回 409（含报告批准）、待确认可读可忽略；关着一字节不变。"""

from __future__ import annotations

from _reconcile_common import tiers
from test_activity_reports import REPORTS, _ids, _status, _submit, _world_ids
from test_activity_suggestions import BEARER, SUG, _pending, _recent, _seg, _session_events, _upload
from test_auto_track import _track, world  # noqa: F401

API = "/api/core"
MSG = "已打开三级分类，请在窗口块里分类"


def test_confirm_is_409_when_tiers_on_and_pending_stays_readable_and_dismissable(client, seeded):
    task = seeded["tasks"]["示例任务三"]["id"]
    _upload(client, [_seg(_recent(30)), _seg(_recent(20))])
    ids = [i["id"] for i in _pending(client)["items"]]
    tiers(client)
    r = client.post(f"{SUG}/{ids[0]}/confirm", json={"taskId": task})
    assert r.status_code == 409 and r.json()["detail"] == MSG
    assert _session_events() == [] and _pending(client)["total"] == 2  # 待确认照常可读
    assert client.post(f"{SUG}/{ids[1]}/dismiss").status_code == 200  # 可忽略
    assert _pending(client)["total"] == 1
    assert _upload(client, [_seg(_recent(10))])["accepted"] == 1  # 建议上传仍然收


def test_confirm_works_again_when_tiers_off(client, seeded):
    task = seeded["tasks"]["示例任务三"]["id"]
    _upload(client, [_seg(_recent(30))])
    sid = _pending(client)["items"][0]["id"]
    tiers(client)
    assert client.post(f"{SUG}/{sid}/confirm", json={"taskId": task}).status_code == 409
    tiers(client, on=False)
    r = client.post(f"{SUG}/{sid}/confirm", json={"taskId": task})
    assert r.status_code == 200 and len(_session_events()) == 1


def test_auto_record_stops_when_tiers_on_and_is_unchanged_when_off(client, world):  # noqa: F811
    _track(client)
    seg_off = _seg(_recent(60), task_id=world["a"])
    _upload(client, [seg_off], headers=BEARER)
    assert _pending(client)["total"] == 0 and len(_session_events()) == 1  # tiers 关着：照旧自动记录
    tiers(client)
    _upload(client, [_seg(_recent(50), task_id=world["a"])], headers=BEARER)
    assert _pending(client)["total"] == 1 and len(_session_events()) == 1  # 开着：留在待确认，不记
    tiers(client, on=False)
    _upload(client, [_seg(_recent(40), task_id=world["a"])], headers=BEARER)
    assert len(_session_events()) == 2  # 再关：恢复旧行为


def test_report_approval_does_not_confirm_when_tiers_on(client, seeded):
    ids = _ids(client, 2)
    task, _project = _world_ids(seeded)
    rid = _submit(client, [{"kind": "assign", "suggestionIds": ids, "taskId": task}])["reportId"]
    tiers(client)
    out = client.post(f"{REPORTS}/{rid}/approve")
    assert out.status_code == 200, out.text
    assert out.json()["applied"] == 0 and out.json()["stale"] == 1  # 条数（一条 assign 含两段）
    assert _session_events() == [] and _pending(client)["total"] == 2
    assert MSG in str(_status(client, rid))
