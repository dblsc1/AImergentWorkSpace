"""「分类规则」（rules.js，contracts/detector.rules.v1）。

判据落在行为上：列表照规则填得对不对、PUT 出去的整套与 If-Match 一字不差、412 不覆盖、422 挂到哪一行哪一格、
草稿横幅的计数与逐条改动、「应用」一次点击发了什么、AI 写的文本只当文本、窄屏不横滚。
"""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest
from conftest import open_page, overflowing
from playwright.sync_api import Route

RULES = [
    {"id": "r_1", "app": "code", "title": "garden", "taskId": "t_word", "confidence": 0.9, "note": "编辑器",
     "enabled": True},
    {"id": "r_2", "app": None, "title": "视奏", "taskId": "t_read", "confidence": 0.7, "note": None, "enabled": False},
    {"id": "r_3", "app": "blender", "title": None, "taskId": "t_gone", "confidence": 0.8, "note": None,
     "enabled": True},
]
EVIL = '<img src=x onerror="window.__pwned=1">'
DRAFT = {
    "id": "drf_1", "status": "pending", "author": "assistant", "summary": "按终端标题分开两个项目 " + EVIL,
    "createdAt": "2026-09-30T02:00:00+00:00", "expiresAt": "2026-10-14T02:00:00+00:00",
    "baseVersion": 4, "currentVersion": 4,
    "rules": [{**RULES[0], "title": "garden|plot"}, RULES[1],
              {"id": "r_new", "app": None, "title": EVIL, "taskId": "t_word", "confidence": 0.9, "note": None,
               "enabled": True}],
    "diff": {"added": ["r_new"], "removed": ["r_3"], "changed": ["r_1"], "unchanged": 1, "reordered": False},
}


class RulesStub:
    def __init__(self, draft: dict | None = None) -> None:
        self.version = 4
        self.rules = copy.deepcopy(RULES)
        self.draft = copy.deepcopy(draft)
        self.calls: list[tuple[str, str, str | None, Any]] = []   # (方法, 路径尾, If-Match, body)
        self.put_reply: tuple[int, dict] | None = None
        self.apply_reply: tuple[int, dict] | None = None

    def route(self, route: Route) -> None:
        req = route.request
        tail = req.url.split("/api/core/detector/rules", 1)[1]
        body = json.loads(req.post_data) if req.post_data else None
        self.calls.append((req.method, tail, req.headers.get("if-match"), body))
        j = lambda code, obj: route.fulfill(status=code, content_type="application/json",  # noqa: E731
                                            body=json.dumps(obj, ensure_ascii=False))
        state = lambda: {"version": self.version, "updatedAt": None, "rules": self.rules}  # noqa: E731
        if tail == "" and req.method == "GET":
            return j(200, state())
        if tail == "" and req.method == "PUT":
            if self.put_reply:
                return j(*self.put_reply)
            if req.headers.get("if-match") != f'"{self.version}"':
                return j(412, {"detail": "stale", "currentVersion": self.version})
            self.version += 1
            self.rules = [{"id": r.get("id") or f"r_srv{i}", **{k: v for k, v in r.items() if k != "id"}}
                          for i, r in enumerate(body["rules"])]
            return j(200, state())
        if tail == "/drafts/current":
            return j(200, {"draft": self.draft})
        if tail.endswith("/apply"):
            if self.apply_reply:
                return j(*self.apply_reply)
            self.version += 1
            self.rules, self.draft = self.draft["rules"], None
            return j(200, state())
        if tail.endswith("/discard"):
            self.draft = None
            return route.fulfill(status=204)
        return j(404, {"detail": "Not Found"})


def page_with(browser, base, stub: RulesStub, **kw):
    return open_page(browser, base, routes={r"/api/core/detector/rules": stub.route}, **kw)


def ready(page) -> None:
    page.wait_for_selector("#rules-editor:not([hidden])")


def row(page, i: int, f: str) -> str:
    return f'#rules-list > li[data-index="{i}"] [data-f="{f}"]'


