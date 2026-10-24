"""改挂未分类时间：``POST /api/core/sessions/{eventId}/reassign``（契约 v2.11「改挂未分类时间」节）。

要害一句：**只追加，不改写。** 原来那条 ``session.completed`` 一个字节不动；每次改挂往台账追加一条
``session.reassigned``，链连续、最后一条为准。本文件钉端点的规则与档案读端；各视图的合计、重建、恢复、并发
在 ``test_session_reassign_views.py``。
"""

from __future__ import annotations

import pytest

from test_activity_suggestions import BEARER, _recent
from test_restore import AI_HEAD, ai_token  # noqa: F401 —— ai_token 是 fixture

API = "/api/core"
PLANNER = f"{API}/planner"
EVENTS = f"{API}/events"
B = {"X-Nexus-Tenant": "ch_bbbb"}


def _db():
    from app.repo import get_db  # noqa: PLC0415

    return get_db()


def _post(client, path: str, body: dict | None = None, headers=None) -> dict:
    resp = client.post(path, json=body, headers=headers or {})
    assert resp.status_code in (200, 201), resp.text
    return resp.json()


def _world(client, headers=None) -> dict:
    """一个分区、两个项目：P 下任务 a / b 和它的桶，Q 下任务 c。返回各 id。"""
    zone = _post(client, f"{PLANNER}/zones", {"name": "改挂分区"}, headers)["id"]
    p = _post(client, f"{PLANNER}/projects", {"zoneId": zone, "name": "改挂项目 P"}, headers)["id"]
    q = _post(client, f"{PLANNER}/projects", {"zoneId": zone, "name": "改挂项目 Q"}, headers)["id"]
    ids = {"zone": zone, "p": p, "q": q,
           "bucket": _post(client, f"{PLANNER}/projects/{p}/unclassified", None, headers)["taskId"]}
    for key, project in (("a", p), ("b", p), ("c", q)):
        ids[key] = _post(client, f"{PLANNER}/tasks", {"projectId": project, "name": f"任务 {key}"}, headers)["id"]
    return ids


def _session(client, task_id: str, minutes_ago: int = 60, seconds: int = 600, headers=None) -> str:
    """补登一段，返回那条 session.completed 的事件 id。``minutes_ago`` 不同 = 不同的段。"""
    return _post(client, f"{API}/timer/backfill", {
        "taskId": task_id, "startAt": _recent(minutes_ago).isoformat(), "durationSeconds": seconds},
        headers)["event"]["id"]


def _reassign(client, event_id: str, expect: int = 200, headers=None, **body) -> dict:
    resp = client.post(f"{API}/sessions/{event_id}/reassign", json=body, headers=headers or {})
    assert resp.status_code == expect, resp.text
    return resp.json()


def _chain(event_id: str) -> list[dict]:
    docs = _db()["events"].find({"type": "session.reassigned", "data.sessionEventId": event_id}, {"_id": 0})
    return sorted(docs, key=lambda d: d["data"]["seq"])


