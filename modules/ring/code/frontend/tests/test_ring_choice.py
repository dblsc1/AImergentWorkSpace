"""自动跟踪在计时页上的两样东西（nexus-core v2.14）：人那张卡上的「自动 · 项目 / 任务」+ 走秒的钟（共享 lanes.js），
和泳道最上面那张「你在 X，记到哪？」（ring-choice.js，经 ring-lanes.js 的 opts.lead 摆进去）。

同 test_ring_lanes.py：真浏览器、接口全在浏览器侧桩掉。判据落在行为上：卡在不在、在第几张、发了什么请求、
服务端各种回法之后卡上写了什么、窗口标题只当文本、窄屏不横滚。
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone
import json
import re
from typing import Any

import pytest
from playwright.sync_api import Route
from test_ring_lanes import fx, open_lanes

EVIL = '<img src=x onerror="window.__pwned=1">'
NEED = {"key": "wk_" + "0a" * 10, "app": "code", "title": "plot.gd — " + EVIL, "since": fx.at("10:12")}
AUTO = {"taskId": "t_a", "projectId": "p_1", "taskName": "<b>浇水</b>", "projectName": "花园", "since": fx.at("10:15"),
        "app": "code", "title": "garden", "source": "rules"}
CARD = "#lanes-view [data-run-id=lead]"


def lanes(*, auto: dict | None = None, need: dict | None = None, running: bool = False) -> dict[str, Any]:
    d = copy.deepcopy(fx.LANES_FULL)
    if not running:
        d["human"]["running"] = None
    d["human"]["auto"], d["human"]["needsChoice"] = auto, need
    return d


class ChoiceStub:
    """activity/choice 与 choice/dismiss：记下请求；成功后服务端不再报这个窗口（泳道桩里拿掉 needsChoice）。"""

    def __init__(self, lanes_stub: Any, reply: tuple[int, dict] | None = None) -> None:
        self.lanes = lanes_stub
        self.reply = reply
        self.posts: list[tuple[str, Any]] = []

    def route(self, route: Route) -> None:
        tail = route.request.url.split("/api/core/activity/", 1)[1]
        self.posts.append((tail, json.loads(route.request.post_data)))
        status, body = self.reply or (200, {"ok": True, "remembered": True, "pseudonymized": False})
        if status == 200:
            self.lanes.body["human"]["needsChoice"] = None
        route.fulfill(status=status, content_type="application/json", body=json.dumps(body, ensure_ascii=False))


def with_choice(page, lanes_stub, reply: tuple[int, dict] | None = None) -> ChoiceStub:
    stub = ChoiceStub(lanes_stub, reply)
    page.route(re.compile(r"/api/core/activity/choice(/dismiss)?$"), stub.route)
    page.wait_for_selector(f"{CARD} .choice-project option[value=p_eng]", state="attached")
    return stub


def deck(page) -> list[str]:
    return page.eval_on_selector_all("#lanes-view > .hcl-deck > .hcl-card", "ns => ns.map(n => n.dataset.runId || 'me')")


# ------------------------------------------------------------------ 「自动 · 项目 / 任务」


def test_auto_pill_with_ticking_clock_distinct_from_manual_timer(browser, static_base_url) -> None:
    with open_lanes(browser, static_base_url, lanes(auto=AUTO), clock=True) as (page, _):
        page.wait_for_selector(".hcl-row-human .hcl-st-auto")
        pills = page.eval_on_selector_all(".hcl-row-human .hcl-pill", "ns => ns.map(n => n.className)")
        assert pills == ["hcl-pill hcl-st-present", "hcl-pill hcl-st-auto"]      # 没有「计时中」那一粒
        assert page.text_content(".hcl-row-human .hcl-auto-text") == "自动 · 花园 / <b>浇水</b>"
        assert page.locator(".hcl-row-human .hcl-st-auto b").count() == 0        # 任务名只当文本
        assert page.text_content(".hcl-row-human .hcl-clock") == "05:00"         # since 10:15，now 10:20
        assert page.eval_on_selector(".hcl-row-human .hcl-st-auto", "n => getComputedStyle(n).borderTopStyle") == "dashed"
        assert page.text_content(".hcl-row-human .hcl-stat") == "正在用 code · garden"
        assert page.locator(".hcl-row-human .is-running").count() == 0           # 轨道上不画「计时中」的段
        page.clock.pause_at(datetime.fromtimestamp(page.evaluate("() => Date.now()") / 1000 + 1, timezone.utc))
        page.clock.run_for(1000)
        a = page.text_content(".hcl-row-human .hcl-clock")
        page.clock.run_for(3000)
        b = page.text_content(".hcl-row-human .hcl-clock")
        secs = lambda t: int(t[:2]) * 60 + int(t[3:])  # noqa: E731
        assert secs(b) - secs(a) == 3 and secs(a) > 300
        assert not page.locator(CARD).count()


def test_auto_project_only_and_manual_timer_wins(browser, static_base_url) -> None:
    only = {**AUTO, "taskId": None, "taskName": None}
    with open_lanes(browser, static_base_url, lanes(auto=only)) as (page, _):
        page.wait_for_selector(".hcl-row-human .hcl-st-auto")
        assert page.text_content(".hcl-row-human .hcl-auto-text") == "自动 · 花园"
    # 在计时：就算响应里带着 auto / needsChoice（服务端此时给 null），也只画手动计时
    with open_lanes(browser, static_base_url, lanes(auto=AUTO, need=NEED, running=True)) as (page, _):
        page.wait_for_selector(".hcl-row-human .hcl-pill")
        assert page.eval_on_selector_all(".hcl-row-human .hcl-pill", "ns => ns.map(n => n.textContent)") == \
            ["在电脑前", "计时中"]
        assert page.locator(".hcl-st-auto").count() == 0 and page.locator(CARD).count() == 0
        assert deck(page)[0] == "me"


# ------------------------------------------------------------------ 「你在 X，记到哪？」


def test_card_on_top_text_only_and_confirm_to_project_bucket(browser, static_base_url) -> None:
    with open_lanes(browser, static_base_url, lanes(need=NEED)) as (page, stub):
        choice = with_choice(page, stub)
        assert deck(page)[:2] == ["lead", "me"]                                  # 泳道最上面，人那张卡之前
        assert page.text_content(f"{CARD} .choice-title") == "你在 code · plot.gd — " + EVIL + "，记到哪？"
        assert page.locator(f"{CARD} img").count() == 0 and page.evaluate("window.__pwned") is None
        assert page.eval_on_selector_all(f"{CARD} .choice-project option", "os => os.map(o => o.textContent)") == \
            ["选项目…", "练琴区 / 吉他练习", "练琴区 / 考级计划", "制作区 / 录音棚整备"]
        assert page.is_checked(f"{CARD} .choice-remember input")                 # 「以后这个窗口都这样记」缺省勾着
        assert page.text_content(f"{CARD} .choice-remember").strip() == "以后这个窗口都这样记"
        assert page.is_disabled(f"{CARD} .choice-ok") and page.is_disabled(f"{CARD} .choice-task")
        assert page.text_content(f"{CARD} .choice-ok") == "确定" and page.text_content(f"{CARD} .choice-skip") == "这次不选"
        page.evaluate("() => { window.__t = 0; window.addEventListener('honeycomb:timer-changed', () => window.__t++); }")
        page.select_option(f"{CARD} .choice-project", "p_eng")
        assert page.input_value(f"{CARD} .choice-task") == ""                    # 缺省「未分类」
        assert page.eval_on_selector(f"{CARD} .choice-task", "s => s.options[0].textContent") == "未分类"
        page.click(f"{CARD} .choice-ok")
        page.wait_for_selector(CARD, state="detached")
        assert choice.posts == [("choice", {"key": NEED["key"], "remember": True, "projectId": "p_eng"})]
        assert deck(page)[0] == "me"
        assert page.evaluate("window.__t") == 1                                  # 顶栏芯片上的小点跟着收


def test_confirm_to_task_without_remember(browser, static_base_url) -> None:
    with open_lanes(browser, static_base_url, lanes(need=NEED)) as (page, stub):
        choice = with_choice(page, stub, (200, {"ok": True, "remembered": False, "pseudonymized": False}))
        page.select_option(f"{CARD} .choice-project", "p_eng")
        names = page.eval_on_selector_all(f"{CARD} .choice-task option", "os => os.map(o => o.value)")
        assert names == ["", "t_word", "t_read", "t_legacy", "t_write", "t_dangling"]
        page.select_option(f"{CARD} .choice-task", "t_read")
        page.uncheck(f"{CARD} .choice-remember input")
        page.click(f"{CARD} .choice-ok")
        page.wait_for_selector(CARD, state="detached")                           # 没让记：不啰嗦，直接收
        assert choice.posts == [("choice", {"key": NEED["key"], "remember": False, "taskId": "t_read"})]


def test_dismiss_this_time(browser, static_base_url) -> None:
    with open_lanes(browser, static_base_url, lanes(need=NEED)) as (page, stub):
        choice = with_choice(page, stub, (200, {"ok": True}))
        page.click(f"{CARD} .choice-skip")
        page.wait_for_selector(CARD, state="detached")
        assert choice.posts == [("choice/dismiss", {"key": NEED["key"]})]


@pytest.mark.parametrize(("reply", "said"), [
    ((200, {"ok": True, "remembered": False, "pseudonymized": True}), "标题代号"),
    ((200, {"ok": True, "remembered": False, "pseudonymized": False}), "规则没写成"),
    ((404, {"detail": "没有这个窗口：'wk_x'（最近 2 小时的在场记录里没有它）"}), "已经不在最近的记录里"),
])
def test_card_explains_then_closes_on_acknowledge(browser, static_base_url, reply, said) -> None:
    with open_lanes(browser, static_base_url, lanes(need=NEED)) as (page, stub):
        choice = with_choice(page, stub, reply)
        page.select_option(f"{CARD} .choice-project", "p_book")
        page.click(f"{CARD} .choice-ok")
        page.wait_for_selector(f"{CARD} .choice-done")
        assert said in page.text_content(f"{CARD} .choice-message")
        assert page.locator(f"{CARD} .choice-ok").count() == 0                   # 不能再点一遍
        assert len(choice.posts) == 1
        stub.body["human"]["needsChoice"] = None
        page.click(f"{CARD} .choice-done")
        page.wait_for_selector(CARD, state="detached")


def test_failure_keeps_card_and_shows_reason_as_text(browser, static_base_url) -> None:
    with open_lanes(browser, static_base_url, lanes(need=NEED)) as (page, stub):
        choice = with_choice(page, stub, (404, {"detail": "项目不存在：'p_eng' <u>x</u>"}))
        page.select_option(f"{CARD} .choice-project", "p_eng")
        page.click(f"{CARD} .choice-ok")
        page.wait_for_selector(f"{CARD} .choice-message.is-error")
        assert page.text_content(f"{CARD} .choice-message") == "没记上：项目不存在：'p_eng' <u>x</u>"
        assert page.locator(f"{CARD} .choice-message u").count() == 0
        assert page.is_enabled(f"{CARD} .choice-ok") and page.input_value(f"{CARD} .choice-project") == "p_eng"
        choice.reply = (403, {"detail": "只有人能选"})
        page.click(f"{CARD} .choice-skip")
        page.wait_for_function("() => document.querySelector('.choice-message').textContent === '没记上：只有人能选'")
        assert choice.posts[-1] == ("choice/dismiss", {"key": NEED["key"]})
        assert page.locator(CARD).count() == 1


def test_card_is_sticky_across_polls_until_answered(browser, static_base_url) -> None:
    with open_lanes(browser, static_base_url, lanes(need=NEED), clock=True) as (page, stub):
        with_choice(page, stub)
        page.select_option(f"{CARD} .choice-project", "p_eng")
        page.select_option(f"{CARD} .choice-task", "t_word")
        page.focus(f"{CARD} .choice-task")
        # 人切到浏览器来答，服务端这时改报别的窗口 / 不报了：卡不换、表单不丢、焦点还在
        stub.body = lanes(need={**NEED, "key": "wk_other", "title": "别的窗口"})
        n = len(stub.urls)
        page.clock.pause_at(datetime.fromtimestamp(page.evaluate("() => Date.now()") / 1000 + 1, timezone.utc))
        page.clock.run_for(15_500)
        page.wait_for_timeout(200)
        assert len(stub.urls) > n
        assert page.get_attribute(CARD, "data-key") == NEED["key"]
        assert page.input_value(f"{CARD} .choice-project") == "p_eng"
        assert page.input_value(f"{CARD} .choice-task") == "t_word"
        assert page.evaluate("() => document.activeElement.className") == "field choice-task"
        assert deck(page)[:2] == ["lead", "me"]
        # 这时手动开始计时：卡收掉（手动计时永远优先）
        stub.body = lanes(need=None, running=True)
        page.clock.run_for(15_500)
        page.wait_for_selector(CARD, state="detached")


@pytest.mark.parametrize("width", [320, 390])
def test_card_and_auto_pill_fit_narrow_screens(browser, static_base_url, width) -> None:
    need = {**NEED, "title": "很长的窗口标题" * 20 + "averyveryverylongunbrokenwindowtitle" * 5}
    auto = {**AUTO, "projectName": "很长很长的项目名字" * 6, "taskName": "averyverylongtaskname" * 5}
    with open_lanes(browser, static_base_url, lanes(auto=auto, need=need), width=width) as (page, stub):
        with_choice(page, stub)
        page.select_option(f"{CARD} .choice-project", "p_eng")
        over = page.evaluate(
            "() => [...document.querySelectorAll('#lanes-panel, #lanes-panel *')]"
            ".filter(e => e.getBoundingClientRect().right > document.documentElement.clientWidth + 0.5)"
            ".map(e => e.tagName + '.' + e.className)")
        assert over == []
        assert page.evaluate("() => document.documentElement.scrollWidth <= document.documentElement.clientWidth")
        assert page.is_visible(".hcl-row-human .hcl-clock")                      # 名字截断，钟不被挤掉
