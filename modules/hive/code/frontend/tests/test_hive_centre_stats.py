"""中心环（人 + 代理总计时）、左上角大肥鱼头像、统计页（hive.stage.v1「八」）。夹具沿用 test_hive_stage.py。"""

from __future__ import annotations

from datetime import timedelta
import json
import re
from typing import Any

from test_hive_focus import base, browser  # noqa: F401  (fixtures)
from test_hive_stage import TZ, Stage, _busy_day, _iso, open_stage


def _lanes(st: Stage) -> None:
    now = st.now().astimezone(TZ)
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)

    def seg(a: float, b: float, pid: str | None) -> dict[str, Any]:
        return {"start": _iso(day + timedelta(hours=a)), "end": _iso(day + timedelta(hours=b)), "projectId": pid, "taskId": None}

    st.time_lanes = {"today": day.date().isoformat(), "now": _iso(now), "windowStart": _iso(day), "windowEnd": _iso(day + timedelta(days=1)),
                     "human": [seg(0.5, 1.5, "p_math"), seg(2, 2.5, "p_piano")],
                     "agents": [{"agent": "claude-code", "tool": "claude-code", "name": "garden", "runId": "r1", "segments": [seg(0.6, 1.4, "p_math"), seg(3, 4, "p_studio")]},
                                {"agent": "codex", "tool": "codex", "name": "scratch", "runId": "r2", "segments": [seg(1, 2, None)]}]}


def _setup(st: Stage) -> None:
    _busy_day(st)
    _lanes(st)


def test_ring_ticker_rotates_every_four_seconds_and_keeps_totals_line(browser, base) -> None:  # noqa: F811
    with open_stage(browser, base, setup=_setup) as st:
        lines = json.loads(st.page.locator("#stRing").get_attribute("data-lines"))
        assert len(lines) >= 2 and any(x.startswith("你 ") and "\n代理 " in x for x in lines)
        seen = set()
        for _ in range(len(lines) + 1):
            seen.add(st.page.locator("#stRing .st-tk").text_content())
            st.page.clock.run_for(4000)
        assert len(seen) >= 2
        rx, ry = st.center("#stRing")
        cx, cy = st.center(".hex-cell.hex-center")
        assert abs(rx - cx) < 3 and abs(ry - cy) < 3


def test_fish_avatar_sits_above_the_loose_agent_slots(browser, base) -> None:  # noqa: F811
    pack = {"claude": {"moods": {"idle": ["i/a.webm"], "working": ["w/a.webm"]}}}   # 新契约「十三」8：最近 7 天干活最久的 tool（夹具里是 claude-code）对应的包
    with open_stage(browser, base, setup=_setup, pets=pack, hold_webm=True) as st:
        st.page.wait_for_selector("#stFish video")
        assert st.page.locator("#stFish video").get_attribute("src").endswith("/hive/pets/claude/i/a.webm#t=0.1")
        fish = st.box("#stFish")
        shell = st.box(".board-shell")
        assert fish["x"] - shell["x"] < 120 and fish["y"] - shell["y"] < 130 and abs(fish["width"] - fish["height"]) < 1 and fish["width"] >= 70
        for pet in st.page.locator(".st-pet.is-loose .st-pet-in").all():
            b = pet.bounding_box()
            assert b and (b["y"] >= fish["y"] + fish["height"] or b["x"] >= fish["x"] + fish["width"]), (b, fish)


def test_fish_without_a_pack_falls_back_to_the_placeholder(browser, base) -> None:  # noqa: F811
    with open_stage(browser, base, setup=_setup) as st:
        assert st.page.locator("#stFish svg").count() == 1 and st.page.locator("#stFish video").count() == 0


def test_fish_opens_the_agent_page(browser, base) -> None:  # noqa: F811  新契约：hive.stage.v1「十三」（卡片化的 Agent 页，细节见 test_hive_agent_page.py）
    with open_stage(browser, base, setup=_setup) as st:
        st.page.click("#stFish")
        page = st.page.locator("#agPage")
        page.locator(".ag-card").first.wait_for()
        assert page.locator(".ag-h").inner_text() == "Agent" and page.locator(".ag-card .ag-name").count() == 2
        assert st.page.locator("#csVeil").is_hidden()
        st.page.keyboard.press("Escape")
        assert not st.page.locator("#agVeil").is_visible()


def test_ring_click_flies_clock_and_fish_then_opens_the_mixed_page(browser, base) -> None:  # noqa: F811
    with open_stage(browser, base, setup=_setup) as st:
        st.page.click("#stRing")
        assert st.page.locator(".st-fly").count() == 2                    # 时钟与头像的副本；原件不动
        assert st.page.locator("#stClock").is_visible() and st.page.locator("#stFish").is_visible()
        page = st.page.locator("#csPage")
        page.locator(".cs-me").wait_for()
        assert st.page.locator(".st-fly").count() == 0
        sum_text = page.locator(".cs-sum").inner_text()
        assert sum_text.startswith("你 1.5 小时 · 代理 2.8 小时") and "你 1 小时，带出代理 1.9 小时的活" in sum_text and sum_text.endswith("代理并行峰值 2")
        assert page.locator(".cs-who").all_inner_texts() == ["", "你", "garden", "scratch"]
        human = page.locator(".cs-me .cs-seg").first
        assert human.get_attribute("title") == "00:30–01:30 你 数学"
        agent = page.locator(".cs-row:not(.cs-me) .cs-seg").first
        assert agent.get_attribute("title") == "00:36–01:24 garden 数学"
        assert agent.evaluate("e => getComputedStyle(e).backgroundColor") != human.evaluate("e => getComputedStyle(e).backgroundColor")
        # 共用一条时间轴：人 00:30 与 garden 00:36 的左边只差 6 分钟的宽度
        lane = page.locator(".cs-me .cs-lane").bounding_box()
        assert lane
        dx = agent.bounding_box()["x"] - human.bounding_box()["x"]
        assert abs(dx / lane["width"] - 6 / 1440) < 0.003
        # 本周：重读，按周几排 7 格
        with st.page.expect_request(re.compile(r"views/time-lanes\?from=\d{4}-\d\d-\d\d&to=")):
            page.get_by_role("button", name="本周").click()
        assert page.locator(".cs-day").count() == 7
        st.page.locator("#csPage .cs-x").click()
        assert not st.page.locator("#csVeil").is_visible()


def test_reduced_motion_skips_the_flight(browser, base) -> None:  # noqa: F811
    with open_stage(browser, base, setup=_setup) as st:
        st.page.emulate_media(reduced_motion="reduce")
        st.page.reload()
        st.page.wait_for_selector("#stRing .st-tk", state="attached")
        st.page.click("#stRing")
        st.page.locator(".cs-me").wait_for()
        assert st.page.locator(".st-fly").count() == 0
