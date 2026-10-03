"""「待确认建议」面板（suggestions.js，nexus-core v2.2 活动建议 + v2.5 idle 段）。

2026-09-30 连同本文件从 ring 搬来「AI助理」页：断言照旧，只少了「确认后刷新圆环」（这页没有圆环），
多了 idle 段的几条。

同本套件其余文件：真浏览器、接口全在浏览器侧桩掉，一个字节都不写库。
判据只落在行为上：发了什么请求、哪些条目留下、面板在不在、窄屏会不会横滚。
"""

from __future__ import annotations

import contextlib
import json
from types import SimpleNamespace
from typing import Any, Iterator

import pytest
from conftest import TREE, open_page as _open
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
    """GET 返回 ``self.items``（None = 404，模拟老后端）；confirm/dismiss 记下并从列表里拿掉；
    unmatch 记下、把那条的任务清掉。"""

    def __init__(self, items: list[dict] | None) -> None:
        self.items = json.loads(json.dumps(items)) if items is not None else None
        self.posts: list[tuple[str, str, Any]] = []
        self.fail_ids: set[str] = set()
        self.get_status = 200
        self.extra_total = 0  # total 比 items 多出来的条数（模拟分页之外还有更早的）  # 非 200 = 列表接口出错（不是 404 那种「老后端」）
        self.hold_get = False  # True = GET 先压着不回（测「重拉还在路上」），测试自己拿 held 里的放行
        self.held: list[Route] = []

    def route(self, route: Route) -> None:
        req = route.request
        if req.method == "GET" and self.hold_get:
            self.held.append(route)
            return
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
        if action == "unmatch":  # v2.7 人说「否」：条目留在待确认里，任务清掉并记住
            item = next(i for i in self.items if i["id"] == sug_id)
            item["rejectedTaskIds"] = [item["suggestion"]["taskId"]]
            item["suggestion"] = {**item["suggestion"], "taskId": None, "confidence": 0, "reason": ""}
            route.fulfill(status=200, content_type="application/json", body=json.dumps(
                {"id": sug_id, "status": "pending", "rejectedTaskIds": item["rejectedTaskIds"]}))
            return
        self.items = [i for i in self.items if i["id"] != sug_id]
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps({"id": sug_id, "status": action + "ed"}))


@contextlib.contextmanager
def open_page(browser: Browser, base: str, items, *, width: int = 1100,
              theme: str | None = None) -> Iterator[tuple[SimpleNamespace, SuggestStub]]:
    stub = SuggestStub(items)
    init = "window.__timerEvents = 0; window.addEventListener('honeycomb:timer-changed', () => window.__timerEvents++)"
    with _open(browser, base, routes={r"/api/core/activity/suggestions([/?]|$)": stub.route},
               width=width, theme=theme, init=init) as page:
        yield SimpleNamespace(page=page), stub


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


def test_confirm_sends_chosen_task_without_timer_event(browser, static_base_url):
    with open_page(browser, static_base_url, ITEMS) as (h, stub):
        page = h.page
        page.wait_for_selector("#suggest-list li")
        page.locator('li[data-id="sug_b"] select').select_option("t_legacy")
        page.locator('li[data-id="sug_b"] .suggest-confirm').click()
        page.wait_for_function("() => !document.querySelector('li[data-id=\"sug_b\"]')")
        assert stub.posts == [("confirm", "sug_b", {"taskId": "t_legacy"})]
        assert _ids(page) == ["sug_a", "sug_c"]
        page.wait_for_timeout(300)
        assert page.evaluate("window.__timerEvents") == 0  # 确认不是计时，不叫顶栏


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
        assert _ids(page) == ["sug_a", "sug_b"]   # 已忽略的那条从旧列表里拿掉，不能再点一次
        assert page.is_hidden("#suggest-more")     # 总数跟着减：不多报「还有 1 条更早的」


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
          const S = window.assistantSuggestions;
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


IDLE = {"id": "sug_idle", "deviceId": "dev_x", "startAt": "2026-09-26T13:00:00+08:00",
        "endAt": "2026-09-26T13:40:00+08:00", "durationSeconds": 2400, "app": "SumatraPDF", "title": "论文.pdf",
        "suggestion": {"taskId": "t_read", "confidence": 0.3, "reason": "无操作，可能在阅读", "classifier": "rules"},
        "idle": True, "status": "pending"}


