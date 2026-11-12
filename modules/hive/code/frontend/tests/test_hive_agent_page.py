"""左上角 Agent 页：一个 agent 一张卡（hive.stage.v1「十三」；默认范围按「十七」5：7 天不等宽标尺）。夹具沿用 test_hive_stage.py。"""

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
        assert sub == "agent 最近 7 天工作了 3 小时 6 分钟，并行峰值 2，现在有 1 个正在工作", sub   # 十七·5：默认 = 7 天标尺
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
        assert [x.split(" ", 2)[2] for x in titles] == ["garden 数学", "garden 数学", "garden 录音棚"], titles   # 默认标尺跨 7 天，前面带 MM/DD
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
        assert strips.first.get_attribute("title") == f"{a:%m/%d} {a:%H:%M}–{b:%H:%M} 聚焦"   # 十七·3：超过 24 小时的范围前面带 MM/DD
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
        line, lane = st.box("#agNow"), st.box("#agPage .ag-card .ag-lane")
        last = st.page.locator("#agPage .ag-card").last.bounding_box()
        assert line["height"] >= last["y"] + last["height"] - st.box("#agPage .ag-card")["y"] - 2
        assert lane["x"] + lane["width"] * 0.7 < line["x"] + line["width"] / 2 < lane["x"] + lane["width"] - 3   # 十七·5：默认标尺里现在线在今天那一格里，不在最右
        assert re.fullmatch(r"\d\d:\d\d", st.page.locator("#agNow span").inner_text())
        st.page.get_by_role("button", name="日").click()
        st.page.wait_for_timeout(200)
        line, lane = st.box("#agNow"), st.box("#agPage .ag-card .ag-lane")
        assert abs(line["x"] + line["width"] / 2 - (lane["x"] + lane["width"])) < 3                    # 滚动窗口：现在线在最右
        st.page.get_by_role("button", name="日").click()                                               # 再点一次 = 回默认
        st.page.wait_for_timeout(200)
        n = len([u for u in st.requests if "since=" in u])
        slider = st.page.locator("#agHours")
        slider.evaluate("e => { e.value = '8'; e.dispatchEvent(new Event('input', { bubbles: true })); }")
        assert st.page.locator("#agHoursOut").inner_text() == "最近 8 小时"
        assert len([u for u in st.requests if "since=" in u]) == n                      # 拖动中不发请求
        slider.evaluate("e => e.dispatchEvent(new Event('change', { bubbles: true }))")
        st.page.wait_for_timeout(200)
        assert len([u for u in st.requests if "since=" in u]) == n + 1
        assert st.page.locator("#agSub").inner_text().startswith("agent 最近 8 小时工作了")
        st.page.get_by_role("button", name="周").click()
        st.page.wait_for_timeout(200)
        assert st.page.locator("#agSub").inner_text().startswith("agent 最近 7 天工作了")
        assert st.page.locator("#agPage [data-mode].on").inner_text() == "周"
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


def test_default_range_has_no_button_selected_and_uneven_day_ruler(browser, base) -> None:  # noqa: F811  十七·5
    with open_stage(browser, base, setup=_setup) as st:
        _open(st)
        assert st.page.locator("#agPage [data-mode].on").count() == 0 and "on" not in st.page.locator("#agPage .ag-hours").get_attribute("class")
        days = st.page.locator("#agRuler .ag-tick")
        assert days.count() == 7 and days.last.inner_text() == "今天"
        widths = [days.nth(i).bounding_box()["width"] for i in range(7)]
        assert widths[6] == max(widths) and widths[6] > widths[0] * 1.5
        st.page.get_by_role("button", name="周").click()
        st.page.wait_for_timeout(200)
        widths = [st.page.locator("#agRuler .ag-tick").nth(i).bounding_box()["width"] for i in range(7)]
        assert len({round(w) for w in widths}) == 1                                                    # 周：等宽
        st.page.get_by_role("button", name="周").click()
        st.page.wait_for_timeout(200)
        assert st.page.locator("#agPage [data-mode].on").count() == 0 and st.page.locator("#agRuler .ag-tick").last.inner_text() == "今天"


def test_running_working_agent_without_a_work_segment_in_the_window_still_counts(browser, base) -> None:  # noqa: F811  十七·4
    def setup(st: Stage) -> None:
        _setup(st)
        st.time_lanes["agents"] = [_agent(st, "x1", "cc\nlong", "long", [], phase="working"), _agent(st, "x2", "codex\nlong2", "long2", [], tool="codex", phase="working"),
                                   _agent(st, "x3", "cc\ngone", "gone", [], running=False)]
    with open_stage(browser, base, setup=setup) as st:
        _open(st)
        assert st.page.locator("#agSub").inner_text().endswith("现在有 2 个正在工作")
        assert st.page.locator(".ag-card").count() == 3


