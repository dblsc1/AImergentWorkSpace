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
    from app.modules.activity import auto  # noqa: PLC0415
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
    # 记住的选择写的规则（标题带计数前缀）与装饰标题的 AI 规则：它们存的是 src（原始窗口），清理按它走
    remembered = "(3) " + MARK + " 记住"
    clock(S + 70)
    _beat(client, title=remembered)
    _post(client, f"{API}/activity/choice", {"key": auto.window_key("code", remembered), "taskId": task, "remember": True})
    decorated = "⠋ " + MARK + " 装饰"
    _run(client, clock, [(100, decorated, None), (130, decorated, None), (160, decorated, None)])
    clock(S + 161)
    _answer(client, _claim(client)["key"], taskId=task, confidence=0.9)
    stored = _db()["detector_rules"].find_one({"user": "u_local"})
    assert len([r for r in stored["rules"] if MARK in json.dumps(r, ensure_ascii=False)]) == 3 and "draft" in stored  # 前提
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

    def doc(i, title):
        return {"user": "u_local", "id": f"sug_{i}", "dedupeKey": f"d{i}", "status": "pending", "app": "code", "title": title,
                "durationSeconds": 3, "startTs": now, "endTs": now}

    # 命中的散在扫描顺序的中间和**最末尾**（扫描有任何截断 / 预算偷懒都会漏掉最后那些）
    docs = [doc(i, "别的") for i in range(2700)] + [doc(5000 + i, MARK) for i in range(3)]
    docs += [doc(6000 + i, "别的") for i in range(2700)] + [doc(9000 + i, MARK) for i in range(4)]
    _db()["activity_suggestions"].insert_many(docs)
    out = _ignore(client, "code", MARK)
    assert out["removed"] == 7 and _dump(MARK) <= {"activity_ignores"}
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
    return _span_at(datetime.now(timezone.utc).replace(microsecond=0), title, ago, seconds, guess)


def _span_at(now, title, ago, seconds, guess=True):
    sp = {"app": "code", "title": title, "from": (now - timedelta(seconds=ago)).isoformat(), "seconds": seconds}
    if guess:
        sp["guess"] = {"projectId": "p_x", "confidence": 0.9, "classifier": "rules"}
    return now, sp


def test_write_gate_holds_while_the_leftover_purge_fails_permanently(client, world, clock, monkeypatch):  # noqa: F811
    from app.modules.activity import ignore  # noqa: PLC0415
    from test_auto_track import S  # noqa: PLC0415

    w = _waiting(client, clock, MARK)  # 规则之前就在场、被 AI 认领的窗口
    _ignore(client, "code", MARK)
    _db()["activity_ignores"].update_many({}, {"$set": {"purged": False}})  # 残留清理「永远没做完」
    monkeypatch.setattr(ignore, "purge_leftovers", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("never works")))
    # 上传：匹配的丢、不匹配的照常 2xx
    assert _upload(client, [_seg(10, "code", MARK + " a"), _seg(20, "chrome", "别的")]) == {"accepted": 1, "duplicates": 0, "rejected": [], "ignored": 1}
    # 心跳：顶层 + 每个 span（含 guess）都抹掉；不匹配的 span 照常存下标题。beat 用夹具的时钟造：夹具把服务端时钟钉在过去，
    # 用真实时钟造的 span 都落在已提交时间线的末尾之前，会被丢掉——那样根本没练到 span 闸门
    now = clock(S + 200)
    s1, s2 = _span_at(now, MARK + " b", 30, 10)[1], _span_at(now, "别的", 15, 10)[1]
    resp = client.post(f"{API}/activity/presence", json={"deviceId": DEV, "app": "code", "title": MARK + " c", "afk": False,
                                                          "guess": {"projectId": "p_x", "confidence": 0.9, "classifier": "rules"},
                                                          "sentAt": now.isoformat(), "spans": [s1, s2]})
    assert resp.status_code == 200, resp.text
    doc = _db()["activity_presence"].find_one({"deviceId": DEV})
    assert (doc["app"], doc["title"]) == ("", "") and not doc.get("guess")
    assert any(sp["title"] == "别的" for sp in doc["spans"])  # 不匹配的 span 照常存（混合的一拍：一个抹、一个留）
    blank = [sp for sp in doc["spans"] if sp["app"] == ""]
    assert blank and all(sp["title"] == "" and "guess" not in sp for sp in blank)
    assert not any(MARK in sp["title"] for sp in doc["spans"])
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


