"""此刻的焦点（契约 v2.16「此刻的焦点」节）。

要害三句：**心跳新鲜就有 focus，与开关、与有没有手动计时都无关**；**目标按「自动跟踪 → 代理会话 → 按以往」先中先用，
已经不存在的不出**；**只是显示——读多少遍都不写一个字，也不随轮询变贵**。
"""

from __future__ import annotations

from datetime import datetime

import pytest

from test_auto_track import API, B, DEV, PLANNER, _beat, _guess, _post, _track, clock, world  # noqa: F401
from test_match_history import _confirmed
from test_session_link import _start

TITLE = "plot.gd — garden"
NO_TARGET = {"projectId": None, "projectName": None, "taskId": None, "taskName": None, "source": None}


@pytest.fixture(autouse=True)
def _no_history_cache():
    from app.modules.activity import focus  # noqa: PLC0415

    focus._history_cache.clear()  # noqa: SLF001 —— 进程内的 30 秒缓存，测试之间不许互相喂
    yield focus._history_cache  # noqa: SLF001


def _focus(client, headers=None) -> dict | None:
    """views/current 与 views/lanes 的 human 必须是同一份。"""
    current = client.get(f"{API}/views/current", headers=headers or {})
    lanes = client.get(f"{API}/views/lanes", headers=headers or {})
    assert current.status_code == 200 and lanes.status_code == 200, current.text + lanes.text
    assert current.json()["focus"] == lanes.json()["human"]["focus"]
    return current.json()["focus"]


def _target(focus: dict) -> dict:
    return {k: focus[k] for k in NO_TARGET}


def _ts(iso: str) -> datetime:
    return datetime.fromisoformat(iso)


# ─────────────────────────────────────────── 有 / 没有 / 离开


def test_null_without_presence_and_when_stale(client, clock):
    assert _focus(client) is None
    clock(-200)
    _beat(client)
    clock(-91)
    assert _focus(client) is None, "最新心跳超过 90 秒：不算"
    clock(-110)
    assert _focus(client)["state"] == "present", "90 秒之内：算"


def test_present_without_switch_and_without_target(client, world, clock):
    """开关没开、什么都认不出：照样有 focus，目标键全为 null（键不消失）。"""
    t0 = clock(-30)
    _beat(client)
    clock(0)
    focus = _focus(client)
    assert focus == {"state": "present", "app": "code", "title": TITLE, "since": focus["since"],
                     "dwellSeconds": 0, **NO_TARGET}
    assert _ts(focus["since"]) == t0
    body = client.get(f"{API}/views/current").json()
    assert (body["auto"], body["needsChoice"], body["aiThinking"]) == (None, None, None)


def test_afk_since_is_the_start_of_the_away_run(client, world, clock):
    clock(-60)
    _beat(client)
    away = clock(-40)
    _beat(client, afk=True)
    clock(-20)
    _beat(client, afk=True)
    clock(0)
    focus = _focus(client)
    assert (focus["state"], _ts(focus["since"])) == ("afk", away) and _target(focus) == NO_TARGET


def test_afk_never_carries_a_target(client, world, clock):
    _confirmed(client, TITLE, 10, taskId=world["a"])
    clock(-20)
    _beat(client)
    assert _focus(client)["taskId"] == world["a"]
    clock(-10)
    _beat(client, afk=True)
    assert _target(_focus(client)) == NO_TARGET


# ─────────────────────────────────────────── since


def test_since_spans_the_same_window_across_spinner_titles(client, world, clock):
    """标题开头的转圈符号在变 = 心跳开了新段，但还是同一个窗口：since 不重起。"""
    t0 = clock(-60)
    _beat(client, title="✳ Claude Code · cockpit")
    clock(-45)
    _beat(client, title="⠂ Claude Code · cockpit")
    clock(-30)
    _beat(client, title="(2) Claude Code · cockpit")
    clock(0)
    focus = _focus(client)
    assert _ts(focus["since"]) == t0 and focus["title"] == "(2) Claude Code · cockpit"


def test_since_restarts_on_another_window_and_after_a_gap(client, world, clock):
    clock(-300)
    _beat(client)
    clock(-290)
    _beat(client, title="别的窗口")
    back = clock(-280)
    _beat(client)
    clock(-270)
    assert _ts(_focus(client)["since"]) == back, "切走再切回来：从切回来那一刻起"

    clock(-200)
    _beat(client)  # 离上一次 80 秒 > 45 秒：中间断过
    again = clock(-200)
    clock(-190)
    assert _ts(_focus(client)["since"]) == again


