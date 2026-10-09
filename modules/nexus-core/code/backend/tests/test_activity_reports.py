"""AI 报告（契约 v2.20「AI 报告」节）。

要害一句：**AI 只提议，批准是人点的，而且批准走的是跟手点完全一样的 confirm。** 所以最重的断言是：
提交之后台账一个字节不动；全部批准之后每段至多一条事实（含并发）；Bearer / 匿名 / read 范围碰不了批准；
别的租户的 id 看不出存在与否；事实带 ``ai.report`` 出处。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from test_activity_suggestions import BEARER, SUG, A, B, _db, _match, _pending, _recent, _seg, _session_events, _upload
from test_session_reassign_races import EVENTS, _external, _post, _world

API = "/api/core"
REPORTS = f"{API}/activity/reports"
EVIL = '<img src=x onerror="window.__pwned=1">'


def _ids(client, n, headers=None, device=None, **over):
    """n 段待确认（不同开始时刻），新的在前。"""
    _upload(client, [_seg(_recent(10 * (i + 1)), **over) for i in range(n)], headers=headers, **({"device": device} if device else {}))
    return [i["id"] for i in _pending(client, headers)["items"]]


def _submit(client, items, summary="汇总", headers=None, expect=200, **extra):
    resp = client.post(REPORTS, json={"summary": summary, "items": items, **extra}, headers=headers or {})
    assert resp.status_code == expect, resp.text
    return resp.json()


def _world_ids(seeded):
    t = seeded["tasks"]["示例任务三"]
    return t["id"], t["projectId"]


def _extra_task(project, name):
    from app.modules.planner import service as planner  # noqa: PLC0415

    return planner.create_task(name, project)["id"]


def _status(client, rid, headers=None):
    return client.get(f"{REPORTS}/{rid}", headers=headers or {}).json()


# ─────────────────────────────────────────── 提交


def test_submit_stores_report_and_writes_nothing(client, seeded):
    ids = _ids(client, 4)
    task, project = _world_ids(seeded)
    out = _submit(client, [
        {"kind": "assign", "suggestionIds": ids[:2], "taskId": task, "reason": EVIL},
        {"kind": "assign", "suggestionIds": [ids[2]], "projectId": project},
        {"kind": "dismiss", "suggestionIds": [ids[3]]}], summary=EVIL, author="hermes")
    assert out["accepted"] == 3 and out["rejected"] == [] and out["status"] == "pending"
    assert _session_events() == [] and _pending(client)["total"] == 4  # 什么都没入账、没忽略
    rep = _status(client, out["reportId"])
    assert rep["summary"] == EVIL and rep["author"] == "hermes" and rep["items"][0]["reason"] == EVIL  # 纯文本原样
    assert rep["counts"] == {"items": 3, "pending": 3, "byKind": {"assign": 2, "dismiss": 1}, "seconds": 4 * 240}
    assert [i["id"] for i in rep["items"]] == ["i0", "i1", "i2"]
    assert all(s["status"] == "pending" for i in rep["items"] for s in i["suggestions"])
    assert "suggestions" not in client.get(f"{REPORTS}/{out['reportId']}", params={"resolve": "false"}).json()["items"][0]


def test_submit_rejects_bad_items_per_item_and_keeps_the_rest(client, seeded):
    ids = _ids(client, 4)
    task, _ = _world_ids(seeded)
    other = _ids(client, 1, headers=B, device="dev_0000000000000000")[0]  # 别的租户的真 id（id 由设备+开始时刻派生，换设备才不撞）
    items = [
        {"kind": "assign", "suggestionIds": ids[:1], "taskId": task},
        {"kind": "assign", "suggestionIds": [other], "taskId": task},          # 别的租户的
        {"kind": "assign", "suggestionIds": ["sug_nope"], "taskId": task},      # 不存在
        {"kind": "assign", "suggestionIds": [ids[1]], "taskId": "t_nope"},
        {"kind": "assign", "suggestionIds": [ids[1]], "projectId": "p_nope"},
        {"kind": "assign", "suggestionIds": [ids[1]]},                          # 没目标
        {"kind": "dismiss", "suggestionIds": [ids[1]], "taskId": task},         # dismiss 不许给目标
        {"kind": "assign", "suggestionIds": [ids[0]], "taskId": task},          # 同一份里重复引用
        {"kind": "assign", "suggestionIds": [ids[1]], "taskId": task, "reason": "x" * 301},
        {"kind": "bogus", "suggestionIds": [ids[1]]},
        "not-an-object",
        {"kind": "assign", "suggestionIds": [ids[2]], "taskId": task, "extra": 1}]
    out = _submit(client, items)
    assert out["accepted"] == 1
    got = {r["index"]: r["code"] for r in out["rejected"]}
    assert got == {1: "unknown_suggestion", 2: "unknown_suggestion", 3: "unknown_task", 4: "unknown_project",
                   5: "invalid_item", 6: "invalid_item", 7: "duplicate_suggestion", 8: "invalid_item",
                   9: "invalid_item", 10: "invalid_item", 11: "invalid_item"}
    reasons = {r["index"]: r["reason"] for r in out["rejected"]}
    assert reasons[1].replace(other, "X") == reasons[2].replace("sug_nope", "X")  # 别租户的与不存在的同一句话，没有存在性预言机


def test_submit_with_no_valid_item_stores_nothing(client, seeded):
    ids = _ids(client, 2)
    task, _ = _world_ids(seeded)
    first = _submit(client, [{"kind": "assign", "suggestionIds": ids[:1], "taskId": task}], author="a")
    none = _submit(client, [{"kind": "assign", "suggestionIds": ["sug_nope"], "taskId": task}], author="a")
    assert none["reportId"] is None and none["accepted"] == 0
    assert _status(client, first["reportId"])["status"] == "pending"


def test_oversized_input_is_422(client, seeded):
    task, _ = _world_ids(seeded)
    one = {"kind": "dismiss", "suggestionIds": ["sug_x"]}
    _submit(client, [one] * 201, expect=422)
    _submit(client, [one], summary="x" * 2001, expect=422)
    _submit(client, [one], author="x" * 65, expect=422)
    _submit(client, [{"kind": "dismiss", "suggestionIds": [f"s{i}" for i in range(201)]}], expect=200)  # 条内超限 = 该条被拒
    assert client.post(REPORTS, json={"summary": "x"}).status_code == 422
    assert client.post(REPORTS, json={"summary": 1, "items": []}).status_code == 422


def test_collection_selector_resolves_at_submit(client, seeded):
    ids = _ids(client, 3)
    _, project = _world_ids(seeded)
    assert client.post(f"{SUG}/matches", json={"matches": [
        {"id": i, "collection": {"name": "Write Docs"}} for i in ids[:2]]}).json()["matched"] == 2
    out = _submit(client, [{"kind": "assign", "collection": " write   DOCS ", "projectId": project},
                           {"kind": "assign", "collection": "没有这个", "projectId": project}])
    assert out["accepted"] == 1 and out["rejected"][0]["code"] == "unknown_suggestion"
    item = _status(client, out["reportId"])["items"][0]
    assert sorted(s["id"] for s in item["suggestions"]) == sorted(ids[:2])


def test_new_task_item_validates_like_matches(client, seeded):
    ids = _ids(client, 2)
    task, project = _world_ids(seeded)
    same = seeded["tasks"]["示例任务三"]["name"]
    out = _submit(client, [{"kind": "newTask", "suggestionIds": ids[:1], "newTask": {"projectId": project, "name": same}},
                           {"kind": "newTask", "suggestionIds": ids[1:], "newTask": {"projectId": project, "name": " "}},
                           {"kind": "newTask", "suggestionIds": ids[1:], "newTask": {"projectId": project, "name": "重构存档"}}])
    assert out["accepted"] == 1 and {r["code"] for r in out["rejected"]} == {"newtask_refused"}
    assert _db()["tasks"].count_documents({"name": "重构存档"}) == 0  # 提交不建任务


# ─────────────────────────────────────────── 只收人 / 范围 / 租户


def test_only_humans_can_submit_read_or_decide(client, seeded):
    ids = _ids(client, 2)
    task, _ = _world_ids(seeded)
    rid = _submit(client, [{"kind": "assign", "suggestionIds": ids[:1], "taskId": task}])["reportId"]
    calls = [("post", REPORTS, {"summary": "x", "items": []}), ("get", REPORTS, None), ("get", f"{REPORTS}/{rid}", None),
             ("post", f"{REPORTS}/{rid}/approve", None), ("post", f"{REPORTS}/{rid}/reject", None),
             ("post", f"{REPORTS}/{rid}/items/i0/approve", {"taskId": task}), ("post", f"{REPORTS}/{rid}/items/i0/reject", None)]
    for headers in (BEARER, {"X-Nexus-Scope": "read"}, {"X-Nexus-Scope": "report", "X-Nexus-Anonymous": "1"}):
        for method, url, body in calls:
            if method == "get" and headers.get("X-Nexus-Scope") == "read":
                continue  # read 范围本来就能 GET（v2.19）；Bearer 的 GET 仍 403
            resp = getattr(client, method)(url, headers=headers, **({"json": body} if body is not None else {}))
            assert resp.status_code == 403, (headers, url, resp.text)
    # Bearer 带坏请求体：先 403，不是 422
    assert client.post(REPORTS, headers=BEARER, content=b"{not json").status_code in (403, 422)
    assert client.post(REPORTS, headers=BEARER, json={"summary": 1}).status_code == 403
    assert _session_events() == [] and _status(client, rid)["status"] == "pending"


def test_tenant_isolation_on_every_id(client, seeded):
    ids = _ids(client, 2)
    task, _ = _world_ids(seeded)
    rid = _submit(client, [{"kind": "assign", "suggestionIds": ids[:1], "taskId": task}])["reportId"]
    for method, url in (("get", f"{REPORTS}/{rid}"), ("post", f"{REPORTS}/{rid}/approve"), ("post", f"{REPORTS}/{rid}/reject"),
                        ("post", f"{REPORTS}/{rid}/items/i0/approve"), ("post", f"{REPORTS}/{rid}/items/i0/reject")):
        assert getattr(client, method)(url, headers=B).status_code == 404
    assert client.get(REPORTS, headers=B).json() == {"items": []}
    assert _session_events() == []


# ─────────────────────────────────────────── 批准


def test_approve_all_applies_every_kind_through_confirm_with_provenance(client, seeded):
    ids = _ids(client, 6)
    task, project = _world_ids(seeded)
    rid = _submit(client, [
        {"kind": "assign", "suggestionIds": ids[:2], "taskId": task},
        {"kind": "assign", "suggestionIds": ids[2:3], "projectId": project},
        {"kind": "newTask", "suggestionIds": ids[3:5], "newTask": {"projectId": project, "name": "重构存档"}},
        {"kind": "dismiss", "suggestionIds": ids[5:]}], author="hermes")["reportId"]
    out = client.post(f"{REPORTS}/{rid}/approve")
    assert out.status_code == 200, out.text
    body = out.json()
    assert body["status"] == "approved" and (body["applied"], body["stale"], body["failed"]) == (4, 0, 0)
    assert [i["applied"] for i in body["items"]] == [2, 1, 2, 1]
    events = _session_events()
    assert len(events) == 5  # 5 段入账，1 段忽略
    new = _db()["tasks"].find_one({"name": "重构存档"})
    assert _db()["tasks"].count_documents({"name": "重构存档"}) == 1
    by_task = sorted(e["subject"]["task"] for e in events)
    assert by_task.count(task) == 2 and by_task.count(new["id"]) == 2 and by_task.count("t_unc_" + project) == 1
    for e in events:  # 与手点同形（source / 防重键），多一个出处
        assert e["source"] == "activity-confirmed" and e["dedupeKey"].startswith("activity:sug_")
        assert e["ai"] == {"generated": True, "confidence": 0.9, "confirmed": True,
                           "report": {"id": rid, "author": "hermes"}}
    assert _pending(client)["total"] == 0
    assert _pending(client, status="dismissed")["total"] == 1


def test_approve_all_is_idempotent_and_reapproving_changes_nothing(client, seeded):
    ids = _ids(client, 3)
    task, _ = _world_ids(seeded)
    rid = _submit(client, [{"kind": "assign", "suggestionIds": ids, "taskId": task}])["reportId"]
    assert client.post(f"{REPORTS}/{rid}/approve").json()["applied"] == 1
    again = client.post(f"{REPORTS}/{rid}/approve").json()
    assert (again["status"], again["applied"], again["stale"], again["failed"]) == ("approved", 0, 0, 0)
    assert len(_session_events()) == 3
    assert client.post(f"{REPORTS}/{rid}/reject").status_code == 409  # 批准过的不能再整份不要


def test_stale_suggestions_are_skipped_not_errors(client, seeded):
    ids = _ids(client, 4)
    task, project = _world_ids(seeded)
    rid = _submit(client, [{"kind": "assign", "suggestionIds": ids[:2], "taskId": task},
                           {"kind": "assign", "suggestionIds": ids[2:3], "taskId": task},
                           {"kind": "dismiss", "suggestionIds": ids[3:]}])["reportId"]
    assert client.post(f"{SUG}/{ids[0]}/confirm", json={"taskId": task}).status_code == 200  # 人先手点了第一段
    assert client.post(f"{SUG}/{ids[2]}/dismiss").status_code == 200  # 第三段被人忽略
    live = _status(client, rid)["items"]
    assert [i["staleNow"] for i in live] == [1, 1, 0]
    body = client.post(f"{REPORTS}/{rid}/approve").json()
    assert (body["applied"], body["stale"], body["failed"]) == (2, 1, 0)
    assert [(i["status"], i["applied"], i["stale"]) for i in body["items"]] == [
        ("applied", 1, 1), ("stale", 0, 1), ("applied", 1, 0)]
    assert len(_session_events()) == 2  # 手点的 + 批准的第二段，没有重复
    assert all(e["subject"]["task"] == task for e in _session_events())


def test_one_failure_does_not_block_others_and_failed_item_can_be_retried(client, seeded):
    ids = _ids(client, 3)
    task, project = _world_ids(seeded)
    doomed = _extra_task(project, "会被删的任务")
    rid = _submit(client, [{"kind": "assign", "suggestionIds": ids[:1], "taskId": doomed},
                           {"kind": "assign", "suggestionIds": ids[1:], "taskId": task}])["reportId"]
    _db()["tasks"].delete_one({"id": doomed})
    body = client.post(f"{REPORTS}/{rid}/approve").json()
    assert body["status"] == "pending" and (body["applied"], body["failed"]) == (1, 1)
    assert body["items"][0]["status"] == "failed" and body["items"][0]["failure"]
    assert len(_session_events()) == 2 and _pending(client)["total"] == 1  # 失败的那段留在待确认
    # 改目标后再批准：改的目标存下并被使用
    out = client.post(f"{REPORTS}/{rid}/items/i0/approve", json={"taskId": task})
    assert out.status_code == 200 and out.json()["status"] == "applied" and out.json()["reportStatus"] == "approved"
    assert len(_session_events()) == 3


def test_edit_then_approve_item_persists_target_and_uses_it(client, seeded):
    ids = _ids(client, 3)
    task, project = _world_ids(seeded)
    other = _extra_task(project, "另一个任务")
    rid = _submit(client, [{"kind": "newTask", "suggestionIds": ids[:2], "newTask": {"projectId": project, "name": "新的"}},
                           {"kind": "assign", "suggestionIds": ids[2:], "taskId": task}])["reportId"]
    assert client.post(f"{REPORTS}/{rid}/items/i0/approve", json={"taskId": task, "projectId": project}).status_code == 400
    assert client.post(f"{REPORTS}/{rid}/items/i0/approve", json={"taskId": "t_nope"}).status_code == 404
    assert client.post(f"{REPORTS}/{rid}/items/i0/approve", json={"projectId": "p_nope"}).status_code == 404
    out = client.post(f"{REPORTS}/{rid}/items/i0/approve", json={"taskId": other}).json()
    assert out["status"] == "applied" and out["applied"] == 2 and out["reportStatus"] == "pending"
    assert {e["subject"]["task"] for e in _session_events()} == {other}
    assert _db()["tasks"].count_documents({"name": "新的"}) == 0  # 改成现成任务后，提议的新任务没建
    item = _status(client, rid)["items"][0]
    assert item["kind"] == "assign" and item["taskId"] == other and item["newTask"] is None
    assert client.post(f"{REPORTS}/{rid}/items/i0/approve", json={"taskId": task}).status_code == 409  # 已处理不能再改
    again = client.post(f"{REPORTS}/{rid}/items/i0/approve").json()  # 重点 = 什么都不做
    assert again["status"] == "applied" and len(_session_events()) == 2
    # dismiss 条不能改目标
    rid2 = _submit(client, [{"kind": "dismiss", "suggestionIds": ids[2:]}], author="b")["reportId"]
    assert client.post(f"{REPORTS}/{rid2}/items/i0/approve", json={"taskId": task}).status_code == 400


def test_reject_item_and_reject_report(client, seeded):
    ids = _ids(client, 4)
    task, project = _world_ids(seeded)
    rid = _submit(client, [{"kind": "assign", "suggestionIds": ids[:1], "taskId": task},
                           {"kind": "newTask", "suggestionIds": ids[1:2], "newTask": {"projectId": project, "name": "不要的"}},
                           {"kind": "assign", "suggestionIds": ids[2:3], "taskId": task}])["reportId"]
    assert client.post(f"{REPORTS}/{rid}/items/i1/reject").json()["status"] == "rejected"
    assert client.post(f"{REPORTS}/{rid}/items/i1/reject").status_code == 200  # 幂等
    assert client.post(f"{REPORTS}/{rid}/items/i9/reject").status_code == 404
    # 否掉的新任务提议：助理不许再提（同 unmatch）
    again = client.post(f"{SUG}/matches", json={"matches": [{"id": ids[3], "confidence": 0.5,
                        "newTask": {"projectId": project, "name": "不要的"}}]}).json()
    assert again["matched"] == 0 and "否掉" in again["rejected"][0]["reason"]
    assert client.post(f"{REPORTS}/{rid}/reject").json()["status"] == "rejected"
    st = _status(client, rid)
    assert st["status"] == "rejected" and {i["status"] for i in st["items"]} == {"rejected"}
    assert client.post(f"{REPORTS}/{rid}/approve").status_code == 409
    assert client.post(f"{REPORTS}/{rid}/items/i0/approve").status_code == 409
    assert client.post(f"{REPORTS}/{rid}/reject").status_code == 200
    assert _session_events() == [] and _pending(client)["total"] == 4


def test_all_items_rejected_one_by_one_closes_the_report_as_rejected(client, seeded):
    ids = _ids(client, 1)
    task, _ = _world_ids(seeded)
    rid = _submit(client, [{"kind": "assign", "suggestionIds": ids, "taskId": task}])["reportId"]
    assert client.post(f"{REPORTS}/{rid}/items/i0/reject").json()["reportStatus"] == "rejected"
    assert client.get(REPORTS).json() == {"items": []}  # 缺省只列待批准的
    assert [r["id"] for r in client.get(REPORTS, params={"status": "all"}).json()["items"]] == [rid]


# ─────────────────────────────────────────── 顶掉 / 上限


def test_author_is_only_a_label_nobody_can_supersede_or_hide_another_ais_report(client, seeded):
    """身份只有租户：body 里的 author 不参与任何判定。冒用别人的名字既顶不掉、也看不到更多、改不了什么。"""
    ids = _ids(client, 3)
    task, _ = _world_ids(seeded)
    item = lambda n: [{"kind": "assign", "suggestionIds": ids[n:n + 1], "taskId": task}]  # noqa: E731
    b = _submit(client, item(0), author="opencode")
    a = _submit(client, item(1), author="opencode")           # 同名再交：不顶掉
    liar = _submit(client, item(2), author="hermes")
    assert "superseded" not in b and a["reportId"] != b["reportId"]
    for rid in (b["reportId"], a["reportId"], liar["reportId"]):
        assert _status(client, rid)["status"] == "pending"
    assert len(client.get(REPORTS).json()["items"]) == 3 and _session_events() == []
    # 自报的作者只落进显示字段与出处，不改变谁能做什么
    assert client.post(REPORTS, json={"summary": "x", "items": [], "author": "opencode", "user": "ch_bbbb"}).status_code == 422
    assert _status(client, b["reportId"], B).get("id") is None  # 别的租户读不到（404 体）


def test_pending_cap_per_tenant_counts_every_report_whoever_the_author_claims_to_be(client, seeded):
    ids = _ids(client, 1)
    task, _ = _world_ids(seeded)
    item = [{"kind": "assign", "suggestionIds": ids, "taskId": task}]
    for n in range(5):
        _submit(client, item, author=f"a{n}")
    for author in ("late", "a0"):  # 换名字、冒用旧名字，都过不了上限
        resp = client.post(REPORTS, json={"summary": "x", "items": item, "author": author})
        assert resp.status_code == 429 and "5" in resp.json()["detail"]
    assert len(client.get(REPORTS).json()["items"]) == 5
    # 别的租户有自己的名额（它的 id 看不到 A 的建议 → 一条都收不下，但不是 429）
    assert _submit(client, item, author="late", headers=B)["accepted"] == 0


def test_every_report_route_is_human_only_even_ones_added_later(client, seeded):
    """结构性的底：路由表里 /activity/reports 下的每一条（含以后加的）带 Bearer / 匿名 / report 范围都 403；
    read 范围除 GET 外都 403。不靠上面那张手写的清单。"""
    from app.main import app  # noqa: PLC0415

    routes = [r for r in app.routes if getattr(r, "path", "").startswith(f"{REPORTS}") and getattr(r, "methods", None)]
    assert len(routes) >= 7
    for r in routes:
        url = r.path.replace("{reportId}", "rp_x").replace("{itemId}", "i0")
        for method in r.methods - {"HEAD", "OPTIONS"}:
            for headers in (BEARER, {"X-Nexus-Scope": "report"}, {"X-Nexus-Scope": "report", "X-Nexus-Anonymous": "1"},
                            {"X-Nexus-Scope": "write", "Authorization": "Bearer y"}):
                assert client.request(method, url, headers=headers, json={}).status_code == 403, (method, url, headers)
            if method != "GET":
                assert client.request(method, url, headers={"X-Nexus-Scope": "read"}, json={}).status_code == 403


def test_approve_all_cannot_do_more_than_the_single_paths_would(client, seeded):
    """批准走的就是单条的 confirm / dismiss：单条做不成的（任务已删），批量也做不成，记 failed；
    已被人手点过的，批量不会再来一次。"""
    ids = _ids(client, 3)
    task, project = _world_ids(seeded)
    doomed = _extra_task(project, "会被删的任务")
    rid = _submit(client, [{"kind": "assign", "suggestionIds": ids[:1], "taskId": doomed},
                           {"kind": "dismiss", "suggestionIds": ids[1:2]},
                           {"kind": "assign", "suggestionIds": ids[2:], "taskId": task}])["reportId"]
    _db()["tasks"].delete_one({"id": doomed})
    assert client.post(f"{SUG}/{ids[0]}/confirm", json={"taskId": doomed}).status_code >= 400  # 单条同样做不成
    assert client.post(f"{SUG}/{ids[1]}/confirm", json={"taskId": task}).status_code == 200      # 人先确认了要被忽略的那段
    body = client.post(f"{REPORTS}/{rid}/approve").json()
    assert [i["status"] for i in body["items"]] == ["failed", "stale", "applied"]
    assert len(_session_events()) == 2 and _pending(client)["total"] == 1


# ─────────────────────────────────────────── 并发


def test_concurrent_approve_all_and_single_item_confirm_a_segment_only_once(client, seeded):
    ids = _ids(client, 8)
    task, project = _world_ids(seeded)
    rid = _submit(client, [{"kind": "assign", "suggestionIds": ids[:4], "taskId": task},
                           {"kind": "assign", "suggestionIds": ids[4:], "projectId": project}])["reportId"]

    def go(fn):
        return fn()

    jobs = [lambda: client.post(f"{REPORTS}/{rid}/approve"), lambda: client.post(f"{REPORTS}/{rid}/approve"),
            lambda: client.post(f"{REPORTS}/{rid}/items/i0/approve"), lambda: client.post(f"{REPORTS}/{rid}/items/i1/approve"),
            lambda: client.post(f"{SUG}/{ids[0]}/confirm", json={"taskId": task})]
    with ThreadPoolExecutor(5) as pool:
        resps = list(pool.map(go, jobs))
    assert all(r.status_code in (200, 409) for r in resps), [r.text for r in resps]
    events = _session_events()
    assert len(events) == 8 and len({e["dedupeKey"] for e in events}) == 8  # 每段恰好一条事实
    st = _status(client, rid)
    assert st["status"] == "approved"
    states = [r["state"] for i in st["items"] for r in i["results"].values()]
    assert states.count("applied") + states.count("stale") == 8 and states.count("failed") == 0


# ─────────────────────────────────────────── 出处不可伪造


def test_external_event_ingest_cannot_forge_report_provenance(client):
    w = _world(client)
    ai = {"generated": True, "confidence": 0.9, "confirmed": True, "report": {"id": "rp_forged", "author": "x"}}
    forged = {**_external(w, "evt_rep1", "activity:sug_forged_rep", w["a"]), "source": "activity-confirmed", "ai": ai}
    assert _post(client, EVENTS, [forged])["accepted"] == 1
    stored = _db()["events"].find_one({"id": "evt_rep1"})["ai"]
    assert stored == {"generated": True, "confidence": 0.9, "confirmed": True}



# ─────────────────────────────────────────── 评审补记：与单条路径同样的闸、状态由结果推出、批准中报告被清掉


def test_assign_refused_when_user_rejected_the_task_or_a_rule_already_assigned_one(client, seeded):
    ids = _ids(client, 2)
    task, project = _world_ids(seeded)
    other = _extra_task(project, "另一个任务")
    _db()["activity_suggestions"].update_one({"id": ids[0]}, {"$set": {"rejectedTaskIds": [task]}})   # 人否掉过 task
    _db()["activity_suggestions"].update_one({"id": ids[1]}, {"$set": {"suggestion.taskId": other,
                                                                       "suggestion.classifier": "rules"}})  # 规则给的任务
    out = _submit(client, [{"kind": "assign", "suggestionIds": [ids[0]], "taskId": task},
                           {"kind": "assign", "suggestionIds": [ids[1]], "taskId": task},
                           {"kind": "assign", "suggestionIds": [ids[0]], "projectId": project}])  # 只到项目：不碰任务，照收
    assert out["accepted"] == 1 and [(r["index"], r["code"]) for r in out["rejected"]] == [(0, "task_rejected"), (1, "rule_assigned")]
    # 与单条路径一致：assistant 的 match 同样拒绝这两条
    assert _match(client, [{"id": ids[0], "taskId": task, "confidence": 0.5}])["matched"] == 0


def test_task_rejected_after_submit_is_stale_at_apply_time(client, seeded):
    ids = _ids(client, 1)
    task, _ = _world_ids(seeded)
    rid = _submit(client, [{"kind": "assign", "suggestionIds": ids, "taskId": task}])["reportId"]
    _db()["activity_suggestions"].update_one({"id": ids[0]}, {"$set": {"rejectedTaskIds": [task]}})  # 提交之后人否掉了
    body = client.post(f"{REPORTS}/{rid}/approve").json()
    assert [i["status"] for i in body["items"]] == ["stale"] and body["stale"] == 1
    assert _session_events() == [] and _pending(client)["total"] == 1
    result = next(iter(_status(client, rid)["items"][0]["results"].values()))
    assert result["state"] == "stale" and "否掉" in result["reason"]


def test_item_status_is_derived_from_results_even_when_two_approvals_interleave(client, seeded):
    from app.modules.activity import reports, reports_repo  # noqa: PLC0415

    ids = _ids(client, 1)
    task, _ = _world_ids(seeded)
    rid = _submit(client, [{"kind": "assign", "suggestionIds": ids, "taskId": task}])["reportId"]
    item = reports_repo.get("u_local", rid)["items"][0]
    # 批准 B 看到「已被别处确认」先写 stale 并定下状态；批准 A 随后才把 applied 写进结果
    reports_repo.set_results("u_local", rid, "i0", {ids[0]: {"state": "stale", "reason": "已被别处确认"}})
    assert reports_repo.set_item_status("u_local", rid, "i0", "stale")
    reports_repo.set_results("u_local", rid, "i0", {ids[0]: {"state": "applied"}})
    assert reports._sync_status("u_local", rid, item, {ids[0]: {"state": "applied"}})["status"] == "applied"
    shown = _status(client, rid)["items"][0]
    assert (shown["status"], shown["applied"], shown["stale"]) == ("applied", 1, 0)


def test_report_purged_during_approval_returns_what_was_done_not_404(client, seeded, monkeypatch):
    from app.modules.activity import reports_repo  # noqa: PLC0415

    ids = _ids(client, 2)
    task, project = _world_ids(seeded)
    rid = _submit(client, [{"kind": "assign", "suggestionIds": ids[:1], "taskId": task},
                           {"kind": "assign", "suggestionIds": ids[1:], "projectId": project}])["reportId"]
    real = reports_repo.set_results

    def then_purged(*a, **k):
        real(*a, **k)
        _db()["activity_reports"].delete_many({"id": rid})  # TTL 清理恰好在批准进行中把它删了

    monkeypatch.setattr(reports_repo, "set_results", then_purged)
    resp = client.post(f"{REPORTS}/{rid}/approve")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "purged" and body["applied"] == 2 and [i["status"] for i in body["items"]] == ["applied", "applied"]
    assert len(_session_events()) == 2  # 事实照写，只写一次
