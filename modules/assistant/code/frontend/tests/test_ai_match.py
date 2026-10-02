"""「让 AI 匹配」+ 是 / 否（suggestions.js，nexus-core v2.7「AI 匹配」，仓主 2026-10-02）。

同本套件其余文件：真浏览器，/api/agent/ 与 /api/core/activity/suggestions 全在浏览器侧桩掉。
判据落在行为上：按钮在不在、发了哪一轮对话、答完列表有没有重拉、是 / 否各发了什么请求、条目留没留下。
"""

from __future__ import annotations

import contextlib
import json
from typing import Iterator

import pytest
from conftest import open_page as _open, overflowing
from playwright.sync_api import Browser, Page, Route
from test_chat import ChatStub
from test_suggestions import ITEMS, SuggestStub

# 助理配好的一条：reason 是模型写的，页面只许当文本
AI = {"taskId": "t_read", "confidence": 0.7, "reason": "标题里有 <i>视奏</i>", "classifier": "assistant"}
AI_ITEMS = [ITEMS[0], ITEMS[1], {**ITEMS[2], "suggestion": AI}]
LI = 'li[data-id="sug_c"]'


@contextlib.contextmanager
def open_page(browser: Browser, base: str, items, *, chat: ChatStub | None = None, matches_on_turn: bool = False,
              width: int = 1100, theme: str | None = None) -> Iterator[tuple[Page, SuggestStub, ChatStub | None]]:
    sug = SuggestStub(items)
    routes = {r"/api/core/activity/suggestions([/?]|$)": sug.route}
    if chat is not None:
        def chat_route(route: Route) -> None:
            # 助理在这一轮里经 MCP 配了任务：等答完页面重拉时才看得到
            if matches_on_turn and route.request.method == "POST" and route.request.url.endswith("/messages"):
                next(i for i in sug.items if i["id"] == "sug_c")["suggestion"] = dict(AI)
            chat.route(route)
        routes[r"/api/agent/"] = chat_route
    with _open(browser, base, routes=routes, width=width, theme=theme) as page:
        page.wait_for_selector("#suggest-list li")
        yield page, sug, chat


@pytest.mark.parametrize("chat", [None, ChatStub(health={"status": "ok", "configured": False}), ChatStub(health=502)])
def test_button_hidden_without_configured_chat(browser, static_base_url, chat):
    with open_page(browser, static_base_url, ITEMS, chat=chat) as (page, _sug, _chat):
        page.wait_for_selector("#suggest-list li")
        page.wait_for_timeout(400)
        assert page.is_hidden("#suggest-ai-match")
        assert page.is_visible("#suggest-confirm-all")   # 其余照常


def test_button_starts_a_turn_and_list_refreshes_when_done(browser, static_base_url):
    chat = ChatStub()
    chat.hold_reply = True
    with open_page(browser, static_base_url, ITEMS, chat=chat, matches_on_turn=True) as (page, sug, _):
        page.wait_for_selector("#suggest-ai-match:not([hidden]):not([disabled])")
        assert page.locator(LI + " select").count() == 1 and "没有建议" in page.inner_text(LI)
        page.click("#suggest-ai-match")
        # 进行中：按钮禁用并说明在干什么；对话面板里看得到这一轮
        page.wait_for_function("() => document.querySelector('#suggest-ai-match').textContent === 'AI 正在匹配…'")
        assert page.is_disabled("#suggest-ai-match")
        assert "AI 对话" in page.inner_text("#suggest-message")
        sent = [c for c in chat.calls if c[0] == "POST" and c[1].endswith("/messages")]
        assert len(sent) == 1 and "匹配待确认的活动" in sent[0][2]["text"]
        chat.hold_reply = False
        chat.hold.pop().fulfill(status=200, content_type="text/event-stream", body=chat.reply)

        # 答完：列表重拉，助理配的那条变成「AI 建议 + 是 / 否」，不出下拉
        page.wait_for_selector(LI + ".is-ai")
        li = page.locator(LI)
        assert li.locator(".suggest-hint").inner_text() == "AI 建议：练琴区 / 吉他练习 / 曲目视奏 · 把握 70% · 标题里有 <i>视奏</i>"
        assert li.locator("i").count() == 0 and li.locator("select").count() == 0
        assert li.locator(".suggest-what").inner_text() == "slack · x"
        assert [b.inner_text() for b in li.locator("button").all()] == ["是 ✓", "否 ✗"]
        assert page.inner_text("#suggest-message") == "AI 给 1 条配了任务，逐条点「是」或「否」。"
        page.wait_for_function("() => document.querySelector('#suggest-ai-match').textContent === '让 AI 匹配'")
        assert page.is_enabled("#suggest-ai-match")
        assert sug.posts == []   # 页面自己什么都没写：配任务是助理经 MCP 做的
        # 规则给的建议还是原来的「确认 / 忽略」
        assert [b.inner_text() for b in page.locator('li[data-id="sug_a"] button').all()] == ["确认", "忽略"]


