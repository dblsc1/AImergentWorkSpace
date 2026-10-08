"""共享顶栏计时芯片的泳道预览（navbar.js 末段 + lanes.js，契约「泳道预览」节）。

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
NAV = {"home": "/hive/", "timer": "/ring/", "tabs": [{"href": "/hive/", "label": "蜂巢"}, {"href": "/ring/", "label": "计时"}]}

PAGE = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<script>window.HONEYCOMB_BASE="/";window.HONEYCOMB_NAV=%s;</script>
<script>%s</script></head>
<body><main style="padding:16px"><h1>页面</h1><button id="other">别的按钮</button></main>
<link rel="stylesheet" href="/__cockpit/tokens.css"><link rel="stylesheet" href="/__cockpit/navbar.css">
<script src="/__cockpit/focus.js" defer></script><script src="/__cockpit/navbar.js" defer></script></body></html>"""


class Site:
    def __init__(self, lanes: dict[str, Any] | None, status: int = 200) -> None:
        self.lanes = lanes
        self.status = status
        self.lanes_urls: list[str] = []
        self.current: dict[str, Any] = {"running": False}

    def route(self, route: Route) -> None:
        path = route.request.url[len(ORIGIN):].split("?")[0]
        if path.startswith("/__cockpit/current"):
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


POP = "[data-ckpt-lanes]"


def is_open(page: Page) -> bool:
    return page.evaluate(f"() => !!document.querySelector('{POP}.ckpt-open')")


def settle(page: Page, ms: int) -> None:
    page.clock.run_for(ms)
    page.wait_for_timeout(50)


def hover_open(page: Page) -> None:
    page.hover("[data-ckpt-chip]")
    settle(page, 200)
    page.wait_for_selector(f"{POP}.ckpt-open .hcl-row")


def test_hover_opens_human_plus_four_agents_in_priority_order(browser) -> None:
    with open_site(browser) as (page, site):
        chip = page.locator("[data-ckpt-chip]")
        assert chip.get_attribute("aria-expanded") == "false"
        assert chip.get_attribute("aria-controls") == page.get_attribute(POP, "id")
        page.hover("[data-ckpt-chip]")
        settle(page, 100)
        assert not is_open(page), "不到 150 ms 不开"
        settle(page, 100)
        page.wait_for_selector(f"{POP}.ckpt-open .hcl-row")
        assert chip.get_attribute("aria-expanded") == "true"
        names = page.eval_on_selector_all(f"{POP} .hcl-name", "ns => ns.map(n => n.textContent)")
        # 在等的在前（plot），其次在干活（按最近一次转入倒序：garden 10:00 > codex 09:55 > old-job），
        # 空闲的 tests 被挤掉；docs 09:15 已结束、不在最近 1 小时里
        assert names == ["我", "plot", "garden", "codex", "old-job"]
        more = page.locator(f"{POP} a.hcl-more")
        assert more.text_content() == "还有 1 个 → 计时页"
        assert more.get_attribute("href") == "/ring/"
        # 同一套配色与段：plot 末段是在等（黄）且在闪
        assert page.eval_on_selector(f"{POP} [data-run-id=run_c] .is-live", "n => n.className") == \
            "hcl-seg hcl-ph-waiting is-live"
        # 2026-10-03 起预览也画在场（人那条线下半），最上一行写人此刻的状态：在电脑前 + 在计时（优先于前台程序）
        assert page.locator(f"{POP} .hcl-track-human .hcl-seg.hcl-presence").count() == 3
        assert page.text_content(f"{POP} .hcl-status") == "我：在电脑前 · 计时中 · 10:00 起"
        assert page.text_content(f"{POP} .hcl-row-human .hcl-sub") == "计时中"
        # 预览仍是列表式（不是计时页的卡片），不写活跃分钟
        assert page.locator(f"{POP} .hcl-card").count() == 0
        assert page.locator(f"{POP} .hcl-stat").count() == 0
        # 读屏摘要
        assert "plot：等你回话" in page.text_content(f"{POP} [data-hcl-summary]")
        assert site.lanes_urls[0].endswith("/api/core/views/lanes")


