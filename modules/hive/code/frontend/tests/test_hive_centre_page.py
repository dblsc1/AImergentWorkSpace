"""点中心环打开的「人 + agent 混合泳道」页（hive.stage.v1「十七」）。夹具沿用 test_hive_stage / test_hive_human_page / test_hive_agent_page。"""

from __future__ import annotations

import os
import re

from test_hive_agent_page import _agent, _setup as _agents
from test_hive_focus import base, browser  # noqa: F401  (fixtures)
from test_hive_human_page import _day
from test_hive_stage import Stage, open_stage

SHOT = os.environ.get("CENTRE_PAGE_SHOT")


def _setup(st: Stage) -> None:
    _agents(st)     # 3 个 agent（garden 两条运行、scratch、note），带红条；蜂巢上的小人 r1 / r2
    pets, seen = st.lanes["agents"], st.lanes["interactions"]
    _day(st)        # 人：手动计时 + 正在跑的计时 + 窗口段（归类的 / 没归类的 / 标题已隐去的）；它会重置 lanes，把小人放回去
    st.lanes["agents"], st.lanes["interactions"] = pets, seen


def _open(st: Stage) -> None:
    st.page.click("#stRing")
    st.page.locator("#cpPage .ag-card:not(.ag-me)").first.wait_for()
    st.page.wait_for_function("() => document.querySelectorAll('#cpPage .ag-me .ag-seg').length >= 1")
    st.page.wait_for_timeout(100)


def test_ring_opens_the_mixed_page_human_lane_first_then_the_agent_cards(browser, base) -> None:  # noqa: F811  十七·1、2、3
    with open_stage(browser, base, width=1600, setup=_setup) as st:
        _open(st)
        page = st.page.locator("#cpPage")
        assert page.locator(".ag-h b").inner_text() == "你 + 代理"
        assert st.page.locator("#csVeil, #csPage, .st-fly").count() == 0 and not st.page.locator("#agVeil").is_visible()
        names = page.locator(".ag-card .ag-name").all_inner_texts()
        assert names[0] == "你" and sorted(names[1:]) == ["garden", "note", "scratch"] and names[1] == "scratch"
        assert "ag-me" in page.locator(".ag-card").first.get_attribute("class")
        assert page.locator(".ag-me .ag-dur").inner_text() == "2 小时 0 分钟"        # 30 + 20 + 正在跑 10 + 三天前 60（默认范围 7 天）
        sub = page.locator("#cpSub").inner_text()
        assert sub.startswith("你 2 小时 · 代理 3.1 小时 · 你 1 小时，带出代理") and sub.endswith("代理并行峰值 2 · 现在有 1 个正在工作"), sub
        # 同一套 agent 卡片：红色聚焦条、按项目上色的段、各卡泳道与人的泳道左右对齐
        garden = page.locator(".ag-card:has(.ag-name:text-is('garden'))")
        assert garden.locator(".ag-focus").count() == 2 and garden.locator(".ag-seg").count() == 3
        me, ag = page.locator(".ag-me .ag-lane").bounding_box(), garden.locator(".ag-lane").bounding_box()
        assert me and ag and abs(me["x"] - ag["x"]) < 1.5 and abs(me["width"] - ag["width"]) < 1.5
        st.page.keyboard.press("Escape")
        assert not st.page.locator("#cpVeil").is_visible()


def test_human_lane_has_focus_segments_inside_and_the_timer_strip_on_the_top_edge(browser, base) -> None:  # noqa: F811  十七·3
    with open_stage(browser, base, width=1600, setup=_setup) as st:
        _open(st)
        lane = st.page.locator("#cpPage .ag-me .ag-lane")
        box = lane.bounding_box()
        timers = lane.locator(".ag-timer")
        assert timers.count() == 3                                                    # 三天前、刚才那段手动计时 + 正在跑的这一段
        for i in range(3):
            b = timers.nth(i).bounding_box()
            assert b and abs(b["y"] - box["y"]) < 1.5 and 2 <= b["height"] <= box["height"] / 3        # 贴在泳道上沿
        assert timers.first.evaluate("e => getComputedStyle(e).backgroundColor") != "rgba(0, 0, 0, 0)"
        titles = timers.evaluate_all("els => els.map(e => e.title)")
        assert all(re.fullmatch(r"\d\d/\d\d \d\d:\d\d–\d\d:\d\d 手动计时( / .+)?", t) for t in titles), titles
        assert any(t.endswith("手动计时 / 录音棚") for t in titles)                    # 正在跑的计时记在 p_studio
        segs = lane.locator(".ag-seg")
        wins = segs.evaluate_all("els => els.map(e => ({t: e.title, lite: e.classList.contains('ag-lite'), y: e.getBoundingClientRect().y}))")
        filed = [w for w in wins if "聚焦在 数学 / code · main.go" in w["t"]]
        assert filed and not filed[0]["lite"]                                         # 归了类的用项目色
        hidden = [w for w in wins if "chrome · 标题已隐去" in w["t"]]
        assert hidden and hidden[0]["lite"]                                           # 没归类的中性浅色；代号标题写「标题已隐去」
        assert all(w["y"] >= box["y"] + 6 for w in wins)                              # 窗口段在计时条下面的泳道里


