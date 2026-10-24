"""对账的健壮性：作废段不能改挂、逐租户隔离、别的来源取全、只重访脏块、kick 不抢锁、blocked、日志、tiers 关掉后收口。"""

from __future__ import annotations

import logging
import threading

import pytest
from _reconcile_common import assign, doc, ev, live_sessions, put_doc, rnd, secs, tiers
from _tier_common import BLK, at, day, db, rec, upload
from app.modules.activity import reconcile, reconcile_repo, records_repo

D = day(-1)
K = "chrome · 周报"


@pytest.fixture()
def tid(seeded):
    return seeded["tasks"]["示例任务三"]["id"]


def _backfill(client, tid, hms, dur):
    r = client.post("/api/core/timer/backfill", json={"taskId": tid, "startAt": at(D, hms).isoformat(), "durationSeconds": dur})
    assert r.status_code == 200, r.text


def test_voided_session_cannot_be_reassigned(client, tid, seeded):
    tiers(client)
    other = next(i["id"] for n, i in seeded["tasks"].items() if i["id"] != tid)
    upload(client, [rec(D, "09:00:00", "09:10:00", title="周报")])
    b = assign(client, D, K, tid)
    sid = live_sessions()[0]["id"]
    client.post(f"{BLK}/reset", json={"date": D, "blockKey": K, "ifSeq": b["seq"]})
    assert live_sessions() == []
    r = client.post(f"/api/core/sessions/{sid}/reassign", json={"taskId": other})
    assert r.status_code == 409 and "作废" in r.json()["detail"]
    assert ev("session.reassigned", "session-reassign") == []


def test_one_tenant_failing_does_not_stop_the_round(client, tid, monkeypatch):
    tiers(client)
    db().activity_settings.insert_one({"user": "u_bad", "version": 1, "tiers": True, "tiersSince": None})
    upload(client, [rec(D, "09:00:00", "09:10:00", title="周报")])
    put_doc(D, K, tid)
    real = reconcile.run_round

    def flaky(user=None, **kw):
        if user == "u_bad":
            raise RuntimeError("boom")
        return real(user, **kw)

    monkeypatch.setattr(reconcile, "run_round", flaky)
    assert reconcile.run_all() == 1 and len(ev()) == 1


def test_other_sources_are_fetched_in_full_not_truncated(client, tid, monkeypatch):
    for hms in ("09:00:00", "12:00:00", "13:00:00", "14:00:00", "15:00:00"):
        _backfill(client, tid, hms, 120)
    tiers(client)
    upload(client, [rec(D, "09:00:00", "10:00:00", title="周报")])
    put_doc(D, K, tid)
    monkeypatch.setattr(reconcile, "_PAGE", 2)  # 最老的那条在第三页
    assert rnd() == 1
    assert secs(live_sessions()) == 3600 - 120


def test_incomplete_others_abandon_the_block_and_flag_it(client, tid, monkeypatch):
    for hms in ("09:00:00", "12:00:00", "13:00:00", "14:00:00", "15:00:00"):
        _backfill(client, tid, hms, 120)
    tiers(client)
    upload(client, [rec(D, "09:00:00", "10:00:00", title="周报")])
    put_doc(D, K, tid)
    monkeypatch.setattr(reconcile, "_PAGE", 2)
    monkeypatch.setattr(reconcile, "_PAGES", 1)
    assert rnd() == 0 and ev() == [] and doc(D, K)["reconciling"] is True
    monkeypatch.undo()
    assert rnd() == 1 and secs(live_sessions()) == 3600 - 120 and not doc(D, K)["reconciling"]


