"""「待确认」面板（ring-suggestions.js，nexus-core v2.2 活动建议）。

同本套件其余文件：真浏览器、接口全在浏览器侧桩掉，一个字节都不写库。
判据只落在行为上：发了什么请求、哪些条目留下、面板在不在、窄屏会不会横滚。
"""

from __future__ import annotations

import contextlib
import json
import re
from typing import Any, Iterator

import pytest
from conftest import CURRENT_IDLE, PAGE_NAME, TREE, RingHarness, _install_stub_routes
from playwright.sync_api import Browser, Route

ITEMS = [
    {"id": "sug_a", "deviceId": "dev_x", "startAt": "2026-09-26T11:05:00+08:00",
     "endAt": "2026-09-26T12:07:00+08:00", "durationSeconds": 3600, "app": "code",
     "title": "<b>plot.gd</b> — garden — Visual Studio Code",
     "suggestion": {"taskId": "t_word", "confidence": 0.9, "reason": "规则 #1", "classifier": "rules"},
     "status": "pending"},
    {"id": "sug_b", "deviceId": "dev_x", "startAt": "2026-09-26T09:00:00+08:00",
     "endAt": "2026-09-26T09:30:00+08:00", "durationSeconds": 1500, "app": "chrome", "title": "",
     "suggestion": {"taskId": "t_read", "confidence": 0.6, "reason": "", "classifier": "service"},
     "status": "pending"},
    {"id": "sug_c", "deviceId": "dev_x", "startAt": "2026-09-25T23:50:00+08:00",
     "endAt": "2026-09-26T00:20:00+08:00", "durationSeconds": 20, "app": "slack", "title": "x",
     "suggestion": {"taskId": None, "confidence": 0, "reason": "", "classifier": "rules"},
     "status": "pending"},
]


class SuggestStub:
    """GET 返回 ``self.items``（None = 404，模拟老后端）；confirm/dismiss 记下并从列表里拿掉。"""

    def __init__(self, items: list[dict] | None) -> None:
        self.items = json.loads(json.dumps(items)) if items is not None else None
        self.posts: list[tuple[str, str, Any]] = []
        self.fail_ids: set[str] = set()
        self.get_status = 200
        self.extra_total = 0  # total 比 items 多出来的条数（模拟分页之外还有更早的）  # 非 200 = 列表接口出错（不是 404 那种「老后端」）

    def route(self, route: Route) -> None:
        req = route.request
        if req.method == "GET":
            if self.get_status != 200:
                route.fulfill(status=self.get_status, body="boom")
                return
            if self.items is None:
                route.fulfill(status=404, content_type="application/json", body='{"detail":"Not Found"}')
                return
            body = {"total": len(self.items) + self.extra_total, "items": self.items}
            route.fulfill(status=200, content_type="application/json", body=json.dumps(body, ensure_ascii=False))
            return
        parts = req.url.split("?")[0].rstrip("/").split("/")
        sug_id, action = parts[-2], parts[-1]
        self.posts.append((action, sug_id, json.loads(req.post_data) if req.post_data else None))
        if sug_id in self.fail_ids:
            route.fulfill(status=404, content_type="application/json",
                          body=json.dumps({"detail": "任务不存在：'t_gone'"}, ensure_ascii=False))
            return
        self.items = [i for i in self.items if i["id"] != sug_id]
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps({"id": sug_id, "status": action + "ed"}))


@contextlib.contextmanager
def open_page(browser: Browser, base: str, items, *, width: int = 1100,
              theme: str | None = None) -> Iterator[tuple[RingHarness, SuggestStub]]:
    context = browser.new_context(viewport={"width": width, "height": 900}, timezone_id="Asia/Shanghai")
    page = context.new_page()
    harness = RingHarness(page, CURRENT_IDLE)
    _install_stub_routes(page, harness)
    stub = SuggestStub(items)
    page.route(re.compile(r"/api/core/activity/suggestions([/?]|$)"), stub.route)
    events: list[str] = []
    page.expose_function("__timerChanged", lambda: events.append("x"))
    page.add_init_script("window.addEventListener('honeycomb:timer-changed', () => window.__timerChanged())")
    page.goto(f"{base}/{PAGE_NAME}")
    page.wait_for_selector("#task-select", state="attached")
    if theme:  # 线上由顶栏 navbar.js 设这个属性；本套件不注入顶栏，直接设
        page.evaluate(f"document.documentElement.dataset.theme = {theme!r}")
    stub.timer_events = events  # type: ignore[attr-defined]
    try:
        yield harness, stub
    finally:
        context.close()


