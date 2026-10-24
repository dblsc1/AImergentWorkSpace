"""窗口块对账（契约 v2.38「四」）：补写、收口、迟到、跨设备、扣重叠、改目标、作废、gen。"""

from __future__ import annotations

import pytest
from _reconcile_common import assign, doc, ev, live_sessions, rnd, secs, tiers
from _tier_common import BLK, day, one, rec, upload

D = day(-1)
K = "chrome · 周报"


@pytest.fixture()
def tid(seeded):
    return seeded["tasks"]["示例任务三"]["id"]


def test_assigned_block_is_written_once(client, tid):
    tiers(client)
    upload(client, [rec(D, "09:00:00", "09:10:00", title="周报"), rec(D, "09:12:00", "09:20:00", title="周报")])
    assign(client, D, K, tid)  # 块的写操作触发一轮
    got = live_sessions()
    assert len(got) == 1 and got[0]["subject"]["task"] == tid
    assert got[0]["data"]["durationSeconds"] == 18 * 60 and got[0]["data"]["mode"] == "do"
    assert got[0]["ai"] == {"generated": True, "confirmed": True, "block": {"date": D, "blockKey": K}}
    d = doc(D, K)
    assert [r["eventId"] for r in d["runs"]] == [got[0]["id"]] and d["gen"] == 1 and not d.get("reconciling")
    assert got[0]["dedupeKey"].startswith(f"block:{D}:") and got[0]["dedupeKey"].endswith(f":1:{d['runs'][0]['startTs']}")
    assert rnd() == 0 and len(ev()) == 1  # 再来一轮什么都不写
    from _tier_common import db
    assert db().activity_records.count_documents({"ledgerEventId": got[0]["id"]}) == 2
    assert client.get("/api/core/views/current").status_code == 200


def test_gap_over_300_makes_two_runs_and_rounding(client, tid):
    tiers(client)
    upload(client, [rec(D, "09:00:00", "09:00:01", title="周报"), rec(D, "09:05:01", "09:05:02", title="周报"),
                    rec(D, "09:11:00", "09:11:03", title="周报")])
    assign(client, D, K, tid)
    got = live_sessions()
    # 间隔 300 秒整（09:00:01→09:05:01）连成一段；再隔 358 秒另起一段
    assert sorted(e["data"]["durationSeconds"] for e in got) == [2, 3]


def test_not_closed_not_written(client, tid):
    from datetime import datetime
    from _tier_common import TZ, day as dd
    tiers(client)
    now = datetime.now(TZ)
    today = dd(0)
    if now.hour < 1:  # 刚过午夜：2 分钟前可能在昨天，用例无意义
        pytest.skip("midnight")
    from datetime import timedelta
    s, e = now - timedelta(minutes=4), now - timedelta(minutes=2)
    upload(client, [{"startAt": s.isoformat(), "endAt": e.isoformat(), "app": "chrome", "title": "周报", "titleIsPseudonym": False, "block": None}])
    assign(client, today, K, tid)
    assert ev() == []  # 段末 + 300 秒还没到


def _backfill(client, tid, start, dur):
    r = client.post("/api/core/timer/backfill", json={"taskId": tid, "startAt": start.isoformat(), "durationSeconds": dur})
    assert r.status_code == 200, r.text


def _spans(events):
    from datetime import datetime
    return sorted((datetime.fromisoformat(e["data"]["startAt"]), e["data"]["durationSeconds"]) for e in events)


def test_late_record_is_a_new_run_old_run_untouched(client, tid):
    tiers(client)
    upload(client, [rec(D, "09:00:00", "09:10:00", title="周报")])
    assign(client, D, K, tid)
    first = live_sessions()
    assert len(first) == 1
    upload(client, [rec(D, "09:11:00", "09:12:00", title="周报")], device="dev_late")  # 离线补传：距旧段只差 60 秒
    got = live_sessions()
    assert len(got) == 2 and first[0]["id"] in {e["id"] for e in got}
    old = next(e for e in got if e["id"] == first[0]["id"])
    assert old == first[0] and secs(got) == 11 * 60  # 旧事件原样，迟到的 60 秒另成一条
    assert len(doc(D, K)["runs"]) == 2


