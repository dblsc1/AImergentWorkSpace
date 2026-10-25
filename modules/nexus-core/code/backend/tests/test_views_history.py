"""``GET /api/core/views/history``（契约 v2.39 二）：人和 agent 分开给、永不相加。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from _collab_common import D, HIST, API, backfill, get, iso, run, t, world
from _reconcile_common import assign, rnd, tiers
from _tier_common import day, rec, upload
from app.modules.views import collab_history
from app.repo import get_db


def span(**kw):
    return {"from": D, "to": D, **kw}


# ------------------------------------------------------------------ 形状 / who / 422


def test_empty_defaults_to_today_both(client):
    body = get(client, HIST)
    today = day(0)
    assert body["range"]["from"] == body["range"]["to"] == body["range"]["today"] == today
    assert body["range"]["tz"][0] in "+-" and len(body["range"]["tz"]) == 6
    assert body["human"]["confirmedSeconds"] == 0 and body["human"]["pendingSeconds"] == 0
    assert [d["date"] for d in body["human"]["byDay"]] == [today]
    assert body["agents"] == {"workingSeconds": 0, "byDay": [{"date": today, "workingSeconds": 0}], "byAgent": []}
    assert "handoffs" not in body and "overlap" not in body and body["truncated"] is False
    assert "segments" not in body["human"] and "blocks" not in body["human"] and "tiersOff" not in body


def test_who_filters(client):
    w = world(client)
    backfill(client, w["a"]["id"], "09:00:00", 600)
    run(client, 1, "09:00:00", "09:10:00")
    both = get(client, HIST, **span(detail="segments"))
    assert set(both) == {"range", "human", "agents", "handoffs", "overlap", "truncated"}
    human = get(client, HIST, **span(who="human", detail="segments"))
    assert set(human) == {"range", "human", "truncated"} and "runs" not in human["human"]
    agent = get(client, HIST, **span(who="agent", detail="segments"))
    assert set(agent) == {"range", "agents", "truncated"} and len(agent["agents"]["runs"]) == 1
    # summary / windows 下 both 也没有 handoffs / overlap
    assert "handoffs" not in get(client, HIST, **span(detail="summary")) and "overlap" not in get(client, HIST, **span(detail="windows"))


def test_human_and_agent_never_added(client):
    w = world(client)
    backfill(client, w["a"]["id"], "09:00:00", 3600)
    run(client, 1, "09:00:00", "10:00:00")
    body = get(client, HIST, **span())
    assert body["human"]["confirmedSeconds"] == 3600 and body["agents"]["workingSeconds"] == 3600
    assert "totalSeconds" not in body


@pytest.mark.parametrize("params,word", [
    ({"from": "2026-13-01"}, "from"), ({"to": "2026-02-30"}, "to"), ({"from": "yesterday"}, "from"), ({"to": "20261024"}, "to"),
    ({"who": "robot"}, "who"), ({"detail": "everything"}, "detail"), ({"from": "2026-10-02", "to": "2026-10-01"}, "from"),
    ({"from": "2026-10-01", "to": "2026-10-08", "detail": "segments"}, "detail"),
    ({"from": "2026-10-01", "to": "2026-10-08", "detail": "windows"}, "detail"),
    ({"from": "2026-01-01", "to": "2026-04-03"}, "detail"),  # 93 天
])
def test_422(client, params, word):
    r = client.get(HIST, params=params)
    assert r.status_code == 422 and word in r.json()["detail"], r.text


def test_span_limits_are_inclusive(client):
    assert client.get(HIST, params={"from": "2026-01-01", "to": "2026-04-02"}).status_code == 200  # 92 天
    assert client.get(HIST, params={"from": "2026-01-01", "to": "2026-01-07", "detail": "segments"}).status_code == 200
    assert client.get(HIST, params={"from": "2026-01-01", "to": "2026-01-07", "detail": "windows"}).status_code == 200


# ------------------------------------------------------------------ 已入账


def test_confirmed_by_day_and_target(client):
    w = world(client)
    backfill(client, w["a"]["id"], "09:00:00", 3600)
    backfill(client, w["a"]["id"], "11:00:00", 600)
    backfill(client, w["b"]["id"], "13:00:00", 1800)
    prev = (datetime.fromisoformat(D) - timedelta(days=1)).date().isoformat()
    backfill(client, w["c"]["id"], "10:00:00", 900, date=prev)
    body = get(client, HIST, **{"from": prev, "to": D})
    h = body["human"]
    assert h["confirmedSeconds"] == 3600 + 600 + 1800 + 900 and h["pendingSeconds"] == 0
    assert [d["date"] for d in h["byDay"]] == [prev, D]
    assert h["byDay"][0]["seconds"] == 900
    day2 = h["byDay"][1]
    assert day2["seconds"] == 6000
    assert [(x["taskId"], x["address"], x["seconds"]) for x in day2["byTarget"]] == [
        (w["a"]["id"], "jy-jgsc-kcdm", 4200), (w["b"]["id"], "jy-jgsc-xbg", 1800)]
    assert day2["byTarget"][0]["projectId"] == w["p1"]["id"]


def test_reassigned_follows_current_owner(client):
    w = world(client)
    bucket = f"t_unc_{w['p1']['id']}"
    assert client.post(f"{API}/planner/projects/{w['p1']['id']}/unclassified").status_code in (200, 201)
    out = backfill(client, bucket, "09:00:00", 600)
    h = get(client, HIST, **span(detail="segments"))["human"]
    assert h["byDay"][0]["byTarget"][0]["address"] == "jy-jgsc-wfl" and h["segments"][0]["address"] == "jy-jgsc-wfl"
    r = client.post(f"{API}/sessions/{out['event']['id']}/reassign", json={"taskId": w["a"]["id"]})
    assert r.status_code == 200, r.text
    h = get(client, HIST, **span(detail="segments"))["human"]
    assert h["confirmedSeconds"] == 600
    assert [(x["taskId"], x["address"]) for x in h["byDay"][0]["byTarget"]] == [(w["a"]["id"], "jy-jgsc-kcdm")]
    assert h["segments"][0]["taskId"] == w["a"]["id"] and h["segments"][0]["eventId"] == out["event"]["id"]


def test_confirmed_segments_fields(client):
    w = world(client)
    out = backfill(client, w["a"]["id"], "09:00:00", 600)
    segs = get(client, HIST, **span(detail="segments"))["human"]["segments"]
    assert len(segs) == 1
    s = segs[0]
    assert s["status"] == "confirmed" and s["seconds"] == 600 and s["window"] is None and s["onAgentRun"] is None
    assert s["from"] == iso("09:00:00") or datetime.fromisoformat(s["from"]) == t("09:00:00")
    assert datetime.fromisoformat(s["to"]) == t("09:10:00")
    assert s["eventId"] == out["event"]["id"]
    assert set(s) == {"from", "to", "seconds", "status", "eventId", "taskId", "projectId", "address", "window", "onAgentRun"}


def test_scope_filters_confirmed_and_nulls_pending(client):
    w = world(client)
    backfill(client, w["a"]["id"], "09:00:00", 600)
    backfill(client, w["c"]["id"], "10:00:00", 300)
    for scope, want in ((w["a"]["id"], 600), (w["p1"]["id"], 600), (w["p2"]["id"], 300), (w["z"]["id"], 900)):
        h = get(client, HIST, **span(scope=scope, detail="segments"))["human"]
        assert h["confirmedSeconds"] == want and h["pendingSeconds"] is None
        assert all(d["pendingSeconds"] is None for d in h["byDay"])
        assert sum(s["seconds"] for s in h["segments"]) == want and all(s["status"] == "confirmed" for s in h["segments"])


def test_pending_suggestions_when_tiers_off(client):
    from datetime import timezone  # noqa: PLC0415

    now = datetime.now(timezone.utc)
    for i, (start, secs, app, title) in enumerate([("09:00:00", 600, "code", "a.py"), ("09:30:00", 300, "chrome", "line1\n  line2")]):
        get_db().activity_suggestions.insert_one({
            "user": "u_local", "id": f"s{i}", "dedupeKey": f"k{i}", "startTs": t(start), "durationSeconds": secs, "app": app,
            "title": title, "status": "pending", "receivedAt": now})
    get_db().activity_suggestions.insert_one({"user": "u_local", "id": "s9", "dedupeKey": "k9", "startTs": t("12:00:00"),
                                              "durationSeconds": 999, "app": "x", "title": "", "status": "dismissed"})
    h = get(client, HIST, **span(detail="segments"))["human"]
    assert h["pendingSeconds"] == 900 and h["byDay"][0]["pendingSeconds"] == 900
    segs = h["segments"]
    assert [(s["status"], s["seconds"], s["window"]) for s in segs] == [("pending", 600, "code · a.py"), ("pending", 300, "chrome · line1 line2")]
    assert all(s["eventId"] is None and s["taskId"] is None and s["projectId"] is None and s["address"] is None for s in segs)


def test_window_text_is_one_line_and_capped(client):
    long = "x" * 200
    get_db().activity_suggestions.insert_one({"user": "u_local", "id": "s", "dedupeKey": "k", "startTs": t("09:00:00"),
                                              "durationSeconds": 60, "app": "app", "title": f"a\tb\n{long}​", "status": "pending"})
    w = get(client, HIST, **span(detail="segments"))["human"]["segments"][0]["window"]
    assert len(w) == 80 and "\n" not in w and w.startswith("app · a b xxx")


# ------------------------------------------------------------------ tiers 开：待分类块、窗口块


def test_pending_blocks_when_tiers_on(client):
    w = world(client)
    tiers(client)
    upload(client, [
        rec(D, "09:00:00", "09:10:00", title="doc"),
        rec(D, "09:12:00", "09:15:00", title="doc"),   # 间隔 120 秒 → 同一段
        rec(D, "10:00:00", "10:05:00", title="doc"),   # 间隔 > 300 秒 → 新一段
        rec(D, "11:00:00", "11:02:00", title="other"),
    ])
    h = get(client, HIST, **span(detail="segments"))["human"]
    assert h["pendingSeconds"] == 600 + 180 + 300 + 120
    segs = [s for s in h["segments"] if s["status"] == "pending"]
    assert [(s["seconds"], s["window"]) for s in segs] == [
        (600 + 180, "chrome · doc"), (300, "chrome · doc"), (120, "chrome · other")]
    assert datetime.fromisoformat(segs[0]["from"]) == t("09:00:00") and datetime.fromisoformat(segs[0]["to"]) == t("09:15:00")
    # 分类之后：不再是 pending；入账的段出现在 confirmed 里，窗口块名跟着
    assign(client, D, "chrome · doc", w["a"]["id"])
    rnd()
    h = get(client, HIST, **span(detail="segments"))["human"]
    assert h["pendingSeconds"] == 120
    assert h["confirmedSeconds"] == 600 + 180 + 300
    conf = [s for s in h["segments"] if s["status"] == "confirmed"]
    assert conf and all(s["window"] == "chrome · doc" and s["address"] == "jy-jgsc-kcdm" for s in conf)
    # 同一时间的 pending 与 confirmed 不互相扣
    assert sum(s["seconds"] for s in h["segments"] if s["status"] == "pending") == 120


def test_pending_split_uncaught_vs_unassigned(client):
    tiers(client)
    upload(client, [rec(D, "09:00:00", "09:10:00", title="doc"),
                    rec(D, "10:00:00", "10:03:00", title="x", block="香草")])   # 带 block = 第一级收了、第二级没定
    for detail in ("summary", "segments"):
        h = get(client, HIST, **span(detail=detail))["human"]
        assert (h["pendingUncaughtSeconds"], h["pendingUnassignedSeconds"], h["pendingSeconds"]) == (600, 180, 780)


def test_pending_union_across_devices(client):
    tiers(client)
    upload(client, [rec(D, "09:00:00", "09:10:00", title="doc")], device="dev_aaaa")
    upload(client, [rec(D, "09:05:00", "09:15:00", title="doc")], device="dev_bbbb")
    h = get(client, HIST, **span(detail="segments"))["human"]
    assert h["pendingSeconds"] == 900 and [s["seconds"] for s in h["segments"]] == [900]


def test_windows_tiers_off(client):
    body = get(client, HIST, **span(detail="windows"))
    assert body["tiersOff"] is True and body["human"]["blocks"] == []


def test_windows_blocks(client):
    w = world(client)
    tiers(client)
    upload(client, [rec(D, "09:00:00", "09:10:00", title="doc"), rec(D, "10:00:00", "10:01:00", title="b\nb")])
    assign(client, D, "chrome · doc", w["b"]["id"])
    body = get(client, HIST, **span(detail="windows"))
    assert "tiersOff" not in body
    blocks = body["human"]["blocks"]
    assert [b["blockKey"] for b in blocks] == ["chrome · doc", "chrome · b b"]
    first = blocks[0]
    assert first["date"] == D and first["status"] == "assigned" and first["address"] == "jy-jgsc-xbg" and first["seconds"] == 600
    assert first["parts"][0]["address"] == "jy-jgsc-xbg"
    assert first["records"] == [{"from": first["records"][0]["from"], "to": first["records"][0]["to"], "seconds": 600,
                                 "app": "chrome", "title": "doc"}]
    assert blocks[1]["status"] == "pending" and blocks[1]["address"] is None
    assert blocks[1]["records"][0]["title"] == "b b"
    assert "recordsTruncated" not in first
    # scope 只留已分类到那里的块
    only = get(client, HIST, **span(detail="windows", scope=w["p1"]["id"]))["human"]["blocks"]
    assert [b["blockKey"] for b in only] == ["chrome · doc"]


def test_windows_records_top_50_ascending(client):
    tiers(client)
    recs = [rec(D, f"09:{i // 60:02d}:{i % 60:02d}", f"09:{(i + 1 + (i % 7)) // 60:02d}:{(i + 1 + (i % 7)) % 60:02d}", title="doc")
            for i in range(0, 60, 1)]
    # 60 条互不相同的记录（时长 1..7 秒），只留时长最长的 50 条，按时间升序
    upload(client, recs)
    b = get(client, HIST, **span(detail="windows"))["human"]["blocks"][0]
    assert b["recordsTruncated"] is True and len(b["records"]) == 50
    starts = [r["from"] for r in b["records"]]
    assert starts == sorted(starts)
    assert min(r["seconds"] for r in b["records"]) >= 2


# ------------------------------------------------------------------ agent


def test_agents_summary_and_clamp(client):
    prev = (datetime.fromisoformat(D) - timedelta(days=1)).date().isoformat()
    run(client, 1, "09:00:00", "10:00:00", agent="claude-code", label="L1")
    run(client, 2, "09:30:00", "09:40:00", agent="codex", label="L2")
    run(client, 3, "23:30:00", "00:30:00", agent="claude-code", label="L1", start_date=prev, end_date=D)  # 跨零点
    run(client, 4, "09:00:00", "10:00:00", phases=[("09:00:00", "idle")], agent="idle", label="L9")      # 全程 idle
    a = get(client, HIST, **span())["agents"]
    assert a["workingSeconds"] == 3600 + 600 + 1800
    assert a["byDay"] == [{"date": D, "workingSeconds": 6000}]
    assert [(x["agent"], x["label"], x["workingSeconds"], x["runs"]) for x in a["byAgent"]] == [
        ("claude-code", "L1", 5400, 2), ("codex", "L2", 600, 1), ("idle", "L9", 0, 1)]
    wide = get(client, HIST, **{"from": prev, "to": D})["agents"]
    assert [d["workingSeconds"] for d in wide["byDay"]] == [1800, 6000]


def test_other_days_runs_excluded(client):
    run(client, 1, "09:00:00", "10:00:00")
    other = (datetime.fromisoformat(D) - timedelta(days=3)).date().isoformat()
    a = get(client, HIST, **{"from": other, "to": other})["agents"]
    assert a["workingSeconds"] == 0 and a["byAgent"] == []


def _scenario(client):
    # R1：09:00 干活 → 09:20 等人 → 09:30 人回复后干活 → 09:50 等人（人 09:55 来看）→ 10:00 结束
    run(client, 1, "09:00:00", "10:00:00", label="L1", phases=[
        ("09:00:00", "working"), ("09:20:00", "waiting_input"), ("09:30:00", "working"), ("09:50:00", "waiting_input")],
        interactions=[("reply", "09:30:00"), ("attend", "09:35:00", "09:40:00"), ("attend", "09:55:00", "09:58:00")])
    # R2：09:00 干活 → 09:10 等人，到 09:40 结束都没人理
    run(client, 2, "09:00:00", "09:40:00", agent="codex", label="L2", phases=[("09:00:00", "working"), ("09:10:00", "waiting_input")],
        interactions=[("attend", "09:36:00", "09:39:00")])


def test_runs_phases_started_by(client):
    _scenario(client)
    runs = get(client, HIST, **span(detail="segments"))["agents"]["runs"]
    assert [r["runId"] for r in runs] == ["cv-1", "cv-2"]
    r1 = runs[0]
    assert [(p["phase"], p.get("startedBy")) for p in r1["phases"]] == [
        ("working", None), ("waiting_input", None), ("working", "human_reply"), ("waiting_input", None)]
    assert datetime.fromisoformat(r1["phases"][1]["from"]) == t("09:20:00") and datetime.fromisoformat(r1["phases"][1]["to"]) == t("09:30:00")
    assert all("subagents" not in p for p in r1["phases"])
    assert r1["workingSeconds"] == 2400 and r1["humanAttentionSeconds"] == 300 + 180
    assert r1["label"] == "L1" and r1["endAt"] is not None
    assert runs[1]["workingSeconds"] == 600 and runs[1]["humanAttentionSeconds"] == 180


def test_reply_beyond_60_seconds_does_not_count(client):
    run(client, 1, "09:00:00", "10:00:00", phases=[("09:00:00", "working"), ("09:20:00", "waiting_input"), ("09:30:00", "working")],
        interactions=[("reply", "09:30:00")])
    run(client, 2, "09:00:00", "10:00:00", phases=[("09:00:00", "working"), ("09:20:00", "waiting_input"), ("09:30:00", "working")],
        interactions=[("reply", "09:29:00")])  # 回复在转入 working 之前 → 不算这段的开头
    runs = {r["runId"]: r for r in get(client, HIST, **span(detail="segments"))["agents"]["runs"]}
    assert runs["cv-1"]["phases"][2]["startedBy"] == "human_reply"
    assert "startedBy" not in runs["cv-2"]["phases"][2]


def test_handoffs_and_waited_seconds(client):
    _scenario(client)
    h = get(client, HIST, **span(detail="segments"))["handoffs"]
    got = [(datetime.fromisoformat(x["at"]).astimezone(t("00:00:00").tzinfo).strftime("%H:%M"), x["runId"], x["type"], x.get("waitedSeconds", "-")) for x in h]
    # R2 在 09:10 转入等人，人 09:36 才开始看它（attend）→ 1560；没有回复也没有 attend 的见下一个用例
    assert got == [("09:10", "cv-2", "agent_to_human", 1560), ("09:20", "cv-1", "agent_to_human", 600),
                   ("09:30", "cv-1", "human_to_agent", "-"), ("09:50", "cv-1", "agent_to_human", 300)]


def test_waited_is_null_when_reply_after_range_end(client):
    run(client, 1, "09:00:00", "10:00:00", phases=[("09:00:00", "working"), ("09:20:00", "waiting_input")])
    h = get(client, HIST, **span(detail="segments"))["handoffs"]
    assert h == [{"at": h[0]["at"], "runId": "cv-1", "type": "agent_to_human", "waitedSeconds": None}]


def test_overlap(client):
    _scenario(client)
    o = get(client, HIST, **span(detail="segments"))["overlap"]
    # 干活并集 09:00–09:20 + 09:30–09:50 = 2400；人在 09:35–09:40 看着干活中的 R1 → 扣 300；R2 那次在它等人时，不算「看着干活的」
    assert o == {"maxAgentsWorkingAtOnce": 2, "secondsWithAgentsWorkingWhileHumanElsewhere": 2100}


def test_on_agent_run_exactly_one(client):
    w = world(client)
    _scenario(client)
    backfill(client, w["a"]["id"], "09:36:00", 120)   # R1 与 R2 的 attend 都压着它 → 分不清 → null
    backfill(client, w["a"]["id"], "09:56:00", 60)    # 只有 R1 → R1
    backfill(client, w["a"]["id"], "08:00:00", 60)    # 谁也没看 → null
    segs = {datetime.fromisoformat(s["from"]).astimezone(t("00:00:00").tzinfo).strftime("%H:%M"): s["onAgentRun"]
            for s in get(client, HIST, **span(detail="segments"))["human"]["segments"]}
    assert segs == {"08:00": None, "09:36": None, "09:56": "cv-1"}


def test_segments_sorted_ascending_mixed(client):
    w = world(client)
    backfill(client, w["a"]["id"], "12:00:00", 60)
    backfill(client, w["a"]["id"], "08:00:00", 60)
    froms = [s["from"] for s in get(client, HIST, **span(detail="segments"))["human"]["segments"]]
    assert froms == sorted(froms, key=datetime.fromisoformat)


# ------------------------------------------------------------------ 上限


def test_caps_set_truncated(client, monkeypatch):
    w = world(client)
    backfill(client, w["a"]["id"], "09:00:00", 60)
    backfill(client, w["b"]["id"], "10:00:00", 60)
    run(client, 1, "09:00:00", "10:00:00", phases=[("09:00:00", "working"), ("09:20:00", "waiting_input")])
    run(client, 2, "09:00:00", "10:00:00", phases=[("09:00:00", "working"), ("09:20:00", "waiting_input")])
    assert get(client, HIST, **span(detail="segments"))["truncated"] is False
    for name, kw in (("CAP_SEGMENTS", {"detail": "segments"}), ("CAP_RUNS", {"detail": "segments"}), ("CAP_HANDOFFS", {"detail": "segments"}),
                     ("CAP_BY_TARGET", {})):
        monkeypatch.setattr(collab_history, name, 1)
        body = get(client, HIST, **span(**kw))
        assert body["truncated"] is True, name
        monkeypatch.undo()
    monkeypatch.setattr(collab_history, "CAP_SEGMENTS", 1)
    body = get(client, HIST, **span(detail="segments"))
    assert len(body["human"]["segments"]) == 1 and body["human"]["confirmedSeconds"] == 120  # 汇总不受段上限影响
    monkeypatch.undo()
    monkeypatch.setattr(collab_history, "CAP_RUNS", 1)
    body = get(client, HIST, **span(detail="segments"))
    assert len(body["agents"]["runs"]) == 1 and body["agents"]["workingSeconds"] == 2400  # 汇总不受 runs 上限影响
    assert len(body["agents"]["byAgent"]) == 1 and body["agents"]["byAgent"][0]["runs"] == 2


def test_caps_blocks(client, monkeypatch):
    tiers(client)
    upload(client, [rec(D, "09:00:00", "09:01:00", title="a"), rec(D, "10:00:00", "10:01:00", title="b")])
    monkeypatch.setattr(collab_history, "CAP_BLOCKS", 1)
    body = get(client, HIST, **span(detail="windows"))
    assert len(body["human"]["blocks"]) == 1 and body["truncated"] is True


# ------------------------------------------------------------------ 评审补充


def test_unknown_scope_is_422(client):
    r = client.get(HIST, params={"scope": "nope"})
    assert r.status_code == 422 and "scope" in r.json()["detail"]


def test_segment_crossing_midnight_listed_with_real_bounds(client):
    w = world(client)
    prev = (datetime.fromisoformat(D) - timedelta(days=1)).date().isoformat()
    backfill(client, w["a"]["id"], "23:30:00", 3600, date=prev)       # 前一天 23:30 → D 00:30
    body = get(client, HIST, **span(detail="segments"))
    seg = body["human"]["segments"][0]
    assert datetime.fromisoformat(seg["from"]) == t("23:30:00", prev) and datetime.fromisoformat(seg["to"]) == t("00:30:00")
    assert body["human"]["confirmedSeconds"] == 0                      # byDay 按投影整段归开始那天
    wide = get(client, HIST, **{"from": prev, "to": D})["human"]
    assert wide["byDay"][0]["seconds"] == 3600 and wide["byDay"][1]["seconds"] == 0


def test_oldest_segments_kept_when_read_limit_hit(client, monkeypatch):
    w = world(client)
    for i in range(3):
        backfill(client, w["a"]["id"], f"0{i + 1}:00:00", 60)
    monkeypatch.setattr(collab_history, "_READ_LIMIT", 2)
    body = get(client, HIST, **span(detail="segments"))
    assert body["truncated"] is True
    assert [datetime.fromisoformat(s["from"]) for s in body["human"]["segments"]] == [t("01:00:00"), t("02:00:00")]


def test_summary_pending_does_not_read_day_by_day(client, monkeypatch):
    from app.modules.activity import collab_repo, records_repo  # noqa: PLC0415

    tiers(client)
    upload(client, [rec(D, "09:00:00", "09:10:00", title="doc")])
    calls = {"n": 0}
    for mod, name in ((collab_repo, "day_records_full"), (records_repo, "day_records")):
        if hasattr(mod, name):
            real = getattr(mod, name)
            monkeypatch.setattr(mod, name, lambda *a, _r=real, **k: (calls.__setitem__("n", calls["n"] + 1), _r(*a, **k))[1])
    spans = {"pending_spans": 0, "range_records": 0}
    for name in spans:
        real = getattr(collab_repo, name)
        monkeypatch.setattr(collab_repo, name, lambda *a, _r=real, _n=name, **k: (spans.__setitem__(_n, spans[_n] + 1), _r(*a, **k))[1])
    first = (datetime.fromisoformat(D) - timedelta(days=91)).date().isoformat()
    body = get(client, HIST, **{"from": first, "to": D})
    assert body["human"]["pendingSeconds"] == 600 and len(body["human"]["byDay"]) == 92
    assert calls["n"] == 0 and spans == {"pending_spans": 1, "range_records": 0}


def test_segments_read_records_once(client, monkeypatch):
    from app.modules.activity import collab_repo  # noqa: PLC0415

    tiers(client)
    upload(client, [rec(D, "09:00:00", "09:10:00", title="doc")])
    n = {"c": 0}
    real = collab_repo.range_records
    monkeypatch.setattr(collab_repo, "range_records", lambda *a, **k: (n.__setitem__("c", n["c"] + 1), real(*a, **k))[1])
    get(client, HIST, **span(detail="segments"))
    assert n["c"] == 1
    get(client, HIST, **span(detail="windows"))
    assert n["c"] == 2


def test_agent_only_does_not_load_planner(client, monkeypatch):
    from app.modules.planner import service as planner_service  # noqa: PLC0415

    def boom(*a, **k):
        raise AssertionError("who=agent 不该读 planner")

    monkeypatch.setattr(planner_service, "list_tasks", boom)
    run(client, 1, "09:00:00", "10:00:00")
    assert get(client, HIST, **span(who="agent", detail="segments"))["agents"]["workingSeconds"] == 3600


def test_pending_block_cap(client, monkeypatch):
    tiers(client)
    upload(client, [rec(D, "09:00:00", "09:01:00", title="a"), rec(D, "10:00:00", "10:01:00", title="b")])
    monkeypatch.setattr(collab_history, "CAP_PENDING_BLOCKS", 1)
    body = get(client, HIST, **span())
    assert body["truncated"] is True and body["human"]["pendingSeconds"] == 60


def test_lanes_view_does_not_leak_new_projection_keys(client):
    w = world(client)
    backfill(client, w["a"]["id"], "09:00:00", 600)
    lanes = get(client, f"{API}/views/lanes", date=D)
    assert lanes["human"]["sessions"] and all(set(s) == {"startAt", "endAt", "durationSeconds", "taskId", "projectId", "mode", "source"}
                                              for s in lanes["human"]["sessions"])
