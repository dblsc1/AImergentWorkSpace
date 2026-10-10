"""手动排位的 ``live_order`` 与 ``get_lanes`` 同一条管线（契约 v2.24）：已结束的兄弟运行也要算进「显示 / 不显示」。

全程走真接口（PUT /lanes/prefs/order + GET views/lanes）；世界的搭法同 test_lanes_active。
"""

from __future__ import annotations

import random

import pytest
from test_lanes_active import _ago, _ago_s, _attends, _get, _split, _win, w  # noqa: F401

ORDER = "/api/core/lanes/prefs/order"


def _put(client, rid, index=0):
    return client.put(ORDER, json={"runId": rid, "index": index, **_win()})  # 带页面读图用的窗口，任何时刻都和 GET 一致


def _put_today(client, rid, index=0):
    """不带窗口 = 缺省今天（老客户端）。"""
    return client.put(ORDER, json={"runId": rid, "index": index})


def _open_order(client):
    return [a["label"] for a in sorted(_get(client)["agents"], key=lambda a: a["rank"]) if a["endAt"] is None]


def test_open_idle_90min_but_sibling_done_20min_ago_is_shown_and_draggable(client, w):  # noqa: F811
    a = w.live("a", start_min=100, phases=[(90, "idle")])
    b = w.live("b", phases=[(5, "working")])
    w.closed("a", 30, 20)  # 同一条泳道刚做完：最后干活 = 20 分钟前 → 泳道显示
    shown, inactive, _ = _split(client)
    assert shown == {"a", "b"} and inactive == {}
    assert _put(client, a, 1).status_code == 200  # 修之前：只看在跑的 = 空闲 90 分钟 → 404
    assert _open_order(client) == ["b", "a"]
    assert _put(client, b, 1).status_code == 200 and _open_order(client) == ["a", "b"]


def test_open_idle_10min_but_newer_failed_sibling_collapses_and_does_not_offset_indexes(client, w):  # noqa: F811
    w.live("x", start_min=400, phases=[(5, "working")])
    bad = w.live("bad", start_min=45, phases=[(8, "idle")])  # 干了 35 分钟 + 兄弟 20 分钟 = normal 档
    y = w.live("y", start_min=200, phases=[(5, "working")])
    w.closed("bad", 40, 20, outcome="failed")  # 比在跑的更新、已失败 → 泳道折叠成出错
    shown, inactive, _ = _split(client)
    assert shown == {"x", "y"} and inactive["bad"]["reason"] == "error"
    assert _put(client, bad).status_code == 404  # 没显示着的不能拖
    assert _put(client, y, 1).status_code == 200  # 下标只数显示着的 x、y
    assert _open_order(client) == ["x", "y"]
    assert _put(client, y, 0).status_code == 200 and _open_order(client) == ["y", "x"]


def test_404_detail_mentions_not_shown(client, w):  # noqa: F811
    r = _put(client, "no-such-run")
    assert r.status_code == 404 and "未显示" in r.text


@pytest.mark.parametrize("seed", range(6))
def test_property_random_lanes_with_closed_siblings_follow_reference_model(client, w, seed):  # noqa: F811
    rng = random.Random(seed)
    ids: dict[str, str] = {}
    for i in range(7):
        label = f"L{i}"
        start = rng.randrange(150, 700)
        ph = rng.choice([(rng.randrange(1, 120), "idle"), (rng.randrange(1, 120), "working"),
                         (rng.randrange(1, 120), "error"), (rng.randrange(1, 120), "waiting_input")])
        watched = rng.random() < 0.35  # 人看过 12 分钟 → high 档：空闲 / 结束后留得更久
        if rng.random() < 0.2:  # 空闲了几个小时、但人刚看过（注意力结束于 30 秒到 50 分钟前）：靠注意力锚点仍显示
            start, ph, watched = rng.randrange(260, 700), (rng.randrange(150, 250), "idle"), True
        ids[label] = w.live(label, start_min=start, phases=[ph], seen_min=rng.randrange(1, 100),
                            attn=[(rng.randrange(30, 3000), 720)] if watched else ())
        kind = rng.choice(["none", "recent-done", "newer-failed", "old-failed"])
        if kind == "recent-done":
            w.closed(label, start - 1, rng.randrange(1, 40), attn=[(1800, 700)] if watched else ())
        elif kind == "newer-failed":
            w.closed(label, rng.randrange(2, start), 1, outcome="failed")
        elif kind == "old-failed":
            w.closed(label, start + 50, start + 40, outcome="failed")
    shown = [a["label"] for a in sorted(_get(client)["agents"], key=lambda a: a["rank"]) if a["endAt"] is None]
    assert len(shown) >= 2, shown  # 至少两条才拖得动；折叠的（可能为 0）一律 404，见下
    want, drags = list(shown), 0
    while drags < 70:
        pick, idx = rng.choice(want), rng.randrange(len(want) + 1)
        r = _put(client, ids[pick], idx)
        assert r.status_code == 200, (pick, idx, r.text)  # 显示着的卡片不能 404
        want.remove(pick)
        want.insert(min(idx, len(want)), pick)
        assert _open_order(client) == want, (seed, pick, idx)
        drags += 1
    for label in set(ids) - set(shown):
        assert _put(client, ids[label]).status_code == 404


