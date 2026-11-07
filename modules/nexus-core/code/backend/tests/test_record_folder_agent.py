"""记录的 folder、终端连代理运行（onboarding.v1 二·1、二·2）。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from _onboarding_common import live_rec, rec, run_attending
from _tier_common import day, db, upload

D = day(-1)


def _doc(**q):
    return db()["activity_records"].find_one(q)


def test_folder_stored_and_absent(client):
    out = upload(client, [rec(D, "09:00:00", "09:01:00", app="ptyxis", folder="garden"),
                          rec(D, "10:00:00", "10:01:00", app="ptyxis")])
    assert out["accepted"] == 2
    assert _doc(startAt={"$exists": True}, folder="garden") and "folder" not in _doc(date=D, seconds=60.0, folder={"$exists": False})


def test_folder_out_of_range_rejected_others_kept(client):
    good = rec(D, "09:00:00", "09:01:00", app="ptyxis", folder="ok")
    bad = [{**rec(D, f"10:0{i}:00", f"10:0{i}:30", app="ptyxis"), "folder": v} for i, v in enumerate(["", "x" * 65, 5])]
    out = upload(client, [good, *bad])
    assert out["accepted"] == 1 and sorted(r["index"] for r in out["rejected"]) == [1, 2, 3]
    assert upload(client, [rec(D, "11:00:00", "11:01:00", app="ptyxis", folder="x" * 64)])["accepted"] == 1


def test_folder_first_wins_on_duplicate(client):
    upload(client, [rec(D, "09:00:00", "09:01:00", app="ptyxis", folder="a")])
    assert upload(client, [rec(D, "09:00:00", "09:01:00", app="ptyxis", folder="b")])["duplicates"] == 1
    assert _doc(app="ptyxis")["folder"] == "a"


def _linked(client, offset=-100, seconds=20, attend=(0, 60), **kw):
    r = live_rec(offset, seconds, **kw)
    a = datetime.fromisoformat(r["startAt"])
    run_id = run_attending(client, "garden", [(a + timedelta(seconds=attend[0]), a + timedelta(seconds=attend[1]))])
    upload(client, [r])
    return run_id, _doc(startTs=a)


def test_terminal_record_links_single_run(client):
    run_id, d = _linked(client)
    assert d["agentRunId"] == run_id and d["blockKey"] == "ptyxis · garden" and d["keySource"] == "agent"


def test_no_attend_untouched(client):
    upload(client, [live_rec(-100, 20)])
    d = _doc(app="ptyxis")
    assert d["blockKey"] == "ptyxis · Coder" and d["keySource"] == "auto" and "agentRunId" not in d


def test_two_runs_untouched(client):
    r = live_rec(-100, 20)
    a = datetime.fromisoformat(r["startAt"])
    span = [(a - timedelta(seconds=5), a + timedelta(seconds=60))]
    run_attending(client, "one", span)
    run_attending(client, "two", span)
    upload(client, [r])
    assert _doc(app="ptyxis")["keySource"] == "auto"


def test_non_terminal_and_rule_block_not_linked(client):
    a = datetime.fromisoformat(live_rec(-100, 20)["startAt"])
    run_attending(client, "garden", [(a - timedelta(seconds=5), a + timedelta(seconds=60))])
    upload(client, [live_rec(-100, 20, app="code", title="x"), live_rec(-140, 20, title="rule", block="我的块")])
    assert _doc(app="code")["keySource"] == "auto"
    assert _doc(title="rule")["keySource"] == "rule"


def test_attend_half_open_boundary(client):
    end = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(seconds=-100)
    run_attending(client, "garden", [(end - timedelta(seconds=110), end)])
    on = {**live_rec(0, 1), "startAt": (end - timedelta(seconds=1)).isoformat(), "endAt": (end + timedelta(seconds=1)).isoformat(), "title": "on"}
    inside = {**on, "startAt": (end - timedelta(seconds=2)).isoformat(), "endAt": (end - timedelta(milliseconds=100)).isoformat(), "title": "in"}
    upload(client, [on, inside])
    assert _doc(title="on")["keySource"] == "auto"  # 中点 == until
    assert _doc(title="in")["keySource"] == "agent"


def test_agent_records_not_recomputed(client):
    _, d = _linked(client)
    from app.modules.activity import block_key  # noqa: PLC0415

    block_key.recompute_stale("u_local")
    assert _doc(id=d["id"])["keySource"] == "agent"


def test_earlier_records_not_relinked(client):
    r = live_rec(-100, 20)
    upload(client, [r])
    a = datetime.fromisoformat(r["startAt"])
    run_attending(client, "garden", [(a, a + timedelta(seconds=60))])
    assert _doc(startTs=a)["keySource"] == "auto"
