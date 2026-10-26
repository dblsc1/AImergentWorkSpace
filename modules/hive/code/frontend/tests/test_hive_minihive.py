"""决策栏小卡的小蜂巢（hex-minihive.js）：整张蜂巢缩小 + 目标标记、目标已不存在时标红并禁用批准、
悬停卡片时主蜂巢推近并打同样的标记（移开后复原）、批准时卡片飞向目标、拖待分类窗口块进蜂巢（描框 → 停 2 秒推近 → 放下改挂 / 按块名建任务）。

端点沿用 test_hive_tiers 的桩；时间用假时钟推。
"""

from __future__ import annotations

import json
from typing import Any

from test_hive_decide import _settle, _until  # noqa: F401
from test_hive_focus import TREE, base, browser  # noqa: F401  (fixtures)
from test_hive_tiers import DOC, PROPS, _posts, _show, open_tiers

CH = [
    {"id": "cd1", "op": "delete", "target": "task", "targetId": "t_p_math", "projectId": "p_math", "targetName": "数学的任务"},
    {"id": "cd2", "op": "delete", "target": "task", "targetId": "t_gone", "projectId": "p_math", "targetName": "已删的任务"},
    {"id": "cm1", "op": "move", "target": "task", "targetId": "t_p_math", "projectId": "p_math", "targetName": "数学的任务", "toId": "p_piano", "toName": "钢琴"},
    {"id": "cp1", "op": "move", "target": "project", "targetId": "p_math", "targetName": "数学", "toId": "z_b", "toName": "制作"},
]
P2 = {**PROPS, "changes": CH}


def _open(browser: Any, base: str, **kw: Any) -> Any:
    return open_tiers(browser, base, props=P2, **kw)


def _card(hive: Any, key: str) -> Any:
    return hive.page.locator(f'.hd-card[data-key="{key}"]')


def _state(hive: Any, expr: str) -> Any:
    return hive.page.evaluate(f"() => {{ const s = window.NexusTableHexApp.getState(); return {expr}; }}")


def _hover(hive: Any, key: str) -> None:
    _card(hive, key).scroll_into_view_if_needed()
    _card(hive, key).hover(position={"x": 30, "y": 12})
    hive.page.clock.run_for(200)    # 悬停防抖 150ms


def _leave(hive: Any) -> None:
    hive.page.mouse.move(300, 450)
    hive.page.clock.run_for(200)


def test_minihive_draws_the_whole_hive_with_a_mark_per_op(browser, base) -> None:  # noqa: F811
    with _open(browser, base, width=1900) as hive:
        _show(hive)
        n = _state(hive, "s.hive.cells.filter(c => !c.__sprout).length")
        for key in ("nt_2", "cd1", "cm1", "cp1"):
            assert _card(hive, key).locator('.hd-minihive svg use[href="#hm-base"]').count() == 1, "底图共用一份 symbol"
        assert hive.page.locator("symbol#hm-base .hm-cell").count() == n, "整张蜂巢：每一格都画"
        for key in ("nt_2", "cd1", "cm1", "cp1"):
            assert _card(hive, key).locator(".hd-minihive svg .hm-cell").count() == 0
        mk = lambda key, sel: _card(hive, key).locator(f".hd-minihive {sel}").count()  # noqa: E731
        assert mk("nt_2", ".hm-m.is-frame") == 1 and mk("nt_2", ".hm-line") == 0, "新建任务：目标项目格上虚线框"
        assert mk("cd1", ".hm-m.is-del") == 1, "删除：红框"
        assert mk("cm1", ".hm-m.is-frame") == 1 and mk("cm1", ".hm-line") == 1 and mk("cm1", ".hm-origin") == 1, "移任务：目的地虚线框 + 从原位置来的线"
        assert mk("cp1", ".hm-m.is-frame polygon, .hm-m-zone .is-frame") >= 1 and mk("cp1", ".hm-line") == 1, "移项目：目的分区虚线框 + 线"
        assert _card(hive, "cd1").locator(".hd-minihive text").count() == 0, "小蜂巢里没有文字"
        box = _card(hive, "nt_2").locator(".hd-minihive").bounding_box()
        assert box["width"] >= 80 and box["height"] >= 80