# ── 卡片详情（hive.stage.v1「二十」）────────────────────────────────
def _detail_setup(st: Stage) -> None:
    _setup(st)
    now = st.now().astimezone(TZ)
    for r in st.time_lanes["agents"]:
        if r["runId"] == "r1":
            r.update(startedAt=_iso(now - timedelta(hours=2.7)), endedAt=None, taskId=None, beatSource="companion", unverified=False)
        if r["runId"] == "r9":
            r.update(startedAt=_iso(now - timedelta(hours=1.6)), endedAt=_iso(now - timedelta(hours=0.9)), taskId="t_p_math", outcome="done", projectId="p_math")


def _open_detail(st: Stage, name: str = "garden") -> Any:
    _open(st)
    st.page.locator(f".ag-card:has(.ag-name:text-is('{name}'))").click()
    d = st.page.locator("#agPage .ag-detail")
    d.locator(".ag-run").first.wait_for()
    return d


def test_click_a_card_opens_a_detail_with_one_small_card_per_run_newest_first(browser, base) -> None:  # noqa: F811
    with open_stage(browser, base, setup=_detail_setup) as st:
        d = _open_detail(st)
        assert d.locator(".ag-dt-title").inner_text() == "garden"
        assert d.locator(".ag-big-slot").count() == 1 and d.locator(".ag-big-slot .st-art").count() == 1   # 大立绘（没有素材时是占位形象）
        runs = d.locator(".ag-run")
        assert runs.count() == 2
        assert runs.nth(0).get_attribute("data-run-id") == "r9" and runs.nth(1).get_attribute("data-run-id") == "r1"   # r9 开始得晚（1.6 小时前）
        assert runs.nth(1).locator(".ag-run-st").inner_text() == "正在工作" and runs.nth(0).locator(".ag-run-st").inner_text() == "已结束"
        now = st.now().astimezone(TZ)
        a = now - timedelta(hours=2.7)
        assert runs.nth(1).locator(".ag-run-time").inner_text() == f"{a:%m/%d} {a:%H:%M} – 现在"
        assert runs.nth(1).locator(".ag-run-dur").inner_text() == "2 小时 42 分钟"
        assert runs.nth(1).locator(".ag-run-v").inner_text() == "agent 钩子上报 · 伴随进程心跳 · 窗口识别"   # r1 有 attend 段
        assert runs.nth(0).locator(".ag-run-v").inner_text() == "agent 钩子上报"
        assert runs.nth(1).locator(".ag-sel-project").input_value() == "p_studio" and runs.nth(1).locator(".ag-sel-task").input_value() == ""
        assert runs.nth(0).locator(".ag-sel-project").input_value() == "p_math" and runs.nth(0).locator(".ag-sel-task").input_value() == "t_p_math"
        assert runs.nth(0).locator(".ag-sel-task option").all_inner_texts() == ["不挂任务", "数学的任务"]
        assert st.posts == []
        # 详情盖住别的卡，同时只开一张；缩回后点另一张，详情里是它的运行
        st.page.keyboard.press("Escape")
        d.wait_for(state="hidden")
        st.page.locator(".ag-card:has(.ag-name:text-is('scratch'))").click()
        d.locator(".ag-run-st").wait_for()
        assert d.locator(".ag-dt-title").inner_text() == "scratch" and d.locator(".ag-run").count() == 1
        assert d.locator(".ag-run-st").inner_text() == "等输入"


def test_detail_closes_by_title_x_and_escape_and_escape_leaves_the_page_open(browser, base) -> None:  # noqa: F811
    with open_stage(browser, base, setup=_detail_setup) as st:
        d = _open_detail(st)
        st.page.keyboard.press("Escape")
        d.wait_for(state="hidden"); assert st.page.locator("#agPage").is_visible()       # Esc 只关详情
        st.page.locator(".ag-card:has(.ag-name:text-is('garden'))").click()
        assert d.is_visible()
        d.locator(".ag-dt-title").click()
        d.wait_for(state="hidden")
        st.page.locator(".ag-card:has(.ag-name:text-is('garden'))").click()
        d.locator(".ag-dt-x").click()
        d.wait_for(state="hidden"); assert st.page.locator("#agPage").is_visible()
        st.page.locator(".ag-card:has(.ag-name:text-is('garden'))").click()
        st.page.keyboard.press("Escape"); st.page.keyboard.press("Escape")
        assert not st.page.locator("#agPage").is_visible()


