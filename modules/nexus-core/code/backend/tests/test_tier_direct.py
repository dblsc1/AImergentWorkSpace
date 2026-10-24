"""三级分类第二部分 B：直接施加（agentDirect）、直接施加日志、撤回。"""

from __future__ import annotations

from _reconcile_common import OFF, live_sessions, tiers
from _tier_common import BLK, SET, db, day, one, rec, upload
from app.modules.activity import tier_items
import pytest

P = "/api/core/activity/proposals"
LOG = "/api/core/activity/direct-log"
D = day(-1)
K = "chrome · 周报"
WRITE = {"X-Nexus-Scope": "write"}


@pytest.fixture()
def tid(seeded):
    return seeded["tasks"]["示例任务三"]["id"]


@pytest.fixture()
def blk(client):
    tiers(client)
    upload(client, [rec(D, "09:00:00", "09:10:00", title="周报")])


def direct(client, enabled=True, win=True, hc=True):
    v = client.get(SET).json()
    r = client.put(SET, json={"tiers": True, "agentDirect": {"enabled": enabled, "windowsClassify": win, "honeycombRecords": hc}},
                   headers={"If-Match": f'"{v["version"]}"'})
    assert r.status_code == 200, r.text


def act(client, headers=WRITE, **kw):
    return client.post(f"{P}/block-actions", headers=headers, json={"action": "assign", "date": D, "blockKey": K, "directApply": True, **kw})


def test_403_reasons(client, tid, blk):
    body = {"taskId": tid}
    r = act(client, **body)
    assert r.status_code == 403 and r.json()["reason"] == "agentDirectOff" and "agentDirect" in r.json()["detail"]
    direct(client, True, False, False)
    assert act(client, **body).json()["reason"] == "windowsClassifyOff"
    direct(client, True, True, False)
    assert act(client, headers={}, **body).json()["reason"] == "needWriteScope"  # 人
    assert act(client, headers={"X-Nexus-Scope": "read"}, **body).status_code == 403
    r = client.post(f"{P}/changes", headers=WRITE, json={"target": "task", "targetId": tid, "op": "delete", "directApply": True})
    assert r.status_code == 403 and r.json()["reason"] == "honeycombRecordsOff"
    assert one(client, D, K)["status"] == "pending" and db().activity_direct_log.count_documents({}) == 0
    for url, b in (("window-rules", {"op": "add", "rule": {"app": "a", "block": "b"}}), ("block-rules", {"op": "add", "blockKey": "k", "taskId": tid})):
        assert client.post(f"{P}/{url}", headers={}, json={**b, "directApply": True}).json()["reason"] == "needWriteScope"


def test_direct_block_action_log_and_undo(client, tid, blk):
    direct(client)
    r = act(client, taskId=tid)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["applied"] is True and out["directLogId"].startswith("dl_") and out["result"]["status"] == "assigned"
    b = one(client, D, K)
    assert b["via"] == "ai-direct" and db().activity_new_items.count_documents({}) == 0
    ev = live_sessions()
    assert len(ev) == 1 and ev[0]["ai"]["confirmed"] is False and ev[0]["ai"]["auto"] is True
    # v2.39：已分类的块再 assign = 改去向（见 test_tier3）；其它动作（ignore / generic）仍 409
    for body in ({"action": "ignore"}, {"action": "generic"}):
        r = client.post(f"{P}/block-actions", headers=WRITE, json={**body, "date": D, "blockKey": K, "directApply": True})
        assert r.status_code == 409, body
    lst = client.get(LOG).json()["items"]
    assert len(lst) == 1 and lst[0]["id"] == out["directLogId"] and lst[0]["kind"] == "blockAction" and lst[0]["actor"] == "ai"
    assert "before" not in lst[0] and lst[0]["undoneAt"] is None and lst[0]["payload"]["blockKey"] == K
    # 非人不能撤回
    for h in (WRITE, {"Authorization": "Bearer t"}):
        assert client.post(f"{LOG}/{out['directLogId']}/undo", headers=h).status_code == 403
    r = client.post(f"{LOG}/{out['directLogId']}/undo")
    assert r.status_code == 200 and r.json()["undoneAt"]
    assert one(client, D, K)["status"] == "pending" and live_sessions() == []
    voids = list(db().events.find({"type": "session.voided"}))
    assert [v["data"]["reason"] for v in voids] == ["direct-undo"]
    assert client.post(f"{LOG}/{out['directLogId']}/undo").status_code == 409  # 已撤回
    assert client.get(LOG).json()["items"][0]["undoneAt"]
    assert client.post(f"{LOG}/dl_nope/undo").status_code == 404


