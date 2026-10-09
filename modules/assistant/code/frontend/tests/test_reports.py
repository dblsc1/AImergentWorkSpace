"""「AI 报告」面板（reports.js，nexus-core v2.20「AI 报告」）。

同本套件其余文件：真浏览器、接口全在浏览器侧桩掉。判据落在行为上：列出了什么（分组、排序、全是文本）、
一次点击发了什么请求、结果行怎么说、改目标发了什么、失败怎么报、处理中控件是否禁用、面板在不在、窄屏会不会横滚。
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from conftest import open_page, overflowing
from playwright.sync_api import Route

EVIL = '<img src=x onerror="window.__pwned=1">'
RID = "rp_000000000001"


def _sug(sid: str, app: str, title: str, seconds: int) -> dict:
    return {"id": sid, "app": app, "title": title, "startAt": "2026-10-09T09:00:00+08:00",
            "durationSeconds": seconds, "status": "pending"}


def _item(iid: str, kind: str, seconds: int, sugs: list[dict], **over: Any) -> dict:
    base = {"id": iid, "kind": kind, "status": "pending", "reason": "", "taskId": None, "projectId": None,
            "newTask": None, "results": {}, "applied": 0, "stale": 0, "failed": 0, "failure": None,
            "seconds": seconds, "staleNow": 0, "suggestions": sugs}
    base.update(over)
    return base


def _report() -> dict:
    items = [
        _item("i0", "assign", 600, [_sug("s0", "code", "plot.gd — " + EVIL, 600)], taskId="t_word", reason="理由 " + EVIL),
        _item("i1", "dismiss", 9000, [_sug("s1", "slack", "闲聊", 9000)]),   # 最长，但「忽略」一组放最后
        _item("i2", "assign", 3000, [_sug("s2", "kitty", "ssh", 1800), _sug("s3", "kitty", "ssh", 1200)], projectId="p_book"),
        _item("i3", "newTask", 1200, [_sug("s4", "code", "save.gd", 1200)],
              newTask={"proposalId": "tp_x", "projectId": "p_eng", "name": "重构存档"}),
        _item("i4", "assign", 300, [_sug("s5", "code", "stale.gd", 300)], taskId="t_read", staleNow=1),
    ]
    return {"id": RID, "author": "hermes " + EVIL, "summary": "归类 8 段 " + EVIL, "status": "pending",
            "createdAt": "2026-10-09T10:30:00+08:00", "decidedAt": None,
            "counts": {"items": 5, "pending": 5, "byKind": {"assign": 3, "newTask": 1, "dismiss": 1}, "seconds": 14100},
            "items": items}


class Stub:
    def __init__(self, report: dict | None) -> None:
        self.report = report                    # None = 没有待批准的报告
        self.missing = False                    # 端点 404（后端早于 v2.20）
        self.posts: list[tuple[str, Any]] = []
        self.fail: str | None = None
        self.hold: list[Route] = []             # hold=True 时把 POST 扣下，测试里手动放行
        self.holding = False
        self.reply: dict = {"id": RID, "status": "approved", "applied": 4, "stale": 1, "failed": 0, "items": []}

    def reports(self, route: Route) -> None:
        if self.missing:
            route.fulfill(status=404, content_type="application/json", body='{"detail":"Not Found"}')
            return
        items = [{"id": self.report["id"]}] if self.report and self.report["status"] == "pending" else []
        route.fulfill(status=200, content_type="application/json", body=json.dumps({"items": items}))

    def detail(self, route: Route) -> None:
        route.fulfill(status=200, content_type="application/json", body=json.dumps(self.report, ensure_ascii=False))

    def act(self, route: Route) -> None:
        path = route.request.url.split("/activity/reports/")[1]
        body = json.loads(route.request.post_data or "{}")
        self.posts.append((path, body))
        if self.holding:
            self.hold.append(route)
            return
        self.finish(route, path, body)

    def finish(self, route: Route, path: str, body: Any) -> None:
        if self.fail:
            route.fulfill(status=409, content_type="application/json",
                          body=json.dumps({"detail": self.fail}, ensure_ascii=False))
            return
        if path.endswith("/approve") and "/items/" not in path:
            self.report["status"] = "approved"
        elif path.endswith("/reject") and "/items/" not in path:
            self.report["status"] = "rejected"
        else:
            iid = path.split("/items/")[1].split("/")[0]
            item = next(i for i in self.report["items"] if i["id"] == iid)
            item["status"] = "rejected" if path.endswith("/reject") else "applied"
            item["applied"] = 1 if item["status"] == "applied" else 0
            if body.get("taskId"):
                item.update(taskId=body["taskId"], projectId=None, kind="assign", newTask=None)
            if body.get("projectId"):
                item.update(projectId=body["projectId"], taskId=None, kind="assign", newTask=None)
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps({**self.reply, "status": "applied" if "/items/" in path else self.reply["status"],
                                       "failure": None}))


def opened(browser, base, stub: Stub, width: int = 1100, **kw):
    return open_page(browser, base, width=width, routes={
        r"/api/core/activity/reports(\?[^/]*)?$": stub.reports,
        r"/api/core/activity/reports/rp_[0-9]+$": stub.detail,
        r"/api/core/activity/reports/rp_[0-9]+/.+$": stub.act}, **kw)


def _idle(page) -> None:
    page.wait_for_function("() => { const b = document.getElementById('report-approve-all'); return !b.disabled || b.hidden; }")


@pytest.mark.parametrize("mode", ["empty", "missing"])
def test_panel_hidden_without_a_pending_report(browser, static_base_url, mode):
    stub = Stub(None)
    stub.missing = mode == "missing"
    errors: list[str] = []
    with opened(browser, static_base_url, stub) as page:
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.wait_for_timeout(300)
        assert page.is_hidden("#report-panel")
    assert errors == [] and stub.posts == []


def test_lists_groups_by_project_longest_first_and_everything_is_text(browser, static_base_url):
    stub = Stub(_report())
    with opened(browser, static_base_url, stub) as page:
        page.wait_for_selector("#report-panel:not([hidden])")
        assert page.inner_text("#report-count") == "5"
        meta = page.inner_text("#report-meta")
        assert "作者 hermes " + EVIL in meta and "共 5 条" in meta and "覆盖约 3 小时 55 分" in meta
        assert "记到项目 / 任务 3 条" in meta and "新建任务 1 条" in meta and "忽略 1 条" in meta
        assert page.inner_text("#report-summary") == "归类 8 段 " + EVIL
        groups = page.eval_on_selector_all("#report-groups > section", "els => els.map(e => e.dataset.group)")
        # 考级计划 3000 > 吉他练习 600+1200+300=2100；「忽略」(9000) 放最后
        assert groups == ["p_book", "p_eng", "__dismiss"]
        assert page.inner_text('section[data-group="p_book"] .unc-project') == "练琴区 / 考级计划"
        eng = [r.get_attribute("data-id") for r in page.locator('section[data-group="p_eng"] li').all()]
        assert eng == ["i3", "i0", "i4"]                      # 组内也是时间长的在前
        first = page.locator('li[data-id="i0"]')
        assert first.locator(".unc-what").inner_text() == "code · plot.gd — " + EVIL
        assert first.locator(".auto-target").inner_text() == "→ 练琴区 / 吉他练习 / 音阶练习"
        assert first.locator(".report-reason").inner_text() == "理由：理由 " + EVIL
        assert first.locator(".report-dur").inner_text() == "10 分"
        assert page.inner_text('li[data-id="i3"] .auto-target') == "→ 新建任务「重构存档」（练琴区 / 吉他练习）"
        assert page.inner_text('li[data-id="i2"] .unc-what') == "kitty · ssh 等 2 段"
        assert page.inner_text('li[data-id="i2"] .auto-target') == "→ 练琴区 / 考级计划 · 未分类"
        assert page.inner_text('li[data-id="i1"] .auto-target') == "→ 忽略（当噪声）"
        assert "已被你处理过" in page.inner_text('li[data-id="i4"] .report-status')
        assert page.locator('li[data-id="i1"] .report-pick').count() == 0      # 「忽略」不能改目标
        assert page.locator("#report-panel img").count() == 0 and page.evaluate("window.__pwned") is None
        assert stub.posts == []                                                   # 只是看，什么都没发


def test_approve_all_is_one_click_and_shows_the_result_line(browser, static_base_url):
    stub = Stub(_report())
    with opened(browser, static_base_url, stub) as page:
        page.wait_for_selector("#report-panel:not([hidden])")
        page.evaluate("() => { window.__t = 0; document.addEventListener('honeycomb:timer-changed', () => window.__t++);"
                      " window.__c = 0; document.addEventListener('assistant:reports-changed', () => window.__c++); }")
        page.click("#report-approve-all")
        page.wait_for_function("() => document.getElementById('report-message').textContent.startsWith('已批准')")
        assert stub.posts == [(f"{RID}/approve", {})]                             # 一次点击 = 一个请求
        assert page.inner_text("#report-message") == "已批准 4 条，1 条已过期"
        assert page.is_visible("#report-panel") and page.locator("#report-groups > section").count() == 0
        assert page.is_hidden("#report-approve-all")
        assert page.evaluate("[window.__t, window.__c]") == [0, 1]               # 不是计时；通知别的块重拉


def test_reject_report(browser, static_base_url):
    stub = Stub(_report())
    stub.reply = {"id": RID, "status": "rejected"}
    with opened(browser, static_base_url, stub) as page:
        page.wait_for_selector("#report-panel:not([hidden])")
        page.click("#report-reject-all")
        page.wait_for_function("() => document.getElementById('report-message').textContent.includes('整份不要')")
        assert stub.posts == [(f"{RID}/reject", {})]


def test_row_approve_reject_and_edit_target(browser, static_base_url):
    stub = Stub(_report())
    with opened(browser, static_base_url, stub) as page:
        page.wait_for_selector("#report-panel:not([hidden])")
        page.click('li[data-id="i0"] .report-ok')
        page.wait_for_selector('li[data-id="i0"].is-applied')
        assert stub.posts == [(f"{RID}/items/i0/approve", {})]
        assert page.is_disabled('li[data-id="i0"] .report-ok') and page.is_disabled('li[data-id="i0"] .report-no')
        page.click('li[data-id="i2"] .report-no')
        page.wait_for_selector('li[data-id="i2"].is-rejected')
        assert stub.posts[1] == (f"{RID}/items/i2/reject", {})
        # 改：选了就是先改目标再批准；任务 = {taskId}，项目的未分类 = {projectId}
        _idle(page)
        page.select_option('li[data-id="i3"] .report-pick', "t:t_read")
        page.wait_for_selector('li[data-id="i3"].is-applied')
        assert stub.posts[2] == (f"{RID}/items/i3/approve", {"taskId": "t_read"})
        assert page.inner_text('li[data-id="i3"] .auto-target') == "→ 练琴区 / 吉他练习 / 曲目视奏"
        _idle(page)
        page.select_option('li[data-id="i4"] .report-pick', "p:p_book")
        page.wait_for_selector('li[data-id="i4"].is-applied')
        assert stub.posts[3] == (f"{RID}/items/i4/approve", {"projectId": "p_book"})


def test_failure_is_shown_as_text_and_keeps_the_report(browser, static_base_url):
    stub = Stub(_report())
    stub.fail = "报告已被新报告顶掉 <u>x</u>"
    with opened(browser, static_base_url, stub) as page:
        page.wait_for_selector("#report-panel:not([hidden])")
        page.click("#report-approve-all")
        page.wait_for_selector("#report-message.is-error")
        assert page.inner_text("#report-message") == "没成：报告已被新报告顶掉 <u>x</u>"
        assert page.locator("#report-message u").count() == 0
        _idle(page)
        assert page.locator("#report-groups li").count() == 5 and page.is_enabled("#report-approve-all")


def test_controls_are_disabled_while_a_request_is_in_flight(browser, static_base_url):
    stub = Stub(_report())
    stub.holding = True
    with opened(browser, static_base_url, stub) as page:
        page.wait_for_selector("#report-panel:not([hidden])")
        page.click("#report-approve-all")
        page.wait_for_function("() => document.getElementById('report-approve-all').disabled")
        assert page.is_disabled("#report-reject-all") and page.is_disabled('li[data-id="i0"] .report-ok')
        assert page.is_disabled('li[data-id="i0"] .report-pick')
        page.click("#report-approve-all", force=True)                              # 再点也不会发第二个
        assert len(stub.posts) == 1
        stub.holding = False
        stub.report["status"] = "approved"
        stub.hold[0].fulfill(status=200, content_type="application/json", body=json.dumps(stub.reply))
        page.wait_for_function("() => document.getElementById('report-message').textContent.startsWith('已批准')")
        assert len(stub.posts) == 1


def test_processed_rows_are_labelled_and_failed_ones_can_be_retried(browser, static_base_url):
    report = _report()
    report["items"][0].update(status="applied", applied=1)
    report["items"][2].update(status="stale", stale=1)
    report["items"][3].update(status="failed", failed=1, failure="任务不存在 <b>x</b>")
    stub = Stub(report)
    with opened(browser, static_base_url, stub) as page:
        page.wait_for_selector("#report-panel:not([hidden])")
        assert page.inner_text('li[data-id="i0"] .report-status') == "已入账"
        assert page.inner_text('li[data-id="i2"] .report-status') == "已过期（你已处理过）"
        assert page.inner_text('li[data-id="i3"] .report-status') == "没成：任务不存在 <b>x</b>"
        assert page.locator("#report-panel b").count() == 0
        assert page.is_disabled('li[data-id="i0"] .report-ok') and page.is_enabled('li[data-id="i3"] .report-ok')
        assert page.locator('li[data-id="i0"] .report-pick').count() == 0       # 已处理的不能再改


def test_keyboard_operable(browser, static_base_url):
    stub = Stub(_report())
    with opened(browser, static_base_url, stub) as page:
        page.wait_for_selector("#report-panel:not([hidden])")
        page.focus("#report-approve-all")
        page.keyboard.press("Enter")
        page.wait_for_function("() => document.getElementById('report-message').textContent.startsWith('已批准')")
        assert stub.posts == [(f"{RID}/approve", {})]
    stub = Stub(_report())
    with opened(browser, static_base_url, stub) as page:
        page.wait_for_selector("#report-panel:not([hidden])")
        label = page.get_attribute('li[data-id="i0"] .report-ok', "aria-label")
        assert label.startswith("批准：") and page.get_attribute('li[data-id="i0"] .report-pick', "aria-label").startswith("改记到：")
        page.focus('li[data-id="i0"] .report-ok')
        page.keyboard.press("Space")
        page.wait_for_selector('li[data-id="i0"].is-applied')


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("width", [320, 390])
def test_narrow_screen_no_horizontal_scroll_in_both_themes(browser, static_base_url, width, theme):
    report = _report()
    report["items"][0]["suggestions"][0]["title"] = "很长的窗口标题" * 30 + "averyveryverylongunbrokenwindowtitle" * 6
    report["items"][0]["reason"] = "理由" * 80
    report["summary"] = "概述" * 200 + "x" * 200
    with opened(browser, static_base_url, Stub(report), width=width, theme=theme) as page:
        page.wait_for_selector("#report-panel:not([hidden])")
        assert overflowing(page, "#report-panel") == []
        assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
        # 主按钮在两种主题下颜色取自 token（不是写死的）
        assert page.evaluate("getComputedStyle(document.getElementById('report-approve-all')).backgroundColor") \
            != "rgba(0, 0, 0, 0)"


def test_more_pending_reports_are_hinted(browser, static_base_url):
    stub = Stub(_report())

    def two(route: Route) -> None:
        route.fulfill(status=200, content_type="application/json", body=json.dumps({"items": [{"id": RID}, {"id": "rp_000000000002"}]}))

    with open_page(browser, static_base_url, routes={r"/api/core/activity/reports(\?[^/]*)?$": two,
                                                     r"/api/core/activity/reports/rp_[0-9]+$": stub.detail}) as page:
        page.wait_for_selector("#report-panel:not([hidden])")
        assert "另有 1 份待批准" in page.inner_text("#report-meta")


def test_pure_functions(browser, static_base_url):
    with opened(browser, static_base_url, Stub(None)) as page:
        assert page.evaluate("assistantReports.formatMinutes(90)") == "2 分"
        assert page.evaluate("assistantReports.formatMinutes(20)") == "不到 1 分"
        assert page.evaluate("assistantReports.formatMinutes(3600)") == "1 小时"
        assert page.evaluate("assistantReports.formatMinutes(4500)") == "1 小时 15 分"
        assert page.evaluate("assistantReports.target(null, {kind: 'dismiss'}).projectId") is None