def _read(client, **params) -> dict:
    resp = client.get(EVENTS, params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


# ------------------------------------------------------------------ 只追加，不改写


def test_reassign_appends_a_fact_and_never_touches_the_session(client):
    w = _world(client)
    sid = _session(client, w["bucket"])
    original = _db()["events"].find_one({"id": sid})  # 含 _id：连同主键逐字段比
    count = _db()["events"].count_documents({})

    out = _reassign(client, sid, taskId=w["a"])
    assert out == {"sessionEventId": sid, "duplicate": False, "fromTaskId": w["bucket"], "taskId": w["a"],
                   "projectId": w["p"], "seq": 1, "event": out["event"]}
    assert out["event"]["type"] == "session.reassigned" and out["event"]["dedupeKey"] == f"reassign:{sid}:1"

    assert _db()["events"].find_one({"id": sid}) == original, "原来那条事实一个字节都不能变"
    assert _db()["events"].count_documents({}) == count + 1
    (fact,) = _chain(sid)
    assert fact["id"] == out["event"]["id"] and fact["source"] == "session-reassign" and fact["user"] == "u_local"
    assert fact["subject"] == {"zone": w["zone"], "project": w["p"], "task": w["a"]}
    assert fact["data"] == {
        "sessionEventId": sid, "seq": 1, "fromTaskId": w["bucket"], "fromProjectId": w["p"],
        "toTaskId": w["a"], "toProjectId": w["p"], "actor": "human",
        "session": {"source": original["source"], "dedupeKey": original["dedupeKey"],
                    "startAt": original["data"]["startAt"], "durationSeconds": 600},
    }
    assert fact["recordedAt"] and fact["time"]


def test_chain_a_b_c_and_back_to_the_bucket_keeps_full_history(client):
    w = _world(client)
    sid = _session(client, w["bucket"])
    steps = [({"taskId": w["a"]}, w["a"], w["p"]), ({"taskId": w["b"]}, w["b"], w["p"]),
             ({"taskId": w["c"]}, w["c"], w["q"]),              # 跨项目
             ({"projectId": w["p"]}, w["bucket"], w["p"]),      # 放回原项目的未分类
             ({"projectId": w["q"]}, f"t_unc_{w['q']}", w["q"])]  # 另一个项目的桶：懒建
    previous = w["bucket"]
    for seq, (body, task, project) in enumerate(steps, start=1):
        out = _reassign(client, sid, **body)
        assert (out["seq"], out["fromTaskId"], out["taskId"], out["projectId"], out["duplicate"]) == (
            seq, previous, task, project, False)
        previous = task

    chain = _chain(sid)
    assert [f["data"]["seq"] for f in chain] == [1, 2, 3, 4, 5]
    assert [f["dedupeKey"] for f in chain] == [f"reassign:{sid}:{n}" for n in range(1, 6)]
    for earlier, later in zip(chain, chain[1:]):  # 链连续：后一条的 from = 前一条的 to
        assert (later["data"]["fromTaskId"], later["data"]["fromProjectId"]) == (
            earlier["data"]["toTaskId"], earlier["data"]["toProjectId"])
    assert _db()["tasks"].count_documents({"kind": "unclassified"}) == 2  # P 的 + Q 懒建的，各一个
    # 历史从档案读端读得到，新的在前
    history = _read(client, type="session.reassigned")
    assert history["total"] == 5 and [e["data"]["seq"] for e in history["items"]] == [5, 4, 3, 2, 1]


def test_same_target_is_an_idempotent_duplicate(client):
    w = _world(client)
    sid = _session(client, w["bucket"])
    # 从没改挂过、目标就是它所在的桶：duplicate，seq 0，没有 event
    assert _reassign(client, sid, projectId=w["p"]) == {
        "sessionEventId": sid, "duplicate": True, "fromTaskId": w["bucket"], "taskId": w["bucket"],
        "projectId": w["p"], "seq": 0, "event": None}
    first = _reassign(client, sid, taskId=w["a"])
    again = _reassign(client, sid, taskId=w["a"])
    assert again == {**first, "duplicate": True, "fromTaskId": w["a"]}  # event 回显的是原来那条
    assert len(_chain(sid)) == 1


# ------------------------------------------------------------------ 拒绝：什么都不追加


@pytest.mark.parametrize("case, expect, needle", [
    ("unknown-event", 404, "evt_nope"),
    ("not-a-bucket-session", 409, "未分类"),
    ("empty-body", 400, "taskId"),
    ("both", 400, "taskId"),
    ("extra-key", 422, ""),
    ("unknown-task", 404, "t_nope"),
    ("target-is-a-bucket", 400, "projectId"),
    ("unknown-project", 404, "p_nope"),
])
def test_invalid_requests_append_nothing(client, case, expect, needle):
    w = _world(client)
    on_bucket, on_task = _session(client, w["bucket"]), _session(client, w["a"], 90)
    event_id, body = {
        "unknown-event": ("evt_nope", {"taskId": w["a"]}),
        "not-a-bucket-session": (on_task, {"taskId": w["b"]}),  # 直接记在任务上的时间不在本版范围
        "empty-body": (on_bucket, {}),
        "both": (on_bucket, {"taskId": w["a"], "projectId": w["p"]}),
        "extra-key": (on_bucket, {"taskId": w["a"], "note": "x"}),
        "unknown-task": (on_bucket, {"taskId": "t_nope"}),
        "target-is-a-bucket": (on_bucket, {"taskId": w["bucket"]}),
        "unknown-project": (on_bucket, {"projectId": "p_nope"}),
    }[case]
    out = _reassign(client, event_id, expect, **body)
    assert needle in str(out["detail"])
    assert _db()["events"].count_documents({"type": "session.reassigned"}) == 0
    assert _read(client, type="session.completed", taskId=w["bucket"])["total"] == 1


def test_only_session_completed_can_be_reassigned(client):
    w = _world(client)
    run = _post(client, f"{API}/agents/start", {"taskId": w["bucket"], "agent": "codex", "tool": "cli"})
    stopped = _post(client, f"{API}/agents/{run['runId']}/stop", {"outcome": "done"})
    assert "session.completed" in _reassign(client, stopped["event"]["id"], 404, taskId=w["a"])["detail"]


def test_ambiguous_event_id_is_409(client):
    # 事件 id 不唯一（同 id 不同 dedupeKey 两条都落）：不猜是哪一条
    w = _world(client)
    envelope = {"spec": "yq-event/v1", "id": "evt_twin", "type": "session.completed", "user": "u_local",
                "source": "ext", "time": _recent(5).isoformat(),
                "subject": {"zone": w["zone"], "project": w["p"], "task": w["bucket"]},
                "data": {"durationSeconds": 60, "startAt": _recent(6).isoformat()}}
    assert _post(client, EVENTS, [{**envelope, "dedupeKey": "k1"}, {**envelope, "dedupeKey": "k2"}])["accepted"] == 2
    assert "不唯一" in _reassign(client, "evt_twin", 409, taskId=w["a"])["detail"]


def test_device_token_and_ai_source_are_403(client, ai_token):  # noqa: F811
    w = _world(client)
    sid = _session(client, w["bucket"])
    assert "设备令牌" in _reassign(client, sid, 403, headers=BEARER, taskId=w["a"])["detail"]
    assert "ai" in _reassign(client, sid, 403, headers=AI_HEAD, taskId=w["a"])["detail"]
    assert _reassign(client, sid, 403, headers=AI_HEAD, projectId=w["q"])["detail"]
    assert _chain(sid) == [] and _db()["tasks"].count_documents({"id": f"t_unc_{w['q']}"}) == 0  # 桶也没被建


def test_public_event_entry_refuses_the_fact(client):
    # 事件入口对设备令牌 / 外部 source 开放：放行就绕过了上面全部规则
    w = _world(client)
    sid = _session(client, w["bucket"])
    forged = {"spec": "yq-event/v1", "id": "evt_forged", "dedupeKey": f"reassign:{sid}:1",
              "type": "session.reassigned", "user": "u_local", "source": "session-reassign",
              "time": _recent(1).isoformat(), "subject": {"zone": w["zone"], "project": w["p"], "task": w["a"]},
              "data": {"sessionEventId": sid, "seq": 1, "fromTaskId": w["bucket"], "fromProjectId": w["p"],
                       "session": {"source": "manual-backfill", "dedupeKey": "x", "durationSeconds": 600,
                                   "startAt": _recent(60).isoformat()}}}
    out = _post(client, EVENTS, forged)
    assert (out["accepted"], out["duplicate"]) == (0, 0) and "reassign" in out["rejected"][0]["reason"]
    assert _chain(sid) == [] and "currentSubject" not in _read(client, type="session.completed")["items"][0]
    assert _reassign(client, sid, taskId=w["a"])["seq"] == 1  # 真的改挂不受那次伪造影响


def test_another_tenant_cannot_see_or_move_it(client):
    w = _world(client)
    sid = _session(client, w["bucket"])
    theirs = _world(client, B)
    _reassign(client, sid, 404, headers=B, taskId=theirs["a"])
    _reassign(client, sid, 404, taskId=theirs["a"])  # 自己的段也不能挂到别人的任务上（查无此任务）
    assert _chain(sid) == []


# ------------------------------------------------------------------ 档案读端：按当前归属


def test_events_read_follows_the_current_assignment(client):
    w = _world(client)
    moved, kept = _session(client, w["bucket"], 60), _session(client, w["bucket"], 90)
    native = _session(client, w["a"], 120)
    assert _read(client, type="session.completed", taskId=w["bucket"])["total"] == 2

    _reassign(client, moved, taskId=w["a"])
    waiting = _read(client, type="session.completed", taskId=w["bucket"])  # 「待分类」少了一段
    assert waiting["total"] == 1 and [e["id"] for e in waiting["items"]] == [kept]
    assert "currentSubject" not in waiting["items"][0]  # 没改挂过的条目没有这个键
    on_a = {e["id"]: e for e in _read(client, type="session.completed", taskId=w["a"])["items"]}
    assert set(on_a) == {moved, native} and "currentSubject" not in on_a[native]
    assert on_a[moved]["subject"]["task"] == w["bucket"], "subject 原样，永不变"
    assert on_a[moved]["currentSubject"] == {"zone": w["zone"], "project": w["p"], "task": w["a"]}
    # 不带 type 时同一口径；改挂事实自己按 subject.task（去向）过滤
    assert {e["type"] for e in _read(client, taskId=w["a"])["items"]} == {"session.completed", "session.reassigned"}
    assert _read(client, type="session.reassigned", taskId=w["a"])["total"] == 1
    # 导出的 events 是台账原样：没有读时现算的键
    assert all("currentSubject" not in e for e in client.get(f"{API}/export").json()["events"])

    _reassign(client, moved, projectId=w["p"])  # 放回：又回到「待分类」，带着 currentSubject = 桶
    back = {e["id"]: e for e in _read(client, type="session.completed", taskId=w["bucket"])["items"]}
    assert set(back) == {moved, kept} and back[moved]["currentSubject"]["task"] == w["bucket"]
