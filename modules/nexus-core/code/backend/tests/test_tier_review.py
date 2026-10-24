"""三级分类第二部分 B 审查补强：幂等、AI 章与快照、原子撤回、落地后失败、占位恢复、同块作废、隐私、对账原因。"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

import pytest
from _reconcile_common import rnd, tiers
from _tier_common import BLK, RULES, SET, at, day, db, one, rec, upload
from app.modules import activity  # noqa: F401
from app.modules.activity import direct_log, new_items, reconcile, tier_items

P = "/api/core/activity/proposals"
LOG = "/api/core/activity/direct-log"
D = day(-1)
K = "chrome · 周报"
WRITE = {"X-Nexus-Scope": "write"}
READ = {"X-Nexus-Scope": "read"}


@pytest.fixture()
def tid(seeded):
    return seeded["tasks"]["示例任务三"]["id"]


@pytest.fixture()
def blk(client):
    tiers(client)
    upload(client, [rec(D, "09:00:00", "09:10:00", title="周报"), rec(D, "10:00:00", "10:20:00", title="另一个")])


def direct_on(client):
    v = client.get(SET).json()["version"]
    client.put(SET, json={"tiers": True, "agentDirect": {"enabled": True, "windowsClassify": True, "honeycombRecords": True}}, headers={"If-Match": f'"{v}"'})


def wr(client, op="add", rule=None, **kw):
    return client.post(f"{P}/window-rules", json={"op": op, "rule": rule or {"app": "chrome", "title": "secret.*", "block": "浏览器"}, **kw}, **({"headers": kw.pop("headers")} if "headers" in kw else {}))


def ba(client, action="assign", key=K, **kw):
    return client.post(f"{P}/block-actions", json={"action": action, "date": D, "blockKey": key, **kw})


def rules():
    return (db().detector_rules.find_one({"user": "u_local"}) or {"rules": []})["rules"]


def test_window_edit_validated_422_stays_pending(client):
    a = wr(client).json()
    client.post(f"{P}/{a['id']}/approve")
    rid = rules()[0]["id"]
    e = client.post(f"{P}/window-rules", json={"op": "edit", "rule": {"id": rid, "block": "ok"}}).json()
    r = client.post(f"{P}/{e['id']}/approve", json={"edit": {"rule": {"block": "x" * 65}}})
    assert r.status_code == 422
    r = client.post(f"{P}/{e['id']}/approve", json={"edit": {"rule": {"block": "   "}}})
    assert r.status_code == 422
    assert db().activity_new_items.find_one({"id": e["id"]})["status"] == "pending" and rules()[0]["block"] == "浏览器"


def test_window_add_idempotent_direct_and_approve(client):
    direct_on(client)
    p = wr(client).json()  # 待批准
    body = {"op": "add", "rule": {"app": "chrome", "title": "secret.*", "block": "浏览器"}, "directApply": True}
    r1 = client.post(f"{P}/window-rules", json=body, headers=WRITE).json()
    r2 = client.post(f"{P}/window-rules", json=body, headers=WRITE).json()
    assert len(rules()) == 1 and r1["result"]["ruleId"] == r2["result"]["ruleId"]
    assert db().activity_new_items.find_one({"id": p["id"]})["status"] == "accepted"  # 同 id 的待批准提议记成已批准
    v = rules and db().detector_rules.find_one({"user": "u_local"})["version"]
    assert client.post(f"{P}/{p['id']}/approve").json()["status"] == "accepted"
    assert len(rules()) == 1 and db().detector_rules.find_one({"user": "u_local"})["version"] == v
    # 再提 → 换新的 pending，批准也不重复
    again = wr(client).json()
    out = client.post(f"{P}/{again['id']}/approve").json()
    assert len(rules()) == 1 and out["createdId"] == rules()[0]["id"]


def test_ai_stamp_and_snapshot_revert(client):
    a = wr(client).json()
    client.post(f"{P}/{a['id']}/approve")
    r = rules()[0]
    assert r["author"] == "assistant" and r["aiAt"]
    doc = db().detector_rules.find_one({"user": "u_local"})
    assert len(doc["snapshots"]) == 1 and doc["snapshots"][0]["rules"] == []
    out = client.post(f"{RULES}/ai-revert", headers={"If-Match": f'"{doc["version"]}"'})
    assert out.status_code == 200 and rules() == []


def test_direct_log_pending_hidden_and_failed_removed(client, tid, blk):
    direct_on(client)
    assert ba(client, taskId=tid, directApply=True).status_code == 403  # 人：needWriteScope，没留记录
    ok = client.post(f"{P}/block-actions", headers=WRITE, json={"action": "assign", "date": D, "blockKey": K, "taskId": tid, "directApply": True})
    assert ok.status_code == 200
    bad = client.post(f"{P}/block-actions", headers=WRITE, json={"action": "ignore", "date": D, "blockKey": K, "directApply": True})
    assert bad.status_code == 409 and db().activity_direct_log.count_documents({}) == 1  # 失败的记录删掉
    db().activity_direct_log.insert_one({"user": "u_local", "id": "dl_p", "at": datetime.now(timezone.utc), "actor": "ai", "kind": "blockRule",
                                         "payload": {}, "state": "pending", "before": None, "after": None, "undoneAt": None})
    assert [i["id"] for i in client.get(LOG).json()["items"]] == [ok.json()["directLogId"]]


def test_undo_block_atomic_and_generic_since(client, tid, blk, monkeypatch):
    direct_on(client)
    # 块原来就是通用窗口（since = 某天）：撤回后 since 不变
    db().activity_generic.insert_one({"user": "u_local", "blockKey": K, "since": "2020-01-01"})
    r = client.post(f"{P}/block-actions", headers=WRITE, json={"action": "assign", "date": D, "blockKey": K, "taskId": tid, "directApply": True}).json()
    # 撤回时块文档写不进（被别处改）→ 409，通用窗口标记原样、不留撤回标记
    from app.modules.activity import blocks_repo
    real = blocks_repo.put_block
    monkeypatch.setattr(blocks_repo, "put_block", lambda *a, **k: None)
    assert client.post(f"{LOG}/{r['directLogId']}/undo").status_code == 409
    monkeypatch.setattr(blocks_repo, "put_block", real)
    assert db().activity_direct_log.find_one({"id": r["directLogId"]})["undoneAt"] is None
    assert one(client, D, K)["status"] == "generic"
    assert client.post(f"{LOG}/{r['directLogId']}/undo").status_code == 200
    assert db().activity_generic.find_one({"blockKey": K})["since"] == "2020-01-01"
    # 直接标的通用窗口：撤回后标记没了
    g = client.post(f"{P}/block-actions", headers=WRITE, json={"action": "generic", "date": D, "blockKey": "chrome · 另一个", "directApply": True}).json()
    assert db().activity_generic.count_documents({"blockKey": "chrome · 另一个"}) == 1
    assert client.post(f"{LOG}/{g['directLogId']}/undo").status_code == 200
    assert db().activity_generic.count_documents({"blockKey": "chrome · 另一个"}) == 0


def test_post_landing_failure_does_not_revert_to_pending(client, tid, blk, monkeypatch):
    a = ba(client, taskId=tid).json()
    real, n = direct_log.block_state, {"n": 0}

    def flaky(*args):
        n["n"] += 1
        if n["n"] == 3:  # 批准里的第三次读（落地之后）出错
            raise RuntimeError("boom")
        return real(*args)

    monkeypatch.setattr(tier_items.direct_log, "block_state", flaky)
    r = client.post(f"{P}/{a['id']}/approve")
    assert r.status_code == 200 and r.json()["status"] == "accepted"
    assert one(client, D, K)["status"] == "assigned" and db().activity_new_items.find_one({"id": a["id"]})["applyClaimedAt"] is None


def test_stale_claim_is_taken_over(client, tid, blk):
    a = ba(client, taskId=tid).json()
    old = datetime.now(timezone.utc) - timedelta(seconds=3600)
    db().activity_new_items.update_one({"id": a["id"]}, {"$set": {"status": "accepted", "applyClaimedAt": old, "decidedAt": old}})
    r = client.post(f"{P}/{a['id']}/approve")
    assert r.status_code == 200 and r.json()["status"] == "accepted"
    assert one(client, D, K)["via"] == "ai"
    assert db().activity_new_items.find_one({"id": a["id"]})["applyClaimedAt"] is None


def test_ai_action_supersedes_others_on_same_block(client, tid, blk):
    direct_on(client)
    a, b = ba(client, taskId=tid).json(), ba(client, "ignore").json()
    other = ba(client, "ignore", key="chrome · 另一个").json()
    assert client.post(f"{P}/{a['id']}/approve").status_code == 200
    st = {i["id"]: i["status"] for i in client.get(P, params={"status": "all"}).json()["blockActions"]}
    assert st == {a["id"]: "accepted", b["id"]: "superseded", other["id"]: "pending"}
    # 直接施加同样
    c = client.post(f"{P}/block-actions", json={"action": "assign", "date": D, "blockKey": "chrome · 另一个", "taskId": tid}).json()
    assert client.post(f"{P}/block-actions", headers=WRITE, json={"action": "assign", "date": D, "blockKey": "chrome · 另一个", "taskId": tid,
                                                                    "directApply": True}).status_code == 200
    st = {i["id"]: i["status"] for i in client.get(P, params={"status": "all"}).json()["blockActions"]}
    assert st[c["id"]] == "accepted" and st[other["id"]] == "superseded"


def test_privacy_title_masked_for_non_human(client):
    direct_on(client)
    a = wr(client).json()
    assert a["rule"]["title"] == "secret.*"
    got = client.get(P, headers=READ).json()["windowRules"][0]
    assert "title" not in got["rule"] and got["rule"]["hasTitle"] is True and got["rule"]["app"] == "chrome"
    assert client.get(P).json()["windowRules"][0]["rule"]["title"] == "secret.*"
    w = client.post(f"{P}/window-rules", headers=WRITE, json={"op": "add", "rule": {"title": "t.*", "block": "x"}})
    assert "title" not in w.json()["rule"] and w.json()["rule"]["hasTitle"] is True
    d = client.post(f"{P}/window-rules", headers=WRITE, json={"op": "add", "rule": {"title": "zz", "block": "y"}, "directApply": True}).json()
    assert "title" not in d["result"]["rule"]
    item = client.get(LOG, headers=READ).json()["items"][0]
    assert "title" not in item["payload"]["rule"] and item["payload"]["rule"]["hasTitle"] is True
    assert client.get(LOG).json()["items"][0]["payload"]["rule"]["title"] == "zz"


def test_human_write_during_claim_marks_superseded(client, tid, blk, monkeypatch):
    a = ba(client, taskId=tid).json()
    real = tier_items.apply

    def racing(*args, **kw):  # 批准占了位之后、落地之前，人动了这个块（人的写看不见占位中的提议）
        assert client.post(f"{BLK}/ignore", json={"date": D, "blockKey": K, "forever": False, "ifSeq": 0}).status_code == 200  # v2.39：assign 同目标已是空操作，改用 ignore
        return real(*args, **kw)

    monkeypatch.setattr(tier_items, "apply", racing)
    r = client.post(f"{P}/{a['id']}/approve")
    assert r.status_code == 409
    assert db().activity_new_items.find_one({"id": a["id"]})["status"] == "superseded"


def test_human_assign_other_target_during_claim_marks_superseded(client, tid, blk, monkeypatch, seeded):
    other = next(t["id"] for n, t in seeded["tasks"].items() if t["id"] != tid)
    a = ba(client, taskId=tid).json()
    real = tier_items.apply

    def racing(*args, **kw):  # 占位之后人把块分到了另一个任务：AI 的 assign 不许盖过去
        assert client.post(f"{BLK}/assign", json={"date": D, "blockKey": K, "taskId": other, "ifSeq": 0}).status_code == 200
        return real(*args, **kw)

    monkeypatch.setattr(tier_items, "apply", racing)
    assert client.post(f"{P}/{a['id']}/approve").status_code == 409
    assert db().activity_new_items.find_one({"id": a["id"]})["status"] == "superseded"
    assert one(client, D, K)["status"] == "assigned" and db().activity_blocks.find_one({"blockKey": K})["parts"][0]["taskId"] == other


def test_undo_reason_survives_skipped_kick(client, tid, blk):
    direct_on(client)
    r = client.post(f"{P}/block-actions", headers=WRITE, json={"action": "assign", "date": D, "blockKey": K, "taskId": tid, "directApply": True}).json()
    rnd()
    lock = reconcile._locks.setdefault("u_local", threading.Lock())
    with lock:  # 对账正占着锁：撤回触发的 kick 被跳过
        assert client.post(f"{LOG}/{r['directLogId']}/undo").status_code == 200
    assert db().events.count_documents({"type": "session.voided"}) == 0
    rnd()
    assert [v["data"]["reason"] for v in db().events.find({"type": "session.voided"})] == ["direct-undo"]


def test_direct_change_failure_cleans_up_and_skips_cap(client, seeded, monkeypatch):
    direct_on(client)
    t = db().tasks.find_one({"kind": {"$ne": "unclassified"}})
    monkeypatch.setitem(new_items.MAX_PENDING, "change", 1)
    other = db().tasks.find_one({"kind": {"$ne": "unclassified"}, "id": {"$ne": t["id"]}})
    assert client.post(f"{P}/changes", json={"target": "task", "targetId": other["id"], "op": "rename", "name": "占名额"}).status_code == 200  # 待批准已满
    from app.modules.activity.proposals import ConflictError

    def boom(*a, **k):
        raise ConflictError("落不下")

    monkeypatch.setattr(new_items.planner_service, "update_task", boom)
    body = {"target": "task", "targetId": t["id"], "op": "rename", "name": "直改", "directApply": True}
    assert client.post(f"{P}/changes", headers=WRITE, json=body).status_code == 409  # 不是 429：直接施加不占名额
    assert db().activity_new_items.count_documents({"target": "task", "targetId": t["id"]}) == 0
    assert db().activity_direct_log.count_documents({}) == 0
