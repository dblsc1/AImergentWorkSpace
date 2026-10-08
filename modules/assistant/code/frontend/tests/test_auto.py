"""「自动记录」面板（auto.js，nexus-core v2.14「自动跟踪进行中的任务」）。

同本套件其余文件：真浏览器、接口全在浏览器侧桩掉。判据落在行为上：列出了什么、改归属发了什么请求、
失败怎么报、面板在不在、窗口标题只当文本、窄屏会不会横滚。
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from conftest import open_page, overflowing
from playwright.sync_api import Route

EVIL = '<img src=x onerror="window.__pwned=1">'


def _items() -> list[dict]:
    return [
        {"id": "seg_2", "startAt": "2026-10-08T10:30:00+08:00", "endAt": "2026-10-08T10:42:00+08:00",
         "durationSeconds": 720, "app": "code", "title": "garden — " + EVIL, "eventId": "evt_2",
         "taskId": "t_word", "projectId": "p_eng", "reassigned": False},
        {"id": "seg_1", "startAt": "2026-10-08T09:05:00+08:00", "endAt": "2026-10-08T09:05:20+08:00",
         "durationSeconds": 20, "app": "blender", "title": None, "eventId": "evt_1",
         "taskId": "t_unc_p_book", "projectId": "p_book", "reassigned": True},
    ]


class Stub:
    def __init__(self, items: list[dict] | None) -> None:
        self.items = items                      # None = 端点 404（后端早于 v2.14）
        self.posts: list[tuple[str, Any]] = []
        self.gets = 0
        self.fail = False

    def auto(self, route: Route) -> None:
        self.gets += 1
        if self.items is None:
            route.fulfill(status=404, content_type="application/json", body='{"detail":"Not Found"}')
            return
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps({"items": self.items}, ensure_ascii=False))

    def reassign(self, route: Route) -> None:
        event_id = route.request.url.rstrip("/").split("/")[-2]
        body = json.loads(route.request.post_data)
        self.posts.append((event_id, body))
        if self.fail:
            route.fulfill(status=409, content_type="application/json",
                          body=json.dumps({"detail": "正被并发改挂 <u>x</u>"}, ensure_ascii=False))
            return
        for item in self.items:
            if item["eventId"] == event_id:
                item.update(taskId=body.get("taskId") or "t_unc_" + body["projectId"], reassigned=True)
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps({"sessionEventId": event_id, "duplicate": False, "seq": 2}))


def opened(browser, base, stub: Stub, width: int = 1100):
    return open_page(browser, base, width=width, routes={
        r"/api/core/activity/auto$": stub.auto, r"/api/core/sessions/[^/]+/reassign$": stub.reassign})


def _idle(page) -> None:
    page.wait_for_function("() => { const s = document.querySelector('.auto-pick'); return !s || !s.disabled; }")


@pytest.mark.parametrize("items", [None, []])
def test_panel_hidden_when_nothing_recorded(browser, static_base_url, items):
    errors: list[str] = []
    stub = Stub(items)
    with opened(browser, static_base_url, stub) as page:
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.wait_for_timeout(300)
        assert stub.gets == 1 and page.is_hidden("#auto-panel")
    assert errors == []


def test_lists_rows_with_target_and_text_only(browser, static_base_url):
    stub = Stub(_items())
    with opened(browser, static_base_url, stub) as page:
        page.wait_for_selector("#auto-panel:not([hidden])")
        assert page.inner_text("#auto-count") == "2"
        rows = page.locator("#auto-list > li")
        assert [r.get_attribute("data-id") for r in rows.all()] == ["evt_2", "evt_1"]
        assert rows.nth(0).locator(".unc-what").inner_text() == "10:30 · 12 分 · code · garden — " + EVIL
        assert rows.nth(0).locator(".auto-target").inner_text() == "→ 练琴区 / 吉他练习 / 音阶练习"
        assert rows.nth(0).locator(".suggest-badge").count() == 0
        # 项目的未分类桶（树里没带 unclassifiedTaskId 也认得）；没有标题的程序；不到一分钟；改过的带「已改」
        assert rows.nth(1).locator(".unc-what").inner_text() == "09:05 · 不到 1 分 · blender"
        assert rows.nth(1).locator(".auto-target").inner_text() == "→ 练琴区 / 考级计划 · 未分类"
        assert rows.nth(1).locator(".suggest-badge").inner_text() == "已改"
        assert page.locator("#auto-panel img").count() == 0
        assert page.evaluate("window.__pwned") is None
        assert stub.posts == []


def test_reassign_to_task_and_to_project(browser, static_base_url):
    stub = Stub(_items())
    with opened(browser, static_base_url, stub) as page:
        page.wait_for_selector("#auto-panel:not([hidden])")
        page.evaluate("() => { window.__t = 0; document.addEventListener('honeycomb:timer-changed', () => window.__t++); }")
        page.select_option('li[data-id="evt_2"] .auto-pick', "t:t_read")
        page.wait_for_selector("#auto-message:not([hidden])")
        _idle(page)
        assert stub.posts == [("evt_2", {"taskId": "t_read"})]
        assert page.inner_text("#auto-message") == "已改归属 → 练琴区 / 吉他练习 / 曲目视奏"
        assert page.inner_text('li[data-id="evt_2"] .auto-target') == "→ 练琴区 / 吉他练习 / 曲目视奏"
        assert page.inner_text('li[data-id="evt_2"] .suggest-badge') == "已改"
        page.select_option('li[data-id="evt_2"] .auto-pick', "p:p_book")
        page.wait_for_function("() => document.getElementById('auto-message').textContent.includes('考级计划')")
        assert stub.posts[1] == ("evt_2", {"projectId": "p_book"})
        assert page.inner_text('li[data-id="evt_2"] .auto-target') == "→ 练琴区 / 考级计划 · 未分类"
        assert page.evaluate("window.__t") == 0          # 改归属不是计时


def test_reassign_failure_keeps_row_and_shows_reason_as_text(browser, static_base_url):
    stub = Stub(_items())
    stub.fail = True
    with opened(browser, static_base_url, stub) as page:
        page.wait_for_selector("#auto-panel:not([hidden])")
        page.select_option('li[data-id="evt_1"] .auto-pick', "t:t_word")
        page.wait_for_selector("#auto-message.is-error")
        assert page.inner_text("#auto-message") == "没改成：正被并发改挂 <u>x</u>"
        assert page.locator("#auto-message u").count() == 0
        assert page.inner_text('li[data-id="evt_1"] .auto-target') == "→ 练琴区 / 考级计划 · 未分类"
        _idle(page)
        assert page.input_value('li[data-id="evt_1"] .auto-pick') == ""


@pytest.mark.parametrize("width", [320, 390])
def test_narrow_screen_no_horizontal_scroll(browser, static_base_url, width):
    items = _items()
    items[0]["title"] = "很长的窗口标题" * 30 + "averyveryverylongunbrokenwindowtitle" * 6
    with opened(browser, static_base_url, Stub(items), width=width) as page:
        page.wait_for_selector("#auto-panel:not([hidden])")
        assert overflowing(page, "#auto-panel") == []
        assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")


def test_pure_functions(browser, static_base_url):
    with opened(browser, static_base_url, Stub([])) as page:
        assert page.evaluate("assistantAuto.rowLabel({startAt: 'x', durationSeconds: 90, app: 'a', title: ''})") \
            == "2 分 · a"
        assert page.evaluate("assistantAuto.targetLabel(null, {taskId: 't_x', projectId: 'p'})") == "t_x"


def test_segments_recorded_by_an_ai_recognised_rule_are_marked(browser, static_base_url):
    """v2.15：GET activity/auto 的 ai: true = 按 AI 认的那条规则记下的。标出来，改归属照旧。"""
    items = _items()
    items[0]["ai"], items[1]["ai"] = True, False
    stub = Stub(items)
    with opened(browser, static_base_url, stub) as page:
        page.wait_for_selector("#auto-panel:not([hidden])")
        rows = page.locator("#auto-list > li")
        assert rows.nth(0).locator(".auto-ai").inner_text() == "AI 认的"
        assert rows.nth(1).locator(".auto-ai").count() == 0
        rows.nth(0).locator(".auto-pick").select_option("p:p_book")
        _idle(page)
        assert stub.posts == [("evt_2", {"projectId": "p_book"})]


def test_rows_sorted_longest_first_stable(browser, static_base_url):
    items = list(reversed(_items()))      # 后端给的顺序：短的在前
    items.append({**items[0], "id": "seg_3", "eventId": "evt_3", "durationSeconds": 20,
                  "startAt": "2026-10-08T11:00:00+08:00"})
    stub = Stub(items)
    with opened(browser, static_base_url, stub) as page:
        page.wait_for_selector(".auto-row")
        ids = lambda: page.eval_on_selector_all(".auto-row", "els => els.map(e => e.dataset.id)")  # noqa: E731
        assert ids() == ["evt_2", "evt_1", "evt_3"]    # 一样长的保持后端的顺序
        page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
        page.wait_for_timeout(300)
        assert ids() == ["evt_2", "evt_1", "evt_3"]