def test_cross_device_union(client, tid):
    tiers(client)
    upload(client, [rec(D, "09:00:00", "09:10:00", title="周报")])
    upload(client, [rec(D, "09:05:00", "09:15:00", title="周报")], device="dev_b")
    assign(client, D, K, tid)
    got = live_sessions()
    assert len(got) == 1 and got[0]["data"]["durationSeconds"] == 15 * 60


def test_other_sources_are_deducted_and_cut_at_boundaries(client, tid):
    from app.modules.timer import service as ts
    from _tier_common import at
    tiers(client)
    _backfill(client, tid, at(D, "09:03:00"), 120)  # 手动补登 09:03–09:05
    ts.record_session(tid, at(D, "09:08:00").isoformat(), at(D, "09:09:00").isoformat(), 60, source="activity-confirmed",
                      dedupe_key="activity:zz", ai={"generated": True, "confirmed": True})
    upload(client, [rec(D, "09:00:00", "09:10:00", title="周报")])
    assign(client, D, K, tid)
    got = live_sessions()
    # 09:00–09:03、09:05–09:08、09:09–09:10 三段，间隔 ≤ 300 秒 → 连成一段，时长 180+180+60
    assert len(got) == 1 and got[0]["data"]["durationSeconds"] == 420
    assert doc(D, K)["runs"][0]["ivs"] == [[ev_ms(D, "09:00:00"), ev_ms(D, "09:03:00")], [ev_ms(D, "09:05:00"), ev_ms(D, "09:08:00")],
                                           [ev_ms(D, "09:09:00"), ev_ms(D, "09:10:00")]]


def ev_ms(date, hms):
    from _tier_common import at
    return round(at(date, hms).timestamp() * 1000)


def test_running_timer_is_deducted(client, tid):
    from datetime import datetime, timedelta
    from _tier_common import TZ, db
    tiers(client)
    upload(client, [rec(D, "09:00:00", "09:10:00", title="周报")])
    # 一个"正在跑"的手动计时，开始于 09:06（昨天的时刻，直接写活状态）
    db().timer_state.insert_one({"user": "u_local", "sessionId": "s", "taskId": tid, "projectId": "p", "zoneId": "z",
                                 "startAt": ev_iso(D, "09:06:00"), "mode": "do"})
    assign(client, D, K, tid)
    got = live_sessions()
    assert len(got) == 1 and got[0]["data"]["durationSeconds"] == 360


def ev_iso(date, hms):
    from _tier_common import at
    return at(date, hms).isoformat()


def test_later_manual_backfill_voids_written_run_and_rewrites_remainder(client, tid):
    tiers(client)
    upload(client, [rec(D, "09:00:00", "09:10:00", title="周报")])
    assign(client, D, K, tid)
    old = live_sessions()[0]
    from _tier_common import at
    _backfill(client, tid, at(D, "09:04:00"), 120)  # 后来补的 09:04–09:06 盖住了它
    voids = ev("session.voided")
    assert len(voids) == 1 and voids[0]["data"]["voidedEventId"] == old["id"] and voids[0]["data"]["reason"] == "manual-overlap"
    assert voids[0]["dedupeKey"] == f"void:{old['id']}" and voids[0]["subject"] == old["subject"]
    got = live_sessions()
    assert len(got) == 1 and got[0]["data"]["durationSeconds"] == 480 and got[0]["id"] != old["id"]
    assert doc(D, K)["gen"] == 2 and ":2:" in got[0]["dedupeKey"]