def test_undo_409_when_overwritten(client, tid, blk):
    direct(client)
    lid = act(client, taskId=tid).json()["directLogId"]
    seq = one(client, D, K)["seq"]
    assert client.post(f"{BLK}/reset", json={"date": D, "blockKey": K, "ifSeq": seq}).status_code == 200
    assert client.post(f"{BLK}/ignore", json={"date": D, "blockKey": K, "ifSeq": seq + 1}).status_code == 200
    assert client.post(f"{LOG}/{lid}/undo").status_code == 409
    assert one(client, D, K)["status"] == "ignored"
    assert db().activity_direct_log.find_one({"id": lid})["undoneAt"] is None  # 失败不留撤回标记


def test_direct_window_rule_and_undo(client):
    direct(client)
    body = {"op": "add", "rule": {"app": "chrome", "block": "浏览器"}, "directApply": True}
    r = client.post(f"{P}/window-rules", json=body, headers=WRITE)
    assert r.status_code == 200 and r.json()["applied"] and r.json()["result"]["rule"]["block"] == "浏览器"
    lid = r.json()["directLogId"]
    assert db().detector_rules.find_one({"user": "u_local"})["rules"][0]["kind"] == "block"
    # 之后又有人改规则（版本变了）→ 撤回 409
    db().detector_rules.update_one({"user": "u_local"}, {"$inc": {"version": 1}})
    assert client.post(f"{LOG}/{lid}/undo").status_code == 409
    db().detector_rules.update_one({"user": "u_local"}, {"$inc": {"version": -1}})
    assert client.post(f"{LOG}/{lid}/undo").status_code == 200
    doc = db().detector_rules.find_one({"user": "u_local"})
    assert doc["rules"] == [] and doc["snapshots"] == []  # 施加时压进去的快照一并用掉


def test_direct_block_rule_and_undo(client, tid, blk):
    direct(client)
    r = client.post(f"{P}/block-rules", json={"op": "add", "blockKey": K, "taskId": tid, "directApply": True}, headers=WRITE)
    assert r.status_code == 200 and r.json()["result"]["blockKey"] == K
    assert one(client, D, K)["status"] == "assigned" and one(client, D, K)["via"] == "rule"
    assert db().activity_block_rules.find_one({"blockKey": K})["via"] == "ai-direct"
    assert client.post(f"{LOG}/{r.json()['directLogId']}/undo").status_code == 200
    assert db().activity_block_rules.count_documents({}) == 0 and one(client, D, K)["status"] == "pending"


