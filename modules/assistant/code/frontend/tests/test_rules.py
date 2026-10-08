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


def test_reorder_only_draft_lists_moves(browser, static_base_url):
    d = copy.deepcopy(DRAFT)
    d["rules"] = [RULES[1], RULES[0], RULES[2]]
    d["diff"] = {"added": [], "removed": [], "changed": [], "unchanged": 3, "reordered": True}
    with page_with(browser, static_base_url, RulesStub(d)) as page:
        ready(page)
        page.wait_for_selector("#rules-draft:not([hidden])")
        assert page.inner_text("#rules-draft-head") == "AI 草稿：新增 0 / 修改 0 / 删除 0（顺序有变）"
        page.click(".rules-diff summary")
        tags = page.eval_on_selector_all("#rules-diff-list .diff-tag", "ts => ts.map(t => t.textContent)")
        assert tags == ["挪动 #2 → #1", "挪动 #1 → #2"]


# ------------------------------------------------------------------ 只到项目的规则（detector.rules.v1 v1.1）

P_RULE = {"id": "r_p", "app": "figma", "title": None, "taskId": None, "projectId": "p_book", "confidence": 0.9,
          "note": None, "enabled": True}


def test_project_target_rule_shown_and_round_trips(browser, static_base_url):
    stub = RulesStub()
    stub.rules = [copy.deepcopy(P_RULE), copy.deepcopy(RULES[0]), {**P_RULE, "id": "r_pg", "projectId": "p_gone"}]
    with page_with(browser, static_base_url, stub) as page:
        ready(page)
        assert page.input_value(row(page, 0, "taskId")) == "p:p_book"
        assert page.eval_on_selector(row(page, 0, "taskId"), "s => s.selectedOptions[0].textContent") \
            == "练琴区 / 考级计划 · 未分类（只到项目）"
        assert page.input_value(row(page, 2, "taskId")) == "p:p_gone"
        assert "项目已删除（p_gone）" in page.inner_text(row(page, 2, "taskId"))
        page.select_option(row(page, 1, "taskId"), "p:p_eng")     # 任务 → 只到项目
        page.select_option(row(page, 0, "taskId"), "t_read")      # 只到项目 → 任务
        page.click("#rules-save")
        page.wait_for_selector("#rules-message:not([hidden])")
        put = [c for c in stub.calls if c[0] == "PUT"]
        assert put[0][3] == {"rules": [
            {"id": "r_p", "app": "figma", "title": None, "taskId": "t_read", "confidence": 0.9, "note": None,
             "enabled": True},                                     # 到任务的规则不带 projectId 键（同 v1）
            {"id": "r_1", "app": "code", "title": "garden", "taskId": None, "projectId": "p_eng", "confidence": 0.9,
             "note": "编辑器", "enabled": True},
            {"id": "r_pg", "app": "figma", "title": None, "taskId": None, "projectId": "p_gone", "confidence": 0.9,
             "note": None, "enabled": True}]}


def test_project_target_422_and_describe(browser, static_base_url):
    stub = RulesStub()
    stub.rules = [copy.deepcopy(P_RULE)]
    stub.put_reply = (422, {"detail": "规则有误", "errors": [{"index": 0, "field": "projectId", "message": "项目不存在"}]})
    with page_with(browser, static_base_url, stub) as page:
        ready(page)
        page.fill(row(page, 0, "note"), "x")
        page.click("#rules-save")
        page.wait_for_selector('#rules-list > li[data-index="0"] .field-error')
        assert "项目不存在" in page.inner_text('#rules-list > li[data-index="0"]')
        said = page.evaluate("""() => {
            const R = window.assistantRules, projects = R.projectPaths({zones: [{id: 'z', name: '区'}],
                projects: [{id: 'p', zoneId: 'z', name: '<b>项</b>'}]});
            return [R.describe({app: 'a', title: null, taskId: null, projectId: 'p', confidence: 0.9}, {}, projects),
                    R.describe({app: 'a', title: null, taskId: null, projectId: 'p_x', confidence: 0.9}, {}, projects),
                    R.toWire({app: 'a', title: '', taskId: 't', projectId: null, confidence: '0.5'})];
        }""")
        assert said[0] == "程序 /a/ → 区 / <b>项</b> · 未分类 · 90%"
        assert said[1] == "程序 /a/ → 项目已删除（p_x） · 90%"
        assert said[2] == {"app": "a", "title": None, "taskId": "t", "confidence": 0.5, "note": None, "enabled": True}


def test_ai_written_rule_has_a_badge_and_its_provenance_survives_a_save(browser, static_base_url):
    """v1.2（nexus-core v2.15）：AI 认窗口时直接写下的规则标「AI 自动」；照样能改，整套存回去不丢 author / auto。"""
    stub = RulesStub()
    ai = {"id": "r_ai_0a0a", "app": "^kitty$", "title": "^notes$", "taskId": "t_word", "confidence": 0.9,
          "note": "AI 认的：" + EVIL, "enabled": True, "author": "assistant", "auto": True}
    stub.rules = [ai, copy.deepcopy(RULES[0])]
    with page_with(browser, static_base_url, stub) as page:
        ready(page)
        badges = page.eval_on_selector_all("#rules-list > li", "ls => ls.map(l => l.querySelectorAll('.rule-auto').length)")
        assert badges == [1, 0]
        assert page.inner_text('#rules-list > li[data-index="0"] .rule-auto') == "AI 自动"
        assert page.locator("#rules-list img").count() == 0 and page.evaluate("window.__pwned") is None
        page.select_option(row(page, 0, "taskId"), "t_read")       # 人改它的目标
        page.fill(row(page, 1, "note"), "动一下")
        page.click("#rules-save")
        page.wait_for_selector("#rules-message:not([hidden])")
        put = [c for c in stub.calls if c[0] == "PUT"][0][3]["rules"]
        assert put[0] == {**ai, "taskId": "t_read"}                # 出处键原样带回
        assert "author" not in put[1] and "auto" not in put[1]     # 普通规则不多带键
        page.click('#rules-list > li[data-index="0"] button[aria-label="删除第 1 条"]')   # 也能删
        assert page.locator("#rules-list .rule-auto").count() == 0
