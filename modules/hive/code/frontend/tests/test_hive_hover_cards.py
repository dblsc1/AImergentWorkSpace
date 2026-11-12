"""右上角时钟体现计时、悬停时钟 / 中心环出粗略信息卡（hive.stage.v1「二十七」）。夹具沿用 test_hive_stage.py。"""

from __future__ import annotations

from datetime import datetime, timedelta
import json
import os
from typing import Any

from test_hive_focus import base, browser  # noqa: F401  (fixtures)
from test_hive_stage import TZ, Stage, _busy_day, _iso, open_stage

CARD = ".st-hover-card"


def _timing(st: Stage, seconds: int, project: str | None = "p_math") -> datetime:
    start = st.now().astimezone(TZ) - timedelta(seconds=seconds)
    st.lanes["human"]["running"] = {"startAt": _iso(start), "taskId": None, "projectId": project}
    st.poll()
    return start


def _clock_text(st: Stage, start: datetime, lag: int = 0) -> str:
    """页面此刻（假时钟）算出的计时读数；poll 会把假时钟推进 5.6 秒，所以不能按设定值写死。"""
    sec = int(st.page.evaluate("() => Date.now()") / 1000 - start.timestamp()) - lag
    return f"{sec // 3600}:{sec // 60 % 60:02d}:{sec % 60:02d}" if sec >= 3600 else f"{sec // 60:02d}:{sec % 60:02d}"


CHIP_STUB = """
window.__chipSets = [];
window.__chipStub = { chip: null, takeOver() { window.__taken = true; }, set(f) { window.__chipSets.push(f); } };
document.addEventListener('DOMContentLoaded', () => {
  const a = document.createElement('a'); a.href = '#x'; a.id = 'navChip'; a.textContent = 'chip';
  a.style.cssText = 'position:fixed;left:200px;top:8px;width:120px;height:36px;z-index:5000;background:#eee';
  document.body.appendChild(a); window.__chipStub.chip = a; window.HoneycombNavChip = window.__chipStub; window.dispatchEvent(new CustomEvent('honeycomb:navchip', { detail: window.__chipStub }));
});
"""


def _hover(st: Stage, selector: str, ms: int = 350) -> None:
    x, y = st.center(selector)
    st.page.mouse.move(x, y)
    st.page.clock.run_for(ms)


def _lines(st: Stage) -> list[str]:
    return st.page.eval_on_selector_all(f"{CARD} .st-hc-row", "ns => ns.map(n => n.textContent)")


def _shot(st: Stage, name: str) -> None:
    d = os.environ.get("HOVER_SHOT_DIR")
    if d:
        os.makedirs(d, exist_ok=True)
        st.page.screenshot(path=os.path.join(d, name))


def test_clock_shows_a_red_arc_and_elapsed_time_while_timing_and_plain_when_not(browser, base) -> None:  # noqa: F811  二十七·1 / 3
    with open_stage(browser, base, setup=_busy_day) as st:
        assert st.page.get_attribute("#stClock .st-arc-timer", "stroke-dasharray") == "0 100"
        assert "小时" in st.page.locator("#stClock .st-clock-today").inner_text()
        start = _timing(st, 15 * 60)                                # 约 1/4 圈
        st.page.clock.run_for(1000)
        assert st.page.locator("#stClock .st-clock-today").inner_text() in (_clock_text(st, start), _clock_text(st, start, 1))
        small = st.page.locator("#stClock .st-clock-proj").inner_text()
        assert small.startswith("数学 · 今天 ") and small.endswith(" 分")
        arc = float(st.page.get_attribute("#stClock .st-arc-timer", "stroke-dasharray").split()[0])
        assert 25 < arc < 26
        start = _timing(st, 3700, project=None)                    # 满 1 小时：h:mm:ss，没记到项目只写「今天」
        st.page.clock.run_for(1000)
        assert st.page.locator("#stClock .st-clock-today").inner_text() in (_clock_text(st, start), _clock_text(st, start, 1)) and st.page.locator("#stClock .st-clock-today").inner_text().startswith("1:")
        assert st.page.locator("#stClock .st-clock-proj").inner_text().startswith("今天 ")
        del st.lanes["human"]["running"]
        st.poll()
        assert st.page.get_attribute("#stClock .st-arc-timer", "stroke-dasharray") == "0 100"


