"""「泳道」主视图（ring-lanes.js + 共享的 __cockpit/lanes.js，nexus-core v2.4 views.lanes.v1）。

同本套件其余文件：真浏览器、接口全在浏览器侧桩掉。桩数据与顶栏预览的测试共用
（modules/nginx-docker/tests/lanes_fixtures.py）。判据落在行为上：画了哪些行、什么顺序、
哪段是什么相位（class + 实际算出的颜色）、谁在闪、悬停说了什么、404 时面板在不在、窄屏会不会横滚。
"""

from __future__ import annotations

import contextlib
import copy
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
        "#lanes-view .hcl-card .hcl-name", "ns => ns.map(n => n.textContent)")


def test_lanes_cards_human_pinned_then_waiting_then_by_activity(browser, static_base_url) -> None:
    with open_lanes(browser, static_base_url, fx.LANES_FULL) as (page, stub):
        page.wait_for_selector("#lanes-panel:not([hidden]) .hcl-card")
        # 最近 3 小时（07:20–10:20），卡片排：人钉在最前；2026-10-08 起按档位：在等你的 plot → 在跑干活
        # （old-job 180 > garden 90 > codex 70 分）→ 在跑空闲 tests → 已结束 docs（105 分也垫底）
        assert rows(page) == ["我", "plot", "old-job", "garden", "codex", "tests", "docs"]
        # 前 5 张代理卡展开（+ 人那张），第 6 张收进「还有 1 个」，默认收着
        top = page.eval_on_selector_all("#lanes-view > .hcl-deck > .hcl-card", "ns => ns.map(n => n.dataset.runId || 'me')")
        assert top == ["me", "run_c", "run_e", "run_a", "run_b", "run_f"]
        assert page.text_content("#lanes-view details.hcl-fold > summary") == "还有 1 个"
        assert not page.is_visible("[data-run-id=run_d]")
        page.click("#lanes-view details.hcl-fold > summary")
        assert page.is_visible("[data-run-id=run_d]")
        # 卡头：相位胶囊 + 活跃分钟；在等你的那张单独标出来
        assert page.text_content("[data-run-id=run_c] .hcl-pill") == "等你回话"
        assert page.text_content("[data-run-id=run_c] .hcl-stat") == "活跃 50 分 · 看了 4 分 · 最近 10:12"
        assert page.text_content("[data-run-id=run_d] .hcl-pill") == "已结束"
        assert page.text_content("[data-run-id=run_d] .hcl-stat") == "活跃 105 分 · 09:15 结束"
        assert page.text_content("[data-run-id=run_e] .hcl-stat") == "活跃 180 分 · 最近 9/29 21:00"
        assert page.eval_on_selector_all("#lanes-view .is-needs-you", "ns => ns.map(n => n.dataset.runId)") == ["run_c"]
        # 人那张卡：此刻在电脑前（最后一段在场到 10:20、不是离开），在计时 → 写计时，优先于前台程序
        assert page.eval_on_selector_all(".hcl-row-human .hcl-pill", "ns => ns.map(n => n.textContent)") == \
            ["在电脑前", "计时中"]
        assert page.text_content(".hcl-row-human .hcl-stat") == "计时中 · 10:00 起"
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
        f"[data-run-id={run_id}] .hcl-seg:not(.hcl-attn)", "ns => ns.map(n => n.className)")