def test_idle_item_is_marked_and_never_bulk_confirmed(browser, static_base_url):
    with open_page(browser, static_base_url, [IDLE] + ITEMS) as (h, stub):
        page = h.page
        page.wait_for_selector("#suggest-list li")
        li = page.locator('li[data-id="sug_idle"]')
        assert "is-idle" in li.get_attribute("class")
        assert li.locator(".suggest-badge").inner_text() == "无操作·可能在阅读"
        assert "把握 30%" in li.inner_text()
        # 别的条目没有徽标
        assert page.locator('li[data-id="sug_a"] .suggest-badge').count() == 0
        page.fill("#suggest-threshold", "0")
        assert page.locator("#suggest-confirm-all").inner_text().endswith("· 2")   # sug_a、sug_b，不含 idle
        page.click("#suggest-confirm-all")
        page.wait_for_function("() => document.querySelectorAll('#suggest-list li').length === 2")
        assert [i for _, i, _ in stub.posts] == ["sug_a", "sug_b"]
        # 逐条确认照常可以
        page.locator('li[data-id="sug_idle"] .suggest-confirm').click()
        page.wait_for_function("() => !document.querySelector('li[data-id=\"sug_idle\"]')")
        assert stub.posts[-1] == ("confirm", "sug_idle", {"taskId": "t_read"})


def test_eligible_skips_idle(browser, static_base_url):
    with open_page(browser, static_base_url, []) as (h, _stub):
        r = h.page.evaluate("""() => window.assistantSuggestions.eligible([
            {id: 'a', suggestion: {taskId: 't', confidence: 0.9}},
            {id: 'b', idle: true, suggestion: {taskId: 't', confidence: 0.9}},
            {id: 'c', idle: false, suggestion: {taskId: 't', confidence: 0.9}}], 0).map(i => i.id)""")
        assert r == ["a", "c"]


# ── 按窗口分组（仓主 2026-10-03「一个窗口对应一个任务」）+「以后这个窗口都记到这个任务」 ──────────────

def _seg(i: int, *, app: str = "kitty", title: str = "vim garden [IP]", task: str | None = "t_word",
         conf: float = 0.9, classifier: str = "rules", idle: bool = False) -> dict:
    """第 i 分钟开始的 90 秒。"""
    return {"id": f"seg_{i}", "deviceId": "dev_x", "startAt": f"2026-10-03T10:{i:02d}:00+08:00",
            "endAt": f"2026-10-03T10:{i + 1:02d}:30+08:00", "durationSeconds": 90, "app": app, "title": title,
            "idle": idle, "status": "pending",
            "suggestion": {"taskId": task, "confidence": conf, "reason": "", "classifier": classifier}}


WINDOW = [_seg(1), _seg(3, conf=0.85), _seg(5, conf=0.95), _seg(7, app="chrome", title="docs")]


@contextlib.contextmanager
def open_both(browser: Browser, base: str, items, rules_stub=None):
    from test_rules import RulesStub
    stub = SuggestStub(items)
    rules = rules_stub or RulesStub()
    with _open(browser, base, routes={r"/api/core/activity/suggestions([/?]|$)": stub.route,
                                      r"/api/core/detector/rules": rules.route}) as page:
        page.wait_for_selector("#suggest-list li")
        page.wait_for_selector("#rules-editor:not([hidden])", state="attached")
        yield page, stub, rules


def _put_calls(rules):
    return [c for c in rules.calls if c[0] == "PUT"]


def test_groups_same_window_into_one_row(browser, static_base_url):
    with open_both(browser, static_base_url, WINDOW) as (page, _stub, _rules):
        assert _ids(page) == ["seg_1", "seg_7"]
        li = page.locator('li[data-id="seg_1"]')
        assert li.get_attribute("data-count") == "3"
        assert "3 段 · 共 5 分" in li.inner_text()          # 90 秒 × 3
        assert "10-03 10:01–10:06" in li.inner_text()      # 第一段开始 – 最后一段结束
        assert li.locator(".suggest-seg").count() == 3
        # 三段都建议 t_word：预选它，把握取最小（85%）
        assert li.locator("select").input_value() == "t_word"
        assert "把握 85%" in li.inner_text()
        # 单段的组与原来一条一样
        one = page.locator('li[data-id="seg_7"]')
        assert "段" not in one.locator(".suggest-dur").inner_text() and one.locator("details").count() == 0
        assert page.inner_text("#suggest-count") == "4"