def test_lost_run_with_heartbeats_last_work_stops_at_last_seen(client, w):  # noqa: F811
    rid = w.live("lostbeat", start_min=300, phases=[(299, "working")], lost=True, seen_min=100)
    next(r for r in w.open if r["runId"] == rid)["beatCount"] = 3
    shown, inactive, _ = _split(client)
    assert shown == set() and inactive["lostbeat"]["reason"] == "ended"  # 失联 = 没有在跑的运行
    assert inactive["lostbeat"]["lastWorkAt"] == _ago(100).isoformat()  # 失联：干活到最后一次信号，不是到现在


def test_capped_lost_run_dropped_seconds_stop_at_last_seen(client, w, monkeypatch):  # noqa: F811
    from app.modules.views import lane_cap  # noqa: PLC0415

    monkeypatch.setattr(lane_cap, "MAX_LIVE", 1)
    w.live("lostone", start_min=300, phases=[(299, "working")], lost=True, seen_min=200)
    w.live("fresh", start_min=10, phases=[(9, "working")])
    _, inactive, _ = _split(client)
    assert inactive["lostone"]["elapsedSeconds"] == 100 * 60  # 开始 300 分钟前 → 最后信号 200 分钟前


# ---- 窗口：拖拽按页面查询时的窗口数（页面在 00:00–03:00 查「昨天 + 今天」）----

def _midnight(monkeypatch):
    import test_lanes_active as base  # noqa: PLC0415

    t = base.datetime.combine(base.BASE, base.time(0, 10), base._tz())
    monkeypatch.setattr(base, "_now", lambda: t)  # 钉在 00:10；World / _ago 都读这个
    return {"from": (t.date() - base.timedelta(days=1)).isoformat(), "to": t.date().isoformat()}


def _two_day_open_order(client, win):
    return [a["label"] for a in sorted(_get(client, params=win)["agents"], key=lambda a: a["rank"]) if a["endAt"] is None]


def test_window_sibling_done_yesterday_shown_in_two_day_page_is_draggable_with_window(client, w, monkeypatch):  # noqa: F811
    win = _midnight(monkeypatch)
    a = w.live("a", start_min=100, phases=[(90, "idle")])  # 22:40 起空闲
    b = w.live("b", phases=[(5, "working")])
    w.closed("a", 30, 20)  # 23:40–23:50（昨天）：只在两天窗口里
    assert _two_day_open_order(client, win) == ["b", "a"]
    assert _put_today(client, a, 1).status_code == 404  # 缺省 = 今天：不变
    r = client.put(ORDER, json={"runId": a, "index": 0, "from": win["from"], "to": win["to"]})
    assert r.status_code == 200
    assert _two_day_open_order(client, win) == ["a", "b"]
    assert client.put(ORDER, json={"runId": b, "index": 0, "from": win["from"], "to": win["to"]}).status_code == 200
    assert _two_day_open_order(client, win) == ["b", "a"]


def test_window_failed_sibling_yesterday_collapses_in_two_day_page_404_and_no_offset(client, w, monkeypatch):  # noqa: F811
    win = _midnight(monkeypatch)
    w.live("x", start_min=400, phases=[(5, "working")])
    bad = w.live("bad", start_min=45, phases=[(8, "idle")])
    y = w.live("y", start_min=200, phases=[(5, "working")])
    w.closed("bad", 40, 20, outcome="failed")
    assert _two_day_open_order(client, win) == ["x", "y"]
    assert client.put(ORDER, json={"runId": bad, "index": 0, **{"from": win["from"], "to": win["to"]}}).status_code == 404
    assert client.put(ORDER, json={"runId": y, "index": 1, "from": win["from"], "to": win["to"]}).status_code == 200
    assert _two_day_open_order(client, win) == ["x", "y"]  # 只数显示着的 x、y，没有被 bad 顶偏
    assert client.put(ORDER, json={"runId": y, "index": 0, "from": win["from"], "to": win["to"]}).status_code == 200
    assert _two_day_open_order(client, win) == ["y", "x"]
    # 缺省（今天）：bad 没有昨天的兄弟，显示着 → 可拖（旧客户端行为不变）
    assert _put_today(client, bad).status_code == 200


@pytest.mark.parametrize("body", [{"from": "x"}, {"date": "2026-01-01", "from": "2026-01-01"},
                                  {"from": "2026-01-01", "to": "2026-01-20"}])
def test_window_invalid_params_same_error_as_get(client, w, body):  # noqa: F811
    rid = w.live("a", phases=[(5, "working")])
    got = client.get("/api/core/views/lanes", params=body)
    put = client.put(ORDER, json={"runId": rid, "index": 0, **body})
    assert got.status_code == put.status_code == 422