# ─────────────────────────────────────────── 复审：src / 装饰前缀 / 草稿 / 读遮罩 / 草稿闸门 / 折叠 / 并发上限 / 变异

import threading  # noqa: E402
from concurrent.futures import ThreadPoolExecutor  # noqa: E402

from test_activity_suggestions import BEARER  # noqa: E402
from test_auto_ai import _claim  # noqa: E402,F811

DECOR_TITLES = ["(3) Inbox Zx9 - Mail", "⠋ Inbox Zx9 - Mail", "[2] Inbox Zx9 - Mail"]  # 计数前缀 / 转圈符号 / 方括号计数
DECOR = "^(?:[^\\pL\\pN]|\\(\\d+\\)|\\[\\d+\\])*"


def _stored_rules():
    return (_db()["detector_rules"].find_one({"user": "u_local"}) or {}).get("rules") or []


def _unpurged_flags():
    from app.modules.activity import ignore_repo  # noqa: PLC0415

    return [r["purged"] for r in ignore_repo.all_rules("u_local")]


def _boom(*_a, **_k):
    raise RuntimeError("boom")


@pytest.mark.parametrize("title", DECOR_TITLES)
def test_remember_rule_carries_a_server_only_src_and_the_ignore_removes_it(client, world, clock, title):
    from app.modules.activity import auto  # noqa: PLC0415
    from test_auto_track import S, _beat  # noqa: PLC0415

    clock(S)
    _beat(client, title=title)
    _post(client, f"{API}/activity/choice", {"key": auto.window_key("code", title), "taskId": world["a"], "remember": True})
    assert _stored_rules()[0]["src"] == {"app": "code", "title": title}   # 存的是原始窗口
    got = _rules(client)
    for out in (got, _rules(client, BEARER), client.get(f"{RULES}/drafts/current").json()):   # 任何输出都没有 src
        assert "src" not in json.dumps(out) and "Zx9" not in json.dumps(out["draft"] if "draft" in out else {}) or "draft" not in out
    # 人整套回写（请求体里没有 src）：服务端按 id 带过去；草稿 diff 不把它当「修改」
    assert client.put(RULES, json={"rules": got["rules"]}, headers={"If-Match": f'"{got["version"]}"'}).status_code == 200
    assert _stored_rules()[0]["src"]["title"] == title
    draft = client.post(f"{RULES}/drafts", json={"summary": "不变", "rules": _rules(client)["rules"]}).json()
    assert draft["diff"]["changed"] == [] and "src" not in json.dumps(draft)
    _ignore(client, "code", title)   # 页面把整个标题（带装饰）填进匹配文字
    assert _stored_rules() == [] and _dump("Zx9") <= DECLARED


@pytest.mark.parametrize("title", DECOR_TITLES)
def test_ai_rule_from_a_decorated_title_is_purged_by_the_whole_title_fragment(client, world, clock, title):
    w = _waiting(client, clock, title)
    _answer(client, w["key"], taskId=world["a"], confidence=0.9)
    assert _stored_rules()[0]["src"] == {"app": "code", "title": title} and _stored_rules()[0]["title"].startswith(DECOR)
    client.post(f"{RULES}/drafts", json={"summary": "x", "author": "assistant", "rules": [
        {"app": "^code$", "title": DECOR + "Inbox Zx9 - Mail$", "taskId": world["a"]}]})   # 旧形状的待批准草稿
    _ignore(client, "code", title)
    assert _stored_rules() == [] and client.get(f"{RULES}/drafts/current").json()["draft"] is None
    assert _dump("Zx9") <= DECLARED


