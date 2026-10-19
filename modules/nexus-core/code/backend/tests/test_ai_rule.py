"""「交给 AI 判断」规则（契约 v2.34 / detector.rules.v1 v1.4）：mode "ai" 的规则命中的窗口不硬分。

要害：校验（ai ⇒ 无目标，普通规则不变）；命中的窗口不实时问 AI、不请人、不自动记、留在待确认带 needsAI；
AI 经 suggest_window_target 或报告回答都不写规则；AI 直接写规则删不掉人写的 ai 规则；普通规则不受影响。
"""

from __future__ import annotations

from test_activity_reports import REPORTS, _status, _submit
from test_activity_suggestions import API, SUG, _pending, _recent, _seg, _session_events, _upload
from test_auto_ai import CLAIM, SUGGEST, _answer, _claim, _dwell
from test_auto_track import DEV, RULES, S, _beat, _human, _post, _rules, _run, _track, clock, world  # noqa: F401

AI = {"app": "^code$", "title": None, "mode": "ai"}


def _put(client, rules, version=0):
    return client.put(RULES, json={"rules": rules}, headers={"If-Match": f'"{version}"'})


def _flag(**over):
    return {"taskId": None, "confidence": 0, "reason": "规则命中：交给 AI 判断", "classifier": "rules",
            "needsAI": True, **over}


def _guess_ai():
    return {"needsAI": True, "confidence": 0, "classifier": "rules"}


# ─────────────────────────────────────────── 规则校验


def test_ai_rule_validation_and_round_trip(client, world):
    r = _put(client, [AI])
    assert r.status_code == 200, r.text
    rule = r.json()["rules"][0]
    assert rule["mode"] == "ai" and rule["taskId"] is None and "projectId" not in rule
    assert _rules(client)["rules"] == r.json()["rules"]
    for bad in ({**AI, "taskId": world["a"]}, {**AI, "projectId": world["p"]}, {**AI, "mode": "rules"},
                {"app": "x", "mode": "ai", "taskId": world["a"], "projectId": world["p"]}):
        resp = _put(client, [bad], 1)
        assert resp.status_code == 422, bad
    assert _put(client, [{"app": "x"}], 1).status_code == 422              # 普通规则仍须有目标
    assert _put(client, [{"title": "t"}], 1).status_code == 422            # ai 规则仍须有 app / title
    ok = _put(client, [{"app": "x", "taskId": world["a"]}], 1).json()["rules"][0]
    assert "mode" not in ok                                                # 普通规则不带 mode 键


def test_ai_apply_cannot_drop_or_change_a_human_ai_rule(client, world):
    human = _put(client, [AI, {"app": "y", "taskId": world["a"]}]).json()["rules"]
    client.put(f"{API}/detector/rules/ai-direct", json={"enabled": True})
    ai_apply = f"{RULES}/ai-apply"
    body = lambda rules: {"rules": rules, "summary": "改"}  # noqa: E731
    assert client.post(ai_apply, json=body([human[1]])).status_code == 422                       # 删
    assert client.post(ai_apply, json=body([{**human[0], "app": "z"}, human[1]])).status_code == 422   # 改
    out = client.post(ai_apply, json=body([human[0], human[1], {"app": "w", "taskId": world["a"]}]))
    assert out.status_code == 200, out.text                                                      # 原样保留、可以加别的
    assert out.json()["rules"][0] == human[0]


# ─────────────────────────────────────────── 上传：待确认带标记，不自动记


def test_flagged_upload_stays_pending_with_the_flag_and_is_never_auto_recorded(client, world, clock):  # noqa: F811
    _track(client)
    _upload(client, [_seg(_recent(30), suggestion=_flag())])
    items = _pending(client)["items"]
    assert len(items) == 1 and items[0]["suggestion"]["needsAI"] is True and items[0]["auto"] is False
    assert _session_events() == []                                     # autoTrack 开着也不记
    # 不是 needsAI 的建议不带这个键
    _upload(client, [_seg(_recent(10), title="other", suggestion={"taskId": None, "confidence": 0, "reason": "r",
                                                                   "classifier": "rules"})])
    plain = [i for i in _pending(client)["items"] if i["title"] == "other"][0]
    assert "needsAI" not in plain["suggestion"]
    only = client.get(SUG, params={"needsAI": "true"}).json()
    assert only["total"] == 1 and only["items"][0]["suggestion"]["needsAI"] is True
    assert client.get(SUG).json()["total"] == 2


