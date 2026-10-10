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
def test_work_300_leaves_low(client, w, dur, want):  # noqa: F811
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

@pytest.mark.parametrize(("attn", "work", "dropped", "want"), [
    (599, 0, 0, "normal"), (600, 0, 0, "high"), (0, 3599, 0, "normal"), (0, 3600, 0, "high"),
    (0, 299, 0, "low"), (0, 300, 0, "normal"), (29, 299, 0, "low"), (30, 299, 0, "normal"),
    (600, 100, 0, "high"),  # high 优先于 low
    (0, 0, 1, "normal"), (0, 3600, 1, "high"),  # 有被封顶丢掉的已结束运行 = 不是临时的，但 high 照旧
])
def test_tier_of_boundaries(attn, work, dropped, want):
    from app.modules.views.lane_tier import tier_of  # noqa: PLC0415

    assert tier_of(attn, work, dropped) == want


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


# ---- 临时会话：开着不管，只看干活时间（不是时长）----

def _idle_after_2min_work(w, x):
    """在跑运行干了 2 分钟（120 秒）后转 idle，已空闲 x 秒；最近有心跳，没有任何注意力。"""
    w.live_s("a", start_s=x + 120, phases=[(x, "idle")], seen_s=5, beats=3)


@pytest.mark.parametrize(("x", "state"), [(599, "shown"), (600, "idle")])
def test_ephemeral_open_session_folds_at_600_idle(client, w, x, state):  # noqa: F811
    _idle_after_2min_work(w, x)
    shown, inactive, _ = _view(client)
    if state == "shown":
        assert shown == {"a"}
    else:
        assert shown == set() and (inactive["a"]["reason"], inactive["a"]["tier"]) == ("idle", "low")


@pytest.mark.parametrize("t", [7199, 7200, 30000])
def test_ephemeral_open_session_stays_folded_while_its_run_is_alive(client, w, t):  # noqa: F811
    _idle_after_2min_work(w, 600 + t)  # 空闲 600 秒折叠，再过 t 秒；运行还开着、没失联 → 永不过期
    shown, inactive, body = _view(client)
    assert shown == set() and (inactive["a"]["reason"], inactive["a"]["tier"], body["expiredAgents"]) == ("idle", "low", 0)


@pytest.mark.parametrize(("e", "state"), [(7199, "listed"), (7200, "expired")])
def test_ephemeral_session_expires_7200_after_its_run_ended(client, w, e, state):  # noqa: F811
    w.closed_s("a", 20000 + e, e, phases=[(19900 + e, "idle")])  # 干了 100 秒就空闲，一直开着到 e 秒前才结束
    shown, inactive, body = _view(client)
    assert shown == set()
    if state == "listed":
        assert (inactive["a"]["tier"], body["expiredAgents"]) == ("low", 0)
    else:
        assert inactive == {} and body["expiredAgents"] == 1


@pytest.mark.parametrize("start_s", [6 * H, 9 * H])
def test_silent_bare_start_open_run_folds_idle_and_never_expires(client, w, start_s):  # noqa: F811
    w.live_s("a", start_s=start_s, seen_s=start_s)  # 不发心跳、没有相位、开始后没再有信号
    shown, inactive, body = _view(client)
    assert shown == set() and (inactive["a"]["reason"], inactive["a"]["tier"], body["expiredAgents"]) == ("idle", "low", 0)


def test_heartbeating_bare_run_is_shown_and_closed_bare_3h_run_is_high(client, w):  # noqa: F811
    w.live_s("b", start_s=6 * H, seen_s=5, beats=3)
    assert _view(client)[0] == {"b"}
    assert _tier_of_closed(client, w, "c", 3 * H) == "high"


