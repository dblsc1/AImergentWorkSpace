"""让 AI 认窗口（契约 v2.15「让 AI 认窗口」节）。时钟全是注入的，没有一处真等。

要害：**没人来认领 = 与 v2.14 完全相同**；AI 只能在「这个窗口此刻被认领着等回答」时写、只写这一个窗口；
每个状态迁移与超时、每小时上限、6 小时不重问、把握门槛、规则形状、出处 ``source: "ai"``、人说「不对」、租户隔离、类型校验。
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from test_activity_suggestions import BEARER, _seg, _upload
from test_auto_track import (  # noqa: F401 —— world / clock 是夹具
    API, B, DEV, RULES, S, _beat, _db, _guess, _human, _post, _rules, _run, _track, clock, world,
)

CLAIM, SUGGEST, REJECT = f"{API}/activity/ai/claim", f"{API}/activity/ai/suggest", f"{API}/activity/choice/reject"


def _claim(client, headers=None):
    return _post(client, CLAIM, None, headers)["window"]


def _dwell(client, clock, title="notes", start=0):
    """开关开着，``title`` 在前台攒够 60 秒（三次心跳）。返回最后一次心跳的秒数。"""
    _track(client)
    _run(client, clock, [(start, title, None), (start + 30, title, None), (start + 60, title, None)])
    return start + 60


def _waiting(client, clock, title="notes"):
    """一个被认领、等 AI 回答的窗口；时钟停在认领那一刻（S + 61）。"""
    _dwell(client, clock, title)
    clock(S + 61)
    return _claim(client)


def _answer(client, key, expect=200, **body):
    body.setdefault("reason", "标题里有项目名")
    return _post(client, SUGGEST, {"key": key, **body}, expect=expect)


# ─────────────────────────────────────────── 这条路活不活


def test_nobody_claiming_is_exactly_step_one(client, clock):
    _dwell(client, clock)
    human = _human(client)
    assert human["needsChoice"]["title"] == "notes" and human["aiThinking"] is None
    assert _db()["activity_ai_asks"].count_documents({}) == 0   # 读端不写


def test_claim_hands_out_the_window_and_the_card_waits(client, clock):
    from app.modules.activity import auto_ai  # noqa: PLC0415

    _dwell(client, clock)
    at = clock(S + 61)
    w = _claim(client)
    key = _human(client)["aiThinking"]["key"]
    assert w == {"key": key, "app": "code", "title": "notes", "claimedAt": at.isoformat(),
                 "answerBy": (at + auto_ai.AI_ANSWER_WAIT).isoformat()}
    human = _human(client)
    assert human["needsChoice"] is None and human["aiThinking"]["title"] == "notes"
    assert client.get(f"{API}/views/current").json()["aiThinking"]["key"] == key   # 顶栏读的是同一份
    assert _claim(client) == w                      # 再取：同一个，不另认领
    assert len(_db()["activity_ai_asks"].find_one({"key": "_tenant"})["claims"]) == 1


def test_alive_path_shows_thinking_before_the_claim_and_falls_back_after_grace(client, clock):
    from app.modules.activity import auto_ai  # noqa: PLC0415

    _track(client)
    clock(S)
    assert _claim(client) is None                   # 没有窗口在等：只是报了个到
    _run(client, clock, [(0, "notes", None), (30, "notes", None), (59, "notes", None)])
    assert _human(client)["aiThinking"] is None and _human(client)["needsChoice"] is None   # 还没攒够
    _run(client, clock, [(60, "notes", None)])
    assert _human(client)["aiThinking"]["title"] == "notes"      # 这条路活着：直接等 AI，不闪一下人的卡
    grace = auto_ai.AI_CLAIM_GRACE.total_seconds()
    _run(client, clock, [(grace, "notes", None)])
    assert _human(client)["aiThinking"] is not None              # 距上次认领恰好 60 秒：还算活着
    _run(client, clock, [(grace + 1, "notes", None)])
    human = _human(client)
    assert human["aiThinking"] is None and human["needsChoice"]["title"] == "notes"   # 没人再来：请人选


def test_no_answer_within_the_wait_falls_back_to_the_human(client, clock):
    from app.modules.activity import auto_ai  # noqa: PLC0415

    w = _waiting(client, clock)
    wait = auto_ai.AI_ANSWER_WAIT.total_seconds()
    _run(client, clock, [(61 + wait, "notes", None)])
    assert _human(client)["aiThinking"] is not None              # 恰好到点：还在等
    _run(client, clock, [(62 + wait, "notes", None)])
    human = _human(client)
    assert human["aiThinking"] is None and human["needsChoice"]["key"] == w["key"]
    assert _answer(client, w["key"], 409, none=True)["detail"].endswith("什么都没写")   # 迟到的回答不收
    assert _claim(client) is None                                # 问过了：6 小时内不再认领这个窗口
    assert _human(client)["needsChoice"]["key"] == w["key"]


# ─────────────────────────────────────────── AI 的回答


def test_suggest_writes_one_window_rule_and_shows_at_once(client, world, clock):
    w = _waiting(client, clock)
    out = _answer(client, w["key"], taskId=world["a"], confidence=0.95, reason="标题是 notes，历史里记到任务 a")
    assert out == {"key": w["key"], "outcome": "suggested", "taskId": world["a"], "projectId": world["p"],
                   "confidence": 0.9, "autoRecord": True, "ruleWritten": True}
    rules = _rules(client)
    assert rules["version"] == 1
    assert rules["rules"] == [{"id": "r_ai_" + w["key"][3:], "app": "^code$", "title": "^notes$", "taskId": world["a"],
                               "confidence": 0.9, "note": "AI 认的：标题是 notes，历史里记到任务 a", "enabled": True,
                               "author": "assistant", "auto": True}]
    human = _human(client)
    assert human["needsChoice"] is None and human["aiThinking"] is None
    assert (human["auto"]["taskId"], human["auto"]["source"], human["auto"]["key"]) == (world["a"], "ai", w["key"])
    # 检测程序拉到规则、带着猜测来：仍然认得出是 AI 认的
    _run(client, clock, [(90, "notes", _guess(taskId=world["a"]))])
    assert _human(client)["auto"]["source"] == "ai"
    assert _answer(client, w["key"], 409, projectId=world["q"], confidence=0.9)["detail"].endswith("什么都没写")
    assert len(_rules(client)["rules"]) == 1                     # 一次问询恰好答一次


@pytest.mark.parametrize(("given", "stored", "records"), [(0.8, 0.9, True), (0.79, 0.79, False), (0.3, 0.3, False)])
def test_confidence_reaches_the_auto_record_threshold_only_when_the_ai_is_sure(client, world, clock, given, stored,
                                                                               records):
    w = _waiting(client, clock)
    out = _answer(client, w["key"], projectId=world["q"], confidence=given)
    assert (out["confidence"], out["autoRecord"], out["taskId"], out["projectId"]) == (stored, records, None,
                                                                                         world["q"])
    rule = _rules(client)["rules"][0]
    assert (rule["confidence"], rule["projectId"], rule["taskId"]) == (stored, world["q"], None)
    assert _human(client)["auto"]["source"] == "ai"              # 把握低也照样显示，只是不直接记成时间


def test_none_goes_straight_to_the_human_and_is_not_asked_again_for_hours(client, clock):
    from app.modules.activity import auto_ai  # noqa: PLC0415

    w = _waiting(client, clock)
    out = _answer(client, w["key"], none=True, reason="历史和任务树里都对不上")
    assert (out["outcome"], out["ruleWritten"], out["confidence"]) == ("none", False, None)
    assert _rules(client)["rules"] == []
    human = _human(client)
    assert human["aiThinking"] is None and human["needsChoice"]["key"] == w["key"]
    retry = auto_ai.AI_RETRY.total_seconds()
    _dwell(client, clock, start=61 + retry - 120)                # 差一点到 6 小时
    clock(S + 61 + retry - 59)
    assert _claim(client) is None and _human(client)["needsChoice"]["key"] == w["key"]
    _dwell(client, clock, start=61 + retry)                      # 过了 6 小时：再问一次
    clock(S + 61 + retry + 61)
    assert _claim(client)["key"] == w["key"]


def test_suggest_only_while_that_window_is_claimed(client, world, clock):
    _dwell(client, clock)
    key = _human(client)["needsChoice"]["key"]
    ok = {"taskId": world["a"], "confidence": 0.9}
    assert _answer(client, key, 409, **ok)["detail"].endswith("什么都没写")              # 没认领过
    assert _answer(client, "wk_" + "0" * 20, 409, **ok)["detail"].endswith("什么都没写")  # 不认识的窗口
    assert _rules(client)["rules"] == [] and _db()["activity_choices"].count_documents({}) == 0


def test_suggest_target_must_be_a_live_normal_task_or_project(client, world, clock):
    w = _waiting(client, clock)
    done = _post(client, f"{API}/planner/tasks", {"projectId": world["p"], "name": "做完的"})["id"]
    assert client.patch(f"{API}/planner/tasks/{done}", json={"done": True}).status_code == 200
    for target, status in (({"taskId": "t_nope"}, 404), ({"projectId": "p_nope"}, 404), ({"taskId": done}, 400)):
        assert _answer(client, w["key"], status, confidence=0.9, **target)["detail"].endswith("什么都没写")
    assert _rules(client)["rules"] == []
    assert _human(client)["aiThinking"] is not None              # 目标不对不算答过：这一轮里还能改
    assert _answer(client, w["key"], taskId=world["a"], confidence=0.9)["ruleWritten"] is True


def test_suggest_refused_when_switch_is_off_or_the_human_already_chose(client, world, clock):
    w = _waiting(client, clock)
    _track(client, on=False)
    assert _answer(client, w["key"], 409, taskId=world["a"], confidence=0.9)["detail"].endswith("什么都没写")
    _track(client)
    _post(client, f"{API}/activity/choice", {"key": w["key"], "projectId": world["q"]})   # 人先选了
    assert _answer(client, w["key"], 409, taskId=world["a"], confidence=0.9)["detail"].endswith("什么都没写")
    assert _rules(client)["rules"] == [] and _human(client)["auto"]["source"] == "choice"


def test_pseudonymized_windows_are_never_given_to_the_ai(client, clock):
    _track(client)
    clock(S)
    _claim(client)
    _run(client, clock, [(0, "窗口名3", None), (30, "窗口名3", None), (60, "窗口名3", None)])
    human = _human(client)
    assert human["aiThinking"] is None and human["needsChoice"]["title"] == "窗口名3"   # 写不出规则：直接请人
    assert _claim(client) is None


def test_manual_timer_and_switch_off_mean_nothing_to_claim(client, world, clock):
    _dwell(client, clock)
    _track(client, on=False)
    clock(S + 61)
    assert _claim(client) is None
    _track(client)
    assert client.post(f"{API}/timer/start", json={"taskId": world["a"]}).status_code == 200
    assert _claim(client) is None and _human(client)["aiThinking"] is None
    assert _db()["activity_ai_asks"].count_documents({"key": {"$ne": "_tenant"}}) == 0


# ─────────────────────────────────────────── 上限


def test_hourly_cap_then_the_human_is_asked(client, clock):
    from app.modules.activity import auto_ai  # noqa: PLC0415

    _track(client)
    t = 0
    for i in range(auto_ai.AI_MAX_PER_HOUR):
        _run(client, clock, [(t, f"窗 {i}", None), (t + 30, f"窗 {i}", None), (t + 60, f"窗 {i}", None)])
        w = _claim(client)
        assert w["title"] == f"窗 {i}"
        _answer(client, w["key"], none=True)
        t += 70
    _run(client, clock, [(t, "第十三个", None), (t + 30, "第十三个", None), (t + 60, "第十三个", None)])
    assert _claim(client) is None                                # 这一小时问满了
    human = _human(client)
    assert human["aiThinking"] is None and human["needsChoice"] is not None
    assert len(_db()["activity_ai_asks"].find_one({"key": "_tenant"})["claims"]) == auto_ai.AI_MAX_PER_HOUR
    later = t + 3600
    _run(client, clock, [(later, "第十三个", None), (later + 30, "第十三个", None), (later + 60, "第十三个", None)])
    assert _claim(client)["title"] == "第十三个"                  # 一小时后名额回来


# ─────────────────────────────────────────── 人说「不对」


def test_reject_removes_the_ai_rule_clears_the_override_and_asks_the_human(client, world, clock):
    client.put(RULES, json={"rules": [{"app": "blender", "taskId": world["a"]}]}, headers={"If-Match": '"0"'})
    w = _waiting(client, clock)
    _answer(client, w["key"], taskId=world["a"], confidence=0.9)
    assert [r.get("auto") for r in _rules(client)["rules"]] == [True, None]
    out = _post(client, REJECT, {"key": w["key"]})
    assert out == {"key": w["key"], "app": "code", "title": "notes", "ruleRemoved": True}
    assert [r["app"] for r in _rules(client)["rules"]] == ["blender"]          # 只删 AI 那条
    human = _human(client)
    assert human["auto"] is None and human["aiThinking"] is None and human["needsChoice"]["key"] == w["key"]
    # 检测程序还没拉到新规则、还带着那个猜测来：不算
    _run(client, clock, [(75, "notes", _guess(taskId=world["a"]))])
    assert _human(client)["auto"] is None and _human(client)["needsChoice"]["key"] == w["key"]
    assert _claim(client) is None                                              # 不再丢给 AI
    assert client.post(REJECT, json={"key": w["key"]}).status_code == 404      # 说过了
    # 人自己选：照常
    _post(client, f"{API}/activity/choice", {"key": w["key"], "projectId": world["q"], "remember": True})
    assert _human(client)["auto"]["source"] == "choice"
    assert [r.get("auto") for r in _rules(client)["rules"]] == [None, None]


def test_reject_is_for_humans_and_only_for_ai_recognised_windows(client, world, clock):
    key = _waiting(client, clock)["key"]
    assert client.post(REJECT, json={"key": key}).status_code == 404           # 还没答
    _answer(client, key, taskId=world["a"], confidence=0.9)
    assert client.post(REJECT, json={"key": key}, headers=BEARER).status_code == 403
    assert client.post(REJECT, json={"key": 1}, headers=BEARER).status_code == 403
    assert client.post(REJECT, json={"key": "nope"}).status_code == 422
    assert client.post(REJECT, json={"key": "wk_" + "0" * 20}).status_code == 404
    assert len(_rules(client)["rules"]) == 1


def test_auto_recorded_segments_say_when_the_ai_recognised_them(client, world, clock):
    from test_activity_suggestions import _recent  # noqa: PLC0415

    w = _waiting(client, clock)
    _answer(client, w["key"], taskId=world["a"], confidence=0.9)
    sug = {"taskId": world["a"], "confidence": 0.9, "reason": "网页规则 #1 命中", "classifier": "rules"}
    _upload(client, [_seg(_recent(30), suggestion=sug, title="notes"), _seg(_recent(20), suggestion=sug, title="别的窗口")])
    clock(0)
    items = client.get(f"{API}/activity/auto").json()["items"]
    assert sorted((i["title"], i["ai"]) for i in items) == [("notes", True), ("别的窗口", False)]


# ─────────────────────────────────────────── 规则上的出处键


def test_rule_provenance_keys_round_trip_and_are_validated(client, world):
    put = lambda rules, v: client.put(RULES, json={"rules": rules}, headers={"If-Match": f'"{v}"'})  # noqa: E731
    base = {"title": "x", "taskId": world["a"]}
    r = put([{**base, "author": "assistant", "auto": True}, {"title": "y", "taskId": world["a"]}], 0)
    assert r.status_code == 200, r.text
    marked, plain = r.json()["rules"]
    assert (marked["author"], marked["auto"]) == ("assistant", True)
    assert "author" not in plain and "auto" not in plain                       # 没给就不带：与旧规则逐字节相同
    assert put([marked, plain], 1).json()["rules"] == [marked, plain]          # 页面整套存回去不丢
    for bad in ({"author": "robot"}, {"auto": "yes"}, {"auto": 1}, {"author": {"$ne": ""}}):
        assert put([{**base, **bad}], 2).status_code == 422


# ─────────────────────────────────────────── 守卫、校验、租户


def test_ai_endpoints_refuse_device_tokens_before_reading_the_body(client, world, clock):
    w = _waiting(client, clock)
    for url, body in ((CLAIM, None), (SUGGEST, {"key": w["key"], "none": True, "reason": "x"}), (SUGGEST, {"key": 1})):
        assert client.post(url, json=body, headers=BEARER).status_code == 403
    assert _human(client)["aiThinking"] is not None                            # 什么都没变


def test_suggest_validation(client, world, clock):
    key = _waiting(client, clock)["key"]
    ok = {"key": key, "taskId": world["a"], "confidence": 0.9, "reason": "r"}
    for body in (
        {**ok, "projectId": world["p"]},                 # 两个目标
        {k: v for k, v in ok.items() if k != "taskId"},  # 没有目标
        {k: v for k, v in ok.items() if k != "confidence"},
        {k: v for k, v in ok.items() if k != "reason"},
        {**ok, "confidence": 0}, {**ok, "confidence": 1.01}, {**ok, "confidence": "0.9"}, {**ok, "confidence": True},
        {**ok, "reason": ""}, {**ok, "reason": "长" * 201}, {**ok, "reason": ["x"]},
        {**ok, "taskId": {"$ne": ""}}, {**ok, "taskId": ""}, {**ok, "key": {"$ne": ""}}, {**ok, "key": "wk_zz"},
        {**ok, "none": True}, {**ok, "none": False}, {"key": key, "none": "true", "reason": "r"},
        {**ok, "title": "另一个窗口"}, {**ok, "app": "x"},   # 不能替别的标题写规则：多余的键一律拒
    ):
        assert client.post(SUGGEST, json=body).status_code == 422, body
    assert _rules(client)["rules"] == [] and _human(client)["aiThinking"] is not None


def test_tenants_do_not_see_or_answer_each_others_windows(client, world, clock):
    w = _waiting(client, clock)
    assert _claim(client, B) is None                                           # B 认领不到 A 的窗口
    assert _human(client, B)["aiThinking"] is None
    r = client.post(SUGGEST, json={"key": w["key"], "none": True, "reason": "x"}, headers=B)
    assert r.status_code == 409
    assert client.post(REJECT, json={"key": w["key"]}, headers=B).status_code == 404
    assert _human(client)["aiThinking"]["key"] == w["key"]                     # A 的还在等
    # B 自己的窗口、自己的上限
    _track(client, headers=B)
    for sec in (0, 30, 60):
        clock(S + 100 + sec)
        _beat(client, title="b 的窗口", headers=B)
    assert _claim(client, B)["title"] == "b 的窗口"
    assert _db()["activity_ai_asks"].count_documents({"key": {"$ne": "_tenant"}}) == 2
    assert _claim(client)["key"] == w["key"]


def test_old_asks_are_purged_on_claim(client, clock):
    from app.modules.activity import auto_ai  # noqa: PLC0415

    w = _waiting(client, clock)
    _answer(client, w["key"], none=True)
    clock(S + 61 + auto_ai.AI_KEEP.total_seconds() + 1)
    assert _claim(client) is None
    assert _db()["activity_ai_asks"].count_documents({"key": {"$ne": "_tenant"}}) == 0


def test_constants_match_the_contract():
    from app.modules.activity import auto, auto_ai  # noqa: PLC0415

    assert (auto_ai.AI_CLAIM_GRACE, auto_ai.AI_ANSWER_WAIT) == (timedelta(seconds=60), timedelta(seconds=120))
    assert (auto_ai.AI_RETRY, auto_ai.AI_MAX_PER_HOUR, auto_ai.AI_KEEP) == (timedelta(hours=6), 12, timedelta(days=30))
    assert auto_ai.AI_TRUST == 0.8 < auto.AUTO_CONFIDENCE