def test_direct_change_and_log_listing(client, tid, blk, seeded):
    direct(client)
    t = db().tasks.find_one({"id": tid})
    r = client.post(f"{P}/changes", headers=WRITE, json={"target": "task", "targetId": tid, "op": "rename", "name": "直改", "directApply": True})
    assert r.status_code == 200 and r.json()["applied"] and db().tasks.find_one({"id": tid})["name"] == "直改" != t["name"]
    assert client.post(f"{LOG}/{r.json()['directLogId']}/undo").status_code == 409  # change 不可撤回
    for i in range(3):
        db().activity_direct_log.insert_one({"user": "u_local", "id": f"dl_x{i}", "at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc),
                                             "actor": "ai", "kind": "blockRule", "payload": {}, "before": {"secret": 1}, "after": None, "undoneAt": None})
    items = client.get(LOG, params={"limit": 3}).json()["items"]
    assert len(items) == 3 and all("before" not in i and "after" not in i for i in items)
    assert [i["at"] for i in items] == sorted((i["at"] for i in items), reverse=True)
    assert len(client.get(LOG).json()["items"]) == 4
    assert client.get(LOG, params={"limit": 0}).status_code == 422 and client.get(LOG, params={"limit": 201}).status_code == 422
    assert len(client.get(LOG, params={"date": day(0)}).json()["items"]) == 4 and client.get(LOG, params={"date": day(-3)}).json()["items"] == []
    assert client.get(LOG, headers={"Authorization": "Bearer t"}).status_code == 403
    old = __import__("datetime").datetime.now(__import__("datetime").timezone.utc) - __import__("datetime").timedelta(days=91)
    db().activity_direct_log.update_one({"id": "dl_x0"}, {"$set": {"at": old}})
    assert len(client.get(LOG).json()["items"]) == 3


def test_undo_rule_conditional_write_refuses_newer_human_change(client, tid, blk, monkeypatch):
    from app.modules.activity import blocks_repo, direct_log
    direct(client)
    r = client.post(f"{P}/block-rules", json={"op": "add", "blockKey": K, "taskId": tid, "directApply": True}, headers=WRITE)
    lid = r.json()["directLogId"]
    real = direct_log._same_rule

    def raced(a, b):  # 比较通过之后、写之前，人改了规则的去向
        ok = real(a, b)
        db().activity_block_rules.update_one({"blockKey": K}, {"$set": {"taskId": "t_human", "via": "human"}})
        return ok

    monkeypatch.setattr(direct_log, "_same_rule", raced)
    assert client.post(f"{LOG}/{lid}/undo").status_code == 409
    assert db().activity_block_rules.find_one({"blockKey": K})["taskId"] == "t_human"
    assert db().activity_direct_log.find_one({"id": lid})["undoneAt"] is None


def test_undo_block_conditional_write_refuses_newer_human_change(client, tid, blk, monkeypatch):
    from app.modules.activity import blocks_repo
    direct(client)
    lid = act(client, taskId=tid).json()["directLogId"]
    real, n = blocks_repo.get_block, {"n": 0}

    def raced(*a):  # 第一次读之后、写之前，人改了这个块
        out = real(*a)
        n["n"] += 1
        if n["n"] == 1:
            db().activity_blocks.update_one({"blockKey": K}, {"$set": {"status": "ignored"}, "$inc": {"seq": 1}})
        return out

    monkeypatch.setattr(blocks_repo, "get_block", raced)
    assert client.post(f"{LOG}/{lid}/undo").status_code == 409
    assert db().activity_blocks.find_one({"blockKey": K})["status"] == "ignored"


def test_undo_block_generic_conflict_leaves_doc_untouched_and_retry_works(client, tid, blk, monkeypatch):
    from app.modules.activity import blocks_repo
    direct(client)
    lid = act(client, action="generic").json()["directLogId"]  # 原来没标记、施加后标上
    real = blocks_repo.swap_generic
    monkeypatch.setattr(blocks_repo, "swap_generic", lambda *a: False)
    assert client.post(f"{LOG}/{lid}/undo").status_code == 409
    assert db().activity_generic.count_documents({"blockKey": K}) == 1 and db().activity_direct_log.find_one({"id": lid})["undoneAt"] is None
    monkeypatch.setattr(blocks_repo, "swap_generic", real)
    assert client.post(f"{LOG}/{lid}/undo").status_code == 200  # 重试能完成
    assert db().activity_generic.count_documents({"blockKey": K}) == 0
