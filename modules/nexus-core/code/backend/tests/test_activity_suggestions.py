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
                          "suggestion", "idle", "rejectedTaskIds", "status"}  # idle：v2.5；rejectedTaskIds：v2.7
    assert first["idle"] is False and first["rejectedTaskIds"] == []
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
    {"app": ""}, {"title": None},
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


def test_long_app_and_title_truncated_by_code_point(client):
    _upload(client, [_seg(_recent(), app="应" * 130, title="标" * 600)])
    item = _pending(client)["items"][0]
    assert item["app"] == "应" * 128 and item["title"] == "标" * 512


def test_time_rules(client):
    now = _recent(0)
    backwards = _seg(now - timedelta(minutes=5)) | {"endAt": (now - timedelta(minutes=6)).isoformat()}
    future = _seg(now + timedelta(minutes=5))  # endAt 超前 9 分钟：超出 300 秒容差
    skewed = _seg(now - timedelta(minutes=1))  # endAt 超前 3 分钟：设备时钟偏快，照收
    too_long = _seg(now - timedelta(days=2), minutes=60 * 25)
    reason_200_bytes_ok = _seg(now - timedelta(minutes=30))
    reason_200_bytes_ok["suggestion"]["reason"] = "中" * 66 + "ab"  # 200 字节整，放行
    out = _upload(client, [backwards, future, too_long, reason_200_bytes_ok, "not-an-object", skewed])
    assert [r["index"] for r in out["rejected"]] == [0, 1, 2, 4]
    assert out["accepted"] == 2


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


# ─────────────────────────────────────────── v2.7 AI 匹配：助理配任务，人答「是 / 否」

MATCHES = f"{SUG}/matches"
BEARER = {"Authorization": "Bearer " + "x" * 8}  # 形状像设备令牌即可，不是真凭据


def _match(client, matches, headers=None):
    resp = client.post(MATCHES, json={"matches": matches}, headers=headers or {})
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_match_sets_assistant_suggestion_without_confirming(client, seeded):
    task = seeded["tasks"]["示例任务三"]
    _upload(client, [_seg(_recent(10))])
    sug_id = _pending(client)["items"][0]["id"]
    before = _human_views(client)

    out = _match(client, [{"id": sug_id, "taskId": task["id"], "confidence": 0.7, "reason": "标题里有 garden"}])
    assert out == {"matched": 1, "rejected": []}
    item = _pending(client)["items"][0]
    assert item["status"] == "pending"
    assert item["suggestion"] == {"taskId": task["id"], "confidence": 0.7, "reason": "标题里有 garden",
                                  "classifier": "assistant"}
    # 什么都没确认：台账、投影、导出一个字节不动
    assert _session_events() == [] and _human_views(client) == before
    # 助理可以改自己配的（重交幂等；整数 1 也收，reason 可省）
    other = seeded["tasks"]["示例任务四"]
    assert _match(client, [{"id": sug_id, "taskId": other["id"], "confidence": 1}])["matched"] == 1
    assert _pending(client)["items"][0]["suggestion"] == {
        "taskId": other["id"], "confidence": 1.0, "reason": "", "classifier": "assistant"}


