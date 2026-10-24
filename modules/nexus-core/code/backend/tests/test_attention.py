"""串行的注意力时间线（契约 v2.17 同名节）。

仓主 2026-10-09：「不要按组计算」「每 5 秒汇报一次，每次汇报里带各个窗口的停留时间」「人的注意力是严格串行的」
「泳道里 AI 需要有蓝色的条，代表人类把注意力放在他们身上的时间」。

要害：**每台设备一条时间线，段不重叠、不合计**；**时间一律是服务端的**（设备的钟差多少都落在同一处）；
**窗口是不是某条代理会话只有一条规则**（v2.13 的归一化相等）；**老检测程序照旧能用**。
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pytest

from test_auto_track import API, B, DEV, _beat, _guess, _human, _track, clock, world  # noqa: F401
from test_session_link import AGENTS, _start

URL = f"{API}/activity/presence"
TERM = "ptyxis"


def _db():
    from app.repo import get_db  # noqa: PLC0415

    return get_db()


def _body(now, spans, *, skew=0, afk=False, device=DEV):
    """spans = [(距设备「此刻」多少秒开始, 停留秒数, 标题[, guess])]；设备的钟 = 服务端的钟 + skew。"""
    sent = now + timedelta(seconds=skew)
    out = []
    for ago, seconds, title, *guess in spans:
        out.append({"app": TERM, "title": title, "from": (sent - timedelta(seconds=ago)).isoformat(),
                    "seconds": seconds, **({"guess": guess[0]} if guess and guess[0] else {})})
    cur = out[-1] if out and not afk else {"app": "", "title": ""}
    return {"deviceId": device, "app": cur["app"], "title": cur["title"], "afk": afk,
            **({"guess": cur["guess"]} if cur.get("guess") else {}), "sentAt": sent.isoformat(), "spans": out}


def _send(client, now, spans, headers=None, expect=200, **kw):
    resp = client.post(URL, json=_body(now, spans, **kw), headers=headers or {})
    assert resp.status_code == expect, resp.text


def _line(now, device=DEV, user="u_local"):
    """存下的时间线：[(起点距 now 的秒, 终点距 now 的秒, 标题, afk)]。"""
    doc = _db()["activity_presence"].find_one({"user": user, "deviceId": device})

    def rel(moment):  # mongo 读回来不带时区
        return (moment.replace(tzinfo=timezone.utc) - now).total_seconds()

    return [(rel(s["from"]), rel(s["to"]), s["title"], s["afk"]) for s in doc["spans"]] if doc else None


def _agent(client, run_id, headers=None):
    body = client.get(f"{API}/views/lanes", headers=headers or {}).json()
    for x in body["inactiveAgents"]:  # v2.25：刚结束的泳道立刻折叠；这里关心的是运行本身，先置顶（置顶永远显示）
        client.put(f"{API}/lanes/prefs/agent", json={"agent": x["agent"] or "", "label": x["label"] or "", "pinned": True,
                                                    "unverified": x["unverified"]}, headers=headers or {})
    body = client.get(f"{API}/views/lanes", headers=headers or {}).json() if body["inactiveAgents"] else body
    return next(a for a in body["agents"] if a["runId"] == run_id)


def _seconds(spans):
    return [(datetime.fromisoformat(a["to"]) - datetime.fromisoformat(a["from"])).total_seconds() for a in spans]


# ─────────────────────────────────────────── 校验


GOOD = {"app": TERM, "title": "A", "from": "2026-10-09T10:00:00+08:00", "seconds": 3}
SENT = "2026-10-09T10:00:05+08:00"


@pytest.mark.parametrize("patch", [
    {"sentAt": None},                                             # 带 spans 必须带 sentAt
    {"sentAt": 1790000000}, {"sentAt": "2026-10-09T10:00:05"},    # 数字、不带偏移
    {"spans": "x"}, {"spans": {"$ne": 1}}, {"spans": [GOOD] * 33},
    {"spans": [{**GOOD, "title": 123}]}, {"spans": [{**GOOD, "title": {"$gt": ""}}]},
    {"spans": [{**GOOD, "app": None}]}, {"spans": [{k: v for k, v in GOOD.items() if k != "from"}]},
    {"spans": [{**GOOD, "seconds": 0}]}, {"spans": [{**GOOD, "seconds": -1}]}, {"spans": [{**GOOD, "seconds": "3"}]},
    {"spans": [{**GOOD, "seconds": True}]}, {"spans": [{**GOOD, "seconds": 121}]},
    {"spans": [{**GOOD, "from": 1790000000}]}, {"spans": [{**GOOD, "from": "2026-10-09T10:00:00"}]},
    {"spans": [{**GOOD, "from": "昨天"}]},
    {"spans": [GOOD, {**GOOD, "from": "2026-10-09T09:59:55+08:00"}]},   # 乱序 / 重叠
    {"spans": [{**GOOD, "seconds": 30}]},                              # 晚于 sentAt
    {"spans": [{**GOOD, "from": "2026-10-09T09:57:00+08:00"}]},        # 比 sentAt 早 120 秒以上
    {"spans": [{**GOOD, "from": "0001-01-01T00:00:00+00:00"}]},
    {"spans": [{**GOOD, "from": "9999-12-31T23:59:59+00:00"}]},        # 加减就越界的时刻：422，不是 500
    {"sentAt": "0001-01-01T00:00:01+00:00"}, {"sentAt": "9999-12-31T23:59:59+00:00"},
    {"spans": [{**GOOD, "guess": {"taskId": "t", "projectId": "p", "confidence": 0.9, "classifier": "rules"}}]},
])
def test_malformed_spans_are_rejected_whole(client, patch):
    body = {"deviceId": DEV, "app": TERM, "title": "A", "afk": False, "sentAt": SENT, "spans": [GOOD], **patch}
    assert client.post(URL, json={k: v for k, v in body.items() if v is not None}).status_code == 422
    assert _db()["activity_presence"].count_documents({}) == 0


def test_empty_spans_and_unknown_keys_are_fine(client, clock):
    now = clock(0)
    body = {**_body(now, []), "app": TERM, "title": "A", "truncated": True, "futureKey": 1}
    assert client.post(URL, json=body).status_code == 200
    assert _line(now) == []  # 带了 spans（空的）：不再按老心跳补一个「此刻」


# ─────────────────────────────────────────── 时间线：串行、合并、裁掉重发的、保留期、上限


@pytest.mark.parametrize("skew", [0, 600, -600, 86400 * 400])
def test_device_clock_does_not_matter(client, clock, skew):
    now = clock(0)
    _send(client, now, [(6, 3, "A"), (3, 3, "B")], skew=skew)
    assert _line(now) == [(-6, -3, "A", False), (-3, 0, "B", False)]


def test_beats_join_into_one_serial_timeline(client, clock):
    t0 = clock(0)
    _send(client, t0, [(6, 3, "A"), (3, 3, "B")])
    now = clock(5)
    _send(client, now, [(5, 2, "B"), (3, 3, "A")])         # B 接着上一拍：并成一段；A 是切回来的：新的一段
    assert _line(now) == [(-11, -8, "A", False), (-8, -3, "B", False), (-3, 0, "A", False)]
    _send(client, now, [(5, 2, "B"), (3, 3, "A")])         # 同一拍重发：一秒都不多记
    assert _line(now) == [(-11, -8, "A", False), (-8, -3, "B", False), (-3, 0, "A", False)]
    now = clock(15)
    _send(client, now, [(20, 12, "A"), (8, 8, "C")])       # 上一拍丢了，这一拍往回带：已有的部分裁掉，只补新的
    assert _line(now)[-2:] == [(-13, -8, "A", False), (-8, 0, "C", False)]
    now = clock(40)
    _send(client, now, [(5, 5, "C")])                      # 同一个窗口，但中间 20 秒不在（离开被扣掉了）：另一段
    assert _line(now)[-2:] == [(-33, -25, "C", False), (-5, 0, "C", False)]
    spans = _line(now)
    assert all(a[1] <= b[0] for a, b in zip(spans, spans[1:])), "段与段不重叠"


def test_afk_beat_and_old_style_beat_still_work_after_spans(client, clock):
    t0 = clock(0)
    _send(client, t0, [(3, 3, "A")])
    for sec in (5, 10):
        now = clock(sec)
        assert client.post(URL, json={"deviceId": DEV, "app": "", "title": "", "afk": True}).status_code == 200
    assert _line(now) == [(-13, -10, "A", False), (-5, 0, "", True)]
    now = clock(15)
    _send(client, now, [(8, 2, "A")], afk=True)             # 离开的那一拍带的段早于时间线末尾：裁掉，离开接着延长
    assert _line(now) == [(-18, -15, "A", False), (-10, 0, "", True)]
    now = clock(20)
    _beat(client, title="old")                             # 老检测程序：一个「此刻」
    assert _line(now)[-1] == (0, 0, "old", False)
    now = clock(25)
    _send(client, now, [(5, 5, "old")])                    # 换成新检测程序：接在后面，不重叠
    assert _line(now)[-1][:3] == (-5, 0, "old")


def test_retention_and_cap(client, clock, monkeypatch):
    from app.modules.activity import presence  # noqa: PLC0415

    t0 = clock(-7300)
    _send(client, t0, [(6, 3, "old"), (3, 3, "kept")])
    now = clock(-7190)
    _send(client, now, [(3, 3, "new")])
    assert [s[2] for s in _line(now)] == ["old", "kept", "new"]
    now = clock(0)
    _send(client, now, [(3, 3, "now")])                    # 2 小时之前的掉出去
    assert [s[2] for s in _line(now)] == ["new", "now"]
    monkeypatch.setattr(presence, "MAX_SPANS", 3)
    now = clock(10)
    _send(client, now, [(8, 2, "a"), (6, 2, "b"), (4, 2, "c"), (2, 2, "d")])
    assert [s[2] for s in _line(now)] == ["b", "c", "d"]


def test_lanes_presence_exposes_the_fine_spans(client, clock):
    now = clock(0)
    _send(client, now, [(9, 3, "A"), (6, 3, "B"), (3, 3, "A")])
    spans = _human(client)["presence"]
    assert [(s["title"], s["afk"], s["runId"]) for s in spans] == [("A", False, None), ("B", False, None), ("A", False, None)]
    assert _seconds(spans) == [3, 3, 3]
    assert all(set(s) == {"deviceId", "from", "to", "app", "title", "afk", "runId"} for s in spans)


# ─────────────────────────────────────────── focus：永远只有当前这一个窗口


def test_focus_under_three_second_switching(client, clock):
    """每 3 秒切一次：focus 是最后那个窗口，since 是这一次切过来的时刻（切走再回来重新起算），
    dwellSeconds 只数这个窗口自己的秒数。"""
    for i in range(6):                                      # 6 拍 × 6 秒：A 3 秒、B 3 秒
        now = clock(-30 + 6 * i)
        _send(client, now, [(6, 3, "A"), (3, 3, "B")])
    focus = client.get(f"{API}/views/current").json()["focus"]
    assert (focus["state"], focus["title"]) == ("present", "B")
    assert datetime.fromisoformat(focus["since"]) == now - timedelta(seconds=3)
    assert focus["dwellSeconds"] == 18
    now = clock(5)
    _send(client, now, [(5, 2, "B"), (3, 3, "A")])
    focus = client.get(f"{API}/views/current").json()["focus"]
    assert focus["title"] == "A" and datetime.fromisoformat(focus["since"]) == now - timedelta(seconds=3)
    assert focus["dwellSeconds"] == 21
    assert client.get(f"{API}/views/lanes").json()["human"]["focus"] == focus


def test_focus_since_survives_the_beat_boundary_and_spinner_titles(client, clock):
    t0 = clock(-10)
    _send(client, t0, [(5, 5, "✳ garden")])
    now = clock(-5)
    _send(client, now, [(5, 5, "⠂ garden")])                 # 转圈符号在变：新的一段，但还是同一个窗口
    clock(0)
    focus = client.get(f"{API}/views/current").json()["focus"]
    assert datetime.fromisoformat(focus["since"]) == t0 - timedelta(seconds=5) and focus["dwellSeconds"] == 10


# ─────────────────────────────────────────── 注意力 → 代理运行（蓝条）


@pytest.mark.parametrize(("app", "title", "hit"), [
    (TERM, "garden", True), (TERM, "✳ garden", True), (TERM, "(2) Garden", True), (TERM, "⠂  GARDEN ", True),
    ("org.gnome.Ptyxis", "garden - Ptyxis", True),          # 结尾的「 - 程序名」
    ("code", "plot.gd — garden", False),                    # 编辑器的窗口里出现了这个词：不是会话（v2.17 之前算）
    (TERM, "garden2", False), (TERM, "ga", False), (TERM, "", False),
])
def test_window_is_the_session_by_the_one_equality_rule(client, clock, app, title, hit):
    clock(-60)
    run = _start(client, label="garden", match="garden")["runId"]
    now = clock(0)
    body = _body(now, [(5, 5, title)])
    body["spans"][0]["app"] = body["app"] = app
    assert client.post(URL, json=body).status_code == 200
    assert _seconds(_agent(client, run)["attention"]) == ([5] if hit else [])
    assert _human(client)["presence"][0]["runId"] == (run if hit else None)


def test_attention_is_serial_merged_and_summed_per_run(client, clock):
    clock(-120)
    garden = _start(client, label="garden", match="garden")["runId"]   # 不挂项目（收件箱）也照样认
    plot = _start(client, label="plot")["runId"]
    for i in range(4):                                      # 4 拍 × 10 秒：garden 4 秒 → plot 3 秒 → 别的 3 秒
        now = clock(-40 + 10 * i)
        _send(client, now, [(10, 4, "✳ garden"), (6, 3, "plot"), (3, 3, "vim notes")])
    now = clock(0)
    _send(client, now, [(10, 5, "vim notes"), (5, 5, "garden")])
    now = clock(5)
    _send(client, now, [(5, 5, "⠂ garden")])                # 接着看（符号变了也是同一个会话）：并进上一段
    a, b = _agent(client, garden), _agent(client, plot)
    assert _seconds(a["attention"]) == [4, 4, 4, 4, 10] and _seconds(b["attention"]) == [3, 3, 3, 3]
    both = sorted(a["attention"] + b["attention"], key=lambda x: x["from"])
    assert all(x["to"] <= y["from"] for x, y in zip(both, both[1:])), "人同一时刻只看一个"
    assert datetime.fromisoformat(a["attention"][-1]["to"]) == now
    # 老的 interactions 里的 attend 还在（同一份数据）；agent-time 的 open[] 给秒数
    lanes = client.get(f"{API}/views/lanes").json()
    assert sum(i["kind"] == "attend" for i in lanes["interactions"]) == 9
    today = lanes["today"]
    resp = client.get(f"{API}/views/agent-time", params={"from": today, "to": today}).json()
    opened = {r["runId"]: r["attentionSeconds"] for r in resp["open"]}
    assert (opened[garden], opened[plot]) == (26, 12)


def test_ambiguous_ended_and_other_tenants_runs_get_nothing(client, clock):
    clock(-60)
    one = _start(client, label="twin")["runId"]
    two = _start(client, label="Twin")["runId"]             # 两条会话同名：不知道在看哪条 → 都不记
    done = _start(client, label="done")["runId"]
    theirs = _start(client, headers=B, label="garden")["runId"]
    client.post(f"{AGENTS}/{done}/stop", json={"outcome": "done"})
    now = clock(0)
    _send(client, now, [(9, 3, "twin"), (6, 3, "done"), (3, 3, "garden")])
    assert [s["runId"] for s in _human(client)["presence"]] == [None, None, None]
    assert _agent(client, one)["attention"] == [] and _agent(client, two)["attention"] == []
    assert _agent(client, done)["attention"] == []
    assert _agent(client, theirs, headers=B)["attention"] == []
    _send(client, now, [(3, 3, "garden")], headers=B)
    assert _seconds(_agent(client, theirs, headers=B)["attention"]) == [3]
    assert _human(client)["presence"][-1]["runId"] is None, "别的租户的运行不出现在我的时间线上"


def test_attention_before_the_run_started_is_clamped(client, clock):
    clock(-4)
    run = _start(client, label="garden")["runId"]
    now = clock(0)
    _send(client, now, [(20, 10, "garden"), (10, 10, "garden ")])   # 运行 4 秒前才开始
    assert _seconds(_agent(client, run)["attention"]) == [4]


def test_lane_attention_is_clipped_merged_and_bounded(monkeypatch):
    from app.modules.views import lanes  # noqa: PLC0415

    def t(seconds):
        return datetime(2026, 10, 9, 2, 0, tzinfo=timezone.utc) + timedelta(seconds=seconds)

    def att(a, b):
        return {"kind": "attend", "at": t(a).isoformat(), "until": t(b).isoformat()}

    run = {"interactions": [att(-50, -10), {"kind": "reply", "at": t(0).isoformat()}, att(-5, 10), att(10, 20),
                            att(30, 40), att(95, 130), att(200, 300)]}
    out = lanes._attention(run, t(0), t(100))  # noqa: SLF001
    spans = [((datetime.fromisoformat(x["from"]) - t(0)).total_seconds(),
              (datetime.fromisoformat(x["to"]) - t(0)).total_seconds()) for x in out]
    assert spans == [(0, 20), (30, 40), (95, 100)]
    monkeypatch.setattr(lanes, "MAX_ATTENTION", 2)
    assert len(lanes._attention(run, t(0), t(100))) == 2  # noqa: SLF001
    # 老的 interactions 列表：reply 全回，attend 只回最新的 MAX_ATTENTION 条
    kept = lanes._interactions(run)  # noqa: SLF001
    assert [i["kind"] for i in kept] == ["reply", "attend", "attend"] and kept[-1] == att(200, 300)


# ─────────────────────────────────────────── 自动跟踪的停留：用真的秒数


def test_needs_choice_counts_real_seconds_per_window(client, world, clock):
    """认不出的窗口与认得出的窗口每 3 秒来回切：只有它自己的秒数算数，满 60 秒才请人选；
    当前窗口认得出，auto 照常跟着它（每段自带 guess）。"""
    _track(client)
    known = _guess(taskId=world["a"])
    for i in range(19):
        now = clock(-200 + 6 * i)
        _send(client, now, [(6, 3, "notes"), (3, 3, "plot.gd", known)])
    human = _human(client)
    assert human["needsChoice"] is None, "notes 只待了 57 秒"
    assert human["auto"]["taskId"] == world["a"]
    assert datetime.fromisoformat(human["auto"]["since"]) == now - timedelta(seconds=3)
    now = clock(-200 + 6 * 19)
    _send(client, now, [(6, 3, "notes"), (3, 3, "plot.gd", known)])
    human = _human(client)
    assert human["needsChoice"]["title"] == "notes" and human["auto"]["taskId"] == world["a"]


def test_dwell_does_not_count_the_gaps_between_exact_spans(client, world, clock):
    """带停留的段首尾是真的：两段之间不在的时间（离开）不算进停留。老心跳的段才往后延到下一拍。"""
    _track(client)
    for i in range(18):                                     # 每 6 秒里只有 3 秒在 notes 上
        now = clock(-150 + 6 * i)
        _send(client, now, [(6, 3, "notes")])
    assert _human(client)["needsChoice"] is None            # 54 秒 + 末段延到此刻的 3 秒 = 57 秒
    now = clock(-150 + 6 * 18)
    _send(client, now, [(6, 3, "notes")])
    assert _human(client)["needsChoice"]["title"] == "notes"


# ─────────────────────────────────────────── 花费


@pytest.mark.parametrize("auto_track", [False, True])
def test_views_current_stays_cheap_with_a_full_timeline(client, clock, monkeypatch, capsys, auto_track):
    """顶栏每 5 秒读一次 views/current：时间线满了（2000 段、每 3 秒切一次）也只读一遍在场文档，不随段数做别的查询。"""
    from app.modules.activity import presence, repo  # noqa: PLC0415

    now = clock(0)
    n = presence.MAX_SPANS
    spans = [{"from": now - timedelta(seconds=3 * (n - i)), "to": now - timedelta(seconds=3 * (n - i - 1)),
              "app": TERM, "title": f"窗口 {i % 7}", "afk": False, "exact": True} for i in range(n)]
    _db()["activity_presence"].insert_one({"user": "u_local", "deviceId": DEV, "lastAt": now, "app": TERM,
                                           "title": spans[-1]["title"], "afk": False, "spans": spans})
    _track(client, on=auto_track)
    calls = []
    real = repo.presence_list
    monkeypatch.setattr(repo, "presence_list", lambda user: calls.append(user) or real(user))
    client.get(f"{API}/views/current")
    started = time.perf_counter()
    rounds = 20
    for _ in range(rounds):
        body = client.get(f"{API}/views/current").json()
    per_read = (time.perf_counter() - started) / rounds
    assert len(calls) == rounds + 1, "每次读只读一遍在场文档"
    assert body["focus"]["title"] == spans[-1]["title"] and body["focus"]["dwellSeconds"] > 0
    with capsys.disabled():
        print(f"\n[views/current, {n} 段, autoTrack={auto_track}] 每次 {per_read * 1000:.1f} ms")
    assert per_read < 0.5, "不是基准测试，只拦数量级的退化"
    _send(client, clock(3), [(3, 3, "又一段")])
    assert len(_db()["activity_presence"].find_one({"deviceId": DEV})["spans"]) == n