def test_dirty_round_skips_settled_blocks_and_revisits_new_data(client, tid, monkeypatch):
    tiers(client)
    upload(client, [rec(D, "09:00:00", "09:10:00", title="周报")])
    assign(client, D, K, tid)
    assert rnd() == 0  # 全量：一遍，记下水位
    reads = []
    real = records_repo.block_records
    monkeypatch.setattr(records_repo, "block_records", lambda *a, **k: reads.append(a) or real(*a, **k))
    assert rnd(full=False) == 0 and reads == []  # 已收口且没有新数据：不读这个块的记录
    upload(client, [rec(D, "11:00:00", "11:05:00", title="周报")])  # 新记录：上传触发的对账用 only=…，水位不动
    assert len(live_sessions()) == 2
    reads.clear()
    assert rnd(full=False) == 0 and reads, "新数据（receivedAt 在水位后）要重访"
    reads.clear()
    assert rnd(full=False) == 0 and reads == []
    db().activity_blocks.update_one({"blockKey": K}, {"$set": {"reconciling": True}})
    rnd(full=False)
    assert reads and not doc(D, K)["reconciling"]  # 带 reconciling 的块照样重访


def test_kick_does_not_wait_for_the_lock_and_loop_catches_up(client, tid):
    tiers(client)
    upload(client, [rec(D, "09:00:00", "09:10:00", title="周报")])
    put_doc(D, K, tid)
    lock = reconcile._locks.setdefault("u_local", threading.Lock())  # noqa: SLF001
    with lock:
        done = []
        t = threading.Thread(target=lambda: (reconcile.kick(), done.append(1)))
        t.start()
        t.join(3)
        assert done == [1] and ev() == []  # 没等锁，直接跳过
    assert "u_local" in reconcile._force_full  # noqa: SLF001
    assert rnd(full=False) == 1 and len(ev()) == 1  # 循环下一轮全量接手


def test_unwritable_target_is_blocked_until_the_block_changes(client, tid, caplog):
    tiers(client)
    upload(client, [rec(D, "09:00:00", "09:10:00", title="周报")])
    put_doc(D, K, "t_gone")
    with caplog.at_level(logging.WARNING, logger="uvicorn.error"):
        rnd()
        rnd()
        rnd()
    assert ev() == [] and [r.getMessage() for r in caplog.records].count(next(m for m in (r.getMessage() for r in caplog.records) if "对账写不成" in m)) == 1
    assert doc(D, K)["blocked"] == [{"task": "t_gone", "seq": 1}] and doc(D, K)["runs"] == []
    assign(client, D, K, tid, seq=1)  # 目标变了（seq 变）：再试
    assert secs(live_sessions()) == 600


def test_finish_failure_and_exhausted_tries_are_logged(client, tid, monkeypatch, caplog):
    tiers(client)
    upload(client, [rec(D, "09:00:00", "09:10:00", title="周报")])
    put_doc(D, K, tid)
    monkeypatch.setattr(reconcile_repo, "finish", lambda *a, **k: False)
    with caplog.at_level(logging.WARNING, logger="uvicorn.error"):
        rnd()
    assert any("收尾没成" in r.getMessage() for r in caplog.records)
    monkeypatch.undo()
    caplog.clear()
    db().activity_blocks.delete_many({})
    put_doc(D, K, tid)
    monkeypatch.setattr(reconcile_repo, "commit", lambda *a, **k: None)
    with caplog.at_level(logging.WARNING, logger="uvicorn.error"):
        rnd()
    assert any("连续" in r.getMessage() for r in caplog.records)


def test_tiers_off_settles_unfinished_intent_but_plans_nothing_new(client, tid, monkeypatch):
    from app.modules.activity import reconcile_events
    tiers(client)
    upload(client, [rec(D, "09:00:00", "09:10:00", title="周报"), rec(D, "10:00:00", "10:10:00", title="另一个")])
    put_doc(D, K, tid)
    monkeypatch.setattr(reconcile_events, "write_completed", lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        rnd(only={(D, K)})
    monkeypatch.undo()
    assert ev() == [] and doc(D, K)["runs"][0]["pend"] == "w"
    put_doc(D, "chrome · 另一个", tid)  # 没写过的块
    db().activity_settings.update_one({"user": "u_local"}, {"$set": {"tiers": False}})
    assert reconcile.run_all() == 1  # 关了：只把已写的意图收口
    assert len(ev()) == 1 and doc(D, K)["runs"][0].get("pend") is None and not doc(D, K).get("reconciling")
    assert doc(D, "chrome · 另一个")["runs"] == []
    assert reconcile.run_all() == 0