def test_match_rejects_per_index_without_failing_batch(client, seeded):
    task = seeded["tasks"]["示例任务三"]
    _upload(client, [_seg(_recent(10)), _seg(_recent(20)), _seg(_recent(30)),
                     _seg(_recent(40), task_id=task["id"])])
    newest, confirmed, dismissed, ruled = (i["id"] for i in _pending(client)["items"])
    client.post(f"{SUG}/{confirmed}/confirm", json={"taskId": task["id"]})
    client.post(f"{SUG}/{dismissed}/dismiss")
    ok = {"taskId": task["id"], "confidence": 0.5, "reason": ""}

    out = _match(client, [
        {"id": newest, **ok},                                    # 0 收
        {"id": "sug_nope", **ok},                                # 1 不存在
        {"id": confirmed, **ok},                                 # 2 已确认
        {"id": dismissed, **ok},                                 # 3 已忽略
        {"id": newest, "taskId": "t_nope", "confidence": 0.5},   # 4 任务不存在
        {"id": newest, **ok, "confidence": 1.5},                 # 5 越界
        {"id": newest, **ok, "confidence": True},                # 6 布尔不是把握
        {"id": newest, **ok, "reason": "字" * 67},               # 7 201 字节
        {"id": newest, "taskId": None, "confidence": 0.5},       # 8 只能配到任务
        {"id": newest, "projectId": "p_x", "confidence": 0.5},   # 9 v2.10 可以只到项目，但项目得存在
        "nope",                                                  # 10 不是对象
        {"id": ruled, **ok},                                     # 11 规则已经配上的不盖
    ])
    assert out["matched"] == 1
    assert [r["index"] for r in out["rejected"]] == list(range(1, 12))
    assert all(r["reason"] for r in out["rejected"])
    # 被拒的没动任何东西
    assert _pending(client, status="confirmed")["items"][0]["suggestion"]["classifier"] == "rules"
    assert _pending(client, status="dismissed")["items"][0]["suggestion"]["taskId"] is None
    by_id = {i["id"]: i for i in _pending(client)["items"]}
    assert by_id[ruled]["suggestion"]["classifier"] == "rules"
    assert by_id[newest]["suggestion"]["taskId"] == task["id"]
    assert len(_session_events()) == 1  # 只有上面人确认的那一条


@pytest.mark.parametrize("body", [{}, {"matches": "x"}, {"matches": [{}] * 201}, []])
def test_match_batch_shape_is_422(client, body):
    assert client.post(MATCHES, json=body).status_code == 422


def test_upload_cannot_claim_assistant(client):
    seg = _seg(_recent())
    seg["suggestion"]["classifier"] = "assistant"
    out = _upload(client, [seg])
    assert out["accepted"] == 0 and out["rejected"][0]["index"] == 0


def test_device_token_cannot_match_or_unmatch(client, seeded):
    task = seeded["tasks"]["示例任务三"]
    _upload(client, [_seg(_recent(), task_id=task["id"])])
    sug_id = _pending(client)["items"][0]["id"]
    body = {"matches": [{"id": sug_id, "taskId": task["id"], "confidence": 0.5}]}
    assert client.post(MATCHES, json=body, headers=BEARER).status_code == 403
    assert client.post(MATCHES, json=body, headers={"Authorization": "  bearer x"}).status_code == 403
    assert client.post(f"{SUG}/{sug_id}/unmatch", headers=BEARER).status_code == 403
    # 403 先于请求体校验：带 Bearer 的坏请求体也是 403，不是 422
    assert client.post(MATCHES, json={}, headers=BEARER).status_code == 403
    assert client.post(MATCHES, content=b"{not json", headers=BEARER).status_code == 403
    assert client.post(f"{SUG}/{sug_id}/unmatch", json={"taskId": 1}, headers=BEARER).status_code == 403
    # 不带 Bearer 的坏请求体照旧 422
    assert client.post(MATCHES, content=b"{not json").status_code == 422
    assert client.post(f"{SUG}/{sug_id}/unmatch", json={"taskId": 1}).status_code == 422
    assert _pending(client)["items"][0]["suggestion"]["classifier"] == "rules"  # 403 在任何写入之前


def test_unmatch_clears_and_remembers_rejected_task(client, seeded):
    task, other = seeded["tasks"]["示例任务三"], seeded["tasks"]["示例任务四"]
    _upload(client, [_seg(_recent())])
    sug_id = _pending(client)["items"][0]["id"]
    _match(client, [{"id": sug_id, "taskId": task["id"], "confidence": 0.7, "reason": "猜的"}])

    # 页面上看到的任务与当前的不一样（助理刚换过）→ 409，什么都不动
    assert client.post(f"{SUG}/{sug_id}/unmatch", json={"taskId": other["id"]}).status_code == 409
    resp = client.post(f"{SUG}/{sug_id}/unmatch", json={"taskId": task["id"]})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"id": sug_id, "status": "pending", "rejectedTaskIds": [task["id"]]}
    item = _pending(client)["items"][0]
    assert item["suggestion"] == {"taskId": None, "confidence": 0.0, "reason": "", "classifier": "assistant"}
    assert item["rejectedTaskIds"] == [task["id"]]
    # 再否一次（没有请求体）：幂等
    assert client.post(f"{SUG}/{sug_id}/unmatch").json()["rejectedTaskIds"] == [task["id"]]

    # 助理不许再配同一个；换一个可以
    out = _match(client, [{"id": sug_id, "taskId": task["id"], "confidence": 0.9},
                          {"id": sug_id, "taskId": other["id"], "confidence": 0.6}])
    assert out["matched"] == 1 and [r["index"] for r in out["rejected"]] == [0]
    assert _pending(client)["items"][0]["suggestion"]["taskId"] == other["id"]
    # 人自己挑回被否过的任务确认：照常可以（否的是助理的猜测，不是人的选择）
    assert client.post(f"{SUG}/{sug_id}/confirm", json={"taskId": task["id"]}).status_code == 200
    assert client.post(f"{SUG}/{sug_id}/unmatch").status_code == 409  # 已确认的不能再否
    assert client.post(f"{SUG}/sug_nope/unmatch").status_code == 404


