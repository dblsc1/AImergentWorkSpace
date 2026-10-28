"""待分类窗口块卡片（v0.5.1）：拖动把手够大、悬停连线 + 小蜂巢推近 / 没有去向时放「未分类」的位、
拖到项目格上慢慢升起、放在「新建任务」行上要确认才建、拖到分区空白处长出新格 → 放下开建项目表单（名字预填）→ 建好再把块挂进去。

端点沿用 test_hive_tiers 的桩；时间用假时钟推。
"""

from __future__ import annotations

import json
from typing import Any

from test_hive_decide import _until  # noqa: F401
from test_hive_focus import base, browser  # noqa: F401  (fixtures)
from test_hive_minihive import _grip_drag, _leave, _state
from test_hive_tiers import DOC, PROPS, ZOOM, _posts, _show, open_tiers


def _block(hive: Any, key: str) -> Any:
    return hive.page.locator(f'.ht-block[data-block="{key}"]')


def _hover_block(hive: Any, key: str) -> None:
    _block(hive, key).scroll_into_view_if_needed()
    _block(hive, key).hover(position={"x": 90, "y": 12})
    hive.page.clock.run_for(200)    # 悬停防抖 150ms


def _stub_creates(hive: Any) -> None:
    """planner/tasks、planner/projects 的 POST 记在 hive.created（[(路径, 请求体)]），回一个新 id。"""
    hive.created = []

    def make(kind: str, new_id: str) -> Any:
        def handler(r: Any) -> None:
            if r.request.method != "POST":
                hive.route(r)
                return
            hive.writes.append("planner/" + kind)
            hive.created.append((kind, json.loads(r.request.post_data)))
            r.fulfill(status=200, content_type="application/json", body=json.dumps({"id": new_id, "name": "x", "zoneId": "z_a", "projectId": "p_math"}))
        return handler

    hive.page.route("**/api/core/planner/tasks", make("tasks", "t_new"))
    hive.page.route("**/api/core/planner/projects", make("projects", "p_new"))


def _drop_on_new_task_row(hive: Any) -> None:
    cell = hive.page.locator("[data-project-id=p_math].hex-cell")
    _grip_drag(hive, DOC, cell)
    hive.page.clock.run_for(2300)
    hive.page.clock.run_for(600); hive.page.wait_for_timeout(300)
    row = hive.page.locator(".hex-cell.is-expanded .hd-newtask")
    row.evaluate("e => e.scrollIntoView({block: 'center'})")
    t = row.bounding_box()
    hive.page.mouse.move(t["x"] + 30, t["y"] + t["height"] / 2, steps=3)
    hive.page.clock.run_for(100)
    hive.page.mouse.up()
    hive.page.wait_for_selector(".hd-newtask input")


def test_grip_is_a_wide_strip_on_the_left_edge(browser, base) -> None:  # noqa: F811
    with open_tiers(browser, base, props=PROPS, width=1900) as hive:
        _show(hive)
        grip = _block(hive, DOC).locator(".ht-grip")
        g, card = grip.bounding_box(), _block(hive, DOC).bounding_box()
        assert g["width"] >= 20, g
        assert abs(g["x"] - card["x"]) <= 2 and g["height"] >= card["height"] - 4, "贴着卡片左边的整条竖带"
        assert grip.evaluate("e => getComputedStyle(e).cursor") == "grab"
        before = grip.evaluate("e => getComputedStyle(e).backgroundColor")
        grip.hover()
        hive.page.wait_for_timeout(300)
        assert grip.evaluate("e => getComputedStyle(e).backgroundColor") != before, "悬停有变化"


def test_hover_block_with_target_draws_line_and_zooms_snapshot(browser, base) -> None:  # noqa: F811
    with open_tiers(browser, base, props=PROPS, width=1900) as hive:
        _show(hive)
        whole = hive.page.evaluate("() => document.querySelector('symbol#hm-base').viewBox.baseVal.width")
        vb = _block(hive, DOC).locator(".hd-minihive svg").evaluate("e => e.viewBox.baseVal.width")
        assert 0 < vb < whole, "小蜂巢推近到目标，不是整张蜂巢"
        assert _block(hive, DOC).locator(".hd-minihive .hm-m").count() == 1, "目标上有标记"
        _hover_block(hive, DOC)
        assert _state(hive, "s.expandedId") == "p_math"
        assert hive.page.locator(".hm-hl").count() == 1, "主蜂巢里目标被点亮"
        hive.page.clock.run_for(700)
        assert hive.page.locator(".hm-line-layer line").count() == 1, "从卡片连到目标的线"
        _leave(hive)
        assert hive.page.locator(".hm-line-layer").count() == 0 and hive.page.locator(".hm-hl").count() == 0


def test_block_without_target_shows_unclassified_slot_and_no_line(browser, base) -> None:  # noqa: F811
    with open_tiers(browser, base, props=PROPS, width=1900) as hive:
        _show(hive)
        mh = _block(hive, ZOOM).locator(".hd-minihive")
        assert mh.locator("svg").count() == 0, "不画看不清的整张蜂巢"
        assert mh.locator(".hm-unc").inner_text() == "未分类"
        box = mh.bounding_box()
        assert box["width"] >= 60 and box["height"] >= 60
        _hover_block(hive, ZOOM)
        hive.page.clock.run_for(700)
        assert hive.page.locator(".hm-line-layer").count() == 0 and hive.page.locator(".hm-hl").count() == 0
        assert _state(hive, "s.expandedId") is None