# ─────────────────────────────────────────── 目标：三个来源与先后


def test_auto_target_is_reused_with_its_source(client, world, clock):
    _track(client)
    clock(-30)
    _beat(client, guess=_guess(taskId=world["a"]))
    clock(0)
    body = client.get(f"{API}/views/current").json()
    assert _target(body["focus"]) == {"projectId": world["p"], "projectName": "项目 P", "taskId": world["a"],
                                      "taskName": "任务 a", "source": "rules"}
    assert {k: body["auto"][k] for k in NO_TARGET} == _target(body["focus"])


def test_choice_source_comes_from_auto(client, world, clock):
    _track(client)
    from app.modules.activity.auto import window_key  # noqa: PLC0415

    clock(-20)
    _beat(client)
    key = window_key("code", TITLE)
    _post(client, f"{API}/activity/choice", {"key": key, "projectId": world["q"], "remember": False})
    assert _target(_focus(client)) == {**NO_TARGET, "projectId": world["q"], "projectName": "项目 Q", "source": "choice"}


def test_agent_session_links_the_window_to_its_project(client, world, clock):
    clock(-20)
    _beat(client, title="⠂ CFO_agent", app="ptyxis")
    assert _target(_focus(client)) == NO_TARGET
    run = _start(client, projectId=world["q"], label="CFO_agent")
    want = {**NO_TARGET, "projectId": world["q"], "projectName": "项目 Q", "source": "agent-session"}
    assert _target(_focus(client)) == want

    other = _start(client, projectId=world["p"], label="cfo_agent", clientKey="k2")
    assert _target(_focus(client)) == NO_TARGET, "同名会话指向两个项目：拿不准就不认"
    _post(client, f"{API}/agents/{other['runId']}/stop", {"outcome": "done"})
    assert _target(_focus(client)) == want
    _post(client, f"{API}/agents/{run['runId']}/stop", {"outcome": "done"})
    assert _target(_focus(client)) == NO_TARGET, "运行结束了：不再认"


def test_history_gives_the_latest_human_decision(client, world, clock, _no_history_cache):
    _confirmed(client, "✳ " + TITLE, 30, projectId=world["q"])
    _confirmed(client, TITLE, 20, taskId=world["a"])
    _confirmed(client, TITLE, 10, taskId=world["a"], app="kitty")  # 程序不同 = 另一个窗口
    clock(-20)
    _beat(client, title="(3) " + TITLE)
    assert _target(_focus(client)) == {"projectId": world["p"], "projectName": "项目 P", "taskId": world["a"],
                                       "taskName": "任务 a", "source": "history"}
    _beat(client, title="没确认过的窗口")
    assert _target(_focus(client)) == NO_TARGET
    _beat(client, title="", app="code")
    assert _target(_focus(client)) == NO_TARGET, "标题被隐私设置去掉了：只凭程序名不认"


def test_history_bucket_and_auto_recorded_segments(client, world, clock):
    _confirmed(client, TITLE, 20, projectId=world["q"])  # 确认到「未分类」桶 = 只到项目
    clock(-20)
    _beat(client)
    assert _target(_focus(client)) == {**NO_TARGET, "projectId": world["q"], "projectName": "项目 Q", "source": "history"}


def test_history_targets_that_no_longer_exist(client, world, clock, _no_history_cache):
    """任务完成 / 删掉 → 只到项目；项目删掉 → 看更早的一次；都没了 → 不认。缓存不让删掉的东西多活一秒。"""
    _confirmed(client, TITLE, 30, projectId=world["q"])
    _confirmed(client, TITLE, 20, taskId=world["a"])
    clock(-20)
    _beat(client)
    assert _focus(client)["taskId"] == world["a"]

    resp = client.patch(f"{PLANNER}/tasks/{world['a']}", json={"done": True})
    assert resp.status_code == 200, resp.text
    project_only = {**NO_TARGET, "projectId": world["p"], "projectName": "项目 P", "source": "history"}
    assert _target(_focus(client)) == project_only, "任务完成了：只到项目（没清缓存）"

    assert client.delete(f"{PLANNER}/tasks/{world['a']}").status_code in (200, 204)
    assert _target(_focus(client)) == project_only, "任务删了：台账里记着它的项目"

    assert client.delete(f"{PLANNER}/projects/{world['p']}").status_code in (200, 204)
    assert _target(_focus(client)) == {**NO_TARGET, "projectId": world["q"], "projectName": "项目 Q", "source": "history"}
    assert client.delete(f"{PLANNER}/projects/{world['q']}").status_code in (200, 204)
    assert _target(_focus(client)) == NO_TARGET


