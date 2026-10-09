"""忽略并记住（契约 v2.22）：规则的增删读、建规则时的唯一清理、读接口不依赖规则。

写入闸门（匹配的窗口入口就丢 / 抹）在 ``test_activity_ignore_gate.py``。这不是隐私擦除：已记下的东西不动。
"""

from __future__ import annotations

import json
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


def test_unignore_makes_windows_count_again(client):
    rule = _ignore(client, "chrome")
    _beat(client, "chrome", "news")
    client.delete(f"{IGN}/{rule['id']}")
    _beat(client, "chrome", "news2")
    doc = _db()["activity_presence"].find_one({})
    assert doc["spans"][-1]["title"] == "news2"


def test_report_and_anonymous_scopes_cannot_read_or_write_ignores(client):
    for headers in ({"X-Nexus-Scope": "report", "X-Nexus-Anonymous": "1"}, {"X-Nexus-Scope": "report", "Authorization": "Bearer t"}):
        assert client.get(IGN, headers=headers).status_code == 403
        assert client.post(IGN, json={"app": "x"}, headers=headers).status_code == 403


# ─────────────────────────────────────────── 辅助：每个集合里序列化后含某段标题的文档数


def _raw_has(title: str) -> dict[str, int]:
    """每个集合里序列化后含这段标题的文档数（只列非 0 的）。"""
    out = {}
    for name in _db().list_collection_names():
        n = sum(1 for d in _db()[name].find({}, {"_id": 0}) if title in json.dumps(d, default=str, ensure_ascii=False))
        if n:
            out[name] = n
    return out


def test_non_human_callers_get_only_the_projection(client):
    _ignore(client, "code", PRIVATE_TITLE)
    _upload(client, [_seg(10, "code", PRIVATE_TITLE, 100)])
    human = client.get(IGN).json()["items"][0]
    assert human["titleContains"] == PRIVATE_TITLE and human["hits"] == 1
    for headers in ({"Authorization": "Bearer t"}, {"X-Nexus-Scope": "read", "Authorization": "Bearer t"}, {"X-Nexus-Scope": "read"}):
        [row] = client.get(IGN, headers=headers).json()["items"]
        assert set(row) == {"id", "app", "hasTitleFilter", "hits", "seconds", "createdAt"} and row["hasTitleFilter"] is True
        assert PRIVATE_TITLE not in json.dumps(row, ensure_ascii=False)


def test_creating_a_rule_removes_pending_and_dismissed_even_past_5000_and_reports_the_count(client):
    now = datetime.now(timezone.utc)

    def doc(i, title, status="pending"):
        return {"user": "u_local", "id": f"sug_{i}", "dedupeKey": f"d{i}", "status": status, "app": "code", "title": title,
                "durationSeconds": 3, "startTs": now, "endTs": now}

    # 命中的散在扫描顺序的中间和最末尾，含 dismissed；别的 app 与已确认的不动
    docs = [doc(i, "别的") for i in range(2700)] + [doc(5000 + i, PRIVATE_TITLE) for i in range(3)]
    docs += [doc(6000 + i, "别的") for i in range(2700)] + [doc(9000 + i, PRIVATE_TITLE, "dismissed") for i in range(2)]
    docs += [doc(9500, PRIVATE_TITLE, "confirmed"), {**doc(9600, PRIVATE_TITLE), "app": "chrome"}]
    _db()["activity_suggestions"].insert_many(docs)
    out = _ignore(client, "code", PRIVATE_TITLE)
    assert out["removed"] == 5 and out["hits"] == 5
    assert _db()["activity_suggestions"].count_documents({}) == 5400 + 2


def test_cleanup_failure_does_not_fail_creation_and_reports_what_was_removed(client, monkeypatch):
    from app.modules.activity import repo  # noqa: PLC0415

    _upload(client, [_seg(10, "code", "a"), _seg(20, "code", "b")])
    monkeypatch.setattr(repo, "delete_pending", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    out = _ignore(client, "code")
    assert out["created"] is True and out["removed"] == 0
    assert client.get(IGN).json()["total"] == 1   # 规则在；以后的窗口照样被过滤
    assert _upload(client, [_seg(30, "code", "c")]).get("ignored") == 1


def test_nothing_else_is_erased_history_stays(client):
    """契约「这不是隐私擦除」：规则之前记下的在场历史、已确认的记录、AI 规则都不动。"""
    _beat(client, "chrome", PRIVATE_TITLE)
    _ignore(client, "chrome")
    assert _raw_has(PRIVATE_TITLE).get("activity_presence") == 1


def test_read_endpoints_never_touch_the_ignore_rules(client, monkeypatch):
    from app.modules.activity import ignore_repo  # noqa: PLC0415

    _upload(client, [_seg(10, "code", PRIVATE_TITLE)])
    _beat(client, "code", PRIVATE_TITLE)
    rule = _ignore(client, "chrome")
    monkeypatch.setattr(ignore_repo, "all_rules", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("must not be read")))
    rep = client.post(f"{API}/activity/reports", json={"summary": "s", "items": []})
    urls = [f"{API}/views/current", f"{API}/views/lanes", SUG, f"{API}/detector/rules", f"{API}/detector/rules/drafts/current"]
    if rep.status_code == 201:
        urls.append(f"{API}/activity/reports/{rep.json()['reportId']}")
    for url in urls:
        assert client.get(url).status_code == 200, url
    focus = client.get(f"{API}/views/current").json()["focus"]
    assert focus["title"] == PRIVATE_TITLE   # 读不遮：规则之前记下的原样
    assert rule["id"]
