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
    real = ignore.drop

    def delete_drop_recreate(*args, **kwargs):
        assert client.delete(f"{IGN}/{rule['id']}").status_code == 204
        out = real(*args, **kwargs)  # 这一刻没有规则：整批放行
        _ignore(client, "code", MARK)  # 同一条规则（同 id）又建回来，清理发生在写入之前
        return out

    monkeypatch.setattr(ignore, "drop", delete_drop_recreate)
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
