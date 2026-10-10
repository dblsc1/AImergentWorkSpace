"""泳道权重档位与留存（契约 v2.25）：每个档位边界、各档留存、出错、过期、纯函数。时钟钉法同 ``test_lanes_active``（三个时刻各跑一遍）。"""

from __future__ import annotations

from datetime import timedelta

import pytest
from test_lanes_active import _ago_s, _get, _now, w  # noqa: F401

H = 3600


def _view(client):
    body = _get(client)
    return {a["label"] for a in body["agents"]}, {a["label"]: a for a in body["inactiveAgents"]}, body


def _tier_of_closed(client, w, label, dur, ended_ago=2000, attn=()):  # noqa: F811
    """一条已结束运行（时长 dur 秒、结束于 ended_ago 秒前，没有相位 = 全程干活）所在泳道的档位；该泳道必须已折叠。"""
    w.closed_s(label, ended_ago + dur, ended_ago, attn=attn)
    shown, inactive, _ = _view(client)
    assert label not in shown, label
    return inactive[label]["tier"]


@pytest.mark.parametrize(("attn", "want"), [(599, "normal"), (600, "high")])
def test_attention_600_makes_high(client, w, attn, want):  # noqa: F811
    assert _tier_of_closed(client, w, "a", 3000, attn=[(2100, attn)]) == want


@pytest.mark.parametrize(("dur", "want"), [(3599, "normal"), (3600, "high")])
def test_work_3600_makes_high(client, w, dur, want):  # noqa: F811
    assert _tier_of_closed(client, w, "a", dur) == want


@pytest.mark.parametrize(("dur", "want"), [(299, "low"), (300, "normal")])
def test_elapsed_300_leaves_low(client, w, dur, want):  # noqa: F811
    assert _tier_of_closed(client, w, "a", dur) == want


@pytest.mark.parametrize(("attn", "want"), [(29, "low"), (30, "normal")])
def test_attention_30_leaves_low(client, w, attn, want):  # noqa: F811
    assert _tier_of_closed(client, w, "a", 299, attn=[(2010, attn)]) == want


def test_attention_is_clipped_to_the_window(client, w):  # noqa: F811
    """跨窗口起点的注意力只算窗口里的部分：700 秒里只有 300 秒在窗口内 → 没到 high。"""
    start = _now().replace(hour=0, minute=0) - timedelta(days=1)  # 两天窗口的起点
    span = int((_now() - start).total_seconds())
    w.closed_s("a", span + 2000, span - 1000, attn=[(span - 300, 700)])  # 注意力 [start-400s, start+300s]
    shown, inactive, _ = _view(client)
    assert "a" not in shown and inactive["a"]["tier"] == "normal"  # 未裁剪会是 700 ≥ 600 → high


# ---- 留存 ----

@pytest.mark.parametrize(("x", "state"), [(7199, "shown"), (7200, "idle")])
def test_idle_keep_high_7200(client, w, x, state):  # noqa: F811
    w.live_s("a", start_s=x + 3600, phases=[(x, "idle")], seen_s=5)  # 干了 3600 秒 = high
    shown, inactive, _ = _view(client)
    if state == "shown":
        assert shown == {"a"}
    else:
        assert shown == set() and (inactive["a"]["reason"], inactive["a"]["tier"]) == ("idle", "high")


@pytest.mark.parametrize(("x", "state"), [(3599, "shown"), (3600, "idle")])
def test_idle_keep_normal_3600(client, w, x, state):  # noqa: F811
    w.live_s("a", start_s=x + 600, phases=[(x, "idle")], seen_s=5)
    shown, inactive, _ = _view(client)
    if state == "shown":
        assert shown == {"a"}
    else:
        assert shown == set() and (inactive["a"]["reason"], inactive["a"]["tier"]) == ("idle", "normal")


