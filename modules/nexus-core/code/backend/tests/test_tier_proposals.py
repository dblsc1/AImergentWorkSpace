"""三级分类第二部分 B（契约 v2.38「五」）：windowRule / blockRule / blockAction 提议、批准、更正、superseded。"""

from __future__ import annotations

import pytest
from _reconcile_common import tiers
from _tier_common import BLK, BR, RULES, db, day, one, rec, upload
from app.modules.activity import new_items, tier_items

P = "/api/core/activity/proposals"
D = day(-1)
K = "chrome · 周报"
WRITE = {"X-Nexus-Scope": "write"}


@pytest.fixture()
def tid(seeded):
    return seeded["tasks"]["示例任务三"]["id"]


@pytest.fixture()
def tid2(seeded):
    return seeded["tasks"]["示例任务一"]["id"] if "示例任务一" in seeded["tasks"] else next(t["id"] for n, t in seeded["tasks"].items() if n != "示例任务三")


@pytest.fixture()
def blk(client):
    tiers(client)
    upload(client, [rec(D, "09:00:00", "09:10:00", title="周报"), rec(D, "10:00:00", "10:20:00", title="周报")])


def wr(client, op="add", rule=None, **kw):
    return client.post(f"{P}/window-rules", json={"op": op, "rule": rule or {"app": "chrome", "block": "浏览器"}, "reason": " r ", **kw})


def ba(client, action="assign", key=K, **kw):
    return client.post(f"{P}/block-actions", json={"action": action, "date": D, "blockKey": key, **kw})


def rules_doc():
    return db().detector_rules.find_one({"user": "u_local"}) or {"rules": [], "version": 0}


def test_window_rule_propose_dedupe_and_list(client):
    a = wr(client).json()
    assert a["id"].startswith("nw_") and a["status"] == "pending" and a["corrected"] is False and a["reason"] == "r"
    assert a["op"] == "add" and a["rule"] == {"app": "chrome", "block": "浏览器"}
    assert wr(client).json()["id"] == a["id"] and len(client.get(P).json()["windowRules"]) == 1
    assert rules_doc()["rules"] == []
    got = client.get(P).json()
    assert {"projects", "tasks", "changes", "ignores", "blockRules", "blockActions"} <= set(got)
    assert wr(client, rule={"app": "chrome", "block": "别的"}).json()["id"] != a["id"]
    assert wr(client, "add", {"id": "r_x", "app": "a", "block": "b"}).status_code == 400
    assert wr(client, "add", {"app": "a"}).status_code == 400
    assert wr(client, "add", {"block": "b"}).status_code == 400  # app / title 至少一个
    assert wr(client, "edit", {"id": "r_nope", "block": "b"}).status_code == 400
    assert wr(client, "zap").status_code == 422


def test_window_rule_approve_edit_and_reject_409(client):
    a = wr(client).json()
    r = client.post(f"{P}/{a['id']}/approve", json={"edit": {"rule": {"block": " 改后 "}}})
    assert r.status_code == 200 and r.json()["status"] == "accepted" and r.json()["corrected"] is True and r.json()["rule"]["block"] == "改后"
    live = rules_doc()["rules"]
    assert len(live) == 1 and live[0]["kind"] == "block" and live[0]["block"] == "改后" and live[0]["id"] == r.json()["createdId"]
    # accepted 再提 = 换新的 pending
    assert wr(client).json()["status"] == "pending"
    # edit / delete
    rid = live[0]["id"]
    e = client.post(f"{P}/window-rules", json={"op": "edit", "rule": {"id": rid, "block": "又改"}}).json()
    assert client.post(f"{P}/{e['id']}/approve").json()["status"] == "accepted" and rules_doc()["rules"][0]["block"] == "又改"
    dl = client.post(f"{P}/window-rules", json={"op": "delete", "rule": {"id": rid, "block": "忽略"}}).json()
    assert dl["rule"] == {"id": rid}
    assert client.post(f"{P}/{dl['id']}/reject").json()["status"] == "rejected"
    assert client.post(f"{P}/window-rules", json={"op": "delete", "rule": {"id": rid}}).status_code == 409
    assert len(rules_doc()["rules"]) == 1


def test_edit_422(client, tid):
    a = wr(client).json()
    for bad in ({"op": "edit"}, {"kind": "x"}, {"blockKey": "x"}, {"rule": {"app": "z"}}, {"rule": {"block": "x"}, "extra": 1}):
        assert client.post(f"{P}/{a['id']}/approve", json={"edit": bad}).status_code == 422
    assert client.post(f"{P}/{a['id']}/approve", json={"edit": {"taskId": tid}}).status_code == 422
    assert client.get(P).json()["windowRules"][0]["status"] == "pending"
    b = client.post(f"{P}/block-rules", json={"op": "add", "blockKey": "k", "taskId": tid}).json()
    for bad in ({"blockKey": "z"}, {"op": "delete"}, {"parts": []}, {}.__class__(taskId=tid, projectId="p")):
        r = client.post(f"{P}/{b['id']}/approve", json={"edit": bad})
        assert r.status_code in (422, 400), r.text
    assert db().activity_block_rules.count_documents({}) == 0