def test_confirm_after_match_writes_fact_with_assistant_confidence(client, seeded):
    task = seeded["tasks"]["示例任务三"]
    _upload(client, [_seg(_recent(), active=120)])
    sug = _pending(client)["items"][0]
    _match(client, [{"id": sug["id"], "taskId": task["id"], "confidence": 0.65, "reason": "r"}])

    resp = client.post(f"{SUG}/{sug['id']}/confirm", json={"taskId": task["id"]})  # 人说「是」
    assert resp.status_code == 200, resp.text
    (event,) = _session_events()
    assert event["source"] == "activity-confirmed" and event["subject"]["task"] == task["id"]
    assert event["data"] == {"durationSeconds": 120, "startAt": sug["startAt"]}
    assert event["ai"] == {"generated": True, "confidence": 0.65, "confirmed": True}


def test_match_tenant_isolation(client):
    _upload(client, [_seg(_recent())], headers=A)
    sug_id = _pending(client, headers=A)["items"][0]["id"]
    task = client.post(f"{API}/planner/zones", json={"name": "z"}, headers=A)
    assert task.status_code in (200, 201), task.text
    proj = client.post(f"{API}/planner/projects", json={"zoneId": task.json()["id"], "name": "p"}, headers=A).json()
    t_a = client.post(f"{API}/planner/tasks", json={"projectId": proj["id"], "name": "t"}, headers=A).json()["id"]
    one = [{"id": sug_id, "taskId": t_a, "confidence": 0.5}]
    # B 看不见 A 的建议，也看不见 A 的任务
    assert _match(client, one, headers=B) == {"matched": 0, "rejected": [
        {"index": 0, "reason": f"活动建议不存在：{sug_id!r}"}]}
    assert client.post(f"{SUG}/{sug_id}/unmatch", headers=B).status_code == 404
    assert _pending(client, headers=A)["items"][0]["suggestion"]["taskId"] is None
    assert _match(client, one, headers=A)["matched"] == 1


def test_bodyless_confirm_racing_an_unmatch_does_not_record_rejected_task(client, seeded, monkeypatch):
    """不带 taskId 的确认读到任务 A 之后、占位之前，人把 A 否掉了：确认必须 409，不能把时间记到 A。"""
    from app.modules.activity import repo, service  # noqa: PLC0415

    task = seeded["tasks"]["示例任务三"]
    _upload(client, [_seg(_recent())])
    sug_id = _pending(client)["items"][0]["id"]
    _match(client, [{"id": sug_id, "taskId": task["id"], "confidence": 0.7}])
    real, seen = repo.claim, {}

    def racing(*args, **kwargs):
        if "unmatch" not in seen:
            seen["unmatch"] = client.post(f"{SUG}/{sug_id}/unmatch").status_code
        return real(*args, **kwargs)

    monkeypatch.setattr(service.repo, "claim", racing)
    assert client.post(f"{SUG}/{sug_id}/confirm", json={}).status_code == 409
    assert seen["unmatch"] == 200 and _session_events() == []
    item = _pending(client)["items"][0]
    assert item["suggestion"]["taskId"] is None and item["rejectedTaskIds"] == [task["id"]]


