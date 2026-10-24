"""此刻的焦点在计时页上（nexus-core v2.16 的 views/current.focus；ring 契约 2026-10-09 条）。

没有手动计时时，大圆环中心不再只说「当前没有进行中的计时」：写出人此刻在哪个窗口 / 项目 / 任务、待了多久，
旁边一个「开始计时」一下就对那个任务起真的手动计时。同本套件其余文件：真浏览器、接口全在浏览器侧桩掉；
时间用 playwright 的假时钟推，不睡。判据落在行为上：中心写了什么、钟走没走、点了发了什么请求、
窗口标题只当文本、减少动效时不呼吸、窄屏不横滚。
"""

from __future__ import annotations

import contextlib
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Iterator

import pytest
from conftest import CURRENT_IDLE, PAGE_NAME, RingHarness, _install_stub_routes, current_running
from playwright.sync_api import Browser, Page, Route
from test_ring_lanes import fx, open_lanes

EVIL = '<img src=x onerror="window.__pwned=1">'
CENTER = "#chrono-center"
TASK = {"projectId": "p_eng", "projectName": "吉他练习", "taskId": "t_word", "taskName": "音阶练习", "source": "history"}
PROJECT = {"projectId": "p_book", "projectName": "考级计划", "source": "agent-session"}


def _focus(page: Page, seconds_ago: int = 125, **over: Any) -> dict[str, Any]:
    since = datetime.fromtimestamp(page.evaluate("() => Date.now()") / 1000 - seconds_ago, timezone.utc).isoformat()
    return {"state": "present", "app": "code", "title": "plot.gd — garden", "since": since, "projectId": None,
            "projectName": None, "taskId": None, "taskName": None, "source": None, **over}


def _idle(**extra: Any) -> dict[str, Any]:
    return {**CURRENT_IDLE, "agents": [], "auto": None, "needsChoice": None, "aiThinking": None, "focus": None, **extra}


@contextlib.contextmanager
def open_focus(browser: Browser, base: str, *, width: int = 1100, reduced_motion: str | None = None,
               theme: str | None = None) -> Iterator[RingHarness]:
    context = browser.new_context(viewport={"width": width, "height": 900}, timezone_id="Asia/Shanghai",
                                  reduced_motion=reduced_motion)
    page = context.new_page()
    page.clock.install()
    harness = RingHarness(page, _idle())
    _install_stub_routes(page, harness)
    page.goto(f"{base}/{PAGE_NAME}")
    page.wait_for_selector("#start-big-btn", state="attached")
    page.wait_for_selector("#project-select option[value=p_eng]", state="attached")
    if theme:
        page.evaluate("t => document.documentElement.setAttribute('data-theme', t)", theme)
    # 钉住假时钟：之后只有 run_for 推时间
    page.clock.pause_at(datetime.fromtimestamp(page.evaluate("() => Date.now()") / 1000 + 1, timezone.utc))
    try:
        yield harness
    finally:
        context.close()


def show(h: RingHarness, **extra: Any) -> None:
    """换一份 views/current 并让页面立刻读一遍（不等 5 秒轮询）。"""
    h.set_current(_idle(**extra))
    h.page.evaluate("() => window.fetchAndRender()")


def texts(page: Page) -> dict[str, str | None]:
    return page.evaluate("""() => Object.fromEntries(["focus-lead", "focus-window", "focus-elapsed", "focus-hint"]
        .map(id => [id.slice(6), document.getElementById(id) && !document.getElementById(id).hidden
                                  ? document.getElementById(id).textContent : null]))""")


