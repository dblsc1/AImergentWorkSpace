"""忽略并记住（契约 v2.22）第二轮审查：每一个可能留着被忽略窗口标题的地方。

要害：全库倾倒——建规则之后、再有写入之后，序列化每个集合的每份文档，找标记标题；
出现标记的集合只许是**声明过的例外**（规则本身存人填的匹配文字、已确认的建议与台账里的事实）。以后新加的存储漏了这里就红。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from test_activity_ignore import DEV, IGN, SUG, _beat as _plain_beat, _ignore, _raw_has, _seg, _upload  # noqa: F401
from test_auto_ai import REJECT, SUGGEST, _answer, _waiting  # noqa: F401
from test_auto_track import API, RULES, _db, _human, _post, _rules, world  # noqa: F401
from test_auto_track import clock  # noqa: F401

MARK = "标记窗口-7731"
#: 声明过的例外：规则存人填的匹配文字；已确认的建议 / 台账 / 投影是人确认过的事实（契约「已确认的不动」）
DECLARED = {"activity_ignores", "activity_suggestions", "events", "proj_lanes", "proj_daily_stats"}


def _dump(marker: str) -> set[str]:
    return set(_raw_has(marker))


# ─────────────────────────────────────────── 全库倾倒


def test_whole_database_has_the_marker_only_in_declared_places(client, world, clock):  # noqa: F811
    from test_auto_ai import _claim  # noqa: PLC0415
    from test_auto_track import S, _beat, _run, _track  # noqa: PLC0415

    task = world["a"]
    _upload(client, [_seg(10, "code", MARK), _seg(20, "code", MARK + " 二"), _seg(30, "code", "别的")])
    mine = [i for i in client.get(SUG, params={"status": "pending"}).json()["items"] if i["title"].startswith(MARK)]
    client.post(f"{SUG}/{mine[0]['id']}/dismiss")
    w = _waiting(client, clock, MARK)  # 在场、AI 问询
    _answer(client, w["key"], taskId=task, confidence=0.9)  # AI 代写的规则
    client.post(f"{RULES}/drafts", json={"summary": f"为 {MARK} 起草", "author": "assistant",
                                         "rules": [{"app": "^code$", "title": "^" + MARK + "$", "taskId": task}]})
    _post(client, f"{API}/activity/choice/dismiss", {"key": w["key"]}, expect=(200, 404))
    assert {"activity_presence", "activity_ai_asks", "detector_rules"} <= _dump(MARK)  # 前提：它们确实存过

    _ignore(client, "code", MARK)
    assert _dump(MARK) <= DECLARED, _dump(MARK)
    # 再有写入（上传 / 心跳 / 又一轮）之后仍然如此
    _upload(client, [_seg(40, "code", MARK + " 三")])
    _track(client)
    _run(client, clock, [(70, MARK, None), (100, MARK, None)])
    clock(S + 120)
    assert _claim(client) is None
    assert _dump(MARK) <= DECLARED, _dump(MARK)
    assert "activity_ignores" in _dump(MARK)  # 规则本身存的是人填的匹配文字


# ─────────────────────────────────────────── I1：AI 代写的规则与草稿


def test_ai_written_rule_and_draft_are_purged_human_rules_stay(client, world, clock):  # noqa: F811
    w = _waiting(client, clock, MARK)
    _answer(client, w["key"], taskId=world["a"], confidence=0.9, reason="像是 a")
    human = client.put(RULES, json={"rules": [*_rules(client)["rules"], {"id": "r_human", "app": "^code$", "title": MARK,
                                                                     "taskId": world["a"], "author": "human"}]},
                       headers={"If-Match": '"1"'})
    assert human.status_code == 200, human.text
    draft = client.post(f"{RULES}/drafts", json={"summary": "x", "author": "assistant", "rules": [
        {"app": "^code$", "title": "^" + MARK + "$", "taskId": world["a"]}]})
    assert draft.status_code == 201, draft.text
    _ignore(client, "code", MARK)
    ids = [r["id"] for r in _rules(client)["rules"]]
    assert ids == ["r_human"]  # AI 那条没了，人写的留着
    assert client.get(f"{RULES}/drafts/current").json()["draft"] is None


def test_suggest_refuses_an_ignored_window(client, world, clock, monkeypatch):  # noqa: F811
    from app.modules.activity import auto  # noqa: PLC0415

    w = _waiting(client, clock, MARK)
    real = auto._find

    def find_then_ignore(user, key):
        out = real(user, key)
        assert client.post(IGN, json={"app": "code", "titleContains": MARK}).status_code == 201
        return out

    monkeypatch.setattr(auto, "_find", find_then_ignore)
    _answer(client, w["key"], 409, taskId=world["a"], confidence=0.9)
    assert [r.get("auto") for r in _rules(client)["rules"]] == []


# ─────────────────────────────────────────── I2：匹配文字只给人


def test_title_filter_text_is_only_for_the_human(client):
    _ignore(client, "code", MARK)
    human = client.get(IGN).json()["items"][0]
    assert human["titleContains"] == MARK
    for headers in ({"Authorization": "Bearer t"}, {"X-Nexus-Scope": "read", "Authorization": "Bearer t"},
                    {"X-Nexus-Scope": "read"}):
        row = client.get(IGN, headers=headers).json()["items"][0]
        assert "titleContains" not in row and row["hasTitleFilter"] is True and MARK not in json.dumps(row, ensure_ascii=False)


# ─────────────────────────────────────────── I3 / I9：同一个归一化

VARIANTS = [("code", "foo  bar  7731"), ("code", "foo\tbar\n7731"), ("Code", "ｆｏｏ bar 7731"), ("code", "f​oo bar 7731")]


@pytest.mark.parametrize("app,title", VARIANTS)
def test_normalised_match_through_upload_and_beat(client, app, title):
    _ignore(client, "code", "foo  bar  7731")  # 规则里的空白被折叠；匹配两边同一个归一化
    assert _upload(client, [_seg(10, app, "x " + title + " y")]).get("ignored") == 1
    _plain_beat(client, app, "x " + title + " y")
    assert _dump("7731") <= {"activity_ignores"}


@pytest.mark.parametrize("app", [" code", "code ", "ＣＯＤＥ", "co​de"])
def test_app_variants_are_ignored(client, app):
    _ignore(client, "code")
    assert _upload(client, [_seg(10, app, "7731")]).get("ignored") == 1
    _plain_beat(client, app, "7731")
    assert _dump("7731") == set()


def test_app_equality_is_exact_after_normalisation(client):
    _ignore(client, "code")
    assert _upload(client, [_seg(10, "code.exe", "7731")]).get("ignored") is None  # 不猜后缀


def test_purge_matches_stored_titles_with_odd_whitespace(client):
    _upload(client, [_seg(10, "code", "x foo   bar\t7731 y")])
    removed = _ignore(client, "code", "foo bar 7731")["removed"]
    assert removed == 1 and _dump("7731") <= {"activity_ignores"}


# ─────────────────────────────────────────── I4：purge 扫到底


def test_purge_scans_every_pending_suggestion(client):
    now = datetime.now(timezone.utc)
    docs = [{"user": "u_local", "id": f"sug_m{i}", "dedupeKey": f"m{i}", "status": "pending", "app": "code", "title": MARK, "durationSeconds": 3,
             "startTs": now, "endTs": now} for i in range(5)]  # 最早插入的 5 条是命中的
    docs += [{"user": "u_local", "id": f"sug_o{i}", "dedupeKey": f"o{i}", "status": "pending", "app": "code", "title": "别的", "durationSeconds": 3,
              "startTs": now, "endTs": now} for i in range(5400)]
    _db()["activity_suggestions"].insert_many(docs)
    out = _ignore(client, "code", MARK)
    assert out["removed"] == 5 and _dump(MARK) <= {"activity_ignores"}
    assert _db()["activity_suggestions"].count_documents({}) == 5400


# ─────────────────────────────────────────── I5：删了又重建


def test_delete_and_recreate_during_an_upload_still_purges(client, monkeypatch):
    from app.modules.activity import ignore  # noqa: PLC0415

    rule = _ignore(client, "code", MARK)
    real = ignore.gate_incoming

    def delete_drop_recreate(*args, **kwargs):
        assert client.delete(f"{IGN}/{rule['id']}").status_code == 204
        out = real(*args, **kwargs)  # 这一刻没有规则：整批放行
        _ignore(client, "code", MARK)  # 同一条规则（同 id）又建回来，清理发生在写入之前
        return out

    monkeypatch.setattr(ignore, "gate_incoming", delete_drop_recreate)
    _upload(client, [_seg(10, "code", MARK)])
    assert _dump(MARK) <= {"activity_ignores"}


# ─────────────────────────────────────────── I6：清理失败后读也不交出残留


def test_failed_purge_masks_reads_instead_of_failing_them(client, monkeypatch):
    from app.modules.activity import repo  # noqa: PLC0415

    _upload(client, [_seg(10, "code", MARK)])
    _plain_beat(client, "code", MARK)
    monkeypatch.setattr(repo, "delete_pending", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    resp = client.post(IGN, json={"app": "code", "titleContains": MARK})
    assert resp.status_code == 503 and "规则已保存" in resp.json()["detail"] and "没有记住" not in resp.text
    assert {"activity_suggestions", "activity_presence"} <= _dump(MARK)  # 清理没做完：库里确有残留
    for url in (f"{API}/views/current", f"{API}/views/lanes", SUG, IGN):
        got = client.get(url)
        assert got.status_code == 200, url  # 不因此 5xx
        assert MARK not in got.text or url == IGN, url  # 读出来的副本里没有残留的标题（规则列表里是人填的匹配文字）


# ─────────────────────────────────────────── 安全审查：怪形状的规则 / 草稿、永远清不掉的规则不许拖垮别的写入


def _raw_rules(rules, draft=None):
    doc = {"user": "u_local", "version": 1, "rules": rules}
    if draft is not None:
        doc["draft"] = draft
    _db()["detector_rules"].replace_one({"user": "u_local"}, doc, upsert=True)


def test_odd_shaped_rules_and_drafts_never_break_the_purge_and_only_provable_ones_go(client):
    ai = {"author": "assistant", "taskId": "t"}
    _raw_rules([
        {"id": "gen", "app": "^code$", "title": "^" + MARK + "$", **ai},                       # 生成形状：出自被忽略的窗口 → 删
        {"id": "gen_decor", "app": "^code$", "title": "^(?:[^\\pL\\pN]|\\(\\d+\\)|\\[\\d+\\])*" + MARK + "$", **ai},  # 带前缀 → 删
        {"id": "apponly", "app": "^code$", **ai},                                              # 只有程序名、没有 title 键 → app 整个忽略时才删
        {"id": "regex", "app": "^code$", "title": "(a+)+$", **ai},                             # 任意正则：不是生成形状 → 留（也不编译）
        {"id": "wild", "app": "^code$", "title": ".*", **ai},                                  # 同上
        {"id": "human", "app": "^code$", "title": "^" + MARK + "$", "taskId": "t", "author": "human"},   # 人写的 → 留
        {"id": "legacy", "app": "^code$", "title": "^" + MARK + "$", "taskId": "t"},           # 没有 author 的老规则 → 留
        {"id": "notitle_none", "app": "^code$", "title": None, **ai},
        {"id": "noapp", "title": "^" + MARK + "$", **ai},                                      # 没有 app → 证明不了 → 留
        {"id": "typo", "app": 7, "title": ["x"], **ai},                                        # 类型怪 → 留
        {"id": "long", "app": "^code$", "title": "^" + "a" * 5000 + "$", **ai},
        "not-a-dict", None,
    ], draft={"id": "drf_x", "author": "assistant", "summary": None, "rules": None, "createdAt": datetime.now(timezone.utc),
              "expiresAt": datetime.now(timezone.utc) + timedelta(hours=1), "baseVersion": 1})
    _ignore(client, "code", MARK)
    kept = [r["id"] for r in _db()["detector_rules"].find_one({"user": "u_local"})["rules"] if isinstance(r, dict)]
    assert kept == ["apponly", "regex", "wild", "human", "legacy", "notitle_none", "noapp", "typo", "long"]
    assert _db()["detector_rules"].find_one({"user": "u_local"}).get("draft") is not None  # 没有规则、摘要为空：不碰
    _ignore(client, "code")  # 整个程序忽略：只有程序名的 AI 规则也出自它
    assert "apponly" not in [r["id"] for r in _db()["detector_rules"].find_one({"user": "u_local"})["rules"] if isinstance(r, dict)]


def test_a_permanently_failing_purge_does_not_break_other_windows_and_is_visible_to_the_human(client, monkeypatch):
    from datetime import timedelta as td  # noqa: PLC0415

    from app.modules.activity import ignore  # noqa: PLC0415
    from app.modules.detector import window_rules  # noqa: PLC0415

    calls = []

    def boom(*a, **k):
        calls.append(1)
        raise RuntimeError("bad document")

    monkeypatch.setattr(window_rules, "drop_ignored", boom)
    monkeypatch.setattr(ignore, "PURGE_BACKOFF", td(0))
    assert client.post(IGN, json={"app": "code", "titleContains": MARK}).status_code == 503
    for _ in range(ignore.PURGE_GIVE_UP + 4):  # 别的窗口的上传 / 心跳：永远 200
        assert _upload(client, [_seg(10, "chrome", "别的窗口")])["accepted"] >= 0
        _plain_beat(client, "chrome", "别的窗口")
    assert len(calls) <= ignore.PURGE_GIVE_UP + 1  # 放弃自动重试：不是每次写入都试
    row = client.get(IGN).json()["items"][0]
    assert row["purgeFailed"] is True  # 人看得到
    # 被忽略窗口自己的数据照样存不进去
    assert _upload(client, [_seg(10, "code", MARK + " 新")]).get("ignored") == 1
    _plain_beat(client, "code", MARK + " 新")
    assert _dump(MARK + " 新") == set()
    # 再建一次同一条规则会重清
    monkeypatch.undo()
    assert client.post(IGN, json={"app": "code", "titleContains": MARK}).status_code == 201
    assert client.get(IGN).json()["items"][0]["purgeFailed"] is False


def test_purge_over_budget_is_incomplete_not_failed_and_resumes(client, monkeypatch):
    from app.modules.activity import ignore  # noqa: PLC0415

    now = datetime.now(timezone.utc)
    _db()["activity_suggestions"].insert_many([{"user": "u_local", "id": f"s{i}", "dedupeKey": f"k{i}", "status": "pending", "app": "code",
                                                "title": MARK if i == 7 else "x", "durationSeconds": 1, "startTs": now, "endTs": now}
                                               for i in range(10)])
    monkeypatch.setattr(ignore, "SCAN_BUDGET", 5)
    assert client.post(IGN, json={"app": "code", "titleContains": MARK}).status_code == 503
    assert client.get(IGN).json()["items"][0]["purgeFailed"] is False  # 预算截断不算失败
    monkeypatch.setattr(ignore, "SCAN_BUDGET", 50_000)
    monkeypatch.setattr(ignore, "PURGE_BACKOFF", __import__("datetime").timedelta(0))
    _plain_beat(client, "chrome", "别的")  # 下一次写入接着清
    assert _dump(MARK) <= {"activity_ignores"}


# ─────────────────────────────────────────── I10：dismiss / claim / reject 的守卫


def _ignore_after(client, monkeypatch, module, name, **rule):
    """module.name 先照常跑完，再建规则——读了旧规则的写入者写在清理之后的交错。"""
    real = getattr(module, name)

    def wrapper(*args, **kwargs):
        out = real(*args, **kwargs)
        assert client.post(IGN, json=rule).status_code == 201
        return out

    monkeypatch.setattr(module, name, wrapper)


def test_dismiss_guard(client, clock, monkeypatch):  # noqa: F811
    from app.modules.activity import auto  # noqa: PLC0415
    from test_auto_track import S, _beat  # noqa: PLC0415

    clock(S)
    _beat(client, title=MARK)
    key = auto.window_key("code", MARK)
    _ignore_after(client, monkeypatch, auto, "_find", app="code", titleContains=MARK)
    assert client.post(f"{API}/activity/choice/dismiss", json={"key": key}).status_code == 200
    assert _dump(MARK) <= {"activity_ignores"}


def test_reject_guard(client, world, clock, monkeypatch):  # noqa: F811
    from app.modules.activity import auto_ai  # noqa: PLC0415

    w = _waiting(client, clock, MARK)
    _answer(client, w["key"], taskId=world["a"], confidence=0.9)
    _ignore_after(client, monkeypatch, auto_ai.window_rules, "remove_auto", app="code", titleContains=MARK)
    assert client.post(REJECT, json={"key": w["key"]}).status_code == 200
    assert _dump(MARK) <= {"activity_ignores"}


def test_claim_guard(client, clock, monkeypatch):  # noqa: F811
    from app.modules.activity import ask_repo  # noqa: PLC0415
    from test_auto_ai import _dwell  # noqa: PLC0415
    from test_auto_track import S  # noqa: PLC0415

    _dwell(client, clock, MARK)
    clock(S + 61)
    real = ask_repo.claim

    def create_then_claim(*args, **kwargs):  # 窗口是在规则之前看到的；认领写下问询时规则已经存在
        assert client.post(IGN, json={"app": "code", "titleContains": MARK}).status_code == 201
        return real(*args, **kwargs)

    monkeypatch.setattr(ask_repo, "claim", create_then_claim)
    assert client.post(f"{API}/activity/ai/claim").status_code == 200
    assert _dump(MARK) <= {"activity_ignores"}


# ─────────────────────────────────────────── （A）写入闸门在残留清理永远失败时也不松

def _span(title, ago, seconds, guess=True):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    sp = {"app": "code", "title": title, "from": (now - timedelta(seconds=ago)).isoformat(), "seconds": seconds}
    if guess:
        sp["guess"] = {"projectId": "p_x", "confidence": 0.9, "classifier": "rules"}
    return now, sp


def test_write_gate_holds_while_the_leftover_purge_fails_permanently(client, world, clock, monkeypatch):  # noqa: F811
    from app.modules.activity import ignore  # noqa: PLC0415

    w = _waiting(client, clock, MARK)  # 规则之前就在场、被 AI 认领的窗口
    _ignore(client, "code", MARK)
    _db()["activity_ignores"].update_many({}, {"$set": {"purged": False}})  # 残留清理「永远没做完」
    monkeypatch.setattr(ignore, "purge_leftovers", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("never works")))
    # 上传：匹配的丢、不匹配的照常 2xx
    assert _upload(client, [_seg(10, "code", MARK + " a"), _seg(20, "chrome", "别的")]) == {"accepted": 1, "duplicates": 0, "rejected": [], "ignored": 1}
    # 心跳：顶层 + 每个 span（含 guess）都抹掉；不匹配的窗口照常 2xx
    now, s1 = _span(MARK + " b", 30, 10)
    _, s2 = _span("别的", 15, 10)
    resp = client.post(f"{API}/activity/presence", json={"deviceId": DEV, "app": "code", "title": MARK + " c", "afk": False,
                                                          "guess": {"projectId": "p_x", "confidence": 0.9, "classifier": "rules"},
                                                          "sentAt": now.isoformat(), "spans": [s1, s2]})
    assert resp.status_code == 200, resp.text
    doc = _db()["activity_presence"].find_one({"deviceId": DEV})
    assert (doc["app"], doc["title"]) == ("", "") and not doc.get("guess")
    blank = [sp for sp in doc["spans"] if sp["app"] == ""]
    assert blank and all(sp["title"] == "" and "guess" not in sp for sp in blank)
    # AI 回答：窗口已被忽略（问询也被清掉）→ 拒绝，什么都没写
    out = client.post(f"{API}/activity/ai/suggest", json={"key": w["key"], "taskId": world["a"], "confidence": 0.9, "reason": "r"})
    assert out.status_code in (404, 409)
    assert _rules(client)["rules"] == []
    assert _dump(MARK) <= DECLARED, _dump(MARK)
    assert _dump(MARK) == {"activity_ignores"}


# ─────────────────────────────────────────── 闸门看的就是被存下的那个字段：归一化 / 截断 / 字段全覆盖 / 状态漂移

NORM_FRAGMENT = "alpha beta 7731"
NORM_TITLE = "ａｌｐｈａ\u200b  beta\t７７３１ tail"        # 只有经 NFKC / 去零宽 / 折叠空白才命中
LONG_TITLE = "LONGMARK" + "y" * 2900 + " alpha beta 7731 end"  # 比所有截断上限（1024 / 512 / 128）都长，命中的片段在最末尾


def _beat_raw(client, app, title, spans=None, **extra):
    body = {"deviceId": DEV, "app": app, "title": title, "afk": False, **extra}
    if spans is not None:
        body["spans"] = spans
    resp = client.post(f"{API}/activity/presence", json=body)
    assert resp.status_code == 200, resp.text


def test_dump_normalised_and_over_long_titles_never_stored_before_or_after_the_rule(client):
    now, norm_span = _span(NORM_TITLE, 20, 10)
    _, long_span = _span(LONG_TITLE, 8, 5)
    _upload(client, [_seg(10, "code", NORM_TITLE)])
    _beat_raw(client, "code", NORM_TITLE, spans=[norm_span], sentAt=now.isoformat())
    assert {"activity_suggestions", "activity_presence"} <= _dump("ａｌｐｈａ")
    _ignore(client, "code", NORM_FRAGMENT)  # 规则里存的是半角、单空格的片段
    assert _dump("ａｌｐｈａ") == set()
    # 规则之前就被截断存下的长标题（命中片段在被截掉的尾巴里）认不出来——声明过的残留（契约「已知的残留」）；规则之后的写入看的是收到的全文
    for title, span in ((NORM_TITLE, norm_span), (LONG_TITLE, long_span)):  # 规则之后的写入：整个丢 / 抹
        assert _upload(client, [_seg(5, "code", title + "2")]).get("ignored") == 1
        _beat_raw(client, "code", title, spans=[span], sentAt=now.isoformat())
    assert _dump("ａｌｐｈａ") == set() and _dump("LONGMARK") == set()


def test_duplicate_json_keys_gate_the_value_that_is_parsed_and_stored(client):
    _ignore(client, "code", MARK)
    raw = ('{"deviceId": "%s", "app": "code", "title": "ok", "title": "%s", "afk": false}' % (DEV, MARK)).encode()
    assert client.post(f"{API}/activity/presence", content=raw, headers={"content-type": "application/json"}).status_code == 200
    assert _dump(MARK) == {"activity_ignores"}


#: 守卫端点的请求模型里每个字符串字段：要么是窗口文字（闸门按它匹配，随整项丢 / 抹），要么证明不是窗口文字。
#: 新加了字符串字段却没归类，这条测试就红——到这里来决定它会不会带标题。
GATED = {"PresenceIn.app", "PresenceIn.title", "Span.app", "Span.title", "_Segment.app", "_Segment.title"}
DROPPED_WITH_ITEM = {"_Suggestion.reason", "_Suggestion.collection", "_NewTask.name", "_Collection.name"}  # 随整段建议一起丢
NOT_WINDOW_TEXT = {"PresenceIn.deviceId", "Guess.taskId", "Guess.projectId", "Guess.classifier", "_Segment.startAt", "_Segment.endAt",
                   "_Suggestion.taskId", "_Suggestion.classifier", "_Suggestion.projectId", "_NewTask.projectId",
                   "_Collection.id", "_Collection.key", "UploadIn.deviceId", "_Suggestion.confidence"}


def _string_fields():
    import typing  # noqa: PLC0415

    from app.modules.activity import presence_router, router, service  # noqa: PLC0415

    seen, out = set(), set()

    def is_str(ann) -> bool:
        return ann is str or (typing.get_origin(ann) is typing.Annotated and is_str(typing.get_args(ann)[0])) \
            or "str" in getattr(ann, "__name__", "").lower() or any(is_str(a) for a in typing.get_args(ann)
                                                                    if not isinstance(a, (int, float, type(None))))

    def models_in(ann) -> list:
        if hasattr(ann, "model_fields"):
            return [ann]
        return [m for a in typing.get_args(ann) for m in models_in(a)]

    def walk(model):
        if model in seen:
            return
        seen.add(model)
        for name, f in model.model_fields.items():
            subs = models_in(f.annotation)
            for sub in subs:
                walk(sub)
            if is_str(f.annotation) and not subs:
                out.add(f"{model.__name__}.{name}")

    for m in (presence_router.PresenceIn, router.UploadIn, service._Segment):
        walk(m)
    return out


def test_every_string_field_of_the_guarded_request_models_is_classified():
    fields = _string_fields()
    known = GATED | DROPPED_WITH_ITEM | NOT_WINDOW_TEXT
    assert fields - known == set(), f"新的字符串字段没归类（会不会带窗口标题？）：{sorted(fields - known)}"
    assert GATED <= fields


def test_one_normalisation_function_everywhere_and_no_process_cache():
    import pathlib  # noqa: PLC0415
    import re  # noqa: PLC0415

    base = pathlib.Path(__file__).parent.parent / "app" / "modules" / "activity"
    for name in ("ignore.py", "ignore_repo.py"):  # ignore_router 里的 .strip() 是在看 Authorization 头，不是窗口文字
        text = base.joinpath(name).read_text(encoding="utf-8")
        code = "\n".join(l.split("#")[0] for l in text.splitlines())
        assert not re.search(r"\.(lower|upper|casefold|strip)\(", code), name  # 比较只许走 textfold.fold
        assert not re.search(r"lru_cache|functools\.cache|_cache\b", code), name  # 规则不许在进程里缓存


def test_a_rule_in_backoff_or_given_up_still_gates_every_write(client):
    _ignore(client, "code", MARK)
    _db()["activity_ignores"].update_many({}, {"$set": {"purged": False, "purgeFailed": True, "purgeTries": 9,
                                                         "purgeLastTry": datetime.now(timezone.utc)}})
    assert _upload(client, [_seg(10, "code", MARK)]).get("ignored") == 1
    _plain_beat(client, "code", MARK)
    assert _dump(MARK) == {"activity_ignores"}


def test_purged_is_only_set_after_a_clean_verification_and_a_recreated_rule_starts_unpurged(client, monkeypatch):
    from app.modules.activity import ignore, ignore_repo  # noqa: PLC0415

    _plain_beat(client, "code", MARK)
    # 校验发现残留（清理步骤被改成什么都没做）→ 不标 purged，读路径继续遮
    monkeypatch.setattr(ignore_repo, "mask_presence", lambda *a, **k: 0)
    assert client.post(IGN, json={"app": "code", "titleContains": MARK}).status_code == 503
    assert [r["purged"] for r in ignore_repo.all_rules("u_local")] == [False]
    monkeypatch.undo()
    # 清理中途规则被删了又建回来：旧的那次清理标不上新的那条
    rule = ignore_repo.all_rules("u_local")[0]
    assert client.delete(f"{IGN}/{rule['id']}").status_code == 204
    _ignore(client, "code", MARK)
    assert ignore_repo.mark_purged("u_local", rule["id"], rule["createdAt"]) is False
    with pytest.raises(ignore._Gone):
        ignore.purge("u_local", rule)  # 删掉的（旧的）规则：清理到此为止


# ─────────────────────────────────────────── 有界：闸门只看前 GATE_CHARS 个码点，存下的是它的前缀


def test_gate_work_is_bounded_and_stored_text_is_a_prefix_of_what_the_gate_saw(client, monkeypatch):
    from app.modules.activity import ignore, presence  # noqa: PLC0415

    seen = []
    real = ignore.fold
    monkeypatch.setattr(ignore, "fold", lambda t: (seen.append(len(t or "")), real(t))[1])
    _ignore(client, "code", "needle")
    seen.clear()
    big = "z" * 40_000  # 远超 GATE_CHARS，也远超所有存储上限
    now = datetime.now(timezone.utc).replace(microsecond=0)
    spans = [{"app": big + str(i), "title": big + "t", "from": (now - timedelta(seconds=118 - 3.6 * i)).isoformat(), "seconds": 3}
             for i in range(presence.MAX_BEAT_SPANS)]
    _beat_raw(client, big, big, spans=spans, sentAt=now.isoformat())
    assert _upload(client, [_seg(5, big, big)])["accepted"] == 1
    assert max(seen) <= ignore.GATE_CHARS  # 没有任何一次归一化碰过无界的串
    rules_n, strings = 1, 2 * (1 + presence.MAX_BEAT_SPANS) + 2  # 心跳顶层 + 每个 span 的 app / title，加上传的一段
    assert len(seen) <= 4 * rules_n * strings  # 每串每条规则至多 4 次 fold：总量由 规则数 × 串数 × GATE_CHARS 封顶
    doc = _db()["activity_presence"].find_one({"deviceId": DEV})
    assert len(doc["app"]) <= 128 and len(doc["title"]) <= 512  # 存储上限在闸门之后
    assert big.startswith(doc["app"]) and big.startswith(doc["title"])  # 存下的是收到的串的前缀


def test_a_fragment_beyond_gate_chars_is_not_seen_documented_limit(client):
    from app.modules.activity import ignore  # noqa: PLC0415

    _ignore(client, "code", "needle")
    title = "q" * ignore.GATE_CHARS + " needle"
    assert _upload(client, [_seg(5, "code", title)]).get("ignored") is None  # 看不到：存下的前缀（512）也装不下它
    assert "needle" not in json.dumps(list(_db()["activity_suggestions"].find({}, {"_id": 0})), default=str)