def test_renders_rules(browser, static_base_url):
    stub = RulesStub()
    with page_with(browser, static_base_url, stub) as page:
        ready(page)
        assert page.locator("#rules-list > li").count() == 3
        assert page.input_value(row(page, 0, "app")) == "code" and page.input_value(row(page, 0, "title")) == "garden"
        assert page.input_value(row(page, 0, "confidence")) == "0.9" and page.input_value(row(page, 0, "note")) == "编辑器"
        assert page.input_value(row(page, 0, "taskId")) == "t_word"
        opts = page.eval_on_selector_all(row(page, 0, "taskId") + " option", "os => os.map(o => o.textContent)")
        assert "练琴区 / 吉他练习 / 音阶练习" in opts
        assert page.input_value(row(page, 1, "app")) == "" and not page.is_checked(row(page, 1, "enabled"))
        assert "is-off" in page.get_attribute('#rules-list > li[data-index="1"]', "class")
        # 规则指向的任务已删：下拉里单列一项，不悄悄换成别的任务
        assert page.input_value(row(page, 2, "taskId")) == "t_gone"
        assert "任务已删除（t_gone）" in page.inner_text(row(page, 2, "taskId"))
        assert page.is_hidden("#rules-draft") and page.is_hidden("#rules-dirty") and page.is_disabled("#rules-save")


def test_edit_reorder_add_delete_then_save(browser, static_base_url):
    stub = RulesStub()
    with page_with(browser, static_base_url, stub) as page:
        ready(page)
        page.fill(row(page, 0, "title"), "garden|plot")
        assert page.is_visible("#rules-dirty") and page.is_enabled("#rules-save")
        page.click('#rules-list > li[data-index="2"] button[aria-label="删除第 3 条"]')
        page.click('#rules-list > li[data-index="1"] button[aria-label="上移第 2 条"]')
        page.click("#rules-add")
        page.fill(row(page, 2, "title"), "考级")
        page.select_option(row(page, 2, "taskId"), "t_read")
        page.fill(row(page, 2, "note"), "")
        page.click("#rules-save")
        page.wait_for_selector("#rules-message:not([hidden])")
        put = [c for c in stub.calls if c[0] == "PUT"]
        assert len(put) == 1 and put[0][2] == '"4"'
        assert put[0][3] == {"rules": [
            {"id": "r_2", "app": None, "title": "视奏", "taskId": "t_read", "confidence": 0.7, "note": None,
             "enabled": False},
            {"id": "r_1", "app": "code", "title": "garden|plot", "taskId": "t_word", "confidence": 0.9,
             "note": "编辑器", "enabled": True},
            {"app": None, "title": "考级", "taskId": "t_read", "confidence": 0.9, "note": None, "enabled": True}]}
        assert "已保存" in page.inner_text("#rules-message")
        assert page.is_hidden("#rules-dirty") and page.is_disabled("#rules-save")
        assert page.input_value(row(page, 0, "title")) == "视奏"


def test_412_keeps_edits_and_second_save_uses_new_version(browser, static_base_url):
    stub = RulesStub()
    with page_with(browser, static_base_url, stub) as page:
        ready(page)
        stub.version = 6          # 别处（比如刚应用的草稿）先改了
        page.fill(row(page, 0, "note"), "我的改动")
        page.click("#rules-save")
        page.wait_for_selector("#rules-message.is-error")
        assert "第 6 版" in page.inner_text("#rules-message")
        assert page.input_value(row(page, 0, "note")) == "我的改动" and page.is_enabled("#rules-save")
        page.click("#rules-save")
        page.wait_for_selector("#rules-message:not(.is-error)")
        assert [c[2] for c in stub.calls if c[0] == "PUT"] == ['"4"', '"6"']


def test_422_errors_attach_to_row_and_field(browser, static_base_url):
    stub = RulesStub()
    stub.put_reply = (422, {"detail": "2 处不合规，第一处：rules[1].title：正则写错了", "errors": [
        {"index": 1, "field": "title", "message": "正则写错了：missing )"},
        {"index": 2, "field": None, "message": "app 与 title 至少写一个"}]})
    with page_with(browser, static_base_url, stub) as page:
        ready(page)
        page.fill(row(page, 1, "title"), "视奏(")
        page.click("#rules-save")
        page.wait_for_selector("#rules-message.is-error")
        assert page.get_attribute(row(page, 1, "title"), "aria-invalid") == "true"
        assert "正则写错了：missing )" in page.inner_text('#rules-list > li[data-index="1"]')
        assert "app 与 title 至少写一个" in page.inner_text('#rules-list > li[data-index="2"]')
        assert page.get_attribute(row(page, 0, "title"), "aria-invalid") is None
        assert page.input_value(row(page, 1, "title")) == "视奏("   # 改动还在