def test_center_shows_task_window_clock_and_source(browser, static_base_url) -> None:
    with open_focus(browser, static_base_url) as h:
        page = h.page
        show(h, focus=_focus(page, **TASK))
        assert texts(page) == {"lead": "正在：吉他练习 / 音阶练习", "window": "code · plot.gd — garden",
                               "elapsed": "02:05", "hint": "按以往 · 未计时"}
        assert page.get_attribute(CENTER, "data-focus") == "present"
        assert page.get_attribute("#focus-window", "title") == "code · plot.gd — garden"
        assert page.locator("#idle-caption").count() == 0, "不再只说「当前没有进行中的计时」"
        assert page.eval_on_selector(".chrono", "n => n.classList.contains('is-focus')")
        assert "正在：吉他练习 / 音阶练习" in page.get_attribute("#chrono-svg", "aria-label")
        assert page.text_content("#focus-start-btn") == "开始计时" and page.is_enabled("#focus-start-btn")
        assert page.is_hidden("#controls-row"), "不是手动计时：停止 / 取消那一行不出现"
        # 钟每秒走（假时钟）
        page.clock.run_for(3000)
        assert texts(page)["elapsed"] == "02:08"
        # 满一小时
        show(h, focus=_focus(page, 3725, **TASK))
        assert texts(page)["elapsed"] == "1:02:05"


@pytest.mark.parametrize(("over", "want", "button"), [
    (PROJECT, {"lead": "正在：考级计划", "hint": "来自会话 · 未计时"}, "#focus-start-btn"),
    ({}, {"lead": "正在用", "hint": "未计时"}, "#start-big-btn"),
    ({"state": "afk", "app": "", "title": ""}, {"lead": "离开", "window": None, "hint": None}, "#start-big-btn"),
])
def test_center_states(browser, static_base_url, over, want, button) -> None:
    with open_focus(browser, static_base_url) as h:
        page = h.page
        show(h, focus=_focus(page, **over))
        got = texts(page)
        assert {k: got[k] for k in want} == want
        assert page.locator("#focus-start-btn, #start-big-btn").count() == 1 and page.locator(button).count() == 1
        afk = over.get("state") == "afk"
        assert page.get_attribute(CENTER, "data-focus") == ("afk" if afk else "present")
        assert page.eval_on_selector(".chrono", "n => n.classList.contains('is-afk')") is afk
        if button == "#start-big-btn":   # 认不出目标：还是原来的按钮——计选中的任务，没选就不能点
            assert page.is_disabled(button)
            page.select_option("#project-select", "p_eng")
            page.select_option("#task-select", "t_word")
            assert page.is_enabled(button)


def test_auto_track_and_sources(browser, static_base_url) -> None:
    with open_focus(browser, static_base_url) as h:
        page = h.page
        auto = {"taskId": "t_word", "projectId": "p_eng", "taskName": "音阶练习", "projectName": "吉他练习",
                "since": _focus(page, 65)["since"], "app": "code", "title": "plot.gd", "source": "ai", "key": "wk_1"}
        show(h, auto=auto, focus=_focus(page, 600, **{**TASK, "source": "ai"}))
        assert texts(page) == {"lead": "自动 · 吉他练习 / 音阶练习", "window": "code · plot.gd", "elapsed": "01:05",
                               "hint": "AI 认的 · 自动跟踪中"}
        for source, word in (("rules", "规则"), ("choice", "你选的")):
            show(h, focus=_focus(page, **{**TASK, "source": source}))
            assert texts(page)["hint"] == word + " · 未计时"


def test_no_fresh_presence_keeps_todays_text_and_manual_timer_wins(browser, static_base_url) -> None:
    with open_focus(browser, static_base_url) as h:
        page = h.page
        show(h, focus=None)
        assert page.text_content("#idle-caption") == "当前没有进行中的计时"
        assert page.get_attribute(CENTER, "data-focus") is None and page.locator("#focus-lead").count() == 0

        show(h, focus=_focus(page, **TASK))
        assert page.locator("#focus-lead").count() == 1
        # 手动计时一开：表芯让回计时（focus 照给也不看），虚线轨道收掉
        h.set_current({**current_running(), "focus": _focus(page, **PROJECT)})
        page.evaluate("() => window.fetchAndRender()")
        assert page.locator("#elapsed-display").count() == 1 and page.locator("#focus-lead").count() == 0
        assert page.get_attribute(CENTER, "data-focus") is None
        assert not page.eval_on_selector(".chrono", "n => n.classList.contains('is-focus')")
        assert page.is_visible("#controls-row")
        # 停了、心跳也没了：回到今天的字
        show(h, focus=None)
        assert page.text_content("#idle-caption") == "当前没有进行中的计时"
        assert not page.eval_on_selector(".chrono", "n => n.classList.contains('is-focus')")