def test_drag_over_project_cell_rises_over_dwell_then_opens_and_reverts_on_leave(browser, base) -> None:  # noqa: F811
    with open_tiers(browser, base, props=PROPS, width=1900) as hive:
        _show(hive)
        cell = hive.page.locator("[data-project-id=p_math].hex-cell")
        _grip_drag(hive, DOC, cell)
        cls = lambda: cell.get_attribute("class") or ""  # noqa: E731
        assert "hd-rise" in cls() and "is-rising" in cls(), "悬着就开始升起"
        assert cell.evaluate("e => e.style.getPropertyValue('--rise-ms')") == "2000ms", "升起时长 = 停留 2 秒"
        hive.page.clock.run_for(600)
        assert hive.page.locator(".hd-spark").count() >= 1, "周围有光点"
        hive.page.mouse.move(5, 5, steps=3)
        hive.page.clock.run_for(100)
        assert "is-rising" not in cls(), "离开：落回"
        hive.page.clock.run_for(400)
        assert "hd-rise" not in cls()
        hive.page.mouse.up()
        hive.page.clock.run_for(100)
    with open_tiers(browser, base, props=PROPS, width=1900) as hive:
        _show(hive)
        cell = hive.page.locator("[data-project-id=p_math].hex-cell")
        _grip_drag(hive, DOC, cell)
        hive.page.clock.run_for(2100)
        assert _state(hive, "s.expandedId") == "p_math", "2 秒到：展开"
        assert "hd-rise" not in (hive.page.locator(".hex-cell.is-expanded").first.get_attribute("class") or ""), "展开时升起效果去掉"
        hive.page.mouse.up()
        hive.page.clock.run_for(100)


def test_drag_over_project_cell_reduced_motion_has_no_rise_or_sparks(browser, base) -> None:  # noqa: F811
    with open_tiers(browser, base, props=PROPS, width=1900, reduced_motion="reduce") as hive:
        _show(hive)
        cell = hive.page.locator("[data-project-id=p_math].hex-cell")
        _grip_drag(hive, DOC, cell)
        hive.page.clock.run_for(600)
        assert "hd-rise" not in (cell.get_attribute("class") or "")
        assert hive.page.locator(".hd-spark").count() == 0
        hive.page.mouse.up()
        hive.page.clock.run_for(100)


def test_drop_on_new_task_row_shows_prefilled_input_and_creates_only_on_confirm(browser, base) -> None:  # noqa: F811
    with open_tiers(browser, base, props=PROPS, width=1900) as hive:
        _show(hive)
        _stub_creates(hive)
        _drop_on_new_task_row(hive)
        inp = hive.page.locator(".hd-newtask input")
        assert inp.input_value() == DOC, "预填块名"
        assert inp.evaluate("e => document.activeElement === e && e.selectionStart === 0 && e.selectionEnd === e.value.length"), "聚焦并全选"
        hive.page.clock.run_for(500)
        assert hive.created == [] and _posts(hive, "activity/blocks/assign") == [], "没确认之前什么都不发"
        inp.fill("新任务名")
        hive.page.locator('.hd-newtask [data-hd="newtask-ok"]').click()
        _until(hive.page, lambda: len(_posts(hive, "activity/blocks/assign")) == 1)
        assert hive.created == [("tasks", {"projectId": "p_math", "name": "新任务名"})]
        assert _posts(hive, "activity/blocks/assign")[0][1]["taskId"] == "t_new"


def test_drop_on_new_task_row_enter_confirms(browser, base) -> None:  # noqa: F811
    with open_tiers(browser, base, props=PROPS, width=1900) as hive:
        _show(hive)
        _stub_creates(hive)
        _drop_on_new_task_row(hive)
        hive.page.keyboard.press("Enter")
        _until(hive.page, lambda: len(_posts(hive, "activity/blocks/assign")) == 1)
        assert hive.created == [("tasks", {"projectId": "p_math", "name": DOC})]


def test_drop_on_new_task_row_escape_and_cancel_create_nothing(browser, base) -> None:  # noqa: F811
    with open_tiers(browser, base, props=PROPS, width=1900) as hive:
        _show(hive)
        _stub_creates(hive)
        _drop_on_new_task_row(hive)
        hive.page.keyboard.press("Escape")
        hive.page.clock.run_for(500)
        assert hive.page.locator(".hd-newtask").count() == 0
        assert hive.created == [] and _posts(hive, "activity/blocks/assign") == []
    with open_tiers(browser, base, props=PROPS, width=1900) as hive:
        _show(hive)
        _stub_creates(hive)
        _drop_on_new_task_row(hive)
        hive.page.locator('.hd-newtask [data-hd="newtask-cancel"]').click()
        hive.page.clock.run_for(500)
        assert hive.page.locator(".hd-newtask").count() == 0
        assert hive.created == [] and _posts(hive, "activity/blocks/assign") == []


