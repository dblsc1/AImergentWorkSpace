"""忽略并记住（契约 v2.22）：匹配的窗口不当作工作。

要害：被忽略的窗口**一个标题都不落库**（建议上传直接丢、在场心跳换成「没有窗口」），也就不会成为焦点 /
自动跟踪目标 / 「请你选」的窗口 / 泳道的「你在看」；规则上只有计数器；取消忽略后以后的窗口照常。
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


def test_report_and_anonymous_scopes_cannot_read_or_write_ignores(client):
    for headers in ({"X-Nexus-Scope": "report", "X-Nexus-Anonymous": "1"}, {"X-Nexus-Scope": "report", "Authorization": "Bearer t"}):
        assert client.get(IGN, headers=headers).status_code == 403
        assert client.post(IGN, json={"app": "x"}, headers=headers).status_code == 403


# ─────────────────────────────────────────── 每个存过窗口标题的地方：建规则后原始集合里都搜不到


def _raw_has(title: str) -> dict[str, int]:
    """每个集合里序列化后含这段标题的文档数（只列非 0 的）。"""
    out = {}
    for name in _db().list_collection_names():
        n = sum(1 for d in _db()[name].find({}, {"_id": 0}) if title in json.dumps(d, default=str, ensure_ascii=False))
        if n:
            out[name] = n
    return out


def test_creating_a_rule_purges_every_store_that_held_the_title(client, seeded):
    task = next(iter(seeded["tasks"].values()))["id"]
    title = "银行转账-私密页面"
    keep = "公开页面"
    _upload(client, [_seg(10, "chrome", title), _seg(20, "chrome", title + " 二"), _seg(30, "chrome", keep)])
    items = client.get(SUG, params={"status": "pending"}).json()["items"]
    mine = [i for i in items if i["title"].startswith(title)]
    # 一段已忽略（dismissed）的、一段已确认（事实）的
    assert client.post(f"{SUG}/{mine[0]['id']}/dismiss").status_code == 200
    _upload(client, [_seg(40, "chrome", title + " 三")])
    confirmed = next(i for i in client.get(SUG, params={"status": "pending"}).json()["items"] if i["title"] == title + " 三")
    assert client.post(f"{SUG}/{confirmed['id']}/confirm", json={"taskId": task}).status_code == 200
    # 在场时间线 + 当前窗口、人的临时选择、AI 问询、报告（引用这些建议）
    _beat(client, "chrome", title)
    now = datetime.now(timezone.utc)
    _db()["activity_choices"].insert_one({"user": "u_local", "key": "wk_" + "a" * 20, "kind": "choice", "app": "chrome",
                                          "title": title, "taskId": task, "at": now, "expiresAt": now + timedelta(hours=1)})
    _db()["activity_ai_asks"].insert_one({"user": "u_local", "key": "wk_" + "b" * 20, "app": "chrome", "title": title,
                                          "claimedAt": now})
    _db()["activity_ai_asks"].insert_one({"user": "u_local", "key": "_tenant", "polledAt": now, "claims": []})
    other = [i for i in client.get(SUG, params={"status": "pending"}).json()["items"] if i["title"].startswith(title)]
    rep = client.post(f"{API}/activity/reports", json={"summary": "s", "items": [
        {"kind": "assign", "suggestionIds": [other[0]["id"]], "taskId": task}]}).json()
    before = _raw_has(title)
    assert {"activity_suggestions", "activity_presence", "activity_choices", "activity_ai_asks"} <= set(before)

    _ignore(client, "chrome", "银行转账")
    left = _raw_has(title)
    # 只剩已确认的建议（事实的出处）和台账里的事实本身——这两样是人确认过的记录，不改写
    assert set(left) <= {"activity_suggestions", "events", "proj_lanes", "proj_daily_stats"}, left
    assert left.get("activity_suggestions", 0) == 1
    assert client.get(SUG, params={"status": "pending"}).json()["items"][0]["title"] == keep
    assert client.get(SUG, params={"status": "dismissed"}).json()["items"] == []
    assert _db()["activity_ai_asks"].count_documents({"key": "_tenant"}) == 1  # 租户级那份不是窗口，不碰
    # 焦点（读在场）不再带标题，也不再有目标；报告里那条建议成了 stale
    focus = client.get(f"{API}/views/current").json()["focus"]
    assert (focus["app"], focus["title"], focus["projectId"]) == ("", "", None)
    assert client.get(f"{API}/views/lanes").json()["human"]["presence"][0]["title"] == ""
    got = client.get(f"{API}/activity/reports/{rep['reportId']}").json()
    assert got["items"][0]["staleNow"] == 1 and got["items"][0]["suggestions"] == []
    # MCP / AI 工具读的就是上面这些端点：历史里也不剩（已确认的除外，历史本来只由确认的事实派生）
    hist = client.get(f"{SUG}/history").json()
    assert title not in json.dumps({"c": hist["collections"], "r": hist["rejected"]}, ensure_ascii=False)
    # 取消忽略不会让清掉的东西回来
    client.delete(f"{IGN}/{client.get(IGN).json()['items'][0]['id']}")
    assert _raw_has(title) == left
    assert len(client.get(SUG, params={"status": "pending"}).json()["items"]) == 1


# ─────────────────────────────────────────── 并发：读了旧规则的写入者，写在建规则的清理之后，也写不回标题


def test_stale_presence_cas_loses_after_the_purge_bumps_the_version(client):
    from app.modules.activity import repo  # noqa: PLC0415

    _beat(client, "chrome", PRIVATE_TITLE)
    prev = repo.presence_get("u_local", DEV)
    _ignore(client, "chrome")  # 清理在这里发生；写入者手里还是清理前读到的那份
    stale = {k: v for k, v in prev.items() if k not in ("v", "gen", "_id")}
    assert repo.presence_cas(stale, prev) is None  # 版本已变：条件写写不中，必须重读
    assert _raw_has(PRIVATE_TITLE) == {}


def _create_rule_mid_flight(client, monkeypatch, module, name, **rule):
    """让 module.name 先照常跑完，然后（在调用方继续往下写之前）建规则——读了旧规则、写在清理之后的交错。"""
    real = getattr(module, name)

    def wrapper(*args, **kwargs):
        out = real(*args, **kwargs)
        assert client.post(IGN, json=rule).status_code == 201
        return out

    monkeypatch.setattr(module, name, wrapper)


def test_heartbeat_that_read_the_rules_before_the_rule_cannot_write_the_title_back(client, monkeypatch):
    from app.modules.activity import ignore  # noqa: PLC0415

    _create_rule_mid_flight(client, monkeypatch, ignore, "mask_beat", app="chrome")
    _beat(client, "chrome", PRIVATE_TITLE)
    assert len(client.get(IGN).json()["items"]) == 1
    assert _raw_has(PRIVATE_TITLE) == {}


def test_upload_that_read_the_rules_before_the_rule_cannot_insert_the_title(client, monkeypatch):
    from app.modules.activity import ignore  # noqa: PLC0415

    _create_rule_mid_flight(client, monkeypatch, ignore, "drop", app="chrome")
    _upload(client, [_seg(10, "chrome", PRIVATE_TITLE)])
    assert _raw_has(PRIVATE_TITLE) == {}
    assert _pending(client) == []


def test_rule_lookup_failure_fails_closed_nothing_is_stored(client, monkeypatch):
    from app.modules.activity import ignore_repo  # noqa: PLC0415

    def boom(user):
        raise RuntimeError("mongo hiccup")

    monkeypatch.setattr(ignore_repo, "all_rules", boom)
    with pytest.raises(RuntimeError):
        _upload(client, [_seg(10, "chrome", PRIVATE_TITLE)])
    with pytest.raises(RuntimeError):
        _beat(client, "chrome", PRIVATE_TITLE)
    assert _raw_has(PRIVATE_TITLE) == {}  # 查不了规则 = 当作忽略：什么都没存


def test_rule_applies_immediately_no_cache(client):
    _ignore(client, "chrome")
    assert _upload(client, [_seg(10, "chrome", PRIVATE_TITLE)]).get("ignored") == 1
    _beat(client, "chrome", PRIVATE_TITLE)
    assert _raw_has(PRIVATE_TITLE) == {}


def test_unignore_racing_an_ingest_does_not_resurrect_or_store(client, monkeypatch):
    from app.modules.activity import ignore  # noqa: PLC0415

    rule = _ignore(client, "chrome")
    real = ignore.drop

    def drop_then_unignore(*args, **kwargs):
        out = real(*args, **kwargs)  # 这一批是在规则还在时过滤的
        assert client.delete(f"{IGN}/{rule['id']}").status_code == 204
        return out

    monkeypatch.setattr(ignore, "drop", drop_then_unignore)
    assert _upload(client, [_seg(10, "chrome", PRIVATE_TITLE)])["accepted"] == 0
    assert _raw_has(PRIVATE_TITLE) == {}


def test_purge_failure_reports_error_keeps_rule_and_retries_on_next_write(client, monkeypatch):
    from app.modules.activity import ignore_repo  # noqa: PLC0415

    _beat(client, "chrome", PRIVATE_TITLE)
    now = datetime.now(timezone.utc)
    _db()["activity_choices"].insert_one({"user": "u_local", "key": "wk_" + "c" * 20, "kind": "dismiss", "app": "chrome",
                                          "title": PRIVATE_TITLE, "at": now, "expiresAt": now + timedelta(hours=1)})
    real = ignore_repo.drop_windows
    monkeypatch.setattr(ignore_repo, "drop_windows", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        _ignore(client, "chrome")
    assert client.get(IGN).json()["total"] == 1  # 规则留着：以后的写入照样被过滤
    assert _db()["activity_choices"].count_documents({}) == 1  # 清理没做完，残留还在
    monkeypatch.setattr(ignore_repo, "drop_windows", real)
    _beat(client, "firefox", "other")  # 下一次写入顺手补清没清完的规则
    assert _raw_has(PRIVATE_TITLE) == {}


def test_choice_written_after_the_purge_is_removed_again(client, monkeypatch, seeded):
    from app.modules.activity import auto  # noqa: PLC0415

    task = next(iter(seeded["tasks"].values()))["id"]
    _beat(client, "chrome", PRIVATE_TITLE)
    key = auto.window_key("chrome", PRIVATE_TITLE)
    real = auto._find

    def find_then_rule(user, k):
        out = real(user, k)  # 窗口是在规则之前找到的
        assert client.post(IGN, json={"app": "chrome"}).status_code == 201
        return out

    monkeypatch.setattr(auto, "_find", find_then_rule)
    resp = client.post(f"{API}/activity/choice", json={"key": key, "taskId": task})
    assert resp.status_code == 200, resp.text
    assert _raw_has(PRIVATE_TITLE) == {}