def test_start_button_starts_a_real_timer_on_the_shown_task(browser, static_base_url) -> None:
    with open_focus(browser, static_base_url) as h:
        page = h.page
        show(h, focus=_focus(page, **TASK))
        page.click("#focus-start-btn")
        page.wait_for_function("() => document.getElementById('focus-start-btn').textContent === '开始计时'")
        assert [u.split("/api/core/")[1] for u in h.timer_write_urls] == ["timer/start"]
        assert json.loads(h.timer_write_bodies[0]) == {"taskId": "t_word"}
        assert h.planner_writes == [], "认到了任务：不碰未分类桶"
        # 下拉预选好了（桩里的 start 不放行 = 开始失败，人看到的就是这个项目 / 任务）
        assert page.input_value("#project-select") == "p_eng" and page.input_value("#task-select") == "t_word"


def test_start_button_uses_the_projects_unclassified_bucket(browser, static_base_url) -> None:
    with open_focus(browser, static_base_url) as h:
        page = h.page
        calls: list[tuple[str, str]] = []

        def bucket(route: Route) -> None:
            calls.append((route.request.method, route.request.url.split("/api/core/")[1]))
            route.fulfill(status=200, content_type="application/json", body='{"taskId": "t_unc_p_book"}')

        page.route("**/api/core/planner/projects/*/unclassified", bucket)
        show(h, focus=_focus(page, **PROJECT))
        page.click("#focus-start-btn")
        page.wait_for_function("() => document.getElementById('focus-start-btn').textContent === '开始计时'")
        assert calls == [("POST", "planner/projects/p_book/unclassified")]
        assert [json.loads(b) for b in h.timer_write_bodies] == [{"taskId": "t_unc_p_book"}]
        assert page.input_value("#project-select") == "p_book"

        # 取桶失败：不起计时，把话说出来
        page.unroute("**/api/core/planner/projects/*/unclassified")
        page.route("**/api/core/planner/projects/*/unclassified", lambda r: r.fulfill(
            status=404, content_type="application/json", body='{"detail": "项目不存在"}'))
        page.click("#focus-start-btn")
        page.wait_for_selector("#timer-error:not([hidden])")
        assert page.text_content("#timer-error") == "项目不存在" and len(h.timer_write_urls) == 1


def test_countdown_tab_hides_the_focus_start_button(browser, static_base_url) -> None:
    with open_focus(browser, static_base_url) as h:
        page = h.page
        show(h, focus=_focus(page, **TASK))
        page.click("#mode-tab-countdown")
        page.clock.run_for(1100)
        assert page.is_hidden("#focus-start-btn")
        page.click("#mode-tab-stopwatch")
        page.clock.run_for(1100)
        assert page.is_visible("#focus-start-btn")


def test_names_and_titles_are_text_only(browser, static_base_url) -> None:
    with open_focus(browser, static_base_url) as h:
        page = h.page
        show(h, focus=_focus(page, title=EVIL, projectId="p_eng", projectName=EVIL, taskId="t_word", taskName="<b>x</b>",
                             source="history"))
        assert texts(page)["lead"] == f"正在：{EVIL} / <b>x</b>" and texts(page)["window"] == "code · " + EVIL
        assert page.locator(f"{CENTER} img, {CENTER} b").count() == 0
        assert page.evaluate("() => window.__pwned") is None


@pytest.mark.parametrize("reduced", [None, "reduce"])
def test_live_ring_is_dashed_and_breathes_unless_reduced_motion(browser, static_base_url, reduced) -> None:
    with open_focus(browser, static_base_url, reduced_motion=reduced) as h:
        page = h.page
        style = "n => [getComputedStyle(n).animationName, getComputedStyle(n).strokeDasharray]"
        assert page.eval_on_selector(".chrono .track", style) == ["none", "none"], "空闲 / 计时中：实线轨道"
        show(h, focus=_focus(page, **TASK))
        name, dash = page.eval_on_selector(".chrono .track", style)
        assert name == ("none" if reduced else "ring-focus-breathe") and dash != "none", "虚线：和手动计时分得开"
        assert page.eval_on_selector("#seg-current", "n => getComputedStyle(n).visibility") == "hidden"
        show(h, focus=_focus(page, state="afk"))
        assert page.eval_on_selector(".chrono .track", "n => getComputedStyle(n).animationName") == "none"