def _drag_to_blank(hive: Any) -> None:
    """从把手拖出去，在蜂巢画布上扫点，直到出现空白处的长出来那一格。"""
    page = hive.page
    _until(page, lambda: page.evaluate("getComputedStyle(document.querySelector('.hd-panel')).transform") == "none")
    grip = _block(hive, DOC).locator(".ht-grip")
    grip.scroll_into_view_if_needed()
    b = grip.bounding_box()
    page.mouse.move(b["x"] + b["width"] / 2, b["y"] + b["height"] / 2)
    page.mouse.down()
    page.mouse.move(b["x"] - 60, b["y"] + 10, steps=3)
    cam = page.evaluate("() => { const r = document.querySelector('#hive').getBoundingClientRect(); return [r.left, r.top, r.width, r.height]; }")
    for gx in range(0, int(cam[2]), 45):
        for gy in range(0, int(cam[3]), 45):
            page.mouse.move(cam[0] + gx, cam[1] + gy)
            page.clock.run_for(50); page.clock.run_for(50)
            if page.locator(".hex-sprout").count():
                return
    raise AssertionError("蜂巢里找不到空白处")


def test_drag_over_empty_zone_area_grows_sprout_and_leaving_removes_it(browser, base) -> None:  # noqa: F811
    with open_tiers(browser, base, props=PROPS, width=1900) as hive:
        _show(hive)
        _drag_to_blank(hive)
        sp = hive.page.locator(".hex-sprout")
        assert sp.count() == 1 and "is-growing" in sp.get_attribute("class"), "同长按建项目一样的那一格"
        assert sp.evaluate("e => getComputedStyle(e).getPropertyValue('--press-ms')").strip() != "", "同一段显现时长"
        assert "is-rising" in sp.get_attribute("class"), "同样的升起"
        hive.page.mouse.move(5, 5, steps=3)
        hive.page.clock.run_for(100)
        assert hive.page.locator(".hex-sprout").count() == 0, "挪开就收掉"
        hive.page.mouse.up()
        hive.page.clock.run_for(100)
        assert hive.page.locator(".hex-newcard").count() == 0


def test_drag_empty_zone_cancel_with_escape_removes_sprout(browser, base) -> None:  # noqa: F811
    with open_tiers(browser, base, props=PROPS, width=1900) as hive:
        _show(hive)
        _stub_creates(hive)
        _drag_to_blank(hive)
        hive.page.keyboard.press("Escape")
        hive.page.clock.run_for(100)
        assert hive.page.locator(".hex-sprout").count() == 0
        hive.page.mouse.up()
        hive.page.clock.run_for(300)
        assert hive.page.locator(".hex-newcard").count() == 0 and hive.created == []


def test_drop_on_empty_zone_area_opens_prefilled_create_form_and_assigns_after_confirm(browser, base) -> None:  # noqa: F811
    with open_tiers(browser, base, props=PROPS, width=1900) as hive:
        _show(hive)
        _stub_creates(hive)
        _drag_to_blank(hive)
        hive.page.mouse.up()
        form = hive.page.locator(".hex-cell.is-expanded .hex-newcard")
        form.wait_for()
        inp = form.locator('input[name="name"]')
        assert inp.input_value() == DOC, "名字预填块名"
        hive.page.clock.run_for(600)
        assert inp.evaluate("e => document.activeElement === e && e.selectionEnd - e.selectionStart === e.value.length"), "聚焦并全选"
        assert hive.created == [] and _posts(hive, "activity/blocks/assign") == [], "没确认什么都不建"
        form.locator('button[type="submit"]').click()
        _until(hive.page, lambda: len(_posts(hive, "activity/blocks/assign")) == 1)
        assert [c[0] for c in hive.created] == ["projects"] and hive.created[0][1]["name"] == DOC and hive.created[0][1]["zoneId"]
        body = _posts(hive, "activity/blocks/assign")[0][1]
        assert body["blockKey"] == DOC and body["projectId"] == "p_new" and body.get("taskId") is None, "建好后按落在项目上的方式改挂"


def test_drop_on_empty_zone_area_cancel_creates_nothing(browser, base) -> None:  # noqa: F811
    for how in ("cancel", "escape"):
        with open_tiers(browser, base, props=PROPS, width=1900) as hive:
            _show(hive)
            _stub_creates(hive)
            _drag_to_blank(hive)
            hive.page.mouse.up()
            hive.page.locator(".hex-cell.is-expanded .hex-newcard").wait_for()
            hive.page.clock.run_for(600)
            if how == "cancel":
                hive.page.locator('.hex-newcard [data-hex-ui="close"]').click()
            else:
                hive.page.keyboard.press("Escape")
            hive.page.clock.run_for(800)
            assert hive.created == [] and _posts(hive, "activity/blocks/assign") == [], how
            # 取消后别处建的项目不能被误挂：关联只属于这一次的表单
            assert hive.page.evaluate("() => window.NexusTableHexSprout.created(document.body, {id: 'x'})") is None
            assert _posts(hive, "activity/blocks/assign") == []
