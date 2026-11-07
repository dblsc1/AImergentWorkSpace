"""onboarding.v1「三」实时询问与只问一次（NC-L / NC-A）。NC-A04（claim）、NC-A10（candidates）等 NB-5 再补。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from _onboarding_common import live_rec
from _tier_common import API, BEARER, BLK, BR, TZ, db, day, upload
from test_auto_track import DEV, B, world, clock  # noqa: F401 —— 都是 fixture
from test_caller_scope import READ, REPORT

ASK, ANSWER = f"{API}/activity/live-ask", f"{API}/activity/live-ask/answer"
APP, TITLE = "chrome", "figma.com · 首页改版"
KEY = f"{APP} · {TITLE}"
TERM = {"X-Nexus-Scope": "write", "Authorization": "Bearer t"}  # 非「人」


@pytest.fixture(autouse=True)
def _tiers_on():
    db().activity_settings.update_one({"user": "u_local"}, {"$set": {"tiers": True, "version": 1, "browserDomains": ["figma.com"]}}, upsert=True)


def _line(t0, segs, device=DEV):
    """segs = [(起秒, 止秒, 标题 | None 表示离开)]，相对 t0；写进在场时间线（与心跳写下的文档同形）。"""
    spans = [{"from": t0 + timedelta(seconds=a), "to": t0 + timedelta(seconds=b), "app": APP if t else "", "title": t or "", "afk": t is None}
             for a, b, t in segs]
    db().activity_presence.replace_one({"user": "u_local", "deviceId": device},
                                       {"user": "u_local", "deviceId": device, "lastAt": spans[-1]["to"], "app": spans[-1]["app"],
                                        "title": spans[-1]["title"], "afk": spans[-1]["afk"], "spans": spans}, upsert=True)


def _ask(client, headers=None, expect=200):
    r = client.get(ASK, headers=headers or {})
    assert r.status_code == expect, r.text
    return r.json().get("ask") if expect == 200 else None


def _stay(client, clock, seconds, gap=None, gap_title="other"):
    """停留 seconds 秒（gap 给了：前 seconds-100 秒、切走 gap 秒、再回来 100 秒）。"""
    t0 = clock(0)
    if gap is None:
        _line(t0, [(-seconds, 0, TITLE)])
    else:
        _line(t0, [(-seconds - gap, -100 - gap, TITLE), (-100 - gap, -100, gap_title), (-100, 0, TITLE)])
    return t0


def test_l01_shape(client, clock):
    t0 = _stay(client, clock, 372)
    ask = _ask(client)
    assert ask == {"blockKey": KEY, "displayName": TITLE, "focusedSeconds": 372, "seq": 0,
                   "since": (t0 - timedelta(seconds=372)).astimezone(TZ).isoformat(), "date": t0.astimezone(TZ).date().isoformat()}


@pytest.mark.parametrize("seconds,asked", [(300, True), (299, False)])
def test_l02_l03_threshold(client, clock, seconds, asked):
    _stay(client, clock, seconds)
    assert bool(_ask(client)) is asked


@pytest.mark.parametrize("gap,asked", [(60, True), (61, False)])
@pytest.mark.parametrize("away", [None, "afk"])
def test_l04_to_l07_gap_switch_or_afk(client, clock, gap, asked, away):
    t0 = clock(0)
    mid = (-100 - gap, -100, "other" if away is None else None)
    _line(t0, [(-300 - gap, -100 - gap, TITLE), mid, (-100, 0, TITLE)])
    got = _ask(client)
    assert bool(got) is asked
    if asked:
        assert got["focusedSeconds"] == 300 and got["since"] == (t0 - timedelta(seconds=300 + gap)).astimezone(TZ).isoformat()


def test_gap_break_counts_from_return(client, clock):
    t0 = clock(0)
    _line(t0, [(-1000, -400, TITLE), (-400, -330, "other"), (-330, 0, TITLE)])
    assert _ask(client)["focusedSeconds"] == 330


@pytest.mark.parametrize("minutes,seconds,asked", [(1, 60, True), (1, 59, False), (60, 3600, True), (60, 3599, False)])
def test_l08_l09_minutes(client, clock, minutes, seconds, asked):
    db().activity_settings.update_one({"user": "u_local"}, {"$set": {"liveAskMinutes": minutes}})
    _stay(client, clock, seconds)
    assert bool(_ask(client)) is asked


def test_l10_disabled(client, clock):
    db().activity_settings.update_one({"user": "u_local"}, {"$set": {"liveAskEnabled": False}})
    _stay(client, clock, 900)
    assert _ask(client) is None


def test_l11_afk(client, clock):
    t0 = clock(0)
    _line(t0, [(-900, -10, TITLE), (-10, 0, None)])
    assert _ask(client) is None


def test_l12_stale_heartbeat(client, clock):
    t0 = clock(0)
    _line(t0, [(-900, -91, TITLE)])
    assert _ask(client) is None
    _line(t0, [(-900, -90, TITLE)])
    assert _ask(client)["focusedSeconds"] == 810


def test_l13_second_level_rule_manages_it(client, clock, world):
    assert client.post(BR, json={"blockKey": KEY, "taskId": world["a"]}).status_code in (200, 201)
    _stay(client, clock, 900)
    assert _ask(client) is None


def test_l14_ignored_and_generic(client, clock):
    _stay(client, clock, 900)
    assert client.post(f"{BLK}/generic", json={"blockKey": KEY, "generic": True}).status_code == 200
    assert _ask(client) is None
    client.post(f"{BLK}/generic", json={"blockKey": KEY, "generic": False})
    assert _ask(client)
    d = day(0)
    db().activity_blocks.insert_one({"user": "u_local", "date": d, "blockKey": KEY, "status": "ignored", "parts": [], "via": "human", "seq": 1})
    assert _ask(client) is None


def test_l15_illegible_terminal(client, clock):
    t0 = clock(0)
    db().activity_presence.insert_one({"user": "u_local", "deviceId": DEV, "lastAt": t0, "app": "ptyxis", "title": "Coder", "afk": False,
                                       "spans": [{"from": t0 - timedelta(seconds=600), "to": t0, "app": "ptyxis", "title": "Coder", "afk": False}]})
    assert _ask(client) is None


def test_l16_l17_asked_forever(client, clock, world):
    t0 = _stay(client, clock, 900)
    db().activity_asked.insert_one({"user": "u_local", "blockKey": KEY, "answer": "unmanaged", "askedAt": t0})
    assert _ask(client) is None
    db().activity_asked.delete_many({})
    d = day(0)
    seq = _ask(client)["seq"]
    r = client.post(f"{BLK}/assign", json={"date": d, "blockKey": KEY, "projectId": world["p"], "ifSeq": seq})
    assert r.status_code == 200, r.text
    r = client.post(f"{BLK}/reset", json={"date": d, "blockKey": KEY, "ifSeq": r.json()["seq"]})
    assert r.status_code == 200 and r.json()["status"] == "pending"
    assert _ask(client) is None


def test_l18_tiers_off(client, clock):
    db().activity_settings.update_one({"user": "u_local"}, {"$set": {"tiers": False}})
    _stay(client, clock, 900)
    r = client.get(ASK)
    assert r.status_code == 200 and r.json() == {"ask": None}


def test_l19_l20_who_can_read(client, clock):
    _stay(client, clock, 900)
    for h in ({}, READ, {"X-Nexus-Scope": "write", "Authorization": "Bearer t"}):
        assert _ask(client, h)
    assert client.get(ASK, headers=REPORT).status_code == 403


def test_l21_no_doc_seq_zero_and_l22_seq_usable(client, clock, world):
    _stay(client, clock, 900)
    assert _ask(client)["seq"] == 0
    d = day(0)
    assert client.post(f"{BLK}/reset", json={"date": d, "blockKey": KEY, "ifSeq": 0}).status_code == 200  # 建文档，seq 1，仍 pending
    ask = _ask(client)
    assert ask["seq"] == 1 and ask["date"] == d
    upload(client, [live_rec(-100, 20, app=APP, title=TITLE)])
    item = next(i for i in client.get(BLK, params={"date": d}).json()["items"] if i["blockKey"] == KEY)
    assert item["seq"] == ask["seq"]
    r = client.post(f"{BLK}/assign", json={"date": d, "blockKey": KEY, "projectId": world["p"], "ifSeq": ask["seq"]})
    assert r.status_code == 200, r.text


def test_l23_latest_device_decides(client, clock):
    t0 = clock(0)
    _line(t0, [(-900, -5, TITLE)], device="dev_a")
    assert _ask(client)
    _line(t0, [(-30, 0, "other doc")], device="dev_b")
    assert _ask(client) is None


def test_midnight_cuts_the_stay(client, clock):
    base = clock(0)
    mid = datetime.combine(base.astimezone(TZ).date(), datetime.min.time(), tzinfo=TZ)
    now = clock((mid + timedelta(seconds=600) - base).total_seconds())
    _line(now, [(-1500, 0, TITLE)])
    ask = _ask(client)
    assert ask["focusedSeconds"] == 600 and ask["since"] == mid.isoformat() and ask["date"] == mid.date().isoformat()


# ─────────────────────────────────────────── 只问一次的写入


def _asked(**q):
    return list(db().activity_asked.find({"user": "u_local", **q}))


@pytest.fixture()
def pending(client, world):
    """今天一个 pending 块（有原始记录）。"""
    upload(client, [live_rec(-100, 20, app=APP, title=TITLE)])
    return day(0)


def _post(client, op, d, **body):
    return client.post(f"{BLK}/{op}", json={"date": d, "blockKey": KEY, "ifSeq": 0, **body})


def test_a01_a03_assign_split_ignore_write_asked(client, pending, world):
    for i, (op, body, answer) in enumerate([
        ("assign", {"projectId": world["p"]}, "assigned"),
        ("split", {"parts": [{"toTs": _ms(pending, 12), "projectId": world["p"]}, {"projectId": world["q"]}]}, "split"),
        ("ignore", {}, "ignored"),
    ]):
        key = f"{KEY}{i}"
        r = client.post(f"{BLK}/{op}", json={"date": pending, "blockKey": key, "ifSeq": 0, **body})
        assert r.status_code == 200, r.text
        [doc] = _asked(blockKey=key)
        assert doc["answer"] == answer and doc["askedAt"]


def _ms(d, hour):
    return round(datetime.fromisoformat(f"{d}T{hour:02d}:00:00").replace(tzinfo=TZ).timestamp() * 1000)


def test_a05_existing_not_touched_and_unique(client, pending, world):
    r = _post(client, "ignore", pending)
    first = _asked()[0]
    r = client.post(f"{BLK}/reset", json={"date": pending, "blockKey": KEY, "ifSeq": r.json()["seq"]})
    assert _post(client, "assign", pending, projectId=world["p"], ifSeq=r.json()["seq"]).status_code == 200
    [doc] = _asked()
    assert doc["answer"] == "ignored" and doc["askedAt"] == first["askedAt"]


def test_a06_failures_write_nothing(client, pending, world):
    assert _post(client, "assign", pending, projectId=world["p"], ifSeq=5).status_code == 409
    assert _post(client, "assign", pending, projectId="p_nope").status_code == 404
    assert _post(client, "assign", pending).status_code == 422
    assert _asked() == []


def test_a07_reset_generic_block_rule_write_nothing(client, pending, world):
    assert _post(client, "reset", pending).status_code == 200
    assert client.post(f"{BLK}/generic", json={"blockKey": KEY, "generic": True}).status_code == 200
    assert client.post(BR, json={"blockKey": KEY, "projectId": world["p"]}).status_code in (200, 201)
    assert _asked() == []


def test_a08_a09_unmanaged(client, clock, pending):
    r = client.post(ANSWER, json={"blockKey": KEY, "answer": "unmanaged"})
    assert r.status_code == 200, r.text
    assert set(r.json()) == {"blockKey", "answer", "askedAt"} and _asked()[0]["answer"] == "unmanaged"
    _stay(client, clock, 900)
    assert _ask(client) is None
    items = client.get(BLK, params={"date": pending, "status": "pending"}).json()["items"]
    assert [i["blockKey"] for i in items] == [KEY]


def test_a11_unknown_block_404_but_doc_alone_is_enough(client):
    assert client.post(ANSWER, json={"blockKey": "nope", "answer": "unmanaged"}).status_code == 404
    old = day(-30)
    db().activity_blocks.insert_one({"user": "u_local", "date": old, "blockKey": "only-doc", "status": None, "seq": 1})
    assert client.post(ANSWER, json={"blockKey": "only-doc", "answer": "unmanaged"}).status_code == 200


@pytest.mark.parametrize("body", [{"blockKey": KEY, "answer": "assigned"}, {"blockKey": KEY, "answer": ""}, {"blockKey": KEY}, {"answer": "unmanaged"}])
def test_a12_bad_body(client, pending, body):
    assert client.post(ANSWER, json=body).status_code == 422


@pytest.mark.parametrize("headers", [BEARER, TERM, READ, REPORT])
def test_a13_only_humans(client, pending, headers):
    assert client.post(ANSWER, json={"blockKey": KEY, "answer": "unmanaged"}, headers=headers).status_code == 403
    assert client.post(ANSWER, json={"nonsense": 1}, headers=headers).status_code == 403  # 先挡人，再看体
    assert _asked() == []


def test_a14_idempotent(client, pending, world):
    assert _post(client, "ignore", pending).status_code == 200
    first = _asked()[0]
    r = client.post(ANSWER, json={"blockKey": KEY, "answer": "unmanaged"})
    assert r.status_code == 200 and r.json()["answer"] == "ignored"
    assert r.json()["askedAt"] == first["askedAt"].replace(tzinfo=timezone.utc).isoformat()
    assert len(_asked()) == 1


def test_a15_kept_forever(client, pending):
    client.post(ANSWER, json={"blockKey": KEY, "answer": "unmanaged"})
    ttl = int(__import__("app.config", fromlist=["settings"]).settings.record_ttl_days)
    db().activity_records.update_many({"user": "u_local"}, {"$set": {"startTs": datetime.now(timezone.utc) - timedelta(days=ttl + 60)}})
    client.get(BLK, params={"date": pending})  # 惰性清理
    assert db().activity_records.count_documents({"user": "u_local"}) == 0 and len(_asked()) == 1


def test_a16_export_restore_round_trip(client):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    db().activity_asked.insert_many([{"user": "u_local", "blockKey": "k1", "answer": "assigned", "askedAt": now},
                                     {"user": "u_local", "blockKey": "k2", "answer": "unmanaged", "askedAt": now - timedelta(hours=1)}])
    snap = client.get(f"{API}/export").json()
    assert [a["blockKey"] for a in snap["activityAsked"]] == ["k2", "k1"]
    db().activity_asked.delete_many({})
    url = f"{API}/restore"
    chk = client.post(url, json=snap, params={"dryRun": True}).json()["checksum"]
    assert client.post(url, json=snap, params={"dryRun": False, "checksum": chk}).status_code == 200
    again = client.get(f"{API}/export").json()
    snap.pop("exportedAt"), again.pop("exportedAt")
    assert again == snap and db().activity_asked.count_documents({}) == 2


def test_restore_validates_first_and_keeps_only_three_keys(client):
    bad = {"zones": [], "projects": [], "tasks": [], "events": [], "activityAsked": [{"blockKey": "k", "answer": "bogus", "askedAt": "2026-01-01T00:00:00+00:00"}]}
    assert client.post(f"{API}/restore", json=bad, params={"dryRun": True}).status_code == 400
    good = {**bad, "activityAsked": [{"blockKey": "k", "answer": "ignored", "askedAt": "2026-01-01T00:00:00+00:00", "extra": 1}]}
    chk = client.post(f"{API}/restore", json=good, params={"dryRun": True}).json()["checksum"]
    assert client.post(f"{API}/restore", json=good, params={"dryRun": False, "checksum": chk}).status_code == 200
    assert "extra" not in db().activity_asked.find_one({"blockKey": "k"})


def test_export_has_no_key_without_asked(client):
    assert "activityAsked" not in client.get(f"{API}/export").json()


def test_a17_tenant(client, clock):
    _stay(client, clock, 900)
    db().activity_asked.insert_one({"user": "ch_bbbb", "blockKey": KEY, "answer": "unmanaged", "askedAt": datetime.now(timezone.utc)})
    assert _ask(client)
    assert client.get(ASK, headers=B).json() == {"ask": None}