def test_turn_that_matches_nothing_says_so(browser, static_base_url):
    with open_page(browser, static_base_url, ITEMS, chat=ChatStub()) as (page, _sug, _):
        page.wait_for_selector("#suggest-ai-match:not([hidden]):not([disabled])")
        page.click("#suggest-ai-match")
        page.wait_for_function("() => document.querySelector('#suggest-message').textContent.includes('没配上')")
        assert page.locator("#suggest-list li.is-ai").count() == 0


def test_yes_confirms_with_the_matched_task(browser, static_base_url):
    with open_page(browser, static_base_url, AI_ITEMS) as (page, sug, _):
        page.locator(LI + " .suggest-confirm").focus()
        page.keyboard.press("Enter")   # 键盘可达：原生按钮
        page.wait_for_function("() => !document.querySelector('li[data-id=\"sug_c\"]')")
        assert sug.posts == [("confirm", "sug_c", {"taskId": "t_read"})]


def test_no_unmatches_and_item_stays_with_manual_picker(browser, static_base_url):
    with open_page(browser, static_base_url, AI_ITEMS) as (page, sug, _):
        page.locator(LI + " .suggest-no").click()
        page.wait_for_selector(LI + ":not(.is-ai) select")
        assert sug.posts == [("unmatch", "sug_c", {"taskId": "t_read"})]
        li = page.locator(LI)
        assert "AI 的建议已否掉" in li.inner_text()
        assert [b.inner_text() for b in li.locator("button").all()] == ["确认", "忽略"]
        assert li.locator(".suggest-confirm").is_disabled()   # 得先自己挑任务
        li.locator("select").select_option("t_word")
        li.locator(".suggest-confirm").click()
        page.wait_for_function("() => !document.querySelector('li[data-id=\"sug_c\"]')")
        assert sug.posts[-1] == ("confirm", "sug_c", {"taskId": "t_word"})


def test_bulk_confirm_includes_assistant_matches_above_threshold(browser, static_base_url):
    with open_page(browser, static_base_url, AI_ITEMS) as (page, sug, _):
        assert page.inner_text("#suggest-confirm-all").endswith("· 1")       # 80%：只有规则那条 0.9
        page.fill("#suggest-threshold", "70")
        assert page.inner_text("#suggest-confirm-all").endswith("· 2")       # + 助理的 0.7
        page.click("#suggest-confirm-all")
        page.wait_for_function("() => document.querySelectorAll('#suggest-list li').length === 1")
        assert sug.posts == [("confirm", "sug_a", {"taskId": "t_word"}), ("confirm", "sug_c", {"taskId": "t_read"})]


def test_assistant_match_to_deleted_task_falls_back_to_picker(browser, static_base_url):
    gone = [{**ITEMS[2], "suggestion": {**AI, "taskId": "t_gone"}}]
    with open_page(browser, static_base_url, gone) as (page, _sug, _):
        li = page.locator(LI)
        assert "任务已不存在" in li.inner_text() and li.locator("select").count() == 1
        assert li.locator(".suggest-confirm").is_disabled()


@pytest.mark.parametrize("width", [320, 390])
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_narrow_no_horizontal_scroll(browser, static_base_url, width, theme):
    long = {**AI, "reason": "很长的理由" * 13}   # 195 字节，接近上限
    items = [{**ITEMS[2], "suggestion": long, "title": "a-very-long-window-title-" * 8}]
    with open_page(browser, static_base_url, items, chat=ChatStub(), width=width, theme=theme) as (page, _sug, _):
        page.wait_for_selector("#suggest-ai-match:not([hidden])")
        assert overflowing(page, "#suggest-panel") == []
        # 是 / 否够大、够得着（≥ 44px 高）
        for sel in (".suggest-confirm", ".suggest-no"):
            assert page.locator(LI + " " + sel).bounding_box()["height"] >= 44
