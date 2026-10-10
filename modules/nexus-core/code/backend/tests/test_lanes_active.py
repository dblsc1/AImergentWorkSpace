"""``views/lanes`` 的灵活留存（契约 v2.24 过滤 + v2.25 权重档位）：折叠进 ``inactiveAgents``，low 档折叠满期后不再列出。

时钟：``now`` 与视图的「今天」/ 窗口**一起**钉死（固定日期 2026-10-10，不看真实时钟），``w`` 固件在 00:10 / 09:00 / 23:50 三个时刻各跑一遍；
默认读「昨天 + 今天」两天窗口，所以相对 ``now`` 24 小时内的数据在任何时刻都在窗口里。在跑的运行用桩出来的 ``list_lane_runs`` 给，已结束的直接写投影。
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pytest

LANES = "/api/core/views/lanes"
PREFS = "/api/core/lanes/prefs/agent"
BASE = date(2026, 10, 10)
HOURS = [(0, 10), (9, 0), (23, 50)]
CLOCK: dict = {}


def _tz():
    from app.config import settings  # noqa: PLC0415

    return settings.tz


def _now() -> datetime:
    return CLOCK["now"]


def _ago(minutes: float) -> datetime:
    return _now() - timedelta(minutes=minutes)


def _ago_s(seconds: int) -> datetime:
    return _now() - timedelta(seconds=seconds)


def _win() -> dict:
    """页面读图用的窗口：昨天 + 今天（PUT order 也要带同一个）。"""
    today = _now().date()
    return {"from": (today - timedelta(days=1)).isoformat(), "to": today.isoformat()}


def _attends(spans) -> list[dict]:
    """spans = [(结束于多少秒前, 持续秒数)] → 运行上的 attend 连线。"""
    return [{"kind": "attend", "at": _ago_s(end + length).isoformat(), "until": _ago_s(end).isoformat()}
            for end, length in spans]


class World:
    def __init__(self, monkeypatch):
        self.open: list[dict] = []
        from app.modules.timer import service  # noqa: PLC0415
        from app.modules.views import lanes  # noqa: PLC0415

        monkeypatch.setattr(service, "list_lane_runs", lambda user=None: (_now(), self.open))
        monkeypatch.setattr(lanes, "_today", lambda: _now().date().isoformat())  # 视图的「今天」与 now 同钉

    def live_s(self, label, start_s=18000, phases=(), lost=False, seen_s=60, attn=(), beats=0, overdue=False):
        """时间都是「多少秒前」；phases = [(多少秒前, 相位)]。"""
        rid = f"o-{label}-{len(self.open)}"
        self.open.append({"runId": rid, "agent": "cc", "tool": "t", "model": None, "label": label, "taskId": None,
                          "projectId": None, "beatSource": None, "beatCount": beats, "unverified": False, "match": None,
                          "startTs": _ago_s(start_s), "endTs": None, "outcome": None,
                          "elapsedSeconds": start_s, "overdue": overdue, "lost": lost,
                          "lastSeenTs": _ago_s(seen_s),
                          "phases": [{"at": _ago_s(m).isoformat(), "phase": p} for m, p in phases],
                          "interactions": _attends(attn)})
        return rid

    def live(self, label, start_min=300, phases=(), lost=False, seen_min=1, **kw):
        return self.live_s(label, start_min * 60, [(m * 60, p) for m, p in phases], lost, seen_min * 60, **kw)

    def closed_s(self, label, start_s, end_s, outcome="done", phases=(), attn=()):
        from app.modules.projector import repo  # noqa: PLC0415

        rid = f"c-{label}-{start_s}-{end_s}"
        repo.apply_lane({"user": "u_local", "key": rid, "kind": "run", "runId": rid, "agent": "cc", "label": label,
                         "startAt": _ago_s(start_s), "endAt": _ago_s(end_s), "durationSeconds": start_s - end_s,
                         "phases": [{"at": _ago_s(m).isoformat(), "phase": p} for m, p in phases],
                         "interactions": _attends(attn), "taskId": None, "projectId": None, "outcome": outcome})
        return rid

    def closed(self, label, start_min, end_min, outcome="done", phases=(), **kw):
        return self.closed_s(label, start_min * 60, end_min * 60, outcome, [(m * 60, p) for m, p in phases], **kw)


@pytest.fixture(params=HOURS, ids=lambda h: f"{h[0]:02d}{h[1]:02d}")
def w(request, client, monkeypatch):
    CLOCK["now"] = datetime.combine(BASE, time(*request.param), _tz())
    return World(monkeypatch)


def _get(client, params=None, **kw):
    resp = client.get(LANES, params=_win() if params is None else params, **kw)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _split(client):
    body = _get(client)
    return {a["label"] for a in body["agents"]}, {a["label"]: a for a in body["inactiveAgents"]}, body


def test_working_and_waiting_always_shown_even_after_hours(client, w):
    w.live("work", phases=[(200, "working")])
    w.live("wait", phases=[(200, "waiting_permission")])
    w.live("ask", phases=[(200, "waiting_input")])
    shown, inactive, _ = _split(client)
    assert shown == {"work", "wait", "ask"} and inactive == {}


def test_idle_threshold_59_shown_61_inactive(client, w):
    w.live("p59", start_min=69, phases=[(59, "idle")])  # 59 分钟前转入空闲 = 最后干活在 59 分钟前（干了 10 分钟 = normal 档）
    w.live("p61", start_min=71, phases=[(61, "idle")])
    shown, inactive, _ = _split(client)
    assert shown == {"p59"} and inactive["p61"]["reason"] == "idle"
    assert inactive["p61"]["lastWorkAt"] == _ago(61).isoformat() and inactive["p61"]["runs"] == 1


def test_error_open_run_is_inactive_even_if_recent(client, w):
    w.live("bad", start_min=20, phases=[(5, "error")])  # 干了 15 分钟 = normal 档，出错立刻折叠
    shown, inactive, _ = _split(client)
    assert shown == set() and inactive["bad"]["reason"] == "error" and inactive["bad"]["tier"] == "normal"


def test_failed_latest_closed_inactive_but_newer_working_run_shows(client, w):
    w.closed("f", 30, 10, outcome="failed")
    shown, inactive, _ = _split(client)
    assert shown == set() and inactive["f"]["reason"] == "error"
    w.live("f", start_min=5)
    shown, inactive, _ = _split(client)
    assert shown == {"f"} and inactive == {}


def test_ended_normal_lanes_fold_immediately_as_ended(client, w):
    w.closed("old", 200, 120)
    w.closed("fresh", 30, 20)  # v2.25：已结束的 normal 泳道不再留 1 小时，立刻折叠
    shown, inactive, _ = _split(client)
    assert shown == set() and {k: v["reason"] for k, v in inactive.items()} == {"old": "ended", "fresh": "ended"}
    assert inactive["fresh"]["lastWorkAt"] == _ago(20).isoformat()


def test_parked_open_run_with_fresh_heartbeats_is_inactive(client, w):
    w.live("parked", phases=[(130, "idle")], seen_min=0)
    shown, inactive, _ = _split(client)
    assert shown == set() and inactive["parked"]["reason"] == "idle" and inactive["parked"]["tier"] == "high"


def test_pinned_inactive_is_shown_and_hidden_only_in_hidden(client, w):
    w.live("pin", phases=[(300, "idle")])
    w.live("hid", phases=[(300, "idle")])
    w.live("hid2", start_min=20, phases=[(5, "error")])
    assert client.put(PREFS, json={"agent": "cc", "label": "pin", "pinned": True}).status_code == 200
    assert client.put(PREFS, json={"agent": "cc", "label": "hid", "hidden": True}).status_code == 200
    assert client.put(PREFS, json={"agent": "cc", "label": "hid2", "hidden": True}).status_code == 200
    shown, inactive, body = _split(client)
    assert shown == {"pin"} and inactive == {}
    assert {h["label"] for h in body["hiddenAgents"]} == {"hid", "hid2"}


def test_past_day_is_unfiltered(client, w):
    from app.modules.projector import repo  # noqa: PLC0415

    day = BASE - timedelta(days=1)  # 窗口整体早于（钉死的）现在
    start = datetime.combine(day, time(8), _tz())
    repo.apply_lane({"user": "u_local", "key": "p1", "kind": "run", "runId": "p1", "agent": "cc", "label": "past",
                     "startAt": start, "endAt": start + timedelta(hours=1), "durationSeconds": 3600, "phases": [],
                     "interactions": [], "taskId": None, "projectId": None, "outcome": "failed"})
    body = _get(client, params={"date": day.isoformat()})
    assert [a["label"] for a in body["agents"]] == ["past"] and body["inactiveAgents"] == []
    assert (body["expiredAgents"], body["expired"]["runs"]) == (0, 0)


def test_every_run_counted_once_and_dropped_follows_inactive_lane(client, w, monkeypatch):
    from app.modules.views import lane_cap  # noqa: PLC0415

    monkeypatch.setattr(lane_cap, "MAX_RUNS_PER_LANE", 2)
    for i in range(5):  # 吵闹的空闲身份：5 条已结束，封顶只留 2 条，3 条进 dropped，但整身份不活跃 → 全并入摘要
        w.closed("noisy", 400 - 10 * i, 395 - 10 * i)
    for i in range(4):  # 吵闹但在干活的身份（有在跑的运行）：2 条留下，2 条仍在 dropped
        w.closed("busy", 40 - 10 * i, 38 - 10 * i)
    w.live("busy", start_min=5, phases=[(1, "working")])
    w.live("work", phases=[(1, "working")])
    w.live("hidden", phases=[(1, "working")])
    assert client.put(PREFS, json={"agent": "cc", "label": "hidden", "hidden": True}).status_code == 200
    shown, inactive, body = _split(client)
    assert inactive["noisy"]["runs"] == 5 and inactive["noisy"]["elapsedSeconds"] == 5 * 300
    assert [d["label"] for d in body["dropped"]] == ["busy"]
    total = len(body["agents"]) + sum(i["runs"] for i in body["inactiveAgents"]) + sum(d["runs"] for d in body["dropped"])
    assert total == 5 + 4 + 1 + 1  # 隐藏的那条另算（在 hiddenAgents）
    assert shown == {"busy", "work"}


def _total(body):
    return len(body["agents"]) + sum(i["runs"] for i in body["inactiveAgents"]) + sum(d["runs"] for d in body["dropped"])


def test_live_cap_dropped_working_run_keeps_lane_shown(client, w, monkeypatch):
    from app.modules.views import lane_cap  # noqa: PLC0415

    monkeypatch.setattr(lane_cap, "MAX_LIVE", 4)
    w.live("busy", start_min=400, phases=[(399, "working")])  # 最旧：被在跑封顶丢掉，但还在干活
    w.live("busy", start_min=200, phases=[(100, "idle")])
    w.live("busy", start_min=150, phases=[(90, "idle")])
    w.live("stale", start_min=400, phases=[(399, "idle")])  # 最旧：被丢，空闲
    w.live("stale", start_min=200, phases=[(199, "idle")])
    w.live("stale", start_min=150, phases=[(149, "idle")])
    shown, inactive, body = _split(client)
    assert shown == {"busy"} and set(inactive) == {"stale"}  # 判定看见被丢的在跑运行
    assert [d["label"] for d in body["dropped"]] == ["busy"] and body["dropped"][0]["runs"] == 1
    assert "openRuns" not in body["dropped"][0] and "live" not in body["dropped"][0]
    assert inactive["stale"]["runs"] == 3  # 被丢的并入不活跃摘要，不在 dropped 重复数
    assert body["truncated"] is True and _total(body) == 6


def test_hidden_lane_in_neither_inactive_nor_dropped_and_prefs_loaded_once(client, w, monkeypatch):
    from app.modules.prefs import service as prefs_service  # noqa: PLC0415
    from app.modules.views import lane_cap  # noqa: PLC0415

    monkeypatch.setattr(lane_cap, "MAX_LIVE", 1)
    for m in (300, 200, 100):
        w.live("hid", start_min=m, phases=[(m - 1, "idle")])
    assert client.put(PREFS, json={"agent": "cc", "label": "hid", "hidden": True}).status_code == 200
    calls = []
    real = prefs_service.load
    monkeypatch.setattr(prefs_service, "load", lambda u: calls.append(u) or real(u))
    _, inactive, body = _split(client)
    assert inactive == {} and body["dropped"] == [] and [h["label"] for h in body["hiddenAgents"]] == ["hid"]
    assert len(calls) == 1


def test_report_scope_cannot_read_lanes(client, w):
    w.live("x", phases=[(5, "error")])
    assert client.get(LANES, headers={"X-Nexus-Scope": "report", "X-Nexus-Anonymous": "1"}).status_code == 403
    assert client.get(LANES, headers={"X-Nexus-Scope": "read", "Authorization": "Bearer t"}).status_code == 200


# ---- 第二轮评审：沉默的 working、拖拽下标、变异杀手 ----

def _seen(w, rid, minutes):
    next(r for r in w.open if r["runId"] == rid)["lastSeenTs"] = _ago(minutes)


def test_working_claim_10h_silent_without_heartbeats_is_inactive(client, w):
    rid = w.live("mute", start_min=700, phases=[(699, "working")], seen_min=600)
    shown, inactive, _ = _split(client)
    assert shown == set() and inactive["mute"]["reason"] == "idle"
    assert inactive["mute"]["lastWorkAt"] == _ago(600).isoformat()
    _seen(w, rid, 50)  # 50 分钟前还有信号 → 仍显示
    shown, inactive, _ = _split(client)
    assert shown == {"mute"} and inactive == {}


def test_silent_exactly_one_hour_is_inactive_boundary(client, w):
    w.live("edge", start_min=70, phases=[(69, "working")], seen_min=60)
    w.live("edge2", start_min=70, phases=[(69, "working")], seen_min=59)
    shown, inactive, _ = _split(client)
    assert shown == {"edge2"} and inactive["edge"]["reason"] == "idle"


def test_heartbeating_working_run_stays_shown(client, w):
    rid = w.live("beat", start_min=700, phases=[(699, "working")], seen_min=1)
    next(r for r in w.open if r["runId"] == rid)["beatCount"] = 5
    shown, inactive, _ = _split(client)
    assert shown == {"beat"} and inactive == {}


def test_overdue_runs_are_inactive_waiting_3h_is_not(client, w):
    for label, ph in (("od-work", "working"), ("od-wait", "waiting_input")):
        rid = w.live(label, start_min=900, phases=[(899, ph)], seen_min=1)
        next(r for r in w.open if r["runId"] == rid)["overdue"] = True
    w.live("wait3h", start_min=300, phases=[(180, "waiting_permission")], seen_min=179)
    shown, inactive, _ = _split(client)
    assert shown == {"wait3h"} and set(inactive) == {"od-work", "od-wait"}


def test_lost_run_is_not_live(client, w):
    w.live("lostrun", start_min=300, phases=[(299, "working")], lost=True, seen_min=200)
    shown, inactive, _ = _split(client)
    assert shown == set() and inactive["lostrun"]["reason"] == "ended"  # 失联 = 没有在跑的运行
    assert inactive["lostrun"]["lastWorkAt"] == _ago(200).isoformat()


def test_latest_run_decides_failure_not_the_oldest(client, w):
    w.closed("mix", 400, 390, outcome="failed")  # 最旧的失败
    w.closed("mix", 30, 10, outcome="done")  # 最新的成功且刚结束
    shown, inactive, _ = _split(client)
    assert shown == set() and inactive["mix"]["reason"] == "ended"  # 不是 error：最新一条决定


def test_unverified_pin_is_not_honoured(client, w):
    rid = w.live("anon", phases=[(300, "idle")])
    next(r for r in w.open if r["runId"] == rid)["unverified"] = True
    from app.modules.views import lane_active  # noqa: PLC0415

    now = _now()
    item = {"runId": rid, "agent": "cc", "label": "anon", "unverified": True, "startAt": _ago(300).isoformat(),
            "endAt": None, "lost": False, "lastSeenAt": None, "phases": [{"at": _ago(300).isoformat(), "phase": "idle"}],
            "outcome": None, "elapsedSeconds": 0}
    key = lane_active.ident("cc", "anon", True)
    prefs = {"agents": [{"key": key, "hidden": False, "pinned": True, "unverified": True}], "order": []}
    kept, inactive, _, _ = lane_active.split([item], [], prefs, now, now - timedelta(hours=12), now + timedelta(hours=12))
    assert kept == [] and inactive[0]["reason"] == "idle"


def test_waiting_run_dropped_by_cap_keeps_lane_shown(client, w, monkeypatch):
    from app.modules.views import lane_cap  # noqa: PLC0415

    monkeypatch.setattr(lane_cap, "MAX_LIVE", 2)
    w.live("q", start_min=400, phases=[(399, "waiting_input")])  # 最旧：被丢，但在等人
    w.live("q", start_min=200, phases=[(100, "idle")])
    w.live("q", start_min=150, phases=[(90, "idle")])
    shown, inactive, body = _split(client)
    assert shown == {"q"} and inactive == {} and body["dropped"][0]["runs"] == 1


def test_lane_whose_runs_were_all_dropped_is_still_judged(client, w, monkeypatch):
    from app.modules.views import lane_cap  # noqa: PLC0415

    monkeypatch.setattr(lane_cap, "MAX_LIVE", 2)
    w.live("old-idle", start_min=500, phases=[(499, "idle")])
    w.live("old-work", start_min=490, phases=[(489, "working")])
    w.live("new1", start_min=20, phases=[(19, "working")])
    w.live("new2", start_min=10, phases=[(9, "working")])
    shown, inactive, body = _split(client)
    assert shown == {"new1", "new2"} and set(inactive) == {"old-idle"} and inactive["old-idle"]["runs"] == 1
    assert [d["label"] for d in body["dropped"]] == ["old-work"] and _total(body) == 4


@pytest.mark.parametrize("seed", range(6))
def test_drag_index_counts_displayed_lanes_only(client, w, seed):
    import random  # noqa: PLC0415

    w.live("a", start_min=300, phases=[(5, "working")])
    w.live("err", start_min=20, phases=[(5, "error")])  # 折叠的出错泳道，档位夹在显示的中间
    w.live("b", start_min=280, phases=[(4, "idle")])
    w.live("gone", start_min=1400, phases=[(1390, "idle")])  # 空闲 > 1 h：折叠
    w.live("c", start_min=270, phases=[(3, "idle")])
    ids = {a["label"]: a["runId"] for a in _get(client)["agents"]}
    assert set(ids) == {"a", "b", "c"}
    order = lambda: [a["label"] for a in sorted(_get(client)["agents"], key=lambda a: a["rank"])]  # noqa: E731
    want, rng = order(), random.Random(seed)
    for _ in range(6):
        pick, idx = rng.choice(want), rng.randrange(3)
        r = client.put("/api/core/lanes/prefs/order", json={"runId": ids[pick], "index": idx, **_win()})
        assert r.status_code == 200, r.text
        want.remove(pick)
        want.insert(idx, pick)
        assert order() == want, (pick, idx)
    for hidden in ("err", "gone"):  # 折叠的不在可排位的队列里
        rid = next(x["runId"] for x in w.open if x["label"] == hidden)
        assert client.put("/api/core/lanes/prefs/order", json={"runId": rid, "index": 0, **_win()}).status_code == 404


def test_idle_exactly_3600s_is_inactive(client, w):
    w.live("exact", start_min=70, phases=[(60, "idle")])
    shown, inactive, _ = _split(client)
    assert shown == set() and inactive["exact"]["reason"] == "idle"