def test_legacy_rules_without_src_are_recognised_by_shape_and_decorated_fragments(client):
    ai = {"author": "assistant", "taskId": "t"}
    full, core = "(3) Inbox Zx9 - Mail", "Inbox Zx9 - Mail"
    _raw_rules([
        {"id": "ai_dec", "app": "^code$", "title": DECOR + core + "$", **ai},                                  # AI，带装饰前缀
        {"id": "ai_plain", "app": "^code$", "title": "^" + core + "$", **ai},                                   # AI，窗口本来没装饰
        {"id": "remember_dec", "app": "^code$", "title": DECOR + core + "$", "taskId": "t", "note": "计时页选的：code · " + full},
        {"id": "page", "app": "^code$", "title": "^" + core + "$", "taskId": "t", "note": "待确认里勾的：code · " + core},
        {"id": "human_dec", "app": "^code$", "title": DECOR + core + "$", "taskId": "t", "author": "human"},    # 人写的，同形：不动
        {"id": "hand", "app": "^code$", "title": core, "taskId": "t"},                                          # 人手写：不动
    ], draft={"id": "drf_x", "author": "assistant", "summary": "为 " + core + " 起草", "createdAt": datetime.now(timezone.utc),
              "expiresAt": datetime.now(timezone.utc) + timedelta(hours=1), "baseVersion": 1, "rules": []})
    _ignore(client, "code", full)   # 带装饰的整个标题：认得出带装饰前缀的那些（核心标题 + note 里的原始标题），没装饰的是另一个窗口
    assert [r["id"] for r in _stored_rules()] == ["ai_plain", "page", "human_dec", "hand"]
    assert _db()["detector_rules"].find_one({"user": "u_local"}).get("draft") is None   # 摘要里是核心标题
    _ignore(client, "code", "Zx9")   # 不带装饰的片段：没装饰的也认
    assert [r["id"] for r in _stored_rules()] == ["human_dec", "hand"]


def test_page_generated_rule_put_through_the_api_gets_src(client, world):
    page = {"app": "^code$", "title": "^" + MARK + "$", "taskId": world["a"], "note": "待确认里勾的：code · " + MARK}
    r = client.put(RULES, json={"rules": [page, {"id": "hand", "title": "foo", "taskId": world["a"]}]}, headers={"If-Match": '"0"'})
    assert r.status_code == 200, r.text
    assert _stored_rules()[0]["src"] == {"app": "code", "title": MARK} and "src" not in _stored_rules()[1]
    _ignore(client, "code", MARK)
    assert [r["id"] for r in _stored_rules()] == ["hand"]


# ── M3：草稿删不掉不许 purged:true，读也遮草稿


def _assistant_draft(client, world, title=MARK):
    r = client.post(f"{RULES}/drafts", json={"summary": f"为 {title} 起草", "author": "assistant",
                                             "rules": [{"app": "^code$", "title": "^" + title + "$", "taskId": world["a"]}]})
    assert r.status_code == 201, r.text


def test_a_failing_draft_deletion_fails_the_purge_and_reads_mask_the_draft(client, world, monkeypatch):
    from datetime import timedelta as td  # noqa: PLC0415

    from app.modules.activity import ignore  # noqa: PLC0415
    from app.modules.detector import repo as drepo  # noqa: PLC0415

    _assistant_draft(client, world)
    real = drepo.drop_draft
    monkeypatch.setattr(drepo, "drop_draft", _boom)
    assert client.post(IGN, json={"app": "code", "titleContains": MARK}).status_code == 503
    assert _unpurged_flags() == [False]
    assert "draft" in _db()["detector_rules"].find_one({"user": "u_local"})   # 库里确有残留
    assert client.get(f"{RULES}/drafts/current").json()["draft"] is None       # 读（含 MCP get_detector_rules.draft）遮住
    monkeypatch.setattr(drepo, "drop_draft", real)
    monkeypatch.setattr(ignore, "PURGE_BACKOFF", timedelta(0))
    _plain_beat(client, "chrome", "别的")   # 下一次写入补清
    assert _unpurged_flags() == [True] and "draft" not in _db()["detector_rules"].find_one({"user": "u_local"})