def test_group_confirm_posts_each_segment(browser, static_base_url):
    with open_both(browser, static_base_url, WINDOW) as (page, stub, rules):
        page.locator('li[data-id="seg_1"] select').select_option("t_read")
        page.locator('li[data-id="seg_1"] .suggest-confirm').click()
        page.wait_for_function("() => !document.querySelector('li[data-id=\"seg_1\"]')")
        assert stub.posts == [("confirm", f"seg_{i}", {"taskId": "t_read"}) for i in (1, 3, 5)]
        assert _put_calls(rules) == []   # 没勾：不碰规则


def test_group_partial_failure_reported_per_group(browser, static_base_url):
    with open_both(browser, static_base_url, WINDOW) as (page, stub, _rules):
        stub.fail_ids.add("seg_3")
        page.locator('li[data-id="seg_1"] .suggest-confirm').click()
        page.wait_for_selector("#suggest-message.is-error")
        msg = page.inner_text("#suggest-message")
        assert msg.startswith("kitty · vim garden [IP]：1 / 3 段失败，") and "t_gone" in msg
        assert [i for _, i, _ in stub.posts] == ["seg_1", "seg_3", "seg_5"]   # 失败的那段不挡后面的
        assert _ids(page) == ["seg_3", "seg_7"]                               # 成功的已确认，失败的留下


def test_group_dismiss_and_ai_unmatch(browser, static_base_url):
    ai = [_seg(1, task="t_read", classifier="assistant", conf=0.7), _seg(2, task="t_read", classifier="assistant")]
    with open_both(browser, static_base_url, WINDOW[3:] + ai) as (page, stub, _rules):
        page.locator('li[data-id="seg_1"] .suggest-no').click()
        page.wait_for_function("() => document.querySelector('li[data-id=\"seg_1\"] select')")
        assert stub.posts == [("unmatch", "seg_1", {"taskId": "t_read"}), ("unmatch", "seg_2", {"taskId": "t_read"})]
        page.locator('li[data-id="seg_1"] .suggest-dismiss').click()
        page.wait_for_function("() => !document.querySelector('li[data-id=\"seg_1\"]')")
        assert stub.posts[2:] == [("dismiss", "seg_1", None), ("dismiss", "seg_2", None)]


@pytest.mark.parametrize("other", [{"task": "t_read"}, {"task": None, "conf": 0}])
def test_disagreeing_suggestions_leave_picker_empty(browser, static_base_url, other):
    # 指向别的任务、或有的段没建议：都算不一致，要人挑
    with open_both(browser, static_base_url, [_seg(1), _seg(2, **other)]) as (page, _stub, _rules):
        li = page.locator('li[data-id="seg_1"]')
        assert "建议不一致" in li.inner_text()
        assert li.locator("select").input_value() == ""
        assert li.locator(".suggest-confirm").is_disabled()
        page.fill("#suggest-threshold", "0")
        assert page.locator("#suggest-confirm-all").is_disabled()


def test_partial_unmatch_requires_manual_pick_and_never_sends_rejected(browser, static_base_url):
    ai = [_seg(1, task="t_read", classifier="assistant"), _seg(2, task="t_read", classifier="assistant")]
    with open_both(browser, static_base_url, ai) as (page, stub, _rules):
        stub.fail_ids.add("seg_2")
        page.locator('li[data-id="seg_1"] .suggest-no').click()
        page.wait_for_selector("#suggest-message.is-error")
        # seg_1 已否掉、seg_2 还建议 t_read：不出「是」，不预选
        li = page.locator('li[data-id="seg_1"]')
        assert li.locator(".suggest-no").count() == 0 and "建议不一致" in li.inner_text()
        assert li.locator("select").input_value() == ""
        stub.fail_ids.clear()
        li.locator("select").select_option("t_read")   # 人偏要选回被否掉的任务：否掉过的那段不发
        li.locator(".suggest-confirm").click()
        page.wait_for_function("() => document.querySelector('#suggest-message').textContent.includes('否掉过')")
        assert [p for p in stub.posts if p[0] == "confirm"] == [("confirm", "seg_2", {"taskId": "t_read"})]
        assert _ids(page) == ["seg_1"]