def test_missing_target_marks_card_red_and_blocks_approve(browser, base) -> None:  # noqa: F811
    with _open(browser, base, width=1900) as hive:
        _show(hive)
        c = _card(hive, "cd2")
        assert "is-missing" in c.get_attribute("class") and c.locator(".hm-gone").inner_text() == "目标已不存在"
        assert c.locator('[data-hd="approve"]').is_disabled()
        assert c.locator(".hd-minihive .hm-m").count() == 0, "目标没了不画标记"
        assert "is-missing" not in (_card(hive, "cd1").get_attribute("class") or "")
        c.focus()
        c.press("a")                                                 # 键盘批准
        hive.page.clock.run_for(6000)
        assert _posts(hive, "activity/proposals/cd2") == [], "批准没发出去"
        assert "is-gone" not in c.get_attribute("class")
        c.press("r")                                                 # 拒绝照样可以
        hive.page.clock.run_for(6000)
        _until(hive.page, lambda: len(_posts(hive, "activity/proposals/cd2/reject")) == 1)


def test_missing_target_blocks_swipe_approve(browser, base) -> None:  # noqa: F811
    with _open(browser, base, width=1900) as hive:
        _show(hive)
        box = _card(hive, "cd2").locator(".hd-face").bounding_box()
        x, y = box["x"] + box["width"] / 2, box["y"] + 14
        hive.page.mouse.move(x, y); hive.page.mouse.down()
        hive.page.mouse.move(x - box["width"] * 0.25, y, steps=3); hive.page.mouse.move(x - box["width"] * 0.6, y, steps=3)
        hive.page.mouse.up()
        hive.page.clock.run_for(6000)
        assert _posts(hive, "activity/proposals/cd2") == [] and _card(hive, "cd2").count() == 1 and "is-gone" not in _card(hive, "cd2").get_attribute("class")


def test_minihive_follows_the_tree(browser, base) -> None:  # noqa: F811
    with _open(browser, base, width=1900) as hive:
        _show(hive)
        assert "is-missing" not in _card(hive, "nt_2").get_attribute("class")
        gone = {**TREE, "projects": [p for p in TREE["projects"] if p["id"] != "p_piano"]}
        hive.page.route("**/api/core/views/tree*", lambda r: r.fulfill(status=200, content_type="application/json", body=json.dumps(gone)))
        hive.page.evaluate("() => window.NexusTableHexApp.refresh()")
        _until(hive.page, lambda: "is-missing" in (_card(hive, "nt_2").get_attribute("class") or ""))
        assert _card(hive, "nt_2").locator('[data-hd="approve"]').is_disabled()


def test_hover_card_zooms_main_hive_and_restores(browser, base) -> None:  # noqa: F811
    with _open(browser, base, width=1900) as hive:
        _show(hive)
        assert _state(hive, "s.expandedId") is None
        _hover(hive, "nt_2")                                   # 新建任务：推近到钢琴，新位置虚线框
        assert _state(hive, "s.expandedId") == "p_piano"
        assert hive.page.locator(".hex-cell.is-expanded li.is-ghost.hm-frame").inner_text().startswith("调音")
        _leave(hive)
        assert _state(hive, "s.expandedId") is None and hive.page.locator(".hm-frame").count() == 0, "移开后回到原来的视图"
        _hover(hive, "cd1")                                    # 删除任务：推近 + 红框
        assert _state(hive, "s.expandedId") == "p_math"
        assert hive.page.locator("li.hm-del").get_attribute("data-task-id") == "t_p_math"
        _hover(hive, "cm1")                                    # 移任务：推近目的项目、目的地虚线框、连回原任务
        assert _state(hive, "s.expandedId") == "p_piano"
        assert hive.page.locator(".hex-cell.is-expanded li.hm-frame").count() == 1
        hive.page.clock.run_for(700)
        assert hive.page.locator(".hm-line-layer line").count() == 1
        _leave(hive)
        assert _state(hive, "s.expandedId") is None and hive.page.locator(".hm-line-layer").count() == 0 and hive.page.locator(".hm-del, .hm-frame").count() == 0
        _hover(hive, "cp1")                                    # 移项目：不推近，只有框和线
        assert _state(hive, "s.expandedId") is None
        assert hive.page.locator('.hex-zone-label[data-zone-id="z_b"].hm-frame').count() == 1
        hive.page.clock.run_for(100)
        assert hive.page.locator(".hm-line-layer line").count() == 1
        _leave(hive)
        assert hive.page.locator(".hm-frame, .hm-line-layer").count() == 0


