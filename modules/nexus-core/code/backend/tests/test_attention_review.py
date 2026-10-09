"""串行的注意力时间线——v2.17.1 的修正（契约「串行的注意力时间线」节 v2.17.1 注）。

审核 PR #87 查出的几处：注意力跟着时间线走（不靠宽限把别的运行的时间并进来）、attend 是真的并集、
同一设备并发的心跳不互相覆盖、注意力没记上就不推进时间线、老心跳（不带 spans）仍按 v2.4 的子串规则认、
spans 的每一段都校验。
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

import pytest

from test_attention import TERM, URL, _agent, _body, _line, _seconds, _send
from test_auto_track import API, DEV, _beat, _human, clock  # noqa: F401
from test_session_link import _start

USER = "u_local"


def _run(client, **body):
    return _start(client, **body)["runId"]


def _raw(run_id):
    from app.repo import get_db  # noqa: PLC0415

    return get_db()["agent_runs"].find_one({"runId": run_id}).get("interactions") or []


def _watched(client):
    """时间线上每条运行被看的秒数（``human.presence`` 里带 runId 的段）。"""
    out: dict[str, list[float]] = {}
    for s in _human(client)["presence"]:
        if s["runId"]:
            out.setdefault(s["runId"], []).extend(_seconds([s]))
    return out


# ─────────────────────────────────────────── 1：注意力之和不超过墙上的时间


def test_attention_across_runs_never_exceeds_wall_time(client, clock):
    clock(-60)
    a, b = _run(client, label="aaa"), _run(client, label="bbb")
    now = clock(0)
    _send(client, now, [(10, 5, "aaa"), (5, 1, "bbb"), (4, 4, "aaa")])   # 中间那 1 秒在看 b：不能算进 a
    att = {r: _seconds(_agent(client, r)["attention"]) for r in (a, b)}
    assert att == {a: [5, 4], b: [1]}
    assert sum(map(sum, att.values())) <= 10
    assert att == _watched(client), "蓝条就是时间线上对上这条运行的那几段"


def test_attention_follows_the_timeline_across_beats(client, clock):
    """同一个窗口跨拍接着看（两拍之间差半秒的抖动）：时间线并成一段，蓝条也是同一段，不多不少。"""
    clock(-60)
    a = _run(client, label="aaa")
    _send(client, clock(0), [(5, 5, "aaa")])
    _send(client, clock(5.5), [(5, 5, "aaa")])
    assert _seconds(_agent(client, a)["attention"]) == [10.5] == _watched(client)[a]
    assert sum(i["kind"] == "attend" for i in _raw(a)) == 1


def test_random_switching_keeps_the_invariant(client, clock):
    rng = random.Random(87)
    clock(-600)
    runs = [_run(client, label=name) for name in ("aaa", "bbb", "ccc")]
    for beat in range(40):
        now = clock(-400 + 5 * beat)
        cuts = sorted(rng.uniform(0.2, 4.8) for _ in range(rng.randint(0, 3)))
        edges = [5.0, *reversed(cuts), 0.0]
        _send(client, now, [(round(x, 3), round(x - y, 3), rng.choice(["aaa", "bbb", "ccc", "vim"]))
                            for x, y in zip(edges, edges[1:]) if round(x - y, 3) > 0])
    att = {r: _seconds(_agent(client, r)["attention"]) for r in runs}
    assert sum(map(sum, att.values())) <= 200 + 1e-6
    watched = _watched(client)
    for r in runs:
        assert sum(att[r]) == pytest.approx(sum(watched.get(r, [])), abs=1e-3)


# ─────────────────────────────────────────── 3：attend 是并集，乱序也一样


def test_attend_is_a_true_union_in_any_order(client, clock):
    from app.modules.timer import service as timer  # noqa: PLC0415

    t0 = clock(-100)
    run = _run(client, label="aaa")

    def at(a, b):
        return (t0 + timedelta(seconds=a), t0 + timedelta(seconds=b))

    def stored():
        return [((datetime.fromisoformat(i["at"]) - t0).total_seconds(),
                 (datetime.fromisoformat(i["until"]) - t0).total_seconds()) for i in _raw(run)]

    zero = timedelta(0)
    timer.record_attend(USER, run, [at(10, 15)], zero)
    timer.record_attend(USER, run, [at(0, 5)], zero)             # 晚到的更早的一段：留着，排在前面
    assert stored() == [(0, 5), (10, 15)]
    timer.record_attend(USER, run, [at(30, 40)], zero)
    timer.record_attend(USER, run, [at(3, 20)], zero)            # 往前、往后都伸出去：两头都算
    assert stored() == [(0, 20), (30, 40)]
    timer.record_attend(USER, run, [at(20, 30)], zero)           # 首尾相接：并
    assert stored() == [(0, 40)]
    timer.record_attend(USER, run, [at(5, 9)], zero)             # 已经盖住的：不变
    assert stored() == [(0, 40)]
    timer.record_attend(USER, run, [at(42, 43)], zero)           # 差 2 秒：不是同一段
    assert stored() == [(0, 40), (42, 43)]


# ─────────────────────────────────────────── 2：空的时间线


def test_empty_spans_on_a_new_device_do_not_break_the_readers(client, clock):
    now = clock(0)
    body = {**_body(now, []), "app": TERM, "title": "A"}         # 新设备、没离开、spans 是空的
    assert client.post(URL, json=body).status_code == 200
    assert _line(now) == []
    current = client.get(f"{API}/views/current")
    lanes = client.get(f"{API}/views/lanes")
    assert current.status_code == 200 and lanes.status_code == 200
    assert current.json()["focus"] is None and lanes.json()["human"]["focus"] is None
    assert lanes.json()["human"]["presence"] == []
    _send(client, clock(5), [(5, 5, "A")])                       # 下一拍带了段：照常
    assert client.get(f"{API}/views/current").json()["focus"]["title"] == "A"


# ─────────────────────────────────────────── 4：同一设备并发的两拍


def test_concurrent_beats_of_one_device_do_not_lose_spans(client, clock, monkeypatch):
    from app.modules.activity import presence, repo  # noqa: PLC0415

    t0 = clock(0)
    _send(client, t0, [(5, 5, "W")])
    now = clock(10)
    real, raced = repo.presence_get, []

    def racing(user, device_id):
        doc = real(user, device_id)
        if not raced:                                            # 这一拍读完、还没写：另一拍整个做完
            raced.append(1)
            presence.heartbeat(DEV, TERM, "X", False, None,
                               [{"app": TERM, "title": "X", "from": now - timedelta(seconds=10), "seconds": 5.0}], now)
        return doc

    monkeypatch.setattr(repo, "presence_get", racing)
    _send(client, now, [(5, 5, "Y")])
    assert raced and _line(now) == [(-15, -10, "W", False), (-10, -5, "X", False), (-5, 0, "Y", False)]


def test_presence_contention_fails_loudly(client, clock, monkeypatch):
    from app.modules.activity import repo  # noqa: PLC0415

    monkeypatch.setattr(repo, "presence_cas", lambda doc, version: False)
    with pytest.raises(RuntimeError):
        _send(client, clock(0), [(5, 5, "A")])


# ─────────────────────────────────────────── 5：注意力没记上，时间线不推进


def test_failed_attend_does_not_advance_the_timeline(client, clock, monkeypatch):
    from app.modules.activity import presence  # noqa: PLC0415

    clock(-60)
    run = _run(client, label="aaa")
    now = clock(0)
    real, failed = presence.timer_service.record_attend, []

    def flaky(*args):
        if not failed:
            failed.append(1)
            raise RuntimeError("写不进去")
        return real(*args)

    monkeypatch.setattr(presence.timer_service, "record_attend", flaky)
    with pytest.raises(RuntimeError):
        _send(client, now, [(5, 5, "aaa")])
    assert not _line(now), "这一拍整个失败：时间线没往前走"
    now = clock(5)
    _send(client, now, [(10, 10, "aaa")])                        # 检测程序下一拍往回多带一截
    assert _seconds(_agent(client, run)["attention"]) == [10] == _watched(client)[run]


def test_attend_contention_fails_loudly(client, clock, monkeypatch):
    from app.modules.timer import repo  # noqa: PLC0415

    clock(-60)
    _run(client, label="aaa")
    now = clock(0)
    monkeypatch.setattr(repo, "cas_agent_run", lambda *a, **k: False)
    with pytest.raises(RuntimeError):
        _send(client, now, [(5, 5, "aaa")])
    assert not _line(now)


# ─────────────────────────────────────────── 6：老心跳（不带 spans）仍是 v2.4 的子串规则（另见 test_presence.py）


def test_spanless_beat_with_one_hit_marks_the_span(client, clock):
    clock(-10)
    run = _run(client, match="garden")
    clock(0)
    _beat(client)                                                # plot.gd — garden
    assert _human(client)["presence"][-1]["runId"] == run


def test_span_beats_keep_the_strict_equality_rule(client, clock):
    clock(-10)
    run = _run(client, match="garden")
    _send(client, clock(0), [(5, 5, "plot.gd — garden")])        # 带 spans：包含不算，要相等
    assert _raw(run) == []
    _send(client, clock(5), [(5, 5, "✳ garden")])
    assert _seconds(_agent(client, run)["attention"]) == [5]


# ─────────────────────────────────────────── 8：spans 的每一段都校验


S = datetime(2026, 10, 9, 2, 0, 5, tzinfo=timezone.utc)


def _spans(*pairs):
    """pairs = (起点距 sentAt 的秒, 停留秒数)。"""
    return [{"app": TERM, "title": "A", "from": (S + timedelta(seconds=a)).isoformat(), "seconds": d} for a, d in pairs]


def _walk_back(first, step_back, count):
    """每一段都比上一段的终点早一点（各自都在 2 秒的容差里）：一步步往回走。"""
    pairs, end = [first], first[0] + first[1]
    for _ in range(count):
        pairs.append((end - step_back, 0.1))
        end = end - step_back + 0.1
    return pairs


@pytest.mark.parametrize("pairs", [
    _walk_back((-120, 0.5), 1.9, 3),                 # 起点一步步漂到 120 秒之前
    _walk_back((-1, 31), 1.9, 16),                   # 中间的段伸到 sentAt 之后 30 秒，最后一段又走回来
    [(-10, 1), (-10.5, 2.5)],                        # 起点倒着排
    [(-10, 5), (-8, 1), (-6.5, 1)],                  # 重叠超过容差
    [(-5, 8)],                                       # 晚于 sentAt
    [(-123, 1)],                                     # 早于 120 秒
], ids=["drift-back", "future-middle", "backwards", "overlap", "future", "too-old"])
def test_every_span_is_validated(client, pairs):
    from app.repo import get_db  # noqa: PLC0415

    body = {"deviceId": DEV, "app": TERM, "title": "A", "afk": False, "sentAt": S.isoformat(), "spans": _spans(*pairs)}
    assert client.post(URL, json=body).status_code == 422
    assert get_db()["activity_presence"].count_documents({}) == 0


def test_accepted_spans_are_always_in_bounds_and_ordered():
    """随机造拍子：收下的每一拍，每一段都在 [sentAt − 120 秒, sentAt]（容 2 秒）里、起点不倒退、重叠不超过 2 秒。"""
    from pydantic import ValidationError  # noqa: PLC0415

    from app.modules.activity.presence_router import PresenceIn  # noqa: PLC0415

    rng, accepted, slack = random.Random(2017), 0, 2.0
    for _ in range(3000):
        pos, pairs = rng.uniform(-125, -1), []
        for _ in range(rng.randint(1, 8)):
            dur = rng.choice([0.05, 0.5, 3, 20, 70])
            pairs.append((pos, dur))
            pos += dur + rng.choice([0, 0, 0.3, -0.5, -1.9, -2.5, 5, -30])
        body = {"deviceId": DEV, "app": TERM, "title": "A", "afk": False, "sentAt": S.isoformat(), "spans": _spans(*pairs)}
        try:
            PresenceIn.model_validate(body)
        except ValidationError:
            continue
        accepted += 1
        for i, (a, d) in enumerate(pairs):
            assert -120 - slack <= a and a + d <= slack + 1e-6, pairs
            if i:
                assert a >= pairs[i - 1][0] and a >= sum(pairs[i - 1]) - slack - 1e-6, pairs
    assert accepted > 200, "造出来的拍子得有不少是合格的，否则这条测试什么都没测"
