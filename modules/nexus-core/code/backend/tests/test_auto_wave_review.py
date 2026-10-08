"""自动跟踪的「波次统一审核」修复（2026-10-08，契约「自动跟踪进行中的任务」「让 AI 认窗口」两节末尾的同名追加）。

四件事，每件都先有这里的失败用例：① 人说过「不对」的目标，上传的段也不按它自动记；② 人与 AI 抢同一个窗口，**人总是赢**
（三处插入点 × 四种人的动作）；③ 认领是一次条件写，不盖掉已经答过的问询、名额只算一次；④ 人的临时选择（没勾「以后都这样」）
也是人的决定，那个窗口的段照它自动记。时钟全是注入的；交错用「在某一步前后插一脚」钉死，真并发的那个多跑几轮。
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest

from test_activity_suggestions import _pending, _recent, _seg, _session_events, _upload
from test_auto_ai import REJECT, SUGGEST, _answer, _claim, _dwell, _waiting
from test_auto_track import (  # noqa: F401 —— world / clock 是夹具
    API, S, _ask, _db, _guess, _human, _post, _rules, _run, _track, clock, world,
)

CHOICE = f"{API}/activity/choice"


def _once(monkeypatch, obj, name, before=None, after=None):
    """``obj.name`` 第一次被调用时，在它之前 / 之后插一脚（插的那一脚里再调到它不重入）。"""
    real, fired = getattr(obj, name), []

    def wrapper(*args, **kwargs):
        first = not fired
        fired.append(1)
        if first and before:
            before()
        out = real(*args, **kwargs)
        if first and after:
            after()
        return out

    monkeypatch.setattr(obj, name, wrapper)


def _asks(**filt):
    return list(_db()["activity_ai_asks"].find({"key": {"$ne": "_tenant"}, **filt}))


def _allowance():
    return len(_db()["activity_ai_asks"].find_one({"key": "_tenant"}).get("claims") or [])


def _notes(minutes_ago, task_id=None, confidence=None, classifier="rules", **over):
    """窗口 code · notes 的一段；没给任务 = 规则没认出。"""
    start = minutes_ago if not isinstance(minutes_ago, int) else _recent(minutes_ago)
    seg = _seg(start, title="notes", task_id=task_id, confidence=confidence or (0.9 if task_id else 0.0), **over)
    seg["suggestion"]["classifier"] = classifier
    return seg


# ─────────────────────────────────────────── ① 「不对」之后，旧规则带来的段不自动记


def test_rejected_ai_target_is_not_auto_recorded_from_a_stale_detector(client, world, clock):
    w = _waiting(client, clock)
    _answer(client, w["key"], taskId=world["a"], confidence=0.95)
    _post(client, REJECT, {"key": w["key"]})
    stale = {"taskId": world["a"], "confidence": 0.9, "reason": "网页规则 #1 命中", "classifier": "rules"}
    other = {**stale, "taskId": None, "projectId": world["q"]}
    # 检测程序还没拉到新规则：同一个窗口、被否掉的目标 → 留在待确认；别的窗口 / 这个窗口的别的目标照常记
    _upload(client, [_seg(_recent(30), suggestion=stale, title="notes"),
                     _seg(_recent(20), suggestion=stale, title="别的窗口"),
                     _seg(_recent(10), suggestion=other, title="notes")])
    pending = _pending(client)["items"]
    assert [(i["title"], i["suggestion"]["taskId"]) for i in pending] == [("notes", world["a"])]
    assert sorted(e["subject"]["task"] for e in _session_events()) == sorted([world["a"], f"t_unc_{world['q']}"])


def test_rejection_stops_hiding_the_target_once_the_human_picks_it_themselves(client, world, clock):
    w = _waiting(client, clock)
    _answer(client, w["key"], taskId=world["a"], confidence=0.95)
    _post(client, REJECT, {"key": w["key"]})
    _post(client, CHOICE, {"key": w["key"], "taskId": world["a"], "remember": True})   # 人改主意：就是它
    _run(client, clock, [(75, "notes", _guess(taskId=world["a"]))])
    assert _human(client)["auto"]["source"] == "rules"                   # 人写的规则带来的猜测不再被当成旧的
    _upload(client, [_notes(5, world["a"])])
    assert _pending(client)["total"] == 0


# ─────────────────────────────────────────── ② 人与 AI 抢同一个窗口：人赢


def _human_acts(client, how, key, world):
    if how == "reject":
        return _post(client, REJECT, {"key": key})
    if how == "dismiss":
        return _post(client, f"{CHOICE}/dismiss", {"key": key})
    return _post(client, CHOICE, {"key": key, "projectId": world["q"], "remember": how == "remember"})


# AI 还没答就没有可「不对」的东西（404），所以没有 before_answer × reject
@pytest.mark.parametrize(("when", "how"), [(w, h) for w in ("before_answer", "after_answer", "after_rule")
                                           for h in ("choose", "remember", "dismiss", "reject")
                                           if (w, h) != ("before_answer", "reject")])
def test_a_human_decision_landing_inside_the_ai_answer_always_wins(client, world, clock, monkeypatch, when, how):
    from app.modules.activity import ask_repo  # noqa: PLC0415
    from app.modules.detector import window_rules  # noqa: PLC0415

    key = _waiting(client, clock)["key"]
    act = lambda: _human_acts(client, how, key, world)  # noqa: E731
    if when == "after_rule":
        _once(monkeypatch, window_rules, "prepend", after=act)       # AI 的规则已写、临时选择还没写
    else:
        _once(monkeypatch, ask_repo, "answer", **{when.split("_")[0]: act})   # 检查都过了；答案写下之前 / 之后
    resp = client.post(SUGGEST, json={"key": key, "taskId": world["a"], "confidence": 0.95, "reason": "r"})
    assert resp.status_code == 409 and resp.json()["detail"].endswith("什么都没写"), resp.text

    rules = _rules(client)["rules"]
    assert [r for r in rules if r.get("auto")] == []                     # AI 的规则没留下
    assert [(r["projectId"], r["note"][:5]) for r in rules] == ([(world["q"], "计时页选的")] if how == "remember" else [])
    choice = _db()["activity_choices"].find_one({"key": key, "expiresAt": {"$gt": clock(S + 61)}})
    human = _human(client)
    if how in ("choose", "remember"):
        assert (choice["kind"], choice.get("projectId"), choice.get("by")) == ("choice", world["q"], None)
        assert (human["auto"]["projectId"], human["auto"]["taskId"], human["auto"]["source"]) == (world["q"], None,
                                                                                                    "choice")
    else:
        assert (choice or {}).get("kind") == (None if how == "reject" else "dismiss") and human["auto"] is None
    assert human["aiThinking"] is None
    assert (human["needsChoice"] or {}).get("key") == (key if how == "reject" else None)
    assert _claim(client) is None                                        # 这个窗口不再丢给 AI


def test_a_human_choice_after_the_ai_answer_overrides_it_unless_it_agrees(client, world, clock):
    key = _waiting(client, clock)["key"]
    _answer(client, key, taskId=world["a"], confidence=0.95)
    _post(client, CHOICE, {"key": key, "taskId": world["a"]})            # 选的就是 AI 认的：AI 的规则留着
    assert [r.get("auto") for r in _rules(client)["rules"]] == [True] and _human(client)["auto"]["source"] == "ai"
    _post(client, CHOICE, {"key": key, "projectId": world["q"]})         # 没勾「以后都这样」、选了别的：等同说了「不对」
    assert _rules(client)["rules"] == [] and _asks(key=key)[0]["outcome"] == "rejected"
    _run(client, clock, [(75, "notes", _guess(taskId=world["a"]))])      # 检测程序还带着 AI 那条规则的猜测
    human = _human(client)
    assert (human["auto"]["projectId"], human["auto"]["taskId"], human["auto"]["source"]) == (world["q"], None, "choice")


def test_agreeing_with_the_ai_and_then_rejecting_it_still_hides_the_stale_guess(client, world, clock):
    key = _waiting(client, clock)["key"]
    _answer(client, key, taskId=world["a"], confidence=0.95)
    _post(client, CHOICE, {"key": key, "taskId": world["a"]})
    _post(client, REJECT, {"key": key})
    _run(client, clock, [(75, "notes", _guess(taskId=world["a"]))])
    human = _human(client)
    assert human["auto"] is None and human["needsChoice"]["key"] == key
    _upload(client, [_notes(5, world["a"])])
    assert _pending(client)["total"] == 1


def test_the_ai_never_displaces_a_human_rule_for_the_same_window(client, world, clock):
    from app.modules.activity import auto, auto_ai, choice_repo  # noqa: PLC0415

    key = _waiting(client, clock)["key"]
    doc = _db()["activity_presence"].find_one({})
    span = doc["spans"][-1]
    # 人的规则在、临时选择已经过期（或规则是在规则页手写的）：AI 的回答不能把它换掉
    assert auto_ai.window_rules.prepend(auto._window_rule(span["app"], span["title"], {"projectId": world["q"]}))  # noqa: SLF001
    out = _answer(client, key, taskId=world["a"], confidence=0.95)
    assert (out["ruleWritten"], out["autoRecord"]) == (False, False)
    rules = _rules(client)["rules"]
    assert len(rules) == 1 and rules[0]["projectId"] == world["q"] and "auto" not in rules[0]
    assert choice_repo.live("u_local", clock(S + 61))[key]["by"] == "ai"  # 临时选择照样生效（同「规则已满」）


# ─────────────────────────────────────────── ③ 认领：一次条件写


@pytest.mark.parametrize("inner", ["claimed", "answered"])
def test_a_claim_racing_another_never_replaces_its_ask(client, world, clock, monkeypatch, inner):
    from app.modules.activity import auto  # noqa: PLC0415

    _dwell(client, clock)
    clock(S + 61)
    got = {}

    def other():                                                         # 外层读完状态之后，另一个认领整个跑完
        clock(S + 62)
        got["window"] = _claim(client)
        if inner == "answered":
            _answer(client, got["window"]["key"], taskId=world["a"], confidence=0.95)

    _once(monkeypatch, auto, "state", after=other)
    outer = _claim(client)
    (ask,) = _asks()
    assert ask["claimedAt"].isoformat()[:19] == got["window"]["claimedAt"][:19]      # 先写下的那份没被换掉
    assert _allowance() == 1                                             # 名额只算一次
    if inner == "claimed":
        assert outer == got["window"]                                    # 没抢到的拿到同一个
    else:
        assert outer is None and ask["outcome"] == "suggested"           # 答过的没被抹成「没答」
        assert len(_rules(client)["rules"]) == 1 and _human(client)["auto"]["source"] == "ai"


def test_concurrent_claims_agree_on_one_ask_and_count_once(client, clock, monkeypatch):
    from app.modules.activity import auto, auto_ai  # noqa: PLC0415

    _dwell(client, clock)
    base, lock, ticks = clock(S + 61), threading.Lock(), [0]

    def now():                                                           # 每次读表多 1 毫秒：各个认领的「此刻」互不相同
        with lock:
            ticks[0] += 1
            return base + timedelta(milliseconds=ticks[0])

    monkeypatch.setattr(auto, "_now", now)
    for _ in range(6):
        _db()["activity_ai_asks"].delete_many({})
        barrier = threading.Barrier(8)

        def go():
            barrier.wait()
            return auto_ai.claim()["window"]

        with ThreadPoolExecutor(8) as pool:
            got = [f.result() for f in [pool.submit(go) for _ in range(8)]]
        assert got[0] is not None and all(g == got[0] for g in got), got
        assert len(_asks()) == 1 and _allowance() == 1


def test_a_claim_that_finds_the_hour_full_leaves_no_ask_behind(client, clock, monkeypatch):
    from app.modules.activity import ask_repo, auto_ai  # noqa: PLC0415

    _dwell(client, clock)
    at = clock(S + 61)

    def fill():                                                          # 别的窗口的认领恰好在这期间用完了名额
        _db()["activity_ai_asks"].update_one({"key": "_tenant"}, {"$set": {"claims": [at] * auto_ai.AI_MAX_PER_HOUR}})

    _once(monkeypatch, ask_repo, "claim", before=fill)
    assert _claim(client) is None and _asks() == [] and _allowance() == auto_ai.AI_MAX_PER_HOUR
    assert _human(client)["needsChoice"]["title"] == "notes"


# ─────────────────────────────────────────── ④ 人的临时选择也自动记


def test_a_live_temporary_choice_auto_records_that_windows_segments(client, world, clock, monkeypatch):
    key = _ask(client, clock)
    _post(client, CHOICE, {"key": key, "projectId": world["q"]})          # 没勾「以后都这样」：没有规则
    assert _human(client)["auto"]["source"] == "choice" and _rules(client)["rules"] == []
    start = _recent(10)
    out = _upload(client, [_notes(start), _notes(6, world["a"], classifier="service"),   # 规则没认出 / 分类服务配了别的
                           _seg(_recent(20), title="别的窗口", confidence=0.0)])
    assert out["accepted"] == 3
    assert [i["title"] for i in _pending(client)["items"]] == ["别的窗口"]
    events = _session_events()
    assert [e["subject"]["task"] for e in events] == [f"t_unc_{world['q']}"] * 2
    assert all(e["ai"]["auto"] is True and e["ai"]["confirmed"] is False for e in events)
    monkeypatch.setattr("app.modules.activity.auto._now", lambda: start)   # 「今天」钉在那一段开始的那天
    items = client.get(f"{API}/activity/auto").json()["items"]
    assert {(i["source"], i["projectId"], i["ai"]) for i in items} == {("choice", world["q"], False)}


def test_rule_recorded_segments_say_source_rules(client, world, monkeypatch):
    _track(client)
    start = _recent(5)
    _upload(client, [_notes(start, world["a"])])
    monkeypatch.setattr("app.modules.activity.auto._now", lambda: start)
    assert [i["source"] for i in client.get(f"{API}/activity/auto").json()["items"]] == ["rules"]


@pytest.mark.parametrize("why", ["switch_off", "idle", "manual_timer", "before_the_choice", "dismissed", "expired",
                                 "rule_guess_wins", "ai_choice"])
def test_temporary_choice_auto_entry_exceptions_stay_pending(client, world, clock, why):
    from app.modules.activity import auto  # noqa: PLC0415

    if why == "ai_choice":                                               # AI 把握不够时写下的临时选择不直接记成时间
        key = _waiting(client, clock)["key"]
        _answer(client, key, projectId=world["q"], confidence=0.5)
    else:
        key = _ask(client, clock)
        _post(client, CHOICE, {"key": key, "projectId": world["q"]})
    seg = _notes(5, idle=why == "idle")
    if why == "switch_off":
        _track(client, on=False)
    elif why == "manual_timer":
        _post(client, f"{API}/timer/start", {"taskId": world["a"]})
    elif why == "before_the_choice":
        seg = _notes(40)                                                 # 人做决定之前就结束了的段（补传的积压）
    elif why == "dismissed":
        _post(client, f"{CHOICE}/dismiss", {"key": key})
    elif why == "expired":
        clock(S + 60 + auto.CHOICE_AWAY.total_seconds() + 1)
    elif why == "rule_guess_wins":                                       # 规则给了别的目标但把握不够：同页面，规则优先 → 不记
        seg = _notes(5, world["a"], confidence=0.6)
    assert _upload(client, [seg])["accepted"] == 1
    assert _pending(client)["total"] == 1 and [e for e in _session_events() if e["source"] != "timer-backend"] == []
