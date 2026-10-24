"""忽略并记住（契约 v2.22）评审跟进：单一「人」判据、存储截断差、先读规则再清过期、add 的失败路径、sweep 计数、计数器尽力而为。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from test_activity_ignore import DEV, IGN, _db, _ignore, _raw_has, _seg, _upload
from test_activity_ignore_gate import MARK, _beat_raw, _boom, _span

# ─────────────────────────────────────────── 1：读写共用一个「人」判据

NON_HUMAN = [{"Authorization": "Bearer t"}, {"X-Nexus-Scope": "write"}, {"X-Nexus-Scope": "read"},
             {"X-Nexus-Scope": "report", "X-Nexus-Anonymous": "1"}, {"X-Nexus-Anonymous": "1"}]


def test_one_human_predicate_for_read_and_write(client):
    rule = _ignore(client, "code", MARK)
    for h in NON_HUMAN:
        assert client.post(IGN, json={"app": "x"}, headers=h).status_code == 403, h
        assert client.delete(f"{IGN}/{rule['id']}", headers=h).status_code == 403, h
        r = client.get(IGN, headers=h)
        if r.status_code == 200:  # report / 匿名在中间件就 403
            assert all("titleContains" not in i and i["hasTitleFilter"] for i in r.json()["items"]), h
    assert client.get(IGN).json()["items"][0]["titleContains"] == MARK
    assert client.get(IGN).json()["total"] == 1   # 上面的 DELETE 一个都没生效


# ─────────────────────────────────────────── 2：存储截断后才匹配的差

@pytest.mark.parametrize("pad", [" ", "​", "​ ​"])
def test_padded_app_that_folds_into_the_rule_after_storage_clip_is_ignored(client, pad):
    _ignore(client, "code")
    app = "code" + pad * 200 + "x"   # 全文折叠后是 codex（过得了闸门），存储截到 128 后折叠成 code
    assert _upload(client, [_seg(10, app, "t")]).get("ignored") == 1
    now, sp = _span("t", 5, 3)
    _beat_raw(client, app, "t", spans=[{**sp, "app": app}], sentAt=now.isoformat())
    doc = _db()["activity_presence"].find_one({"deviceId": DEV})
    assert doc["app"] == "" and not any(s["app"].startswith("code") for s in doc["spans"])
    assert _db()["activity_suggestions"].count_documents({}) == 0


def test_padded_title_is_matched_on_the_received_and_on_the_clipped_form(client):
    _ignore(client, "code", "abc")
    assert _upload(client, [_seg(10, "code", "ab" + "​" * 600 + "c")]).get("ignored") == 1   # 收到的折叠后含 abc
    assert _upload(client, [_seg(20, "code", "abc" + "​" * 600 + "ab")]).get("ignored") == 1  # 同理，另一头


def test_clipped_form_is_tested_too():
    from app.modules.activity import ignore  # noqa: PLC0415

    rules_ = ignore.Prepared([{"id": "r", "app": "code", "titleContains": "abc"}])
    assert rules_.find("code", "ab c") is None
    assert rules_.find("code" + "\u200b" * 200 + "x", "abc") is not None   # 收到的是 codex，截后折叠 = code


def test_storage_clip_constants_agree():
    from app.modules.activity import presence, service  # noqa: PLC0415

    assert (service._MAX_APP, service._MAX_TITLE) == (presence._MAX_APP, presence._MAX_TITLE)


# ─────────────────────────────────────────── 3：规则读不到 → 什么都没存、什么都没删

def test_rules_unreadable_upload_neither_stores_nor_purges(client, monkeypatch):
    from app.config import settings  # noqa: PLC0415
    from app.modules.activity import ignore_repo  # noqa: PLC0415

    old = datetime.now(timezone.utc) - timedelta(days=settings.suggestion_ttl_days + 5)
    _db()["activity_suggestions"].insert_one({"user": "u_local", "id": "sug_old", "dedupeKey": "old", "status": "pending",
                                              "app": "a", "title": "b", "durationSeconds": 1, "startTs": old, "endTs": old,
                                              "receivedAt": old})
    monkeypatch.setattr(ignore_repo, "all_rules", _boom)
    with pytest.raises(RuntimeError):
        _upload(client, [_seg(10, "chrome", MARK)])
    assert _db()["activity_suggestions"].count_documents({}) == 1 and _raw_has(MARK) == {}


# ─────────────────────────────────────────── 4：add 的失败路径、重复建不再清理

def test_count_raising_removes_the_new_rule_and_fails(client, monkeypatch):
    from app.modules.activity import ignore_repo  # noqa: PLC0415

    monkeypatch.setattr(ignore_repo, "count", _boom)
    with pytest.raises(RuntimeError):
        client.post(IGN, json={"app": "code"})
    monkeypatch.undo()
    assert client.get(IGN).json()["total"] == 0


def test_reread_failures_after_insert_are_not_fatal(client, monkeypatch):
    from app.modules.activity import ignore_repo  # noqa: PLC0415

    _upload(client, [_seg(10, "code", "a", 7)])
    monkeypatch.setattr(ignore_repo, "all_rules", _boom)
    r = client.post(IGN, json={"app": "code"})
    assert r.status_code == 201 and r.json()["created"] is True and r.json()["removed"] == 1
    monkeypatch.undo()
    assert client.get(IGN).json()["items"][0]["hits"] == 1


def test_repost_of_an_existing_rule_does_not_sweep(client):
    first = _ignore(client, "code")
    _db()["activity_suggestions"].insert_one({"user": "u_local", "id": "sug_1", "dedupeKey": "d", "status": "pending",
                                              "app": "code", "title": "left", "durationSeconds": 3})
    again = _ignore(client, "code")
    assert (again["id"], again["created"], again["removed"]) == (first["id"], False, 0)
    assert _db()["activity_suggestions"].count_documents({}) == 1


# ─────────────────────────────────────────── 5：sweep 的秒数只算确实删掉的、每批记一次

def test_sweep_seconds_count_only_actually_deleted_and_survive_a_later_failure(client, monkeypatch):
    from app.modules.activity import repo  # noqa: PLC0415

    now = datetime.now(timezone.utc)

    def doc(i, status="pending", sec=10):
        return {"user": "u_local", "id": f"sug_{i}", "dedupeKey": f"d{i}", "status": status, "app": "code", "title": "t",
                "durationSeconds": sec, "startTs": now, "endTs": now}

    _db()["activity_suggestions"].insert_many([doc(1), doc(2, sec=5), doc(3, "confirmed", 99)])
    real = repo.pending_windows

    def windows(user, batch=1000):
        yield from real(user, batch)
        yield [{"id": "sug_ghost", "app": "code", "title": "t", "durationSeconds": 1000}]   # 扫到后自己消失了
        raise RuntimeError("later batch failed")

    monkeypatch.setattr(repo, "pending_windows", windows)
    out = _ignore(client, "code")
    assert out["removed"] == 2 and out["hits"] == 2 and out["seconds"] == 15


# ─────────────────────────────────────────── 8：计数器写失败不挡入库

def test_hit_failure_does_not_fail_the_upload(client, monkeypatch):
    from app.modules.activity import ignore_repo  # noqa: PLC0415

    _ignore(client, "code")
    monkeypatch.setattr(ignore_repo, "hit", _boom)
    out = _upload(client, [_seg(10, "code", "a"), _seg(20, "chrome", "b")])
    assert out["ignored"] == 1 and out["accepted"] == 1
