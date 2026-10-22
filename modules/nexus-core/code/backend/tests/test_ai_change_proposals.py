"""v2.36 AI 提议改名 / 搬移 / 删除、提议忽略规则：AI 只提议，人批准才动手；批准 / 不要只许人。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from app.modules.activity import new_items

API = "/api/core/activity/proposals"
BEARER = {"Authorization": "Bearer " + "x" * 8}


def _db():
    from app.repo import get_db  # noqa: PLC0415

    return get_db()


def _chg(client, target, tid, op, **kw):
    return client.post(f"{API}/changes", json={"target": target, "targetId": tid, "op": op, "reason": "r", **kw})


def _ign(client, app="Chrome", **kw):
    return client.post(f"{API}/ignores", json={"app": app, "reason": "r", **kw})


def _task(seeded=None):
    return _db()["tasks"].find_one({"kind": {"$ne": "unclassified"}})


def _empty_project(client, seeded):
    z = next(iter(seeded["zones"].values()))["id"]
    pid = client.post(f"{API}/projects", json={"zoneId": z, "name": "空项目"}).json()
    return client.post(f"{API}/{pid['id']}/approve").json()["createdId"], z


def test_propose_each_op_writes_nothing(client, seeded):
    t = _task()
    proj = _db()["projects"].find_one({"id": t["projectId"]})
    other = _db()["projects"].find_one({"id": {"$ne": proj["id"]}})
    before = _db()["tasks"].find_one({"id": t["id"]})
    r = _chg(client, "task", t["id"], "rename", name=" 新  名 ")
    assert r.status_code == 200 and r.json()["id"].startswith("nc_") and r.json()["status"] == "pending"
    assert r.json()["name"] == "新  名" and r.json()["targetName"] == t["name"] and r.json()["projectId"] == proj["id"]
    assert _chg(client, "task", t["id"], "rename", name="新  名").json()["id"] == r.json()["id"]
    m = _chg(client, "task", t["id"], "move", toId=other["id"]).json()
    assert m["toName"] == other["name"] and m["toId"] == other["id"]
    assert _chg(client, "project", other["id"], "rename", name="另名").status_code == 200
    assert _db()["tasks"].find_one({"id": t["id"]}) == before


def test_validation_refusals(client, seeded):
    t = _task()
    sib = _db()["tasks"].find_one({"projectId": t["projectId"], "id": {"$ne": t["id"]}})
    proj = _db()["projects"].find_one({"id": t["projectId"]})
    assert _chg(client, "task", "t_nope", "delete").status_code == 400
    assert _chg(client, "project", "p_inbox", "rename", name="x").status_code == 400
    bucket = {"id": "t_unc_x", "projectId": proj["id"], "name": "未分类", "kind": "unclassified", "done": False}
    _db()["tasks"].insert_one(dict(bucket))
    assert _chg(client, "task", "t_unc_x", "delete").status_code == 400
    assert _chg(client, "task", t["id"], "rename").status_code == 400  # 缺 name
    assert _chg(client, "task", t["id"], "delete", name="x").status_code == 400
    assert _chg(client, "task", t["id"], "rename", name=t["name"].upper()).status_code == 400  # 同名（自己）
    if sib:
        assert _chg(client, "task", t["id"], "rename", name=sib["name"]).status_code == 409
    assert _chg(client, "task", t["id"], "move", toId="p_nope").status_code == 400
    assert _chg(client, "task", t["id"], "move", toId=proj["id"]).status_code == 400  # 就在那
    assert _chg(client, "project", proj["id"], "move", toId=proj["zoneId"]).status_code == 400
    r = _chg(client, "project", proj["id"], "delete")
    assert r.status_code == 409 and "先提议删掉或移走" in r.json()["detail"]
    if sib:
        _db()["tasks"].update_one({"id": sib["id"]}, {"$set": {"dependsOn": [t["id"]]}})
        assert _chg(client, "task", t["id"], "delete").status_code == 409
    assert client.post(f"{API}/changes", json={"target": "task", "targetId": "x", "op": "zap"}).status_code == 422
    assert client.post(f"{API}/changes", headers=BEARER, json={"target": "task", "targetId": t["id"], "op": "delete"}).status_code == 403


def test_approve_rename_move_delete_through_guard(client, seeded):
    t = _task()
    other = _db()["projects"].find_one({"id": {"$ne": t["projectId"]}})
    a = _chg(client, "task", t["id"], "rename", name="改后名").json()["id"]
    r = client.post(f"{API}/{a}/approve")
    assert r.status_code == 200 and r.json()["status"] == "accepted"
    assert _db()["tasks"].find_one({"id": t["id"]})["name"] == "改后名"
    assert _db()["planner_audit"].count_documents({"objectType": "tasks", "op": "update", "outcome": "applied", "objectId": t["id"]}) == 1
    b = _chg(client, "task", t["id"], "move", toId=other["id"]).json()["id"]
    assert client.post(f"{API}/{b}/approve").status_code == 200
    assert _db()["tasks"].find_one({"id": t["id"]})["projectId"] == other["id"]
    c = _chg(client, "task", t["id"], "delete").json()["id"]
    assert client.post(f"{API}/{c}/approve").status_code == 200
    assert _db()["tasks"].find_one({"id": t["id"]}) is None
    assert _db()["planner_audit"].count_documents({"objectType": "tasks", "op": "delete", "outcome": "applied", "objectId": t["id"]}) == 1
    pid, z = _empty_project(client, seeded)
    z2 = "z_other"
    _db()["zones"].insert_one({"id": z2, "key": "9", "name": "另一分区"})
    d = _chg(client, "project", pid, "move", toId=z2).json()["id"]
    assert client.post(f"{API}/{d}/approve").status_code == 200 and _db()["projects"].find_one({"id": pid})["zoneId"] == z2
    e = _chg(client, "project", pid, "delete").json()["id"]
    assert client.post(f"{API}/{e}/approve").status_code == 200 and _db()["projects"].find_one({"id": pid}) is None


def test_non_human_approve_403_and_reject_forever(client, seeded):
    t = _task()
    a = _chg(client, "task", t["id"], "delete").json()["id"]
    for h in (BEARER, {"X-Nexus-Scope": "write"}, {"X-Nexus-Anonymous": "1"}):
        assert client.post(f"{API}/{a}/approve", headers=h).status_code == 403
    assert _db()["tasks"].find_one({"id": t["id"]}) is not None
    assert client.post(f"{API}/{a}/reject").json()["status"] == "rejected"
    assert _chg(client, "task", t["id"], "delete").status_code == 409
    assert client.post(f"{API}/{a}/approve").status_code == 409


def test_double_approve_loser_waits_for_winner_and_gets_real_view(client, seeded, monkeypatch):
    import threading  # noqa: PLC0415
    import time  # noqa: PLC0415
    from types import SimpleNamespace  # noqa: PLC0415
    from app.modules.planner import service  # noqa: PLC0415

    t = _task()
    a = _chg(client, "task", t["id"], "rename", name="只改一次").json()["id"]
    real, out, entered = service.update_task, {}, threading.Event()

    def sleep(sec):  # 输家进了等待循环
        entered.set()
        time.sleep(sec)

    monkeypatch.setattr(new_items, "time", SimpleNamespace(monotonic=time.monotonic, sleep=sleep))

    def slow(*args, **kw):  # 赢家停在落地中间，直到输家确实在等
        th = threading.Thread(target=lambda: out.update(r=client.post(f"{API}/{a}/approve")))
        th.start()
        out["th"] = th
        assert entered.wait(10)
        return real(*args, **kw)

    monkeypatch.setattr(service, "update_task", slow)
    first = client.post(f"{API}/{a}/approve")
    out["th"].join()
    assert first.status_code == 200 and out["r"].status_code == 200
    assert out["r"].json()["status"] == "accepted" and out["r"].json()["targetName"] == "只改一次"
    assert _db()["planner_audit"].count_documents({"op": "update", "outcome": "applied", "objectId": t["id"]}) == 1
    assert _db()["activity_new_items"].find_one({"id": a})["applyClaimedAt"] is None


def test_precheck_failure_on_rejected_item_is_409_not_a_view(client, seeded, monkeypatch):
    t = _task()
    a = _chg(client, "task", t["id"], "delete").json()["id"]

    def late(d):
        _db()["activity_new_items"].update_one({"id": a}, {"$set": {"status": "rejected"}})
        raise new_items.ConflictError("x")

    monkeypatch.setattr(new_items, "_precheck", late)
    assert client.post(f"{API}/{a}/approve").status_code == 409


def test_stale_takeover_with_sibling_clash_releases_to_pending(client, seeded):
    from datetime import timedelta  # noqa: PLC0415

    t = _task()
    sib = _db()["tasks"].find_one({"projectId": t["projectId"], "id": {"$ne": t["id"]}, "kind": {"$ne": "unclassified"}})
    a = _chg(client, "task", t["id"], "rename", name="将撞名2").json()["id"]
    _db()["tasks"].update_one({"id": sib["id"]}, {"$set": {"name": "将撞名2"}})
    _db()["activity_new_items"].update_one({"id": a}, {"$set": {"status": "accepted",
                                                               "applyClaimedAt": new_items._now() - timedelta(hours=1)}})
    r = client.post(f"{API}/{a}/approve")
    assert r.status_code == 409
    d = _db()["activity_new_items"].find_one({"id": a})
    assert d["status"] == "pending" and d["applyClaimedAt"] is None and "decidedAt" not in d
    assert _db()["tasks"].find_one({"id": t["id"]})["name"] == t["name"]
    assert _db()["tasks"].count_documents({"name": "将撞名2"}) == 1


def test_delete_target_gone_mid_apply_counts_as_done(client, seeded, monkeypatch):
    from app.modules.planner import service  # noqa: PLC0415

    t = _task()
    a = _chg(client, "task", t["id"], "delete").json()["id"]
    real = service.delete_task

    def gone(task_id):  # 别人抢先删了：service 抛 NotFound
        _db()["tasks"].delete_one({"id": task_id})
        return real(task_id)

    monkeypatch.setattr(service, "delete_task", gone)
    r = client.post(f"{API}/{a}/approve")
    assert r.status_code == 200 and r.json()["status"] == "accepted"
    assert _db()["activity_new_items"].find_one({"id": a})["applyClaimedAt"] is None


def test_loser_whose_precheck_failed_only_because_winner_finished_gets_view(client, seeded, monkeypatch):
    t = _task()
    a = _chg(client, "task", t["id"], "rename", name="赢家改的").json()["id"]

    def late(d):  # 复查时赢家刚好落地完：提议已 accepted、占位已放
        _db()["tasks"].update_one({"id": t["id"]}, {"$set": {"name": "赢家改的"}})
        _db()["activity_new_items"].update_one({"id": a}, {"$set": {"status": "accepted"}})
        raise new_items.ConflictError("目标已被改过")

    monkeypatch.setattr(new_items, "_precheck", late)
    r = client.post(f"{API}/{a}/approve")
    assert r.status_code == 200 and r.json()["status"] == "accepted"


def test_loser_times_out_with_409_while_claim_is_fresh(client, seeded, monkeypatch):
    t = _task()
    a = _chg(client, "task", t["id"], "rename", name="占着").json()["id"]
    _db()["activity_new_items"].update_one({"id": a}, {"$set": {"status": "accepted", "applyClaimedAt": new_items._now()}})
    monkeypatch.setattr(new_items.proposals, "WAIT_SECONDS", 0.3)
    assert client.post(f"{API}/{a}/approve").status_code == 409


def test_stale_claim_is_taken_over_and_applied(client, seeded):
    from datetime import timedelta  # noqa: PLC0415

    t = _task()
    a = _chg(client, "task", t["id"], "rename", name="接手改").json()["id"]
    old = new_items._now() - timedelta(hours=1)  # 上一个批准崩在落地前
    _db()["activity_new_items"].update_one({"id": a}, {"$set": {"status": "accepted", "applyClaimedAt": old}})
    r = client.post(f"{API}/{a}/approve")
    assert r.status_code == 200 and r.json()["targetName"] == "接手改"
    assert _db()["activity_new_items"].find_one({"id": a})["applyClaimedAt"] is None
    assert _db()["planner_audit"].count_documents({"op": "update", "outcome": "applied", "objectId": t["id"]}) == 1


def test_stale_claim_already_reflected_just_releases(client, seeded):
    from datetime import timedelta  # noqa: PLC0415

    t = _task()
    a = _chg(client, "task", t["id"], "rename", name="早已改好").json()["id"]
    _db()["tasks"].update_one({"id": t["id"]}, {"$set": {"name": "早已改好"}})  # 改成了，只是没来得及放占位
    _db()["activity_new_items"].update_one({"id": a}, {"$set": {"status": "accepted",
                                                               "applyClaimedAt": new_items._now() - timedelta(hours=1)}})
    assert client.post(f"{API}/{a}/approve").status_code == 200
    assert _db()["activity_new_items"].find_one({"id": a})["applyClaimedAt"] is None
    assert _db()["planner_audit"].count_documents({"objectId": t["id"], "outcome": "applied"}) == 0
    i = _ign(client, "Gone").json()["id"]  # ignore：规则已在 → 补 createdId
    from datetime import timedelta as td  # noqa: PLC0415,N813
    rid = client.post("/api/core/activity/ignores", json={"app": "Gone"}).json()["id"]
    _db()["activity_new_items"].update_one({"id": i}, {"$set": {"status": "accepted", "applyClaimedAt": new_items._now() - td(hours=1)}})
    r = client.post(f"{API}/{i}/approve").json()
    assert r["createdId"] == rid


def test_move_clash_and_snapparent_mismatch_at_approve(client, seeded):
    t = _task()
    other = _db()["projects"].find_one({"id": {"$ne": t["projectId"]}})
    third = {"id": "p_third", "zoneId": other["zoneId"], "name": "第三个", "key": "1-99"}
    _db()["projects"].insert_one(third)
    m = _chg(client, "task", t["id"], "move", toId=other["id"]).json()["id"]
    _db()["tasks"].insert_one({"id": "t_dup2", "projectId": other["id"], "name": t["name"], "done": False})  # 提议后目的地有了同名
    r = client.post(f"{API}/{m}/approve")
    assert r.status_code == 409 and "t_dup2" in r.json()["detail"]
    assert _db()["activity_new_items"].find_one({"id": m})["status"] == "pending"
    _db()["tasks"].delete_one({"id": "t_dup2"})
    _db()["tasks"].update_one({"id": t["id"]}, {"$set": {"projectId": "p_third"}})  # 别处搬走了：父级变了
    r = client.post(f"{API}/{m}/approve")
    assert r.status_code == 409 and "已被改过" in r.json()["detail"]
    # 再提同一条：快照刷新，然后能批
    assert _chg(client, "task", t["id"], "move", toId=other["id"]).json()["id"] == m
    assert _db()["activity_new_items"].find_one({"id": m})["snapParent"] == "p_third"
    assert client.post(f"{API}/{m}/approve").status_code == 200
    assert _db()["tasks"].find_one({"id": t["id"]})["projectId"] == other["id"]


def test_approve_refuses_sibling_clash_and_stale_snapshot(client, seeded):
    t = _task()
    sib = _db()["tasks"].find_one({"projectId": t["projectId"], "id": {"$ne": t["id"]}, "kind": {"$ne": "unclassified"}})
    a = _chg(client, "task", t["id"], "rename", name="将撞名").json()["id"]
    _db()["tasks"].update_one({"id": sib["id"]}, {"$set": {"name": "将撞名"}})  # 提议之后同级有了同名
    r = client.post(f"{API}/{a}/approve")
    assert r.status_code == 409 and sib["id"] in r.json()["detail"]
    assert _db()["activity_new_items"].find_one({"id": a})["status"] == "pending"
    assert _db()["tasks"].find_one({"id": t["id"]})["name"] == t["name"]
    b = _chg(client, "task", t["id"], "rename", name="快照").json()["id"]
    _db()["tasks"].update_one({"id": t["id"]}, {"$set": {"name": "被人改了"}})
    r = client.post(f"{API}/{b}/approve")
    assert r.status_code == 409 and "已被改过" in r.json()["detail"]
    other = _db()["projects"].find_one({"id": {"$ne": t["projectId"]}})  # 搬移：目的地已有同名
    _db()["tasks"].insert_one({"id": "t_dup", "projectId": other["id"], "name": "被人改了", "done": False})
    assert _chg(client, "task", t["id"], "move", toId=other["id"]).status_code == 409


def test_move_to_inbox_allowed(client, seeded):
    t = _task()
    _db()["projects"].insert_one({"id": "p_inbox", "zoneId": next(iter(seeded["zones"].values()))["id"], "name": "收件箱", "key": "1-9"})
    a = _chg(client, "task", t["id"], "move", toId="p_inbox").json()["id"]
    assert client.post(f"{API}/{a}/approve").status_code == 200
    assert _db()["tasks"].find_one({"id": t["id"]})["projectId"] == "p_inbox"


def test_replace_refused_while_approve_in_flight(client, seeded):
    t = _task()
    a = _chg(client, "task", t["id"], "rename", name="在飞").json()["id"]
    _db()["activity_new_items"].update_one({"id": a}, {"$set": {"status": "accepted", "applyClaimedAt": new_items._now()}})
    assert _chg(client, "task", t["id"], "rename", name="在飞").status_code == 409


def test_listing_limit_is_per_kind(client, seeded):
    t = _task()
    z = next(iter(seeded["zones"].values()))["id"]
    client.post(f"{API}/projects", json={"zoneId": z, "name": "老项目"})
    _chg(client, "task", t["id"], "delete")
    for i in range(3):
        _ign(client, f"app{i}")
    body = client.get(f"{API}?limit=1").json()
    assert (len(body["projects"]), len(body["changes"]), len(body["ignores"])) == (1, 1, 1)


def test_target_gone_409_and_failure_reverts_to_pending(client, seeded):
    t = _task()
    a = _chg(client, "task", t["id"], "rename", name="另一个").json()["id"]
    _db()["tasks"].delete_one({"id": t["id"]})
    r = client.post(f"{API}/{a}/approve")
    assert r.status_code == 409 and "已不存在" in r.json()["detail"]
    assert _db()["activity_new_items"].find_one({"id": a})["status"] == "pending"
    assert client.get(API).json()["changes"][0]["targetName"] is None


def test_apply_failure_reverts_to_pending(client, seeded, monkeypatch):
    import pytest  # noqa: PLC0415
    from app.modules.planner import service  # noqa: PLC0415

    t = _task()
    b = _chg(client, "task", t["id"], "rename", name="撞名").json()["id"]
    monkeypatch.setattr(service, "update_task", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        client.post(f"{API}/{b}/approve")
    d = _db()["activity_new_items"].find_one({"id": b})
    assert d["status"] == "pending" and "decidedAt" not in d and d["applyClaimedAt"] is None


def test_ignore_proposal_approve_creates_rule_and_hides_title_from_non_human(client, seeded):
    r = _ign(client, "Chrome", titleContains="银行 ").json()
    assert r["id"].startswith("ni_") and r["titleContains"] == "银行" and r["createdId"] is None
    listed = client.get(API, headers=BEARER).json()["ignores"][0]
    assert "titleContains" not in listed and listed["hasTitleFilter"] is True
    assert client.get(API).json()["ignores"][0]["titleContains"] == "银行"
    assert client.post(f"{API}/{r['id']}/approve", headers=BEARER).status_code == 403
    ok = client.post(f"{API}/{r['id']}/approve").json()
    assert ok["status"] == "accepted" and ok["createdId"].startswith("ig_")
    rules = client.get("/api/core/activity/ignores").json()["items"]
    assert [x["id"] for x in rules] == [ok["createdId"]]
    assert _ign(client, "chrome", titleContains="银行").status_code == 409  # 已有同样的规则
    assert _ign(client, "x" * 129).status_code == 400
    assert _ign(client, "Chrome", titleContains="y" * 201).status_code == 400
    assert _ign(client, "Chrome", titleContains="银行").status_code == 409
    n = _ign(client, "Slack").json()["id"]
    client.post(f"{API}/{n}/reject")
    assert _ign(client, "slack").status_code == 409


def test_caps(client, seeded, monkeypatch):
    monkeypatch.setitem(new_items.MAX_PENDING, "ignore", 2)
    assert [_ign(client, f"app{i}").status_code for i in range(3)] == [200, 200, 429]
    monkeypatch.setitem(new_items.MAX_PENDING, "change", 1)
    ts = list(_db()["tasks"].find({"kind": {"$ne": "unclassified"}}).limit(2))
    assert [_chg(client, "task", x["id"], "delete").status_code for x in ts] == [200, 429]


def test_listing_keeps_old_keys_and_separates_kinds(client, seeded):
    t = _task()
    _chg(client, "task", t["id"], "delete")
    _ign(client)
    body = client.get(API).json()
    assert body["projects"] == [] and body["tasks"] == [] and len(body["changes"]) == 1 and len(body["ignores"]) == 1