def test_leaving_closes_after_300ms_and_polling_stops(browser) -> None:
    with open_site(browser) as (page, site):
        hover_open(page)
        settle(page, 15_100)
        n_open = len(site.lanes_urls)
        assert n_open == 2, "打开着约 15 秒拉一次"
        page.mouse.move(5, 700)
        settle(page, 200)
        assert is_open(page), "300 ms 内不关（防闪）"
        settle(page, 200)
        assert not is_open(page)
        assert page.get_attribute("[data-ckpt-chip]", "aria-expanded") == "false"
        settle(page, 60_000)
        assert len(site.lanes_urls) == n_open, "关上就不再拉"


def test_moving_into_popover_keeps_it_open(browser) -> None:
    with open_site(browser) as (page, _):
        hover_open(page)
        box = page.locator(POP).bounding_box()
        page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2, steps=4)
        settle(page, 600)
        assert is_open(page)
        # 段上悬停出提示
        page.hover(f"{POP} [data-run-id=run_c] .is-live")
        assert page.text_content(f"{POP} .hcl-tip") == "plot · 等你回话 · 10:12–现在（8 分）"


def test_keyboard_focus_opens_esc_closes_and_keeps_focus(browser) -> None:
    with open_site(browser) as (page, _):
        page.focus("[data-ckpt-chip]")
        settle(page, 50)
        page.wait_for_selector(f"{POP}.ckpt-open .hcl-row")
        page.keyboard.press("Tab")                       # 焦点走进预览（紧跟芯片）不关
        settle(page, 50)
        assert is_open(page)
        assert page.evaluate(f"() => document.querySelector('{POP}').contains(document.activeElement)")
        page.keyboard.press("Escape")
        assert not is_open(page)
        assert page.evaluate("() => document.activeElement.hasAttribute('data-ckpt-chip')")
        # 焦点走到别的控件上就关
        page.focus("[data-ckpt-chip]")
        page.evaluate("() => document.activeElement.blur()")
        page.focus("[data-ckpt-chip]")
        settle(page, 50)
        page.wait_for_selector(f"{POP}.ckpt-open")
        page.focus("#other")
        assert not is_open(page)


def test_touch_tap_toggles_instead_of_navigating(browser) -> None:
    with open_site(browser, touch=True, width=390) as (page, _):
        page.tap("[data-ckpt-chip]")
        settle(page, 50)
        page.wait_for_selector(f"{POP}.ckpt-open .hcl-row")
        assert page.url == ORIGIN + "/hive/", "第一下点不跳走"
        go = page.locator(f"{POP} .ckpt-lanes-go")
        assert go.text_content() == "去计时页" and go.get_attribute("href") == "/ring/"
        page.tap("[data-ckpt-chip]")
        settle(page, 50)
        assert not is_open(page)
        page.tap("[data-ckpt-chip]")
        settle(page, 50)
        page.wait_for_selector(f"{POP}.ckpt-open")
        page.touchscreen.tap(20, 700)                    # 点外面（预览下方的页面）关
        settle(page, 50)
        assert not is_open(page)


def test_not_on_the_timer_page(browser) -> None:
    with open_site(browser, path="/ring/") as (page, site):
        assert page.locator(POP).count() == 0
        assert page.get_attribute("[data-ckpt-chip]", "aria-expanded") is None
        page.hover("[data-ckpt-chip]")
        page.focus("[data-ckpt-chip]")
        settle(page, 1000)
        assert site.lanes_urls == []
        assert page.evaluate("() => !window.HoneycombLanes"), "计时页上顶栏不加载渲染件"


@pytest.mark.parametrize("status", [404, 401, 500])
def test_non_2xx_means_no_preview(browser, status) -> None:
    with open_site(browser, None, status=status) as (page, site):
        page.hover("[data-ckpt-chip]")
        settle(page, 300)
        page.wait_for_function("() => window.HoneycombLanes")
        settle(page, 100)
        assert site.lanes_urls, "问过"
        assert not is_open(page)
        assert page.get_attribute("[data-ckpt-chip]", "aria-expanded") == "false"
        assert page.url == ORIGIN + "/hive/", "401 也不跳登录页"


def test_hidden_tab_stops_polling(browser) -> None:
    with open_site(browser) as (page, site):
        hover_open(page)
        n = len(site.lanes_urls)
        page.evaluate("""() => { Object.defineProperty(document, 'visibilityState', {value: 'hidden', configurable: true});
                                 document.dispatchEvent(new Event('visibilitychange')); }""")
        settle(page, 46_000)
        assert len(site.lanes_urls) == n
        page.evaluate("""() => { Object.defineProperty(document, 'visibilityState', {value: 'visible', configurable: true});
                                 document.dispatchEvent(new Event('visibilitychange')); }""")
        settle(page, 50)
        assert len(site.lanes_urls) == n + 1


