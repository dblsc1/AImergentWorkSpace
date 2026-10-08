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
<script src="/__cockpit/navbar.js" defer></script></body></html>"""


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
