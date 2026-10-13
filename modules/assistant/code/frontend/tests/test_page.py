"""「待批准」页与「自动记录」页整体：各块的顺序、两页互链、回顾入口、老后端下不出错、窄屏不横滚。"""

from __future__ import annotations

import json

import pytest
from conftest import AUTO_PAGE, open_page, overflowing


def test_sections_in_order_and_review_links(browser, static_base_url):
    with open_page(browser, static_base_url) as page:
        ids = page.eval_on_selector_all("main > section", "ss => ss.map(s => s.id)")
        assert ids == ["report-panel", "chat-panel", "rules-draft-panel", "suggest-panel", "unclassified-panel", "tokens-panel", "review-panel"]
        assert page.title() == "待批准"
        assert page.get_attribute("#review-lanes", "href") == "../ring/#lanes-panel"
        assert page.get_attribute("#review-hive", "href") == "../hive/"
        assert page.is_visible("#review-lanes") and page.is_visible("#review-hive")
        # 自动记录的东西不在这一页（在「自动记录」页）；草稿卡片没有草稿时是藏着的
        for gone in ("#settings-panel", "#det-form", "#det-health", "#det-restart", "#det-incidents", "#det-startlog",
                     "#det-rules", "#rules-list", "#rules-aidirect", "#rules-revert", "#auto-panel"):
            assert page.locator(gone).count() == 0, gone
        assert page.is_hidden("#rules-draft") and page.is_hidden("#rules-draft-panel")
        assert page.get_attribute("a[data-needs='auto/']", "href") == "../auto/"


def test_everything_404_leaves_a_quiet_page(browser, static_base_url):
    """老后端 / 没装聊天：三块数据面板要么不出现、要么说明原因，控制台不报错。"""
    errors: list[str] = []
    with open_page(browser, static_base_url) as page:
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.wait_for_timeout(300)
        assert page.is_hidden("#chat-panel") and page.is_hidden("#suggest-panel")
        assert page.is_hidden("#unclassified-panel")
        assert page.is_visible("#review-panel")
        assert page.is_hidden("#tokens-panel")  # 认证服务不支持令牌列表（404）：整块不出现
    assert errors == []


def test_review_link_hidden_when_page_not_installed(browser, static_base_url):
    nav = {"home": "/", "timer": None, "tabs": [{"href": "/hive/", "label": "任务"},
                                                  {"href": "/assistant/", "label": "待批准"}]}
    init = f"window.HONEYCOMB_BASE='/';window.HONEYCOMB_NAV={json.dumps(nav)};"
    with open_page(browser, static_base_url, init=init) as page:
        assert page.is_hidden("#review-lanes") and page.is_visible("#review-hive")


def test_auto_page_renders_its_blocks_and_links_back(browser, static_base_url):
    """「自动记录」页：检测设置（含设备状态）、分类规则、今天自动记下的，三块都在；待批准页的那些块这里没有。"""
    errors: list[str] = []
    with open_page(browser, static_base_url, page_name=AUTO_PAGE) as page:
        page.on("pageerror", lambda e: errors.append(str(e)))
        assert page.title() == "自动记录"
        ids = page.eval_on_selector_all("main > section", "ss => ss.map(s => s.id)")
        assert ids == ["settings-panel", "auto-panel"]
        for here in ("#det-device-row", "#det-health", "#det-restart", "#det-incidents", "#det-startlog", "#det-logup",
                     "#det-form", "[data-key=autoTrack]", "#det-rules", "#rules-list", "#rules-aidirect-field", "#rules-revert"):
            assert page.locator(here).count() == 1, here
        # 老后端（全 404）：设置面板在、说明在，规则只剩一句说明
        page.wait_for_selector("#det-note:not([hidden])")
        assert page.is_visible("#det-rules")
        page.wait_for_selector("#rules-note:not([hidden])")
        assert "nexus-core v2.6" in page.inner_text("#rules-note")
        assert page.is_hidden("#rules-editor")
        # 待批准页的东西不在这里
        for gone in ("#chat-panel", "#suggest-panel", "#report-panel", "#unclassified-panel", "#tokens-panel",
                     "#rules-draft", "#rules-draft-panel", "#rules-apply", "#ignores-panel"):
            assert page.locator(gone).count() == 0, gone
        assert page.get_attribute("a[data-needs='assistant/']", "href") == "../assistant/"
    assert errors == []


def test_cross_links_hidden_when_other_page_not_installed(browser, static_base_url):
    nav = {"home": "/", "timer": None, "tabs": [{"href": "/hive/", "label": "任务"}]}
    init = f"window.HONEYCOMB_BASE='/';window.HONEYCOMB_NAV={json.dumps(nav)};"
    with open_page(browser, static_base_url, init=init) as page:
        assert page.is_hidden("a[data-needs='auto/']")
    with open_page(browser, static_base_url, init=init, page_name=AUTO_PAGE) as page:
        assert page.is_hidden("a[data-needs='assistant/']")


@pytest.mark.parametrize("width", [320, 390])
def test_auto_page_no_horizontal_scroll(browser, static_base_url, width):
    with open_page(browser, static_base_url, width=width, page_name=AUTO_PAGE) as page:
        page.wait_for_timeout(300)
        assert overflowing(page, "main") == []
        assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")


@pytest.mark.parametrize("width", [320, 390])
def test_whole_page_no_horizontal_scroll(browser, static_base_url, width):
    with open_page(browser, static_base_url, width=width) as page:
        page.wait_for_timeout(300)
        assert overflowing(page, "main") == []
        assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