def test_hour_window_across_midnight_reads_two_days(browser) -> None:
    with open_site(browser, fx.just_after_midnight()) as (page, site):
        page.hover("[data-ckpt-chip]")
        settle(page, 200)
        page.wait_for_selector(f"{POP}.ckpt-open")
        assert site.lanes_urls[1].endswith("?from=2026-09-29&to=2026-09-30")


def test_reduced_motion_no_blink(browser) -> None:
    with open_site(browser, reduced_motion="reduce") as (page, _):
        hover_open(page)
        assert page.eval_on_selector_all(
            f"{POP} .hcl-seg, {POP} .hcl-dot",
            "ns => ns.filter(n => getComputedStyle(n).animationName !== 'none').length") == 0


@pytest.mark.parametrize("width", [320, 390, 1100])
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_overlay_fits_viewport_without_layout_shift(browser, width, theme) -> None:
    with open_site(browser, width=width, theme=theme) as (page, _):
        before = page.evaluate("() => [document.querySelector('main').getBoundingClientRect().top, document.body.scrollHeight]")
        page.focus("[data-ckpt-chip]")
        settle(page, 50)
        page.wait_for_selector(f"{POP}.ckpt-open .hcl-row")
        assert pop_colours(page)["pop"] == PANEL[theme]
        box = page.locator(POP).bounding_box()
        assert box["width"] == pytest.approx(min(360, width - 32), abs=0.5)
        assert box["x"] >= 16 - 0.5 and box["x"] + box["width"] <= width - 16 + 0.5
        after = page.evaluate("() => [document.querySelector('main').getBoundingClientRect().top, document.body.scrollHeight]")
        assert after == before, "浮层不占文档流"
        sw = page.evaluate("() => document.scrollingElement.scrollWidth")
        assert sw <= width, f"横向滚动：{sw} > {width}"


def test_focus_survives_mouse_exit_and_polling(browser) -> None:
    """Codex 审核：键盘打开时鼠标划过再离开不关；轮询重画不把焦点从区尾链接上弄丢。"""
    with open_site(browser) as (page, site):
        page.focus("[data-ckpt-chip]")
        settle(page, 50)
        page.wait_for_selector(f"{POP}.ckpt-open a.hcl-more")
        page.focus(f"{POP} a.hcl-more")
        page.hover("[data-ckpt-chip]")
        page.mouse.move(5, 700)
        settle(page, 600)
        assert is_open(page)
        n = len(site.lanes_urls)
        settle(page, 15_100)
        assert len(site.lanes_urls) == n + 1
        assert page.evaluate("() => document.activeElement.classList.contains('hcl-more')")


PANEL = {"light": "rgb(255, 255, 255)", "dark": "rgb(21, 28, 46)"}   # --panel（design-tokens §3.1）


def pop_colours(page: Page) -> dict[str, str]:
    return page.evaluate(f"""() => {{
        const pop = document.querySelector('{POP}');
        const track = pop.querySelector('.hcl-track');
        return {{theme: document.documentElement.getAttribute('data-theme'),
                 pop: getComputedStyle(pop).backgroundColor,
                 nav: getComputedStyle(document.querySelector('[data-ckpt-nav]')).backgroundColor,
                 track: getComputedStyle(track).backgroundColor}};
    }}""")


@pytest.mark.parametrize("how", ["manual", "os"])
def test_popover_follows_dark_theme(browser, how) -> None:
    """暗色：手动选「暗」（localStorage）与「跟随」+ 系统暗色两条路，浮层与顶栏都得是暗面板。"""
    kw = {"theme": "dark"} if how == "manual" else {"theme": "auto", "color_scheme": "dark"}
    with open_site(browser, **kw) as (page, _):
        hover_open(page)
        c = pop_colours(page)
        assert c["theme"] == "dark"
        assert c["pop"] == PANEL["dark"] and c["nav"] == PANEL["dark"], c
        assert c["track"] == "rgb(29, 38, 57)", c          # --panel-2 暗
        # 顶栏主题面板里切到「亮」，浮层跟着变（颜色全是 var(--…)，不用重画）
        page.click("button[aria-label='切换主题']")
        page.click("[data-ckpt-mode='light']")
        assert pop_colours(page)["pop"] == PANEL["light"]


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


