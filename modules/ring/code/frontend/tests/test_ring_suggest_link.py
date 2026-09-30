"""计时页「N 条待确认 → AI助理」小链接（ring-suggest-link.js）。

待确认面板与聊天 2026-09-30 搬去「AI助理」页（modules/assistant，那边的测试跟着搬走了）；
这里只剩一个报个数的链接：0 条、404、没装 AI助理页时不出现。
"""

from __future__ import annotations

import contextlib
import json
import re
from typing import Iterator

import pytest
from conftest import CURRENT_IDLE, PAGE_NAME, RingHarness, _install_stub_routes
from playwright.sync_api import Browser

NAV_WITH = {"home": "/hive/", "timer": "/ring/", "tabs": [
    {"href": "/hive/", "label": "任务"}, {"href": "/ring/", "label": "计时"}, {"href": "/assistant/", "label": "AI助理"}]}
NAV_WITHOUT = {"home": "/", "timer": "/ring/", "tabs": [{"href": "/ring/", "label": "计时"}]}


@contextlib.contextmanager
def open_page(browser: Browser, base: str, status: int, total: int, nav: dict | None = None) -> Iterator[tuple]:
    context = browser.new_context(viewport={"width": 390, "height": 900})
    page = context.new_page()
    _install_stub_routes(page, RingHarness(page, CURRENT_IDLE))
    urls: list[str] = []

    def route(r):
        urls.append(r.request.url)
        r.fulfill(status=status, content_type="application/json",
                  body=json.dumps({"total": total, "items": []} if status == 200 else {"detail": "Not Found"}))

    page.route(re.compile(r"/api/core/activity/suggestions"), route)
    if nav is not None:
        page.add_init_script(f"window.HONEYCOMB_BASE='/';window.HONEYCOMB_NAV={json.dumps(nav)};")
    page.goto(f"{base}/{PAGE_NAME}")
    page.wait_for_selector("#task-select", state="attached")
    try:
        yield page, urls
    finally:
        context.close()


def test_link_shows_count_and_points_to_assistant(browser, static_base_url):
    with open_page(browser, static_base_url, 200, 3, NAV_WITH) as (page, urls):
        page.wait_for_selector("#suggest-link:not([hidden])")
        assert page.inner_text("#suggest-link") == "3 条待确认 → AI助理"
        assert page.get_attribute("#suggest-link", "href") == "../assistant/"
        assert all("status=pending" in u and "limit=1" in u for u in urls) and urls
        # 两块面板已经搬走，计时页上不再有
        assert page.locator("#suggest-panel, #chat-panel").count() == 0
        page.focus("#suggest-link")   # 键盘可达
        assert page.evaluate("document.activeElement.id") == "suggest-link"


@pytest.mark.parametrize("status,total,nav", [(200, 0, NAV_WITH), (404, 0, None), (500, 0, None), (200, 5, NAV_WITHOUT)])
def test_link_hidden(browser, static_base_url, status, total, nav):
    with open_page(browser, static_base_url, status, total, nav) as (page, _urls):
        page.wait_for_timeout(400)
        assert page.is_hidden("#suggest-link")


def test_link_visible_without_gateway_nav(browser, static_base_url):
    """没注入页签配置（直接打开文件、单测）时不猜，照常显示。"""
    with open_page(browser, static_base_url, 200, 1) as (page, _urls):
        page.wait_for_selector("#suggest-link:not([hidden])")
        assert page.inner_text("#suggest-link") == "1 条待确认 → AI助理"