def test_flag_with_a_target_is_rejected(client, world):
    out = _upload(client, [_seg(_recent(10), suggestion=_flag(taskId=world["a"]))])
    assert out["accepted"] == 0 and len(out["rejected"]) == 1


# ─────────────────────────────────────────── 在场心跳：不实时问 AI、不请人


def test_flagged_window_is_neither_asked_to_ai_nor_to_the_human(client, world, clock):  # noqa: F811
    _track(client)
    clock(S)
    _claim(client)                                        # AI 这条路活着
    _run(client, clock, [(0, "term", _guess_ai()), (30, "term", _guess_ai()), (60, "term", _guess_ai()),
                         (61, "term", _guess_ai())])
    human = _human(client)
    assert human["needsChoice"] is None and human["aiThinking"] is None and human["auto"] is None
    assert _claim(client) is None                          # 没有可认领的窗口
    # 对照：同样的停留、没有标记 = 照常请 AI
    _run(client, clock, [(70, "plain", None), (100, "plain", None), (130, "plain", None)])
    _claim(client)                                          # 还活着（grace 60 秒）
    assert _human(client)["aiThinking"]["title"] == "plain"


def test_suggest_on_a_flagged_window_writes_no_rule_and_never_auto_records(client, world, clock):  # noqa: F811
    _track(client)
    _run(client, clock, [(0, "term", _guess_ai()), (30, "term", _guess_ai())])
    from app.modules.activity import auto  # noqa: PLC0415

    key = auto.window_key("code", "term")
    out = _answer(client, key, taskId=world["a"], confidence=0.95, reason="终端里在做任务 a")
    assert out["ruleWritten"] is False and out["autoRecord"] is False and out["needsAI"] is True
    assert out["taskId"] == world["a"]
    assert _rules(client)["version"] == 0 and _rules(client)["rules"] == []
    assert _db_asks() == 0                                  # 不留问询
    none = _answer(client, key, none=True)
    assert none["outcome"] == "none" and none["ruleWritten"] is False
    assert _rules(client)["version"] == 0
    # 没有标记的窗口仍须先被认领
    _run(client, clock, [(40, "plain", None)])
    assert _answer(client, auto.window_key("code", "plain"), 409, none=True)


def _db_asks() -> int:
    from test_auto_track import _db  # noqa: PLC0415

    return _db()["activity_ai_asks"].count_documents({"key": {"$ne": "_tenant"}})


def test_guess_shape_validation(client):
    _track(client)
    _beat(client, guess=_guess_ai())
    _beat(client, guess={**_guess_ai(), "taskId": "t"}, expect=422)
    _beat(client, guess={"confidence": 0.9, "classifier": "rules"}, expect=422)   # 没有目标也没有 needsAI


# ─────────────────────────────────────────── 报告批准：不写规则


def test_report_assignment_on_a_flagged_suggestion_writes_no_rule(client, seeded):
    _upload(client, [_seg(_recent(10), suggestion=_flag())])
    ids = [i["id"] for i in _pending(client)["items"]]
    task = seeded["tasks"]["示例任务三"]["id"]
    rid = _submit(client, [{"kind": "assign", "suggestionIds": ids, "taskId": task, "reason": "AI 判断"}])["reportId"]
    assert client.post(f"{REPORTS}/{rid}/approve").status_code == 200
    assert _status(client, rid)["items"][0]["status"] != "pending"
    assert _rules(client)["version"] == 0                   # 回答只对这条建议生效
    assert len(_session_events()) == 1


# ─────────────────────────────────────────── 评审补强（v2.34）


def _ai_on(client):
    assert client.put(f"{RULES}/ai-direct", json={"enabled": True}).status_code == 200


def _apply(client, rules):
    return client.post(f"{RULES}/ai-apply", json={"rules": rules, "summary": "改"})