def test_changing_target_reassigns_not_voids(client, seeded, tid):
    tiers(client)
    other = seeded["tasks"]["示例任务一"]["id"] if "示例任务一" in seeded["tasks"] else next(i["id"] for n, i in seeded["tasks"].items() if i["id"] != tid)
    upload(client, [rec(D, "09:00:00", "09:10:00", title="周报")])
    b = assign(client, D, K, tid)
    old = live_sessions()[0]
    assign(client, D, K, other, seq=b["seq"])
    assert len(live_sessions()) == 1 and live_sessions()[0]["id"] == old["id"] and ev("session.voided") == []
    re = ev("session.reassigned", "session-reassign")
    assert len(re) == 1 and re[0]["data"]["toTaskId"] == other and re[0]["data"]["actor"] == "human" and re[0]["subject"]["task"] == other
    assert doc(D, K)["runs"][0]["task"] == other and rnd() == 0


def test_split_voids_and_rewrites_and_split_reset_assign_loses_no_time(client, tid, seeded):
    from _tier_common import at
    tiers(client)
    other = next(i["id"] for n, i in seeded["tasks"].items() if i["id"] != tid)
    upload(client, [rec(D, "09:00:00", "09:10:00", title="周报"), rec(D, "09:20:00", "09:30:00", title="周报")])
    b = assign(client, D, K, tid)
    assert secs(live_sessions()) == 1200
    cut = ev_ms(D, "09:15:00")
    r = client.post(f"{BLK}/split", json={"date": D, "blockKey": K, "ifSeq": b["seq"],
                                          "parts": [{"toTs": cut, "taskId": tid}, {"fromTs": cut, "taskId": other}]})
    assert r.status_code == 200, r.text
    assert [v["data"]["reason"] for v in ev("session.voided")] == ["block-split", "block-split"] or len(ev("session.voided")) >= 1
    got = live_sessions()
    assert sorted((e["subject"]["task"], e["data"]["durationSeconds"]) for e in got) == sorted([(tid, 600), (other, 600)])
    g2 = doc(D, K)["gen"]
    # 撤销拆分（reset）→ 再整块分一次：时间一秒不少，键不撞已作废的
    r = client.post(f"{BLK}/reset", json={"date": D, "blockKey": K, "ifSeq": r.json()["seq"]})
    assert secs(live_sessions()) == 0 and doc(D, K)["gen"] > g2
    assert {v["data"]["reason"] for v in ev("session.voided")} >= {"block-split", "block-reset"}
    assign(client, D, K, tid, seq=r.json()["seq"])
    assert secs(live_sessions()) == 1200 and len({e["dedupeKey"] for e in ev()}) == len(ev())


@pytest.mark.parametrize(("op", "reason"), [("ignore", "block-ignored"), ("reset", "block-reset")])
def test_ignore_and_reset_void_everything(client, tid, op, reason):
    tiers(client)
    upload(client, [rec(D, "09:00:00", "09:10:00", title="周报")])
    b = assign(client, D, K, tid)
    assert secs(live_sessions()) == 600
    r = client.post(f"{BLK}/{op}", json={"date": D, "blockKey": K, "ifSeq": b["seq"]})
    assert r.status_code == 200
    assert live_sessions() == [] and [v["data"]["reason"] for v in ev("session.voided")] == [reason]
    assert doc(D, K)["runs"] == [] and one(client, D, K)["status"] in ("ignored", "pending")
    from _tier_common import db
    assert db().activity_records.count_documents({"ledgerEventId": {"$ne": None}}) == 0