def test_precedence_auto_then_session_then_history(client, world, clock, _no_history_cache):
    zone = client.get(f"{API}/views/tree").json()["zones"][0]["id"]
    r = _post(client, f"{PLANNER}/projects", {"zoneId": zone, "name": "项目 R"})["id"]
    title = "CFO_agent"
    _confirmed(client, title, 10, projectId=r, app="ptyxis")
    clock(-20)
    _beat(client, title=title, app="ptyxis")
    assert (_focus(client)["projectId"], _focus(client)["source"]) == (r, "history")

    _start(client, projectId=world["q"], label=title)
    assert (_focus(client)["projectId"], _focus(client)["source"]) == (world["q"], "agent-session")

    _track(client)
    clock(-10)
    _beat(client, title=title, app="ptyxis", guess=_guess(taskId=world["a"]))
    assert (_focus(client)["taskId"], _focus(client)["source"]) == (world["a"], "rules")


# ─────────────────────────────────────────── 租户、手动计时、只读、花费


def test_tenants_do_not_see_each_other(client, world, clock):
    _confirmed(client, TITLE, 10, taskId=world["a"])
    _start(client, projectId=world["q"], label="CFO_agent")
    clock(-20)
    _beat(client)
    assert _focus(client)["taskId"] == world["a"] and _focus(client, B) is None

    _beat(client, headers=B)  # 同一个窗口、另一个租户：有 focus，但认不到别人的历史
    assert _target(_focus(client, B)) == NO_TARGET
    _beat(client, title="CFO_agent", app="ptyxis", headers=B)
    assert _target(_focus(client, B)) == NO_TARGET, "别人的代理会话也认不到"


def test_focus_is_still_returned_while_a_manual_timer_runs(client, world, clock):
    _track(client)
    _confirmed(client, TITLE, 10, projectId=world["q"])
    clock(-30)
    _beat(client, guess=_guess(taskId=world["a"]))
    _post(client, f"{API}/timer/start", {"taskId": world["a"]})
    clock(0)
    body = client.get(f"{API}/views/current").json()
    assert body["running"] is True and body["auto"] is None, "手动计时永远优先：auto 照旧为 null"
    focus = _focus(client)
    assert (focus["state"], focus["title"]) == ("present", TITLE)
    assert (focus["projectId"], focus["source"]) == (world["q"], "history"), "在计时没有 auto：往下认"


def test_reading_writes_nothing_and_history_is_looked_up_once_per_window(client, world, clock, monkeypatch):
    """花费的护栏：顶栏每个页面约 10 秒读一次——在场文档每次读一遍，按以往的查找每个窗口 30 秒一次。"""
    from app.modules.activity import focus, repo  # noqa: PLC0415
    from app.modules.events import service as events  # noqa: PLC0415
    from app.repo import get_db  # noqa: PLC0415

    _confirmed(client, TITLE, 10, taskId=world["a"])
    clock(-20)
    _beat(client)
    calls = {"presence": 0, "history": [], "ledger": 0}
    real = (repo.presence_list, repo.confirmed_for_app, events.current_sessions)
    monkeypatch.setattr(repo, "presence_list", lambda u: calls.__setitem__("presence", calls["presence"] + 1) or real[0](u))
    monkeypatch.setattr(repo, "confirmed_for_app", lambda u, a, cap: calls["history"].append(cap) or real[1](u, a, cap))
    monkeypatch.setattr(events, "current_sessions",
                        lambda *a: calls.__setitem__("ledger", calls["ledger"] + 1) or real[2](*a))
    counts = {n: get_db()[n].count_documents({}) for n in get_db().list_collection_names()}

    for _ in range(5):
        assert client.get(f"{API}/views/current").json()["focus"]["taskId"] == world["a"]
    assert calls == {"presence": 5, "history": [focus.HISTORY_SCAN], "ledger": 1}
    assert {n: get_db()[n].count_documents({}) for n in get_db().list_collection_names()} == counts, "只读"

    clock(focus.HISTORY_TTL.total_seconds() - 20)  # 过了 30 秒：重查一次
    _beat(client)
    client.get(f"{API}/views/current")
    assert len(calls["history"]) == 2

    names = {i["name"] for i in get_db()["activity_suggestions"].list_indexes()}
    assert "user_status_app_decided" in names, "按以往的查找走这条索引"