def test_bodyless_confirm_never_backfills_a_superseded_task(client, seeded, monkeypatch):
    """确认甲读到任务 A → 人否掉 A、助理换成 B → 确认乙已占位但事实还没写：甲补写只能写 B，不能写 A。"""
    from app.modules.activity import repo, service  # noqa: PLC0415

    old, new = seeded["tasks"]["示例任务三"], seeded["tasks"]["示例任务四"]
    _upload(client, [_seg(_recent())])
    sug_id = _pending(client)["items"][0]["id"]
    _match(client, [{"id": sug_id, "taskId": old["id"], "confidence": 0.7}])
    real, seen = repo.claim, {}

    def racing(*args, **kwargs):
        if not seen:
            seen["unmatch"] = client.post(f"{SUG}/{sug_id}/unmatch").status_code
            seen["match"] = _match(client, [{"id": sug_id, "taskId": new["id"], "confidence": 0.4}])["matched"]
            real(args[0], sug_id, args[2])  # 确认乙：占了位，还没写事实
        return real(*args, **kwargs)

    monkeypatch.setattr(service.repo, "claim", racing)
    assert client.post(f"{SUG}/{sug_id}/confirm", json={}).status_code == 200
    assert seen == {"unmatch": 200, "match": 1}
    (event,) = _session_events()
    assert event["subject"]["task"] == new["id"] and event["ai"]["confidence"] == 0.4


def test_confirm_uses_confidence_as_of_the_claim(client, seeded, monkeypatch):
    """确认读到把握 0.9 之后、占位之前，助理把同一个任务重配成 0.2：事实里记的是 0.2。"""
    from app.modules.activity import repo, service  # noqa: PLC0415

    task = seeded["tasks"]["示例任务三"]
    _upload(client, [_seg(_recent())])
    sug_id = _pending(client)["items"][0]["id"]
    _match(client, [{"id": sug_id, "taskId": task["id"], "confidence": 0.9}])
    real, seen = repo.claim, {}

    def racing(*args, **kwargs):
        if not seen:
            seen["match"] = _match(client, [{"id": sug_id, "taskId": task["id"], "confidence": 0.2}])["matched"]
        return real(*args, **kwargs)

    monkeypatch.setattr(service.repo, "claim", racing)
    assert client.post(f"{SUG}/{sug_id}/confirm", json={}).status_code == 200
    (event,) = _session_events()
    assert seen == {"match": 1} and event["ai"]["confidence"] == 0.2


def test_failed_confirm_does_not_reopen_a_record_another_confirm_holds(client, seeded, monkeypatch):
    """确认甲（任务不存在）占位 → 确认乙加入 → 甲写失败：不许把状态放回 pending（乙还占着）。反过来也一样。"""
    from app.modules.activity import service  # noqa: PLC0415

    task = seeded["tasks"]["示例任务三"]
    _upload(client, [_seg(_recent())])
    sug_id = _pending(client)["items"][0]["id"]
    _match(client, [{"id": sug_id, "taskId": task["id"], "confidence": 0.7}])
    real, seen = service.timer_service.record_session, {}

    def racing(task_id, *args, **kwargs):
        if task_id == "t_nope" and not seen:
            seen["b"] = "started"
            seen["b"] = client.post(f"{SUG}/{sug_id}/confirm", json={}).status_code   # 乙：接手并写成
        return real(task_id, *args, **kwargs)

    monkeypatch.setattr(service.timer_service, "record_session", racing)
    assert client.post(f"{SUG}/{sug_id}/confirm", json={"taskId": "t_nope"}).status_code == 404
    assert seen == {"b": 200}
    assert _pending(client)["total"] == 0 and _pending(client, status="confirmed")["total"] == 1
    (event,) = _session_events()
    assert event["subject"]["task"] == task["id"]
    # 没人接手时照旧放回 pending
    _upload(client, [_seg(_recent(30))])
    other = _pending(client)["items"][0]["id"]
    monkeypatch.setattr(service.timer_service, "record_session", real)
    assert client.post(f"{SUG}/{other}/confirm", json={"taskId": "t_nope"}).status_code == 404
    assert [i["id"] for i in _pending(client)["items"]] == [other]
    # 反过来：甲（任务对）占位、正要写 → 乙（任务不存在）加入、失败 → 不放回；甲写成后状态是已确认
    _upload(client, [_seg(_recent(50))])
    third = next(i["id"] for i in _pending(client)["items"] if i["id"] != other)
    seen.clear()

    def racing2(task_id, *args, **kwargs):
        if task_id == task["id"] and not seen:
            seen["b"] = "started"
            seen["b"] = client.post(f"{SUG}/{third}/confirm", json={"taskId": "t_nope"}).status_code
            assert [i["id"] for i in _pending(client)["items"]] == [other]   # 乙失败后 third 没回到待确认
        return real(task_id, *args, **kwargs)

    monkeypatch.setattr(service.timer_service, "record_session", racing2)
    assert client.post(f"{SUG}/{third}/confirm", json={"taskId": task["id"]}).status_code == 200
    assert seen == {"b": 404}
    assert {i["id"] for i in _pending(client, status="confirmed")["items"]} == {sug_id, third}
    assert len(_session_events()) == 2


