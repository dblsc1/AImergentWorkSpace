"""「AI助理」页整体：四块的顺序、回顾入口、老后端下不出错、窄屏不横滚。"""

from __future__ import annotations

import json

import pytest
from conftest import open_page, overflowing


def test_sections_in_order_and_review_links(browser, static_base_url):
    with open_page(browser, static_base_url) as page:
        ids = page.eval_on_selector_all("main > section", "ss => ss.map(s => s.id)")
        assert ids == ["chat-panel", "suggest-panel", "unclassified-panel", "auto-panel", "settings-panel", "tokens-panel", "review-panel"]
        assert page.title() == "AI助理"
        assert page.get_attribute("#review-lanes", "href") == "../ring/#lanes-panel"
        assert page.get_attribute("#review-hive", "href") == "../hive/"
        assert page.is_visible("#review-lanes") and page.is_visible("#review-hive")
        # 「分类规则」在检测设置面板里；老后端（404）只剩一句说明，没有编辑器
        page.wait_for_selector("#rules-note:not([hidden])")
        assert "nexus-core v2.6" in page.inner_text("#rules-note")
        assert page.is_hidden("#rules-editor") and page.is_hidden("#rules-draft")


def test_everything_404_leaves_a_quiet_page(browser, static_base_url):
    """老后端 / 没装聊天：三块数据面板要么不出现、要么说明原因，控制台不报错。"""
    errors: list[str] = []
    with open_page(browser, static_base_url) as page:
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.wait_for_selector("#det-note:not([hidden])")
        assert page.is_visible("#det-rules")
        assert page.is_hidden("#chat-panel") and page.is_hidden("#suggest-panel")
        assert page.is_hidden("#unclassified-panel")
        assert page.is_visible("#review-panel")
        assert page.is_hidden("#tokens-panel")  # 认证服务不支持令牌列表（404）：整块不出现
    assert errors == []


def test_review_link_hidden_when_page_not_installed(browser, static_base_url):
    nav = {"home": "/", "timer": None, "tabs": [{"href": "/hive/", "label": "任务"},
                                                  {"href": "/assistant/", "label": "AI助理"}]}
    init = f"window.HONEYCOMB_BASE='/';window.HONEYCOMB_NAV={json.dumps(nav)};"
    with open_page(browser, static_base_url, init=init) as page:
        assert page.is_hidden("#review-lanes") and page.is_visible("#review-hive")


@pytest.mark.parametrize("width", [320, 390])
def test_whole_page_no_horizontal_scroll(browser, static_base_url, width):
    with open_page(browser, static_base_url, width=width) as page:
        page.wait_for_timeout(300)
        assert overflowing(page, "main") == []
        assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
