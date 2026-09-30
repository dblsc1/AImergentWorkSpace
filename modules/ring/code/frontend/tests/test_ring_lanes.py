"""「泳道」主视图（ring-lanes.js + 共享的 __cockpit/lanes.js，nexus-core v2.4 views.lanes.v1）。

同本套件其余文件：真浏览器、接口全在浏览器侧桩掉。桩数据与顶栏预览的测试共用
（modules/nginx-docker/tests/lanes_fixtures.py）。判据落在行为上：画了哪些行、什么顺序、
哪段是什么相位（class + 实际算出的颜色）、谁在闪、悬停说了什么、404 时面板在不在、窄屏会不会横滚。
"""

from __future__ import annotations

import contextlib
from datetime import datetime, timezone
import json
import sys
from typing import Any, Iterator

import pytest
from conftest import CURRENT_IDLE, COCKPIT_STATIC_DIR, PAGE_NAME, RingHarness, _install_stub_routes
from playwright.sync_api import Browser, Route

sys.path.insert(0, str(COCKPIT_STATIC_DIR.parent / "tests"))
import lanes_fixtures as fx  # noqa: E402


class LanesStub:
    def __init__(self, body: dict[str, Any] | None, status: int = 200) -> None:
        self.body = body
        self.status = status
        self.urls: list[str] = []

    def route(self, route: Route) -> None:
        self.urls.append(route.request.url)
        if self.status != 200 or self.body is None:
            route.fulfill(status=self.status, content_type="application/json", body='{"detail":"x"}')
            return
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps(self.body, ensure_ascii=False))


@contextlib.contextmanager
def open_lanes(browser: Browser, base: str, body: dict[str, Any] | None, *, status: int = 200,
               width: int = 1100, theme: str | None = None, reduced_motion: str | None = None,
               clock: bool = False) -> Iterator[tuple[Any, LanesStub]]:
    context = browser.new_context(viewport={"width": width, "height": 900}, timezone_id="Asia/Shanghai",
                                  reduced_motion=reduced_motion)
    page = context.new_page()
    if clock:
        page.clock.install()
    harness = RingHarness(page, CURRENT_IDLE)
    _install_stub_routes(page, harness)
    stub = LanesStub(body, status)
    page.route("**/api/core/views/lanes*", stub.route)
    page.goto(f"{base}/{PAGE_NAME}")
    if theme:
        page.evaluate("t => document.documentElement.setAttribute('data-theme', t)", theme)
        assert page.evaluate("() => getComputedStyle(document.body).colorScheme") == theme
    try:
        yield page, stub
    finally:
        context.close()


def rows(page) -> list[str]:
    return page.eval_on_selector_all(
        "#lanes-view .hcl-row .hcl-name", "ns => ns.map(n => n.textContent)")


def test_lanes_rows_order_human_first_then_runs_by_start(browser, static_base_url) -> None:
    with open_lanes(browser, static_base_url, fx.LANES_FULL) as (page, stub):
        page.wait_for_selector("#lanes-panel:not([hidden]) .hcl-row")
        # 最近 3 小时（07:20–10:20）：六个运行都有重叠，按响应顺序（startAt 升序）
        assert rows(page) == ["我", "old-job", "docs", "garden", "codex", "plot", "tests"]
        # 第一次不带参（= 今天），没跨零点就不补拉
        assert stub.urls[0].endswith("/api/core/views/lanes")
        assert "from=" not in "".join(stub.urls)
        # 标题上的红绿灯：plot 在等，garden/codex/old-job 在干活
        assert page.text_content("#lanes-state") == "1 个在等你 · 3 个在干活"
        # 没名字的运行用 agent；子标题写代理种类
        assert page.text_content("[data-run-id=run_b] .hcl-sub") == "codex"
        assert page.text_content("[data-run-id=run_a] .hcl-sub") == "claude-code"
        assert page.text_content("[data-run-id=run_e] .hcl-sub") == "超时未收"


def seg_classes(page, run_id: str) -> list[str]:
    return page.eval_on_selector_all(
        f"[data-run-id={run_id}] .hcl-seg", "ns => ns.map(n => n.className)")