def test_purged_is_not_set_while_a_draft_or_an_ai_rule_remains_and_reads_mask_the_rule(client, world, clock, monkeypatch):
    from app.modules.detector import window_rules  # noqa: PLC0415

    w = _waiting(client, clock, MARK)
    _answer(client, w["key"], taskId=world["a"], confidence=0.9)
    _assistant_draft(client, world)
    human = client.put(RULES, json={"rules": [*_rules(client)["rules"], {"id": "r_human", "app": "^code$", "title": MARK,
                                                                     "taskId": world["a"], "author": "human"}]},
                       headers={"If-Match": '"1"'})
    assert human.status_code == 200, human.text
    monkeypatch.setattr(window_rules, "drop_ignored", lambda *a, **k: 0)   # 删规则 / 草稿「悄悄没做」：校验要发现
    assert client.post(IGN, json={"app": "code", "titleContains": MARK}).status_code == 503
    assert _unpurged_flags() == [False]
    assert [r["id"] for r in _rules(client)["rules"]] == ["r_human"]               # AI 规则读时遮住，人写的照给
    assert client.get(f"{RULES}/drafts/current").json()["draft"] is None


def test_silently_failing_draft_drop_is_caught_by_the_verification(client, world, monkeypatch):
    from app.modules.detector import repo as drepo  # noqa: PLC0415

    _assistant_draft(client, world)
    monkeypatch.setattr(drepo, "drop_draft", lambda *a, **k: None)   # 不抛、也没删
    assert client.post(IGN, json={"app": "code", "titleContains": MARK}).status_code == 503
    assert _unpurged_flags() == [False]


# ── L4：规则之后写的草稿


def test_a_draft_written_after_the_rule_is_refused(client, world):
    _ignore(client, "code", MARK)
    body = {"summary": "x", "author": "assistant", "rules": [{"app": "^code$", "title": "^" + MARK + " 二$", "taskId": world["a"]}]}
    assert client.post(f"{RULES}/drafts", json=body).status_code == 409
    assert client.post(f"{RULES}/drafts", json={**body, "summary": f"为 {MARK} 起草", "rules": [
        {"app": "^chrome$", "taskId": world["a"]}]}).status_code == 409                  # 摘要带着被忽略的文字
    assert client.post(f"{RULES}/drafts", json={**body, "rules": [{"app": "^code$", "title": "^别的$", "taskId": world["a"]}]}).status_code == 201
    assert client.post(f"{RULES}/drafts", json={**body, "author": "human"}).status_code == 201   # 人写的草稿不碰
    _ignore(client, "code")   # 整个程序都忽略
    assert client.post(f"{RULES}/drafts", json={**body, "rules": [{"app": "^code$", "taskId": world["a"]}]}).status_code == 409


# ── L5：清理没做完时，认领与报告解析不交出残留


def test_claim_and_report_resolve_do_not_hand_out_leftovers_after_a_failed_purge(client, clock, monkeypatch):
    from app.modules.activity import repo  # noqa: PLC0415
    from test_activity_reports import REPORTS, _submit  # noqa: PLC0415

    w = _waiting(client, clock, MARK)
    _upload(client, [_seg(10, "code", MARK)])
    sid = next(i["id"] for i in client.get(SUG, params={"status": "pending"}).json()["items"] if i["title"] == MARK)
    rid = _submit(client, [{"kind": "dismiss", "suggestionIds": [sid]}])["reportId"]
    assert MARK in client.get(f"{REPORTS}/{rid}", params={"resolve": "true"}).text   # 前提：规则之前读得到
    monkeypatch.setattr(repo, "delete_pending", _boom)
    assert client.post(IGN, json={"app": "code", "titleContains": MARK}).status_code == 503
    assert _db()["activity_ai_asks"].count_documents({"key": w["key"]}) == 1   # 前提：问询的残留还在
    assert _claim(client) is None   # 残留的问询不再经认领交给 AI
    assert MARK not in client.get(f"{REPORTS}/{rid}", params={"resolve": "true"}).text