def test_ai_apply_cannot_insert_or_move_a_fixed_rule_before_a_human_ai_rule(client, world):
    fixed = lambda a: {"app": a, "taskId": world["a"]}  # noqa: E731
    cur = _put(client, [fixed("first"), AI, fixed("last")]).json()["rules"]
    first, ai, last = cur
    _ai_on(client)
    assert _apply(client, [fixed("new"), first, ai, last]).status_code == 422            # 新规则插在前
    assert _apply(client, [first, last, ai]).status_code == 422                          # 后面的挪到前
    assert _apply(client, [{**first, "app": "changed"}, ai, last]).status_code == 422    # 前面的被改
    assert _apply(client, [ai, first, last]).status_code == 200                          # 把 ai 挪到最前、后面照旧：可以
    ok = _apply(client, [ai, first, last, fixed("tail")])
    assert ok.status_code == 200, ok.text                                                # 加在后面可以


def test_human_edit_clears_provenance_so_a_switched_rule_is_protected(client, world):
    _ai_on(client)
    r = _apply(client, [{"app": "term", "taskId": world["a"]}]).json()["rules"][0]
    assert r["author"] == "assistant" and r["aiAt"]
    swapped = {k: v for k, v in r.items() if k not in ("taskId",)} | {"taskId": None, "mode": "ai"}
    out = _put(client, [swapped], client.get(RULES).json()["version"])
    assert out.status_code == 200, out.text
    mine = out.json()["rules"][0]
    assert mine["mode"] == "ai" and "author" not in mine and "aiAt" not in mine
    assert _apply(client, []).status_code == 422                                          # 现在是人的，AI 删不掉


def test_server_matches_stored_ai_rules_without_a_detector_flag(client, world, clock):  # noqa: F811
    from app.modules.activity import auto  # noqa: PLC0415

    _put(client, [{"app": "^code$", "title": "TERM", "mode": "ai"}, {"app": "code", "taskId": world["a"]}])
    _track(client)
    clock(S)
    _claim(client)                                         # AI 路活着
    # 没带 needsAI（老检测程序 / 规则同步前），还带了高把握的猜测：照样当「交给 AI 判断」
    guess = {"taskId": world["a"], "confidence": 0.9, "classifier": "rules"}
    _run(client, clock, [(0, "my term 1", guess), (30, "my term 1", guess), (60, "my term 1", guess)])
    h = _human(client)
    assert h["auto"] is None and h["needsChoice"] is None and h["aiThinking"] is None
    assert _claim(client) is None
    key = auto.window_key("code", "my term 1")
    out = _answer(client, key, taskId=world["a"], confidence=0.95)
    assert out["ruleWritten"] is False and out["needsAI"] is True
    assert [r["app"] for r in _rules(client)["rules"]] == ["^code$", "code"]              # 没有新规则
    # 上传：服务端自己标上，且不自动记
    _upload(client, [_seg(_recent(10), title="x term", suggestion={"taskId": world["a"], "confidence": 0.9,
                                                                    "reason": "r", "classifier": "rules"})])
    sug = _pending(client)["items"][0]["suggestion"]
    assert sug["needsAI"] is True and sug["taskId"] is None and sug["confidence"] == 0
    assert _session_events() == []


def test_prepend_refuses_a_window_an_ai_rule_matches(client, world):
    from app.modules.detector import window_rules  # noqa: PLC0415

    _put(client, [AI])
    rule = {"app": "^code$", "title": "^notes$", "taskId": world["a"], "confidence": 0.9, "enabled": True}
    assert window_rules.prepend(rule, yield_to_human=True, window=("code", "notes")) is False
    assert window_rules.prepend(rule, yield_to_human=True, window=("vim", "notes")) is True


def test_pathological_stored_regex_matches_in_linear_time(client, world):
    import time  # noqa: PLC0415

    from app.modules.detector import window_rules  # noqa: PLC0415

    assert _put(client, [{"title": "(a+)+$", "mode": "ai"}]).status_code == 200
    assert window_rules._re2("(?=x)") is None                              # RE2 编不过：跳过
    m = window_rules.ai_matchers("u_local")
    t0 = time.monotonic()
    assert window_rules.ai_hit(m, "code", "a" * 5000 + "b") is False       # Python re 在这里指数回溯
    assert window_rules.ai_hit(m, "code", "b" + "a" * 5000) is True
    assert time.monotonic() - t0 < 1