def test_hover_restores_the_view_that_was_open_before(browser, base) -> None:  # noqa: F811
    with _open(browser, base, width=1900) as hive:
        _show(hive)
        hive.page.evaluate("() => window.NexusTableHexApp.expand('p_studio')")
        hive.page.clock.run_for(600)
        _hover(hive, "nt_2")
        assert _state(hive, "s.expandedId") == "p_piano"
        _leave(hive)
        assert _state(hive, "s.expandedId") == "p_studio"


def test_hover_over_missing_card_does_not_move_the_hive(browser, base) -> None:  # noqa: F811
    with _open(browser, base, width=1900) as hive:
        _show(hive)
        _hover(hive, "cd2")
        assert _state(hive, "s.expandedId") is None and hive.page.locator(".hm-frame, .hm-del, .hm-hl").count() == 0


def test_hover_is_debounced(browser, base) -> None:  # noqa: F811
    with _open(browser, base, width=1900) as hive:
        _show(hive)
        _card(hive, "nt_2").scroll_into_view_if_needed()
        _card(hive, "nt_2").hover(position={"x": 30, "y": 12})
        hive.page.clock.run_for(100)
        assert _state(hive, "s.expandedId") is None, "150ms 内不动"
        hive.page.clock.run_for(100)
        assert _state(hive, "s.expandedId") == "p_piano"


def test_approve_flies_the_card_into_the_hive(browser, base) -> None:  # noqa: F811
    for reduced in (None, "reduce"):
        with _open(browser, base, width=1900, reduced_motion=reduced) as hive:
            _show(hive)
            _card(hive, "nt_2").locator('[data-hd="approve"]').click()
            assert hive.page.locator(".hm-fly").count() == 1
            kf = hive.page.evaluate("() => document.querySelector('.hm-fly').getAnimations()[0].effect.getKeyframes().map(k => Object.keys(k).filter(x => x === 'transform' || x === 'opacity'))")
            assert all("opacity" in k for k in kf)
            assert any("transform" in k for k in kf) == (reduced is None), "减少动效只淡出，不缩放位移"
            _until(hive.page, lambda: hive.page.locator(".hm-fly").count() == 0, 3000)


def _grip_drag(hive: Any, key: str, target: Any) -> None:
    page = hive.page
    _until(page, lambda: page.evaluate("getComputedStyle(document.querySelector('.hd-panel')).transform") == "none")
    target.evaluate("e => e.scrollIntoView({block: 'center', inline: 'center'})")
    page.clock.run_for(600); page.wait_for_timeout(400)
    grip = page.locator(f'.ht-block[data-block="{key}"] .ht-grip')
    grip.scroll_into_view_if_needed()
    b = grip.bounding_box()
    page.mouse.move(b["x"] + b["width"] / 2, b["y"] + b["height"] / 2)
    page.mouse.down()
    page.mouse.move(b["x"] - 60, b["y"] + 10, steps=3)
    t = target.bounding_box()
    page.mouse.move(t["x"] + t["width"] / 2, t["y"] + t["height"] / 2, steps=4)
    page.clock.run_for(50); page.clock.run_for(50)


def test_drag_block_outlines_cell_then_zooms_after_two_seconds_and_drop_on_task_assigns(browser, base) -> None:  # noqa: F811
    with _open(browser, base, width=1900) as hive:
        _show(hive)
        cell = hive.page.locator("[data-project-id=p_math].hex-cell")
        _grip_drag(hive, DOC, cell)
        assert "hd-over" in cell.get_attribute("class"), "路过 / 停在格子上：先描框"
        assert _state(hive, "s.expandedId") is None
        hive.page.clock.run_for(1800)
        assert _state(hive, "s.expandedId") is None, "不到 2 秒不推近"
        hive.page.clock.run_for(300)
        assert _state(hive, "s.expandedId") == "p_math", "停够 2 秒推近"
        hive.page.clock.run_for(600); hive.page.wait_for_timeout(300)
        li = hive.page.locator(".hex-cell.is-expanded .hex-detail li[data-task-id=t_p_math]")
        li.evaluate("e => e.scrollIntoView({block: 'center'})")
        t = li.bounding_box()
        hive.page.mouse.move(t["x"] + 30, t["y"] + t["height"] / 2, steps=3)
        hive.page.clock.run_for(100)
        assert "hd-over" in li.get_attribute("class")
        hive.page.mouse.up()
        _until(hive.page, lambda: len(_posts(hive, "activity/blocks/assign")) == 1)
        body = _posts(hive, "activity/blocks/assign")[0][1]
        assert body["blockKey"] == DOC and body["taskId"] == "t_p_math" and body["remember"] is True