def test_rule_deleted_or_broken_voids_and_rule_blocks_get_a_doc(client, tid):
    tiers(client)
    upload(client, [rec(D, "09:00:00", "09:10:00", title="周报")])
    r = client.post("/api/core/activity/block-rules", json={"blockKey": K, "taskId": tid})
    assert r.status_code == 201, r.text
    got = live_sessions()
    assert len(got) == 1 and got[0]["ai"]["auto"] is True and got[0]["ai"]["confirmed"] is False  # 规则：没有人确认
    d = doc(D, K)
    assert d["status"] == "assigned" and d["via"] == "rule" and d["ruleId"] == r.json()["id"] and d["parts"][0]["taskId"] == tid
    assert one(client, D, K)["status"] == "assigned"
    assert client.delete(f"/api/core/activity/block-rules/{r.json()['id']}").status_code == 204
    assert live_sessions() == [] and [v["data"]["reason"] for v in ev("session.voided")] == ["block-rule"]
    d = doc(D, K)
    assert d["status"] is None and d["via"] is None and one(client, D, K)["status"] == "pending"
    # 规则又回来：新 gen，时间不丢
    client.post("/api/core/activity/block-rules", json={"blockKey": K, "taskId": tid})
    assert secs(live_sessions()) == 600
    # 目标任务被删 → 规则失效 → 下一轮作废
    assert client.delete(f"/api/core/planner/tasks/{tid}").status_code in (200, 204)
    rnd()
    assert live_sessions() == [] and ev("session.voided")[-1]["data"]["reason"] == "block-rule"


def test_rule_target_change_reassigns(client, tid, seeded):
    tiers(client)
    other = next(i["id"] for n, i in seeded["tasks"].items() if i["id"] != tid)
    upload(client, [rec(D, "09:00:00", "09:10:00", title="周报")])
    client.post("/api/core/activity/block-rules", json={"blockKey": K, "taskId": tid})
    client.post("/api/core/activity/block-rules", json={"blockKey": K, "taskId": other})
    assert len(live_sessions()) == 1 and ev("session.voided") == []
    assert ev("session.reassigned", "session-reassign")[0]["data"]["actor"] == "system"


def test_generic_mark_voids_and_unmark_restores(client, tid):
    tiers(client)
    upload(client, [rec(D, "09:00:00", "09:10:00", title="周报")])
    client.post("/api/core/activity/block-rules", json={"blockKey": K, "taskId": tid})
    assert secs(live_sessions()) == 600
    from _tier_common import db
    db().activity_generic.delete_many({})
    r = client.post(f"{BLK}/generic", json={"blockKey": K, "generic": True})
    assert r.status_code == 200
    # since = 今天，D = 昨天：通用窗口只管 since 之后；昨天的不受影响
    assert secs(live_sessions()) == 600
    db().activity_generic.update_one({"blockKey": K}, {"$set": {"since": D}})
    rnd()
    assert live_sessions() == [] and ev("session.voided")[-1]["data"]["reason"] == "block-generic"
    client.post(f"{BLK}/generic", json={"blockKey": K, "generic": False})
    assert secs(live_sessions()) == 600


def test_records_before_tiers_since_are_not_recorded(client, tid):
    tiers(client, since_days=0)  # tiersSince = 现在：昨天的记录都在它之前
    upload(client, [rec(D, "09:00:00", "09:10:00", title="周报")])
    assign(client, D, K, tid)
    assert ev() == []


def test_off_on_off_on(client, tid):
    from _tier_common import db
    tiers(client)
    upload(client, [rec(D, "09:00:00", "09:10:00", title="周报")])
    assign(client, D, K, tid)
    assert secs(live_sessions()) == 600
    tiers(client, on=False)
    upload(client, [rec(D, "11:00:00", "11:10:00", title="周报")])
    assert rnd() == 0 and secs(live_sessions()) == 600  # 关着：不对账，已入账的不动
    tiers(client, on=True, since_days=0)  # 再开：tiersSince 更新为此刻，关着期间的记录不入账
    assert secs(live_sessions()) == 600
    since = db().activity_settings.find_one({"user": "u_local"})["tiersSince"]
    upload(client, [rec(D, "13:00:00", "13:10:00", title="周报")])
    assert secs(live_sessions()) == 600 and since is not None
    tiers(client, on=False)
    tiers(client, on=True, since_days=30)  # 拨回过去：旧记录这时才算数
    assert secs(live_sessions()) == 1800