@pytest.mark.parametrize(("ended", "state"), [(1799, "shown"), (1800, "ended")])
def test_ended_keep_high_1800(client, w, ended, state):  # noqa: F811
    w.closed_s("a", ended + 3600, ended)
    shown, inactive, _ = _view(client)
    if state == "shown":
        assert shown == {"a"}
    else:
        assert shown == set() and (inactive["a"]["reason"], inactive["a"]["tier"]) == ("ended", "high")


@pytest.mark.parametrize(("dur", "tier"), [(600, "normal"), (100, "low")])
def test_ended_normal_and_low_fold_at_once(client, w, dur, tier):  # noqa: F811
    w.closed_s("a", dur + 1, 1)
    shown, inactive, _ = _view(client)
    assert shown == set() and (inactive["a"]["reason"], inactive["a"]["tier"]) == ("ended", tier)


def test_ended_high_by_attention_lingers(client, w):  # noqa: F811
    w.closed_s("a", 900, 60, attn=[(100, 650)])  # 只干了 14 分钟，但人看了 650 秒
    shown, _, _ = _view(client)
    assert shown == {"a"}


@pytest.mark.parametrize(("e", "state"), [(1799, "shown"), (1800, "error")])
def test_error_keep_high_1800(client, w, e, state):  # noqa: F811
    w.live_s("a", start_s=e + 5200, phases=[(e, "error")], seen_s=5)  # 出错前干了 5200 秒 = high
    shown, inactive, _ = _view(client)
    if state == "shown":
        assert shown == {"a"}
    else:
        assert shown == set() and (inactive["a"]["reason"], inactive["a"]["tier"]) == ("error", "high")


def test_error_normal_folds_at_once_and_failed_high_lingers(client, w):  # noqa: F811
    w.live_s("n", start_s=700, phases=[(100, "error")], seen_s=5)
    w.closed_s("h", 1799 + 3600, 1799, outcome="failed")
    w.closed_s("h2", 1800 + 3600, 1800, outcome="failed")
    shown, inactive, _ = _view(client)
    assert shown == {"h"} and inactive["n"]["reason"] == "error" and inactive["n"]["tier"] == "normal"
    assert inactive["h2"]["reason"] == "error" and inactive["h2"]["tier"] == "high"


def test_waiting_is_never_folded_whatever_the_tier(client, w):  # noqa: F811
    w.live_s("fresh", start_s=40, phases=[(30, "waiting_input")])  # low 档
    w.live_s("long", start_s=9 * H, phases=[(8 * H, "waiting_permission")], seen_s=8 * H)
    shown, inactive, _ = _view(client)
    assert shown == {"fresh", "long"} and inactive == {}


# ---- 过期（只对 low；视图层，什么都没删）----

@pytest.mark.parametrize(("t", "state"), [(7199, "listed"), (7200, "expired")])
def test_low_lane_expires_7200_after_folding(client, w, t, state):  # noqa: F811
    w.closed_s("a", t + 100, t)
    w.closed_s("a", t + 300, t + 200)  # 同一泳道第二条，过期时一并数
    shown, inactive, body = _view(client)
    assert shown == set()
    if state == "listed":
        assert inactive["a"]["tier"] == "low" and (body["expiredAgents"], body["expired"]) == (0, {"agents": 0, "runs": 0, "elapsedSeconds": 0})
    else:
        assert inactive == {} and body["expiredAgents"] == 1
        assert body["expired"] == {"agents": 1, "runs": 2, "elapsedSeconds": 200}
        assert body["agents"] == [] and body["dropped"] == []


def test_low_lane_idle_fold_clock_starts_after_the_keep_not_at_last_work(client, w):  # noqa: F811
    """low 的折叠起点 = 不再显示的那一刻；已结束的 low 起点 = 结束（ENDED_KEEP 0）。"""
    w.closed_s("old", 8000 + 100, 8000)
    w.closed_s("new", 7000 + 100, 7000)
    _, inactive, body = _view(client)
    assert set(inactive) == {"new"} and body["expiredAgents"] == 1