def test_phase_segments_merge_and_colour_by_class_and_token(browser, static_base_url) -> None:
    with open_lanes(browser, static_base_url, fx.LANES_FULL) as (page, _):
        page.wait_for_selector("[data-run-id=run_b] .hcl-seg")
        # codex：working@09:10 与 working@09:20 读时合成一段，然后 error、working（在跑的末段）
        assert seg_classes(page, "run_b") == [
            "hcl-seg hcl-ph-working", "hcl-seg hcl-ph-error", "hcl-seg hcl-ph-working is-live"]
        assert seg_classes(page, "run_a") == [
            "hcl-seg hcl-ph-working", "hcl-seg hcl-ph-waiting", "hcl-seg hcl-ph-working",
            "hcl-seg hcl-ph-idle", "hcl-seg hcl-ph-working is-live"]
        assert seg_classes(page, "run_c")[-1] == "hcl-seg hcl-ph-waiting is-live"
        # 没报过相位的超时运行：整段当 working，末段虚线
        assert seg_classes(page, "run_e") == ["hcl-seg hcl-ph-working is-live is-overdue"]
        # 人：三种模式 + 在计时 + 在场细带（离开一段带 is-afk）
        human = page.eval_on_selector_all(".hcl-row-human .hcl-seg", "ns => ns.map(n => n.className)")
        assert "hcl-seg hcl-human hcl-mode-prompt" in human
        assert "hcl-seg hcl-human hcl-mode-review" in human
        assert "hcl-seg hcl-human hcl-mode-do is-running" in human
        assert "hcl-seg hcl-presence is-afk" in human
        # 颜色真的来自 tokens（不是 JS 现算）：段的背景 = 该 token 解析出来的颜色
        colours = page.evaluate("""() => {
            const probe = document.createElement('span');
            document.body.appendChild(probe);
            const tok = v => { probe.style.color = `var(${v})`; return getComputedStyle(probe).color; };
            const bg = s => getComputedStyle(document.querySelector(s)).backgroundColor;
            return {
              work: [bg('[data-run-id=run_b] .hcl-ph-working'), tok('--agent-work')],
              wait: [bg('[data-run-id=run_a] .hcl-ph-waiting'), tok('--agent-wait')],
              idle: [bg('[data-run-id=run_a] .hcl-ph-idle'), tok('--ink-3')],
              err:  [bg('[data-run-id=run_b] .hcl-ph-error'), tok('--danger')],
            };
        }""")
        for name, (actual, expected) in colours.items():
            assert actual == expected, name


def animated(page) -> list[str]:
    return page.eval_on_selector_all(
        "#lanes-view .hcl-seg",
        "ns => ns.filter(n => getComputedStyle(n).animationName !== 'none')"
        ".map(n => n.closest('.hcl-row').dataset.runId + ':' + n.className)")


def test_only_live_last_segment_blinks(browser, static_base_url) -> None:
    with open_lanes(browser, static_base_url, fx.LANES_FULL) as (page, _):
        page.wait_for_selector("[data-run-id=run_c] .hcl-seg")
        got = animated(page)
        # 在跑运行的末段且是干活 / 在等；tests 末段是空闲（不闪），docs 已结束（不闪）
        assert sorted(got) == sorted([
            "run_e:hcl-seg hcl-ph-working is-live is-overdue",
            "run_a:hcl-seg hcl-ph-working is-live",
            "run_b:hcl-seg hcl-ph-working is-live",
            "run_c:hcl-seg hcl-ph-waiting is-live",
        ])
        # 黄闪得比绿快
        dur = page.evaluate("""() => [
            getComputedStyle(document.querySelector('[data-run-id=run_c] .is-live')).animationDuration,
            getComputedStyle(document.querySelector('[data-run-id=run_a] .is-live')).animationDuration]""")
        assert float(dur[0].rstrip("s")) < float(dur[1].rstrip("s"))


def test_no_blink_under_reduced_motion(browser, static_base_url) -> None:
    with open_lanes(browser, static_base_url, fx.LANES_FULL, reduced_motion="reduce") as (page, _):
        page.wait_for_selector("[data-run-id=run_c] .hcl-seg")
        assert animated(page) == []
        assert page.eval_on_selector_all(
            "#lanes-view .hcl-dot", "ns => ns.filter(n => getComputedStyle(n).animationName !== 'none').length") == 0


def test_tooltip_names_phase_range_and_duration(browser, static_base_url) -> None:
    with open_lanes(browser, static_base_url, fx.LANES_FULL) as (page, _):
        page.wait_for_selector("[data-run-id=run_c] .is-live")
        page.hover("[data-run-id=run_c] .is-live")
        tip = page.locator("#lanes-view .hcl-tip")
        tip.wait_for(state="visible")
        assert tip.text_content() == "plot · 等你回话 · 10:12–现在（8 分）"
        page.hover("[data-run-id=run_a] .hcl-ph-waiting")
        assert tip.text_content() == "garden · 等你批准（Bash） · 08:55–09:00（5 分）"
        # 在场细带：程序 + 标题只当文本（标题里的 <b> 原样显示，不是标签）
        page.hover(".hcl-presence:not(.is-afk)")
        assert tip.text_content() == "code · <b>plot.gd</b> — garden — VS Code · 09:30–10:05"
        assert page.locator("#lanes-view b").count() == 0


def test_connectors_reply_lines_and_attend_band(browser, static_base_url) -> None:
    with open_lanes(browser, static_base_url, fx.LANES_FULL) as (page, _):
        page.wait_for_selector("#lanes-view .hcl-reply")
        assert page.locator("#lanes-view .hcl-reply").count() == 3
        assert page.locator("#lanes-view .hcl-attend").count() == 1
        assert page.locator("#lanes-view .hcl-now").count() == 1
        # 竖线从人那条线的中线落到该代理那条线的中线
        geo = page.evaluate("""() => {
            const line = [...document.querySelectorAll('.hcl-reply')].pop();   // plot@10:04
            const mid = s => { const r = document.querySelector(s).getBoundingClientRect(); return r.top + r.height / 2; };
            const r = line.getBoundingClientRect();
            return [r.top, r.bottom, mid('.hcl-row-human'), mid('[data-run-id=run_c]')];
        }""")
        assert abs(geo[0] - geo[2]) <= 2 and abs(geo[1] - geo[3]) <= 2
        # 图例 + 读屏摘要
        assert "在等你" in page.text_content("#lanes-view .hcl-legend")
        assert "plot：等你回话" in page.text_content("#lanes-view [data-hcl-summary]")


