"""活动建议（契约 v2.2「活动建议」节）。

要害一句：**建议不是事实，人确认了才是。** 所以最重的断言是「看不见」——没确认之前，
proj_daily_stats / proj_current / 甘特 / 回顾 / 导出 一个字节都不许动；确认之后写的那条
必须与补登同形（零投影改动的前提），且全系统至多一条。
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta, timezone

import pytest

API = "/api/core"
SUG = f"{API}/activity/suggestions"
A = {"X-Nexus-Tenant": "ch_aaaa"}
B = {"X-Nexus-Tenant": "ch_bbbb"}
DEV = "dev_3f9a1c2b7d4e5a60"


def _db():
    from app.repo import get_db  # noqa: PLC0415

    return get_db()


def _seg(start: datetime, *, minutes: int = 4, active: int | None = None, task_id=None,
         confidence: float = 0.9, **over) -> dict:
    end = start + timedelta(minutes=minutes)
    seg = {
        "startAt": start.isoformat(), "endAt": end.isoformat(),
        "durationSeconds": active if active is not None else minutes * 60,
        "app": "code", "title": "plot.gd — garden — Visual Studio Code",
        "suggestion": {"taskId": task_id, "confidence": confidence, "reason": "规则 #1 命中",
                       "classifier": "rules"},
    }
    seg.update(over)
    return seg


def _recent(minutes_ago: int = 5) -> datetime:
    """几分钟前、东八区写法——「今天、本周」，并顺带验证非 UTC 偏移。"""
    t = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=minutes_ago)
    return t.astimezone(timezone(timedelta(hours=8)))


def _upload(client, segments, headers=None, device=DEV):
    resp = client.post(SUG, json={"deviceId": device, "segments": segments}, headers=headers or {})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _pending(client, headers=None, **params):
    resp = client.get(SUG, params=params, headers=headers or {})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _session_events() -> list[dict]:
    return list(_db()["events"].find({"type": "session.completed"}, {"_id": 0}))


# ─────────────────────────────────────────── 上传 + 读


def test_upload_then_list_newest_first_with_contract_shape(client, seeded):
    task = seeded["tasks"]["示例任务三"]
    older, newer = _recent(30), _recent(10)
    out = _upload(client, [_seg(older, task_id=task["id"]), _seg(newer, active=100)])
    assert out == {"accepted": 2, "duplicates": 0, "rejected": []}

    body = _pending(client)
    assert body["total"] == 2
    first, second = body["items"]
    assert first["startAt"] == newer.isoformat() and second["startAt"] == older.isoformat()
    assert set(first) == {"id", "deviceId", "startAt", "endAt", "durationSeconds", "app", "title",
                          "suggestion", "status"}
    assert first["status"] == "pending" and first["deviceId"] == DEV and first["durationSeconds"] == 100
    assert second["suggestion"] == {"taskId": task["id"], "confidence": 0.9, "reason": "规则 #1 命中",
                                    "classifier": "rules"}
    assert first["id"].startswith("sug_")

    page = _pending(client, limit=1, offset=1)
    assert page["total"] == 2 and [i["id"] for i in page["items"]] == [second["id"]]
    assert _pending(client, status="confirmed") == {"total": 0, "items": []}


def test_reupload_is_noop_and_utc_normalised(client):
    start = _recent(20)
    _upload(client, [_seg(start)])
    # 同一时刻换成 Z 写法、换了标题和建议：仍是同一段，什么都不改
    same_utc = start.astimezone(timezone.utc)
    again = _seg(same_utc, title="别的标题", confidence=0.1)
    assert _upload(client, [again]) == {"accepted": 0, "duplicates": 1, "rejected": []}
    items = _pending(client)["items"]
    assert len(items) == 1 and items[0]["title"].startswith("plot.gd")
    # 另一台设备同一时刻：另一段
    assert _upload(client, [_seg(start)], device="dev_other")["accepted"] == 1


def test_unknown_task_in_suggestion_stored_as_null(client, seeded):
    out = _upload(client, [_seg(_recent(), task_id="t_nope", confidence=0.95)])
    assert out["accepted"] == 1
    sug = _pending(client)["items"][0]["suggestion"]
    assert sug["taskId"] is None and sug["confidence"] == 0


@pytest.mark.parametrize("bad", [
    {"app": ""}, {"app": "x" * 129}, {"title": "x" * 513}, {"title": None},
    {"durationSeconds": 0}, {"durationSeconds": 241}, {"durationSeconds": "60"}, {"durationSeconds": True},
    {"startAt": "2026-09-26T11:05:00"}, {"endAt": "nope"},
    {"suggestion": {"taskId": None, "confidence": 1.5, "reason": "", "classifier": "rules"}},
    {"suggestion": {"taskId": None, "confidence": True, "reason": "", "classifier": "rules"}},
    {"suggestion": {"taskId": None, "confidence": 0.5, "reason": "中" * 67, "classifier": "rules"}},
    {"suggestion": {"taskId": None, "confidence": 0.5, "reason": "", "classifier": "llm"}},
    {"suggestion": None},
])
def test_bad_segment_rejected_others_kept(client, bad):
    good = _seg(_recent(10))
    out = _upload(client, [good, _seg(_recent(20), **bad)])
    assert out["accepted"] == 1 and out["duplicates"] == 0
    assert [r["index"] for r in out["rejected"]] == [1] and out["rejected"][0]["reason"]
    assert _pending(client)["total"] == 1


def test_time_rules(client):
    now = _recent(0)
    backwards = _seg(now - timedelta(minutes=5)) | {"endAt": (now - timedelta(minutes=6)).isoformat()}
    future = _seg(now + timedelta(minutes=5))
    too_long = _seg(now - timedelta(days=2), minutes=60 * 25)
    reason_200_bytes_ok = _seg(now - timedelta(minutes=30))
    reason_200_bytes_ok["suggestion"]["reason"] = "中" * 66 + "ab"  # 200 字节整，放行
    out = _upload(client, [backwards, future, too_long, reason_200_bytes_ok, "not-an-object"])
    assert [r["index"] for r in out["rejected"]] == [0, 1, 2, 4]
    assert out["accepted"] == 1


@pytest.mark.parametrize("body", [
    {"deviceId": "dev:colon", "segments": []},
    {"deviceId": "", "segments": []},
    {"deviceId": DEV},
    {"deviceId": DEV, "segments": {}},
    {"deviceId": DEV, "segments": [{}] * 201},
])
def test_batch_shape_is_422(client, body):
    assert client.post(SUG, json=body).status_code == 422
    assert _db()["activity_suggestions"].count_documents({}) == 0


def test_list_bad_status_is_422(client):
    assert client.get(SUG, params={"status": "all"}).status_code == 422


# ─────────────────────────────────────────── 看不见：确认之前不是事实


def _human_views(client) -> tuple:
    db = _db()
    gantt = client.get(f"{API}/views/gantt").json()
    review = client.get(f"{API}/views/review").json()
    export = client.get(f"{API}/export").json()
    return (
        db["proj_daily_stats"].count_documents({}),
        db["proj_current"].count_documents({}),
        [(p["id"], p.get("actual")) for p in gantt["projects"]],
        [(t["id"], t.get("actual")) for p in gantt["projects"] for t in p.get("tasks", [])],
        [(p["projectId"], p["actualSecondsThisWeek"]) for p in review["planVsActual"]],
        len(export["events"]),
        sorted(export),
    )


def test_suggestions_invisible_until_confirmed(client, seeded):
    task = seeded["tasks"]["示例任务三"]
    before = _human_views(client)
    _upload(client, [_seg(_recent(6), task_id=task["id"], active=200)])
    assert _db()["events"].count_documents({}) == 0
    assert _human_views(client) == before  # 导出形状也一个键没加

    sug = _pending(client)["items"][0]
    resp = client.post(f"{SUG}/{sug['id']}/confirm", json={})
    assert resp.status_code == 200, resp.text
    after = _human_views(client)
    assert after != before
    rows = list(_db()["proj_daily_stats"].find({}, {"_id": 0}))
    assert [(r["taskId"], r["seconds"]) for r in rows] == [(task["id"], 200)]
    assert dict(after[4])[seeded["projects"]["示例项目三"]["id"]] == 200


# ─────────────────────────────────────────── 确认：一条、补登同形


def test_confirm_writes_backfill_shaped_event(client, seeded):
    task = seeded["tasks"]["示例任务三"]
    other = seeded["tasks"]["示例任务四"]
    project = seeded["projects"]["示例项目三"]
    zone = seeded["zones"]["示例分区二"]
    start = _recent(8)
    _upload(client, [_seg(start, task_id=task["id"], active=150, confidence=0.7)])
    sug = _pending(client)["items"][0]

    # 人改了任务 + 模式
    resp = client.post(f"{SUG}/{sug['id']}/confirm", json={"taskId": other["id"], "mode": "review"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "confirmed" and body["duplicate"] is False
    assert body["event"]["dedupeKey"] == f"activity:{sug['id']}"
    from app import timeutil  # noqa: PLC0415
    from app.config import settings  # noqa: PLC0415

    assert body["date"] == timeutil.local_date(start, settings.tz)

    (event,) = _session_events()
    assert event["source"] == "activity-confirmed"
    assert event["time"] == sug["endAt"]
    assert event["subject"] == {"zone": zone["id"], "project": project["id"], "task": other["id"]}
    assert event["data"] == {"durationSeconds": 150, "startAt": sug["startAt"], "mode": "review"}
    assert event["ai"] == {"generated": True, "confidence": 0.7, "confirmed": True}
    assert event["flags"] == []
    assert _pending(client)["total"] == 0
    assert _pending(client, status="confirmed")["items"][0]["id"] == sug["id"]

    # 与补登同形：同一组键（ai 之外），默认 mode 不写
    client.post(f"{API}/timer/backfill", json={
        "taskId": task["id"], "startAt": (start - timedelta(hours=1)).isoformat(), "durationSeconds": 60})
    backfill = next(e for e in _session_events() if e["source"] == "manual-backfill")
    assert set(backfill) == set(event)
    assert set(backfill["data"]) == {"durationSeconds", "startAt"}


def test_confirm_uses_suggested_task_and_default_mode(client, seeded):
    task = seeded["tasks"]["示例任务三"]
    _upload(client, [_seg(_recent(), task_id=task["id"])])
    sug = _pending(client)["items"][0]
    assert client.post(f"{SUG}/{sug['id']}/confirm").status_code == 200  # 无请求体也行
    (event,) = _session_events()
    assert event["subject"]["task"] == task["id"] and "mode" not in event["data"]


def test_confirm_twice_is_one_event(client, seeded):
    task = seeded["tasks"]["示例任务三"]
    _upload(client, [_seg(_recent(), task_id=task["id"])])
    sug_id = _pending(client)["items"][0]["id"]
    first = client.post(f"{SUG}/{sug_id}/confirm", json={}).json()
    second = client.post(f"{SUG}/{sug_id}/confirm",
                         json={"taskId": seeded["tasks"]["示例任务四"]["id"]})
    assert second.status_code == 200
    assert second.json() == {**first, "duplicate": True}
    assert len(_session_events()) == 1


def test_reupload_after_confirm_stays_confirmed(client, seeded):
    task = seeded["tasks"]["示例任务三"]
    seg = _seg(_recent(), task_id=task["id"])
    _upload(client, [seg])
    sug_id = _pending(client)["items"][0]["id"]
    client.post(f"{SUG}/{sug_id}/confirm", json={})
    assert _upload(client, [seg])["duplicates"] == 1
    assert _pending(client)["total"] == 0
    assert _pending(client, status="confirmed")["total"] == 1


def test_confirm_without_task_is_400(client):
    _upload(client, [_seg(_recent())])
    sug_id = _pending(client)["items"][0]["id"]
    resp = client.post(f"{SUG}/{sug_id}/confirm", json={})
    assert resp.status_code == 400 and "taskId" in resp.json()["detail"]
    assert client.post(f"{SUG}/{sug_id}/confirm", json={"taskId": "t_nope"}).status_code == 404
    assert _pending(client)["items"][0]["status"] == "pending"
    assert _db()["events"].count_documents({}) == 0


def test_confirm_bad_mode_is_422(client, seeded):
    _upload(client, [_seg(_recent(), task_id=seeded["tasks"]["示例任务三"]["id"])])
    sug_id = _pending(client)["items"][0]["id"]
    assert client.post(f"{SUG}/{sug_id}/confirm", json={"mode": "sleep"}).status_code == 422


def test_unknown_id_is_404(client):
    assert client.post(f"{SUG}/sug_nope/confirm", json={}).status_code == 404
    assert client.post(f"{SUG}/sug_nope/dismiss").status_code == 404


def test_confirm_does_not_touch_timer(client, seeded):
    task = seeded["tasks"]["示例任务三"]
    client.post(f"{API}/timer/start", json={"taskId": task["id"]})
    state = _db()["timer_state"].find_one({}, {"_id": 0})
    _upload(client, [_seg(_recent(), task_id=task["id"])])
    client.post(f"{SUG}/{_pending(client)['items'][0]['id']}/confirm", json={})
    assert _db()["timer_state"].find_one({}, {"_id": 0}) == state


# ─────────────────────────────────────────── 忽略 / 冲突


def test_dismiss_and_conflicts(client, seeded):
    task = seeded["tasks"]["示例任务三"]
    _upload(client, [_seg(_recent(10), task_id=task["id"]), _seg(_recent(20), task_id=task["id"])])
    a, b = (i["id"] for i in _pending(client)["items"])

    assert client.post(f"{SUG}/{a}/dismiss").json() == {"id": a, "status": "dismissed"}
    assert client.post(f"{SUG}/{a}/dismiss").status_code == 200  # 幂等
    resp = client.post(f"{SUG}/{a}/confirm", json={})
    assert resp.status_code == 409 and resp.json()["detail"]

    assert client.post(f"{SUG}/{b}/confirm", json={}).status_code == 200
    assert client.post(f"{SUG}/{b}/dismiss").status_code == 409

    assert len(_session_events()) == 1
    assert [i["id"] for i in _pending(client, status="dismissed")["items"]] == [a]


def test_dismiss_racing_a_confirm_loses(client, seeded, monkeypatch):
    """确认占位之后、写事实之前来了一次忽略：忽略必须 409，不能「忽略成功、事实照写」。"""
    from app.modules.activity import service  # noqa: PLC0415

    task = seeded["tasks"]["示例任务三"]
    _upload(client, [_seg(_recent(), task_id=task["id"])])
    sug_id = _pending(client)["items"][0]["id"]
    real = service.timer_service.record_session
    seen = {}

    def racing(*args, **kwargs):
        seen["dismiss"] = client.post(f"{SUG}/{sug_id}/dismiss").status_code
        return real(*args, **kwargs)

    monkeypatch.setattr(service.timer_service, "record_session", racing)
    assert client.post(f"{SUG}/{sug_id}/confirm", json={}).status_code == 200
    assert seen["dismiss"] == 409
    assert _pending(client, status="confirmed")["total"] == 1 and len(_session_events()) == 1


def test_pending_with_existing_fact_confirms_as_duplicate(client, seeded):
    """过期清掉又重传成 pending、事实早已在台账：再确认回原事件并补齐状态，不需要 taskId。"""
    task = seeded["tasks"]["示例任务三"]
    seg = _seg(_recent(), task_id=task["id"])
    _upload(client, [seg])
    sug_id = _pending(client)["items"][0]["id"]
    client.post(f"{SUG}/{sug_id}/confirm", json={})
    col = _db()["activity_suggestions"]
    col.update_one({"id": sug_id}, {"$set": {"status": "pending", "suggestion.taskId": None}})
    body = client.post(f"{SUG}/{sug_id}/confirm", json={}).json()
    assert body["duplicate"] is True
    assert col.find_one({"id": sug_id})["status"] == "confirmed"


# ─────────────────────────────────────────── 租户隔离


def test_tenant_isolation(client):
    _upload(client, [_seg(_recent())], headers=A)
    assert _pending(client, headers=B)["total"] == 0
    sug_id = _pending(client, headers=A)["items"][0]["id"]
    assert client.post(f"{SUG}/{sug_id}/dismiss", headers=B).status_code == 404
    assert client.post(f"{SUG}/{sug_id}/confirm", json={"taskId": "t_x"}, headers=B).status_code == 404
    # 同一设备同一段在另一个租户里是另一条
    assert _upload(client, [_seg(_recent())], headers=B)["accepted"] == 1
    assert _pending(client, headers=A)["items"][0]["status"] == "pending"


# ─────────────────────────────────────────── 过期


@pytest.fixture()
def ttl_days(monkeypatch):
    from app import config  # noqa: PLC0415

    def set_days(days: int):
        monkeypatch.setattr(config, "settings", dataclasses.replace(config.settings, suggestion_ttl_days=days))

    return set_days


def test_ttl_purge_is_lazy(client, seeded, ttl_days):
    task = seeded["tasks"]["示例任务三"]
    _upload(client, [_seg(_recent(10), task_id=task["id"]), _seg(_recent(20)), _seg(_recent(30))])
    ids = [i["id"] for i in _pending(client)["items"]]
    client.post(f"{SUG}/{ids[0]}/confirm", json={})
    client.post(f"{SUG}/{ids[1]}/dismiss")
    old = datetime.now(timezone.utc) - timedelta(days=3)
    col = _db()["activity_suggestions"]
    col.update_many({}, {"$set": {"receivedAt": old, "decidedAt": old}})
    # 另一个租户的旧数据不归这次请求清
    col.insert_one({"user": "ch_other", "id": "sug_x", "dedupeKey": "k", "status": "pending",
                    "receivedAt": old})

    ttl_days(4)
    assert _pending(client)["total"] == 1
    ttl_days(2)
    assert _pending(client)["total"] == 0
    assert col.count_documents({"user": "u_local"}) == 0  # 已确认/已忽略的也清
    assert col.count_documents({"user": "ch_other"}) == 1
    assert len(_session_events()) == 1  # 事实不受影响


def test_purged_then_reuploaded_confirm_still_one_event(client, seeded, ttl_days):
    """过期清掉又被重传：id 由防重键派生，再确认命中同一条事实。"""
    task = seeded["tasks"]["示例任务三"]
    seg = _seg(_recent(), task_id=task["id"])
    _upload(client, [seg])
    sug_id = _pending(client)["items"][0]["id"]
    client.post(f"{SUG}/{sug_id}/confirm", json={})
    _db()["activity_suggestions"].delete_many({})  # 等价于过期清理

    _upload(client, [seg])
    assert _pending(client)["items"][0]["id"] == sug_id
    again = client.post(f"{SUG}/{sug_id}/confirm", json={}).json()
    assert again["duplicate"] is True
    assert len(_session_events()) == 1


def test_ttl_config_rejects_garbage():
    from app.config import ConfigError, load_settings  # noqa: PLC0415

    env = {"NEXUS_MONGO_URI": "mongodb://x", "NEXUS_DB_NAME": "d_test", "NEXUS_TZ": "UTC"}
    assert load_settings(env).suggestion_ttl_days == 14
    assert load_settings({**env, "NEXUS_SUGGESTION_TTL_DAYS": "3"}).suggestion_ttl_days == 3
    with pytest.raises(ConfigError, match="NEXUS_SUGGESTION_TTL_DAYS"):
        load_settings({**env, "NEXUS_SUGGESTION_TTL_DAYS": "0"})