@pytest.mark.parametrize("width", [320, 390])
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_fits_narrow_screens(browser, static_base_url, width, theme) -> None:
    with open_focus(browser, static_base_url, width=width, theme=theme) as h:
        page = h.page
        show(h, focus=_focus(page, 3700, title="很长很长的窗口标题" * 12, projectId="p_eng",
                             projectName="很长很长的项目名字" * 6, taskId="t_word", taskName="很长的任务名" * 6,
                             source="history"))
        assert page.evaluate("() => document.documentElement.scrollWidth <= document.documentElement.clientWidth")
        # 两行字都截在圆环里（全文在 title 里），按钮点得到
        box = page.eval_on_selector(".chrono", "n => { const r = n.getBoundingClientRect(); return [r.left, r.right]; }")
        for sel in ("#focus-lead", "#focus-window", "#focus-start-btn"):
            left, right = page.eval_on_selector(sel, "n => { const r = n.getBoundingClientRect(); return [r.left, r.right]; }")
            assert box[0] <= left and right <= box[1], sel
        assert page.eval_on_selector("#focus-window", "n => n.scrollWidth > n.clientWidth && n.title.length > 60")
        assert page.eval_on_selector("#focus-start-btn", "n => n.getBoundingClientRect().height") >= 44


def test_lane_card_line_uses_the_same_helper(browser, static_base_url) -> None:
    """泳道里人那张卡那行字：认得出项目时「正在：项目 · 窗口」，认不出照旧「正在用 窗口」。"""
    d = copy.deepcopy(fx.LANES_FULL)
    d["human"]["running"] = None
    line = "#lanes-view .hcl-row-human .hcl-stat, #lanes-view .hcl-card .hcl-stat"
    with open_lanes(browser, static_base_url, d) as (page, stub):
        page.wait_for_selector("#lanes-panel:not([hidden]) .hcl-card")
        before = page.locator(line).first.text_content()
        assert before.startswith("正在用 ")
        d["human"]["focus"] = {"state": "present", "app": "x", "title": "y", "since": fx.at("10:15"),
                               "projectId": "p_1", "projectName": "<b>花园</b>", "taskId": None, "taskName": None,
                               "source": "history"}
        stub.body = d
        page.evaluate("() => document.dispatchEvent(new Event('visibilitychange'))")
        page.wait_for_function("s => document.querySelector(s).textContent.startsWith('正在：')", arg=line.split(",")[0])
        assert page.locator(line).first.text_content() == "正在：<b>花园</b> · " + before[len("正在用 "):]
        assert page.locator("#lanes-view .hcl-stat b").count() == 0


def test_no_innerhtml_added() -> None:
    """本条新增的画法全走 textContent：这几个文件里 innerHTML 的出现次数不许比加之前多。"""
    here = Path(__file__).resolve().parent.parent
    modules = here.parent.parent.parent
    before = {here / "ring-instrument.js": 2, here / "ring-countdown.js": 0, here / "ring-focus.js": 1,
              modules / "nginx-docker/static/navbar.js": 2, modules / "nginx-docker/static/lanes.js": 1,
              modules / "nginx-docker/static/focus.js": 0, modules / "hive/code/frontend/hex-app.js": 9}
    assert {str(f): f.read_text(encoding="utf-8").count("innerHTML") for f in before} == \
        {str(f): n for f, n in before.items()}


# ─────────────────────────────────────────── 串行：永远只写当前这一个窗口（nexus-core v2.17）

WINDOWS = [("ptyxis", "garden", PROJECT), ("code", "notes.md", {}), ("ptyxis", "plot", TASK), ("firefox", "文档", {})]


