"""忽略并记住（契约 v2.22）的写入闸门：匹配的窗口在入口就丢 / 抹，不存；怎么变形都躲不过（归一化、不可见字符、截断、每个字段）。

不在这里的承诺：已经记下的东西不被清（契约「这不是隐私擦除」）；读接口不看忽略规则。
"""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from test_activity_ignore import API, DEV, IGN, SUG, _beat as _plain_beat, _db, _ignore, _raw_has, _seg, _upload

MARK = "标记窗口-7731"


def _dump(marker: str) -> set[str]:
    return set(_raw_has(marker))


def _boom(*_a, **_k):
    raise RuntimeError("boom")




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


def _span(title, ago, seconds, guess=True):
    return _span_at(datetime.now(timezone.utc).replace(microsecond=0), title, ago, seconds, guess)


def _span_at(now, title, ago, seconds, guess=True):
    sp = {"app": "code", "title": title, "from": (now - timedelta(seconds=ago)).isoformat(), "seconds": seconds}
    if guess:
        sp["guess"] = {"projectId": "p_x", "confidence": 0.9, "classifier": "rules"}
    return now, sp


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


def test_normalised_and_over_long_titles_are_never_stored(client):
    now, norm_span = _span(NORM_TITLE, 20, 10)
    _, long_span = _span(LONG_TITLE, 8, 5)
    _ignore(client, "code", NORM_FRAGMENT)  # 规则里存的是半角、单空格的片段
    for title, span in ((NORM_TITLE, norm_span), (LONG_TITLE, long_span)):  # 闸门看的是收到的全文：命中的片段在被截掉的尾巴里也认得出
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


# ─────────────────────────────────────────── 不可见字符 / 折叠次数 / 200 条上限 / 出错当作被忽略

INVISIBLES = ["\u00ad", "\u200e", "\u200f", "\u034f", "\ufe0f", "\ufe00", "\u180e", "\u202a", "\u202e", "\u200b", "\u200d",
              "\u2060", "\ufeff", "\U000e0100", "\U000e01ef", "\u061c", "\u2066"]


@pytest.mark.parametrize("cp", INVISIBLES)
def test_invisible_code_points_inside_the_fragment_do_not_evade_the_rule(client, cp):
    from app.textfold import fold  # noqa: PLC0415

    assert fold("a" + cp + "b") == "ab"
    _ignore(client, "code", "needle 7731")
    title = "x nee" + cp + "dle 77" + cp + "31 y"
    assert _upload(client, [_seg(10, "code", title)]).get("ignored") == 1
    _plain_beat(client, "code", title)
    assert _db()["activity_presence"].find_one({"deviceId": DEV})["title"] == ""
    assert _dump("7731") <= {"activity_ignores"} and _dump("nee") <= {"activity_ignores"}


@pytest.mark.parametrize("cp", INVISIBLES[:6])
def test_invisible_code_points_in_the_rule_text_match_clean_titles(client, cp):
    _ignore(client, "code", "nee" + cp + "dle 7731")
    assert _upload(client, [_seg(10, "code", "x needle 7731")]).get("ignored") == 1



def test_folds_are_linear_in_strings_plus_rules(client, monkeypatch):
    from app.modules.activity import ignore, presence  # noqa: PLC0415

    R, now = 200, datetime.now(timezone.utc)
    _db()["activity_ignores"].insert_many([{"user": "u_local", "id": f"ig_{i}", "app": "code", "titleContains": f"needle{i}",
                                            "createdAt": now + timedelta(microseconds=i), "hits": 0, "seconds": 0}
                                           for i in range(R)])
    seen, real = [], ignore.fold
    monkeypatch.setattr(ignore, "fold", lambda t: (seen.append(1), real(t))[1])
    assert _upload(client, [_seg(10 + i, "code", f"title {i}") for i in range(33)])["accepted"] == 33
    assert len(seen) <= 2 * R + 2 * 33 + 10, len(seen)   # 规则各一次（app + 匹配文字）+ 进来的每个串一次；不是 串数 × 规则数
    seen.clear()
    beat_now = datetime.now(timezone.utc).replace(microsecond=0)
    spans = [{"app": "code", "title": f"t{i}", "from": (beat_now - timedelta(seconds=118 - 3.6 * i)).isoformat(), "seconds": 3}
             for i in range(presence.MAX_BEAT_SPANS)]
    _beat_raw(client, "code", "top", spans=spans, sentAt=beat_now.isoformat())
    assert len(seen) <= 2 * R + 2 * (1 + presence.MAX_BEAT_SPANS) + 10, len(seen)



def test_rule_cap_holds_under_concurrent_creates(client, monkeypatch):
    from app.modules.activity import ignore, ignore_repo  # noqa: PLC0415

    n, barrier, real = 8, threading.Barrier(8, timeout=20), ignore_repo.count
    monkeypatch.setattr(ignore, "MAX_IGNORES", 3)

    def count_after_everyone_inserted(user):   # 把所有创建者都逼到「先看数、再动手」同一刻：检查再插入的写法在这里全部通过
        out = real(user)
        barrier.wait()
        return out

    monkeypatch.setattr(ignore_repo, "count", count_after_everyone_inserted)
    with ThreadPoolExecutor(n) as pool:
        codes = list(pool.map(lambda i: client.post(IGN, json={"app": f"app{i}"}).status_code, range(n)))
    assert set(codes) <= {201, 422}
    monkeypatch.setattr(ignore_repo, "count", real)
    assert client.get(IGN).json()["total"] <= 3