def test_bulk_uses_displayed_task_after_suggestion_changes(browser, static_base_url):
    with open_both(browser, static_base_url, [_seg(1, task=None, conf=0)]) as (page, stub, _rules):
        page.locator('li[data-id="seg_1"] select').select_option("t_legacy")   # 人先挑了一个
        # 助理随后配了 t_read：重拉后这行显示「AI 建议 + 是 / 否」，旧的下拉选择作废
        stub.items[0]["suggestion"] = {"taskId": "t_read", "confidence": 0.9, "reason": "", "classifier": "assistant"}
        page.evaluate("document.dispatchEvent(new Event('assistant:turn-done'))")
        page.wait_for_selector('li[data-id="seg_1"].is-ai')
        page.click("#suggest-confirm-all")
        page.wait_for_function("() => !document.querySelector('li[data-id=\"seg_1\"]')")
        assert stub.posts == [("confirm", "seg_1", {"taskId": "t_read"})]


@pytest.mark.parametrize("suggested", [None, "t_word"])
def test_chosen_task_deleted_after_refresh_is_dropped(browser, static_base_url, suggested):
    import re
    items = [_seg(1, task=suggested, conf=0.9 if suggested else 0)]
    with open_both(browser, static_base_url, items) as (page, stub, _rules):
        li = page.locator('li[data-id="seg_1"]')
        li.locator("select").select_option("t_legacy")
        # 别处把 t_legacy 删了，然后这页重拉
        tree = json.loads(json.dumps(TREE))
        tree["projects"][0]["tasks"] = [t for t in tree["projects"][0]["tasks"] if t["id"] != "t_legacy"]
        page.route(re.compile(r"/api/core/views/tree$"), lambda r: r.fulfill(
            status=200, content_type="application/json", body=json.dumps(tree, ensure_ascii=False)))
        page.evaluate("document.dispatchEvent(new Event('assistant:turn-done'))")
        page.wait_for_function("() => !document.querySelector('#suggest-list option[value=\"t_legacy\"]')")
        li = page.locator('li[data-id="seg_1"]')
        assert li.locator("select").input_value() == (suggested or "")   # 退回看得到的：建议，或空
        if suggested is None:
            assert li.locator(".suggest-confirm").is_disabled()
            return
        page.click("#suggest-confirm-all")
        page.wait_for_function("() => !document.querySelector('li[data-id=\"seg_1\"]')")
        assert stub.posts == [("confirm", "seg_1", {"taskId": "t_word"})]


def test_unmatch_kept_when_reload_fails(browser, static_base_url):
    ai = [_seg(1, task="t_read", classifier="assistant"), _seg(2, task="t_read", classifier="assistant"), WINDOW[3]]
    with open_both(browser, static_base_url, ai) as (page, stub, _rules):
        stub.extra_total = 5   # 服务端总数 8：页上 3 段，还有 5 条更早的
        page.evaluate("document.dispatchEvent(new Event('assistant:turn-done'))")
        page.wait_for_selector("#suggest-more:not([hidden])")
        stub.get_status = 500
        page.locator('li[data-id="seg_1"] .suggest-no').click()
        page.wait_for_selector("#suggest-message.is-error")
        page.wait_for_function("() => document.querySelector('li[data-id=\"seg_1\"] select')")
        # 否掉的段仍待确认：留在列表里，建议清掉，换成手动挑
        assert _ids(page) == ["seg_1", "seg_7"]
        li = page.locator('li[data-id="seg_1"]')
        assert li.get_attribute("data-count") == "2"
        assert li.locator(".suggest-no").count() == 0 and li.locator("select").input_value() == ""
        assert "AI 的建议已否掉" in li.inner_text()
        assert page.inner_text("#suggest-more") == "还有 5 条更早的待确认，先处理上面的"   # 否掉不减总数
        # 忽略成功的则拿掉，总数跟着减（8 → 7，页上 2 段，仍是 5 条更早的）
        page.locator('li[data-id="seg_7"] .suggest-dismiss').click()
        page.wait_for_function("() => !document.querySelector('li[data-id=\"seg_7\"]')")
        assert _ids(page) == ["seg_1"]
        assert page.inner_text("#suggest-more") == "还有 5 条更早的待确认，先处理上面的"


