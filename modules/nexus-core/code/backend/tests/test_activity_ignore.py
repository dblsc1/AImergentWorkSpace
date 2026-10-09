"""忽略并记住（契约 v2.22）：匹配的窗口不当作工作。

要害：被忽略的窗口**一个标题都不落库**（建议上传直接丢、在场心跳换成「没有窗口」），也就不会成为焦点 /
自动跟踪目标 / 「请你选」的窗口 / 泳道的「你在看」；规则上只有计数器；取消忽略后以后的窗口照常。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

API = "/api/core"
IGN = f"{API}/activity/ignores"
SUG = f"{API}/activity/suggestions"
PRES = f"{API}/activity/presence"
DEV = "dev_3f9a1c2b7d4e5a60"
A = {"X-Nexus-Tenant": "ch_aaaa"}
B = {"X-Nexus-Tenant": "ch_bbbb"}
PRIVATE_TITLE = "银行转账-私密页面"


def _db():
    from app.repo import get_db  # noqa: PLC0415

    return get_db()


def _seg(minutes_ago=10, app="chrome", title="home", seconds=240) -> dict:
    end = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=minutes_ago)
    start = end - timedelta(seconds=seconds)
    return {"startAt": start.isoformat(), "endAt": end.isoformat(), "durationSeconds": seconds, "app": app,
            "title": title, "suggestion": {"taskId": None, "confidence": 0.0, "reason": "", "classifier": "rules"}}


def _upload(client, segs, headers=None):
    resp = client.post(SUG, json={"deviceId": DEV, "segments": segs}, headers=headers or {})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _pending(client, headers=None):
    return client.get(SUG, params={"status": "pending"}, headers=headers or {}).json()["items"]


def _ignore(client, app="chrome", title=None, headers=None, expect=201):
    body = {"app": app, **({"titleContains": title} if title is not None else {})}
    resp = client.post(IGN, json=body, headers=headers or {})
    assert resp.status_code == expect, resp.text
    return resp.json()


def _beat(client, app="chrome", title=PRIVATE_TITLE, afk=False, headers=None, **extra):
    resp = client.post(PRES, json={"deviceId": DEV, "app": app, "title": title, "afk": afk, **extra},
                       headers=headers or {})
    assert resp.status_code == 200, resp.text


# ─────────────────────────────────────────── 规则的增删读


def test_rules_crud_is_idempotent_and_human_only(client):
    assert client.get(IGN).json() == {"total": 0, "items": []}
    first = _ignore(client, "Chrome")
    assert (first["created"], first["removed"], first["titleContains"], first["hits"]) == (True, 0, None, 0)
    again = _ignore(client, "chrome")  # 不分大小写：同一条
    assert again["id"] == first["id"] and again["created"] is False
    _ignore(client, "chrome", "银行")
    assert [(r["app"], r["titleContains"]) for r in client.get(IGN).json()["items"]] == [("Chrome", None), ("chrome", "银行")]
    for headers in ({"Authorization": "Bearer t"}, {"X-Nexus-Scope": "read", "Authorization": "Bearer t"},
                    {"X-Nexus-Scope": "report", "X-Nexus-Anonymous": "1"}):
        assert client.post(IGN, json={"app": "x"}, headers=headers).status_code == 403
        assert client.delete(f"{IGN}/{first['id']}", headers=headers).status_code == 403
    assert client.get(IGN).json()["total"] == 2
    assert client.delete(f"{IGN}/{first['id']}").status_code == 204
    assert client.delete(f"{IGN}/{first['id']}").status_code == 204  # 幂等
    assert client.get(IGN).json()["total"] == 1


@pytest.mark.parametrize("body", [{}, {"app": ""}, {"app": "x" * 600}, {"app": "x", "extra": 1}, {"app": "x", "titleContains": 1}])
def test_bad_bodies(client, body):
    assert client.post(IGN, json=body).status_code == 422


def test_caps_and_whitespace_only_app(client, monkeypatch):
    from app.modules.activity import ignore  # noqa: PLC0415

    assert client.post(IGN, json={"app": "   "}).status_code == 400
    assert client.post(IGN, json={"app": "x" * 200}).status_code == 422
    monkeypatch.setattr(ignore, "MAX_IGNORES", 1)
    _ignore(client, "a")
    assert client.post(IGN, json={"app": "b"}).status_code == 422
    _ignore(client, "a", expect=201)  # 已有的那条不受上限影响


def test_tenant_isolation(client):
    _ignore(client, "chrome", headers=A)
    assert client.get(IGN, headers=B).json()["total"] == 0
    assert _upload(client, [_seg()], headers=B)["accepted"] == 1
    assert _upload(client, [_seg()], headers=A).get("ignored") == 1


# ─────────────────────────────────────────── 建议：不存、计数、取消后恢复


def test_matching_uploads_are_dropped_and_counted_not_stored(client):
    rule = _ignore(client, "chrome", "银行")
    out = _upload(client, [_seg(10, "chrome", PRIVATE_TITLE, 240), _seg(20, "Chrome", "银行首页", 100),
                           _seg(30, "chrome", "news"), _seg(40, "code", PRIVATE_TITLE)])
    assert (out["accepted"], out["ignored"]) == (2, 2)
    assert {i["app"] for i in _pending(client)} == {"chrome", "code"} and len(_pending(client)) == 2
    assert not _db()["activity_suggestions"].count_documents({"title": {"$regex": "银行"}, "app": "chrome"})
    [item] = client.get(IGN).json()["items"]
    assert (item["id"], item["hits"], item["seconds"]) == (rule["id"], 2, 340) and item["lastHitAt"]
    # 重传同一段：照样丢（不是「重复」）
    assert _upload(client, [_seg(10, "chrome", PRIVATE_TITLE, 240)])["accepted"] == 0


def test_response_shape_is_unchanged_without_ignores(client):
    assert _upload(client, [_seg()]) == {"accepted": 1, "duplicates": 0, "rejected": []}


def test_creating_a_rule_deletes_matching_pending_but_not_confirmed_facts(client, seeded):
    task = next(iter(seeded["tasks"].values()))["id"]
    _upload(client, [_seg(10, "chrome", "a", 100), _seg(20, "chrome", "b", 200), _seg(30, "code", "c")])
    chrome = [i for i in _pending(client) if i["app"] == "chrome"]
    assert client.post(f"{SUG}/{chrome[0]['id']}/confirm", json={"taskId": task}).status_code == 200
    out = _ignore(client, "chrome")
    assert out["removed"] == 1 and out["hits"] == 1  # 只剩一条还 pending 的 chrome
    assert [i["app"] for i in _pending(client)] == ["code"]
    assert _db()["events"].count_documents({"type": "session.completed"}) == 1  # 已确认的事实不动


def test_unignore_restores_future_records(client):
    rule = _ignore(client, "chrome")
    assert _upload(client, [_seg(10)])["accepted"] == 0
    assert client.delete(f"{IGN}/{rule['id']}").status_code == 204
    assert _upload(client, [_seg(20)])["accepted"] == 1
    assert [i["app"] for i in _pending(client)] == ["chrome"]


# ─────────────────────────────────────────── 在场：不成为焦点 / 目标 / 注意力


def test_ignored_window_is_not_stored_nor_focus_nor_attention(client):
    run = client.post(f"{API}/agents/start", json={"agent": "cc", "tool": "claude-code", "label": "garden", "match": "garden"}).json()
    _ignore(client, "ptyxis")
    _beat(client, "ptyxis", "✳ garden " + PRIVATE_TITLE)
    doc = _db()["activity_presence"].find_one({})
    assert [(s["app"], s["title"], s["afk"]) for s in doc["spans"]] == [("", "", False)]
    assert (doc["app"], doc["title"]) == ("", "")
    assert PRIVATE_TITLE not in str(doc)  # 标题没有落库
    body = client.get(f"{API}/views/lanes").json()
    [agent] = body["agents"]
    assert agent["attention"] == [] and [p["runId"] for p in body["human"]["presence"]] == [None]
    assert body["human"]["needsChoice"] is None and body["human"]["auto"] is None
    focus = client.get(f"{API}/views/current").json()["focus"]
    assert (focus["state"], focus["app"], focus["title"], focus["taskId"], focus["projectId"]) == ("present", "", "", None, None)
    # 对照：别的窗口照旧能对上会话
    _beat(client, "kitty", "✳ garden")
    assert client.get(f"{API}/views/lanes").json()["agents"][0]["attention"] != [] and run["runId"]


def test_ignored_spans_in_a_batched_beat_are_masked_one_by_one(client):
    _ignore(client, "chrome", "银行")
    now = datetime.now(timezone.utc).replace(microsecond=0)
    spans = [{"app": "chrome", "title": PRIVATE_TITLE, "from": (now - timedelta(seconds=40)).isoformat(), "seconds": 20},
             {"app": "chrome", "title": "news", "from": (now - timedelta(seconds=20)).isoformat(), "seconds": 15,
              "guess": {"projectId": "p_x", "confidence": 0.9, "classifier": "rules"}}]
    _beat(client, "chrome", "news", sentAt=now.isoformat(), spans=spans)
    doc = _db()["activity_presence"].find_one({})
    assert [(s["app"], s["title"]) for s in doc["spans"]] == [("", ""), ("chrome", "news")]
    assert PRIVATE_TITLE not in str(doc) and "guess" not in doc["spans"][0]


def test_unignore_makes_windows_count_again(client):
    rule = _ignore(client, "chrome")
    _beat(client, "chrome", "news")
    client.delete(f"{IGN}/{rule['id']}")
    _beat(client, "chrome", "news2")
    doc = _db()["activity_presence"].find_one({})
    assert doc["spans"][-1]["title"] == "news2"