def _ids(page) -> list[str]:
    return page.eval_on_selector_all("#suggest-list li", "els => els.map(e => e.dataset.id)")


def test_lists_items_with_path_confidence_and_safe_text(browser, static_base_url):
    with open_page(browser, static_base_url, ITEMS) as (h, _stub):
        page = h.page
        page.wait_for_selector("#suggest-list li")
        assert page.is_visible("#suggest-panel")
        assert _ids(page) == ["sug_a", "sug_b", "sug_c"]
        first = page.locator("#suggest-list li").first
        assert "09-26 11:05–12:07" in first.inner_text()
        assert "60 分" in first.inner_text()
        assert "练琴区 / 吉他练习 / 音阶练习" in first.inner_text() and "90%" in first.inner_text()
        # 别的机器来的标题只当文本，不当 HTML
        assert first.locator(".suggest-what").inner_text().startswith("code · <b>plot.gd</b>")
        assert first.locator("b").count() == 0
        # 跨天带结束日期；没建议的提示人挑、确认按钮禁用
        third = page.locator("#suggest-list li").nth(2)
        assert "09-25 23:50–09-26 00:20" in third.inner_text()
        assert "没有建议" in third.inner_text()
        assert third.locator(".suggest-confirm").is_disabled()
        assert page.inner_text("#suggest-count") == "3"
        assert page.is_hidden("#suggest-empty")


def test_confirm_sends_chosen_task_and_refreshes_ring_without_timer_event(browser, static_base_url):
    with open_page(browser, static_base_url, ITEMS) as (h, stub):
        page = h.page
        page.wait_for_selector("#suggest-list li")
        polls = h.current_polls
        page.locator('li[data-id="sug_b"] select').select_option("t_legacy")
        page.locator('li[data-id="sug_b"] .suggest-confirm').click()
        page.wait_for_function("() => !document.querySelector('li[data-id=\"sug_b\"]')")
        assert stub.posts == [("confirm", "sug_b", {"taskId": "t_legacy"})]
        assert _ids(page) == ["sug_a", "sug_c"]
        page.wait_for_timeout(300)
        assert h.current_polls > polls  # 圆环刷新了
        assert stub.timer_events == []  # 确认不是计时，不叫顶栏


def test_dismiss(browser, static_base_url):
    with open_page(browser, static_base_url, ITEMS) as (h, stub):
        page = h.page
        page.wait_for_selector("#suggest-list li")
        page.locator('li[data-id="sug_c"] .suggest-dismiss').click()
        page.wait_for_function("() => !document.querySelector('li[data-id=\"sug_c\"]')")
        assert stub.posts == [("dismiss", "sug_c", None)]


def test_confirm_all_only_confident_items_with_task(browser, static_base_url):
    with open_page(browser, static_base_url, ITEMS) as (h, stub):
        page = h.page
        page.wait_for_selector("#suggest-list li")
        btn = page.locator("#suggest-confirm-all")
        assert "80%" in btn.inner_text() and btn.inner_text().endswith("· 1")
        page.fill("#suggest-threshold", "50")
        assert btn.inner_text().endswith("· 2")
        page.fill("#suggest-threshold", "0")
        assert btn.inner_text().endswith("· 2")  # 没 taskId 的那条永远不在「全部确认」里
        btn.click()
        page.wait_for_function("() => document.querySelectorAll('#suggest-list li').length === 1")
        assert [(a, i) for a, i, _ in stub.posts] == [("confirm", "sug_a"), ("confirm", "sug_b")]
        assert [b["taskId"] for _, _, b in stub.posts] == ["t_word", "t_read"]
        assert "已确认 2 条" in page.inner_text("#suggest-message")