def test_hover_clock_card_appears_after_300ms_below_the_clock_and_goes_away(browser, base) -> None:  # noqa: F811  二十七·5 / 6
    with open_stage(browser, base, setup=_busy_day) as st:
        start = _timing(st, 125)
        x, y = st.center("#stClock")
        st.page.mouse.move(x, y)
        st.page.clock.run_for(200)
        assert st.page.locator(CARD).count() == 0, "不到 300 毫秒不出"
        st.page.clock.run_for(150)
        assert st.page.locator(CARD).count() == 1
        lines = _lines(st)
        assert lines[0] == "正在计时 · 数学 · " + _clock_text(st, start) and lines[1].startswith("今天 ") and lines[-1].startswith("数学 ")
        clock, card = st.box("#stClock"), st.box(CARD)
        assert card["y"] >= clock["y"] + clock["height"] and card["x"] >= 0 and card["x"] + card["width"] <= 1280 and card["y"] + card["height"] <= 900
        assert st.page.locator(f"{CARD} .st-hc-dot").count() >= 1
        _shot(st, "clockhover-clock.png")
        st.page.clock.run_for(1000)                                 # 每秒随数据更新
        assert _lines(st)[0] in ("正在计时 · 数学 · " + _clock_text(st, start), "正在计时 · 数学 · " + _clock_text(st, start, 1))
        st.page.mouse.move(100, 800)
        assert st.page.locator(CARD).count() == 0


def test_hover_clock_card_stays_out_when_pressing_or_when_the_human_page_is_open(browser, base) -> None:  # noqa: F811  二十七·5 / 6
    with open_stage(browser, base, setup=_busy_day) as st:
        x, y = st.center("#stClock")
        st.page.mouse.move(x, y)
        st.page.clock.run_for(350)
        assert st.page.locator(CARD).count() == 1
        st.page.mouse.down()
        assert st.page.locator(CARD).count() == 0, "按下立刻收起"
        st.page.mouse.up()
        st.page.wait_for_selector(".cm-panel")
        st.page.clock.run_for(600)
        assert st.page.locator(CARD).count() == 0, "人的页面开着不出"
        st.page.mouse.move(100, 800)
        st.page.keyboard.press("Escape")
        assert st.page.locator(".cm-panel").count() == 0
        st.page.keyboard.press("Tab")                               # 键盘聚焦同样出现
        st.page.focus("#stClock")
        st.page.keyboard.press("Shift+Tab")
        st.page.keyboard.press("Tab")
        st.page.clock.run_for(100)
        assert st.page.locator(CARD).count() == 1
        st.page.keyboard.press("Tab")
        assert st.page.locator(CARD).count() == 0


def test_clock_card_without_timer_lists_focus_and_top_projects_by_time(browser, base) -> None:  # noqa: F811  二十七·6
    def setup(st: Stage) -> None:
        _busy_day(st)
        now = st.now().astimezone(TZ)
        st.lanes["human"]["sessions"].append({"startAt": _iso(now - timedelta(minutes=50)), "endAt": _iso(now - timedelta(minutes=45)), "durationSeconds": 300, "taskId": None,
                                              "projectId": "p_piano", "mode": "do", "source": "timer-backend"})
        st.current = {"running": False, "focus": {"state": "present", "app": "code", "title": "main.go", "since": _iso(now), "projectId": "p_math", "projectName": None,
                                                  "taskId": None, "taskName": None, "source": "rules"}}

    with open_stage(browser, base, setup=setup) as st:
        _hover(st, "#stClock")
        lines = _lines(st)
        assert lines[0] == "没在计时" and lines[2] == "数学 / code · main.go"
        assert lines[3].startswith("数学 ") and lines[4].startswith("钢琴 ") and len(lines) == 5
        st.page.mouse.move(100, 800)
        st.current["focus"]["state"] = "afk"
        st.poll()
        _hover(st, "#stClock")
        assert _lines(st)[2] == "离开"


def test_hover_ring_card_lists_today_workers_and_peak_and_skips_when_a_page_is_open(browser, base) -> None:  # noqa: F811  二十七·7
    with open_stage(browser, base, setup=_busy_day) as st:
        _timing(st, 125)
        _hover(st, "#stRing")
        lines = _lines(st)
        assert lines[0].startswith("今天：你 ") and " · 代理 " in lines[0]
        assert lines[1] == "现在 1 个 agent 在干活" and lines[2] == "garden · 数学"
        ring, card = st.box("#stRing"), st.box(CARD)
        assert card["x"] >= ring["x"] + ring["width"] or card["x"] + card["width"] <= ring["x"], "不盖住环"
        _shot(st, "clockhover-ring.png")
        st.page.mouse.move(100, 800)
        assert st.page.locator(CARD).count() == 0
        st.page.click("#stRing")
        st.page.clock.run_for(600)
        st.page.mouse.move(*st.center("#stRing"))
        st.page.clock.run_for(400)
        assert st.page.locator(CARD).count() == 0, "中心页开着不出"