def test_cas_conflict_retries(client, tid, monkeypatch):
    from app.modules.activity import reconcile_repo
    tiers(client)
    upload(client, [rec(D, "09:00:00", "09:10:00", title="周报")])
    real, hit = reconcile_repo.commit, []

    def racing(user, date, key, old, fields, inc_seq, inc_gen):
        if not hit:  # 第一次提交前，人刚改了块（seq 变了）
            hit.append(1)
            from _tier_common import db
            db().activity_blocks.update_one({"date": date, "blockKey": key}, {"$inc": {"seq": 1}})
        return real(user, date, key, old, fields, inc_seq, inc_gen)

    b = assign(client, D, K, tid)  # 先建好文档与一次对账
    # 制造需要再对账的差：后来补一条记录
    monkeypatch.setattr(reconcile_repo, "commit", racing)
    upload(client, [rec(D, "10:00:00", "10:05:00", title="周报")])
    assert hit and secs(live_sessions()) == 900 and len(live_sessions()) == 2
    assert doc(D, K)["seq"] == b["seq"] + 1


def test_bounded_per_round(client, tid, monkeypatch):
    from app.modules.activity import reconcile
    tiers(client)
    for i in range(5):
        upload(client, [rec(D, f"0{i + 1}:00:00", f"0{i + 1}:10:00", title=f"文档{i}")])
        client.post("/api/core/activity/block-rules", json={"blockKey": f"chrome · 文档{i}", "taskId": tid})
    assert len(live_sessions()) == 5
    from _tier_common import db
    db().events.delete_many({})
    db().activity_blocks.update_many({}, {"$set": {"runs": []}})
    db().activity_records.update_many({}, {"$set": {"ledgerEventId": None}})
    monkeypatch.setattr(reconcile, "MAX_BLOCKS", 2)
    assert rnd() == 2 and len(ev()) == 2
    assert rnd() == 2 and rnd() == 1 and len(ev()) == 5 and rnd() == 0
    monkeypatch.setattr(reconcile, "MAX_BLOCKS", 500)
    db().events.delete_many({})
    db().activity_blocks.update_many({}, {"$set": {"runs": []}})
    db().activity_records.update_many({}, {"$set": {"ledgerEventId": None}})
    monkeypatch.setattr(reconcile, "MAX_EVENTS", 3)
    assert rnd() == 3 and len(ev()) == 3
    assert rnd() == 2 and len(ev()) == 5 and not any(d.get("reconciling") for d in db().activity_blocks.find())


