"""「忽略并记住」与「被忽略任务」（ignores.js + suggestions.js，nexus-core v2.22）。

同本套件其余文件：真浏览器，接口全在浏览器侧桩掉。IgnoreStub 是个有状态的小后端：POST 建规则时顺手把匹配的待确认项
从 SuggestStub 里拿掉（服务端的行为），DELETE 删规则。判据落在行为上：发了什么、面板长什么样、重拉后列表变成什么。
"""

from __future__ import annotations

import contextlib
import json
from typing import Any, Iterator

from conftest import open_page as _open, overflowing
from playwright.sync_api import Browser, Page, Route
from test_collections import MIXED, seg
from test_suggestions import SuggestStub

RULE = {"id": "ig_1", "app": "chrome", "titleContains": "银行", "createdAt": "2026-10-09T10:30:00+08:00",
        "hits": 3, "seconds": 700, "lastHitAt": "2026-10-09T11:00:00+08:00"}


class IgnoreStub:
    """items = None → 404（老后端）。"""

    def __init__(self, items: list[dict] | None, sugs: SuggestStub | None = None) -> None:
        self.items = json.loads(json.dumps(items)) if items is not None else None
        self.sugs, self.calls, self.fail = sugs, [], False

    def route(self, route: Route) -> None:
        req = route.request
        if self.items is None:
            route.fulfill(status=404, content_type="application/json", body='{"detail":"Not Found"}')
            return
        if req.method == "GET":
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"total": len(self.items), "items": self.items}))
            return
        body = json.loads(req.post_data) if req.post_data else None
        self.calls.append((req.method, req.url.split("/ignores")[1], body))
        if self.fail:
            route.fulfill(status=422, content_type="application/json",
                          body=json.dumps({"detail": "忽略规则最多 200 条"}, ensure_ascii=False))
            return
        if req.method == "DELETE":
            self.items = [r for r in self.items if r["id"] != req.url.rsplit("/", 1)[1]]
            route.fulfill(status=204, body="")
            return
        title = body.get("titleContains")
        hit = [i for i in (self.sugs.items if self.sugs else [])
               if i["app"].lower() == body["app"].lower() and (not title or title.lower() in i["title"].lower())]
        if self.sugs:
            self.sugs.items = [i for i in self.sugs.items if i not in hit]
        self.items.append({"id": f"ig_{len(self.items) + 1}", "app": body["app"], "titleContains": title,
                           "createdAt": "2026-10-09T12:00:00+08:00", "hits": len(hit), "seconds": 0, "lastHitAt": None})
        route.fulfill(status=201, content_type="application/json",
                      body=json.dumps({**self.items[-1], "created": True, "removed": len(hit)}))


@contextlib.contextmanager
def open_ignores(browser: Browser, base: str, items, rules, *, width: int = 1100) -> Iterator[tuple[Page, SuggestStub, IgnoreStub]]:
    sugs = SuggestStub(items)
    ign = IgnoreStub(rules, sugs)
    routes = {r"/api/core/activity/suggestions([/?]|$)": sugs.route, r"/api/core/activity/ignores": ign.route}
    with _open(browser, base, routes=routes, width=width) as page:
        page.wait_for_selector(".suggest-coll")
        yield page, sugs, ign


def test_panel_lists_rules_with_counters_and_cancel_deletes_the_rule(browser, static_base_url) -> None:
    with open_ignores(browser, static_base_url, MIXED, [RULE]) as (page, _sugs, ign):
        page.wait_for_selector("#ignores-panel:not([hidden])")
        assert page.text_content("#ign-title") == "被忽略任务 (1)"
        assert page.get_attribute("#ignores-panel details", "open") is None  # 默认收着
        page.click("#ign-title")
        assert page.text_content(".ign-what") == "chrome · 标题含“银行”"
        assert page.text_content(".ign-meta") == "自 10-09 10:30 起 · 已忽略 3 条 · 12 分"  # 700 秒 ≈ 12 分；没有任何窗口标题
        page.click(".ign-undo")
        assert ign.calls == [("DELETE", "/ig_1", None)]
        page.wait_for_selector("#ignores-panel", state="hidden")  # 一条都没有了：整块收起


def test_panel_hidden_without_rules_or_on_old_backend(browser, static_base_url) -> None:
    for rules in ([], None):
        with open_ignores(browser, static_base_url, MIXED, rules) as (page, _s, _i):
            page.wait_for_function("window.assistantIgnores && window.assistantIgnores.supported !== undefined")
            assert page.is_hidden("#ignores-panel")
        if rules is None:  # 老后端：「忽略并记住」按钮也不出现
            with open_ignores(browser, static_base_url, MIXED, None) as (page, _s, _i):
                page.wait_for_function("window.assistantIgnores.supported === false")
                page.wait_for_selector(".suggest-coll")
                assert page.query_selector(".suggest-ignore-btn") is None


