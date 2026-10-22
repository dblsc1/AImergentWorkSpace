"""蜂巢右上角「AI 待批准」浮层（hex-pending.js）：卡片出现、点卡片在蜂巢里指给人看、批准走对的端点。

复用 test_hive_focus.py 的页面夹具，只把 proposals / reports 两个端点桩成有数据。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from playwright.sync_api import Route

import test_hive_focus as base_mod
from test_hive_focus import base, browser, open_hive  # noqa: F401  (fixtures)

PROPOSALS = {
    "projects": [{"id": "np_1", "kind": "project", "name": "写书", "reason": "最近常开文档", "zoneId": "z_a",
                  "zoneName": "学习", "createdAt": "2026-10-20T00:00:00Z",
                  "tasks": [{"id": "nt_9", "name": "列大纲", "status": "pending"}]}],
    "tasks": [{"id": "nt_2", "kind": "task", "name": "调音", "reason": "钢琴相关", "projectId": "p_piano",
               "projectName": "钢琴", "projectProposalId": None}],
}
# 真实形状：列表（items=true）的条不带 suggestions / seconds，详情（GET reports/{id}）才带
_ITEMS = [
    {"id": "i0", "kind": "assign", "status": "pending", "taskId": "t_p_math", "projectId": None, "newTask": None, "reason": "在做数学"},
    {"id": "i1", "kind": "dismiss", "status": "pending", "taskId": None, "projectId": None, "newTask": None, "reason": "刷视频"}]
_REPORT = {"id": "r1", "author": "hermes", "summary": "今天的整理", "status": "pending", "counts": {"items": 2}}
REPORTS = {"items": [{**_REPORT, "items": _ITEMS}]}
DETAIL = {**_REPORT, "items": [{**_ITEMS[0], "suggestions": [{"id": "a", "durationSeconds": 900}], "seconds": 900},
                               {**_ITEMS[1], "suggestions": [{"id": "b", "durationSeconds": 60}], "seconds": 60}]}


def _install(hive: Any, proposals: dict = PROPOSALS) -> None:
    def route(r: Route) -> None:
        path = r.request.url.split("/api/core/", 1)[1]
        if path.startswith("activity/proposals?"):
            r.fulfill(status=200, content_type="application/json", body=json.dumps(proposals))
        elif path == "activity/reports/r1" and r.request.method == "GET":
            r.fulfill(status=200, content_type="application/json", body=json.dumps(DETAIL))
        elif path.startswith("activity/reports?"):
            r.fulfill(status=200, content_type="application/json", body=json.dumps(REPORTS))
        elif r.request.method == "POST" and path.startswith("activity/proposals/ch_409/"):
            r.fulfill(status=409, content_type="application/json", body=json.dumps({"detail": "目标已经变了"}))
        elif r.request.method == "POST":
            hive.writes.append(path)
            r.fulfill(status=200, content_type="application/json", body="{}")
        else:
            hive.route(r)
    hive.page.route("**/api/core/activity/**", route)
    na = {"today": "2026-10-09", "zones": [{"actionable": [
        {"id": "t_p_math", "name": "数学的任务", "projectId": "p_math", "projectName": "数学"}], "waiting": []}]}
    hive.page.route("**/api/core/views/next-actions*", lambda r: r.fulfill(
        status=200, content_type="application/json", body=json.dumps(na)))


def test_panel_cards_and_approve(browser, base) -> None:  # noqa: F811
    with open_hive(browser, base) as hive:
        _install(hive)
        hive.page.evaluate("() => window.NexusTableHexApp.refresh()")
        hive.page.wait_for_selector(".hp-card")
        assert hive.page.locator(".hp-card").count() == 4
        assert hive.page.locator(".hp-badge").inner_text() == "4"
        assert "1 个窗口 · 15 分" in hive.page.locator('.hp-card[data-key="r1/i0"]').inner_text(), "分钟来自详情"
        hive.page.locator('.hp-card[data-key="nt_2"] [data-hp=approve]').click()
        hive.page.wait_for_timeout(50)
        assert hive.writes == ["activity/proposals/nt_2/approve"]
        hive.page.locator('.hp-card[data-key="np_1"] [data-hp=reject]').click()
        hive.page.wait_for_timeout(50)
        assert hive.writes[-1] == "activity/proposals/np_1/reject"
        hive.page.locator('[data-report=r1] [data-hp=approve-all]').click()
        hive.page.wait_for_timeout(50)
        assert hive.writes[-1] == "activity/reports/r1/approve"


def test_click_move_highlights_task_and_new_project_is_ghost(browser, base) -> None:  # noqa: F811
    with open_hive(browser, base) as hive:
        _install(hive)
        hive.page.evaluate("() => window.NexusTableHexApp.refresh()")
        hive.page.wait_for_selector(".hp-card")
        hive.page.locator('.hp-card[data-key="r1/i0"] .hp-name').click()
        assert hive.page.locator(".hex-cell.is-expanded").get_attribute("data-project-id") == "p_math"
        assert hive.page.locator(".is-pending-hl").get_attribute("data-task-id") == "t_p_math"
        assert hive.page.locator(".hex-cell.is-expanded").count() == 1, "点浮层没有把展开收掉"

        hive.page.locator('.hp-card[data-key="nt_2"] .hp-name').click()
        assert hive.page.locator(".hex-cell.is-expanded .hex-todo-card.is-ghost").inner_text().startswith("调音")

        hive.page.locator('.hp-card[data-key="nt_2"] .hp-name').click()
        assert hive.page.locator(".hex-cell.is-expanded .hex-todo-card.is-ghost").count() == 1, "重复点不堆叠"

        hive.page.locator('.hp-card[data-key="np_1"] .hp-name').click()
        ghost = hive.page.locator(".hex-cell.is-ghost.is-expanded")
        assert ghost.count() == 1 and "列大纲" in ghost.inner_text() and "AI 建议" in ghost.inner_text()
        shot = os.environ.get("HP_SHOT")
        if shot:
            hive.page.screenshot(path=shot)
        hive.page.keyboard.press("Escape")
        assert hive.page.locator(".hex-cell.is-ghost").count() == 0, "收起后幽灵格消失"


_CH = {"kind": "change", "status": "pending", "reason": "", "createdAt": "x", "createdId": None, "name": None, "toId": None, "toName": None}
EDITS = {"projects": [], "tasks": [], "changes": [
    {**_CH, "id": "ch_1", "op": "delete", "target": "task", "targetId": "t_p_math", "targetName": "数学的任务", "projectId": "p_math"},
    {**_CH, "id": "ch_2", "op": "move", "target": "task", "targetId": "t_p_math", "targetName": "数学的任务", "projectId": "p_math",
     "toId": "p_piano", "toName": "钢琴"},
    {**_CH, "id": "ch_3", "op": "rename", "target": "project", "targetId": "p_math", "targetName": "数学", "projectId": None, "name": "代数"}],
    "ignores": [{"id": "ig_1", "kind": "ignore", "status": "pending", "app": "Slack", "titleContains": "闲聊", "reason": "", "createdAt": "x", "createdId": None}]}


def test_change_and_ignore_cards(browser, base) -> None:  # noqa: F811
    with open_hive(browser, base) as hive:
        _install(hive, EDITS)
        hive.page.evaluate("() => window.NexusTableHexApp.refresh()")
        hive.page.wait_for_selector(".hp-card")
        assert hive.page.locator(".hp-badge").inner_text() == "6"  # 4 条提议 + 报告的 2 条
        assert "标题含" in hive.page.locator('.hp-card[data-key="ig_1"]').inner_text()
        hive.page.locator('.hp-card[data-key="ch_1"] .hp-name').click()
        assert hive.page.locator(".hex-cell.is-expanded").get_attribute("data-project-id") == "p_math"
        assert hive.page.locator(".is-pending-hl.is-pending-del").get_attribute("data-task-id") == "t_p_math"
        hive.page.locator('.hp-card[data-key="ch_2"] .hp-name').click()
        assert hive.page.locator(".is-pending-del").count() == 0, "换卡清掉删除标记"
        assert hive.page.locator(".hex-cell.is-pending-hl").get_attribute("data-project-id") == "p_piano", "目的项目也点亮"
        hive.page.wait_for_timeout(500)   # opacity 过渡 300ms
        assert hive.page.evaluate("getComputedStyle(document.querySelector('.hex-cell.is-pending-hl')).opacity") == "1", "目的格没被调灰"
        hive.page.locator('.hp-card[data-key="ch_3"] .hp-name').click()
        assert hive.page.locator(".hex-cell.is-pending-hl").get_attribute("data-project-id") == "p_math"
        hive.page.keyboard.press("Escape")
        assert hive.page.locator(".is-pending-hl, .is-pending-del").count() == 0, "收起后高亮清掉"
        hive.page.locator('.hp-card[data-key="ig_1"] .hp-name').click()
        hive.page.locator('.hp-card[data-key="ch_3"] [data-hp=approve]').click()
        hive.page.wait_for_timeout(50)
        assert hive.writes == ["activity/proposals/ch_3/approve"]


def test_project_delete_marks_cell_and_409_shows_detail(browser, base) -> None:  # noqa: F811
    props = {"projects": [], "tasks": [], "ignores": [], "changes": [
        {**_CH, "id": "ch_409", "op": "delete", "target": "project", "targetId": "p_math", "targetName": "数学", "projectId": None},
        {**_CH, "id": "ch_x", "op": "teleport", "target": "task", "targetId": "t", "targetName": "x", "projectId": None}]}
    with open_hive(browser, base) as hive:
        _install(hive, props)
        hive.page.evaluate("() => window.NexusTableHexApp.refresh()")
        hive.page.wait_for_selector(".hp-card")
        assert hive.page.locator('.hp-card[data-key="ch_x"]').count() == 0, "未知操作不画"
        hive.page.locator('.hp-card[data-key="ch_409"] .hp-name').click()
        assert hive.page.locator(".hex-cell.is-pending-del.is-pending-hl").get_attribute("data-project-id") == "p_math"
        hive.page.locator('.hp-card[data-key="ch_409"] [data-hp=approve]').click()
        hive.page.wait_for_selector(".hp-err")
        assert hive.page.locator(".hp-err").inner_text() == "目标已经变了"