# ── L6：别的写入和建规则交错，严格清理遇到「又脏了」不 5xx


@pytest.mark.parametrize("dirty_times,purged", [(2, True), (99, False)])
def test_strict_post_write_purge_retries_when_dirty_and_never_5xxs_the_write(client, clock, monkeypatch, dirty_times, purged):
    from app.modules.activity import auto, ignore  # noqa: PLC0415
    from test_auto_track import S, _beat  # noqa: PLC0415

    clock(S)
    _beat(client, title=MARK)
    real_find, real_check, calls = auto._find, ignore.leftovers_exist, []

    def dirty(user, rule):
        calls.append(1)
        return len(calls) <= dirty_times or real_check(user, rule)

    def find_then_ignore(user, key):
        out = real_find(user, key)
        assert client.post(IGN, json={"app": "code", "titleContains": MARK}).status_code == 201   # 规则在写的当中出现
        _db()["activity_ignores"].update_many({}, {"$set": {"purged": False}})   # 建它的那个请求还没清完 / 没标
        monkeypatch.setattr(ignore, "leftovers_exist", dirty)   # 此后清完到校验之间总有别的写入落进来
        return out

    monkeypatch.setattr(auto, "_find", find_then_ignore)
    resp = client.post(f"{API}/activity/choice/dismiss", json={"key": auto.window_key("code", MARK)})
    assert resp.status_code == 200, resp.text    # 本次写入的正常结果，不是 5xx
    assert _unpurged_flags() == [purged]
    assert len(calls) == min(dirty_times + 1, ignore.STRICT_TRIES)
    assert _dump(MARK) <= DECLARED   # 本次请求自己的数据（刚写的临时选择）已经被清掉
    if not purged:
        monkeypatch.undo()
        _plain_beat(client, "chrome", "别的")   # 下一次写入补清
        assert _unpurged_flags() == [True]


# ── L7：不可见字符


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


# ── L8：折叠次数