def test_truncated_says_there_is_more(browser, static_base_url) -> None:
    with open_lanes(browser, static_base_url, fx.truncated()) as (page, _):
        page.wait_for_selector("#lanes-view .hcl-more")
        assert page.text_content("#lanes-view .hcl-more").startswith("还有更多")
    with open_lanes(browser, static_base_url, fx.LANES_FULL) as (page, _):
        page.wait_for_selector("#lanes-view .hcl-row")
        assert page.locator("#lanes-view .hcl-more").count() == 0


def test_empty_day_still_shows_human_lane(browser, static_base_url) -> None:
    with open_lanes(browser, static_base_url, fx.LANES_EMPTY) as (page, _):
        page.wait_for_selector("#lanes-panel:not([hidden]) .hcl-row")
        assert rows(page) == ["我"]
        assert page.text_content("#lanes-view .hcl-empty") == "这段时间没有代理在跑。"
        assert page.text_content("#lanes-state") == ""


@pytest.mark.parametrize("status", [404])
def test_old_backend_hides_the_panel(browser, static_base_url, status) -> None:
    with open_lanes(browser, static_base_url, None, status=status) as (page, stub):
        page.wait_for_selector("#task-select", state="attached")
        page.wait_for_function("() => window.HoneycombLanes")
        page.wait_for_timeout(300)
        assert stub.urls, "应该问过一次"
        assert page.locator("#lanes-panel").is_hidden()


def test_today_toggle_reads_the_day(browser, static_base_url) -> None:
    with open_lanes(browser, static_base_url, fx.LANES_FULL) as (page, stub):
        page.wait_for_selector("#lanes-view .hcl-row")
        page.click(".lanes-range [data-hours='0']")
        page.wait_for_function("() => document.querySelector('.lanes-range [data-hours=\"0\"]').getAttribute('aria-pressed') === 'true'")
        page.wait_for_timeout(200)
        assert stub.urls[-1].endswith("?date=2026-09-30")
        # 今天 0–24 点：刻度 3 小时一格，从 00:00 起
        labels = page.eval_on_selector_all("#lanes-view .hcl-tick-label", "ns => ns.map(n => n.textContent)")
        assert labels[0] == "00:00" and "21:00" in labels


def test_live_window_across_midnight_reads_two_days(browser, static_base_url) -> None:
    with open_lanes(browser, static_base_url, fx.just_after_midnight()) as (page, stub):
        page.wait_for_selector("#lanes-panel:not([hidden])")
        assert stub.urls[1].endswith("?from=2026-09-29&to=2026-09-30")


def test_polls_every_15s_and_stops_while_hidden(browser, static_base_url) -> None:
    with open_lanes(browser, static_base_url, fx.LANES_FULL, clock=True) as (page, stub):
        page.wait_for_selector("#lanes-view .hcl-row")
        # 之后只有 run_for 推时间（给 datetime：数字在各版本 playwright 里单位不一）
        page.clock.pause_at(datetime.fromtimestamp(page.evaluate("() => Date.now()") / 1000 + 1, timezone.utc))
        n = len(stub.urls)
        page.clock.run_for(15_500)
        page.wait_for_timeout(100)
        assert len(stub.urls) == n + 1
        page.evaluate("""() => { Object.defineProperty(document, 'visibilityState', {value: 'hidden', configurable: true});
                                 document.dispatchEvent(new Event('visibilitychange')); }""")
        page.clock.run_for(46_000)
        page.wait_for_timeout(100)
        assert len(stub.urls) == n + 1
        page.evaluate("""() => { Object.defineProperty(document, 'visibilityState', {value: 'visible', configurable: true});
                                 document.dispatchEvent(new Event('visibilitychange')); }""")
        page.wait_for_timeout(100)
        assert len(stub.urls) == n + 2


@pytest.mark.parametrize("width", [320, 390])
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_narrow_no_horizontal_overflow(browser, static_base_url, width, theme) -> None:
    with open_lanes(browser, static_base_url, fx.LANES_FULL, width=width, theme=theme) as (page, _):
        page.wait_for_selector("#lanes-view .hcl-row")
        # ring 在 html/body 上 overflow-x:hidden，页面级 scrollWidth 看不出来：直接量面板
        over = page.evaluate("""(w) => {
            const p = document.getElementById('lanes-panel');
            const bad = [...p.querySelectorAll('*')].filter(n => {
              const r = n.getBoundingClientRect();
              return r.width && (r.right > w + 0.5 || r.left < -0.5) && !n.closest('.hcl-tip');
            }).map(n => n.className);
            return {bad, sw: p.scrollWidth, cw: p.clientWidth};
        }""", width)
        assert over["bad"] == [] and over["sw"] <= over["cw"], over
