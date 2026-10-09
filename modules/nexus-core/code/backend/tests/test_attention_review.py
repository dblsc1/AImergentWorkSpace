"""串行的注意力时间线——v2.17.1 的修正（契约「串行的注意力时间线」节 v2.17.1 注）。

审核 PR #87 查出的几处：注意力跟着时间线走（不靠宽限把别的运行的时间并进来）、attend 是真的并集、
同一设备并发的心跳不互相覆盖、注意力没记上就不推进时间线、老心跳（不带 spans）仍按 v2.4 的子串规则认、
spans 的每一段都校验。
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

import pytest

from test_attention import TERM, URL, _agent, _body, _db, _line, _seconds, _send
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

    monkeypatch.setattr(repo, "presence_cas", lambda doc, prev: None)
    with pytest.raises(RuntimeError):
        _send(client, clock(0), [(5, 5, "A")])


# ─────────────────────────────────────────── 5：注意力跟着已提交的时间线（outbox，v2.17.2 修订）


def test_failed_attend_stays_pending_and_the_next_beat_recovers_it(client, clock, monkeypatch):
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
    _send(client, now, [(5, 5, "aaa")])                          # 时间线已落盘，这一拍仍是 2xx
    assert _line(now) == [(-5, 0, "aaa", False)]
    assert _agent(client, run)["attention"] == []
    assert _db()["activity_presence"].find_one({})["pendingAttend"], "没记上的留在 outbox"
    now = clock(5)
    _send(client, now, [(5, 5, "bbb")])                          # 下一拍先补上上一拍的注意力
    assert _seconds(_agent(client, run)["attention"]) == [5]
    assert _db()["activity_presence"].find_one({})["pendingAttend"] == []


def test_attend_failure_never_loses_attention_across_many_beats(client, clock, monkeypatch):
    from app.modules.timer import repo  # noqa: PLC0415

    clock(-60)
    run = _run(client, label="aaa")
    real = repo.cas_agent_run
    monkeypatch.setattr(repo, "cas_agent_run", lambda *a, **k: False)
    for i in range(3):
        _send(client, clock(5 * i), [(5, 5, "aaa")])             # 争用重试用尽：抛错被吞，留在 outbox
    assert _db()["activity_presence"].find_one({})["pendingAttend"]
    monkeypatch.setattr(repo, "cas_agent_run", real)
    _send(client, clock(15), [(5, 5, "vim")])
    assert _seconds(_agent(client, run)["attention"]) == [15]


def test_cas_loser_does_not_write_attention_for_spans_that_did_not_commit(client, clock, monkeypatch):
    """两拍并发读到同一份文档，一拍说 [0,10] 看 aaa，另一拍说 [0,10] 看 bbb：先提交的算，后提交的被裁光。"""
    from app.modules.activity import presence, repo  # noqa: PLC0415

    clock(-60)
    a, b = _run(client, label="aaa"), _run(client, label="bbb")
    now = clock(0)
    real, raced = repo.presence_get, []

    def racing(user, device_id):
        doc = real(user, device_id)
        if not raced:
            raced.append(1)
            presence.heartbeat(DEV, TERM, "bbb", False, None,
                               [{"app": TERM, "title": "bbb", "from": now - timedelta(seconds=10), "seconds": 10.0}], now)
        return doc

    monkeypatch.setattr(repo, "presence_get", racing)
    _send(client, now, [(10, 10, "aaa")])
    assert raced
    att = {r: _seconds(_agent(client, r)["attention"]) for r in (a, b)}
    assert att == {a: [], b: [10]} and sum(map(sum, att.values())) <= 10


def test_out_of_order_beats_of_one_device_contribute_nothing_for_covered_time(client, clock, monkeypatch):
    """B：时间线只增不倒（定义好的行为）。Y 先提交 [-5,0]；X 的 [-10,-5] 晚到，只算已提交末尾之后的——什么都不剩。"""
    from app.modules.activity import presence, repo  # noqa: PLC0415

    clock(-60)
    x, y = _run(client, label="xxx"), _run(client, label="yyy")
    now = clock(0)
    real, raced = repo.presence_get, []

    def racing(user, device_id):
        doc = real(user, device_id)
        if not raced:
            raced.append(1)
            presence.heartbeat(DEV, TERM, "yyy", False, None,
                               [{"app": TERM, "title": "yyy", "from": now - timedelta(seconds=5), "seconds": 5.0}], now)
        return doc

    monkeypatch.setattr(repo, "presence_get", racing)
    _send(client, now, [(10, 5, "xxx")])
    assert _line(now) == [(-5, 0, "yyy", False)]
    assert _seconds(_agent(client, x)["attention"]) == [] and _seconds(_agent(client, y)["attention"]) == [5]


# ─────────────────────────────────────────── C：老的一拍晚到，不改写当前状态


@pytest.mark.parametrize("old_afk", [False, True], ids=["spanless", "afk-marker"])
def test_delayed_older_beat_does_not_rewrite_current_state(client, clock, monkeypatch, old_afk):
    from app.modules.activity import presence, repo  # noqa: PLC0415

    t0 = clock(0)
    real, raced = repo.presence_get, []

    def racing(user, device_id):
        doc = real(user, device_id)
        if not raced:
            raced.append(1)
            clock(5)                                              # 更新的一拍（5 秒后收到）先提交
            presence.heartbeat(DEV, TERM, "new", False)
            clock(0)
        return doc

    monkeypatch.setattr(repo, "presence_get", racing)
    presence.heartbeat(DEV, "" if old_afk else TERM, "" if old_afk else "old", old_afk)   # 收到时刻 t0，晚了才写
    assert raced
    doc = _db()["activity_presence"].find_one({})
    assert (doc["app"], doc["title"], doc["afk"]) == (TERM, "new", False)
    assert doc["lastAt"].replace(tzinfo=timezone.utc) == t0 + timedelta(seconds=5)
    ends = [s["to"] for s in doc["spans"]]
    assert ends == sorted(ends) and [s["title"] for s in doc["spans"]] == ["new"]


# ─────────────────────────────────────────── D：同一份请求体再发一遍


def test_replaying_the_identical_body_cannot_count_more_than_wall_time(client, clock):
    """检测程序从不重发同一份体（失败就丢，下一拍带新的 sentAt）；即便重放，也只算已提交末尾之后的。"""
    clock(-60)
    run = _run(client, label="aaa")
    t0 = clock(0)
    body = _body(t0, [(5, 5, "aaa")])
    assert client.post(URL, json=body).status_code == 200
    clock(5)
    assert client.post(URL, json=body).status_code == 200
    assert client.post(URL, json=body).status_code == 200        # 同一刻再发：什么都不多算
    total = sum(_seconds(_agent(client, run)["attention"]))
    assert total <= 10 + 1e-6                                     # ≤ 最早一段的起点到最后一次收到的墙上时间
    assert total == pytest.approx(sum(_watched(client)[run]))
    line = _line(t0)
    assert all(a[1] <= b[0] + 1e-6 for a, b in zip(line, line[1:])), "时间线仍不重叠"


# ─────────────────────────────────────────── E：并集是规范形、幂等


def _attend_stored(run, t0):
    return [((datetime.fromisoformat(i["at"]) - t0).total_seconds(),
             (datetime.fromisoformat(i["until"]) - t0).total_seconds()) for i in _raw(run)]


def test_union_gap_reaches_only_from_the_new_interval_and_is_idempotent(client, clock):
    from app.modules.timer import service as timer  # noqa: PLC0415

    t0 = clock(-300)
    run = _run(client, label="aaa")

    def at(a, b):
        return (t0 + timedelta(seconds=a), t0 + timedelta(seconds=b))

    exact = timedelta(0)
    for a, b in ((0, 1), (40, 41), (80, 81)):
        timer.record_attend(USER, run, [at(a, b)], exact)
    timer.record_attend(USER, run, [at(20, 21)], exact)         # 精确插入：不去并相距 < 45 秒的旧段
    assert _attend_stored(run, t0) == [(0, 1), (20, 21), (40, 41), (80, 81)]
    legacy = timedelta(seconds=45)
    timer.record_attend(USER, run, [at(120, 120)], legacy)       # 老心跳的点只并上它够得着的
    assert _attend_stored(run, t0) == [(0, 1), (20, 21), (40, 41), (80, 120)]   # 点够不着 (40,41)：相距 79 秒，不串起来
    timer.record_attend(USER, run, [at(120, 120)], legacy)       # 再记一遍：不变
    assert _attend_stored(run, t0) == [(0, 1), (20, 21), (40, 41), (80, 120)]
    timer.record_attend(USER, run, [at(60, 60)], legacy)         # 够得着 (20,21)、(40,41)、(80,120)；(0,1) 相距 59 秒，够不着
    once = _attend_stored(run, t0)
    assert once == [(0, 1), (20, 120)]
    timer.record_attend(USER, run, [at(60, 60)], legacy)
    assert _attend_stored(run, t0) == once


def test_legacy_point_does_not_cascade_through_old_spans(client, clock):
    from app.modules.timer import service as timer  # noqa: PLC0415

    t0 = clock(-300)
    run = _run(client, label="aaa")
    for a, b in ((0, 1), (40, 41), (80, 81)):
        timer.record_attend(USER, run, [(t0 + timedelta(seconds=a), t0 + timedelta(seconds=b))], timedelta(0))
    timer.record_attend(USER, run, [(t0 + timedelta(seconds=20),) * 2], timedelta(seconds=45))
    assert _attend_stored(run, t0) == [(0, 41), (80, 81)]        # 点 20 并上两头；并成的这一段不再往外够 (80,81)
    timer.record_attend(USER, run, [(t0 + timedelta(seconds=20),) * 2], timedelta(seconds=45))
    assert _attend_stored(run, t0) == [(0, 41), (80, 81)]


def test_several_new_intervals_merge_the_same_in_any_order(client, clock):
    from app.modules.timer import service as timer  # noqa: PLC0415

    t0 = clock(-300)
    runs = [_run(client, label="aaa"), _run(client, label="bbb")]
    pts = [(t0 + timedelta(seconds=x),) * 2 for x in (0, 30, 60, 200)]
    for run, order in zip(runs, (pts, pts[::-1])):
        timer.record_attend(USER, run, order, timedelta(seconds=45))
    assert _attend_stored(runs[0], t0) == _attend_stored(runs[1], t0) == [(0, 60), (200, 200)]


# ─────────────────────────────────────────── F：超过上限时并拢而不是丢


def test_attend_cap_coalesces_instead_of_dropping(client, clock, monkeypatch):
    from app.modules.timer import agent_phases, service as timer  # noqa: PLC0415

    monkeypatch.setattr(agent_phases, "MAX_ATTENDS", 3)
    t0 = clock(-300)
    run = _run(client, label="aaa")
    for a, b in ((0, 1), (10, 11), (20, 21), (100, 101), (200, 201)):
        timer.record_attend(USER, run, [(t0 + timedelta(seconds=a), t0 + timedelta(seconds=b))], timedelta(0))
    got = _attend_stored(run, t0)
    assert len(got) == 3 and got[-1] == (200, 201), "最新的一段总是记下"
    assert got[0] == (0, 21) or got[0] == (0, 11)                # 最近的相邻两段先并拢


# ─────────────────────────────────────────── G：ABA


def test_stale_cas_does_not_match_a_recreated_document():
    from app.modules.activity import repo  # noqa: PLC0415

    doc = {"user": USER, "deviceId": DEV, "lastAt": datetime.now(timezone.utc), "app": "", "title": "", "afk": False,
           "spans": []}
    assert repo.presence_cas(doc, None)
    stale = repo.presence_get(USER, DEV)                          # 读到 v=1
    assert repo.presence_cas({**doc, "title": "x"}, {"v": 5, "gen": "nope"}) is None, "版本 / 代号对不上"
    repo.presence_delete(USER, [DEV])                             # 被 20 台上限挤掉
    assert repo.presence_cas({**doc, "title": "other"}, None)     # 又以 v=1 重建，内容不同
    assert repo.presence_get(USER, DEV)["v"] == stale["v"]
    assert repo.presence_cas({**doc, "title": "stale"}, stale) is None
    assert repo.presence_get(USER, DEV)["title"] == "other"


def test_replacement_never_upserts():
    from app.modules.activity import repo  # noqa: PLC0415

    doc = {"user": USER, "deviceId": DEV, "lastAt": datetime.now(timezone.utc), "app": "", "title": "", "afk": False,
           "spans": []}
    assert repo.presence_cas(doc, {"v": 1, "gen": "gone"}) is None
    assert repo.presence_get(USER, DEV) is None


# ─────────────────────────────────────────── H：app / title 的长度


def test_long_strings_are_truncated_and_absurd_ones_rejected(client, clock):
    now = clock(0)
    long_title = "python3 -c " + "x" * 3000                       # 终端把整条命令放进标题
    body = _body(now, [(5, 5, long_title)])
    body["title"] = long_title
    assert client.post(URL, json=body).status_code == 200
    doc = _db()["activity_presence"].find_one({})
    assert len(doc["title"]) <= 512 and all(len(s["title"]) <= 512 for s in doc["spans"])
    huge = {**_body(now, [(5, 5, "x" * 20000)]), "title": "ok"}
    assert client.post(URL, json=huge).status_code == 422, "span 的 title 过大"
    assert client.post(URL, json={**_body(now, [(5, 5, "ok")]), "app": "a" * 20000}).status_code == 422
    assert client.post(URL, json={**_body(now, [(5, 5, "ok")]), "title": "t" * 20000}).status_code == 422
    assert client.post(URL, json={**_body(now, [(5, 5, "ok")]), "title": "t" * 16384}).status_code == 200


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
