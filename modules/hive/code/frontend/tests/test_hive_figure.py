"""形象挑选（hive.stage.v1「十一」「二十六」「三十二」）：人自己的形象；点击 AI 小人 = 看它的详情，在详情里点大立绘改它的形象。"""
from __future__ import annotations

import json

from test_hive_stage import PET, TOUCH_PACK, _one_worker, base, browser, open_stage  # noqa: F401

KIMI = {"kind": "kimi", "name": "Kimi", "moods": {"working": ["kw.webm"], "waiting": ["kwait.webm"], "idle": ["ki.webm"]}, "license": "x", "credits": "x", "source": "x"}
INDEX = {"packs": [{"kind": "claude", "name": "Claude", "cover": "i.webm"}, {"kind": "kimi", "name": "Kimi", "cover": "ki.webm"},
                   {"kind": "qwen", "name": "千问娘", "image": "q.png"}]}
SETTINGS = {"version": 3, "tiers": True, "agentDirect": {"enabled": False, "windowsClassify": False, "honeycombRecords": False},
            "pet": {"packsByTool": {"claude-code": "claude", "*": "deepseek"}}}


def _setup(st) -> None:
    _one_worker(st)
    st.settings = json.loads(json.dumps(SETTINGS))
    st.page.route("**/hive/pets/index.json", lambda r: r.fulfill(status=200, content_type="application/json", body=json.dumps(INDEX)))


def _puts(st) -> list:
    return [b for k, b in st.posts if k == "activity/settings"]


def test_the_human_is_drawn_as_their_pack_with_a_sweat_drop(browser, base) -> None:  # noqa: F811
    with open_stage(browser, base, setup=_setup, pets={"claude": TOUCH_PACK, "kimi": KIMI}, hold_webm=True) as st:
        st.page.clock.run_for(600)
        assert st.page.locator("#stEye .st-eye-drop").count() == 1
        assert st.page.locator("#stEye video.st-art").get_attribute("src").endswith("/hive/pets/kimi/ki.webm")   # 没有心跳 = 离开：idle 的动作


def test_human_page_figure_opens_my_figure_picker_and_picking_saves_it(browser, base) -> None:  # noqa: F811
    with open_stage(browser, base, setup=_setup, pets={"claude": TOUCH_PACK, "kimi": KIMI}, hold_webm=True) as st:
        st.page.mouse.click(*st.center("#stClock"))
        st.page.locator(".hp-figure").click()          # 十四·2：左边的立绘，点它 = 我的形象
        st.page.wait_for_selector(".fp-tile")
        assert st.page.locator(".fp-panel h3").inner_text() == "我的形象" and st.page.locator(".cm-panel").count() == 0
        assert st.page.locator(".fp-tile").evaluate_all("els => els.map(e => e.dataset.kind)") == ["claude", "kimi", "qwen"]
        assert st.page.locator('.fp-tile[aria-pressed="true"]').get_attribute("data-kind") == "kimi"     # 没选过：缺省
        st.replies["activity/settings"] = (200, {**SETTINGS, "version": 4, "pet": {**SETTINGS["pet"], "humanPack": "claude"}})
        with st.page.expect_response(lambda r: r.request.method == "PUT" and r.url.endswith("activity/settings")) as put:
            st.page.locator('.fp-tile[data-kind="claude"]').click()
        assert put.value.request.headers["if-match"] == '"3"'
        assert _puts(st) == [{"tiers": True, "agentDirect": SETTINGS["agentDirect"], "pet": {"packsByTool": SETTINGS["pet"]["packsByTool"], "humanPack": "claude"}}]
        st.settle()
        assert st.page.locator(".fp-panel").count() == 0
        assert "/hive/pets/claude/" in st.page.locator("#stEye video.st-art").get_attribute("src")


def test_click_an_ai_opens_its_detail_and_the_big_figure_picks_its_figure_by_tool(browser, base) -> None:  # noqa: F811  二十六·2，三十二·3（长按改点击）
    def setup(st) -> None:
        _setup(st)
        now = st.now().isoformat()
        st.time_lanes = {"today": now[:10], "now": now, "windowStart": now, "windowEnd": now, "human": [], "agents": [
            {"agent": "claude-code", "tool": "claude-code", "name": "g", "runId": "r1", "laneKey": "cc\ng", "projectId": "p_math", "running": True, "lost": False,
             "phase": "working", "segments": [], "attend": [], "retained": False}]}                    # 不留存的卡收在「其余 N 个」里：照样点开
    with open_stage(browser, base, setup=setup, pets={"claude": TOUCH_PACK, "kimi": KIMI}, hold_webm=True) as st:
        st.page.clock.run_for(600)
        st.page.mouse.click(*st.center(PET + " .st-media"))                                          # 点击小人（三十二·3：原长按）
        st.page.clock.run_for(400)
        detail = st.page.locator("#agPage .ag-detail")
        detail.locator(".ag-dt-title").wait_for()                                                     # 点击小人 = 放大看这个 agent 的详情
        assert detail.locator(".ag-dt-title").inner_text() == "g" and st.page.locator(".fp-panel").count() == 0
        assert st.page.locator(".st-detail").count() == 0                                             # 没有小人信息卡
        detail.locator(".ag-big").click()                                                             # 点大立绘 = 换这个 agent 的形象
        st.page.wait_for_selector(".fp-tile")
        assert st.page.locator(".fp-panel h3").inner_text() == "claude-code 的形象"
        assert st.page.locator('.fp-tile[aria-pressed="true"]').get_attribute("data-kind") == "claude"
        st.replies["activity/settings"] = (200, {**SETTINGS, "version": 4, "pet": {"packsByTool": {"claude-code": "kimi", "*": "deepseek"}}})
        with st.page.expect_response(lambda r: r.request.method == "PUT" and r.url.endswith("activity/settings")):
            st.page.locator('.fp-tile[data-kind="kimi"]').click()
        assert _puts(st)[0]["pet"] == {"packsByTool": {"claude-code": "kimi", "*": "deepseek"}}
        st.settle()
        st.page.keyboard.press("Escape")
        st.page.wait_for_timeout(450)
        st.page.keyboard.press("Escape")
        st.page.wait_for_timeout(450)
        st.page.locator(PET + " video.st-art").dispatch_event("ended")                                 # 点击时的点击动作播完（假视频不会自己结束）
        assert "/hive/pets/kimi/" in st.page.locator(PET + " video.st-art").get_attribute("src")
