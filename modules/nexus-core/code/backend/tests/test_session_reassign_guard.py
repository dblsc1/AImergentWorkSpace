"""改挂的设防面（契约 v2.11「改挂未分类时间」节「谁能调」+ 审核补的几条）。

每条对着一种绕法：凭据判定的先后、请求体 / 台账载荷的类型、幂等键被客户端撞、同 id 的另一段蹭改挂。
"""

from __future__ import annotations

import dataclasses

import pytest

from test_activity_suggestions import BEARER, _recent
from test_restore import AI_HEAD, ai_token  # noqa: F401 —— fixture
from test_session_reassign import API, EVENTS, _chain, _db, _post, _read, _reassign, _session, _world
from test_session_reassign_views import _rebuild, _task_seconds, _views

HUMAN_TOKEN = "test-only-human-1234567890abcdef"


def _external(w: dict, event_id: str, dedupe: str, task: str, minutes_ago: int = 30, seconds: int = 60) -> dict:
    return {"spec": "yq-event/v1", "id": event_id, "dedupeKey": dedupe, "type": "session.completed",
            "user": "u_local", "source": "ext", "time": _recent(minutes_ago - 1).isoformat(),
            "subject": {"zone": w["zone"], "project": w["p"], "task": task},
            "data": {"durationSeconds": seconds, "startAt": _recent(minutes_ago).isoformat()}}


# ------------------------------------------------------------------ 谁能调


@pytest.mark.parametrize("body", [{"taskId": "A"}, {"projectId": "Q"}, {"taskId": "SAME"}])
def test_gate_runs_before_anything_else_for_every_body_shape(client, ai_token, body):  # noqa: F811
    """两种请求体、以及「目标已是当前归属」的幂等早退，都先过门：403 时一个字节不写、也不泄漏段在不在。"""
    w = _world(client)
    sid = _session(client, w["bucket"])
    _reassign(client, sid, taskId=w["a"])
    body = {key: {"A": w["b"], "Q": w["q"], "SAME": w["a"]}[value] for key, value in body.items()}
    events, tasks = _db()["events"].count_documents({}), _db()["tasks"].count_documents({})
    for headers in (BEARER, AI_HEAD, {"X-Nexus-Client-Token": "not-a-known-token-000000"}):
        assert "detail" in _reassign(client, sid, 403, headers=headers, **body)
        # 段不存在也是 403，不是 404：被拒的调用方探不出哪些事件 id 存在
        assert "detail" in _reassign(client, "evt_nope", 403, headers=headers, **body)
    assert _db()["events"].count_documents({}) == events and _db()["tasks"].count_documents({}) == tasks
    assert len(_chain(sid)) == 1


def test_strict_mode_requires_the_human_credential(client, monkeypatch):
    from app import config  # noqa: PLC0415

    w = _world(client)
    sid = _session(client, w["bucket"])
    monkeypatch.setattr(config, "settings", dataclasses.replace(
        config.settings, human_client_token=HUMAN_TOKEN, actor_strict=True))
    assert "人路径凭据" in _reassign(client, sid, 403, taskId=w["a"])["detail"]
    assert _chain(sid) == []
    assert _reassign(client, sid, headers={"X-Nexus-Client-Token": HUMAN_TOKEN}, taskId=w["a"])["seq"] == 1


# ------------------------------------------------------------------ 类型


@pytest.mark.parametrize("body", [
    {"taskId": {"$ne": None}}, {"taskId": ["t_x"]}, {"taskId": 7}, {"projectId": {"$gt": ""}},
    {"taskId": ""}, {"taskId": "t" * 200},
])
def test_non_string_targets_never_reach_a_query(client, body):
    w = _world(client)
    sid = _session(client, w["bucket"])
    _reassign(client, sid, 422, **body)
    assert _chain(sid) == []


def test_malformed_restored_facts_are_skipped_not_fatal(client):
    """快照恢复搬进来的 session.reassigned 没过改挂端点：载荷类型不对的那条当没有，重建与读端都不许炸。"""
    w = _world(client)
    sid = _session(client, w["bucket"], 60, 600)
    good = {"spec": "yq-event/v1", "type": "session.reassigned", "user": "u_local", "source": "session-reassign",
            "time": _recent(1).isoformat(), "subject": {"zone": w["zone"], "project": w["p"], "task": w["a"]},
            "flags": []}
    session = _db()["events"].find_one({"id": sid})
    copied = {"source": session["source"], "dedupeKey": session["dedupeKey"]}
    bad_payloads = [
        None, "x", {"sessionEventId": sid, "seq": 1, "session": "x"},
        {"sessionEventId": {"$ne": None}, "seq": 1, "session": copied},
        {"sessionEventId": [sid], "seq": 1, "session": copied},
        {"sessionEventId": sid, "seq": "9", "session": copied},
        {"sessionEventId": sid, "seq": True, "session": copied},
        {"sessionEventId": sid, "seq": 9, "session": {"source": ["a"], "dedupeKey": {"b": 1}}},
        {"sessionEventId": sid, "seq": 9, "session": {"source": "someone-else", "dedupeKey": "other"}},
    ]
    _db()["events"].insert_many([{**good, "id": f"evt_bad{i}", "dedupeKey": f"bad-{i}", "data": data}
                                 for i, data in enumerate(bad_payloads)])
    _rebuild()
    assert _task_seconds(client) == {w["bucket"]: 600}, "坏的改挂一条都不起作用"
    assert "currentSubject" not in _read(client, type="session.completed")["items"][0]
    assert _read(client)["total"] == 1 + len(bad_payloads)
    assert _reassign(client, sid, taskId=w["b"])["seq"] == 1  # 真的改挂照常，从 1 起
    assert _task_seconds(client) == {w["b"]: 600}