# ------------------------------------------------ v2.10 集合与只到项目的建议（契约「AI 分集合」）


def test_match_labels_collection_and_project_without_touching_the_task(client, seeded):
    """只贴标签：不带 taskId / newTask / confidence。规则给的任务、把握、来源一个不动；什么都不确认。"""
    task = seeded["tasks"]["示例任务三"]
    pid = task["projectId"]
    _upload(client, [_seg(_recent(10)), _seg(_recent(20), task_id=task["id"])])
    blank, ruled = (i["id"] for i in _pending(client)["items"])
    before = _human_views(client)

    out = _match(client, [
        {"id": blank, "collection": {"name": "  Claude  Code · cockpit "}, "projectId": pid},
        {"id": ruled, "collection": {"name": "claude code · COCKPIT"}},   # 规则配上的也能贴；写法不同 = 同一个集合
    ])
    assert out == {"matched": 2, "rejected": []}
    by_id = {i["id"]: i for i in _pending(client)["items"]}
    assert by_id[blank]["suggestion"] == {
        "taskId": None, "confidence": 0.9, "reason": "规则 #1 命中", "classifier": "rules",   # 上传时的原样
        "collection": {"key": "claude code · cockpit", "name": "Claude  Code · cockpit"}, "projectId": pid}
    rs = by_id[ruled]["suggestion"]
    assert (rs["taskId"], rs["classifier"], rs["confidence"]) == (task["id"], "rules", 0.9)
    assert rs["collection"]["key"] == "claude code · cockpit" and "projectId" not in rs
    assert _session_events() == [] and _human_views(client) == before
    assert all(i["status"] == "pending" for i in by_id.values())


def test_match_with_task_carries_labels_and_no_keeps_them(client, seeded):
    task, other = seeded["tasks"]["示例任务三"], seeded["tasks"]["示例任务四"]
    _upload(client, [_seg(_recent(10))])
    sug_id = _pending(client)["items"][0]["id"]
    coll = {"key": "终端", "name": "终端"}

    assert _match(client, [{"id": sug_id, "taskId": task["id"], "confidence": 0.7, "collection": {"name": "终端"},
                            "projectId": task["projectId"]}])["matched"] == 1
    assert _pending(client)["items"][0]["suggestion"] == {
        "taskId": task["id"], "confidence": 0.7, "reason": "", "classifier": "assistant",
        "collection": coll, "projectId": task["projectId"]}
    # 助理换任务、这次没带集合：集合标签留着
    assert _match(client, [{"id": sug_id, "taskId": other["id"], "confidence": 0.6}])["matched"] == 1
    assert _pending(client)["items"][0]["suggestion"]["collection"] == coll
    # 人说「否」：任务清掉，集合标签还在
    assert client.post(f"{SUG}/{sug_id}/unmatch", json={"taskId": other["id"]}).status_code == 200
    sug = _pending(client)["items"][0]["suggestion"]
    assert sug["taskId"] is None and sug["collection"] == coll
    # 提议新任务同样能带集合；否掉提议后也还在
    nt = {"projectId": task["projectId"], "name": "v2.10 新任务"}
    assert _match(client, [{"id": sug_id, "newTask": nt, "confidence": 0.5, "collection": {"name": "终端"}}])["matched"] == 1
    assert client.post(f"{SUG}/{sug_id}/unmatch").status_code == 200
    sug = _pending(client)["items"][0]["suggestion"]
    assert "newTask" not in sug and sug["collection"] == coll
    # 确认照旧：带任务确认，写一条事实
    assert client.post(f"{SUG}/{sug_id}/confirm", json={"taskId": task["id"]}).status_code == 200
    assert len(_session_events()) == 1