def test_window_rule_if_match_retry(client, monkeypatch):
    from app.modules.detector import repo
    a = wr(client).json()
    real, calls = repo.replace_rules, {"n": 0}

    def flaky(*args, **kw):
        calls["n"] += 1
        return None if calls["n"] <= 2 else real(*args, **kw)

    monkeypatch.setattr(repo, "replace_rules", flaky)
    assert client.post(f"{P}/{a['id']}/approve").status_code == 200 and calls["n"] == 3
    b = wr(client, rule={"app": "x", "block": "x"}).json()
    calls["n"] = -100  # 一直撞
    r = client.post(f"{P}/{b['id']}/approve")
    assert r.status_code == 409
    assert next(i for i in client.get(P).json()["windowRules"] if i["id"] == b["id"])["status"] == "pending"


def test_block_rule_flow(client, tid, tid2, blk):
    k = "某个块"
    assert client.post(f"{P}/block-rules", json={"op": "add", "blockKey": k}).status_code == 400
    assert client.post(f"{P}/block-rules", json={"op": "add", "blockKey": k, "taskId": "t_no"}).status_code == 400
    assert client.post(f"{P}/block-rules", json={"op": "edit", "blockKey": k, "taskId": tid}).status_code == 400
    assert client.post(f"{P}/block-rules", json={"op": "delete", "blockKey": k}).status_code == 400
    a = client.post(f"{P}/block-rules", json={"op": "add", "blockKey": k, "taskId": tid}).json()
    assert a["id"].startswith("nb_") and client.post(f"{P}/block-rules", json={"op": "add", "blockKey": k, "taskId": tid}).json()["id"] == a["id"]
    r = client.post(f"{P}/{a['id']}/approve", json={"edit": {"taskId": tid2}}).json()
    assert r["corrected"] and db().activity_block_rules.find_one({"blockKey": k})["taskId"] == tid2
    assert db().activity_block_rules.find_one({"blockKey": k})["via"] == "ai"
    # add 遇已有 = edit
    b = client.post(f"{P}/block-rules", json={"op": "add", "blockKey": k, "taskId": tid}).json()
    client.post(f"{P}/{b['id']}/approve")
    assert db().activity_block_rules.count_documents({}) == 1 and db().activity_block_rules.find_one({})["taskId"] == tid
    d = client.post(f"{P}/block-rules", json={"op": "delete", "blockKey": k}).json()
    client.post(f"{P}/{d['id']}/approve")
    assert db().activity_block_rules.count_documents({}) == 0
    assert len(client.get(P, params={"status": "all"}).json()["blockRules"]) == 2
    rej = client.post(f"{P}/block-rules", json={"op": "add", "blockKey": "z", "projectId": "p_inbox"}).json()
    client.post(f"{P}/{rej['id']}/reject")
    assert client.post(f"{P}/block-rules", json={"op": "add", "blockKey": "z", "projectId": "p_inbox"}).status_code == 409


def test_block_action_approve_via_ai_and_409(client, tid, tid2, blk):
    assert ba(client, key="没有的块", taskId=tid).status_code == 400
    assert ba(client, taskId="t_no").status_code == 400
    assert ba(client, "ignore", forever=True).status_code == 400
    assert ba(client, "assign").status_code == 400
    assert ba(client, "split", taskId=tid).status_code == 400
    a = ba(client, taskId=tid).json()
    assert a["id"].startswith("na_") and a["blockTitle"] == K and a["date"] == D and ba(client, taskId=tid).json()["id"] == a["id"]
    assert [x["id"] for x in client.get(P).json()["blockActions"]] == [a["id"]]
    r = client.post(f"{P}/{a['id']}/approve", json={"edit": {"taskId": tid2}})
    assert r.status_code == 200 and r.json()["corrected"] is True
    b = one(client, D, K)
    assert b["status"] == "assigned" and b["via"] == "ai" and b["parts"][0]["taskId"] == tid2
    # 块已处理：另一条提议批不了
    c = ba(client, "ignore").json()
    r = client.post(f"{P}/{c['id']}/approve")
    assert r.status_code == 409 and "这个块已经处理过" in r.json()["detail"]
    assert client.get(P).json()["blockActions"][0]["status"] == "pending"
    # 已写台账：对账事件带 confirmed（人批准）
    ev = db().events.find_one({"source": "activity-block", "type": "session.completed"})
    assert ev and ev["ai"]["confirmed"] is True