def test_unfinished_intent_is_completed_next_round(client, tid, monkeypatch):
    """意图写了、事件没写成（崩溃）：块带 reconciling，下一轮接着写，事件不重复。"""
    from app.modules.activity import reconcile_events
    tiers(client)
    upload(client, [rec(D, "09:00:00", "09:10:00", title="周报")])
    monkeypatch.setattr(reconcile_events, "write_completed", lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        reconcile_run_block_via_round(tid)
    assert ev() == [] and doc(D, K)["reconciling"] is True and one(client, D, K)["reconciling"] is True
    monkeypatch.undo()
    assert rnd() == 1 and len(ev()) == 1 and not doc(D, K).get("reconciling") and doc(D, K)["runs"][0].get("pend") is None


def reconcile_run_block_via_round(tid):
    from _reconcile_common import assign as _a  # noqa: F401
    from _tier_common import db
    from app.modules.activity import blocks_repo
    blocks_repo.put_block("u_local", D, K, {"status": "assigned", "parts": [{"fromTs": None, "toTs": None, "taskId": tid, "projectId": None}],
                                            "via": "human", "ruleId": None, "updatedAt": None}, 0, None)
    rnd()


def test_late_run_after_ledger_rule_old_block_keeps_doc(client, tid):
    """14 天外靠第二级规则分类的块：对账写段时建了文档，之后读时按文档，不再翻成 pending。"""
    from datetime import timedelta
    from _tier_common import db
    d20 = day(-20)
    tiers(client, since_days=40)
    upload(client, [rec(d20, "09:00:00", "09:10:00", title="周报")])
    client.post("/api/core/activity/block-rules", json={"blockKey": K, "taskId": tid})
    # 20 天前不在规则的 14 天影响范围里：没有文档就是 pending，对账什么都不写
    assert one(client, d20, K)["status"] == "pending" and ev() == []
    d3 = day(-3)
    upload(client, [rec(d3, "09:00:00", "09:10:00", title="周报")])
    assert len(live_sessions()) == 1 and doc(d3, K)["via"] == "rule"
    # 把这天的日期"变老"：搬到 20 天前（模拟时间流逝），读时按文档，仍是 assigned
    db().activity_blocks.update_one({"date": d3}, {"$set": {"date": day(-30)}})
    db().activity_records.update_many({"date": d3}, {"$set": {"date": day(-30)}})
    assert one(client, day(-30), K)["status"] == "assigned"


def test_read_endpoints_never_write_the_ledger(client, tid):
    from _tier_common import db
    tiers(client)
    upload(client, [rec(D, "09:00:00", "09:10:00", title="周报")])
    db().activity_blocks.insert_one({"user": "u_local", "date": D, "blockKey": K, "status": "assigned", "via": "human", "ruleId": None, "seq": 1,
                                     "gen": 1, "runs": [], "parts": [{"fromTs": None, "toTs": None, "taskId": tid, "projectId": None}]})
    for _ in range(2):
        client.get(BLK, params={"date": D})
        client.get(f"{BLK}/records", params={"date": D, "blockKey": K})
    assert ev() == []  # 读端点不写台账
    assert rnd() == 1 and len(ev()) == 1


def test_tiers_change_and_manual_timer_trigger_a_round(client, tid):
    from _tier_common import db, SET
    upload(client, [rec(D, "09:00:00", "09:10:00", title="周报")])
    db().activity_blocks.insert_one({"user": "u_local", "date": D, "blockKey": K, "status": "assigned", "via": "human", "ruleId": None, "seq": 1,
                                     "gen": 1, "runs": [], "parts": [{"fromTs": None, "toTs": None, "taskId": tid, "projectId": None}]})
    v = client.get(SET).json()["version"]
    from datetime import datetime, timedelta, timezone
    from _reconcile_common import OFF
    # PUT tiers:true 触发一轮——但 tiersSince = 此刻，昨天的记录不算：先拨回去再用手动计时触发
    client.put(SET, json={"tiers": True, "agentDirect": OFF}, headers={"If-Match": f'"{v}"'})
    db().activity_settings.update_one({"user": "u_local"}, {"$set": {"tiersSince": datetime.now(timezone.utc) - timedelta(days=30)}})
    assert ev() == []
    assert client.post("/api/core/timer/start", json={"taskId": tid}).status_code == 200  # 手动计时开始触发
    assert len(ev()) == 1
    client.post("/api/core/timer/cancel")


def test_loop_is_off_in_tests_and_runs_when_on(client, tid, monkeypatch):
    import dataclasses
    import time
    from app import config
    from app.modules.activity import reconcile_loop
    assert reconcile_loop.start() is None  # 测试环境 NEXUS_RECONCILE_LOOP=0
    monkeypatch.setattr(config, "settings", dataclasses.replace(config.settings, reconcile_loop=True))
    monkeypatch.setattr(reconcile_loop, "INTERVAL", 0.05)
    tiers(client)
    upload(client, [rec(D, "09:00:00", "09:10:00", title="周报")])
    from _tier_common import db
    db().activity_blocks.insert_one({"user": "u_local", "date": D, "blockKey": K, "status": "assigned", "via": "human", "ruleId": None, "seq": 1,
                                     "gen": 1, "runs": [], "parts": [{"fromTs": None, "toTs": None, "taskId": tid, "projectId": None}]})
    handle = reconcile_loop.start()
    try:
        for _ in range(100):
            if ev():
                break
            time.sleep(0.05)
    finally:
        reconcile_loop.stop(handle)
    assert len(ev()) == 1 and not handle[0].is_alive()
