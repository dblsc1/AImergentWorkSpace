"""共享顶栏计时芯片（navbar.js）：芯片状态字 / 读数 / 红点 / 主题 + 悬停信息卡。
泳道预览由 hive.stage.v1「三十」取代，本文件不再测预览。

真浏览器，但不起网关也不起后端：整个站点都在浏览器侧 ``page.route()`` 回——页面是一张
照网关注入形状拼的最小 HTML（tokens.css + navbar.css + navbar.js + HONEYCOMB_NAV），
``__cockpit/*`` 回工作树里的真文件，``api/core/views/lanes`` 回桩数据（lanes_fixtures.py）。

需要 playwright；CI 在「ring 前端测试」那个装了浏览器的 job 里跑本文件。安装器 job 收集到它时
没有 playwright，会响亮 skip。
"""

from __future__ import annotations

import contextlib
from datetime import datetime, timezone
import json
import sys
from pathlib import Path
from typing import Any, Iterator

import pytest

pytest.importorskip("playwright.sync_api", reason="没装 playwright：本文件在 ring 前端测试 job 里跑")
from playwright.sync_api import Browser, Page, Route, sync_playwright  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lanes_fixtures as fx  # noqa: E402

STATIC = Path(__file__).resolve().parent.parent / "static"
ORIGIN = "http://hc.test"
NAV = {"home": "/hive/", "timer": "/hive/", "tabs": [{"href": "/hive/", "label": "蜂巢"}]}

