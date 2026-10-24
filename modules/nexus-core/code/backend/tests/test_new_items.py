"""v2.30 AI 提议新项目 / 项目下的新任务：AI 只提议，人批准才建；批准 / 不要只许人。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from app.modules.activity import new_items

API = "/api/core/activity/proposals"
BEARER = {"Authorization": "Bearer " + "x" * 8}


def _db():
    from app.repo import get_db  # noqa: PLC0415

    return get_db()


def _zone(seeded):
    return next(iter(seeded["zones"].values()))["id"]


def _pp(client, zone, name="新项目", reason="r"):
    return client.post(f"{API}/projects", json={"zoneId": zone, "name": name, "reason": reason})


def _pt(client, name="新任务", headers=None, **kw):
    return client.post(f"{API}/tasks", headers=headers, json={"name": name, "reason": "r", **kw})


def _counts():
    return _db()["projects"].count_documents({}), _db()["tasks"].count_documents({})


def test_propose_dedupes_and_writes_nothing(client, seeded):
    z = _zone(seeded)
    before = _counts()
    a, b = _pp(client, z, " 新  项目 "), _pp(client, z, "新 项目")
    assert a.status_code == b.status_code == 200 and a.json()["id"] == b.json()["id"] and a.json()["id"].startswith("np_")
    assert a.json()["status"] == "pending" and a.json()["createdId"] is None
    assert _counts() == before and _db()["activity_new_items"].count_documents({}) == 1
    t1, t2 = _pt(client, projectProposalId=a.json()["id"]), _pt(client, " 新任务", projectProposalId=a.json()["id"])
    assert t1.json()["id"] == t2.json()["id"] and t1.json()["projectId"] is None
    assert _counts() == before


def test_existing_names_and_bad_input_rejected(client, seeded):
    z = _zone(seeded)
    proj = _db()["projects"].find_one({"zoneId": z})
    r = _pp(client, z, proj["name"].upper())
    assert r.status_code == 409 and proj["id"] in r.json()["detail"]
    task = _db()["tasks"].find_one({})
    r = _pt(client, task["name"], projectId=task["projectId"])
    assert r.status_code == 409 and task["id"] in r.json()["detail"]
    assert _pp(client, "z_nope").status_code == 400
    assert _pp(client, z, "   ").status_code == 400 and _pp(client, z, "字" * 65).status_code == 400
    assert _pt(client, projectId="p_nope").status_code == 400
    assert _pt(client).status_code == 400 and _pt(client, projectId=task["projectId"], projectProposalId="np_x").status_code == 400
    assert _pt(client, projectProposalId="np_nope").status_code == 400


def test_rejected_name_is_remembered(client, seeded):
    z = _zone(seeded)
    pid = _pp(client, z).json()["id"]
    assert client.post(f"{API}/{pid}/reject").json()["status"] == "rejected"
    assert _pp(client, z, "新项目 ").status_code == 409
    assert client.post(f"{API}/{pid}/approve").status_code == 409


def test_caps(client, seeded, monkeypatch):
    z = _zone(seeded)
    monkeypatch.setitem(new_items.MAX_PENDING, "project", 2)
    monkeypatch.setitem(new_items.MAX_PENDING, "task", 2)
    first = [_pp(client, z, f"项目{i}") for i in range(3)]
    assert [r.status_code for r in first] == [200, 200, 429]
    assert _pp(client, z, "项目0").status_code == 200  # 已有的同名提议不占新名额
    pid = first[0].json()["id"]
    assert [_pt(client, f"任务{i}", projectProposalId=pid).status_code for i in range(3)] == [200, 200, 429]


def test_human_only_approve_reject_and_device_token_propose(client, seeded):
    z = _zone(seeded)
    pid = _pp(client, z).json()["id"]
    for h in (BEARER, {"X-Nexus-Scope": "write"}, {"X-Nexus-Scope": "write", "Authorization": "Bearer y"},
              {"X-Nexus-Anonymous": "1"}):
        for verb in ("approve", "reject"):
            assert client.post(f"{API}/{pid}/{verb}", headers=h).status_code == 403
    assert client.post(f"{API}/projects", headers=BEARER, json={"zoneId": z, "name": "x"}).status_code == 403
    assert client.get(API, headers={"X-Nexus-Scope": "read"}).status_code == 200
    assert _counts()[0] == _db()["projects"].count_documents({}) and _db()["activity_new_items"].find_one({"id": pid})["status"] == "pending"


def test_project_approve_creates_once_even_concurrently(client, seeded):
    z = _zone(seeded)
    pid = _pp(client, z).json()["id"]
    n = _db()["projects"].count_documents({})
    with ThreadPoolExecutor(6) as ex:
        rs = list(ex.map(lambda _: client.post(f"{API}/{pid}/approve"), range(6)))
    assert {r.status_code for r in rs} == {200}
    ids = {r.json()["createdId"] for r in rs}
    assert len(ids) == 1 and _db()["projects"].count_documents({}) == n + 1
    made = _db()["projects"].find_one({"id": ids.pop()})
    assert made["name"] == "新项目" and made["zoneId"] == z
    assert _db()["planner_audit"].count_documents({"objectType": "projects", "op": "create", "outcome": "applied"}) == 1
    assert client.post(f"{API}/{pid}/reject").status_code == 409
    # 已批准且项目健在：同名再提 = 已有同名项目
    assert _pp(client, z).status_code == 409


def test_task_under_pending_project(client, seeded):
    z = _zone(seeded)
    pid = _pp(client, z).json()["id"]
    tid = _pt(client, projectProposalId=pid).json()["id"]
    r = client.post(f"{API}/{tid}/approve")
    assert r.status_code == 409 and "项目" in r.json()["detail"]
    assert _db()["tasks"].count_documents({"name": "新任务"}) == 0
    listing = client.get(API).json()
    assert [p["id"] for p in listing["projects"]] == [pid] and [t["id"] for t in listing["projects"][0]["tasks"]] == [tid]
    assert listing["tasks"] == []
    real = client.post(f"{API}/{pid}/approve").json()["createdId"]
    r = _pt(client, "另一个", projectProposalId=pid)  # 已批准的项目提议：拒，指路真 id
    assert r.status_code == 409 and real in r.json()["detail"]
    done = client.post(f"{API}/{tid}/approve")
    assert done.status_code == 200 and done.json()["projectId"] == real
    task = _db()["tasks"].find_one({"id": done.json()["createdId"]})
    assert task["name"] == "新任务" and task["projectId"] == real
    assert client.post(f"{API}/{tid}/approve").json()["createdId"] == task["id"]
    assert _db()["tasks"].count_documents({"name": "新任务"}) == 1
    # AI 看得到结果
    seen = client.get(API, params={"status": "all"}).json()["projects"][0]
    assert seen["status"] == "accepted" and seen["createdId"] == real and seen["tasks"][0]["status"] == "accepted"


def test_task_for_existing_project_and_reject_cascade(client, seeded):
    z = _zone(seeded)
    proj = _db()["projects"].find_one({"zoneId": z})
    tid = _pt(client, "挂在现成项目", projectId=proj["id"]).json()["id"]
    assert client.get(API).json()["tasks"][0]["projectName"] == proj["name"]
    r = client.post(f"{API}/{tid}/approve")
    assert r.status_code == 200 and _db()["tasks"].find_one({"id": r.json()["createdId"]})["projectId"] == proj["id"]
    pid = _pp(client, z).json()["id"]
    t1, t2 = (_pt(client, n, projectProposalId=pid).json()["id"] for n in ("甲", "乙"))
    assert client.post(f"{API}/{pid}/reject").json()["status"] == "rejected"
    st = {i["id"]: i["status"] for i in _db()["activity_new_items"].find({})}
    assert st[t1] == st[t2] == "rejected"
    assert _pt(client, "甲", projectProposalId=pid).status_code == 409
    assert client.get(API).json() == {"projects": [], "tasks": [], "changes": [], "ignores": [], "windowRules": [], "blockRules": [], "blockActions": [], "sessionChanges": []}


def test_approve_after_target_deleted_stays_pending_and_can_be_rejected(client, seeded):
    z = _zone(seeded)
    proj = _db()["projects"].find_one({"zoneId": z})
    pid = _pp(client, z).json()["id"]
    tid = _pt(client, "挂现成", projectId=proj["id"]).json()["id"]
    _db()["projects"].delete_one({"id": proj["id"]})
    r = client.post(f"{API}/{tid}/approve")
    assert r.status_code == 409 and "不存在" in r.json()["detail"]
    _db()["zones"].delete_one({"id": z})
    assert client.post(f"{API}/{pid}/approve").status_code == 409
    st = {i["id"]: i["status"] for i in _db()["activity_new_items"].find({})}
    assert st[pid] == st[tid] == "pending"
    assert client.post(f"{API}/{pid}/reject").json()["status"] == "rejected"


def test_task_under_approved_project_that_vanished_has_distinct_409(client, seeded):
    z = _zone(seeded)
    pid = _pp(client, z).json()["id"]
    tid = _pt(client, projectProposalId=pid).json()["id"]
    real = client.post(f"{API}/{pid}/approve").json()["createdId"]
    _db()["projects"].delete_one({"id": real})
    r = client.post(f"{API}/{tid}/approve")
    assert r.status_code == 409 and "已不存在" in r.json()["detail"] and "还没批准" not in r.json()["detail"]


def test_create_failure_releases_claim_and_same_name_recheck(client, seeded):
    z = _zone(seeded)
    pid = _pp(client, z).json()["id"]
    _db()["projects"].insert_one({**_db()["projects"].find_one({"zoneId": z}, {"_id": 0}), "id": "p_hand", "name": "新项目"})
    r = client.post(f"{API}/{pid}/approve")  # 提议后有人手建了同名：不重复建
    assert r.status_code == 409 and "p_hand" in r.json()["detail"]
    d = _db()["activity_new_items"].find_one({"id": pid})
    assert d["status"] == "pending" and d["createClaimedAt"] is None
    assert _db()["projects"].count_documents({"name": "新项目"}) == 1
    assert client.post(f"{API}/{pid}/reject").json()["status"] == "rejected"


def test_repropose_does_not_delete_accepted_while_create_in_flight(client, seeded):
    z = _zone(seeded)
    pid = _pp(client, z).json()["id"]
    from datetime import datetime, timezone  # noqa: PLC0415
    _db()["activity_new_items"].update_one({"id": pid}, {"$set": {"status": "accepted",
                                           "createClaimedAt": datetime.now(timezone.utc)}})
    assert _pp(client, z).status_code == 409
    assert _db()["activity_new_items"].find_one({"id": pid})["status"] == "accepted"


def test_reject_project_does_not_cascade_when_it_lost_to_approve(client, seeded, monkeypatch):
    z = _zone(seeded)
    pid = _pp(client, z).json()["id"]
    tid = _pt(client, projectProposalId=pid).json()["id"]
    real_get = new_items._get
    def racing(user, item_id):  # reject 读到 pending，之后批准抢先
        d = real_get(user, item_id)
        if item_id == pid and d["status"] == "pending":
            _db()["activity_new_items"].update_one({"id": pid}, {"$set": {"status": "accepted"}})
        return d
    monkeypatch.setattr(new_items, "_get", racing)
    client.post(f"{API}/{pid}/reject")
    monkeypatch.setattr(new_items, "_get", real_get)
    assert _db()["activity_new_items"].find_one({"id": tid})["status"] == "pending"


def test_other_tenant_cannot_see_or_approve(client, seeded):
    z = _zone(seeded)
    pid = _pp(client, z).json()["id"]
    b = {"X-Nexus-Tenant": "u_other"}
    assert client.get(API, headers=b).json() == {"projects": [], "tasks": [], "changes": [], "ignores": [], "windowRules": [], "blockRules": [], "blockActions": [], "sessionChanges": []}
    assert client.post(f"{API}/{pid}/approve", headers=b).status_code == 404
    assert client.post(f"{API}/{pid}/reject", headers=b).status_code == 404
    assert _pt(client, projectProposalId=pid, headers=b).status_code == 400