def test_failure_keeps_item_and_shows_detail(browser, static_base_url):
    with open_page(browser, static_base_url, ITEMS) as (h, stub):
        stub.fail_ids.add("sug_a")
        page = h.page
        page.wait_for_selector("#suggest-list li")
        page.locator('li[data-id="sug_a"] .suggest-confirm').click()
        page.wait_for_selector("#suggest-message.is-error")
        assert "t_gone" in page.inner_text("#suggest-message")
        assert "sug_a" in _ids(page)


def test_buttons_recover_when_reload_fails(browser, static_base_url):
    with open_page(browser, static_base_url, ITEMS) as (h, stub):
        page = h.page
        page.wait_for_selector("#suggest-list li")
        stub.get_status = 500
        page.locator('li[data-id="sug_c"] .suggest-dismiss').click()
        page.wait_for_selector("#suggest-message.is-error")
        assert page.locator('li[data-id="sug_a"] .suggest-confirm').is_enabled()
        assert page.locator("#suggest-confirm-all").is_enabled()


def test_more_than_one_page_hint(browser, static_base_url):
    with open_page(browser, static_base_url, ITEMS) as (h, stub):
        page = h.page
        page.wait_for_selector("#suggest-list li")
        assert page.is_hidden("#suggest-more")
        stub.extra_total = 250
        page.locator('li[data-id="sug_c"] .suggest-dismiss').click()
        page.wait_for_selector("#suggest-more:not([hidden])")
        assert page.inner_text("#suggest-more") == "还有 250 条更早的待确认，先处理上面的"


def test_empty_state(browser, static_base_url):
    with open_page(browser, static_base_url, []) as (h, _stub):
        page = h.page
        page.wait_for_selector("#suggest-panel:not([hidden])")
        assert page.is_visible("#suggest-empty")
        assert page.locator("#suggest-confirm-all").is_disabled()


def test_hidden_when_backend_has_no_endpoint(browser, static_base_url):
    with open_page(browser, static_base_url, None) as (h, _stub):
        page = h.page
        page.wait_for_timeout(500)
        assert page.is_hidden("#suggest-panel")


@pytest.mark.parametrize("width", [320, 390])
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_narrow_no_horizontal_scroll(browser, static_base_url, width, theme):
    with open_page(browser, static_base_url, ITEMS, width=width, theme=theme) as (h, _stub):
        page = h.page
        page.wait_for_selector("#suggest-list li")
        overflow = page.evaluate(
            "() => [...document.querySelectorAll('#suggest-panel, #suggest-panel *')]"
            ".filter(e => e.getBoundingClientRect().right > document.documentElement.clientWidth + 0.5)"
            ".map(e => e.id || e.className)")
        assert overflow == []
        # 暗色主题下面板背景跟着 token 走，不是写死的浅色
        bg = page.eval_on_selector("#suggest-panel", "e => getComputedStyle(e).backgroundColor")
        assert bg == ("rgb(21, 28, 46)" if theme == "dark" else "rgb(255, 255, 255)")


def test_pure_helpers(browser, static_base_url):
    with open_page(browser, static_base_url, []) as (h, _stub):
        r = h.page.evaluate("""() => {
          const S = window.ringSuggestions;
          const tree = %s;
          return {
            path: S.taskPath(tree, 't_read'), gone: S.taskPath(tree, 't_nope'), none: S.taskPath(tree, null),
            elig: S.eligible([
              {id: 'a', suggestion: {taskId: 't', confidence: 0.8}},
              {id: 'b', suggestion: {taskId: 't', confidence: 0.79}},
              {id: 'c', suggestion: {taskId: null, confidence: 1}},
              {id: 'd', suggestion: {taskId: 't'}},
              {id: 'e'}], 0.8).map(i => i.id),
            eligTree: S.eligible([
              {id: 'a', suggestion: {taskId: 't_read', confidence: 0.9}},
              {id: 'b', suggestion: {taskId: 't_gone', confidence: 0.9}}], 0.8, tree).map(i => i.id),
          };
        }""" % json.dumps(TREE, ensure_ascii=False))
        assert r == {"path": "练琴区 / 吉他练习 / 曲目视奏", "gone": None, "none": None, "elig": ["a"],
                     "eligTree": ["a"]}