def test_now_line_crosses_the_human_lane_and_every_agent_card_inside_the_today_cell(browser, base) -> None:  # noqa: F811  十七·3、5
    with open_stage(browser, base, width=1600, setup=_setup) as st:
        _open(st)
        line, me, last = st.box("#cpNow"), st.box("#cpPage .ag-me"), st.page.locator("#cpPage .ag-card").last.bounding_box()
        assert line["y"] <= me["y"] + 1 and line["y"] + line["height"] >= last["y"] + last["height"] - 2
        lane = st.box("#cpPage .ag-me .ag-lane")
        assert lane["x"] + lane["width"] * 0.61 < line["x"] + line["width"] / 2 < lane["x"] + lane["width"] - 3   # 今天那一格从 61.6% 起
        assert re.fullmatch(r"\d\d:\d\d", st.page.locator("#cpNow span").inner_text())


def test_default_ruler_is_uneven_and_the_three_range_controls_start_unselected(browser, base) -> None:  # noqa: F811  十七·5
    with open_stage(browser, base, width=1600, setup=_setup) as st:
        _open(st)
        page = st.page.locator("#cpPage")
        assert page.locator("[data-mode].on").count() == 0 and "on" not in page.locator(".ag-hours").get_attribute("class")
        days = page.locator("#cpRuler .ag-tick")
        assert days.count() == 7 and days.last.inner_text() == "今天"
        widths = [days.nth(i).bounding_box()["width"] for i in range(7)]
        assert widths[6] == max(widths) and widths[6] > widths[0] * 1.5               # 今天那一格最宽
        page.get_by_role("button", name="日").click()
        st.page.wait_for_timeout(300)
        assert page.locator("[data-mode].on").inner_text() == "日"
        xs = [page.locator("#cpRuler .ag-tick").nth(i).bounding_box()["x"] for i in range(page.locator("#cpRuler .ag-tick").count())]
        gaps = {round(b - a) for a, b in zip(xs, xs[1:])}
        assert len(xs) >= 5 and len(gaps) == 1                                        # 日：等宽的钟点刻度
        line, lane = st.box("#cpNow"), st.box("#cpPage .ag-me .ag-lane")
        assert abs(line["x"] + line["width"] / 2 - (lane["x"] + lane["width"])) < 3   # 滚动窗口里现在线在最右
        page.get_by_role("button", name="日").click()                                 # 再点一次 = 回默认
        st.page.wait_for_timeout(300)
        assert page.locator("[data-mode].on").count() == 0 and page.locator("#cpRuler .ag-tick").last.inner_text() == "今天"


def test_subtitle_counts_running_working_agents_even_without_a_segment_in_range(browser, base) -> None:  # noqa: F811  十七·4
    def setup(st: Stage) -> None:
        _setup(st)
        st.time_lanes["agents"] = [_agent(st, "x1", "cc\nlong", "long", [], phase="working"), _agent(st, "x2", "codex\nlong2", "long2", [], tool="codex", phase="working"),
                                   _agent(st, "x3", "cc\nidle", "idle", [], phase="idle"), _agent(st, "x4", "cc\nold", "old", [], running=False)]
    with open_stage(browser, base, width=1600, setup=setup) as st:
        st.page.click("#stRing")
        st.page.locator("#cpPage .ag-card:not(.ag-me)").first.wait_for()
        assert st.page.locator("#cpSub").inner_text().endswith("现在有 2 个正在工作")
        assert st.page.locator("#cpPage .ag-card:not(.ag-me)").count() == 4


def test_pets_fly_into_the_cards_and_back(browser, base) -> None:  # noqa: F811  十七·2（复用「十三」7）
    with open_stage(browser, base, width=1600, setup=_setup) as st:
        pet = st.page.locator('.st-pet[data-run-id="r1"]')
        st.page.click("#stRing")
        st.page.locator(".ag-flyer").first.wait_for()
        st.page.locator(".ag-flyer").first.wait_for(state="detached")
        assert pet.evaluate("e => getComputedStyle(e).visibility") == "hidden"
        st.page.keyboard.press("Escape")
        st.page.wait_for_function("() => getComputedStyle(document.querySelector('.st-pet[data-run-id=\"r1\"]')).visibility === 'visible'")


def test_screenshot(browser, base) -> None:  # noqa: F811
    if not SHOT:
        return
    with open_stage(browser, base, width=1600, setup=_setup) as st:
        st.page.wait_for_timeout(600)
        _open(st)
        st.page.wait_for_timeout(900)
        st.page.screenshot(path=SHOT)