def test_draft_banner_diff_and_one_click_apply(browser, static_base_url):
    stub = RulesStub(DRAFT)
    with page_with(browser, static_base_url, stub) as page:
        ready(page)
        page.wait_for_selector("#rules-draft:not([hidden])")
        assert page.inner_text("#rules-draft-head") == "AI 草稿：新增 1 / 修改 1 / 删除 1"
        assert EVIL in page.inner_text("#rules-draft-summary")          # AI 写的文本只当文本
        assert "共 3 条，其中 1 条不变" in page.inner_text("#rules-draft-meta")
        page.click(".rules-diff summary")
        items = page.eval_on_selector_all("#rules-diff-list li", "ls => ls.map(l => [l.className, l.innerText])")
        assert [c for c, _ in items] == ["diff-change", "diff-add", "diff-remove"]
        assert "标题 /garden/" in items[0][1] and "标题 /garden|plot/" in items[0][1]
        assert EVIL in items[1][1] and "blender" in items[2][1] and "任务已删除（t_gone）" in items[2][1]
        assert page.evaluate("window.__pwned") is None and page.locator("#rules-draft img").count() == 0
        page.click("#rules-apply")
        page.wait_for_selector("#rules-draft", state="hidden")
        apply = [c for c in stub.calls if c[1].endswith("/apply")]
        assert apply == [("POST", "/drafts/drf_1/apply", '"4"', None)]
        assert "已应用" in page.inner_text("#rules-message")
        assert page.input_value(row(page, 0, "title")) == "garden|plot" and page.locator("#rules-list > li").count() == 3


def test_apply_with_unsaved_edits_asks_first(browser, static_base_url):
    stub = RulesStub(DRAFT)
    with page_with(browser, static_base_url, stub) as page:
        ready(page)
        page.wait_for_selector("#rules-draft:not([hidden])")
        page.fill(row(page, 0, "note"), "没保存")
        page.once("dialog", lambda d: d.dismiss())
        page.click("#rules-apply")
        page.wait_for_timeout(200)
        assert not [c for c in stub.calls if c[1].endswith("/apply")]


def test_apply_stale_reloads_draft_and_discard(browser, static_base_url):
    stub = RulesStub(DRAFT)
    stub.apply_reply = (412, {"detail": "stale", "currentVersion": 5})
    with page_with(browser, static_base_url, stub) as page:
        ready(page)
        page.wait_for_selector("#rules-draft:not([hidden])")
        before = len([c for c in stub.calls if c[1] == "/drafts/current"])
        page.click("#rules-apply")
        page.wait_for_selector("#rules-message.is-error")
        assert "重新比对" in page.inner_text("#rules-message")
        assert len([c for c in stub.calls if c[1] == "/drafts/current"]) == before + 1
        page.click("#rules-discard")
        page.wait_for_selector("#rules-draft", state="hidden")
        assert ("POST", "/drafts/drf_1/discard", None, None) in stub.calls


def test_chat_turn_done_refreshes_draft(browser, static_base_url):
    stub = RulesStub()
    with page_with(browser, static_base_url, stub) as page:
        ready(page)
        assert page.is_hidden("#rules-draft")
        stub.draft = copy.deepcopy(DRAFT)     # AI 刚在对话里写了草稿
        page.evaluate("document.dispatchEvent(new Event('assistant:turn-done'))")
        page.wait_for_selector("#rules-draft:not([hidden])")


@pytest.mark.parametrize("width,theme", [(320, "light"), (390, "dark")])
def test_narrow_no_horizontal_scroll(browser, static_base_url, width, theme):
    with page_with(browser, static_base_url, RulesStub(DRAFT), width=width, theme=theme) as page:
        ready(page)
        page.wait_for_selector("#rules-draft:not([hidden])")
        page.click(".rules-diff summary")
        page.wait_for_timeout(200)
        assert overflowing(page, "#det-rules") == []
        assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
        # 草稿横幅、按钮都用 tokens 上色，暗色下也不是默认黑白
        bg = page.eval_on_selector("#rules-draft", "e => getComputedStyle(e).backgroundColor")
        assert bg not in ("rgba(0, 0, 0, 0)", "rgb(255, 255, 255)")