def test_normal_and_high_never_expire_inside_the_window(client, w):  # noqa: F811
    w.closed_s("norm", 20 * H + 600, 20 * H)
    w.closed_s("high", 21 * H + 3600, 20 * H)
    _, inactive, body = _view(client)
    assert {k: v["tier"] for k, v in inactive.items()} == {"norm": "normal", "high": "high"}
    assert body["expiredAgents"] == 0


def test_expired_lane_comes_back_when_it_works_again(client, w):  # noqa: F811
    w.closed_s("a", 9000 + 100, 9000)
    assert _view(client)[2]["expiredAgents"] == 1
    w.live_s("a", start_s=30, phases=[(10, "working")])
    shown, inactive, body = _view(client)
    assert shown == {"a"} and body["expiredAgents"] == 0  # 过期只是不列出：它回来就是新的状态


def test_pinned_lane_is_never_folded_or_expired(client, w):  # noqa: F811
    w.closed_s("a", 20 * H + 100, 20 * H)
    assert client.put("/api/core/lanes/prefs/agent", json={"agent": "cc", "label": "a", "pinned": True}).status_code == 200
    shown, inactive, body = _view(client)
    assert shown == {"a"} and body["expiredAgents"] == 0


def test_expiry_leaves_the_books_alone(client, w):  # noqa: F811
    """过期只影响 views/lanes 这一份：投影里的运行还在。"""
    from app.modules.projector.handlers import lanes as projection  # noqa: PLC0415

    w.closed_s("a", 9000 + 100, 9000)
    assert _view(client)[2]["expiredAgents"] == 1
    start, end = _now() - timedelta(days=2), _now() + timedelta(days=1)
    assert len(projection.read_lanes("u_local", "run", start, end, 10)) == 1


# ---- 纯函数 ----

@pytest.mark.parametrize(("attn", "work", "elapsed", "want"), [
    (599, 0, 3000, "normal"), (600, 0, 3000, "high"), (0, 3599, 3599, "normal"), (0, 3600, 3600, "high"),
    (0, 0, 299, "low"), (0, 0, 300, "normal"), (29, 0, 299, "low"), (30, 0, 299, "normal"),
    (600, 0, 100, "high"),  # high 优先于 low
])
def test_tier_of_boundaries(attn, work, elapsed, want):
    from app.modules.views.lane_tier import tier_of  # noqa: PLC0415

    assert tier_of(attn, work, elapsed) == want


@pytest.mark.parametrize(("ago", "folded"), [(599, False), (600, True)])
def test_idle_keep_low_600_pure(ago, folded):
    """low 档的空闲留存（600 秒）：整条链路里 low 要求时长 < 300 秒，所以只能直接对判定函数验。"""
    from app.modules.views import lane_active  # noqa: PLC0415

    now = _now()
    item = {"runId": "r", "agent": "cc", "label": "x", "unverified": False, "startAt": _ago_s(ago).isoformat(), "endAt": None,
            "lost": False, "lastSeenAt": None, "beatCount": 0, "phases": [{"at": _ago_s(ago).isoformat(), "phase": "idle"}],
            "outcome": None, "elapsedSeconds": 0}
    got = lane_active._verdict([item], "low", now)
    assert (got is not None) == folded and (got is None or got[0] == "idle")


def test_constants_are_the_decided_ones():
    from app.modules.views import lane_tier as t  # noqa: PLC0415

    assert (t.ATTN_HIGH_SECONDS, t.WORK_HIGH_SECONDS, t.ATTN_LOW_SECONDS, t.EPHEMERAL_SECONDS) == (600, 3600, 30, 300)
    assert (t.IDLE_KEEP, t.ENDED_KEEP, t.FOLD_EXPIRE) == (
        {"high": 7200, "normal": 3600, "low": 600}, {"high": 1800, "normal": 0, "low": 0}, {"low": 7200})
