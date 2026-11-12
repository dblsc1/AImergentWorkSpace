"""左上角 Agent 页：一个 agent 一张卡（hive.stage.v1「十三」）。夹具沿用 test_hive_stage.py。"""

from __future__ import annotations

from datetime import timedelta
import os
import re
from typing import Any

from test_hive_focus import base, browser  # noqa: F401  (fixtures)
from test_hive_stage import TZ, Stage, _busy_day, _iso, open_stage

SHOT = os.environ.get("AGENT_PAGE_SHOT")


def _agent(st: Stage, rid: str, key: str, name: str, segs: list[tuple[float, float, str | None]], *, tool: str = "claude-code", project: str | None = None,
           attend: list[tuple[float, float]] | None = None, running: bool = True, phase: str = "working") -> dict[str, Any]:
    now = st.now().astimezone(TZ)
    t = lambda h: _iso(now - timedelta(hours=h))  # noqa: E731  h = 距现在几小时
    return {"agent": tool, "tool": tool, "name": name, "runId": rid, "laneKey": key, "projectId": project, "running": running, "lost": False, "phase": phase,
            "segments": [{"start": t(a), "end": t(b), "projectId": p, "taskId": None} for a, b, p in segs],
            "attend": [{"start": t(a), "end": t(b)} for a, b in (attend or [])]}


def _setup(st: Stage) -> None:
    _busy_day(st)
    now = st.now().astimezone(TZ)
    st.time_lanes = {"today": now.date().isoformat(), "now": _iso(now), "windowStart": _iso(now - timedelta(hours=3)), "windowEnd": _iso(now), "human": [], "agents": [
        _agent(st, "r1", "cc\ngarden", "garden", [(2.6, 2.0, "p_math"), (0.9, 0.2, "p_studio")], project="p_studio", attend=[(2.5, 2.2), (0.8, 0.5)]),
        _agent(st, "r9", "cc\ngarden", "garden", [(1.5, 1.0, "p_math")], project="p_math", running=False),     # 同一个 agent 的另一条运行
        _agent(st, "r2", "codex\nscratch", "scratch", [(1.2, 0.3, None)], tool="codex", phase="waiting_input"),
        _agent(st, "r3", "gemini\nnote", "note", [(2.0, 1.6, "p_piano")], tool="gemini-cli", running=False)]}


def _open(st: Stage) -> None:
    st.page.click("#stFish")
    st.page.locator("#agPage .ag-card").first.wait_for()


def test_header_title_subtitle_and_one_card_per_agent(browser, base) -> None:  # noqa: F811
    with open_stage(browser, base, setup=_setup) as st:
        _open(st)
        page = st.page.locator("#agPage")
        assert page.locator(".ag-h b").inner_text() == "Agent"
        assert page.locator(".ag-h b").evaluate("e => getComputedStyle(e).fontWeight") in ("700", "bold")
        sub = page.locator(".ag-sub").inner_text()
        # 干活 = 0.6 + 0.7 + 0.5 + 0.9 + 0.4 小时 = 3 小时 6 分；r1 / r9 并成一张卡，所以 3 张卡；r9 与 r2 重叠，峰值 2
        assert sub == "agent 最近 3 小时工作了 3 小时 6 分钟，并行峰值 2，现在有 1 个正在工作", sub
        assert page.locator(".ag-card").count() == 3
        names = page.locator(".ag-card .ag-name").all_inner_texts()
        assert sorted(names) == ["garden", "note", "scratch"]
        assert names[0] == "scratch"                                   # 在等人排最前
        assert page.locator(".ag-card:has(.ag-name:text-is('garden')) .ag-seg").count() == 3
        assert page.locator(".ag-me, .cs-me").count() == 0              # 没有「人」的泳道 / 卡片


def test_segments_take_the_colour_of_their_own_project_and_the_card_the_current_one(browser, base) -> None:  # noqa: F811
    with open_stage(browser, base, setup=_setup) as st:
        _open(st)
        card = st.page.locator(".ag-card:has(.ag-name:text-is('garden'))")
        segs = card.locator(".ag-seg")
        bg = [segs.nth(i).evaluate("e => getComputedStyle(e).backgroundColor") for i in range(3)]
        titles = [segs.nth(i).get_attribute("title") for i in range(3)]
        assert [x.split(" ", 1)[1] for x in titles] == ["garden 数学", "garden 数学", "garden 录音棚"], titles
        assert bg[0] == bg[1] and bg[0] != bg[2]
        neutral = st.page.locator(".ag-card:has(.ag-name:text-is('scratch')) .ag-seg").first
        assert "ag-neutral" in neutral.get_attribute("class")
        assert card.evaluate("e => getComputedStyle(e).borderTopColor") == bg[2]       # 现在在 p_studio


def test_focus_strip_is_a_short_red_bar_on_top_of_the_lane_with_a_time_tooltip(browser, base) -> None:  # noqa: F811
    with open_stage(browser, base, setup=_setup) as st:
        _open(st)
        card = st.page.locator(".ag-card:has(.ag-name:text-is('garden'))")
        strips = card.locator(".ag-focus")
        assert strips.count() == 2
        now = st.now().astimezone(TZ)
        a, b = now - timedelta(hours=2.5), now - timedelta(hours=2.2)
        assert strips.first.get_attribute("title") == f"{a:%H:%M}–{b:%H:%M} 聚焦"
        lane, strip = card.locator(".ag-lane").bounding_box(), strips.first.bounding_box()
        assert lane and strip and strip["height"] <= lane["height"] / 3 and strip["height"] >= 2
        assert abs(strip["y"] - lane["y"]) < 1.5                                         # 贴在泳道上沿
        assert strips.first.evaluate("e => getComputedStyle(e).backgroundColor")!= "rgba(0, 0, 0, 0)"
        seg = card.locator(".ag-seg").first.bounding_box()
        assert seg and abs(seg["height"] - lane["height"]) < 1.5                         # agent 泳道占满整个高度
        assert st.page.locator(".ag-card:has(.ag-name:text-is('note')) .ag-focus").count() == 0