def test_lost_open_run_expiry_counts_from_when_it_became_lost(client, w):  # noqa: F811
    w.live_s("a", start_s=7400, lost=True, seen_s=7200 + 100, beats=3)  # 最后信号 7300 秒前 → 判出失联在 5500 秒前
    assert _view(client)[2]["expiredAgents"] == 0
    w.open.clear()
    w.live_s("a", start_s=9100, lost=True, seen_s=7200 + 1800, beats=3)  # 判出失联在 7200 秒前
    assert _view(client)[2]["expiredAgents"] == 1


def test_over_truncated_closed_read_makes_every_lane_at_least_normal(client, w, monkeypatch):  # noqa: F811
    from app.modules.views import lanes  # noqa: PLC0415

    monkeypatch.setattr(lanes, "MAX_CLOSED_READ", 4)
    w.closed_s("a", 28000 + 3500, 28000)  # 老的 3500 秒干活：只有它在，窗口里 a 合计 3600 = high；轻读被截断后它读不到
    w.closed_s("a", 8100, 8000)           # 新的 100 秒：读得到；孤零零看是 low，结束已满 7200 秒（本会过期）
    for i in range(3):  # 别的标签的洪水，比 a 的运行都新
        w.closed_s(f"x{i}", 300 + i, 200 + i)
    body = _get(client)
    assert body["truncated"] and body["expiredAgents"] == 0
    assert {a["label"]: a["tier"] for a in body["inactiveAgents"]}["a"] == "normal"


@pytest.mark.parametrize(("e", "state"), [(7199, "shown"), (7200, "idle")])
def test_recent_attention_keeps_an_idle_lane_shown(client, w, e, state):  # noqa: F811
    w.live_s("a", start_s=3 * H + 700, phases=[(3 * H, "idle")], seen_s=5, beats=3, attn=[(e, 700)])  # 3 小时前就空闲，700 秒注意力结束于 e 秒前
    shown, inactive, _ = _view(client)
    assert (shown, inactive["a"]["tier"] if inactive else None) == (({"a"}, None) if state == "shown" else (set(), "high"))


@pytest.mark.parametrize(("e", "state"), [(1799, "shown"), (1800, "ended")])
def test_recent_attention_keeps_an_ended_lane_shown(client, w, e, state):  # noqa: F811
    w.closed_s("a", 4 * H, 3 * H, attn=[(e, 700)])  # 运行 3 小时前结束，注意力结束于 e 秒前
    shown, inactive, _ = _view(client)
    assert (shown, inactive["a"]["reason"] if inactive else None) == (({"a"}, None) if state == "shown" else (set(), "ended"))


def test_attention_does_not_unfold_a_normal_error_lane(client, w):  # noqa: F811
    w.live_s("a", start_s=1200, phases=[(300, "error")], seen_s=5, beats=3, attn=[(10, 100)])  # normal 档（注意力 100 秒），出错
    shown, inactive, _ = _view(client)
    assert shown == set() and inactive["a"]["reason"] == "error"


@pytest.mark.parametrize(("work", "tier"), [(299, "low"), (300, "normal")])
def test_open_session_work_299_vs_300(client, w, work, tier):  # noqa: F811
    w.live_s("a", start_s=700 + work, phases=[(700, "idle")], seen_s=5, beats=3)  # 空闲 700 秒，已过 low 的 600 秒
    _, inactive, _ = _view(client)
    if tier == "low":
        assert inactive["a"]["tier"] == "low"
    else:
        assert "a" not in inactive  # normal 留 3600 秒：仍显示


def test_lane_with_capped_closed_runs_is_never_low(client, w, monkeypatch):  # noqa: F811
    from app.modules.views import lane_cap  # noqa: PLC0415

    monkeypatch.setattr(lane_cap, "MAX_RUNS_PER_LANE", 1)
    w.closed_s("a", 2100, 2000)  # 保留的，100 秒
    w.closed_s("a", 4100, 4000)  # 被封顶丢掉，只有轻记录
    shown, inactive, body = _view(client)
    assert shown == set() and inactive["a"]["tier"] == "normal" and body["expiredAgents"] == 0