def test_center_shows_exactly_one_window_under_three_second_switching(browser, static_base_url) -> None:
    """每 3 秒切一次窗口、页面每轮读到的都是另一个窗口：表芯只有一行「在哪」、一行窗口、一个钟——
    写的是此刻这一个，上一个窗口的字一个不留；钟从这一次切过来起数；下面一行小字是这个窗口自己累计的分钟数。"""
    with open_focus(browser, static_base_url) as h:
        page = h.page
        titles = [t for _a, t, _k in WINDOWS]
        for i in range(12):
            app, title, target = WINDOWS[i % len(WINDOWS)]
            show(h, focus=_focus(page, 3, app=app, title=title, dwellSeconds=600 + 60 * i, **target))
            page.wait_for_function("t => document.getElementById('focus-window').textContent.endsWith(t)", arg=title)
            for sel in ("#focus-lead", "#focus-window", "#focus-elapsed", "#focus-dwell", f"{CENTER} button"):
                assert page.locator(sel).count() == 1, sel
            center = page.text_content(CENTER)
            assert [t for t in titles if t in center] == [title], center
            assert texts(page)["window"] == f"{app} · {title}" and texts(page)["elapsed"] == "00:03"
            assert page.text_content("#focus-dwell") == f"近 2 小时在这上面 {10 + i} 分"
            page.clock.run_for(3000)
            assert texts(page)["elapsed"] == "00:06"       # 没切走就接着数；下一轮切走了，从头数
        # 老后端（没有 dwellSeconds）/ 离开：那行小字不出现
        show(h, focus=_focus(page, 3, title="旧后端"))
        page.wait_for_function("() => document.getElementById('focus-window').textContent.endsWith('旧后端')")
        assert page.is_hidden("#focus-dwell")
        show(h, focus=_focus(page, 30, state="afk", app="", title="", dwellSeconds=None))
        page.wait_for_function("() => document.getElementById('focus-lead').textContent === '离开'")
        assert page.is_hidden("#focus-dwell")


def test_dwell_line_updates_without_rebuilding_the_center(browser, static_base_url) -> None:
    with open_focus(browser, static_base_url) as h:
        page = h.page
        show(h, focus=_focus(page, 40, dwellSeconds=59, **TASK))
        page.wait_for_selector("#focus-start-btn")
        assert page.text_content("#focus-dwell") == "近 2 小时在这上面 不到 1 分"
        page.evaluate("() => { document.getElementById('focus-start-btn').dataset.kept = '1'; }")
        show(h, focus=_focus(page, 45, dwellSeconds=3700, **TASK))
        page.wait_for_function("() => document.getElementById('focus-dwell').textContent.endsWith('1 小时 1 分')")
        assert page.get_attribute("#focus-start-btn", "data-kept") == "1", "只换那行小字，表芯不重建"


def _visibility(page: Page, state: str) -> None:
    page.evaluate("""s => { Object.defineProperty(document, 'visibilityState', {value: s, configurable: true});
                            document.dispatchEvent(new Event('visibilitychange')); }""", state)


def test_current_is_polled_every_five_seconds_and_not_while_hidden(browser, static_base_url) -> None:
    """在页面里数 fetch（定时器回调里同步发出，与假时钟同步）：任意 30 秒恰好 6 次，与轮询的相位无关。"""
    with open_focus(browser, static_base_url) as h:
        page = h.page
        page.evaluate("""() => { window.__polls = 0; const real = window.fetch;
            window.fetch = function (url) {
              if (String(url).indexOf('views/current') >= 0) { window.__polls += 1; }
              return real.apply(this, arguments);
            }; }""")
        polls = lambda: page.evaluate("() => window.__polls")  # noqa: E731
        page.clock.run_for(30_000)
        assert polls() == 6
        _visibility(page, "hidden")
        assert polls() == 6
        page.clock.run_for(60_000)
        assert polls() == 6, "页面不可见：不拉"
        _visibility(page, "visible")
        assert polls() == 7, "回到前台：立刻补一次"