def test_ignore_a_window_by_app_clears_it_from_the_pending_list(browser, static_base_url) -> None:
    with open_ignores(browser, static_base_url, MIXED, []) as (page, sugs, ign):
        chrome = page.locator("li.suggest-item", has_text="chrome · docs")
        chrome.locator(".suggest-ignore-btn").click()
        assert chrome.locator(".suggest-ignore-opt").all_text_contents() == ["忽略 chrome 的所有窗口", "只忽略标题含下面这段文字的"]
        chrome.locator(".suggest-ignore-opt").first.click()
        page.wait_for_function("document.querySelector('#suggest-message').textContent.includes('已忽略并记住')")
        assert ign.calls == [("POST", "", {"app": "chrome"})]
        assert "清掉 1 条待确认" in page.text_content("#suggest-message")
        assert all(i["app"] != "chrome" for i in sugs.items)
        page.wait_for_selector("li.suggest-item:has-text('chrome · docs')", state="detached")  # 重拉后列表里没有了
        page.wait_for_selector("#ignores-panel:not([hidden])")  # 被忽略任务里出现这一条
        assert page.text_content("#ignores-count") == "(1)"


def test_ignore_by_title_sends_the_edited_fragment_not_the_whole_title(browser, static_base_url) -> None:
    items = [seg(1, "✳ 银行转账-私密页面", app="chrome"), seg(2, "新闻", app="chrome")]
    with open_ignores(browser, static_base_url, items, []) as (page, sugs, ign):
        row = page.locator("li.suggest-item", has_text="银行转账")
        row.locator(".suggest-ignore-btn").click()
        field = row.locator(".suggest-ignore-text")
        assert field.input_value() == "银行转账-私密页面"  # 预填（转圈符号去掉），人可以改
        assert "只有你能看到" in row.locator(".suggest-ignore-hint").text_content()
        assert "你在看" in row.locator(".suggest-ignore-warn").text_content()  # 整个程序忽略的后果说在选项旁边
        field.fill("银行")
        row.locator(".suggest-ignore-opt", has_text="标题含").click()
        page.wait_for_function("document.querySelector('#suggest-message').textContent.includes('已忽略并记住')")
        assert ign.calls == [("POST", "", {"app": "chrome", "titleContains": "银行"})]


def test_ignore_by_title_sends_the_normalised_title_and_cancel_restores_the_button(browser, static_base_url) -> None:
    items = [seg(1, "✳ 银行转账", app="chrome"), seg(2, "新闻", app="chrome")]
    with open_ignores(browser, static_base_url, items, []) as (page, sugs, ign):
        row = page.locator("li.suggest-item", has_text="银行转账")
        row.locator(".suggest-ignore-btn").click()
        row.locator(".suggest-ignore-cancel").click()
        assert row.locator(".suggest-ignore-btn").is_visible() and ign.calls == []
        row.locator(".suggest-ignore-btn").click()
        row.locator(".suggest-ignore-opt", has_text="标题含").click()  # 开头的转圈符号不进规则
        page.wait_for_function("document.querySelector('#suggest-message').textContent.includes('已忽略并记住')")
        assert ign.calls == [("POST", "", {"app": "chrome", "titleContains": "银行转账"})]
        assert [i["title"] for i in sugs.items] == ["新闻"]


def test_ignore_a_collection_makes_one_app_rule_per_distinct_app(browser, static_base_url) -> None:
    with open_ignores(browser, static_base_url, MIXED, []) as (page, sugs, ign):
        coll = page.locator('.suggest-coll[data-key="ai:claude code · cockpit"]')
        coll.locator(".suggest-coll-act .suggest-ignore-btn").click()
        coll.locator(".suggest-coll-act .suggest-ignore-opt").click()  # 只有一个选项：这些窗口所在程序的所有窗口（标题一个都不带进规则）
        page.wait_for_function("document.querySelector('#suggest-message').textContent.includes('已忽略并记住')")
        assert [c[2] for c in ign.calls] == [{"app": "kitty"}]
        assert [i["title"] for i in sugs.items] == ["docs"]
        page.wait_for_selector('.suggest-coll[data-key="ai:claude code · cockpit"]', state="detached")  # 整个集合没了


def test_failure_is_reported_and_nothing_pretends_to_succeed(browser, static_base_url) -> None:
    with open_ignores(browser, static_base_url, MIXED, []) as (page, sugs, ign):
        ign.fail = True
        row = page.locator("li.suggest-item", has_text="chrome · docs")
        row.locator(".suggest-ignore-btn").click()
        row.locator(".suggest-ignore-opt").first.click()
        page.wait_for_function("document.querySelector('#suggest-message').textContent.includes('没有记住')")
        assert "忽略规则最多 200 条" in page.text_content("#suggest-message")
        assert any(i["app"] == "chrome" for i in sugs.items)


def test_narrow_screen_does_not_scroll_sideways(browser, static_base_url) -> None:
    with open_ignores(browser, static_base_url, MIXED, [RULE], width=360) as (page, _s, _i):
        page.click("#ign-title")
        page.locator("li.suggest-item", has_text="chrome · docs").locator(".suggest-ignore-btn").click()
        assert overflowing(page, "#suggest-panel") == [] and overflowing(page, "#ignores-panel") == []


def test_panel_wording_is_a_filter_not_an_erase_claim(browser, static_base_url) -> None:
    with open_ignores(browser, static_base_url, MIXED, [RULE]) as (page, _sugs, _ign):
        page.wait_for_selector("#ignores-panel:not([hidden])")
        text = page.text_content("#ignores-panel")
        assert "已经记下的不受影响" in text and "不再计时、不进圆环和泳道" in text
        assert "不再保存" not in text and "清理" not in text   # 不承诺删除标题
