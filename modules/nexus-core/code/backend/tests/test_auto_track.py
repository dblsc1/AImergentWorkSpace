"""自动跟踪进行中的任务（契约 v2.14「自动跟踪进行中的任务」节）。

要害三句：**开关关着，一切照旧**；**手动计时永远优先**；**自动记下的段处处认得出、人能改**。
开关 / 只到项目的规则 / 上传带 projectId / 心跳带 guess / ``auto`` 的每个条件 / ``since`` / ``needsChoice`` 的停留与抖动 /
人答与「这次不选」/ remember 恰好一条规则 / 自动记录的每条例外与出处 / 改挂 / 租户隔离 / 类型校验。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from test_activity_suggestions import BEARER, _pending, _recent, _seg, _session_events, _upload

API = "/api/core"
PLANNER = f"{API}/planner"
RULES = f"{API}/detector/rules"
DEV = "dev_3f9a1c2b7d4e5a60"
B = {"X-Nexus-Tenant": "ch_bbbb"}
S = -900  # 在场时间线的起点：15 分钟前（往后走也到不了未来，不踩上传的「endAt 在未来」）


def _db():
    from app.repo import get_db  # noqa: PLC0415

    return get_db()


def _post(client, path, body=None, headers=None, expect=(200, 201)):
    resp = client.post(path, json=body, headers=headers or {})
    assert resp.status_code in (expect if isinstance(expect, tuple) else (expect,)), resp.text
    return resp.json()


@pytest.fixture()
def world(client):
    """一个分区、两个项目：P 下任务 a，Q 空着。"""
    zone = _post(client, f"{PLANNER}/zones", {"name": "自动分区"})["id"]
    p = _post(client, f"{PLANNER}/projects", {"zoneId": zone, "name": "项目 P"})["id"]
    q = _post(client, f"{PLANNER}/projects", {"zoneId": zone, "name": "项目 Q"})["id"]
    a = _post(client, f"{PLANNER}/tasks", {"projectId": p, "name": "任务 a"})["id"]
    return {"p": p, "q": q, "a": a}


@pytest.fixture()
def clock(monkeypatch):
    """把心跳、自动跟踪、计时三处的服务端时钟钉在「现在 + 偏移（秒）」。"""
    from app.modules.activity import auto, presence  # noqa: PLC0415
    from app.modules.timer import service as timer  # noqa: PLC0415

    base = datetime.now(timezone.utc).replace(microsecond=0)
    state = {"t": base}
    for mod in (auto, presence, timer):
        monkeypatch.setattr(mod, "_now", lambda: state["t"])

    def at(seconds):
        state["t"] = base + timedelta(seconds=seconds)
        return state["t"]

    return at


def _track(client, on=True, device=DEV, headers=None, **privacy):
    body = {"schemaVersion": 1, "autoTrack": on, **({"privacy": privacy} if privacy else {})}
    resp = client.put(f"{API}/detector/settings", params={"deviceId": device}, json=body, headers=headers or {})
    assert resp.status_code == 200, resp.text


def _beat(client, title="plot.gd — garden", guess=None, afk=False, app="code", device=DEV, headers=None, expect=200):
    body = {"deviceId": device, "app": "" if afk else app, "title": "" if afk else title, "afk": afk}
    if guess is not None:
        body["guess"] = guess
    resp = client.post(f"{API}/activity/presence", json=body, headers=headers or {})
    assert resp.status_code == expect, resp.text


def _guess(**target):
    return {**target, "confidence": 0.9, "classifier": "rules"}


def _human(client, headers=None, **params):
    resp = client.get(f"{API}/views/lanes", params=params, headers=headers or {})
    assert resp.status_code == 200, resp.text
    return resp.json()["human"]


def _run(client, clock, beats):
    """beats = [(秒, 标题, guess | None)]，按时刻逐个发。"""
    for sec, title, guess in beats:
        clock(S + sec)
        _beat(client, title=title, guess=guess)


def _rules(client, headers=None):
    return client.get(RULES, headers=headers or {}).json()


# ─────────────────────────────────────────── 开关与只到项目的规则


def test_auto_track_setting_round_trip_default_off_and_strict(client):
    url, q = f"{API}/detector/settings", {"deviceId": DEV}
    assert client.put(url, params=q, json={"schemaVersion": 1}).json()["settings"]["autoTrack"] is False
    _track(client)
    assert client.get(url, params=q, headers=BEARER).json()["settings"]["autoTrack"] is True
    for bad in (1, "true", None, {"on": True}):
        assert client.put(url, params=q, json={"schemaVersion": 1, "autoTrack": bad}).status_code == 422


def test_rule_can_target_a_project(client, world):
    put = lambda rules, v: client.put(RULES, json={"rules": rules}, headers={"If-Match": f'"{v}"'})  # noqa: E731
    r = put([{"title": "blog", "projectId": world["q"]}, {"title": "garden", "taskId": world["a"]}], 0)
    assert r.status_code == 200, r.text
    to_project, to_task = r.json()["rules"]
    assert (to_project["taskId"], to_project["projectId"]) == (None, world["q"])
    assert "projectId" not in to_task  # 到任务的规则与 v1 存下的逐字节相同
    for bad, field in (
        ({"title": "x", "taskId": world["a"], "projectId": world["q"]}, None),   # 都给
        ({"title": "x"}, None),                                                  # 都没给
        ({"title": "x", "projectId": "p_nope"}, "projectId"),                    # 项目不存在
        ({"title": "x", "projectId": {"$ne": ""}}, "projectId"),                 # 不是字符串
    ):
        r = put([bad], 1)
        assert r.status_code == 422, r.text
        assert r.json()["errors"][0]["field"] == field
    # 草稿：没动的到任务规则不算「修改」
    d = client.post(f"{RULES}/drafts", json={"rules": [to_project, to_task], "summary": "不变"}).json()
    assert d["diff"] == {"added": [], "removed": [], "changed": [], "unchanged": 2, "reordered": False}


def test_upload_accepts_a_project_only_rule_guess(client, world):
    def sug(**over):
        return {"taskId": None, "confidence": 0.9, "reason": "网页规则 #1 命中", "classifier": "rules", **over}

    out = _upload(client, [
        _seg(_recent(60), suggestion=sug(projectId=world["q"])),
        _seg(_recent(50), suggestion=sug(projectId="p_nope")),                     # 项目不存在：照收，当没建议
        _seg(_recent(40), suggestion=sug(projectId=world["q"], taskId=world["a"])),  # 都给：拒
        _seg(_recent(30), suggestion=sug(projectId={"$ne": ""})),                  # 不是字符串：拒
    ])
    assert (out["accepted"], [r["index"] for r in out["rejected"]]) == (2, [2, 3])
    gone, kept = (i["suggestion"] for i in _pending(client)["items"])
    assert kept == sug(projectId=world["q"])  # 没有 projectSource：是规则给的，不是按代理会话对上的
    assert "projectId" not in gone and gone["confidence"] == 0
    assert _session_events() == []  # 开关没开：只是建议


# ─────────────────────────────────────────── 心跳带 guess


def test_presence_guess_is_stored_with_the_span_but_not_exposed(client, world, clock):
    _run(client, clock, [(0, "a", _guess(taskId=world["a"])), (15, "a", _guess(taskId=world["a"])),
                         (30, "a", _guess(projectId=world["q"])),   # 同一个窗口、目标变了：新段
                         (45, "a", None)])
    clock(S + 60)
    _beat(client, afk=True, guess=_guess(taskId=world["a"]))  # 离开时带了也不存
    spans = _db()["activity_presence"].find_one({"deviceId": DEV})["spans"]
    assert [s.get("guess") for s in spans] == [
        {"taskId": world["a"], "confidence": 0.9}, {"projectId": world["q"], "confidence": 0.9}, None, None]
    assert all(set(s) == {"deviceId", "from", "to", "app", "title", "afk"} for s in _human(client)["presence"])


@pytest.mark.parametrize("guess", [
    {"taskId": {"$ne": ""}, "confidence": 0.9, "classifier": "rules"},
    {"taskId": ["t_1"], "confidence": 0.9, "classifier": "rules"},
    {"taskId": "t_1", "projectId": "p_1", "confidence": 0.9, "classifier": "rules"},
    {"confidence": 0.9, "classifier": "rules"},
    {"taskId": "t_1", "confidence": True, "classifier": "rules"},
    {"taskId": "t_1", "confidence": 1.5, "classifier": "rules"},
    {"taskId": "t_1", "confidence": 0.9, "classifier": "assistant"},
    {"taskId": "t" * 129, "confidence": 0.9, "classifier": "rules"},
    "t_1",
])
def test_presence_guess_validation_422(client, guess):
    _beat(client, guess=guess, expect=422)
    assert _db()["activity_presence"].count_documents({}) == 0


# ─────────────────────────────────────────── human.auto：每个条件


def test_auto_shows_the_rule_target_with_names(client, world, clock):
    _track(client)
    _run(client, clock, [(0, "plot.gd — garden", _guess(taskId=world["a"]))])
    since = clock(S + 0)
    clock(S + 30)
    human = _human(client)
    assert human["auto"] == {
        "taskId": world["a"], "projectId": world["p"], "taskName": "任务 a", "projectName": "项目 P",
        "since": human["auto"]["since"], "app": "code", "title": "plot.gd — garden", "source": "rules",
        "key": human["auto"]["key"]}  # v2.15 追加 key
    assert human["auto"]["key"].startswith("wk_") and human["aiThinking"] is None
    assert datetime.fromisoformat(human["auto"]["since"]) == since
    assert human["needsChoice"] is None and human["running"] is None


def test_views_current_carries_the_same_auto_and_needs_choice(client, world, clock):
    """顶栏芯片读 views/current：与 views/lanes 的 human 同一份；在计时的时候两个都是 null。"""
    current = lambda: client.get(f"{API}/views/current").json()  # noqa: E731
    assert (current()["auto"], current()["needsChoice"]) == (None, None)   # 开关没开：键在，值 null
    _track(client)
    _run(client, clock, [(0, "notes", None), (30, "notes", None), (60, "notes", None),
                         (75, "plot.gd", _guess(taskId=world["a"]))])
    human, cur = _human(client), current()
    assert cur["running"] is False and cur["auto"]["taskId"] == world["a"] and cur["needsChoice"]["title"] == "notes"
    assert (cur["auto"], cur["needsChoice"]) == (human["auto"], human["needsChoice"])
    _post(client, f"{API}/timer/start", {"taskId": world["a"]})
    cur = current()
    assert cur["running"] is True and (cur["auto"], cur["needsChoice"]) == (None, None)


def test_auto_project_only_and_bucket_have_no_task(client, world, clock):
    _track(client)
    bucket = _post(client, f"{PLANNER}/projects/{world['p']}/unclassified")["taskId"]
    for target, project in ((_guess(projectId=world["q"]), world["q"]), (_guess(taskId=bucket), world["p"])):
        _beat(client, guess=target)
        auto = _human(client)["auto"]
        assert (auto["taskId"], auto["taskName"], auto["projectId"]) == (None, None, project)


@pytest.mark.parametrize("breaker", ["switch_off", "never_set", "timer", "stale", "afk", "no_guess", "task_gone"])
def test_auto_is_null_when_any_condition_fails(client, world, clock, breaker):
    if breaker != "never_set":
        _track(client, on=breaker != "switch_off")
    clock(S)
    _beat(client, guess=None if breaker == "no_guess" else _guess(taskId=world["a"]))
    if breaker == "timer":
        _post(client, f"{API}/timer/start", {"taskId": world["a"]})
    if breaker == "afk":
        clock(S + 15)
        _beat(client, afk=True)
    if breaker == "task_gone":
        assert client.delete(f"{PLANNER}/tasks/{world['a']}").status_code == 204
    clock(S + (91 if breaker == "stale" else 30))
    assert _human(client)["auto"] is None
    if breaker == "timer":  # 手动计时永远优先：停了表，自动才回来
        _post(client, f"{API}/timer/stop")
        assert _human(client)["auto"]["taskId"] == world["a"]


def test_auto_uses_the_most_recent_device(client, world, clock):
    _track(client)                       # DEV 开着
    _track(client, on=False, device="dev_other")
    clock(S)
    _beat(client, guess=_guess(taskId=world["a"]))
    clock(S + 10)
    _beat(client, guess=_guess(taskId=world["a"]), device="dev_other")  # 最近报的是没开开关的那台
    assert _human(client)["auto"] is None


def test_since_follows_the_run_of_the_same_target(client, world, clock):
    _track(client)
    t, q = _guess(taskId=world["a"]), _guess(projectId=world["q"])
    _run(client, clock, [(0, "x", q), (15, "a.py", t), (30, "a.py", t), (45, "b.py", t)])  # 换文件、同一目标：连着
    assert datetime.fromisoformat(_human(client)["auto"]["since"]) == clock(S + 15)
    _run(client, clock, [(60, "x", q)])                                                    # 目标换了：重新起算
    assert datetime.fromisoformat(_human(client)["auto"]["since"]) == clock(S + 60)
    _run(client, clock, [(160, "x", q)])                                                   # 断了 100 秒：重新起算
    assert datetime.fromisoformat(_human(client)["auto"]["since"]) == clock(S + 160)


def test_since_never_predates_the_last_manual_session(client, world, clock):
    _track(client)
    t = _guess(taskId=world["a"])
    _run(client, clock, [(0, "a.py", t), (15, "a.py", t)])
    _post(client, f"{API}/timer/start", {"taskId": world["a"]})
    _run(client, clock, [(30, "a.py", t)])
    stopped = clock(S + 40)
    _post(client, f"{API}/timer/stop")
    _run(client, clock, [(45, "a.py", t)])
    assert datetime.fromisoformat(_human(client)["auto"]["since"]) == stopped


# ─────────────────────────────────────────── needsChoice：停留、抖动、不要求在前台


def test_needs_choice_after_dwell_survives_flicker_and_leaving_the_window(client, world, clock):
    from app.modules.activity import auto  # noqa: PLC0415

    _track(client)
    _run(client, clock, [(0, "✳ notes", None), (15, "⠂ notes", None), (30, "✳ notes", None), (45, "别的", None)])
    assert _human(client)["needsChoice"] is None            # notes 只累计了 45 秒
    _run(client, clock, [(60, "✳ notes", None), (75, "⠂ notes", None)])
    need = _human(client)["needsChoice"]                    # 45 + 15：转圈符号在变、中间切走过，都算同一个窗口
    assert need == {"key": auto.window_key("code", "notes"), "app": "code", "title": "⠂ notes",
                    "since": need["since"]}
    assert datetime.fromisoformat(need["since"]) == clock(S)
    _run(client, clock, [(90, "localhost · HoneyComb", None)])   # 人切到浏览器来回答：问的还是 notes
    assert _human(client)["needsChoice"]["key"] == need["key"]
    # 规则认得出的窗口不问；离开 / 没开开关 / 手动计时在跑：不问
    _run(client, clock, [(100, "✳ notes", _guess(taskId=world["a"]))])
    assert _human(client)["auto"]["taskId"] == world["a"] and _human(client)["needsChoice"]["key"] == need["key"]
    _post(client, f"{API}/timer/start", {"taskId": world["a"]})
    assert _human(client)["needsChoice"] is None
    _post(client, f"{API}/timer/cancel")
    _track(client, on=False)
    assert _human(client)["needsChoice"] is None


def test_needs_choice_ages_out_of_the_lookback(client, clock):
    _track(client)
    _run(client, clock, [(0, "notes", None), (30, "notes", None), (60, "notes", None), (75, "别的", None)])
    assert _human(client)["needsChoice"] is not None
    for sec in range(90, 400, 30):  # 别的窗口每 30 秒换一个标题：谁都攒不够，notes 滑出最近 5 分钟
        _run(client, clock, [(sec, f"页面 {sec}", None)])
    assert _human(client)["needsChoice"] is None


def test_dismiss_silences_that_window_for_hours(client, clock):
    from app.modules.activity import auto  # noqa: PLC0415

    _track(client)
    _run(client, clock, [(0, "notes", None), (30, "notes", None), (60, "notes", None)])
    key = _human(client)["needsChoice"]["key"]
    out = _post(client, f"{API}/activity/choice/dismiss", {"key": key})
    assert datetime.fromisoformat(out["dismissedUntil"]) == clock(S + 60) + auto.DISMISS
    assert _human(client)["needsChoice"] is None
    _run(client, clock, [(75, "notes", None), (90, "notes", None)])
    assert _human(client)["needsChoice"] is None                       # 还在前台也不再问
    later = 60 + auto.DISMISS.total_seconds()
    _run(client, clock, [(later + 1, "notes", None), (later + 31, "notes", None), (later + 61, "notes", None)])
    assert _human(client)["needsChoice"]["key"] == key                 # 过了 4 小时：再问


# ─────────────────────────────────────────── 人答


def _ask(client, clock, title="notes", app="code"):
    _track(client)
    _run(client, clock, [(0, title, None), (30, title, None), (60, title, None)])
    return _human(client)["needsChoice"]["key"]


def test_choice_shows_immediately_and_remember_writes_exactly_one_rule(client, world, clock):
    key = _ask(client, clock)
    out = _post(client, f"{API}/activity/choice", {"key": key, "taskId": world["a"], "remember": True})
    assert out == {"key": key, "taskId": world["a"], "projectId": world["p"], "remembered": True,
                   "pseudonymized": False}
    human = _human(client)
    assert human["needsChoice"] is None
    assert (human["auto"]["taskId"], human["auto"]["source"]) == (world["a"], "choice")
    rules = _rules(client)
    assert rules["version"] == 1 and len(rules["rules"]) == 1
    rule = rules["rules"][0]
    assert rule == {"id": rule["id"], "app": "^code$", "title": "^notes$", "taskId": world["a"], "confidence": 0.9,
                    "note": "计时页选的：code · notes", "enabled": True}  # 与「待确认建议」页写的同形
    # 再答一遍同样的：不多一条，version 不动
    assert _post(client, f"{API}/activity/choice", {"key": key, "taskId": world["a"], "remember": True})["remembered"]
    assert _rules(client)["version"] == 1
    # 改主意换成项目：还是只有一条（同窗口的旧规则去掉）
    out = _post(client, f"{API}/activity/choice", {"key": key, "projectId": world["q"], "remember": True})
    assert (out["taskId"], out["projectId"], out["remembered"]) == (None, world["q"], True)
    rules = _rules(client)["rules"]
    assert len(rules) == 1 and (rules[0]["taskId"], rules[0]["projectId"]) == (None, world["q"])
    assert _human(client)["auto"]["projectId"] == world["q"]
    # 检测程序下一轮带着规则的猜测来：规则优先
    _run(client, clock, [(75, "notes", _guess(projectId=world["q"]))])
    assert _human(client)["auto"]["source"] == "rules"
    assert _session_events() == []  # 人答不写台账


def test_choice_without_remember_writes_no_rule(client, world, clock):
    key = _ask(client, clock)
    out = _post(client, f"{API}/activity/choice", {"key": key, "projectId": world["q"]})
    assert out["remembered"] is False and out["pseudonymized"] is False
    assert _rules(client) == {"version": 0, "updatedAt": None, "rules": []}
    assert _human(client)["auto"]["projectId"] == world["q"]


def test_remember_keeps_other_rules_and_ignores_leading_decoration(client, world, clock):
    client.put(RULES, json={"rules": [{"app": "blender", "taskId": world["a"]}]}, headers={"If-Match": '"0"'})
    key = _ask(client, clock, title="✳ (3) CFO_agent")
    assert _post(client, f"{API}/activity/choice", {"key": key, "taskId": world["a"], "remember": True})["remembered"]
    first, second = _rules(client)["rules"]
    assert second["app"] == "blender"                      # 加在最前面，别的不动
    assert first["title"] == r"^(?:[^\pL\pN]|\(\d+\)|\[\d+\])*CFO_agent$"  # 转圈符号 / 计数不钉死


@pytest.mark.parametrize("how", ["title_is_token", "device_setting"])
def test_remember_on_a_pseudonymizing_device_says_so_and_writes_nothing(client, world, clock, how):
    title = "窗口名3" if how == "title_is_token" else "一个标题"
    key = _ask(client, clock, title=title)
    if how == "device_setting":
        _track(client, titles="pseudonymize")
    out = _post(client, f"{API}/activity/choice", {"key": key, "taskId": world["a"], "remember": True})
    assert (out["remembered"], out["pseudonymized"]) == (False, True)
    assert _rules(client)["rules"] == []
    assert _human(client)["auto"]["taskId"] == world["a"]  # 选择本身照样生效


def test_choice_expires_after_the_window_has_been_away(client, world, clock):
    from app.modules.activity import auto  # noqa: PLC0415

    key = _ask(client, clock)
    _post(client, f"{API}/activity/choice", {"key": key, "taskId": world["a"]})
    away = auto.CHOICE_AWAY.total_seconds()
    for sec in (60 + away - 60, 60 + 2 * away - 120):  # 每次都在失效前回来：一直续着
        _run(client, clock, [(sec, "notes", None)])
        assert _human(client)["auto"]["taskId"] == world["a"]
    _run(client, clock, [(60 + 2 * away - 120 + away + 1, "notes", None)])  # 这次离开满了 30 分钟
    assert _human(client)["auto"] is None
    assert _db()["activity_choices"].count_documents({}) == 0


def test_choice_guards_and_validation(client, world, clock):
    key = _ask(client, clock)
    url = f"{API}/activity/choice"
    ok = {"key": key, "taskId": world["a"]}
    # 设备令牌：先 403，连坏请求体也是 403
    for body in (ok, {"key": 1}):
        assert client.post(url, json=body, headers=BEARER).status_code == 403
        assert client.post(f"{url}/dismiss", json=body, headers=BEARER).status_code == 403
    assert _db()["activity_choices"].count_documents({}) == 0
    for body, status in (
        ({"key": "wk_" + "0" * 20, "taskId": world["a"]}, 404),          # 在场记录里没有这个窗口
        ({"key": key, "taskId": "t_nope"}, 404),
        ({"key": key, "projectId": "p_nope"}, 404),
        ({"key": key}, 400),
        ({"key": key, "taskId": world["a"], "projectId": world["p"]}, 400),
        ({"key": {"$ne": ""}, "taskId": world["a"]}, 422),
        ({"key": [key], "taskId": world["a"]}, 422),
        ({"key": "nope", "taskId": world["a"]}, 422),
        ({"key": key, "taskId": {"$ne": ""}}, 422),
        ({"key": key, "projectId": ["p"]}, 422),
        ({"key": key, "taskId": world["a"], "remember": "yes"}, 422),
        ({"key": key, "taskId": world["a"], "extra": 1}, 422),
        ({"key": key, "taskId": "t" * 129}, 422),
    ):
        assert client.post(url, json=body).status_code == status, body
    for body, status in (({"key": "wk_" + "0" * 20}, 404), ({"key": {"$gt": ""}}, 422), ({}, 422),
                         ({"key": key, "taskId": world["a"]}, 422)):
        assert client.post(f"{url}/dismiss", json=body).status_code == status, body
    assert _db()["activity_choices"].count_documents({}) == 0 and _rules(client)["rules"] == []


# ─────────────────────────────────────────── 自动记录


def _rule_seg(minutes_ago, **over):
    return _seg(_recent(minutes_ago), **over)


def _pin_today(monkeypatch, when):
    """读「今天自动记下的段」时把服务端的「今天」钉在这一段开始的那天：零点后半小时内跑也不会把它算成昨天的。"""
    from app.modules.activity import auto  # noqa: PLC0415

    monkeypatch.setattr(auto, "_now", lambda: when)


def test_auto_entry_records_task_and_project_with_provenance(client, world):
    _track(client)
    to_project = {"taskId": None, "projectId": world["q"], "confidence": 0.95, "reason": "网页规则 #2 命中",
                  "classifier": "rules"}
    first = _rule_seg(60, task_id=world["a"])        # 重传要用同一段（_recent 每次按此刻算，跨秒就成了另一段）
    out = _upload(client, [first, _rule_seg(50, suggestion=to_project)], headers=BEARER)
    assert out == {"accepted": 2, "duplicates": 0, "rejected": []}
    assert _pending(client)["total"] == 0
    done = _pending(client, status="confirmed")["items"]
    assert [i["auto"] for i in done] == [True, True]
    events = {e["subject"]["task"]: e for e in _session_events()}
    assert set(events) == {world["a"], f"t_unc_{world['q']}"}          # 只到项目 → 它的「未分类」桶
    for e in events.values():
        assert e["source"] == "activity-confirmed" and e["dedupeKey"].startswith("activity:sug_")
    assert events[world["a"]]["ai"] == {"generated": True, "confidence": 0.9, "confirmed": False, "auto": True}
    assert events[f"t_unc_{world['q']}"]["ai"]["confidence"] == 0.95
    # 重传：防重命中，不记第二遍；人再点确认 = duplicate
    assert _upload(client, [first], headers=BEARER)["duplicates"] == 1
    again = _post(client, f"{API}/activity/suggestions/{done[0]['id']}/confirm", {"taskId": world["a"]})
    assert again["duplicate"] is True and len(_session_events()) == 2
    # 匹配历史不含自动记下的（那不是人的决定）
    assert client.get(f"{API}/activity/suggestions/history").json()["items"] == []


@pytest.mark.parametrize("why", ["switch_off", "idle", "low_confidence", "service", "task_gone", "agent_session"])
def test_auto_entry_exceptions_stay_pending(client, world, why):
    _track(client, on=why != "switch_off")
    seg = _rule_seg(30, task_id="t_nope" if why == "task_gone" else world["a"],
                    confidence=0.89 if why == "low_confidence" else 0.9, idle=why == "idle")
    if why == "service":
        seg["suggestion"]["classifier"] = "service"
    if why == "agent_session":  # 按代理会话对上的项目（v2.13）不是规则给的：不自动记
        _post(client, f"{API}/agents/start", {"agent": "cc", "tool": "claude-code", "projectId": world["q"],
                                              "label": "CFO_agent"}, expect=201)
        seg = _seg(datetime.now(timezone.utc).replace(microsecond=0) - timedelta(seconds=30), minutes=0, active=20,
                   title="✳ CFO_agent")
        seg["endAt"] = (datetime.fromisoformat(seg["startAt"]) + timedelta(seconds=90)).isoformat()  # 与运行重叠
    assert _upload(client, [seg])["accepted"] == 1
    item = _pending(client)["items"][0]
    assert item["auto"] is False and _session_events() == []
    if why == "agent_session":
        assert item["suggestion"]["projectSource"] == "agent-session"


@pytest.mark.parametrize("timer", ["finished", "running", "backfill_is_not_manual_timing"])
def test_auto_entry_stays_out_of_manually_timed_spans(client, world, clock, timer):
    _track(client)
    if timer == "backfill_is_not_manual_timing":
        _post(client, f"{API}/timer/backfill", {"taskId": world["a"], "startAt": _recent(32).isoformat(),
                                                "durationSeconds": 300})
    else:
        clock(-33 * 60)
        _post(client, f"{API}/timer/start", {"taskId": world["a"]})
        if timer == "finished":
            clock(-28 * 60)
            _post(client, f"{API}/timer/stop")
        clock(0)
    before = len(_session_events())
    # 30 分钟前开始的 4 分钟：与手动计时（33–28 分钟前 / 还在跑）重叠；10 分钟前的那段只在计时还在跑时重叠
    _upload(client, [_rule_seg(30, task_id=world["a"]), _rule_seg(10, task_id=world["a"])])
    pending = _pending(client)["total"]
    assert pending == {"finished": 1, "running": 2, "backfill_is_not_manual_timing": 0}[timer]
    assert len(_session_events()) - before == 2 - pending


def test_auto_entry_failure_never_fails_the_upload(client, world, monkeypatch):
    from app.modules.activity import service  # noqa: PLC0415

    _track(client)
    monkeypatch.setattr(service.timer_service, "record_session",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert _upload(client, [_rule_seg(30, task_id=world["a"])])["accepted"] == 1
    item = _pending(client)["items"][0]
    assert item["status"] == "pending" and item["auto"] is False  # 占的位退了，auto 标记也摘了


def test_auto_sessions_list_and_reassign(client, world, monkeypatch):
    _track(client)
    start = _recent(20)
    _upload(client, [_seg(start, task_id=world["a"]), _rule_seg(10, confidence=0.5, task_id=world["a"])])
    _pin_today(monkeypatch, start)
    human = _pending(client)["items"][0]["id"]
    manual = _post(client, f"{API}/activity/suggestions/{human}/confirm", {"taskId": world["a"]})["event"]["id"]
    items = client.get(f"{API}/activity/auto", headers=BEARER).json()["items"]  # 只读，设备令牌也能读
    assert len(items) == 1
    item = items[0]
    assert set(item) == {"id", "eventId", "startAt", "endAt", "durationSeconds", "app", "title", "taskId",
                         "projectId", "reassigned", "ai"}  # v2.15 追加 ai
    assert item["ai"] is False
    assert (item["taskId"], item["projectId"], item["reassigned"]) == (world["a"], world["p"], False)

    url = f"{API}/sessions/{item['eventId']}/reassign"
    assert client.post(url, json={"projectId": world["q"]}, headers=BEARER).status_code == 403
    moved = _post(client, url, {"projectId": world["q"]})       # 记错了：放到另一个项目的未分类
    assert (moved["fromTaskId"], moved["taskId"], moved["seq"]) == (world["a"], f"t_unc_{world['q']}", 1)
    item = client.get(f"{API}/activity/auto").json()["items"][0]
    assert (item["taskId"], item["projectId"], item["reassigned"]) == (f"t_unc_{world['q']}", world["q"], True)
    assert _post(client, url, {"taskId": world["a"]})["seq"] == 2   # 可以再改回来
    # 人自己确认的、不在桶上的段：照旧不能改挂
    assert client.post(f"{API}/sessions/{manual}/reassign", json={"projectId": world["q"]}).status_code == 409


# ─────────────────────────────────────────── 租户隔离


def test_tenant_isolation(client, world, clock, monkeypatch):
    key = _ask(client, clock)
    _post(client, f"{API}/activity/choice", {"key": key, "taskId": world["a"], "remember": True})
    start = _recent(30)
    _upload(client, [_seg(start, task_id=world["a"])])
    _pin_today(monkeypatch, start)
    assert len(client.get(f"{API}/activity/auto").json()["items"]) == 1
    # 另一个租户：看不到在场、选择、规则、自动记下的段；拿着别人的 key 是 404
    human = _human(client, headers=B)
    assert human["auto"] is None and human["needsChoice"] is None and human["presence"] == []
    assert client.post(f"{API}/activity/choice", json={"key": key, "taskId": world["a"]}, headers=B).status_code == 404
    assert client.post(f"{API}/activity/choice/dismiss", json={"key": key}, headers=B).status_code == 404
    assert _rules(client, headers=B)["rules"] == [] and client.get(f"{API}/activity/auto", headers=B).json() == {
        "items": []}
    # 同一个窗口在 B 那边出现：A 的选择不跟过去，B 的开关也没开
    _beat(client, title="notes", headers=B)
    assert _human(client, headers=B)["auto"] is None
    assert _db()["activity_choices"].count_documents({"user": "ch_bbbb"}) == 0