def test_hover_ring_card_says_nobody_working_and_counts_waiting(browser, base) -> None:  # noqa: F811  二十七·7
    def setup(st: Stage) -> None:
        _busy_day(st)
        now = st.now().astimezone(TZ)
        st.lanes["agents"][0]["phases"] = [{"at": _iso(now - timedelta(seconds=30)), "phase": "waiting_input", "detail": None}]
        st.lanes["agents"][1]["phases"] = [{"at": _iso(now - timedelta(seconds=30)), "phase": "idle", "detail": None}]

    with open_stage(browser, base, setup=setup) as st:
        _hover(st, "#stRing")
        lines = _lines(st)
        assert "现在没有 agent 在干活" in lines and "1 个在等你" in lines


def test_nav_chip_is_driven_like_the_clock_set_every_second_click_opens_the_human_page_hover_shows_the_card(browser, base) -> None:  # noqa: F811  二十七·4
    def setup(st: Stage) -> None:
        _busy_day(st)
        st.page.add_init_script(CHIP_STUB)

    with open_stage(browser, base, setup=setup) as st:
        assert st.page.evaluate("() => window.__taken === true")
        _timing(st, 125)
        st.page.clock.run_for(1000)
        last = st.page.evaluate("() => window.__chipSets[window.__chipSets.length - 1]")
        assert last["big"] == st.page.locator("#stClock .st-clock-today").inner_text() and last["small"] == st.page.locator("#stClock .st-clock-proj").inner_text()
        assert last["arc"] > 3 and last["countdown"] is False
        _hover(st, "#navChip")
        lines = _lines(st)
        assert lines[0].startswith("正在计时 · 数学 · ") and lines[1].startswith("今天 ")
        card, chip = st.box(CARD), st.box("#navChip")
        assert card["y"] >= chip["y"] + chip["height"]
        st.page.mouse.down()
        assert st.page.locator(CARD).count() == 0
        st.page.mouse.up()
        st.page.wait_for_selector(".cm-panel")
        assert st.page.evaluate("() => location.hash") == "", "点胶囊不跳走"


def test_ring_arcs_follow_the_ticker_line_shown(browser, base) -> None:  # noqa: F811  二十七·B
    with open_stage(browser, base, setup=_setup_today) as st:
        seen: dict[str, list[dict[str, Any]]] = {}
        for _ in range(8):
            text = st.page.locator("#stRing .st-tk").text_content() or ""
            seen[text] = json.loads(st.page.get_attribute("#stRing", "data-arcs"))
            if text.startswith("你 ") and "\n代理" in text:
                st.page.clock.run_for(600)                          # 弧的过渡走完再截
                _shot(st, "clockhover-ring-totals.png")
            st.page.clock.run_for(4000)
        totals = next(v for k, v in seen.items() if k.startswith("你 ") and "\n代理" in k)
        assert totals[0]["color"] == "var(--pr-human)" and len(totals) >= 2 and abs(sum(a["share"] for a in totals) - 1) < 1e-6
        assert len({a["color"] for a in totals}) == len(totals) or len(totals) >= 2
        par = next((v for k, v in seen.items() if "个代理并行" in k), None)
        assert par is not None and all(abs(a["share"] - 1 / len(par)) < 1e-6 for a in par)


def _setup_today(st: Stage) -> None:
    _busy_day(st)
    now = st.now().astimezone(TZ)
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)

    def seg(a: float, b: float, pid: str | None) -> dict[str, Any]:
        return {"start": _iso(day + timedelta(hours=a)), "end": _iso(day + timedelta(hours=b)), "projectId": pid, "taskId": None}

    st.time_lanes = {"today": day.date().isoformat(), "now": _iso(now), "windowStart": _iso(day), "windowEnd": _iso(day + timedelta(days=1)),
                     "human": [seg(0.5, 1.5, "p_math")],
                     "agents": [{"agent": "claude-code", "tool": "claude-code", "name": "garden", "runId": "r1", "segments": [seg(0.6, 1.4, "p_math"), seg(3, 4, "p_studio")]},
                                {"agent": "codex", "tool": "codex", "name": "scratch", "runId": "r2", "segments": [seg(1, 2, None)]}]}