def test_buttons_stay_disabled_until_reload_settles(browser, static_base_url):
    with open_both(browser, static_base_url, WINDOW) as (page, stub, _rules):
        stub.hold_get = True
        page.locator('li[data-id="seg_7"] .suggest-dismiss').click()
        for _ in range(100):
            if stub.held:
                break
            page.wait_for_timeout(50)
        assert stub.held
        # 重拉还在路上：调阈值、聊天状态变化都不能把旧列表的按钮放开
        page.fill("#suggest-threshold", "10")
        page.evaluate("document.dispatchEvent(new CustomEvent('assistant:chat-state', {detail: {configured: true, generating: false}}))")
        assert page.locator('li[data-id="seg_1"] .suggest-confirm').is_disabled()
        assert page.locator("#suggest-confirm-all").is_disabled()
        # 绕过禁用硬点：act 自己也不接
        page.evaluate("""() => { const b = document.querySelector('li[data-id="seg_1"] .suggest-confirm');
                                  b.disabled = false; b.click(); }""")
        page.wait_for_timeout(200)
        assert stub.posts == [("dismiss", "seg_7", None)]
        stub.hold_get = False
        stub.route(stub.held.pop())
        page.wait_for_function("() => !document.querySelector('li[data-id=\"seg_7\"]')")
        assert page.locator('li[data-id="seg_1"] .suggest-confirm').is_enabled()


def test_idle_segments_grouped_separately_and_not_bulk(browser, static_base_url):
    items = [_seg(1), _seg(2), _seg(3, idle=True, conf=0.3)]
    with open_both(browser, static_base_url, items) as (page, stub, _rules):
        assert _ids(page) == ["seg_1", "seg_3"]
        assert "is-idle" in page.locator('li[data-id="seg_3"]').get_attribute("class")
        page.fill("#suggest-threshold", "0")
        assert page.inner_text("#suggest-confirm-all").endswith("· 2")
        page.click("#suggest-confirm-all")
        page.wait_for_function("() => document.querySelectorAll('#suggest-list li').length === 1")
        assert [i for _, i, _ in stub.posts] == ["seg_1", "seg_2"]
        assert "已确认 2 条" in page.inner_text("#suggest-message")


WANT_RULE = {"app": "^kitty$", "title": r"^vim garden \[IP\]$", "taskId": "t_word", "confidence": 0.9,
             "note": "待确认里勾的：kitty · vim garden [IP]", "enabled": True}


def test_remember_prepends_window_rule_with_if_match(browser, static_base_url):
    with open_both(browser, static_base_url, WINDOW) as (page, _stub, rules):
        li = page.locator('li[data-id="seg_1"]')
        li.locator(".suggest-rule-cb").check()
        li.locator(".suggest-confirm").click()
        page.wait_for_function("() => document.querySelector('#suggest-message').textContent.includes('已加规则')")
        (put,) = _put_calls(rules)
        assert put[2] == '"4"'
        assert put[3]["rules"][0] == WANT_RULE                      # 放最前：第一条命中生效
        assert [r["id"] for r in put[3]["rules"][1:]] == ["r_1", "r_2", "r_3"]
        # 规则编辑器没手改：跟着刷新到新版本
        page.wait_for_function("() => document.querySelectorAll('#rules-list > li').length === 4")


def test_remember_retries_once_on_412(browser, static_base_url):
    from test_rules import RulesStub

    class Racy(RulesStub):
        raced = False

        def route(self, route):
            if route.request.method == "PUT" and not self.raced:   # 别处在我们 GET 之后先存了一版
                self.raced = True
                self.version += 1
            super().route(route)

    with open_both(browser, static_base_url, WINDOW, Racy()) as (page, _stub, rules):
        li = page.locator('li[data-id="seg_1"]')
        li.locator(".suggest-rule-cb").check()
        li.locator(".suggest-confirm").click()
        page.wait_for_function("() => document.querySelector('#suggest-message').textContent.includes('已加规则')")
        assert [p[2] for p in _put_calls(rules)] == ['"4"', '"5"']
        assert rules.rules[0]["title"] == WANT_RULE["title"] and rules.version == 6


