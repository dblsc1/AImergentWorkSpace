"""中心环（人 + 代理总计时）、左上角大肥鱼头像（hive.stage.v1「八」；点环的页面见「十七」）。夹具沿用 test_hive_stage.py。"""

from __future__ import annotations

from datetime import timedelta
import json
import re
from typing import Any

import pytest

from test_hive_focus import base, browser  # noqa: F401  (fixtures)
from test_hive_stage import TZ, Stage, _busy_day, _focus, _iso, open_stage


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
            st.page.clock.run_for(8000)
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


def test_ring_click_opens_the_mixed_page_without_flying_clock_and_fish(browser, base) -> None:  # noqa: F811  新契约：「十七」（取代「八」的飞入统计页；细节见 test_hive_centre_page.py）
    with open_stage(browser, base, setup=_setup) as st:
        st.page.click("#stRing")
        page = st.page.locator("#cpPage")
        page.locator(".ag-me").wait_for()
        assert st.page.locator(".st-fly").count() == 0 and st.page.locator("#csVeil").count() == 0
        assert st.page.locator("#stClock").is_visible() and st.page.locator("#stFish").is_visible()
        assert page.locator(".ag-name").all_inner_texts() == ["你", "scratch", "garden"] or sorted(page.locator(".ag-name").all_inner_texts()) == ["garden", "scratch", "你"]
        st.page.locator("#cpPage .ag-x").click()
        assert not st.page.locator("#cpVeil").is_visible()


def test_ring_click_with_reduced_motion_opens_the_mixed_page(browser, base) -> None:  # noqa: F811
    with open_stage(browser, base, setup=_setup) as st:
        st.page.emulate_media(reduced_motion="reduce")
        st.page.reload()
        st.page.wait_for_selector("#stRing .st-tk", state="attached")
        st.page.click("#stRing")
        st.page.locator("#cpPage .ag-me").wait_for()
        assert st.page.locator(".st-fly, .ag-flyer").count() == 0


@pytest.mark.parametrize("width,height", [(390, 844), (768, 600), (1280, 800)])
def test_every_ticker_line_stays_inside_the_centre_cell_and_looks_like_a_project_name(browser, base, width, height) -> None:  # noqa: F811  三十三
    def long_names(st: Stage) -> None:
        _setup(st)
        st.lanes["agents"][1]["label"] = "一个特别特别长的代理名字ecsagent-cfo-sandbox-builder"
        now = st.now().astimezone(TZ)
        st.lanes["interactions"].append({"runId": "r2", "kind": "attend", "at": _iso(now), "until": _iso(now + timedelta(seconds=300))})   # 轮播 8 秒一句，整圈要近一分钟：人看着 r2 要覆盖整圈
        _focus(st, "p_math")

    with open_stage(browser, base, width=width, height=height, setup=long_names) as st:
        lines = json.loads(st.page.locator("#stRing").get_attribute("data-lines"))
        assert len(lines) >= 5
        box = st.page.evaluate("""() => {
            const r = e => { const b = e.getBoundingClientRect(); return [b.left, b.top, b.right, b.bottom]; };
            return { ring: r(document.querySelector('#stRing')), cell: r(document.querySelector('.hex-cell.hex-center')) };
        }""")
        seen = set()
        for _ in range(len(lines) + 2):   # 多转两格：起点不一定对齐 8 秒
            st.page.clock.run_for(8000)
            glyphs = st.page.evaluate("""() => {
                const tk = document.querySelector('#stRing .st-tk'), rg = document.createRange(); rg.selectNodeContents(tk);
                const b = rg.getBoundingClientRect(), t = tk.getBoundingClientRect(), cs = getComputedStyle(tk);
                return { content: tk.textContent, text: [b.left, b.top, b.right, b.bottom], box: [t.left, t.top, t.right, t.bottom], weight: cs.fontWeight, color: cs.color,
                         title: (() => { const h = document.querySelector('.hex-cell[data-project-id] .hex-title'); const c = getComputedStyle(h); return { weight: c.fontWeight, color: c.color }; })() };
            }""")
            seen.add(re.sub(r"\d+m", "Nm", glyphs["content"]))
            for name in ("box",) if "特别" in glyphs["content"] else ("text", "box"):   # 超长名字被省略号裁掉的部分不算：只量元素外框
                g = glyphs[name]
                for outer in (box["ring"], box["cell"]):
                    assert g[0] >= outer[0] - 0.5 and g[1] >= outer[1] - 0.5 and g[2] <= outer[2] + 0.5 and g[3] <= outer[3] + 0.5, (name, g, outer, seen)
            ring, b = box["ring"], glyphs["box"]   # 外框的四个角都在环的内圈（环宽 140 里 r = 80 / 100 处，即色弧的内沿）之内
            cx, cy, r = (ring[0] + ring[2]) / 2, (ring[1] + ring[3]) / 2, (ring[2] - ring[0]) / 2 * 0.8
            assert all((x - cx) ** 2 + (y - cy) ** 2 <= (r + 0.5) ** 2 for x in (b[0], b[2]) for y in (b[1], b[3])), (b, ring)
            assert glyphs["weight"] == glyphs["title"]["weight"] and glyphs["color"] == glyphs["title"]["color"]
        assert seen == {re.sub(r"\d+m", "Nm", x) for x in lines}