def test_phase_segments_merge_and_colour_by_class_and_token(browser, static_base_url) -> None:
    with open_lanes(browser, static_base_url, fx.LANES_FULL) as (page, _):
        page.wait_for_selector("[data-run-id=run_b] .hcl-seg")
        page.click("#lanes-view details.hcl-fold > summary")
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
              work: [bg('[data-run-id=run_b] .hcl-seg.hcl-ph-working'), tok('--agent-work')],
              wait: [bg('[data-run-id=run_a] .hcl-seg.hcl-ph-waiting'), tok('--agent-wait')],
              idle: [bg('[data-run-id=run_a] .hcl-seg.hcl-ph-idle'), tok('--ink-3')],
              err:  [bg('[data-run-id=run_b] .hcl-seg.hcl-ph-error'), tok('--danger')],
            };
        }""")
        for name, (actual, expected) in colours.items():
            assert actual == expected, name


def animated(page) -> list[str]:
    return page.eval_on_selector_all(
        "#lanes-view .hcl-seg",
        "ns => ns.filter(n => getComputedStyle(n).animationName !== 'none')"
        ".map(n => n.closest('.hcl-card').dataset.runId + ':' + n.className)")


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
        page.hover("[data-run-id=run_a] .hcl-seg.hcl-ph-waiting")
        assert tip.text_content() == "garden · 等你批准（Bash） · 08:55–09:00（5 分）"
        # 在场细带：程序 + 标题只当文本（标题里的 <b> 原样显示，不是标签）
        page.hover(".hcl-seg.hcl-presence:not(.is-afk)")
        assert tip.text_content() == "code · <b>plot.gd</b> — garden — VS Code · 09:30–10:05（35 分）"
        assert page.locator("#lanes-view b").count() == 0


def test_connectors_reply_lines_and_attention_bars(browser, static_base_url) -> None:
    with open_lanes(browser, static_base_url, fx.LANES_FULL) as (page, _):
        page.wait_for_selector("#lanes-view .hcl-reply")
        # 卡片各自分开：回话 / 你在看画在该代理自己的轨道上（garden 两次回话 + 两段在看、plot 一次回话 + 一段在看）
        per_run = page.evaluate("""() => Object.fromEntries([...document.querySelectorAll('#lanes-view [data-run-id]')]
            .map(c => [c.dataset.runId, [c.querySelectorAll('.hcl-track .hcl-reply').length,
                                         c.querySelectorAll('.hcl-track .hcl-attn').length]]))""")
        assert per_run == {"run_c": [1, 1], "run_e": [0, 0], "run_d": [0, 0], "run_a": [2, 2],
                           "run_b": [0, 0], "run_f": [0, 0]}
        assert page.locator("#lanes-view .hcl-attend").count() == 0, "v2.17 之前那条半透明的「在看」带子不再画"
        assert page.locator("#lanes-view .hcl-reply").count() == 3
        # 「现在」每张卡的轨道上各一根，横向位置一致（各卡的轨道与顶上的时间轴对齐）
        xs = page.eval_on_selector_all("#lanes-view > .hcl-deck .hcl-now", "ns => ns.map(n => n.getBoundingClientRect().left)")
        assert len(xs) == 6 and max(xs) - min(xs) < 1
        axis = page.evaluate("() => { const r = document.querySelector('#lanes-view .hcl-axis').getBoundingClientRect(); return [r.left, r.right]; }")
        track = page.evaluate("() => { const r = document.querySelector('#lanes-view .hcl-track').getBoundingClientRect(); return [r.left, r.right]; }")
        assert abs(axis[0] - track[0]) < 1 and abs(axis[1] - track[1]) < 1
        # 悬停回话竖线说清是什么
        page.hover("[data-run-id=run_c] .hcl-reply")
        assert page.text_content("#lanes-view .hcl-tip") == "我回了话 · 10:04"
        # 图例 + 读屏摘要
        assert "在等你" in page.text_content("#lanes-view .hcl-legend")
        assert "在电脑前" in page.text_content("#lanes-view .hcl-legend")
        assert "未计时" not in page.text_content("#lanes-view .hcl-legend"), "在场那半条不扣计时，不能这么写"
        assert "plot：等你回话" in page.text_content("#lanes-view [data-hcl-summary]")


def test_truncated_says_there_is_more(browser, static_base_url) -> None:
    with open_lanes(browser, static_base_url, fx.truncated()) as (page, _):
        page.wait_for_selector("#lanes-view .hcl-more")
        assert page.text_content("#lanes-view .hcl-more").startswith("还有更多")
    with open_lanes(browser, static_base_url, fx.LANES_FULL) as (page, _):
        page.wait_for_selector("#lanes-view .hcl-card")
        assert page.locator("#lanes-view .hcl-more").count() == 0


def test_empty_day_still_shows_human_lane(browser, static_base_url) -> None:
    with open_lanes(browser, static_base_url, fx.LANES_EMPTY) as (page, _):
        page.wait_for_selector("#lanes-panel:not([hidden]) .hcl-card")
        assert rows(page) == ["我"]
        assert page.locator("#lanes-view details.hcl-fold").count() == 0
        assert page.text_content(".hcl-row-human .hcl-pill") == "不在线"
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
        page.wait_for_selector("#lanes-view .hcl-card")
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
        page.wait_for_selector("#lanes-view .hcl-card")
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
        page.wait_for_selector("#lanes-view .hcl-card")
        page.click("#lanes-view details.hcl-fold > summary")
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


# ── 2026-10-03 卡片式：排序 / 折叠 / 人此刻的状态（共享 lanes.js 的纯函数在真浏览器里调） ─────────

SORT_JS = """() => {
  const L = window.HoneycombLanes, at = s => '2026-09-30T' + s + ':00+08:00';
  const now = Date.parse(at('10:00')), v0 = Date.parse(at('09:00'));
  const run = (id, start, phases, end) => ({runId: id, startAt: at(start), endAt: end ? at(end) : null,
    phases: phases.map(([t, p]) => ({at: at(t), phase: p}))});
  const agents = [
    run('busy', '09:00', [['09:00', 'working']]),                                   // 60 分
    run('tieOld', '09:20', [['09:20', 'working'], ['09:50', 'idle']]),             // 30 分，09:50 转入
    run('tieNew', '09:30', [['09:30', 'working'], ['09:55', 'error'], ['09:57', 'idle']]),   // 27 分
    run('tieNew2', '09:25', [['09:25', 'working'], ['09:55', 'idle']]),            // 30 分，09:55 转入
    run('perm', '09:58', [['09:58', 'waiting_permission']]),                       // 2 分但在等你
    run('ask', '09:40', [['09:40', 'working'], ['09:50', 'waiting_input']]),        // 20 分且在等你
    run('endedWait', '09:00', [['09:00', 'waiting_input']], '09:10'),              // 已结束的「在等」不浮
    run('idle', '09:00', [['09:00', 'idle']]),                                      // 0
  ];
  const before = JSON.stringify(agents);
  const got = L.sortByActivity(agents, v0, now, now).map(r => r.runId);
  return {got, untouched: JSON.stringify(agents) === before,
          act: L.activeSeconds(agents[1], v0, now, now), clipped: L.activeSeconds(agents[0], v0 + 1800000, now, now)};
}"""


def test_sort_waiting_first_then_activity_then_recency(browser, static_base_url) -> None:
    with open_lanes(browser, static_base_url, fx.LANES_EMPTY) as (page, _):
        page.wait_for_function("() => window.HoneycombLanes && window.HoneycombLanes.sortByActivity")
        r = page.evaluate(SORT_JS)
        # 在等你的两条在最前；再在跑干活、在跑空闲（按活跃；30 分打平的按最近转入）；已结束的垫底
        assert r["got"] == ["ask", "perm", "busy", "tieNew2", "tieOld", "tieNew", "idle", "endedWait"]
        assert r["untouched"], "不许改入参"
        assert r["act"] == 1800 and r["clipped"] == 1800, "活跃秒数只算视窗内、不算空闲"


TIER_JS = """() => {
  const L = window.HoneycombLanes, at = s => '2026-09-30T' + s + ':00+08:00';
  const now = Date.parse(at('10:00')), v0 = Date.parse(at('09:00'));
  const run = (id, start, phases, end) => ({runId: id, startAt: at(start), endAt: end ? at(end) : null,
    phases: phases.map(([t, p]) => ({at: at(t), phase: p}))});
  const agents = [
    run('endedHeavy', '09:00', [['09:00', 'working']], '09:55'),                  // 55 分但已结束
    run('idleLive', '09:00', [['09:00', 'working'], ['09:50', 'idle']]),           // 50 分，空闲
    run('errLive', '09:30', [['09:30', 'working'], ['09:55', 'error']]),           // 30 分，出错
    run('workLight', '09:58', [['09:58', 'working']]),                             // 2 分，干活
    run('waitLight', '09:59', [['09:59', 'waiting_input']]),                       // 1 分，在等
  ];
  return L.sortByActivity(agents, v0, now, now).map(r => r.runId);
}"""


def test_sort_tiers_live_working_beats_ended_heavy(browser, static_base_url) -> None:
    with open_lanes(browser, static_base_url, fx.LANES_EMPTY) as (page, _):
        page.wait_for_function("() => window.HoneycombLanes && window.HoneycombLanes.sortByActivity")
        assert page.evaluate(TIER_JS) == ["waitLight", "workLight", "errLive", "idleLive", "endedHeavy"]


def test_live_working_never_folded_even_beyond_five(browser, static_base_url) -> None:
    d = copy.deepcopy(fx.LANES_FULL)
    d["agents"] = [fx.run(f"w{i}", f"w{i}", fx.at("09:00"), phases=[(fx.at("09:00"), "working", None)])
                   for i in range(7)] + \
                  [fx.run("old", "old", fx.at("08:00"), end=fx.at("09:50"), phases=[(fx.at("08:00"), "working", None)])]
    d["interactions"] = []
    with open_lanes(browser, static_base_url, d, clock=True) as (page, _):
        page.wait_for_selector("#lanes-view .hcl-card")
        assert page.locator("#lanes-view > .hcl-deck > .hcl-card[data-run-id^=w]").count() == 7
        assert page.eval_on_selector_all("#lanes-view .hcl-fold .hcl-card", "ns => ns.map(n => n.dataset.runId)") == ["old"]


STATUS_JS = """() => {
  const L = window.HoneycombLanes, now = Date.parse('2026-09-30T10:20:00+08:00');
  const sp = (agoTo, afk, app, title, dev) => ({deviceId: dev || 'd1', from: new Date(now - agoTo * 1000 - 60000).toISOString(),
      to: new Date(now - agoTo * 1000).toISOString(), afk, app: app || '', title: title || ''});
  const H = {
    fresh:   {presence: [sp(600, false, 'old'), sp(10, false, 'code', 'garden')]},
    stale:   {presence: [sp(120, false, 'code', 'garden')]},
    afk:     {presence: [sp(5, true)]},
    two:     {presence: [sp(3, true, '', '', 'laptop'), sp(20, false, 'chrome', '', 'desk')]},
    running: {presence: [sp(10, false, 'code', 'garden')], running: {startAt: '2026-09-30T10:00:00+08:00'}},
    skewOk:  {presence: [sp(-30, false, 'code')]},
    skewBad: {presence: [sp(-120, false, 'code')]},
    offRun:  {presence: [], running: {startAt: '2026-09-30T10:00:00+08:00'}},
    none:    null,
  };
  return Object.fromEntries(Object.entries(H).map(([k, h]) => [k, L.humanStatus(h, now)]));
}"""


def test_human_status_fresh_stale_afk_and_running_precedence(browser, static_base_url) -> None:
    with open_lanes(browser, static_base_url, fx.LANES_EMPTY) as (page, _):
        page.wait_for_function("() => window.HoneycombLanes && window.HoneycombLanes.humanStatus")
        r = page.evaluate(STATUS_JS)
        pick = {k: (v["state"], v["word"], v["detail"]) for k, v in r.items()}
        assert pick["fresh"] == ("present", "在电脑前", "code · garden")
        assert pick["stale"] == ("offline", "不在线", ""), "90 秒前就断了的心跳不算在"
        assert pick["afk"] == ("away", "离开", "")
        assert pick["two"] == ("present", "在电脑前", "chrome"), "一台离开、另一台在用 → 在"
        assert pick["running"] == ("present", "在电脑前", "计时中 · 10:00 起"), "在计时优先于前台程序"
        assert pick["offRun"] == ("offline", "不在线", "计时中 · 10:00 起")
        assert pick["none"] == ("offline", "不在线", "")
        assert pick["skewOk"] == ("present", "在电脑前", "code"), "设备时钟快 30 秒：容得下"
        assert pick["skewBad"] == ("offline", "不在线", ""), "快 2 分钟：不算"
        assert r["running"]["running"] and not r["fresh"]["running"]


def many_agents(n: int) -> dict[str, Any]:
    d = copy.deepcopy(fx.LANES_FULL)
    d["agents"] = [fx.run(f"m{i}", f"<img src=x>m{i}", fx.at("09:00"),
                          phases=[(fx.at("09:00"), "working", None), (fx.at(f"09:{10 + i * 4}"), "idle", None)])
                   for i in range(n)]
    d["interactions"] = []
    return d


def test_top_five_then_fold_and_fold_survives_polling(browser, static_base_url) -> None:
    with open_lanes(browser, static_base_url, many_agents(8), clock=True) as (page, stub):
        page.wait_for_selector("#lanes-view .hcl-card")
        # 活跃越久越靠前：m7（09:00–09:38）… m0（09:00–09:10）；人那张卡不算进 5 张
        assert page.eval_on_selector_all("#lanes-view > .hcl-deck > .hcl-card",
                                         "ns => ns.map(n => n.dataset.runId || 'me')") == \
            ["me", "m7", "m6", "m5", "m4", "m3"]
        assert page.eval_on_selector_all("#lanes-view .hcl-fold .hcl-card", "ns => ns.map(n => n.dataset.runId)") == \
            ["m2", "m1", "m0"]
        assert page.text_content("#lanes-view .hcl-fold > summary") == "还有 3 个"
        # 名字只当文本：<img> 原样显示，不是标签
        assert page.locator("#lanes-view img").count() == 0
        assert page.text_content("[data-run-id=m7] .hcl-name") == "<img src=x>m7"
        # 展开后下一次轮询重画，仍然展开、焦点仍在开关上
        page.focus("#lanes-view .hcl-fold > summary")
        page.keyboard.press("Enter")
        assert page.get_attribute("#lanes-view .hcl-fold", "open") is not None
        page.clock.pause_at(datetime.fromtimestamp(page.evaluate("() => Date.now()") / 1000 + 1, timezone.utc))
        n = len(stub.urls)
        page.clock.run_for(15_500)
        page.wait_for_timeout(100)
        assert len(stub.urls) > n
        assert page.get_attribute("#lanes-view .hcl-fold", "open") is not None
        assert page.evaluate("() => document.activeElement.classList.contains('hcl-fold-toggle')")


def test_lanes_sources_never_use_innerhtml() -> None:
    for f in (COCKPIT_STATIC_DIR / "lanes.js",
              COCKPIT_STATIC_DIR.parent.parent / "ring" / "code" / "frontend" / "ring-lanes.js"):
        assert "innerHTML" not in f.read_text(encoding="utf-8").replace("不用 innerHTML", ""), f


def test_fold_disappearing_moves_focus_to_heading_and_open_state_comes_back(browser, static_base_url) -> None:
    with open_lanes(browser, static_base_url, many_agents(8), clock=True) as (page, stub):
        page.wait_for_selector("#lanes-view .hcl-fold")
        page.focus("#lanes-view .hcl-fold > summary")
        page.keyboard.press("Enter")
        page.clock.pause_at(datetime.fromtimestamp(page.evaluate("() => Date.now()") / 1000 + 1, timezone.utc))

        def poll() -> None:
            n = len(stub.urls)
            page.clock.run_for(15_500)
            page.wait_for_timeout(100)
            assert len(stub.urls) > n

        stub.body = many_agents(4)          # 只剩 4 个：折叠区没了，焦点不掉到 body 上
        poll()
        assert page.locator("#lanes-view .hcl-fold").count() == 0
        assert page.evaluate("() => document.activeElement.id") == "lanes-title"
        stub.body = many_agents(7)          # 又多出来：折叠区回来，照旧开着
        poll()
        assert page.text_content("#lanes-view .hcl-fold > summary") == "还有 2 个"
        assert page.get_attribute("#lanes-view .hcl-fold", "open") is not None


# ── 2026-10-08 结束超过 3 小时的运行不画（lanes.js recentRuns），与选的窗口无关 ─────────

def aged() -> dict[str, Any]:
    """现在 10:20。stale 06:10 结束（4 小时多）、edge 07:20 结束（整 3 小时）→ 不画；recent 08:20 结束（2 小时）→ 画；
    在跑的 6 个（含昨晚起、超时挂着的 old-job）都画。"""
    d = copy.deepcopy(fx.LANES_FULL)
    work = lambda t: [(fx.at(t), "working", None)]  # noqa: E731
    d["agents"] = [fx.run("stale", "stale", fx.at("05:00"), end=fx.at("06:10"), phases=work("05:00")),
                   fx.run("edge", "edge", fx.at("06:00"), end=fx.at("07:20"), phases=work("06:00")),
                   fx.run("recent", "recent", fx.at("07:30"), end=fx.at("08:20"), phases=work("07:30")),
                   fx.run("run_e", "old-job", fx.at("21:00", "2026-09-29"), overdue=True)] + \
                  [fx.run(f"w{i}", f"w{i}", fx.at("09:00"), phases=work("09:00")) for i in range(5)]
    d["interactions"] = []
    return d


@pytest.mark.parametrize("hours", ["3", "0"])
def test_runs_ended_over_three_hours_ago_are_hidden_in_both_windows(browser, static_base_url, hours) -> None:
    with open_lanes(browser, static_base_url, aged()) as (page, stub):
        page.wait_for_selector("#lanes-view .hcl-card")
        if hours == "0":
            page.click(".lanes-range [data-hours='0']")
            page.wait_for_function("() => document.querySelectorAll('#lanes-view .hcl-tick-label')[0].textContent === '00:00'")
        ids = page.eval_on_selector_all("#lanes-view [data-run-id]", "ns => ns.map(n => n.dataset.runId)")
        assert sorted(ids) == ["recent", "run_e", "w0", "w1", "w2", "w3", "w4"]
        # 数的也只是画出来的：6 个在干活（都不折叠），折叠区里只有刚结束的那一个
        assert page.text_content("#lanes-state") == "6 个在干活"
        assert page.text_content("#lanes-view .hcl-fold > summary") == "还有 1 个"
        assert page.eval_on_selector_all("#lanes-view .hcl-fold [data-run-id]", "ns => ns.map(n => n.dataset.runId)") == ["recent"]
        said = page.text_content("#lanes-view [data-hcl-summary]")
        assert "recent：已结束" in said and "stale" not in said and "edge" not in said


def test_only_long_ended_runs_left_shows_the_empty_state(browser, static_base_url) -> None:
    d = aged()
    d["agents"] = d["agents"][:2]
    with open_lanes(browser, static_base_url, d) as (page, _):
        page.wait_for_selector("#lanes-view .hcl-card")
        page.click(".lanes-range [data-hours='0']")
        page.wait_for_function("() => document.querySelectorAll('#lanes-view .hcl-tick-label')[0].textContent === '00:00'")
        assert rows(page) == ["我"]
        assert page.text_content("#lanes-view .hcl-empty") == "这段时间没有代理在跑。"
        assert page.locator("#lanes-view .hcl-fold").count() == 0 and page.text_content("#lanes-state") == ""
        # 纯函数：不改入参
        assert page.evaluate("""(a) => { const before = JSON.stringify(a);
            const got = window.HoneycombLanes.recentRuns(a, Date.parse('2026-09-30T10:20:00+08:00')).map(r => r.runId);
            return [got, JSON.stringify(a) === before]; }""", aged()["agents"]) == \
            [["recent", "run_e", "w0", "w1", "w2", "w3", "w4"], True]


# ── 2026-10-08 换位动效（lanes.js motion()）。不看时间：重画那一刻同步抓动画对象，再等它们的 finished ─────────

# 包一层 render：每次画完当场记下「谁身上挂着换位动画」（此刻一定还在跑），并留着入参给测试自己再画一次
SPY_JS = """() => {
  const L = window.HoneycombLanes, real = L.render;
  window.__draws = [];
  L.render = function (root, data, opts) {
    const out = real.apply(this, arguments);
    window.__args = [root, data, opts];
    window.__draws.push(document.getAnimations().filter(a => a.id === 'hcl-move').map(a => {
      const n = a.effect.target, kf = a.effect.getKeyframes(), t = a.effect.getTiming();
      return {id: n.dataset.runId, state: a.playState, delay: t.delay, duration: t.duration,
              props: [...new Set(kf.flatMap(k => Object.keys(k)))].filter(k => k === 'transform' || k === 'opacity').sort(),
              from: kf[0].transform || '', scaled: kf.some(k => /scale\\(1\\.0[1-9]/.test(k.transform || '')),
              rising: n.classList.contains('is-rising'), now: getComputedStyle(n).transform};
    }));
    return out;
  };
}"""
SETTLE_JS = """async () => {
  await Promise.all(document.getAnimations().filter(a => a.id === 'hcl-move').map(a => a.finished));
  const cards = [...document.querySelectorAll('#lanes-view [data-run-id]')];
  return {left: document.getAnimations().filter(a => a.id === 'hcl-move').length,
          transforms: [...new Set(cards.map(n => getComputedStyle(n).transform))],
          rising: document.querySelectorAll('#lanes-view .is-rising').length,
          order: cards.map(n => n.dataset.runId),
          rank: window.HoneycombLanes.sortByActivity(window.__args[1].agents, window.__args[2].viewStart,
                    Date.parse(window.__args[1].now), Date.parse(window.__args[1].now)).map(r => r.runId)};
}"""
REPOLL_JS = "() => document.dispatchEvent(new Event('visibilitychange'))"    # 页面可见时 = 马上再拉一次


def reordered() -> dict[str, Any]:
    """codex、tests 都转成在等你：codex（70 分）、plot（50）、tests（25）→ 两张往上走，其余往下让。"""
    d = copy.deepcopy(fx.LANES_FULL)
    by = {r["runId"]: r for r in d["agents"]}
    by["run_b"]["phases"].append({"at": fx.at("10:18"), "phase": "waiting_permission", "detail": "Bash"})
    by["run_f"]["phases"].append({"at": fx.at("10:15"), "phase": "waiting_input", "detail": None})
    return d


def redraw(page, stub: LanesStub, body: dict[str, Any]) -> list[dict[str, Any]]:
    page.evaluate(SPY_JS)
    stub.body = body
    page.evaluate(REPOLL_JS)
    page.wait_for_function("() => window.__draws.length > 0")
    return page.evaluate("() => window.__draws[0]")


def dy(move: dict[str, Any]) -> float:
    """动画起点的竖向位移（translate(Xpx, Ypx) …）：> 0 = 从下面上来。"""
    return float(move["from"].split(",")[1].split("px")[0])


def test_first_paint_has_no_reorder_motion(browser, static_base_url) -> None:
    with open_lanes(browser, static_base_url, fx.LANES_FULL) as (page, _):
        page.wait_for_selector("#lanes-view .hcl-card")
        assert page.evaluate("() => document.getAnimations().filter(a => a.id === 'hcl-move').length") == 0
        assert page.locator("#lanes-view .is-phase-changed, #lanes-view .is-rising").count() == 0


def test_reorder_moves_up_scaled_and_staggered_others_float_down(browser, static_base_url) -> None:
    with open_lanes(browser, static_base_url, fx.LANES_FULL) as (page, stub):
        page.wait_for_selector("#lanes-view .hcl-fold")
        page.focus("#lanes-view .hcl-fold > summary")
        page.keyboard.press("Enter")
        moves = {m["id"]: m for m in redraw(page, stub, reordered())}
        # 往上走的两张：在跑、从下面的旧位置出发、略放大、压在上面；排队：靠上的先走
        for rid in ("run_b", "run_f"):
            m = moves[rid]
            assert m["state"] == "running" and m["props"] == ["transform"] and m["scaled"] and m["rising"], m
            assert dy(m) > 1, m
        assert moves["run_b"]["now"] not in ("none", "matrix(1, 0, 0, 1, 0, 0)"), "此刻还在旧位置上"
        assert (moves["run_b"]["delay"], moves["run_f"]["delay"]) == (0, 50)
        # 被挤下去的：只平移（不放大、不抬起），比往上的慢一点
        for rid in ("run_c", "run_e", "run_a"):
            m = moves[rid]
            assert m["state"] == "running" and not m["scaled"] and not m["rising"], m
            assert dy(m) < -1, m
            assert m["duration"] > moves["run_b"]["duration"]
        assert "run_d" not in moves, "没换位置的不动"
        assert all(250 <= m["duration"] <= 450 for m in moves.values())
        # 相位变了的两张：胶囊与卡边闪一下（别的不闪）
        assert sorted(page.eval_on_selector_all("#lanes-view .is-phase-changed", "ns => ns.map(n => n.dataset.runId)")) == \
            ["run_b", "run_f"]
        assert page.eval_on_selector("[data-run-id=run_b] .hcl-pill", "n => getComputedStyle(n).animationName") == "hcl-pop"
        # 走完：都回到原位（没有残留的 transform），DOM 顺序 = 排名顺序
        end = page.evaluate(SETTLE_JS)
        assert end["left"] == 0 and end["transforms"] == ["none"] and end["rising"] == 0, end
        assert end["order"] == end["rank"] == ["run_b", "run_c", "run_f", "run_e", "run_a", "run_d"]
        # 折叠区照旧开着、焦点还在开关上
        assert page.get_attribute("#lanes-view .hcl-fold", "open") is not None
        assert page.evaluate("() => document.activeElement.classList.contains('hcl-fold-toggle')")
        # 页面不可见时重画不动（轮询本来就停了；这里直接再画一次）
        hidden = page.evaluate("""(body) => {
            Object.defineProperty(document, 'visibilityState', {value: 'hidden', configurable: true});
            window.HoneycombLanes.render(window.__args[0], body, window.__args[2]);
            return document.getAnimations().filter(a => a.id === 'hcl-move').length;
        }""", fx.LANES_FULL)
        assert hidden == 0
        assert page.eval_on_selector("#lanes-view [data-run-id]", "n => n.dataset.runId") == "run_c"


def test_reduced_motion_reorders_instantly(browser, static_base_url) -> None:
    with open_lanes(browser, static_base_url, fx.LANES_FULL, reduced_motion="reduce") as (page, stub):
        page.wait_for_selector("#lanes-view .hcl-card")
        moves = redraw(page, stub, reordered())
        assert [m for m in moves if "transform" in m["props"]] == [], moves
        end = page.evaluate(SETTLE_JS)
        assert end["transforms"] == ["none"] and end["order"] == end["rank"]
        assert page.eval_on_selector("[data-run-id=run_b] .hcl-pill", "n => getComputedStyle(n).animationName") == "none"


def test_new_card_fades_in_and_fold_crossing_only_fades(browser, static_base_url) -> None:
    with open_lanes(browser, static_base_url, fx.LANES_FULL) as (page, stub):
        page.wait_for_selector("#lanes-view .hcl-card")
        # 新来一个在干活的 → 它淡入 + 上浮；空闲的 tests 被挤进收着的折叠区（看不见了）→ 不动
        d = copy.deepcopy(fx.LANES_FULL)
        d["agents"].append(fx.run("run_g", "fresh", fx.at("10:10"), phases=[(fx.at("10:10"), "working", None)]))
        moves = {m["id"]: m for m in redraw(page, stub, d)}
        assert moves["run_g"]["props"] == ["opacity", "transform"] and moves["run_g"]["state"] == "running"
        assert "run_f" not in moves and not page.is_visible("[data-run-id=run_f]")
        assert page.evaluate(SETTLE_JS)["transforms"] == ["none"]
        # 再画回去：tests 从折叠区里出来（旧位置看不见）→ 只淡入，不飞
        back = {m["id"]: m for m in redraw(page, stub, fx.LANES_FULL)}
        assert back["run_f"]["props"] == ["opacity"], back


# ─────────────────────────────────────────── 你在看：注意力的蓝条（nexus-core v2.17 的 agents[].attention）

BAR = "[data-run-id=run_c] .hcl-track .hcl-attn"
RECT = "n => { const r = n.getBoundingClientRect(); return [r.left, r.right, r.top, r.bottom]; }"


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_attention_bar_is_blue_in_its_own_subrow_on_the_shared_axis(browser, static_base_url, theme) -> None:
    with open_lanes(browser, static_base_url, fx.LANES_FULL, theme=theme) as (page, _):
        page.wait_for_selector(BAR)
        # 横向：与顶上那条时间轴同一把尺（最近 3 小时：07:20 → 10:20，右边留 3%）；plot 的那一段是 10:00–10:04
        axis = page.eval_on_selector("#lanes-view .hcl-axis", RECT)
        v0, v1 = 7 * 60 + 20, 10 * 60 + 20 + 180 * 0.03

        def x(minute: float) -> float:
            return axis[0] + (minute - v0) / (v1 - v0) * (axis[1] - axis[0])

        bar = page.eval_on_selector(BAR, RECT)
        assert abs(bar[0] - x(600)) < 1 and abs(bar[1] - x(604)) < 1, (bar, x(600), x(604))
        # 纵向：自己的一小行，在相位条的正下方、仍在这条线的轨道里（不靠颜色也分得出）
        track = page.eval_on_selector("[data-run-id=run_c] .hcl-track", RECT)
        phases = page.eval_on_selector_all("[data-run-id=run_c] .hcl-track .hcl-seg[class*='hcl-ph-']", "ns => ns.map(%s)" % RECT)
        assert max(p[3] for p in phases) <= bar[2] + 0.5 and bar[3] <= track[3] + 0.5 and bar[3] - bar[2] >= 3
        # 颜色 = 设计 token --attn（亮暗各一个值），与相位的绿 / 黄、人的青都不同
        colours = page.evaluate("""() => {
            const probe = document.createElement('i'); document.body.appendChild(probe);
            const tok = name => { probe.style.background = 'var(' + name + ')'; return getComputedStyle(probe).backgroundColor; };
            return { bar: getComputedStyle(document.querySelector('%s')).backgroundColor, attn: tok('--attn'),
                     others: ['--agent-work', '--agent-wait', '--fact', '--focus', '--plan'].map(tok) };
        }""" % BAR)
        assert colours["bar"] == colours["attn"] and colours["attn"] not in colours["others"]
        # 图例与字：「你在看」取代以前的「在看」；卡头多一句「看了 N 分」；读屏摘要也说
        legend = page.eval_on_selector_all("#lanes-view .hcl-legend .hcl-key", "ns => ns.map(n => n.textContent)")
        assert "你在看" in legend and "在看" not in legend
        assert page.eval_on_selector("#lanes-view .hcl-legend .hcl-swatch.hcl-attn",
                                     "n => getComputedStyle(n).backgroundColor") == colours["attn"]
        assert page.text_content("[data-run-id=run_a] .hcl-stat") == "活跃 90 分 · 看了 12 分 · 最近 10:00"
        assert "看了" not in page.text_content("[data-run-id=run_b] .hcl-stat"), "没看过的不写"
        summary = page.text_content("#lanes-view [data-hcl-summary]")
        assert "plot：等你回话，你看了 4 分" in summary and "garden：在干活，你看了 12 分" in summary
        page.hover(BAR)
        assert page.text_content("#lanes-view .hcl-tip") == "plot · 你在看 · 10:00–10:04（4 分）"
        # 人那条线上，在看 garden 的那一段在场带染成同一个蓝（半透明），别的段不染
        marked = page.eval_on_selector_all(".hcl-row-human .hcl-seg.hcl-presence",
                                           "ns => ns.map(n => n.classList.contains('is-on-agent'))")
        assert marked == [False, False, True]


@pytest.mark.parametrize("reduced", [None, "reduce"])
def test_growing_attention_span_has_a_live_edge_unless_reduced_motion(browser, static_base_url, reduced) -> None:
    with open_lanes(browser, static_base_url, fx.LANES_FULL, reduced_motion=reduced) as (page, _):
        page.wait_for_selector(BAR)
        # 只有「到此刻还在看」的那一段有活边：garden 的 10:10–10:20；plot 的 10:04 就结束了
        live = page.eval_on_selector_all("#lanes-view .hcl-attn.is-live", "ns => ns.map(n => n.closest('.hcl-card').dataset.runId)")
        assert live == ["run_a"]
        name = page.eval_on_selector("#lanes-view .hcl-attn.is-live", "n => getComputedStyle(n, '::after').animationName")
        assert (name == "none") is (reduced == "reduce")
        assert page.eval_on_selector(BAR, "n => getComputedStyle(n, '::after').content") == "none"


@pytest.mark.parametrize("width", [320, 390, 1100])
def test_three_second_switching_draws_a_serial_timeline(browser, static_base_url, width) -> None:
    """每 3 秒切一次窗口：人那条线是一段接一段、互不重叠的细带；两条代理线上的蓝条此起彼伏，从不同时亮。"""
    with open_lanes(browser, static_base_url, fx.fast_switching(), width=width) as (page, _):
        page.wait_for_selector("[data-run-id=run_a] .hcl-attn")
        # 位置按时间算（left / 右端的百分比）：一段的右端就是下一段的左端。画出来每段至少 2 像素宽（看得见），所以不量像素
        spans = page.eval_on_selector_all(".hcl-row-human .hcl-seg.hcl-presence",
                                          "ns => ns.map(n => [parseFloat(n.style.left), parseFloat(n.style.width.slice(5))])")
        assert len(spans) == 40      # [左端 %, 宽 %]（浏览器把 calc(右% - 左%) 化简成一个百分比，留 6 位有效数字）
        assert all(a[1] > 0 and a[0] + a[1] <= b[0] + 1e-3 for a, b in zip(spans, spans[1:])), "在场的段不重叠"
        bars = page.evaluate("""() => ['run_a', 'run_c'].map(id => [...document.querySelectorAll(
            '[data-run-id=' + id + '] .hcl-attn')].map(n => [n.style.left, n.style.width]))""")
        assert len(bars[0]) == 14 and len(bars[1]) == 13
        assert not set(map(tuple, bars[0])) & set(map(tuple, bars[1])), "人同一时刻只看一个"
        assert page.text_content("[data-run-id=run_a] .hcl-stat").startswith("活跃 90 分 · 看了 1 分 · ")
        tips = page.eval_on_selector_all(".hcl-row-human .hcl-seg.hcl-presence", "ns => ns.map(n => n.dataset.tip)")
        assert tips[0] == "ptyxis · garden · 10:18:00–10:18:03（3 秒）"
        over = page.evaluate("""(w) => [...document.getElementById('lanes-panel').querySelectorAll('*')].filter(n => {
            const r = n.getBoundingClientRect();
            return r.width && (r.right > w + 0.5 || r.left < -0.5) && !n.closest('.hcl-tip');
        }).map(n => n.className)""", width)
        assert over == []


# ── 2026-10-09 失联（nexus-core v2.18）：会发心跳的运行 30 分钟没信号 ─────────────────────────

def test_lost_runs_are_grey_rank_after_live_idle_and_are_not_counted(browser, static_base_url) -> None:
    d = copy.deepcopy(fx.LANES_FULL)
    work = lambda t: [(fx.at(t), "working", None)]  # noqa: E731
    gone = {**fx.run("gone", "gone", fx.at("09:00"), phases=work("09:00")), "lost": True, "lastSeenAt": fx.at("09:40")}
    dead = {**fx.run("dead", "dead", fx.at("08:00"), end=fx.at("09:00"), phases=work("08:00")), "outcome": "lost"}
    d["agents"] = [gone, dead, fx.run("busy", "busy", fx.at("09:30"), phases=work("09:30")),
                   fx.run("idle", "idle", fx.at("09:00"), phases=work("09:00") + [(fx.at("09:05"), "idle", None)])]
    d["interactions"] = []
    with open_lanes(browser, static_base_url, d) as (page, _):
        page.wait_for_selector("#lanes-view [data-run-id=gone]")
        ids = page.eval_on_selector_all("#lanes-view [data-run-id]", "ns => ns.map(n => n.dataset.runId)")
        assert ids == ["busy", "idle", "gone", "dead"]  # 在跑的 → 失联 → 已结束
        pill = lambda rid: page.eval_on_selector(  # noqa: E731
            f"[data-run-id={rid}] .hcl-pill", "n => [n.textContent, n.classList.contains('is-ended')]")
        assert pill("gone") == ["失联", True] and pill("dead") == ["失联结束", True]
        assert page.get_attribute("[data-run-id=gone]", "data-phase") == "lost"
        # 段止于最后一次信号（09:40），不再跟着「现在」闪
        assert seg_classes(page, "gone") == ["hcl-seg hcl-ph-working"]
        page.hover("[data-run-id=gone] .hcl-seg")
        tip = page.locator("#lanes-view .hcl-tip")
        tip.wait_for(state="visible")
        assert tip.text_content() == "gone · 在干活 · 09:00–09:40（40 分）"
        assert "最后信号" in page.text_content("[data-run-id=gone] .hcl-stat")
        assert page.text_content("#lanes-state") == "1 个在干活"  # 失联的那个不算
        said = page.text_content("#lanes-view [data-hcl-summary]")
        assert "gone：失联" in said and "dead：失联结束" in said