def test_now_line_runs_through_all_lanes_and_the_slider_only_reloads_on_release(browser, base) -> None:  # noqa: F811
    with open_stage(browser, base, setup=_setup) as st:
        _open(st)
        line, lane = st.box("#agNow"), st.box(".ag-card .ag-lane")
        last = st.page.locator(".ag-card").last.bounding_box()
        assert line["height"] >= last["y"] + last["height"] - st.box(".ag-card")["y"] - 2
        assert abs(line["x"] + line["width"] / 2 - (lane["x"] + lane["width"])) < 3
        assert re.fullmatch(r"\d\d:\d\d", st.page.locator("#agNow span").inner_text())
        n = len([u for u in st.requests if "since=" in u])
        slider = st.page.locator("#agHours")
        slider.evaluate("e => { e.value = '8'; e.dispatchEvent(new Event('input', { bubbles: true })); }")
        assert st.page.locator("#agHoursOut").inner_text() == "最近 8 小时"
        assert len([u for u in st.requests if "since=" in u]) == n                      # 拖动中不发请求
        slider.evaluate("e => e.dispatchEvent(new Event('change', { bubbles: true }))")
        st.page.wait_for_timeout(200)
        assert len([u for u in st.requests if "since=" in u]) == n + 1
        assert st.page.locator(".ag-sub").inner_text().startswith("agent 最近 8 小时工作了")
        st.page.get_by_role("button", name="周").click()
        st.page.wait_for_timeout(200)
        assert st.page.locator(".ag-sub").inner_text().startswith("agent 最近 7 天工作了")
        assert st.page.locator("#agRuler .ag-tick").count() == 7


def test_pets_fly_into_the_cards_and_back_to_the_hive_on_close(browser, base) -> None:  # noqa: F811
    with open_stage(browser, base, setup=_setup) as st:
        pet = st.page.locator('.st-pet[data-run-id="r1"]')
        assert pet.evaluate("e => getComputedStyle(e).visibility") == "visible"
        st.page.click("#stFish")
        st.page.locator(".ag-flyer").first.wait_for()
        st.page.locator(".ag-flyer").first.wait_for(state="detached")
        assert pet.evaluate("e => getComputedStyle(e).visibility") == "hidden"          # 小人在卡片里，外面的隐去
        st.page.keyboard.press("Escape")
        assert not st.page.locator("#agVeil").is_visible()
        st.page.wait_for_function("() => getComputedStyle(document.querySelector('.st-pet[data-run-id=\"r1\"]')).visibility === 'visible'")
        assert st.page.locator(".ag-flyer").count() == 0


def test_reduced_motion_places_the_pets_without_flying(browser, base) -> None:  # noqa: F811
    with open_stage(browser, base, setup=_setup) as st:
        st.page.emulate_media(reduced_motion="reduce")
        st.page.reload()
        st.page.wait_for_selector("#stRing .st-tk", state="attached")
        st.page.click("#stFish")
        st.page.locator(".ag-card").first.wait_for()
        assert st.page.locator(".ag-flyer").count() == 0


def test_fish_avatar_is_larger_and_uses_the_most_used_tool_pack(browser, base) -> None:  # noqa: F811
    def setup(st: Stage) -> None:
        _setup(st)
        for a in st.time_lanes["agents"]:
            a["tool"] = "codex"
    pack = {"gpt": {"moods": {"idle": ["i/a.webm"]}}}
    with open_stage(browser, base, setup=setup, pets=pack, hold_webm=True) as st:
        st.page.wait_for_selector("#stFish video")
        assert "/hive/pets/gpt/i/a.webm" in st.page.locator("#stFish video").get_attribute("src")
        assert st.box("#stFish")["width"] >= 70


def test_screenshot(browser, base) -> None:  # noqa: F811
    if not SHOT:
        return
    from PIL import Image, ImageDraw  # noqa: PLC0415
    import io  # noqa: PLC0415

    def face(color: str) -> bytes:
        im = Image.new("RGBA", (200, 220), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        d.ellipse((20, 10, 180, 150), fill=color)
        d.ellipse((60, 60, 85, 95), fill="white"); d.ellipse((115, 60, 140, 95), fill="white")
        d.ellipse((70, 70, 82, 88), fill="black"); d.ellipse((125, 70, 137, 88), fill="black")
        d.arc((70, 90, 130, 130), 20, 160, fill="black", width=5)
        d.rounded_rectangle((55, 150, 145, 215), 20, fill=color)
        out = io.BytesIO(); im.save(out, "PNG"); return out.getvalue()

    pets = {k: {"moods": {}, "image": "a.png"} for k in ("claude", "gpt", "gemini", "deepseek")}
    colors = {"claude": "#E08A5B", "gpt": "#4CB28A", "gemini": "#6F8CF0", "deepseek": "#5AA9E6"}

    def setup(st: Stage) -> None:
        _setup(st)
        st.page.route("**/hive/pets/*/a.png", lambda r: r.fulfill(status=200, content_type="image/png", body=face(colors[r.request.url.split("/hive/pets/")[1].split("/")[0]])))
    with open_stage(browser, base, setup=setup, pets=pets) as st:
        st.page.wait_for_timeout(600)
        st.page.click("#stFish")
        st.page.locator(".ag-card").first.wait_for()
        st.page.wait_for_timeout(900)
        st.page.screenshot(path=SHOT)