PAGE = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<script>window.HONEYCOMB_BASE="/";window.HONEYCOMB_NAV=%s;</script>
<script>%s</script></head>
<body><main style="padding:16px"><h1>页面</h1><button id="other">别的按钮</button></main>
<link rel="stylesheet" href="/__cockpit/tokens.css"><link rel="stylesheet" href="/__cockpit/navbar.css">
<script src="/__cockpit/focus.js" defer></script><script src="/__cockpit/clockface.js" defer></script><script src="/__cockpit/navbar.js" defer></script></body></html>"""


class Site:
    def __init__(self, lanes: dict[str, Any] | None, status: int = 200) -> None:
        self.lanes = lanes
        self.status = status
        self.lanes_urls: list[str] = []
        self.current_polls = 0
        self.current: dict[str, Any] = {"running": False}

    def route(self, route: Route) -> None:
        path = route.request.url[len(ORIGIN):].split("?")[0]
        if path.startswith("/__cockpit/current"):
            self.current_polls += 1
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps(self.current, ensure_ascii=False))
        elif path.startswith("/__cockpit/"):
            f = STATIC / path.rsplit("/", 1)[-1]
            ctype = {"css": "text/css", "js": "application/javascript"}.get(f.suffix[1:], "application/octet-stream")
            route.fulfill(status=200 if f.is_file() else 404, content_type=ctype,
                          body=f.read_bytes() if f.is_file() else b"")
        elif path.startswith("/api/core/views/lanes"):
            self.lanes_urls.append(route.request.url)
            if self.status != 200 or self.lanes is None:
                route.fulfill(status=self.status, content_type="application/json", body='{"detail":"x"}')
            else:
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps(self.lanes, ensure_ascii=False))
        else:
            # 主题照线上走：localStorage["cockpit-theme"]（light|dark|auto）+ 网关注入的首帧 boot 脚本
            # （nginx/inject.inc 原样）。只设 data-theme 不够——navbar.js 起来会按 localStorage 重算一遍。
            theme = getattr(self, "theme", "light")
            boot = (f"try{{localStorage.setItem('cockpit-theme','{theme}')}}catch(e){{}}"
                    "(function(){try{var d=document.documentElement,t=localStorage.getItem('cockpit-theme')||'auto';"
                    "if(t==='auto')t=matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light';"
                    "d.setAttribute('data-theme',t);var a=localStorage.getItem('cockpit-accent');"
                    "if(a)d.setAttribute('data-accent',a)}catch(e){}})();")
            route.fulfill(status=200, content_type="text/html; charset=utf-8",
                          body=PAGE % (json.dumps(NAV, ensure_ascii=False), boot))


@pytest.fixture(scope="module")
def browser() -> Iterator[Browser]:
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        yield b
        b.close()


@contextlib.contextmanager
def open_site(browser: Browser, lanes: dict[str, Any] | None = fx.LANES_FULL, *, path: str = "/hive/",
              status: int = 200, width: int = 1100, theme: str = "light", touch: bool = False,
              reduced_motion: str | None = None, color_scheme: str = "light") -> Iterator[tuple[Page, Site]]:
    context = browser.new_context(viewport={"width": width, "height": 800}, timezone_id="Asia/Shanghai",
                                  has_touch=touch, is_mobile=touch, reduced_motion=reduced_motion,
                                  color_scheme=color_scheme)
    page = context.new_page()
    page.clock.install()
    site = Site(lanes, status)
    site.theme = theme
    page.route(f"{ORIGIN}/**", site.route)
    page.goto(ORIGIN + path)
    page.wait_for_selector("[data-ckpt-chip]")
    # 钉住假时钟：之后只有 settle() 推时间，机器慢时真实时间不会把 150/300 ms 的防闪计时器提前触发
    page.clock.pause_at(datetime.fromtimestamp(page.evaluate("() => Date.now()") / 1000 + 1, timezone.utc))
    try:
        yield page, site
    finally:
        context.close()


def settle(page: Page, ms: int) -> None:
    page.clock.run_for(ms)
    page.wait_for_timeout(50)


def test_chip_shows_project_name_for_unclassified_bucket(browser) -> None:
    """仓主 2026-10-08：计的是项目的「未分类」时间桶（nexus-core v2.9）时芯片只写项目名；普通任务照旧写任务名。"""
    with open_site(browser) as (page, site):
        start = datetime.now(timezone.utc).isoformat()
        current = {"running": True, "sessionStartAt": start, "project": {"id": "p1", "name": "数学"},
                   "task": {"id": "t_unc_p1", "name": "未分类", "kind": "unclassified"}}
        site.current = current
        page.evaluate("() => window.dispatchEvent(new Event('honeycomb:timer-changed'))")
        page.wait_for_function("() => document.querySelector('.ckpt-live-word').textContent === '数学'")
        site.current = {**current, "task": {"id": "t1", "name": "做题", "kind": "normal"}}
        page.evaluate("() => window.dispatchEvent(new Event('honeycomb:timer-changed'))")
        page.wait_for_function("() => document.querySelector('.ckpt-live-word').textContent === '做题'")


# ------------------------------------------------------------------ 自动跟踪（nexus-core v2.14）


def _poll(page: Page) -> None:
    page.evaluate("() => window.dispatchEvent(new Event('honeycomb:timer-changed'))")


def _auto(page: Page, seconds_ago: int, **over: Any) -> dict[str, Any]:
    since = datetime.fromtimestamp(page.evaluate("() => Date.now()") / 1000 - seconds_ago, timezone.utc).isoformat()
    return {"taskId": "t1", "projectId": "p1", "taskName": "做题", "projectName": "<b>数学</b>", "since": since,
            "app": "code", "title": "garden", "source": "rules", **over}


NAV_SEL = "[data-ckpt-nav]"
WORD = "document.querySelector('.ckpt-live-word').textContent"


def test_chip_shows_auto_track_with_ticking_clock_and_no_extra_requests(browser) -> None:
    with open_site(browser) as (page, site):
        site.current = {"running": False, "auto": _auto(page, 65), "needsChoice": None}
        _poll(page)
        page.wait_for_function(f"() => {WORD} === '自动 · <b>数学</b> / 做题'")
        assert page.get_attribute(NAV_SEL, "data-ckpt-auto") == ""
        assert page.get_attribute(NAV_SEL, "data-ckpt-timer") == "idle"      # 不是手动计时那个状态
        assert page.locator(".ckpt-live-word b").count() == 0                # 项目名只当文本
        assert page.text_content(".ckpt-elapsed") == "01:05"
        settle(page, 3000)
        assert page.text_content(".ckpt-elapsed") == "01:08"
        assert page.is_hidden(".ckpt-need") and page.get_attribute(NAV_SEL, "data-ckpt-choice") is None
        # 只到项目：只写项目名
        site.current = {"running": False, "auto": _auto(page, 5, taskId=None, taskName=None), "needsChoice": None}
        _poll(page)
        page.wait_for_function(f"() => {WORD} === '自动 · <b>数学</b>'")
        # 没了就回到「未在计时」
        site.current = {"running": False, "auto": None, "needsChoice": None}
        _poll(page)
        page.wait_for_function(f"() => {WORD} === '未在计时'")
        assert page.get_attribute(NAV_SEL, "data-ckpt-auto") is None
        assert site.lanes_urls == []                                         # 预览没打开：一次泳道都不拉


def test_manual_timer_always_wins_over_auto_and_choice(browser) -> None:
    with open_site(browser) as (page, site):
        need = {"key": "wk_1", "app": "code", "title": "x", "since": _auto(page, 90)["since"]}
        start = datetime.fromtimestamp(page.evaluate("() => Date.now()") / 1000 - 10, timezone.utc).isoformat()
        # 服务端在计时时本来就给 null；就算带了，芯片也只认手动计时
        site.current = {"running": True, "sessionStartAt": start, "project": {"id": "p1", "name": "数学"},
                        "task": {"id": "t1", "name": "手动的任务", "kind": "normal"},
                        "auto": _auto(page, 65), "needsChoice": need}
        _poll(page)
        page.wait_for_function(f"() => {WORD} === '手动的任务'")
        assert page.get_attribute(NAV_SEL, "data-ckpt-auto") is None
        assert page.get_attribute(NAV_SEL, "data-ckpt-choice") is None and page.is_hidden(".ckpt-need")
        assert page.get_attribute(NAV_SEL, "data-ckpt-timer") == "running"
        # 本机暂停着：也不让 auto 顶掉「已暂停」
        page.evaluate("() => localStorage.setItem('nexus.timer.paused.v1', JSON.stringify({taskId: 't1'}))")
        site.current = {"running": False, "auto": _auto(page, 65), "needsChoice": None}
        _poll(page)
        page.wait_for_function(f"() => {WORD} === '已暂停'")
        assert page.get_attribute(NAV_SEL, "data-ckpt-auto") is None


def test_needs_choice_dot_on_chip(browser) -> None:
    with open_site(browser) as (page, site):
        assert page.is_hidden(".ckpt-need")
        label = page.get_attribute("[data-ckpt-chip]", "aria-label")
        need = {"key": "wk_1", "app": "code", "title": "<i>x</i>", "since": _auto(page, 90)["since"]}
        site.current = {"running": False, "auto": None, "needsChoice": need}
        _poll(page)
        page.wait_for_selector(".ckpt-need", state="visible")
        assert page.get_attribute(NAV_SEL, "data-ckpt-choice") == ""
        assert page.get_attribute("[data-ckpt-chip]", "title") == "有个窗口不知道记到哪 —— 去蜂巢选"
        assert page.get_attribute("[data-ckpt-chip]", "aria-label") == label + "；有个窗口不知道记到哪 —— 去蜂巢选"
        assert page.get_attribute("[data-ckpt-chip]", "href") == "/hive/#human"   # hive.stage.v1「二十七」4：点 = 打开人的页面
        # 可以和 auto 同时有（等人选的不一定是前台窗口）
        site.current = {"running": False, "auto": _auto(page, 5), "needsChoice": need}
        _poll(page)
        page.wait_for_function(f"() => {WORD}.startsWith('自动 · ')")
        assert page.is_visible(".ckpt-need")
        site.current = {"running": False}                                    # 老后端：没有这两个键
        _poll(page)
        page.wait_for_selector(".ckpt-need", state="hidden")
        assert page.get_attribute("[data-ckpt-chip]", "aria-label") == label
        # 网关降级体：一并收掉
        site.current = {"running": False, "auto": _auto(page, 5), "needsChoice": need, "degraded": True}
        _poll(page)
        page.wait_for_function(f"() => document.querySelector('{NAV_SEL}').dataset.ckptTimer === 'degraded'")
        assert page.is_hidden(".ckpt-need") and page.get_attribute(NAV_SEL, "data-ckpt-auto") is None


@pytest.mark.parametrize("width", [320, 390])
def test_auto_chip_fits_narrow_screens(browser, width) -> None:
    with open_site(browser, width=width) as (page, site):
        need = {"key": "wk_1", "app": "code", "title": "x", "since": _auto(page, 90)["since"]}
        site.current = {"running": False, "needsChoice": need,
                        "auto": _auto(page, 3700, projectName="很长很长的项目名字" * 6, taskName="很长的任务名" * 6)}
        _poll(page)
        page.wait_for_function(f"() => {WORD}.startsWith('自动 · ')")
        assert page.text_content(".ckpt-elapsed") == "1:01:40"
        assert page.evaluate("() => document.documentElement.scrollWidth <= document.documentElement.clientWidth")


# ------------------------------------------------------------------ 此刻的焦点（nexus-core v2.16）


def _focus(page: Page, seconds_ago: int, **over: Any) -> dict[str, Any]:
    since = datetime.fromtimestamp(page.evaluate("() => Date.now()") / 1000 - seconds_ago, timezone.utc).isoformat()
    return {"state": "present", "app": "code", "title": "plot.gd — <i>garden</i>", "since": since, "projectId": None,
            "projectName": None, "taskId": None, "taskName": None, "source": None, **over}


def _show(page: Page, site: Site, current: dict[str, Any], word: str) -> None:
    site.current = current
    _poll(page)
    page.wait_for_function(f"w => {WORD} === w", arg=word)


FOCUS_ATTR = "data-ckpt-focus"
TARGET = {"projectId": "p1", "projectName": "<b>数学</b>", "taskId": "t1", "taskName": "做题", "source": "history"}


def test_chip_shows_live_focus_with_ticking_clock_and_no_extra_requests(browser) -> None:
    with open_site(browser) as (page, site):
        seen: list[str] = []
        page.on("request", lambda r: seen.append(r.url[len(ORIGIN):]))
        # 认得出项目：芯片只写项目名（截得狠），全文在 title 里
        _show(page, site, {"running": False, "auto": None, "needsChoice": None, "focus": _focus(page, 125, **TARGET)},
              "正在：<b>数学</b>")
        assert page.get_attribute(NAV_SEL, FOCUS_ATTR) == "present"
        assert page.get_attribute(NAV_SEL, "data-ckpt-timer") == "idle"      # 不是手动计时
        assert page.get_attribute(NAV_SEL, "data-ckpt-auto") is None
        assert page.locator(".ckpt-live-word b, .ckpt-live-word i").count() == 0   # 名字只当文本
        assert page.get_attribute("[data-ckpt-chip]", "title") == "正在：<b>数学</b> / 做题 · code · plot.gd — <i>garden</i>"
        assert page.text_content(".ckpt-elapsed") == "02:05"
        settle(page, 5000)
        assert page.text_content(".ckpt-elapsed") == "02:10"
        # 认不出：写窗口
        _show(page, site, {"running": False, "focus": _focus(page, 3)}, "正在：code · plot.gd — <i>garden</i>")
        assert page.eval_on_selector(".ckpt-live-word", "n => n.scrollWidth > n.clientWidth"), "长了就截断"
        # 离开
        _show(page, site, {"running": False, "focus": _focus(page, 61, state="afk", app="", title="")}, "离开")
        assert page.get_attribute(NAV_SEL, FOCUS_ATTR) == "afk" and page.text_content(".ckpt-elapsed") == "01:01"
        # 没有新鲜的心跳 / 老后端：同以前
        _show(page, site, {"running": False, "focus": None}, "未在计时")
        assert page.get_attribute(NAV_SEL, FOCUS_ATTR) is None and page.text_content(".ckpt-elapsed") == "00:00"
        assert page.get_attribute("[data-ckpt-chip]", "title") == ""
        assert site.lanes_urls == [] and set(seen) == {"/__cockpit/current"}, "只读已经在拉的那一份"


def test_auto_and_focus_share_one_path_without_double_text(browser) -> None:
    with open_site(browser) as (page, site):
        # 服务端两样都给（自动跟踪开着）：只写「自动 · …」，读数从 auto.since 起
        _show(page, site, {"running": False, "auto": _auto(page, 65), "needsChoice": None,
                           "focus": _focus(page, 600, **TARGET)}, "自动 · <b>数学</b> / 做题")
        assert page.get_attribute(NAV_SEL, "data-ckpt-auto") == "" and page.get_attribute(NAV_SEL, FOCUS_ATTR) is None
        assert page.text_content(".ckpt-elapsed") == "01:05"
        assert "正在" not in page.text_content("[data-ckpt-chip]")
        # 等人选的小点照旧，title 两句都在
        need = {"key": "wk_1", "app": "code", "title": "x", "since": _auto(page, 90)["since"]}
        _show(page, site, {"running": False, "auto": None, "needsChoice": need, "focus": _focus(page, 5)},
              "正在：code · plot.gd — <i>garden</i>")
        assert page.is_visible(".ckpt-need") and page.get_attribute(NAV_SEL, "data-ckpt-choice") == ""
        assert page.get_attribute("[data-ckpt-chip]", "title") == \
            "正在用 · code · plot.gd — <i>garden</i> —— 有个窗口不知道记到哪 —— 去蜂巢选"


def test_manual_timer_pause_and_degraded_hide_focus(browser) -> None:
    with open_site(browser) as (page, site):
        start = datetime.fromtimestamp(page.evaluate("() => Date.now()") / 1000 - 10, timezone.utc).isoformat()
        _show(page, site, {"running": True, "sessionStartAt": start, "project": {"id": "p1", "name": "数学"},
                           "task": {"id": "t1", "name": "手动的任务", "kind": "normal"}, "auto": None,
                           "focus": _focus(page, 300, **TARGET)}, "手动的任务")
        assert page.get_attribute(NAV_SEL, FOCUS_ATTR) is None and page.get_attribute(NAV_SEL, "data-ckpt-timer") == "running"
        assert page.text_content(".ckpt-elapsed") == "00:10"
        page.evaluate("() => localStorage.setItem('nexus.timer.paused.v1', JSON.stringify({taskId: 't1'}))")
        _show(page, site, {"running": False, "focus": _focus(page, 300, **TARGET)}, "已暂停")
        assert page.get_attribute(NAV_SEL, FOCUS_ATTR) is None
        page.evaluate("() => localStorage.removeItem('nexus.timer.paused.v1')")
        _show(page, site, {"running": False, "degraded": True, "focus": _focus(page, 300, **TARGET)}, "状态未知")
        assert page.get_attribute(NAV_SEL, FOCUS_ATTR) is None


@pytest.mark.parametrize("reduced", [None, "reduce"])
def test_focus_dot_breathes_unless_reduced_motion(browser, reduced) -> None:
    with open_site(browser, reduced_motion=reduced) as (page, site):
        _show(page, site, {"running": False, "focus": _focus(page, 5, **TARGET)}, "正在：<b>数学</b>")
        style = page.eval_on_selector(".ckpt-dot", "n => [getComputedStyle(n).animationName, "
                                                   "getComputedStyle(n.parentNode).borderTopStyle]")
        assert style == ["none" if reduced else "ckpt-breathe", "dashed"], "虚线边：和手动计时的实线分得开"
        _show(page, site, {"running": False, "focus": _focus(page, 5, state="afk")}, "离开")
        assert page.eval_on_selector(".ckpt-dot", "n => getComputedStyle(n).animationName") == "none"


@pytest.mark.parametrize("width", [320, 390])
def test_focus_chip_fits_narrow_screens(browser, width) -> None:
    with open_site(browser, width=width) as (page, site):
        long = _focus(page, 3700, title="很长很长的窗口标题" * 12, projectId="p1", projectName="很长很长的项目名字" * 6,
                      source="history")
        _show(page, site, {"running": False, "focus": long}, "正在：" + "很长很长的项目名字" * 6)
        assert page.text_content(".ckpt-elapsed") == "1:01:40"
        assert page.evaluate("() => document.documentElement.scrollWidth <= document.documentElement.clientWidth")


# ─────────────────────────────────────────── 5 秒一拉、不可见退避；预览里的蓝条（nexus-core v2.17）


def _visibility(page: Page, state: str) -> None:
    page.evaluate("""s => { Object.defineProperty(document, 'visibilityState', {value: s, configurable: true});
                            document.dispatchEvent(new Event('visibilitychange')); }""", state)


def test_current_is_polled_every_5s_while_visible_and_backs_off_when_hidden(browser) -> None:
    with open_site(browser) as (page, site):
        _poll(page)                                   # 对齐：从这一次起数
        settle(page, 50)
        n = site.current_polls
        settle(page, 4900)
        assert site.current_polls == n, "不到 5 秒不拉"
        settle(page, 200)
        assert site.current_polls == n + 1
        settle(page, 10_000)
        assert site.current_polls == n + 3
        _visibility(page, "hidden")                   # 切到后台：不立刻拉，改按 60 秒
        settle(page, 55_000)
        assert site.current_polls == n + 3
        settle(page, 6_000)
        assert site.current_polls == n + 4
        _visibility(page, "visible")                  # 回到前台：立刻一次，然后回到 5 秒
        settle(page, 50)
        assert site.current_polls == n + 5
        settle(page, 5_100)
        assert site.current_polls == n + 6
        assert site.lanes_urls == [], "预览没打开：泳道一次都不拉"


def test_chip_tooltip_carries_the_dwell_line(browser) -> None:
    with open_site(browser) as (page, site):
        _show(page, site, {"running": False, "focus": {**_focus(page, 3), "dwellSeconds": 754}},
              "正在：code · plot.gd — <i>garden</i>")
        assert page.text_content(".ckpt-elapsed") == "00:03"
        assert page.get_attribute("[data-ckpt-chip]", "title").endswith("（近 2 小时在这上面 12 分）")


# ------------------------------------------------------------------ 计时胶囊长成时钟的样子（hive.stage.v1「二十七」4）


def _running(page: Page, seconds_ago: int) -> dict[str, Any]:
    since = datetime.fromtimestamp(page.evaluate("() => Date.now()") / 1000 - seconds_ago, timezone.utc).isoformat()
    return {"running": True, "sessionStartAt": since, "task": {"id": "t1", "name": "做题"}, "project": {"name": "数学"}}


def test_chip_looks_like_the_clock_a_dial_with_a_red_arc_big_reading_and_small_line(browser) -> None:
    with open_site(browser) as (page, site):
        assert page.locator("[data-ckpt-chip] .ckpt-dial svg .hs").count() == 1
        assert page.get_attribute("[data-ckpt-chip] .st-arc-timer", "stroke-dasharray") == "0 100"
        site.current = _running(page, 15 * 60)                      # 15 分钟 = 1/4 圈
        _poll(page)
        page.wait_for_function("() => document.querySelector('.ckpt-live-word').textContent === '做题'")
        settle(page, 1000)
        arc = float(page.get_attribute("[data-ckpt-chip] .st-arc-timer", "stroke-dasharray").split()[0])
        assert 25 <= arc < 26 and page.text_content(".ckpt-elapsed").startswith("15:")
        box = page.locator("[data-ckpt-chip]").bounding_box()
        assert box and box["height"] <= 60 and page.is_visible("[data-ckpt-chip] .ckpt-dial")


def test_hive_page_can_take_over_the_chip_text_and_the_nav_hangs_no_card(browser) -> None:
    with open_site(browser) as (page, site):
        page.evaluate("() => window.HoneycombNavChip.takeOver()")
        page.evaluate("() => window.HoneycombNavChip.set({now: Date.now(), arc: 40, countdown: true, big: '12:34', small: '数学 · 今天 1 小时 2 分'})")
        assert page.text_content(".ckpt-elapsed") == "12:34" and page.text_content(".ckpt-live-word") == "数学 · 今天 1 小时 2 分"
        assert page.get_attribute("[data-ckpt-chip] .st-arc-timer", "stroke-dasharray") == "40 100"
        settle(page, 2000)                                          # 顶栏自己的每秒重画不覆盖接管后的字
        assert page.text_content(".ckpt-elapsed") == "12:34"
        page.hover("[data-ckpt-chip]")
        settle(page, 600)
        assert page.locator(".st-hover-card").count() == 0 and site.lanes_urls == []


def _card_rows(page: Page) -> list[str]:
    return page.eval_on_selector_all(".st-hover-card .st-hc-row", "ns => ns.map(n => n.textContent)")


@pytest.mark.parametrize("path", ["/hive/", "/ring/", "/other/"])
def test_hover_shows_the_info_card_on_any_page_and_leaving_hides_it(browser, path) -> None:
    """hive.stage.v1「三十」：非蜂巢页（含 /ring/）悬停约 300ms 出信息卡，移开消失；不拉 views/lanes。"""
    with open_site(browser, path=path) as (page, site):
        site.current = _running(page, 125)
        _poll(page)
        page.wait_for_function("() => document.querySelector('.ckpt-live-word').textContent === '做题'")
        seen: list[str] = []
        page.on("request", lambda r: seen.append(r.url[len(ORIGIN):]))
        page.hover("[data-ckpt-chip]")
        settle(page, 150)
        assert page.locator(".st-hover-card").count() == 0, "不到 300ms 不出"
        settle(page, 200)
        rows = _card_rows(page)
        assert rows and rows[0].startswith("正在计时 · 做题 · 02:"), rows
        assert len(rows) == 1, "顶栏读不到今天的用时：不写那一行"
        assert page.locator("[data-ckpt-lanes], #ckpt-lanes-pop").count() == 0
        page.mouse.move(400, 600)
        settle(page, 50)
        assert page.locator(".st-hover-card").count() == 0
        assert site.lanes_urls == [] and not any("views/lanes" in u for u in seen)


def test_keyboard_focus_shows_the_card_and_press_hides_it(browser) -> None:
    with open_site(browser) as (page, site):
        page.focus("[data-ckpt-chip]")
        settle(page, 400)
        assert _card_rows(page) == ["没在计时"]
        page.mouse.move(5, 700)
        page.mouse.down()
        settle(page, 50)
        assert page.locator(".st-hover-card").count() == 0
        page.mouse.up()


def test_card_lines_follow_idle_focus_and_running(browser) -> None:
    with open_site(browser) as (page, site):
        def hover() -> list[str]:
            page.mouse.move(400, 600)
            settle(page, 50)
            page.hover("[data-ckpt-chip]")
            settle(page, 350)
            return _card_rows(page)
        assert hover() == ["没在计时"]
        _show(page, site, {"running": False, "focus": _focus(page, 5, **TARGET)}, "正在：<b>数学</b>")
        rows = hover()
        assert rows[0] == "没在计时" and "<b>数学</b>" in rows[1] and len(rows) == 2, rows
        _show(page, site, {"running": False, "focus": _focus(page, 61, state="afk", app="", title="")}, "离开")
        assert hover() == ["没在计时", "离开"]
        _show(page, site, _running(page, 125), "做题")
        rows = hover()
        assert len(rows) == 1 and rows[0].startswith("正在计时 · 做题 · 02:")
        assert site.lanes_urls == []


def test_no_clockface_means_no_card_and_chip_still_links(browser) -> None:
    ctx = browser.new_context(viewport={"width": 1100, "height": 800})
    page = ctx.new_page()
    site = Site(None)
    page.route(f"{ORIGIN}/**", lambda r: r.fulfill(status=200, content_type="application/javascript", body=b"") if r.request.url.endswith("clockface.js") else site.route(r))
    page.goto(ORIGIN + "/ring/")
    page.wait_for_selector("[data-ckpt-chip]")
    page.hover("[data-ckpt-chip]")
    page.wait_for_timeout(500)
    assert page.locator(".st-hover-card").count() == 0
    assert page.get_attribute("[data-ckpt-chip]", "href") == "/hive/#human"
    ctx.close()
