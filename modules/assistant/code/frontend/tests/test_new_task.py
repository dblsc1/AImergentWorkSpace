"""助理提议新任务 + 一键确认（suggestions.js，nexus-core v2.8「AI 提议新任务」，仓主 2026-10-03）。

同本套件其余文件：真浏览器，接口全在浏览器侧桩掉。判据落在行为上：行上显示什么、名字能不能改、
「是 / 否」各发了什么请求、「全部确认」绝不替人建任务。
"""

from __future__ import annotations

import pytest
from conftest import overflowing
from test_suggestions import ITEMS, open_page

NT = {"proposalId": "tp_1", "projectId": "p_book", "name": "乐理复习"}
PROP = {"taskId": None, "confidence": 0.9, "reason": "标题里有 <i>乐理</i>", "classifier": "assistant", "newTask": NT}
SEG = {"deviceId": "dev_x", "durationSeconds": 600, "app": "anki", "title": "乐理", "status": "pending",
       "suggestion": PROP}
NEW_ITEMS = [ITEMS[0],
             {**SEG, "id": "sug_n1", "startAt": "2026-09-26T08:00:00+08:00", "endAt": "2026-09-26T08:10:00+08:00"},
             {**SEG, "id": "sug_n2", "startAt": "2026-09-26T07:00:00+08:00", "endAt": "2026-09-26T07:10:00+08:00"}]
LI = 'li[data-id="sug_n1"]'


def test_proposal_row_shows_project_and_editable_name(browser, static_base_url):
    with open_page(browser, static_base_url, NEW_ITEMS) as (h, _stub):
        page = h.page
        li = page.locator(LI)
        li.wait_for()
        assert "is-new" in li.get_attribute("class") and li.get_attribute("data-count") == "2"
        assert li.locator(".suggest-newtask-path").inner_text() == "新建任务：练琴区 / 考级计划 /"
        assert li.locator(".suggest-newname").input_value() == "乐理复习"
        assert li.locator(".suggest-hint").inner_text() == "AI 建议新建任务 · 把握 90% · 标题里有 <i>乐理</i>"
        assert li.locator("i").count() == 0   # 模型写的理由只当文本
        assert [b.inner_text() for b in li.locator("button").all()] == ["是 ✓", "否 ✗"]
        assert li.locator("select").input_value() == ""   # 下拉仍在，缺省「不选现成任务」
        assert li.locator(".suggest-confirm").is_enabled()
        # 名字清空 → 「是」不能点（没选现成任务时）
        li.locator(".suggest-newname").fill("  ")
        assert li.locator(".suggest-confirm").is_disabled()


def test_bulk_confirm_never_creates_tasks(browser, static_base_url):
    with open_page(browser, static_base_url, NEW_ITEMS) as (h, stub):
        page = h.page
        page.locator(LI).wait_for()
        page.fill("#suggest-threshold", "0")
        assert page.inner_text("#suggest-confirm-all").endswith("· 1")   # 只有规则那条，提议的两段不算
        page.click("#suggest-confirm-all")
        page.wait_for_function("() => !document.querySelector('li[data-id=\"sug_a\"]')")
        assert stub.posts == [("confirm", "sug_a", {"taskId": "t_word"})]
        assert page.locator(LI).count() == 1


def test_yes_creates_with_edited_name_then_reuses_task(browser, static_base_url):
    with open_page(browser, static_base_url, NEW_ITEMS) as (h, stub):
        page = h.page
        li = page.locator(LI)
        li.locator(".suggest-newname").fill(" 乐理复习计划 ")
        li.locator(".suggest-confirm").click()
        page.wait_for_function("() => !document.querySelector('li[data-id=\"sug_n1\"]')")
        # 第一段带名字（后端建任务），其余段直接记到建好的那个
        assert stub.posts == [("confirm", "sug_n1", {"name": "乐理复习计划", "proposalId": "tp_1"}), ("confirm", "sug_n2", {"taskId": "t_new"})]
        assert "已新建任务「乐理复习计划」，2 段记进去了。" in page.inner_text("#suggest-message")


def test_picking_an_existing_task_confirms_without_creating(browser, static_base_url):
    with open_page(browser, static_base_url, NEW_ITEMS) as (h, stub):
        page = h.page
        page.locator(LI + " select").select_option("t_read")
        page.locator(LI + " .suggest-confirm").click()
        page.wait_for_function("() => !document.querySelector('li[data-id=\"sug_n1\"]')")
        assert stub.posts == [("confirm", "sug_n1", {"taskId": "t_read"}), ("confirm", "sug_n2", {"taskId": "t_read"})]


def test_no_unmatches_proposal_and_falls_back_to_picker(browser, static_base_url):
    with open_page(browser, static_base_url, NEW_ITEMS) as (h, stub):
        page = h.page
        page.locator(LI + " .suggest-no").click()
        page.wait_for_selector(LI + ":not(.is-new) select")
        assert stub.posts == [("unmatch", "sug_n1", {"proposalId": "tp_1"}), ("unmatch", "sug_n2", {"proposalId": "tp_1"})]
        li = page.locator(LI)
        assert li.locator(".suggest-newname").count() == 0
        assert [b.inner_text() for b in li.locator("button").all()] == ["确认", "忽略"]


def test_mixed_proposal_and_task_is_not_preselected(browser, static_base_url):
    other = {**NEW_ITEMS[2], "suggestion": {**PROP, "taskId": "t_read", "newTask": None}}
    with open_page(browser, static_base_url, [NEW_ITEMS[1], other]) as (h, _stub):
        li = h.page.locator(LI)
        li.wait_for()
        assert "建议不一致" in li.inner_text() and li.locator(".suggest-newname").count() == 0


def test_project_gone_falls_back_to_picker(browser, static_base_url):
    gone = [{**NEW_ITEMS[1], "suggestion": {**PROP, "newTask": {**NT, "projectId": "p_gone"}}}]
    with open_page(browser, static_base_url, gone) as (h, _stub):
        li = h.page.locator(LI)
        li.wait_for()
        assert li.locator(".suggest-newname").count() == 0 and li.locator(".suggest-confirm").is_disabled()


@pytest.mark.parametrize("width", [320, 390])
def test_narrow_no_horizontal_scroll(browser, static_base_url, width):
    long = [{**NEW_ITEMS[1], "suggestion": {**PROP, "newTask": {**NT, "name": "很长的任务名" * 10}}}]
    with open_page(browser, static_base_url, long, width=width) as (h, _stub):
        page = h.page
        page.locator(LI).wait_for()
        assert overflowing(page, "#suggest-panel") == []
        assert page.locator(LI + " .suggest-newname").bounding_box()["height"] >= 44