def test_remember_skips_duplicate_and_keeps_confirm_on_rule_failure(browser, static_base_url):
    from test_rules import RulesStub
    dup = RulesStub()
    dup.rules.insert(0, {"id": "r_dup", **WANT_RULE})   # 已是第一条且启用：不动
    with open_both(browser, static_base_url, WINDOW, dup) as (page, stub, rules):
        li = page.locator('li[data-id="seg_1"]')
        li.locator(".suggest-rule-cb").check()
        li.locator(".suggest-confirm").click()
        page.wait_for_function("() => document.querySelector('#suggest-message').textContent.includes('已经有了')")
        assert _put_calls(rules) == []
        # 规则写不进去（比如 422）：确认照样算，报错
        rules.put_reply = (422, {"detail": "规则太多", "errors": []})
        li = page.locator('li[data-id="seg_7"]')
        li.locator(".suggest-rule-cb").check()
        li.locator(".suggest-confirm").click()
        page.wait_for_selector("#suggest-message.is-error")
        assert "已确认，但规则没加上：规则太多" in page.inner_text("#suggest-message")
        assert _ids(page) == []
        assert [i for a, i, _ in stub.posts if a == "confirm"] == ["seg_1", "seg_3", "seg_5", "seg_7"]


@pytest.mark.parametrize("case", ["disabled_first", "disabled_last", "enabled_not_first", "re2_only_before"])
def test_remember_moves_existing_rule_to_front(browser, static_base_url, case):
    """同样的规则已在，但停用、或不是第一条：挪到第一条并启用，一次 PUT。不在浏览器里猜谁先命中——
    前面那条哪怕是 JS 读不懂的 RE2 写法（[[:alpha:]]），也一样挪。"""
    from test_rules import RulesStub
    stub = RulesStub()
    if case == "re2_only_before":
        stub.rules.insert(0, {"id": "r_posix", "app": "[[:alpha:]]+", "title": None, "taskId": "t_read",
                              "confidence": 0.8, "note": None, "enabled": True})
    dup = {"id": "r_dup", **WANT_RULE, "enabled": not case.startswith("disabled")}
    if case == "disabled_first":
        stub.rules.insert(0, dup)
    else:
        stub.rules.append(dup)
    before = [r["id"] for r in stub.rules if r["id"] != "r_dup"]
    with open_both(browser, static_base_url, WINDOW, stub) as (page, _stub, rules):
        li = page.locator('li[data-id="seg_1"]')
        li.locator(".suggest-rule-cb").check()
        li.locator(".suggest-confirm").click()
        page.wait_for_function("() => document.querySelector('#suggest-message').textContent.includes('已加规则')")
        (put,) = _put_calls(rules)
        assert put[3]["rules"][0] == {"id": "r_dup", **WANT_RULE}       # 保留 id、启用
        assert [r["id"] for r in put[3]["rules"][1:]] == before         # 其余顺序不变，没有重复


def test_remember_disabled_for_pseudonym_titles(browser, static_base_url):
    with open_both(browser, static_base_url, [_seg(1, title="窗口名3")]) as (page, _stub, _rules):
        li = page.locator('li[data-id="seg_1"]')
        assert li.locator(".suggest-rule-cb").is_disabled()
        assert "代号" in li.locator(".suggest-rule").inner_text()


def test_window_rule_helper(browser, static_base_url):
    with open_page(browser, static_base_url, []) as (h, _stub):
        r = h.page.evaluate("""() => {
          const W = window.assistantSuggestions.windowRule;
          return [W('code', 'a.b (1) [主机] $x|y', 't'), W('kitty', '', 't'), W('x', '路径12', 't'),
                  W('x', 'y'.repeat(199), 't')];
        }""")
        assert r[0]["rule"]["app"] == "^code$"
        assert r[0]["rule"]["title"] == r"^a\.b \(1\) \[主机\] \$x\|y$"
        assert r[1]["rule"]["title"] == "^$"
        assert "rule" not in r[2] and "rule" not in r[3]
