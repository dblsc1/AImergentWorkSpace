"""手动排位的 ``live_order`` 与 ``get_lanes`` 同一条管线（契约 v2.24）：已结束的兄弟运行也要算进「显示 / 不显示」。

全程走真接口（PUT /lanes/prefs/order + GET views/lanes）；世界的搭法同 test_lanes_active。
"""

from __future__ import annotations

import random

import pytest
from test_lanes_active import _ago, _get, _split, w  # noqa: F401

ORDER = "/api/core/lanes/prefs/order"


def _put(client, rid, index=0):
    return client.put(ORDER, json={"runId": rid, "index": index})


def _open_order(client):
    return [a["label"] for a in sorted(_get(client)["agents"], key=lambda a: a["rank"]) if a["endAt"] is None]


def test_open_idle_90min_but_sibling_done_20min_ago_is_shown_and_draggable(client, w):  # noqa: F811
    a = w.live("a", phases=[(90, "idle")])
    b = w.live("b", phases=[(5, "working")])
    w.closed("a", 60, 20)  # 同一条泳道刚做完：最后干活 = 20 分钟前 → 泳道显示
    shown, inactive, _ = _split(client)
    assert shown == {"a", "b"} and inactive == {}
    assert _put(client, a, 1).status_code == 200  # 修之前：只看在跑的 = 空闲 90 分钟 → 404
    assert _open_order(client) == ["b", "a"]
    assert _put(client, b, 1).status_code == 200 and _open_order(client) == ["a", "b"]


def test_open_idle_10min_but_newer_failed_sibling_collapses_and_does_not_offset_indexes(client, w):  # noqa: F811
    w.live("x", start_min=400, phases=[(5, "working")])
    bad = w.live("bad", start_min=300, phases=[(10, "idle")])
    y = w.live("y", start_min=200, phases=[(5, "working")])
    w.closed("bad", 30, 20, outcome="failed")  # 比在跑的更新、已失败 → 泳道折叠成出错
    shown, inactive, _ = _split(client)
    assert shown == {"x", "y"} and inactive["bad"]["reason"] == "error"
    assert _put(client, bad).status_code == 404  # 没显示着的不能拖
    assert _put(client, y, 1).status_code == 200  # 下标只数显示着的 x、y
    assert _open_order(client) == ["x", "y"]
    assert _put(client, y, 0).status_code == 200 and _open_order(client) == ["y", "x"]


def test_404_detail_mentions_not_shown(client, w):  # noqa: F811
    r = _put(client, "no-such-run")
    assert r.status_code == 404 and "未显示" in r.text


@pytest.mark.parametrize("seed", range(5))
def test_property_random_lanes_with_closed_siblings_follow_reference_model(client, w, seed):  # noqa: F811
    rng = random.Random(seed)
    ids: dict[str, str] = {}
    for i in range(7):
        label = f"L{i}"
        start = rng.randrange(150, 700)
        ph = rng.choice([(rng.randrange(1, 120), "idle"), (rng.randrange(1, 120), "working"),
                         (rng.randrange(1, 120), "error"), (rng.randrange(1, 120), "waiting_input")])
        ids[label] = w.live(label, start_min=start, phases=[ph], seen_min=rng.randrange(1, 100))
        kind = rng.choice(["none", "recent-done", "newer-failed", "old-failed"])
        if kind == "recent-done":
            w.closed(label, start - 1, rng.randrange(1, 30))
        elif kind == "newer-failed":
            w.closed(label, rng.randrange(2, start), 1, outcome="failed")
        elif kind == "old-failed":
            w.closed(label, start + 50, start + 40, outcome="failed")
    shown = [a["label"] for a in sorted(_get(client)["agents"], key=lambda a: a["rank"]) if a["endAt"] is None]
    assert 2 <= len(shown) < 7, shown  # 世界里既有显示的也有折叠的
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
    assert shown == set() and inactive["lostbeat"]["reason"] == "idle"
    assert inactive["lostbeat"]["lastWorkAt"] == _ago(100).isoformat()  # 失联：干活到最后一次信号，不是到现在


def test_capped_lost_run_dropped_seconds_stop_at_last_seen(client, w, monkeypatch):  # noqa: F811
    from app.modules.views import lane_cap  # noqa: PLC0415

    monkeypatch.setattr(lane_cap, "MAX_LIVE", 1)
    w.live("lostone", start_min=300, phases=[(299, "working")], lost=True, seen_min=200)
    w.live("fresh", start_min=10, phases=[(9, "working")])
    _, inactive, _ = _split(client)
    assert inactive["lostone"]["elapsedSeconds"] == 100 * 60  # 开始 300 分钟前 → 最后信号 200 分钟前