def test_block_action_split_generic_ignore(client, tid, tid2, blk):
    parts = [{"toTs": None, "taskId": tid}, {"taskId": tid2}]
    assert ba(client, "split", parts=parts).status_code == 400  # 缺拆分点
    cut = int(__import__("datetime").datetime.fromisoformat(f"{D}T09:30:00+00:00").timestamp() * 1000)
    from _tier_common import at
    cut = round(at(D, "09:30:00").timestamp() * 1000)
    s = ba(client, "split", parts=[{"toTs": cut, "taskId": tid}, {"fromTs": cut, "taskId": tid2}]).json()
    assert client.post(f"{P}/{s['id']}/approve", json={"edit": {"taskId": tid}}).status_code == 422
    assert client.post(f"{P}/{s['id']}/approve", json={"edit": {"parts": [{"toTs": cut, "taskId": tid2}, {"fromTs": cut, "taskId": tid}]}}).status_code == 200
    got = one(client, D, K)
    assert got["via"] == "ai" and [p["taskId"] for p in got["parts"]] == [tid2, tid]
    g = ba(client, "generic", key="chrome · 周报").json()
    assert client.post(f"{P}/{g['id']}/approve").status_code == 409  # 已处理
    upload(client, [rec(D, "11:00:00", "11:05:00", title="另一个")])
    g = ba(client, "generic", key="chrome · 另一个").json()
    assert client.post(f"{P}/{g['id']}/approve").status_code == 200
    assert db().activity_generic.count_documents({"blockKey": "chrome · 另一个"}) == 1
    upload(client, [rec(D, "12:00:00", "12:05:00", title="第三")])
    i = ba(client, "ignore", key="chrome · 第三").json()
    assert client.post(f"{P}/{i['id']}/approve", json={"edit": {"taskId": tid}}).status_code == 422
    client.post(f"{P}/{i['id']}/approve")
    assert one(client, D, "chrome · 第三")["status"] == "ignored"


def test_human_write_supersedes(client, tid, blk):
    a = ba(client, taskId=tid).json()
    other = ba(client, "ignore", key=K).json()
    upload(client, [rec(D, "13:00:00", "13:05:00", title="别的块")])
    keep = ba(client, "ignore", key="chrome · 别的块").json()
    r = client.post(f"{BLK}/assign", json={"date": D, "blockKey": K, "taskId": tid, "ifSeq": 0})
    assert r.status_code == 200
    st = {i["id"]: i["status"] for i in client.get(P, params={"status": "all"}).json()["blockActions"]}
    assert st == {a["id"]: "superseded", other["id"]: "superseded", keep["id"]: "pending"}
    assert [i["id"] for i in client.get(P).json()["blockActions"]] == [keep["id"]]
    assert client.post(f"{P}/{a['id']}/approve").status_code == 409
    # superseded 的再提 = 换新的，不是 409
    again = ba(client, taskId=tid).json()
    assert again["id"] == a["id"] and again["status"] == "pending"
    # 其余四个人写也算
    for path, body in (("reset", {}), ("ignore", {}), ("split", None)):
        if body is None:
            continue
        ba(client, taskId=tid)
        seq = one(client, D, K)["seq"]
        assert client.post(f"{BLK}/{path}", json={"date": D, "blockKey": K, "ifSeq": seq, **body}).status_code == 200
        assert db().activity_new_items.find_one({"id": a["id"]})["status"] == "superseded"
    ba(client, "generic")
    assert client.post(f"{BLK}/generic", json={"blockKey": K, "generic": True}).status_code == 200
    assert db().activity_new_items.find_one({"id": a["id"]})["status"] == "superseded"


def test_caps_and_gates(client, tid, blk, monkeypatch):
    monkeypatch.setitem(new_items.MAX_PENDING, "windowRule", 2)
    monkeypatch.setitem(new_items.MAX_PENDING, "blockRule", 1)
    monkeypatch.setitem(new_items.MAX_PENDING, "blockAction", 1)
    assert [wr(client, rule={"app": f"a{i}", "block": "b"}).status_code for i in range(3)] == [200, 200, 429]
    assert wr(client, rule={"app": "a0", "block": "b"}).status_code == 200  # 已有的不占新名额
    assert [client.post(f"{P}/block-rules", json={"op": "add", "blockKey": f"k{i}", "taskId": tid}).status_code for i in range(2)] == [200, 429]
    assert [ba(client, key=k, taskId=tid).status_code for k in (K, K + "x")] in ([200, 400],)
    upload(client, [rec(D, "14:00:00", "14:05:00", title="二")])
    assert ba(client, key="chrome · 二", taskId=tid).status_code == 429
    bearer = {"Authorization": "Bearer tok_x"}
    for url, body in (("window-rules", {"op": "add", "rule": {"app": "a", "block": "b"}}), ("block-rules", {"op": "add", "blockKey": "k", "taskId": tid}),
                      ("block-actions", {"action": "ignore", "date": D, "blockKey": K})):
        assert client.post(f"{P}/{url}", json=body, headers=bearer).status_code == 403
        assert client.post(f"{P}/{url}", json=body, headers={"X-Nexus-Scope": "read"}).status_code == 403
        assert client.post(f"{P}/{url}", json=body, headers={"X-Nexus-Anonymous": "1"}).status_code == 403
    for h in (bearer, WRITE, {"X-Nexus-Anonymous": "1"}):
        a = client.get(P).json()["blockRules"][0]["id"]
        assert client.post(f"{P}/{a}/approve", headers=h, json={}).status_code == 403