def test_folds_are_linear_in_strings_plus_rules(client, monkeypatch):
    from app.modules.activity import ignore, presence  # noqa: PLC0415

    R, now = 200, datetime.now(timezone.utc)
    _db()["activity_ignores"].insert_many([{"user": "u_local", "id": f"ig_{i}", "app": "code", "titleContains": f"needle{i}",
                                            "createdAt": now + timedelta(microseconds=i), "hits": 0, "seconds": 0, "purged": True}
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


# ── L9：200 条上限在并发下不被顶过


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


# ── L10：读不到规则 → 读窗口文字的接口 503；坏文档不挡建规则


def test_reads_fail_closed_with_503_when_the_rules_cannot_be_read(client, monkeypatch):
    from app.modules.activity import ignore_repo  # noqa: PLC0415

    _upload(client, [_seg(10, "code", MARK)])
    _plain_beat(client, "code", MARK)
    monkeypatch.setattr(ignore_repo, "all_rules", _boom)
    for url in (f"{API}/views/current", f"{API}/views/lanes", SUG, RULES, f"{RULES}/drafts/current", IGN):
        got = client.get(url)
        assert got.status_code == 503 and MARK not in got.text, url   # 不交出没遮过的数据，也不是 500


def test_a_malformed_rules_document_does_not_block_creating_a_rule(client):
    _db()["detector_rules"].replace_one({"user": "u_local"}, {"user": "u_local", "version": 1, "rules": 7}, upsert=True)
    assert _ignore(client, "code", MARK)["created"] is True
    assert _unpurged_flags() == [True]


# ── T11：变异存活的每一处


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


def test_every_state_changing_endpoint_is_guarded():
    from app.modules.activity import auto, auto_ai, service  # noqa: PLC0415
    from app.modules.detector import rules  # noqa: PLC0415

    for fn in (service.upload, auto.heartbeat, auto.choose, auto.dismiss, auto_ai.claim, auto_ai.suggest, auto_ai.reject):
        assert hasattr(fn, "__wrapped__"), fn.__name__   # ignore.guarded 用 functools.wraps：被摘掉就没有 __wrapped__
    assert rules.create_draft.__name__ == "create_draft"


def test_mark_purged_needs_a_clean_pending_suggestion_scan(client, monkeypatch):
    from app.modules.detector import window_rules  # noqa: PLC0415

    real = window_rules.drop_ignored

    def drop_then_a_pending_one_lands(*a, **k):   # 清完到校验之间，别的上传落进来一条
        out = real(*a, **k)
        now = datetime.now(timezone.utc)
        _db()["activity_suggestions"].insert_one({"user": "u_local", "id": "sug_late", "dedupeKey": "late", "status": "pending",
                                                  "app": "code", "title": MARK, "durationSeconds": 3, "startTs": now, "endTs": now})
        return out

    monkeypatch.setattr(window_rules, "drop_ignored", drop_then_a_pending_one_lands)
    assert client.post(IGN, json={"app": "code", "titleContains": MARK}).status_code == 503
    assert _unpurged_flags() == [False]


def test_backoff_stops_the_purge_from_being_retried_on_every_write(client, monkeypatch):
    from app.modules.detector import window_rules  # noqa: PLC0415

    calls = []
    monkeypatch.setattr(window_rules, "drop_ignored", lambda *a, **k: (calls.append(1), _boom())[1])
    assert client.post(IGN, json={"app": "code", "titleContains": MARK}).status_code == 503
    first = len(calls)
    for _ in range(6):   # 默认退避 1 分钟：这期间的写入 / 读都不再试
        _upload(client, [_seg(10, "chrome", "别的")])
        _plain_beat(client, "chrome", "别的")
        client.get(IGN)
    assert len(calls) == first


def test_ignored_windows_are_refused_in_find_and_in_suggest_each_on_its_own(client, world, clock, monkeypatch):
    from app.modules.activity import auto  # noqa: PLC0415
    w = _waiting(client, clock, MARK)
    key = w["key"]
    # ① 规则已标 purged、但在场里还有这个窗口（赛跑留下的）：_find 自己拒绝
    _ignore(client, "code", MARK)
    _plain_beat(client, "code", MARK)
    _db()["activity_presence"].update_many({}, {"$set": {"app": "code", "title": MARK, "spans": [{
        "app": "code", "title": MARK, "from": datetime.now(timezone.utc) - timedelta(minutes=1), "to": datetime.now(timezone.utc),
        "afk": False, "seconds": 60}]}})
    with pytest.raises(Exception) as exc:   # noqa: PT011
        auto._find("u_local", key)
    assert type(exc.value).__name__ == "NotFoundError"
    assert client.post(f"{API}/activity/choice", json={"key": key, "taskId": world["a"], "remember": True}).status_code == 404
    assert _stored_rules() == []
    # ② suggest 自己的拒绝：_find 被绕开时仍然什么都不写
    ask = {"user": "u_local", "key": key, "app": "code", "title": MARK, "claimedAt": auto._now()}   # noqa: SLF001
    _db()["activity_ai_asks"].replace_one({"user": "u_local", "key": key}, ask, upsert=True)
    monkeypatch.setattr(auto, "_find", lambda user, k: ({"app": "code", "title": MARK, "afk": False}, DEV))
    out = client.post(f"{API}/activity/ai/suggest", json={"key": key, "taskId": world["a"], "confidence": 0.9, "reason": "r"})
    assert out.status_code == 409 and _stored_rules() == []


def test_an_ignored_beat_does_not_renew_a_temporary_choice(client, monkeypatch):
    from app.modules.activity import choice_repo  # noqa: PLC0415

    seen = []
    monkeypatch.setattr(choice_repo, "seen", lambda *a, **k: seen.append(a))
    _ignore(client, "code", MARK)
    _plain_beat(client, "code", MARK)
    assert seen == []
    _plain_beat(client, "code", "别的")
    assert len(seen) == 1