def test_match_label_validation_rejects_per_index(client, seeded):
    task, other = seeded["tasks"]["示例任务三"], seeded["tasks"]["示例临时任务"]
    assert task["projectId"] != other["projectId"]
    _upload(client, [_seg(_recent(10)), _seg(_recent(20))])
    newest, dismissed = (i["id"] for i in _pending(client)["items"])
    client.post(f"{SUG}/{dismissed}/dismiss")
    out = _match(client, [
        {"id": newest, "collection": {"name": "长" * 64}},                                  # 0 收（64 个字刚好）
        {"id": newest, "collection": {"name": "   "}},                                      # 1 空名
        {"id": newest, "collection": {"name": "长" * 65}},                                  # 2 超 64 字
        {"id": newest, "collection": {}},                                                   # 3 缺 name
        {"id": newest, "collection": "终端"},                                               # 4 不是对象
        {"id": newest, "projectId": "p_nope"},                                              # 5 项目不存在
        {"id": newest, "projectId": 7},                                                     # 6 类型不对
        {"id": newest},                                                                     # 7 什么都没给
        {"id": newest, "taskId": task["id"], "collection": {"name": "x"}},                  # 8 配任务必须给 confidence
        {"id": newest, "taskId": task["id"], "confidence": 0.5, "projectId": other["projectId"]},  # 9 项目对不上任务
        {"id": newest, "newTask": {"projectId": task["projectId"], "name": "n"}, "confidence": 0.5,
         "projectId": other["projectId"]},                                                  # 10 项目对不上新任务
        {"id": dismissed, "collection": {"name": "x"}},                                     # 11 已忽略
        {"id": "sug_nope", "collection": {"name": "x"}},                                    # 12 不存在
    ])
    assert out["matched"] == 1 and [r["index"] for r in out["rejected"]] == list(range(1, 13))
    assert all(r["reason"] for r in out["rejected"])
    sug = _pending(client)["items"][0]["suggestion"]
    assert sug["collection"]["name"] == "长" * 64 and sug["taskId"] is None and "projectId" not in sug
    assert "collection" not in _pending(client, status="dismissed")["items"][0]["suggestion"]


def test_labels_device_token_403_upload_cannot_label_and_tenants_isolated(client):
    _upload(client, [{**_seg(_recent(10)), "suggestion": {
        "taskId": None, "confidence": 0, "reason": "", "classifier": "rules",
        "collection": {"key": "k", "name": "检测程序自称的"}, "projectId": "p_x"}}])
    item = _pending(client)["items"][0]
    assert "collection" not in item["suggestion"] and "projectId" not in item["suggestion"]
    label = {"matches": [{"id": item["id"], "collection": {"name": "x"}}]}
    assert client.post(MATCHES, json=label, headers=BEARER).status_code == 403
    other = client.post(MATCHES, json=label, headers=B).json()
    assert other["matched"] == 0 and len(other["rejected"]) == 1
    assert "collection" not in _pending(client)["items"][0]["suggestion"]


def test_label_only_suggestion_confirms_with_project_into_the_bucket(client, seeded):
    """v2.10 接 v2.9：助理只贴了集合 + 项目（没有任务）的建议，人按 {projectId} 确认 → 记到该项目的「未分类」桶。"""
    pid = seeded["tasks"]["示例任务三"]["projectId"]
    _upload(client, [_seg(_recent(10))])
    sug_id = _pending(client)["items"][0]["id"]
    assert _match(client, [{"id": sug_id, "collection": {"name": "终端"}, "projectId": pid}])["matched"] == 1
    assert _pending(client)["items"][0]["suggestion"]["taskId"] is None

    out = client.post(f"{SUG}/{sug_id}/confirm", json={"projectId": pid})
    assert out.status_code == 200, out.text
    assert out.json()["taskId"] == f"t_unc_{pid}"
    events = _session_events()
    assert len(events) == 1 and events[0]["subject"]["task"] == f"t_unc_{pid}"
    assert _pending(client)["items"] == []
