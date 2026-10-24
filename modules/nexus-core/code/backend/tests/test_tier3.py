"""v2.39 第三级（契约「MCP v0.5 的后端」四、五）：秘书报告、问人、sessionChange、blockAction 改去向、忽略规则直接施加。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from _reconcile_common import doc, rnd, tiers
from _tier_common import db, day, rec, upload
from app.modules.activity import new_items
from app.modules.events import service as events_service
from test_session_reassign import _reassign, _session, _world
from test_tier_direct import LOG, WRITE, act, direct

A = "/api/core/activity"
P = f"{A}/proposals"
D = day(-1)
K = "chrome · 周报"
BEARER = {"Authorization": "Bearer tok_device"}
AUTH = {  # 提交口径：(请求头, 期望是否放行)
    "none": ({}, True), "write": ({"X-Nexus-Scope": "write"}, True), "write+bearer": ({**BEARER, "X-Nexus-Scope": "write"}, True),
    "bearer": (BEARER, False), "read": ({"X-Nexus-Scope": "read"}, False), "read+bearer": ({**BEARER, "X-Nexus-Scope": "read"}, False),
    "report": ({"X-Nexus-Scope": "report"}, False), "anon": ({"X-Nexus-Anonymous": "1"}, False),
    "anon+bearer": ({**BEARER, "X-Nexus-Anonymous": "1"}, False),
}


def ago(minutes: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).replace(microsecond=0).isoformat()


@pytest.fixture()
def tid(seeded):
    return seeded["tasks"]["示例任务三"]["id"]


def q(title="这段在干嘛", **kw):
    return {"title": title, "from": ago(120), "to": ago(60), **kw}


def ask(client, *qs, headers=None, expect=200):
    r = client.post(f"{A}/questions", json={"questions": list(qs)}, headers=headers or {})
    assert r.status_code == expect, r.text
    return r.json()


# ── 4.1 秘书报告 ──

def test_secretary_reports(client):
    r = client.post(f"{A}/secretary-reports", json={"date": "2026-10-01", "text": "一", "author": "sec"})
    first = r.json()
    assert r.status_code == 200 and first["revision"] == 1 and first["id"].startswith("sr_") and first["author"] == "sec"
    again = client.post(f"{A}/secretary-reports", json={"date": "2026-10-01", "text": "二"}).json()
    assert again["id"] == first["id"] and again["revision"] == 2 and again["text"] == "二" and again["createdAt"] == first["createdAt"]
    today = client.post(f"{A}/secretary-reports", json={"text": "今天"}).json()
    assert today["date"] == day(0)
    client.post(f"{A}/secretary-reports", json={"date": "2026-10-03", "text": "三"})
    assert [i["date"] for i in client.get(f"{A}/secretary-reports", params={"from": "2026-10-01", "to": "2026-10-03"}).json()["items"]] == ["2026-10-03", "2026-10-01"]
    assert [i["date"] for i in client.get(f"{A}/secretary-reports").json()["items"]] == [day(0)]
    assert client.get(f"{A}/secretary-reports", params={"date": "2026-10-01"}).json()["items"][0]["text"] == "二"
    for bad in ({"text": ""}, {"text": "x" * 8001}, {"text": "a", "date": "nope"}, {"text": "a", "author": "x" * 65}, {}):
        assert client.post(f"{A}/secretary-reports", json=bad).status_code == 400, bad
    assert client.post(f"{A}/secretary-reports", json={"text": "你" * 8000}).status_code == 200  # 按码点
    for params in ({"from": "2026-10-01"}, {"from": "2026-01-01", "to": "2026-10-01"}, {"date": "x"}):
        assert client.get(f"{A}/secretary-reports", params=params).status_code == 400, params


@pytest.mark.parametrize("who", list(AUTH))
def test_submission_auth_matrix(client, tid, who):
    headers, ok = AUTH[who]
    _world_ids = _world(client)
    eid = _session(client, _world_ids["bucket"])
    calls = (("secretary-reports", {"text": "x"}), ("questions", {"questions": [{"title": "t"}]}),
             ("proposals/sessions", {"eventId": eid, "op": "reassign", "taskId": _world_ids["a"]}))
    for path, body in calls:
        r = client.post(f"{A}/{path}", json=body, headers=headers)
        assert (r.status_code == 200) is ok and (ok or r.status_code == 403), (who, path, r.status_code, r.text)


# ── 4.2 问人 ──

def test_questions_submit_dedupe_cap_validation(client, tid):
    out = ask(client, q(options=[{"label": "写周报", "target": {"taskId": tid}}, {"label": "不记", "ignore": True}], allowText=True), q("第二个"))
    a, b = out["items"]
    assert a["id"].startswith("aq_") and a["status"] == "pending" and a["answer"] is None and a["allowText"] is True and len(a["options"]) == 2
    assert a["text"] == "" and b["options"] == [] and b["date"] is None
    again = ask(client, q(options=[{"label": "写周报", "target": {"taskId": tid}}, {"label": "不记", "ignore": True}], allowText=True), q("第二个 "))
    assert [i["id"] for i in again["items"]] == [a["id"], b["id"]] and db().activity_questions.count_documents({}) == 2
    assert ask(client, {**q(), "options": []})["items"][0]["id"] != a["id"]  # 选项不同 = 另一条
    assert [i["id"] for i in client.get(f"{A}/questions").json()["items"]][0] != a["id"]  # 新的在前
    assert len(client.get(f"{A}/questions", params={"limit": 1}).json()["items"]) == 1
    bad = [{}, {"title": ""}, {"title": "x" * 121}, {"title": "t", "text": "x" * 1001}, {"title": "t", "from": ago(5)},
           {"title": "t", "from": ago(5).replace("+00:00", ""), "to": ago(1).replace("+00:00", "")}, {"title": "t", "from": ago(1), "to": ago(5)},
           {"title": "t", "from": ago(60 * 30), "to": ago(1)}, {"title": "t", "from": ago(5), "to": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()},
           {"title": "t", "options": [{"label": str(i)} for i in range(7)]}, {"title": "t", "options": [{"label": ""}]},
           {"title": "t", "options": [{"label": "x" * 41}]}, {"title": "t", "options": [{"label": "a", "target": {"taskId": "t_nope"}}]},
           {"title": "t", "options": [{"label": "a", "target": {"projectId": "p_nope"}}]},
           {"title": "t", "options": [{"label": "a", "target": {"taskId": tid}, "ignore": True}]},
           {"title": "t", "options": [{"label": "a", "target": {"taskId": tid, "projectId": "p"}}]}, {"title": "t", "date": "x"}]
    for item in bad:
        ask(client, item, expect=400)
    ask(client, expect=400)
    assert client.post(f"{A}/questions", json={"questions": [{"title": f"t{i}"} for i in range(11)]}).status_code == 400
    # 待回答上限 30
    for i in range(27):
        ask(client, {"title": f"填 {i}"})
    assert db().activity_questions.count_documents({"status": "pending"}) == 30
    ask(client, q("第二个"))  # 已有的原样返回，不占名额
    ask(client, {"title": "满了"}, expect=429)
    ask(client, {"title": "满了"}, {"title": "也满了"}, expect=429)


def test_answer_records_via_backfill(client, tid, seeded):
    item = ask(client, q(options=[{"label": "写周报", "target": {"taskId": tid}}, {"label": "不记", "ignore": True}], allowText=True))["items"][0]
    r = client.post(f"{A}/questions/{item['id']}/answer", json={"option": 0, "text": "备注"})
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["status"] == "answered" and got["answer"]["recorded"] is True and got["answer"]["option"] == 0 and got["answer"]["text"] == "备注"
    e = db().events.find_one({"id": got["answer"]["eventId"]})
    assert e["type"] == "session.completed" and e["source"] == "manual-backfill" and e["dedupeKey"] == f"question:{item['id']}:{tid}"
    assert e["subject"]["task"] == tid and e["data"]["durationSeconds"] == 3600
    assert client.post(f"{A}/questions/{item['id']}/answer", json={"option": 0}).status_code == 409  # 再答
    assert client.post(f"{A}/questions/{item['id']}/dismiss").status_code == 409
    assert db().events.count_documents({"dedupeKey": f"question:{item['id']}:{tid}"}) == 1
    assert client.get(f"{A}/questions").json()["items"] == []
    assert client.get(f"{A}/questions", params={"status": "all"}).json()["items"][0]["status"] == "answered"
    # 其余只记答案
    item2 = ask(client, q("二", options=[{"label": "写周报", "target": {"taskId": tid}}, {"label": "不记", "ignore": True}], allowText=True))["items"][0]
    got = client.post(f"{A}/questions/{item2['id']}/answer", json={"option": 1}).json()
    assert got["answer"]["recorded"] is False and got["answer"]["eventId"] is None and got["answer"]["option"] == 1
    item3 = ask(client, q("三", options=[{"label": "写周报", "target": {"taskId": tid}}], allowText=True))["items"][0]
    got = client.post(f"{A}/questions/{item3['id']}/answer", json={"text": "不知道"}).json()
    assert got["answer"] == {**got["answer"], "option": None, "recorded": False, "eventId": None, "text": "不知道"}
    item4 = ask(client, {"title": "没时段", "options": [{"label": "x", "target": {"taskId": tid}}]})["items"][0]  # 没 from/to：记不了
    assert client.post(f"{A}/questions/{item4['id']}/answer", json={"option": 0}).json()["answer"]["recorded"] is False
    assert db().events.count_documents({"source": "manual-backfill"}) == 1


def test_answer_project_target_and_validation(client):
    ids = _world(client)
    item = ask(client, q(options=[{"label": "放项目", "target": {"projectId": ids["q"]}}]))["items"][0]
    for bad in ({}, {"option": 5}, {"option": -1}, {"option": "0"}, {"text": "x"}):  # 这个问题不收文字
        assert client.post(f"{A}/questions/{item['id']}/answer", json=bad).status_code == 400, bad
    assert client.post(f"{A}/questions/aq_nope/answer", json={"text": "a"}).status_code == 404
    assert client.get(f"{A}/questions").json()["items"][0]["status"] == "pending"
    got = client.post(f"{A}/questions/{item['id']}/answer", json={"option": 0}).json()
    e = db().events.find_one({"id": got["answer"]["eventId"]})
    assert e["subject"]["project"] == ids["q"] and e["subject"]["task"] == f"t_unc_{ids['q']}"


def test_answer_backfill_error_passthrough_keeps_pending(client):
    ids = _world(client)
    item = ask(client, q(options=[{"label": "c", "target": {"taskId": ids["c"]}}], allowText=True))["items"][0]
    assert client.delete(f"/api/core/planner/tasks/{ids['c']}").status_code in (200, 204)
    r = client.post(f"{A}/questions/{item['id']}/answer", json={"option": 0})
    assert r.status_code == 404, r.text
    assert client.get(f"{A}/questions").json()["items"][0]["status"] == "pending" and db().events.count_documents({"source": "manual-backfill"}) == 0
    assert client.post(f"{A}/questions/{item['id']}/answer", json={"text": "算了"}).status_code == 200  # 改成只记答案


def test_dismiss_and_human_only(client, tid):
    item = ask(client, q(options=[{"label": "a", "target": {"taskId": tid}}]))["items"][0]
    for h in (BEARER, {"X-Nexus-Scope": "write"}, {**BEARER, "X-Nexus-Scope": "write"}, {"X-Nexus-Anonymous": "1"}):
        assert client.post(f"{A}/questions/{item['id']}/answer", json={"option": 0}, headers=h).status_code == 403, h
        assert client.post(f"{A}/questions/{item['id']}/dismiss", headers=h).status_code == 403, h
    assert db().events.count_documents({"source": "manual-backfill"}) == 0
    assert client.get(f"{A}/questions", headers={"X-Nexus-Scope": "read"}).status_code == 200
    r = client.post(f"{A}/questions/{item['id']}/dismiss")
    assert r.status_code == 200 and r.json()["status"] == "dismissed"
    assert client.post(f"{A}/questions/{item['id']}/dismiss").status_code == 409
    assert client.post(f"{A}/questions/{item['id']}/answer", json={"option": 0}).status_code == 409
    assert client.post(f"{A}/questions/aq_nope/dismiss").status_code == 404
    assert ask(client, q(options=[{"label": "a", "target": {"taskId": tid}}]))["items"][0]["id"] != item["id"]  # 撤下后同样的可以再问


# ── 4.3 sessionChange ──

def sc(client, eid, headers=None, **kw):
    return client.post(f"{P}/sessions", json={"eventId": eid, "op": "reassign", **kw}, headers=headers or {})


def owner(eid):
    s = events_service.find_sessions(eid)[0]
    chain = events_service.reassignments_of(s)
    return ((chain[-1] if chain else s)["subject"]["task"], chain)


def test_session_change_propose_list_approve(client):
    ids = _world(client)
    eid = _session(client, ids["bucket"])
    r = sc(client, eid, taskId=ids["a"], reason=" 看窗口 ")
    assert r.status_code == 200, r.text
    v = r.json()
    assert v["id"].startswith("ns_") and v["kind"] == "sessionChange" and v["status"] == "pending" and v["reason"] == "看窗口"
    assert (v["eventId"], v["op"], v["taskId"], v["projectId"], v["fromTaskId"], v["fromProjectId"]) == (eid, "reassign", ids["a"], None, ids["bucket"], ids["p"])
    assert v["durationSeconds"] == 600 and v["startAt"] and v["corrected"] is False
    assert sc(client, eid, taskId=ids["a"]).json()["id"] == v["id"]
    assert sc(client, eid, taskId=ids["b"]).json()["id"] != v["id"]
    got = client.get(P).json()["sessionChanges"]
    assert len(got) == 2 and {g["id"] for g in got} >= {v["id"]}
    assert owner(eid)[0] == ids["bucket"]  # 提议不动台账
    # 校验
    assert sc(client, "evt_nope", taskId=ids["a"]).status_code == 400
    assert sc(client, eid, taskId="t_nope").status_code == 400 and sc(client, eid, projectId="p_nope").status_code == 400
    assert sc(client, eid).status_code == 400 and sc(client, eid, taskId=ids["a"], projectId=ids["p"]).status_code == 400
    assert sc(client, eid, taskId=ids["bucket"]).status_code == 400
    assert sc(client, eid, projectId=ids["p"]).status_code == 409  # 现在就在 P 的未分类
    assert client.post(f"{P}/sessions", json={"eventId": eid, "op": "split", "taskId": ids["a"]}).status_code == 422
    # 批准只许人
    for h in (WRITE, BEARER):
        assert client.post(f"{P}/{v['id']}/approve", headers=h).status_code == 403
    r = client.post(f"{P}/{v['id']}/approve")
    assert r.status_code == 200 and r.json()["status"] == "accepted"
    task, chain = owner(eid)
    assert task == ids["a"] and len(chain) == 1 and chain[0]["data"]["actor"] == "human" and chain[0]["data"]["seq"] == 1
    assert client.post(f"{P}/{v['id']}/approve").json()["status"] == "accepted"  # 幂等
    assert sc(client, eid, taskId=ids["a"]).status_code == 409  # 现在就在 a
    again = sc(client, eid, taskId=ids["b"]).json()  # 同一提议还 pending（上面第二条）
    assert again["status"] == "pending"


def test_session_change_edit_reject_voided_cap(client, monkeypatch):
    ids = _world(client)
    eid = _session(client, ids["bucket"])
    v = sc(client, eid, taskId=ids["a"]).json()
    for bad in ({"taskId": "t_nope"}, {"projectId": "p_nope"}):
        assert client.post(f"{P}/{v['id']}/approve", json={"edit": bad}).status_code == 400
    for bad in ({"foo": 1}, {}, {"taskId": ids["a"], "projectId": ids["p"]}):
        assert client.post(f"{P}/{v['id']}/approve", json={"edit": bad}).status_code == 422, bad
    assert owner(eid)[0] == ids["bucket"]
    r = client.post(f"{P}/{v['id']}/approve", json={"edit": {"taskId": ids["c"]}})
    assert r.status_code == 200 and r.json()["corrected"] is True and r.json()["taskId"] == ids["c"]
    assert owner(eid)[0] == ids["c"]
    # 否掉过的不再提；同一条目标已是当前归属的批准 = 空操作
    eid2 = _session(client, ids["bucket"], minutes_ago=200)
    w = sc(client, eid2, taskId=ids["b"]).json()
    assert client.post(f"{P}/{w['id']}/reject").json()["status"] == "rejected"
    assert sc(client, eid2, taskId=ids["b"]).status_code == 409
    eid3 = _session(client, ids["bucket"], minutes_ago=300)
    x = sc(client, eid3, taskId=ids["b"]).json()
    _reassign(client, eid3, taskId=ids["b"])  # 人先改了
    r = client.post(f"{P}/{x['id']}/approve")
    assert r.status_code == 200 and r.json()["status"] == "accepted" and len(owner(eid3)[1]) == 1
    # 作废：提议 409、批准 409（留 pending）
    eid4 = _session(client, ids["bucket"], minutes_ago=400)
    y = sc(client, eid4, taskId=ids["a"]).json()
    monkeypatch.setattr(events_service, "is_voided", lambda s: s["id"] == eid4)
    assert sc(client, eid4, taskId=ids["b"]).status_code == 409
    assert client.post(f"{P}/{y['id']}/approve").status_code == 409
    assert db().activity_new_items.find_one({"id": y["id"]})["status"] == "pending"
    monkeypatch.undo()
    # 上限 429
    monkeypatch.setitem(new_items.MAX_PENDING, "sessionChange", 1)
    assert sc(client, eid4, taskId=ids["b"]).status_code == 429
    assert sc(client, eid4, taskId=ids["a"]).status_code == 200  # 已在待批准里的原样返回


def test_session_change_direct_and_undo(client):
    ids = _world(client)
    eid = _session(client, ids["bucket"])
    body = {"taskId": ids["a"], "directApply": True}
    assert sc(client, eid, headers=WRITE, **body).json()["reason"] == "agentDirectOff"
    direct(client, True, True, False)
    assert sc(client, eid, headers=WRITE, **body).json()["reason"] == "honeycombRecordsOff"
    direct(client)
    for h in ({}, {"X-Nexus-Scope": "read"}, BEARER):  # 无范围头 = 不当作人放行
        r = sc(client, eid, headers=h, **body)
        assert r.status_code == 403, h
    assert sc(client, eid, **body).json()["reason"] == "needWriteScope"
    assert owner(eid)[0] == ids["bucket"] and db().activity_direct_log.count_documents({}) == 0
    r = sc(client, eid, headers=WRITE, **body)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["applied"] is True and out["directLogId"].startswith("dl_") and out["result"]["taskId"] == ids["a"]
    task, chain = owner(eid)
    assert task == ids["a"] and chain[0]["data"]["actor"] == "ai"
    assert db().activity_new_items.count_documents({"kind": "sessionChange"}) == 0
    assert sc(client, eid, headers=WRITE, **body).status_code == 409  # 已在目标
    log = client.get(LOG).json()["items"][0]
    assert log["kind"] == "sessionChange" and log["payload"]["eventId"] == eid and "before" not in log
    assert client.post(f"{LOG}/{out['directLogId']}/undo", headers=WRITE).status_code == 403
    r = client.post(f"{LOG}/{out['directLogId']}/undo")
    assert r.status_code == 200, r.text
    task, chain = owner(eid)
    assert task == ids["bucket"] and [c["data"]["actor"] for c in chain] == ["ai", "human"] and chain[1]["data"]["toTaskId"] == ids["bucket"]
    assert client.post(f"{LOG}/{out['directLogId']}/undo").status_code == 409
    # 之后又被改挂过 → 409，不动
    out = sc(client, eid, headers=WRITE, taskId=ids["b"], directApply=True).json()
    _reassign(client, eid, taskId=ids["c"])
    assert client.post(f"{LOG}/{out['directLogId']}/undo").status_code == 409
    assert owner(eid)[0] == ids["c"] and db().activity_direct_log.find_one({"id": out["directLogId"]})["undoneAt"] is None
    # 项目目标直接施加 + 被否掉过的提议 409
    eid2 = _session(client, ids["bucket"], minutes_ago=200)
    v = sc(client, eid2, taskId=ids["b"]).json()
    client.post(f"{P}/{v['id']}/reject")
    assert sc(client, eid2, headers=WRITE, taskId=ids["b"], directApply=True).status_code == 409
    got = sc(client, eid2, headers=WRITE, projectId=ids["q"], directApply=True).json()
    assert got["result"]["projectId"] == ids["q"] and owner(eid2)[0] == f"t_unc_{ids['q']}"
    assert client.post(f"{LOG}/{got['directLogId']}/undo").status_code == 200 and owner(eid2)[0] == ids["bucket"]


def test_public_reassign_endpoint_stays_human_only(client):
    ids = _world(client)
    eid = _session(client, ids["bucket"])
    for h in (BEARER, {**BEARER, "X-Nexus-Scope": "write"}):
        assert client.post(f"/api/core/sessions/{eid}/reassign", json={"taskId": ids["a"]}, headers=h).status_code == 403
    assert owner(eid)[0] == ids["bucket"]


# ── 五 ──

@pytest.fixture()
def assigned(client, tid, seeded):
    tiers(client)
    upload(client, [rec(D, "09:00:00", "09:10:00", title="周报")])
    from _reconcile_common import assign  # noqa: PLC0415

    assign(client, D, K, tid)
    rnd()
    return tid, seeded["tasks"]["示例任务一"]["id"] if "示例任务一" in seeded["tasks"] else next(t["id"] for n, t in seeded["tasks"].items() if t["id"] != tid and n != "示例任务三")


def reassigned() -> int:
    return db().events.count_documents({"type": "session.reassigned"})


def test_block_action_retargets_assigned_block(client, assigned):
    tid, other = assigned
    assert reassigned() == 0
    v = client.post(f"{P}/block-actions", json={"action": "assign", "date": D, "blockKey": K, "taskId": other}).json()
    r = client.post(f"{P}/{v['id']}/approve")
    assert r.status_code == 200 and r.json()["status"] == "accepted", r.text
    d = doc(D, K)
    assert d["parts"][0]["taskId"] == other and d["via"] == "ai" and d["status"] == "assigned"
    rnd()
    assert reassigned() == 1  # 已入账的段走 session.reassigned
    # 同一目标 = 空操作，仍记 accepted
    w = client.post(f"{P}/block-actions", json={"action": "assign", "date": D, "blockKey": K, "taskId": other, "reason": "again"}).json()
    r = client.post(f"{P}/{w['id']}/approve")
    assert r.status_code == 200 and r.json()["status"] == "accepted"
    rnd()
    assert reassigned() == 1 and doc(D, K)["seq"] == d["seq"]


def test_block_action_direct_retarget_and_undo(client, assigned):
    tid, other = assigned
    direct(client)
    r = act(client, taskId=other)
    assert r.status_code == 200, r.text
    assert doc(D, K)["parts"][0]["taskId"] == other and doc(D, K)["via"] == "ai-direct"
    assert act(client, taskId=other).json()["applied"] is False  # 同一目标：空操作，不写日志
    assert client.post(f"{LOG}/{r.json()['directLogId']}/undo").status_code == 200
    assert doc(D, K)["parts"][0]["taskId"] == tid and doc(D, K)["via"] == "human"
    # ignored 的块仍不能改
    from _tier_common import BLK  # noqa: PLC0415

    seq = doc(D, K)["seq"]
    assert client.post(f"{BLK}/ignore", json={"date": D, "blockKey": K, "forever": False, "ifSeq": seq}).status_code == 200
    assert act(client, taskId=other).status_code == 409


def ign(client, headers=None, **kw):
    return client.post(f"{P}/ignores", json={"app": "secret-app", "titleContains": "私密", "reason": "r", **kw}, headers=headers or {})


def test_ignore_direct_apply_and_undo(client):
    assert ign(client).json()["status"] == "pending"  # 不带 directApply 照旧
    client.post(f"{P}/{ign(client).json()['id']}/reject")
    assert ign(client, WRITE, directApply=True).status_code == 403
    direct(client, True, False, True)
    assert ign(client, WRITE, directApply=True).json()["reason"] == "windowsClassifyOff"
    direct(client)
    assert ign(client, directApply=True).json()["reason"] == "needWriteScope"
    assert ign(client, BEARER, directApply=True).status_code == 403
    assert ign(client, {"X-Nexus-Scope": "read"}, directApply=True).status_code == 403
    assert ign(client, WRITE, directApply=True).status_code == 409  # 否掉过
    r = ign(client, WRITE, directApply=True, app="direct-app")
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["applied"] is True and out["result"]["app"] == "direct-app" and "titleContains" not in out["result"] and out["result"]["hasTitleFilter"] is True
    rid = out["result"]["id"]
    assert db().activity_ignores.count_documents({"id": rid}) == 1
    log = client.get(LOG, headers=WRITE).json()["items"][0]
    assert log["kind"] == "ignore" and "titleContains" not in log["payload"] and log["payload"]["hasTitleFilter"] is True
    assert client.get(LOG).json()["items"][0]["payload"]["titleContains"] == "私密"
    assert client.post(f"{LOG}/{out['directLogId']}/undo", headers=WRITE).status_code == 403
    assert client.post(f"{LOG}/{out['directLogId']}/undo").status_code == 200
    assert db().activity_ignores.count_documents({"id": rid}) == 0
    assert client.post(f"{LOG}/{out['directLogId']}/undo").status_code == 409
    # 被人删了 → 撤回 409
    out = ign(client, WRITE, directApply=True, app="direct-app").json()
    assert client.delete(f"{A}/ignores/{rid}").status_code == 204
    assert client.post(f"{LOG}/{out['directLogId']}/undo").status_code == 409
    # 已有同一条规则 → 409
    ign(client, WRITE, directApply=True, app="x2")
    assert ign(client, WRITE, directApply=True, app="x2").status_code == 409


def test_direct_apply_without_scope_header_is_need_write_scope(client, tid):
    direct(client)
    eid = _session(client, _world(client)["bucket"])
    cases = (("changes", {"target": "task", "targetId": tid, "op": "delete"}), ("ignores", {"app": "a"}),
             ("window-rules", {"op": "add", "rule": {"app": "a", "block": "b"}}), ("block-rules", {"op": "add", "blockKey": "k", "taskId": tid}),
             ("block-actions", {"action": "assign", "date": D, "blockKey": K, "taskId": tid}),
             ("sessions", {"eventId": eid, "op": "reassign", "taskId": tid}))
    for path, body in cases:
        r = client.post(f"{P}/{path}", json={**body, "directApply": True})
        assert r.status_code == 403 and r.json()["reason"] == "needWriteScope", (path, r.text)


# ── 审查补强 ──

def test_session_change_ineligible_session_409(client):
    ids = _world(client)
    eid = _session(client, ids["a"])  # 记在普通任务上的段：不能改挂
    direct(client)
    for kw in ({}, {"directApply": True}):
        r = sc(client, eid, headers=WRITE, taskId=ids["b"], **kw)
        assert r.status_code == 409 and "只能改挂记在未分类或自动记下的时间" in r.json()["detail"], r.text
    assert db().activity_new_items.count_documents({}) == 0 and db().activity_direct_log.count_documents({}) == 0


def test_answer_validates_before_creating_bucket(client):
    ids = _world(client)
    base = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=10)
    item = ask(client, {"title": "太短", "from": base.isoformat(), "to": (base + timedelta(milliseconds=500)).isoformat(),
                        "options": [{"label": "放", "target": {"projectId": ids["q"]}}]})["items"][0]
    assert client.post(f"{A}/questions/{item['id']}/answer", json={"option": 0}).status_code == 400
    assert db().tasks.count_documents({"id": f"t_unc_{ids['q']}"}) == 0
    assert client.get(f"{A}/questions").json()["items"][0]["status"] == "pending"
    ok = ask(client, q("正常", options=[{"label": "放", "target": {"projectId": ids["q"]}}]))["items"][0]
    assert client.post(f"{A}/questions/{ok['id']}/answer", json={"option": 0}).status_code == 200
    assert db().tasks.count_documents({"id": f"t_unc_{ids['q']}"}) == 1


def test_answer_claim_must_match_after_crash(client, tid):
    item = ask(client, q(options=[{"label": "a", "target": {"taskId": tid}}, {"label": "不记", "ignore": True}], allowText=True))["items"][0]
    db().activity_questions.update_one({"id": item["id"]}, {"$set": {"claimed": {"option": 1}}})  # 上次选了 1 就崩了
    r = client.post(f"{A}/questions/{item['id']}/answer", json={"option": 0})
    assert r.status_code == 409 and db().events.count_documents({"source": "manual-backfill"}) == 0
    assert client.post(f"{A}/questions/{item['id']}/answer", json={"text": "x"}).status_code == 409
    r = client.post(f"{A}/questions/{item['id']}/answer", json={"option": 1})
    assert r.status_code == 200 and r.json()["answer"]["recorded"] is False


def test_answer_check_order(client, tid):
    item = ask(client, q(options=[{"label": "a", "target": {"taskId": tid}}]))["items"][0]  # allowText 关
    assert client.post(f"{A}/questions/{item['id']}/answer", json={"option": 9, "text": "x"}).status_code == 400
    assert client.post(f"{A}/questions/{item['id']}/answer", json={"option": 0}).status_code == 200
    # 已回答：先 409，不管后面的参数对不对
    assert client.post(f"{A}/questions/{item['id']}/answer", json={"option": 9, "text": "x"}).status_code == 409


def test_concurrent_identical_ask_returns_existing(client, monkeypatch):
    first = ask(client, {"title": "并发"})["items"][0]
    from app.modules.activity import questions  # noqa: PLC0415

    real, calls = questions._col, []

    class Late:  # 第一次 find 看不见先到的那条（模拟另一个请求刚插入）
        def __init__(self, col):
            self._c = col

        def find(self, *a, **kw):
            calls.append(1)
            return iter([]) if len(calls) == 1 else self._c.find(*a, **kw)

        def __getattr__(self, name):
            return getattr(self._c, name)

    monkeypatch.setattr(questions, "_col", lambda: Late(real()))
    again = ask(client, {"title": "并发"})["items"][0]
    assert again["id"] == first["id"] and db().activity_questions.count_documents({}) == 1
    from pymongo.errors import DuplicateKeyError  # noqa: PLC0415

    with pytest.raises(DuplicateKeyError):  # 索引本身：同一 askKey 的 pending 唯一
        db().activity_questions.insert_one({**db().activity_questions.find_one({"id": first["id"]}, {"_id": 0}), "id": "aq_other"})


def test_session_change_stale_claim_recovery(client):
    ids = _world(client)
    eid = _session(client, ids["bucket"])
    v = sc(client, eid, taskId=ids["a"]).json()
    old = datetime.now(timezone.utc) - timedelta(hours=1)
    mark = lambda **kw: db().activity_new_items.update_one({"id": v["id"]}, {"$set": {"status": "accepted", "applyClaimedAt": old, **kw}})  # noqa: E731
    # 占位后别人改挂过（seq 变了）：不重做，回 pending 并 409
    mark(claimSeq=0)
    _reassign(client, eid, taskId=ids["c"])
    assert client.post(f"{P}/{v['id']}/approve").status_code == 409
    assert db().activity_new_items.find_one({"id": v["id"]})["status"] == "pending" and owner(eid)[0] == ids["c"]
    # seq 没变：接着做
    eid2 = _session(client, ids["bucket"], minutes_ago=200)
    w = sc(client, eid2, taskId=ids["b"]).json()
    db().activity_new_items.update_one({"id": w["id"]}, {"$set": {"status": "accepted", "applyClaimedAt": old, "claimSeq": 0}})
    assert client.post(f"{P}/{w['id']}/approve").json()["status"] == "accepted" and owner(eid2)[0] == ids["b"]
    # 其实已经落地了：只收尾
    db().activity_new_items.update_one({"id": w["id"]}, {"$set": {"status": "accepted", "applyClaimedAt": old}})
    assert client.post(f"{P}/{w['id']}/approve").json()["status"] == "accepted" and len(owner(eid2)[1]) == 1


def test_session_change_view_reads_current_owner(client):
    ids = _world(client)
    eid = _session(client, ids["bucket"])
    v = sc(client, eid, taskId=ids["a"]).json()
    assert v["fromTaskId"] == ids["bucket"]
    _reassign(client, eid, taskId=ids["c"])
    got = next(i for i in client.get(P).json()["sessionChanges"] if i["id"] == v["id"])
    assert got["fromTaskId"] == ids["c"] and got["fromProjectId"] == ids["q"]


def test_direct_log_orphan_row_listed_and_undoable(client):
    ids = _world(client)
    eid = _session(client, ids["bucket"])
    direct(client)
    out = sc(client, eid, headers=WRITE, taskId=ids["a"], directApply=True).json()
    db().activity_direct_log.update_one({"id": out["directLogId"]}, {"$set": {"state": "pending"}})  # 落地后、收尾前崩了
    assert [i["id"] for i in client.get(LOG).json()["items"]] == [out["directLogId"]]
    assert client.post(f"{LOG}/{out['directLogId']}/undo").status_code == 200 and owner(eid)[0] == ids["bucket"]
    db().activity_direct_log.insert_one({"user": "u_local", "id": "dl_half", "at": datetime.now(timezone.utc), "actor": "ai", "kind": "blockRule",
                                         "payload": {}, "state": "pending", "before": None, "after": None, "undoneAt": None})
    assert "dl_half" not in [i["id"] for i in client.get(LOG).json()["items"]]  # 还没落地的不显示


def test_ignore_undo_requires_ai_author(client):
    direct(client)
    out = ign(client, WRITE, directApply=True, app="mine").json()
    rid = out["result"]["id"]
    assert db().activity_ignores.find_one({"id": rid})["author"] == "ai"
    assert client.delete(f"{A}/ignores/{rid}").status_code == 204
    assert client.post(f"{A}/ignores", json={"app": "mine", "titleContains": "私密"}).status_code == 201  # 人又建了同一条
    assert client.post(f"{LOG}/{out['directLogId']}/undo").status_code == 409
    assert db().activity_ignores.count_documents({"id": rid}) == 1


def test_direct_assign_same_target_is_noop_without_log(client, assigned):
    tid, other = assigned
    direct(client)
    r = act(client, taskId=tid)  # 已经是 tid
    assert r.status_code == 200 and r.json()["applied"] is False and r.json()["directLogId"] is None and r.json()["result"]["status"] == "assigned"
    assert db().activity_direct_log.count_documents({}) == 0
    assert act(client, taskId=other).json()["applied"] is True and db().activity_direct_log.count_documents({}) == 1


def test_session_change_stale_owner_and_undo_conditional(client, monkeypatch):
    from app.modules.activity import session_items
    ids = _world(client)
    eid = _session(client, ids["bucket"])
    direct(client)
    real, n = session_items._owner, {"n": 0}

    def raced(s):  # 提议读完归属之后、真正改挂之前，人先把它挪到 a
        out = real(s)
        n["n"] += 1
        if n["n"] == 1:
            _reassign(client, eid, taskId=ids["a"])
        return out

    monkeypatch.setattr(session_items, "_owner", raced)
    out = sc(client, eid, headers=WRITE, taskId=ids["b"], directApply=True).json()
    assert db().activity_direct_log.find_one({"id": out["directLogId"]})["before"]["taskId"] == ids["a"]  # 前任归属取写下那一刻的
    # 撤回：_owner 检查通过之后、改挂之前又被人改挂 → 409，不覆盖
    def raced2(s):
        res = real(s)
        if not n.get("done"):
            n["done"] = True
            _reassign(client, eid, taskId=ids["c"])
        return res

    monkeypatch.setattr(session_items, "_owner", raced2)
    assert client.post(f"{LOG}/{out['directLogId']}/undo").status_code == 409
    assert owner(eid)[0] == ids["c"] and db().activity_direct_log.find_one({"id": out["directLogId"]})["undoneAt"] is None


def test_dismiss_refuses_claimed_question(client, tid):
    item = ask(client, q(options=[{"label": "a", "target": {"taskId": tid}}]))["items"][0]
    db().activity_questions.update_one({"id": item["id"]}, {"$set": {"claimed": {"option": 0}}})  # answer 已占位
    assert client.post(f"{A}/questions/{item['id']}/dismiss").status_code == 409
    assert db().activity_questions.find_one({"id": item["id"]})["status"] == "pending"


def test_submit_duplicate_recovery_when_first_already_closed(client, monkeypatch):
    from app.modules.activity import questions
    first = ask(client, q("重复"))["items"][0]
    real, st = questions._col, {"hit": False}

    class Proxy:
        def __init__(self, c): self.c = c
        def __getattr__(self, k): return getattr(self.c, k)
        def find(self, *a, **k):  # 提交时没看到已有的 pending（并发），随后撞唯一键
            return iter([]) if not st["hit"] else self.c.find(*a, **k)
        def find_one(self, *a, **k):  # 恢复查找时，先到的已被答掉
            if not st["hit"]:
                st["hit"] = True
                self.c.update_one({"id": first["id"]}, {"$set": {"status": "answered"}})
                return None
            return self.c.find_one(*a, **k)

    monkeypatch.setattr(questions, "_col", lambda: Proxy(real()))
    got = ask(client, q("重复"))["items"][0]
    assert got["id"] != first["id"] and got["status"] == "pending"