def test_dotted_external_ids_do_not_break_the_reads(client):
    # 外部事件的 id 带点时 proj_current 里是嵌套文档：读端摘 0 的时候不能拿它比大小
    w = _world(client)
    assert _post(client, EVENTS, _external(w, "evt_dot", "dot-1", "t.with.dots"))["accepted"] == 1
    _reassign(client, _session(client, w["bucket"]), taskId=w["a"])
    assert client.get(f"{API}/export").status_code == 200
    assert client.post(f"{API}/timer/start", json={"taskId": w["a"]}).status_code == 200
    assert client.get(f"{API}/views/current").json()["task"]["totalSeconds"] == 600
    client.post(f"{API}/timer/cancel")


# ------------------------------------------------------------------ 幂等键与身份


def test_client_chosen_dedupe_key_cannot_swallow_a_reassignment(client):
    """外部 source 的 dedupeKey 是它自己定的：先投一条恰好叫 reassign:<段>:1 的段，改挂的加减也不能被当成「已应用」。"""
    w = _world(client)
    sid = _session(client, w["bucket"], 60, 600)
    for task in (w["bucket"], w["a"]):  # 旧归属那行、新归属那行各占一条同名键
        squat = _external(w, f"evt_squat_{task}", f"reassign:{sid}:1", task, 30, 60)
        assert _post(client, EVENTS, {**squat, "source": f"ext-{task}"})["accepted"] == 1
    assert _task_seconds(client) == {w["bucket"]: 660, w["a"]: 60}

    assert _reassign(client, sid, taskId=w["a"])["seq"] == 1
    assert _task_seconds(client) == {w["bucket"]: 60, w["a"]: 660}
    # proj_current 的幂等账只记 dedupeKey 不分 source（既有行为）：两条同名的外部段它只算了第一条，
    # 与改挂无关——要看的是改挂的 600 秒照样挪过去了
    assert _views(client)["projections"]["proj_current"]["tasks"] == {w["bucket"]: 60, w["a"]: 600}
    live = _views(client)
    _rebuild()
    assert _views(client) == live


def test_another_session_reusing_the_event_id_does_not_inherit_the_assignment(client):
    """事件 id 不唯一：改挂之后再投一条同 id 的段，它不跟着走（读端、重建都按 (source, dedupeKey) 认那一段）。"""
    w = _world(client)
    sid = _session(client, w["bucket"], 60, 600)
    _reassign(client, sid, taskId=w["c"])
    assert _post(client, EVENTS, _external(w, sid, "twin-1", w["bucket"], 30, 60))["accepted"] == 1

    items = {e["dedupeKey"]: e for e in _read(client, type="session.completed")["items"]}
    assert "currentSubject" not in items["twin-1"]
    assert [e["dedupeKey"] for e in _read(client, type="session.completed", taskId=w["bucket"])["items"]] == ["twin-1"]
    assert _task_seconds(client) == {w["bucket"]: 60, w["c"]: 600}
    live = _views(client)
    _rebuild()
    assert _views(client) == live
    # 这个 id 现在对着两段：不猜，409；台账不动
    _reassign(client, sid, 409, taskId=w["a"])
    assert len(_chain(sid)) == 1


def test_another_tenants_session_and_assignment_stay_invisible(client):
    b = {"X-Nexus-Tenant": "ch_bbbb"}
    w = _world(client)
    sid = _session(client, w["bucket"])
    _reassign(client, sid, taskId=w["a"])
    theirs = _world(client, b)
    _reassign(client, sid, 404, headers=b, taskId=theirs["a"])     # 别人的段：查无此段
    _reassign(client, sid, 404, headers=b, projectId=theirs["p"])
    _reassign(client, sid, 404, taskId=theirs["a"])                # 自己的段挂不到别人的任务上
    _reassign(client, sid, 404, projectId=theirs["p"])
    assert client.get(EVENTS, headers=b).json()["total"] == 0      # 改挂事实也只在自己的台账里
    # 别的租户用同一个事件 id 记了一段、也改挂了：两边各算各的
    other = _post(client, EVENTS, {**_external(theirs, sid, "b-1", theirs["bucket"]), "user": "ch_bbbb"}, b)
    assert other["accepted"] == 1
    assert _reassign(client, sid, headers=b, taskId=theirs["b"])["seq"] == 1
    mine = _read(client, type="session.completed")["items"][0]
    assert mine["currentSubject"]["task"] == w["a"] and len(_chain(sid)) == 2  # 两个租户各一条，互不影响
    _rebuild()
    assert _read(client, type="session.completed")["items"][0]["currentSubject"]["task"] == w["a"]
    assert _task_seconds(client) == {w["a"]: 600}