def test_preview_reorder_slides_rows_without_scaling(browser) -> None:
    """2026-10-08 换位动效：预览用同一份 lanes.js，重画换位时行平移到新位置（列表式不放大）；第一次打开不动。"""
    moving = "document.getAnimations().filter(a => a.id === 'hcl-move')"
    with open_site(browser) as (page, site):
        hover_open(page)
        assert page.evaluate(f"() => {moving}.length") == 0
        d = json.loads(json.dumps(fx.LANES_FULL))
        next(r for r in d["agents"] if r["runId"] == "run_e")["phases"].append(
            {"at": fx.at("10:19"), "phase": "waiting_input", "detail": None})
        site.lanes = d
        n = len(site.lanes_urls)
        page.clock.run_for(15_100)
        page.wait_for_function(f"() => {moving}.length > 0")
        assert len(site.lanes_urls) == n + 1
        got = page.evaluate(f"""async () => {{
            const as = {moving};
            const out = {{ids: as.map(a => a.effect.target.dataset.runId).sort(),
                          scaled: as.some(a => a.effect.getKeyframes().some(k => /scale\\(1\\.0[1-9]/.test(k.transform)))}};
            as.forEach(a => a.finish());
            await Promise.all(as.map(a => a.finished));
            const rows = [...document.querySelectorAll('{POP} [data-run-id]')];
            return {{...out, order: rows.map(n => n.dataset.runId),
                    transforms: [...new Set(rows.map(n => getComputedStyle(n).transform))]}};
        }}""")
        # old-job 转成在等（10:19，比 plot 的 10:12 新）→ 升到最前，其余三行各让一位
        assert got["order"] == ["run_e", "run_c", "run_a", "run_b"]
        assert got["ids"] == ["run_a", "run_b", "run_c", "run_e"] and not got["scaled"]
        assert got["transforms"] == ["none"]


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
        assert page.get_attribute("[data-ckpt-chip]", "title") == "有个窗口不知道记到哪 —— 去计时页选"
        assert page.get_attribute("[data-ckpt-chip]", "aria-label") == label + "；有个窗口不知道记到哪 —— 去计时页选"
        assert page.get_attribute("[data-ckpt-chip]", "href") == "/ring/"
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


def test_preview_status_line_shows_auto_with_clock(browser) -> None:
    d = json.loads(json.dumps(fx.LANES_FULL))
    d["human"]["running"] = None
    d["human"]["auto"] = {"taskId": None, "projectId": "p_1", "taskName": None, "projectName": "<b>花园</b>",
                          "since": fx.at("10:15"), "app": "code", "title": "garden", "source": "rules"}
    with open_site(browser, d) as (page, site):
        hover_open(page)
        assert page.text_content(f"{POP} .hcl-status .hcl-auto-text") == "自动 · <b>花园</b>"
        assert page.locator(f"{POP} .hcl-status b").count() == 0
        assert page.text_content(f"{POP} .hcl-status .hcl-clock") == "05:00"
        settle(page, 2000)
        assert page.text_content(f"{POP} .hcl-status .hcl-clock") == "05:02"


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
            "正在用 · code · plot.gd — <i>garden</i> —— 有个窗口不知道记到哪 —— 去计时页选"


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


def test_preview_card_line_uses_the_same_words(browser) -> None:
    """泳道预览里人那一行不变；共享件 humanStatus 多回 focus，auto 的字出自同一个 describe()。"""
    d = json.loads(json.dumps(fx.LANES_FULL))
    d["human"]["running"] = None
    d["human"]["focus"] = {"state": "present", "app": "code", "title": "garden", "since": fx.at("10:15"),
                           "projectId": "p_1", "projectName": "花园", "taskId": None, "taskName": None,
                           "source": "agent-session"}
    with open_site(browser, d) as (page, site):
        hover_open(page)
        got = page.evaluate("""d => {
            const st = window.HoneycombLanes.humanStatus(d.human, Date.parse(d.now));
            return [st.focus.lead, st.focus.hint, st.auto];
        }""", d)
        assert got == ["正在：花园", "来自会话", None]