def test_drag_block_moving_on_restarts_the_two_seconds(browser, base) -> None:  # noqa: F811
    with _open(browser, base, width=1900) as hive:
        _show(hive)
        cell = hive.page.locator("[data-project-id=p_math].hex-cell")
        _grip_drag(hive, DOC, cell)
        hive.page.clock.run_for(1500)
        t = cell.bounding_box()
        hive.page.mouse.move(t["x"] + t["width"] / 2 + 25, t["y"] + t["height"] / 2, steps=2)   # 还在这一格里，但挪了
        hive.page.clock.run_for(50)
        hive.page.clock.run_for(1500)
        assert _state(hive, "s.expandedId") is None, "挪动后从头数"
        hive.page.clock.run_for(700)
        assert _state(hive, "s.expandedId") == "p_math"
        hive.page.mouse.up()
        hive.page.clock.run_for(100)


def test_drag_block_out_of_the_hive_restores_the_view(browser, base) -> None:  # noqa: F811
    with _open(browser, base, width=1900) as hive:
        _show(hive)
        cell = hive.page.locator("[data-project-id=p_math].hex-cell")
        _grip_drag(hive, DOC, cell)
        hive.page.clock.run_for(2200)
        assert _state(hive, "s.expandedId") == "p_math"
        hive.page.mouse.move(5, 5, steps=3)
        hive.page.clock.run_for(100)
        assert _state(hive, "s.expandedId") is None and hive.page.locator(".hd-newtask").count() == 0, "拖出蜂巢：回到原来的视图"
        hive.page.mouse.up()
        hive.page.clock.run_for(100)
        assert _posts(hive, "activity/blocks/assign") == []


def test_drop_block_on_new_task_row_creates_task_named_after_block_and_assigns(browser, base) -> None:  # noqa: F811
    with _open(browser, base, width=1900) as hive:
        _show(hive)
        hive.created = []

        def tasks(r: Any) -> None:
            if r.request.method != "POST":
                hive.route(r)
                return
            hive.writes.append("planner/tasks")
            hive.created.append(json.loads(r.request.post_data))
            r.fulfill(status=200, content_type="application/json", body=json.dumps({"id": "t_new", "projectId": "p_math", "name": "x"}))

        hive.page.route("**/api/core/planner/tasks", tasks)
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
        _until(hive.page, lambda: len(_posts(hive, "activity/blocks/assign")) == 1)
        assert hive.created == [{"projectId": "p_math", "name": DOC}], "按块名直接建，不等输入"
        assert _posts(hive, "activity/blocks/assign")[0][1]["taskId"] == "t_new"


def test_batch_approve_with_only_missing_targets_says_so(browser, base) -> None:  # noqa: F811
    only = {**PROPS, "tasks": [], "changes": [CH[1]]}
    with open_tiers(browser, base, props=only, width=1900) as hive:
        _show(hive)
        hive.page.locator('.hb-big[data-sec="s"] [data-ht="batch-ok"]').click()
        _until(hive.page, lambda: "目标已不存在，没有批准" in " ".join(hive.page.locator(".hd-toast").all_inner_texts()))
        hive.page.clock.run_for(6000)
        assert _posts(hive, "activity/proposals/cd2") == []


def test_approve_rechecks_the_tree_instead_of_trusting_the_card_class(browser, base) -> None:  # noqa: F811
    with _open(browser, base, width=1900) as hive:
        _show(hive)
        hive.page.evaluate("() => { const t = window.NexusTableHexApp.getState().tree; t.projects = t.projects.filter(p => p.id !== 'p_piano'); }")
        assert "is-missing" not in _card(hive, "nt_2").get_attribute("class")
        _card(hive, "nt_2").locator('[data-hd="approve"]').click()
        hive.page.clock.run_for(6000)
        assert _posts(hive, "activity/proposals/nt_2") == []