def test_gate_beat_drops_top_level_and_span_guess_directly(client):
    from app.modules.activity import ignore  # noqa: PLC0415

    _ignore(client, "code", MARK)
    guess = {"projectId": "p_x", "confidence": 0.9, "classifier": "rules"}
    spans = [{"app": "code", "title": MARK, "guess": guess, "from": "x"}, {"app": "code", "title": "别的", "guess": guess, "from": "y"}]
    app, title, g, out, top = ignore.gate_beat("u_local", "code", MARK, guess, spans)
    assert (app, title, g, top) == ("", "", None, True)
    assert out[0] == {"app": "", "title": "", "from": "x"} and out[1]["guess"] == guess and out[1]["title"] == "别的"
    assert ignore.gate_beat("u_local", "code", "别的", guess, None) == ("code", "别的", guess, None, False)


def test_an_error_inside_the_gates_means_ignored_not_kept(client, monkeypatch):
    from app.modules.activity import ignore  # noqa: PLC0415

    _ignore(client, "code", MARK)
    real = ignore.fold
    monkeypatch.setattr(ignore, "fold", lambda t: _boom() if "BOOM" in (t or "") else real(t))
    assert _upload(client, [_seg(10, "code", "BOOM 一"), _seg(20, "code", "别的")]).get("ignored") == 1   # 拿不准 → 丢
    now, sp = _span("BOOM 二", 20, 10)
    _, ok = _span("别的", 8, 5)
    _beat_raw(client, "code", "BOOM 三", spans=[sp, ok], sentAt=now.isoformat())
    doc = _db()["activity_presence"].find_one({"deviceId": DEV})
    assert (doc["app"], doc["title"]) == ("", "")
    assert [s["title"] for s in doc["spans"] if s["app"]] == ["别的"] and not any("BOOM" in s["title"] for s in doc["spans"])
    assert _dump("BOOM") == set()



def test_rules_unreadable_refuses_the_guarded_write_and_stores_nothing(client, monkeypatch):
    from app.modules.activity import ignore_repo  # noqa: PLC0415

    monkeypatch.setattr(ignore_repo, "all_rules", _boom)
    with pytest.raises(RuntimeError):  # 5xx：检测程序会重试
        _upload(client, [_seg(10, "chrome", MARK)])
    with pytest.raises(RuntimeError):
        _plain_beat(client, "chrome", MARK)
    assert _raw_has(MARK) == {}


def test_rule_applies_immediately_no_cache(client):
    assert _upload(client, [_seg(10, "chrome", MARK)])["accepted"] == 1
    rule = _ignore(client, "chrome")
    assert _upload(client, [_seg(20, "chrome", MARK + "2")]).get("ignored") == 1
    client.delete(f"{IGN}/{rule['id']}")
    assert _upload(client, [_seg(30, "chrome", MARK + "3")])["accepted"] == 1


def test_ignored_beat_is_not_focus_nor_target_nor_attention_and_keeps_other_spans(client):
    run = client.post(f"{API}/agents/start", json={"agent": "cc", "tool": "claude-code", "label": "garden", "match": "garden"}).json()
    _ignore(client, "ptyxis")
    _plain_beat(client, "ptyxis", "✳ garden " + MARK)
    doc = _db()["activity_presence"].find_one({})
    assert [(s["app"], s["title"]) for s in doc["spans"]] == [("", "")] and (doc["app"], doc["title"]) == ("", "")
    body = client.get(f"{API}/views/lanes").json()
    assert body["agents"][0]["attention"] == [] and body["human"]["needsChoice"] is None and body["human"]["auto"] is None
    focus = client.get(f"{API}/views/current").json()["focus"]
    assert (focus["state"], focus["app"], focus["title"], focus["taskId"]) == ("present", "", "", None)
    _plain_beat(client, "kitty", "✳ garden")   # 对照：别的窗口照旧对得上会话
    assert client.get(f"{API}/views/lanes").json()["agents"][0]["attention"] != [] and run["runId"]


def test_mixed_beat_blanks_only_the_matching_span(client):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    _ignore(client, "chrome", "银行")
    spans = [_span_at(now, "银行首页", 40, 20)[1] | {"app": "chrome"}, _span_at(now, "news", 15, 10)[1] | {"app": "chrome"}]
    _beat_raw(client, "chrome", "news", spans=spans, sentAt=now.isoformat())
    doc = _db()["activity_presence"].find_one({})
    assert [(s["app"], s["title"]) for s in doc["spans"]] == [("", ""), ("chrome", "news")] and doc["title"] == "news"


def test_an_ignored_beat_does_not_renew_a_temporary_choice(client, monkeypatch):
    from app.modules.activity import choice_repo  # noqa: PLC0415

    seen = []
    monkeypatch.setattr(choice_repo, "seen", lambda *a, **k: seen.append(a))
    _ignore(client, "code", MARK)
    _plain_beat(client, "code", MARK)
    assert seen == []
    _plain_beat(client, "code", "别的")
    assert len(seen) == 1


def test_find_refuses_an_ignored_window_for_the_ai_and_the_human_choice(client):
    from app.modules.activity import auto  # noqa: PLC0415
    from app.modules.planner.errors import NotFoundError  # noqa: PLC0415

    _plain_beat(client, "code", MARK)
    _ignore(client, "code", MARK)
    # 规则之前就存下的在场历史不清；但已忽略的窗口不再被找到（AI 不会被问、人的选择也写不出规则）
    _db()["activity_presence"].update_many({}, {"$set": {"spans": [{
        "app": "code", "title": MARK, "from": datetime.now(timezone.utc) - timedelta(minutes=1), "to": datetime.now(timezone.utc),
        "afk": False, "seconds": 60}]}})
    with pytest.raises(NotFoundError):
        auto._find("u_local", auto.window_key("code", MARK))