def test_change_project_then_task_posts_place_and_failure_puts_the_dropdown_back(browser, base) -> None:  # noqa: F811
    with open_stage(browser, base, setup=_detail_setup) as st:
        d = _open_detail(st)
        run = d.locator('.ag-run[data-run-id="r1"]')
        st.replies["agents/r1/place"] = (200, {"runId": "r1", "taskId": None, "projectId": "p_math", "rule": None})
        r1 = next(r for r in st.time_lanes["agents"] if r["runId"] == "r1")
        r1["projectId"] = "p_math"                                      # 桩 = 服务端：改挂成功后重读读到新归属
        run.locator(".ag-sel-project").select_option("p_math")
        st.settle()
        assert st.posts == [("agents/r1/place", {"projectId": "p_math", "remember": False})]
        assert run.locator(".ag-sel-task option").all_inner_texts() == ["不挂任务", "数学的任务"]    # 任务随项目变
        st.posts.clear()
        r1["taskId"] = "t_p_math"
        run.locator(".ag-sel-task").select_option("t_p_math")
        st.settle()
        assert st.posts == [("agents/r1/place", {"taskId": "t_p_math", "remember": False})]
        st.posts.clear()
        r1["taskId"] = None
        run.locator(".ag-sel-task").select_option("")                  # 不挂任务 = 只记到项目
        st.settle()
        assert st.posts == [("agents/r1/place", {"projectId": "p_math", "remember": False})]
        # 失败：下拉改回去，页面底部报错
        st.replies["agents/r1/place"] = (404, {"detail": "x"})
        before = run.locator(".ag-sel-project").input_value()
        run.locator(".ag-sel-project").select_option("p_piano")
        st.settle()
        assert "目标不存在" in st.toast()
        assert run.locator(".ag-sel-project").input_value() == before
        # 已结束的运行也有下拉
        assert d.locator('.ag-run[data-run-id="r9"] .ag-sel-project').count() == 1


def test_periodic_reload_keeps_the_focused_dropdown_and_the_scroll_position(browser, base) -> None:  # noqa: F811
    with open_stage(browser, base, setup=_detail_setup) as st:
        d = _open_detail(st)
        sel = d.locator('.ag-run[data-run-id="r1"] .ag-sel-project')
        sel.focus()
        st.page.clock.run_for(15000)
        st.page.wait_for_timeout(300)
        assert sel.evaluate("e => e === document.activeElement") and sel.input_value() == "p_studio"
        d.locator(".ag-dt-title").focus()                               # 失去焦点后补画
        st.page.wait_for_timeout(100)
        assert d.locator(".ag-run").count() == 2


def test_reduced_motion_detail_opens_without_animation(browser, base) -> None:  # noqa: F811
    with open_stage(browser, base, setup=_detail_setup) as st:
        st.page.emulate_media(reduced_motion="reduce")
        d = _open_detail(st)
        assert d.evaluate("e => e.getAnimations().length") == 0
        st.page.keyboard.press("Escape")
        assert not d.is_visible()


def test_runs_not_retained_fold_into_a_rest_row_that_toggles_and_the_header_counts_stay(browser, base) -> None:  # noqa: F811  二十·5
    def setup(st: Stage) -> None:
        _setup(st)
        for r in st.time_lanes["agents"]:
            r["retained"] = r["runId"] in ("r1", "r9", "r2")        # gemini 的 note 不留存
    with open_stage(browser, base, setup=setup) as st:
        _open(st)
        page = st.page.locator("#agPage")
        assert sorted(page.locator(".ag-card .ag-name").all_inner_texts()) == ["garden", "scratch"]
        more = page.locator(".ag-more")
        assert more.inner_text() == "其余 1 个"
        assert page.locator(".ag-sub").inner_text() == "agent 最近 7 天工作了 3 小时 6 分钟，并行峰值 2，现在有 1 个正在工作"   # 页头仍算全部
        more.click()
        assert sorted(page.locator(".ag-card .ag-name").all_inner_texts()) == ["garden", "note", "scratch"]
        assert more.inner_text() == "收起其余 1 个" and page.locator(".ag-stack > :last-child").get_attribute("class") == "ag-more"
        st.page.clock.run_for(15000); st.page.wait_for_timeout(300)
        assert page.locator(".ag-card").count() == 3                      # 重读后展开状态保持
        more.click()
        assert page.locator(".ag-card").count() == 2 and more.inner_text() == "其余 1 个"


def test_all_retained_has_no_rest_row(browser, base) -> None:  # noqa: F811  二十·5
    with open_stage(browser, base, setup=_setup) as st:
        _open(st)
        assert st.page.locator("#agPage .ag-more").is_hidden()
