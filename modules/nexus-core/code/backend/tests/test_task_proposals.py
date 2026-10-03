"""AI 提议新任务（契约 v2.8「活动建议」节「AI 提议新任务」）。

要害一句：**AI 只提议，人点「是」才建，而且同一提议只建一个任务。** 所以最重的断言是：
提议之后 planner 一个字节不动；并发确认同一提议的几段，任务表里只多一条；人说「否」之后助理不能再提。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from test_activity_suggestions import BEARER, MATCHES, SUG, _pending, _recent, _seg, _session_events, _upload

API = "/api/core"


def _db():
    from app.repo import get_db  # noqa: PLC0415

    return get_db()


def _setup(client, seeded, n=2):
    """n 段待确认（同一窗口），返回 (段 id 列表（新的在前）, 项目 id)。"""
    _upload(client, [_seg(_recent(10 * (i + 1))) for i in range(n)])
    ids = [i["id"] for i in _pending(client)["items"]]
    return ids, seeded["tasks"]["示例任务三"]["projectId"]


def _propose(client, ids, project_id, name="重构存档", confidence=0.6):
    resp = client.post(MATCHES, json={"matches": [
        {"id": i, "newTask": {"projectId": project_id, "name": name}, "confidence": confidence, "reason": "r"}
        for i in ids]})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _tasks_named(name):
    return list(_db()["tasks"].find({"name": name}, {"_id": 0}))


def test_propose_dedupes_and_writes_nothing_to_planner(client, seeded):
    ids, pid = _setup(client, seeded)
    tasks_before = _db()["tasks"].count_documents({})
    out = client.post(MATCHES, json={"matches": [
        {"id": ids[0], "newTask": {"projectId": pid, "name": " 重构 存档 "}, "confidence": 0.6, "reason": "r"},
        {"id": ids[1], "newTask": {"projectId": pid, "name": "重构  存档"}, "confidence": 1},
    ]}).json()
    assert out == {"matched": 2, "rejected": []}
    items = _pending(client)["items"]
    a, b = (i["suggestion"]["newTask"] for i in items)
    # 空白归一后同名 = 同一条提议；名字是第一次提出时的写法（去首尾空白）
    assert a == b and a["projectId"] == pid and a["name"] == "重构 存档" and a["proposalId"].startswith("tp_")
    assert items[0]["suggestion"]["taskId"] is None and items[0]["suggestion"]["classifier"] == "assistant"
    assert _db()["activity_task_proposals"].count_documents({}) == 1
    # 提议之后 planner、台账一个字节不动
    assert _db()["tasks"].count_documents({}) == tasks_before and _session_events() == []
    # 重交幂等；没有提议的建议没有 newTask 键
    assert _propose(client, ids, pid, name="重构 存档")["matched"] == 2
    _upload(client, [_seg(_recent(90))])
    assert "newTask" not in next(i for i in _pending(client)["items"] if i["id"] not in ids)["suggestion"]


def test_propose_rejects_per_index(client, seeded):
    ids, pid = _setup(client, seeded)
    existing = seeded["tasks"]["示例任务三"]
    ruled = seeded["tasks"]["示例任务四"]
    _upload(client, [_seg(_recent(80), task_id=ruled["id"])])
    ruled_sug = next(i["id"] for i in _pending(client)["items"] if i["suggestion"]["taskId"])
    nt = {"projectId": pid, "name": "新任务"}
    out = client.post(MATCHES, json={"matches": [
        {"id": ids[0], "newTask": nt, "confidence": 0.5},                                  # 0 收
        {"id": ids[0], "taskId": existing["id"], "newTask": nt, "confidence": 0.5},        # 1 二者都给
        {"id": ids[0], "newTask": {"projectId": "p_nope", "name": "x"}, "confidence": 0.5},  # 2 项目不存在
        {"id": ids[0], "newTask": {"projectId": pid, "name": "   "}, "confidence": 0.5},   # 3 空名
        {"id": ids[0], "newTask": {"projectId": pid, "name": "长" * 65}, "confidence": 0.5},  # 4 超 64 字
        {"id": ids[0], "newTask": {"projectId": pid, "name": existing["name"].upper() + " "},
         "confidence": 0.5},                                                               # 5 已有同名任务
        {"id": ruled_sug, "newTask": nt, "confidence": 0.5},                               # 6 规则配上的不盖
        {"id": ids[1], "newTask": {"projectId": pid}, "confidence": 0.5},                  # 7 缺 name
    ]}).json()
    assert out["matched"] == 1 and [r["index"] for r in out["rejected"]] == list(range(1, 8))
    assert existing["id"] in out["rejected"][4]["reason"]   # 有现成的：理由点名该用哪个 taskId


def test_confirm_creates_once_and_other_segments_reuse(client, seeded):
    ids, pid = _setup(client, seeded, n=3)
    _propose(client, ids, pid)
    audit_before = _db()["planner_audit"].count_documents({})
    r1 = client.post(f"{SUG}/{ids[0]}/confirm", json={"name": "  重构存档系统 "})   # 人改了名
    assert r1.status_code == 200, r1.text
    (task,) = _tasks_named("重构存档系统")
    assert task["projectId"] == pid and task["lastWriter"] == "human" and r1.json()["taskId"] == task["id"]
    # 走的是 planner 写入口：留了一条建任务的审计
    (entry,) = list(_db()["planner_audit"].find({}, {"_id": 0}).skip(audit_before))
    assert entry["op"] == "create" and entry["objectId"] == task["id"] and entry["outcome"] == "applied"
    # 其余段（不带名字 / 带别的名字）复用同一个任务，不建第二个
    assert client.post(f"{SUG}/{ids[1]}/confirm", json={}).json()["taskId"] == task["id"]
    assert client.post(f"{SUG}/{ids[2]}/confirm", json={"name": "别的名字"}).json()["taskId"] == task["id"]
    assert _tasks_named("别的名字") == [] and _tasks_named("重构存档") == []
    events = _session_events()
    assert len(events) == 3 and {e["subject"]["task"] for e in events} == {task["id"]}
    assert all(e["ai"] == {"generated": True, "confidence": 0.6, "confirmed": True} for e in events)
    # 重复确认回原来的任务
    again = client.post(f"{SUG}/{ids[0]}/confirm", json={}).json()
    assert again["duplicate"] is True and again["taskId"] == task["id"]
    # 已建成：再提同一名字被拒，理由给 taskId
    _upload(client, [_seg(_recent(120))])
    new = _pending(client)["items"][0]["id"]
    out = _propose(client, [new], pid)
    assert out["matched"] == 0 and task["id"] in out["rejected"][0]["reason"]


def test_concurrent_confirms_create_exactly_one_task(client, seeded, monkeypatch):
    from app.modules.activity import proposals  # noqa: PLC0415

    ids, pid = _setup(client, seeded, n=6)
    _propose(client, ids, pid)
    real = proposals.planner_service.create_task

    def slow(*args, **kwargs):
        import time  # noqa: PLC0415

        time.sleep(0.4)   # 建的那一位慢一点：其余确认一定撞上它
        return real(*args, **kwargs)

    monkeypatch.setattr(proposals.planner_service, "create_task", slow)
    with ThreadPoolExecutor(len(ids)) as pool:
        resps = list(pool.map(lambda i: client.post(f"{SUG}/{i}/confirm", json={}), ids))
    assert [r.status_code for r in resps] == [200] * len(ids), [r.text for r in resps]
    (task,) = _tasks_named("重构存档")
    assert {r.json()["taskId"] for r in resps} == {task["id"]}
    assert len(_session_events()) == len(ids)


def test_confirm_reuses_a_same_named_task_made_meanwhile(client, seeded):
    ids, pid = _setup(client, seeded, n=1)
    _propose(client, ids, pid, name="Save Refactor")
    made = client.post(f"{API}/planner/tasks", json={"projectId": pid, "name": "save  refactor"}).json()  # 人刚手建了
    assert client.post(f"{SUG}/{ids[0]}/confirm", json={}).json()["taskId"] == made["id"]
    assert _tasks_named("Save Refactor") == []


def test_confirm_bad_requests_leave_everything_pending(client, seeded):
    ids, pid = _setup(client, seeded)
    _propose(client, ids, pid)
    task = seeded["tasks"]["示例任务三"]
    assert client.post(f"{SUG}/{ids[0]}/confirm", json={"name": " "}).status_code == 400
    assert client.post(f"{SUG}/{ids[0]}/confirm", json={"name": "x" * 65}).status_code == 400
    assert client.post(f"{SUG}/{ids[0]}/confirm", json={"name": "x", "taskId": task["id"]}).status_code == 400
    # 设备令牌不能建任务：403，任何写入之前
    assert client.post(f"{SUG}/{ids[0]}/confirm", json={}, headers=BEARER).status_code == 403
    assert _pending(client)["total"] == 2 and _tasks_named("重构存档") == [] and _session_events() == []
    # 项目在提议之后被删了：建不成 → 400，放回待确认
    _db()["projects"].delete_one({"id": pid})
    assert client.post(f"{SUG}/{ids[0]}/confirm", json={}).status_code == 400
    assert _pending(client)["total"] == 2 and _session_events() == []
    # 人在下拉里挑了现成任务 = 普通确认，提议不建
    _db()["projects"].insert_one({**seeded["projects"][next(
        n for n, p in seeded["projects"].items() if p["id"] == pid)]})
    assert client.post(f"{SUG}/{ids[1]}/confirm", json={"taskId": task["id"]}).json()["taskId"] == task["id"]
    assert _tasks_named("重构存档") == []


def test_unmatch_rejects_proposal_and_ai_cannot_repropose(client, seeded):
    ids, pid = _setup(client, seeded, n=3)
    _propose(client, ids, pid)
    prop = _pending(client)["items"][0]["suggestion"]["newTask"]["proposalId"]
    assert client.post(f"{SUG}/{ids[0]}/unmatch", json={"proposalId": "tp_other"}).status_code == 409
    assert client.post(f"{SUG}/{ids[0]}/unmatch", json={"taskId": "t_x"}).status_code == 409
    assert client.post(f"{SUG}/{ids[0]}/unmatch", headers=BEARER).status_code == 403
    resp = client.post(f"{SUG}/{ids[0]}/unmatch", json={"proposalId": prop})
    assert resp.status_code == 200 and resp.json()["status"] == "pending"
    item = next(i for i in _pending(client)["items"] if i["id"] == ids[0])
    assert "newTask" not in item["suggestion"] and item["suggestion"]["confidence"] == 0
    # 还有段指着它：提议仍待定；这一段不许再提同一个
    assert _db()["activity_task_proposals"].find_one({"id": prop})["status"] == "pending"
    out = _propose(client, [ids[0]], pid, name="重构存档")
    assert out["matched"] == 0 and "否掉" in out["rejected"][0]["reason"]
    assert client.post(f"{SUG}/{ids[0]}/unmatch", json={"proposalId": prop}).status_code == 200  # 幂等
    # 最后一段也否掉 → 提议标已否掉，谁都不许再提
    client.post(f"{SUG}/{ids[1]}/unmatch", json={"proposalId": prop})
    assert _db()["activity_task_proposals"].find_one({"id": prop})["status"] == "pending"   # ids[2] 还指着
    client.post(f"{SUG}/{ids[2]}/unmatch", json={"proposalId": prop})
    assert _db()["activity_task_proposals"].find_one({"id": prop})["status"] == "rejected"
    _upload(client, [_seg(_recent(120))])
    new = _pending(client)["items"][0]["id"]
    assert _propose(client, [new], pid)["matched"] == 0
    # 换个名字可以；也可以改配现成任务
    assert _propose(client, [new], pid, name="存档迁移")["matched"] == 1
    assert _tasks_named("重构存档") == [] and _session_events() == []
