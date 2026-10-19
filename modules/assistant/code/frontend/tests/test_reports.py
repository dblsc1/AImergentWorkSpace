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
        if path.endswith("/split"):
            iid = path.split("/items/")[1].split("/")[0]
            item = next(i for i in self.report["items"] if i["id"] == iid)
            moved = [x for x in item["suggestions"] if x["id"] in body["suggestionIds"]]
            tgt = {"kind": "dismiss", "taskId": None, "projectId": None} if body.get("dismiss") else {
                "kind": "assign", "taskId": body.get("taskId"), "projectId": body.get("projectId")}
            if len(moved) == len(item["suggestions"]):
                item.update(tgt, newTask=None)
            else:
                item["suggestions"] = [x for x in item["suggestions"] if x not in moved]
                item["suggestionIds"] = [x["id"] for x in item["suggestions"]]
                self.report["items"].append({**item, **tgt, "id": "s" + str(len(self.report["items"])), "newTask": None,
                                             "suggestions": moved})
            route.fulfill(status=200, content_type="application/json", body=json.dumps({"reportId": RID, "itemId": iid, "moved": len(moved)}))
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


def test_lists_one_collapsed_row_per_window_longest_first_and_everything_is_text(browser, static_base_url):
    stub = Stub(_report())
    with opened(browser, static_base_url, stub) as page:
        page.wait_for_selector("#report-panel:not([hidden])")
        assert page.inner_text("#report-count") == "5"
        meta = page.inner_text("#report-meta")
        assert "作者 hermes " + EVIL in meta and "共 5 条" in meta and "覆盖约 3 小时 55 分" in meta
        assert "记到项目 / 任务 3 条" in meta and "新建任务 1 条" in meta and "忽略 1 条" in meta
        assert page.inner_text("#report-summary") == "归类 8 段 " + EVIL
        wins = page.locator("#report-groups .report-win")
        assert wins.count() == 5 and page.locator(".report-seg").count() == 0          # 默认全收起
        # 时长：kitty ssh 3000 > save.gd 1200 > plot 600 > stale 300；全是「忽略」的最后
        assert page.eval_on_selector_all(".report-win .report-toggle", "els => els.map(e => e.textContent)") == [
            "▸ kitty · ssh", "▸ code · save.gd", "▸ code · plot.gd — " + EVIL, "▸ code · stale.gd", "▸ slack · 闲聊"]
        ssh = wins.nth(0)
        assert ssh.locator(".report-dur").inner_text() == "2 段 · 50 分"
        assert ssh.locator(".auto-target").inner_text() == "→ 练琴区 / 考级计划 · 未分类"
        assert wins.nth(1).locator(".auto-target").inner_text() == "→ 新建任务「重构存档」（练琴区 / 吉他练习）"
        assert wins.nth(2).locator(".auto-target").inner_text() == "→ 练琴区 / 吉他练习 / 音阶练习"
        assert wins.nth(4).locator(".auto-target").inner_text() == "→ 忽略（当噪声）"
        assert wins.nth(4).locator(".report-pick").count() == 1 and wins.nth(4).locator(".report-no").is_disabled()
        assert page.locator("#report-panel img").count() == 0 and page.evaluate("window.__pwned") is None
        assert stub.posts == []                                                   # 只是看，什么都没发


def test_item_with_no_visible_segments_gets_a_fallback_row(browser, static_base_url):
    report = _report()
    report["items"][4].update(suggestions=[], staleNow=2, seconds=0)
    with opened(browser, static_base_url, Stub(report)) as page:
        page.wait_for_selector("#report-panel:not([hidden])")
        row = page.locator(".report-orphan")
        assert row.count() == 1 and row.locator(".unc-what").inner_text() == "2 条已过期或已处理"
        assert row.locator(".auto-target").inner_text() == "→ 练琴区 / 吉他练习 / 曲目视奏"
        assert page.is_enabled("#report-approve-all")


def test_partial_move_failure_says_how_far_it_got(browser, static_base_url):
    report = _report()
    report["items"].append(_item("i5", "assign", 300, [_sug("s6", "kitty", "ssh", 300)], taskId="t_read"))
    stub = Stub(report)
    orig = stub.finish

    def flaky(route, path, body):
        if "/items/i5/" in path:
            stub.fail = "这一条刚被改动"
        orig(route, path, body)
        stub.fail = None

    stub.finish = flaky
    with opened(browser, static_base_url, stub) as page:
        page.wait_for_selector("#report-panel:not([hidden])")
        page.locator(".report-win").first.locator(".report-row:not(.report-seg) .report-pick").select_option("p:p_eng")
        page.wait_for_selector("#report-message.is-error")
        assert page.inner_text("#report-message") == "没成：已挪 1/2 条后失败：这一条刚被改动"
        assert page.locator(".report-no >> nth=0").inner_text() == "当噪声"


def test_expand_lists_segments_and_mixed_targets_are_shown(browser, static_base_url):
    report = _report()
    report["items"].append(_item("i5", "assign", 300, [_sug("s6", "KITTY", "✳ ssh", 300)], taskId="t_read"))
    stub = Stub(report)
    with opened(browser, static_base_url, stub) as page:
        page.wait_for_selector("#report-panel:not([hidden])")
        ssh = page.locator("#report-groups .report-win").first
        assert "3 段 · 55 分" in ssh.locator(".report-dur").inner_text()           # 大小写、前导符号不同也归一个窗口
        tgt = ssh.locator(".report-wintarget")
        assert "混合（2 个去向" in tgt.inner_text() and "is-mixed" in tgt.get_attribute("class")
        ssh.locator(".report-toggle").click()
        assert ssh.locator(".report-toggle").get_attribute("aria-expanded") == "true"
        segs = ssh.locator(".report-seg")
        assert segs.count() == 3
        assert sorted(segs.locator(".auto-target").all_inner_texts()) == [
            "→ 练琴区 / 吉他练习 / 曲目视奏", "→ 练琴区 / 考级计划 · 未分类", "→ 练琴区 / 考级计划 · 未分类"]
        ssh.locator(".report-toggle").click()
        assert page.locator(".report-seg").count() == 0 and stub.posts == []


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


def test_move_one_segment_then_window_then_drop_only_split_nothing_approved(browser, static_base_url):
    stub = Stub(_report())
    with opened(browser, static_base_url, stub) as page:
        page.wait_for_selector("#report-panel:not([hidden])")
        ssh = page.locator('.report-win').first
        ssh.locator(".report-toggle").click()
        ssh.locator(".report-seg").first.locator(".report-pick").select_option("t:t_read")   # 只挪第一段（开始最早的 s2）
        page.wait_for_function("() => document.getElementById('report-message').textContent.startsWith('已改')")
        assert stub.posts == [(f"{RID}/items/i2/split", {"suggestionIds": ["s2"], "taskId": "t_read"})]
        ssh = page.locator('.report-win').first
        assert ssh.locator(".report-toggle").get_attribute("aria-expanded") == "true"        # 重绘后还展开着
        assert "混合" in ssh.locator(".report-wintarget").inner_text()
        # 整窗口改到一个项目：混合的两条各发一个 split；挪走一整条 = 就地改目标
        _idle(page)
        ssh.locator(".report-row:not(.report-seg) .report-pick").first.select_option("p:p_eng")
        page.wait_for_function("() => document.querySelectorAll('.report-win')[0].querySelector('.report-wintarget').textContent.indexOf('混合') < 0")
        assert len(stub.posts) == 3 and all(b["projectId"] == "p_eng" for _, b in stub.posts[1:])
        assert sorted(b["suggestionIds"][0] for _, b in stub.posts[1:]) == ["s2", "s3"]
        # 不要 = 忽略（split dismiss），不是 reject
        _idle(page)
        page.locator('.report-win').first.locator(".report-row:not(.report-seg) .report-no").click()
        page.wait_for_function("() => document.querySelectorAll('.report-win .report-wintarget')[4].textContent === '→ 忽略（当噪声）' && document.querySelectorAll('.report-win .report-wintarget')[3].textContent === '→ 忽略（当噪声）'")
        assert stub.posts[-1][0].endswith("/split") and stub.posts[-1][1]["dismiss"] is True
        assert not any(p.endswith("/approve") or p.endswith("/reject") for p, _ in stub.posts)


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
        assert page.locator("#report-groups .report-win").count() == 5 and page.is_enabled("#report-approve-all")


def test_controls_are_disabled_while_a_request_is_in_flight(browser, static_base_url):
    stub = Stub(_report())
    stub.holding = True
    with opened(browser, static_base_url, stub) as page:
        page.wait_for_selector("#report-panel:not([hidden])")
        page.click("#report-approve-all")
        page.wait_for_function("() => document.getElementById('report-approve-all').disabled")
        assert page.is_disabled("#report-reject-all") and page.is_disabled(".report-win .report-no >> nth=0")
        assert page.is_disabled(".report-win .report-pick >> nth=0")
        page.click("#report-approve-all", force=True)                              # 再点也不会发第二个
        assert len(stub.posts) == 1
        stub.holding = False
        stub.report["status"] = "approved"
        stub.hold[0].fulfill(status=200, content_type="application/json", body=json.dumps(stub.reply))
        page.wait_for_function("() => document.getElementById('report-message').textContent.startsWith('已批准')")
        assert len(stub.posts) == 1


def test_processed_segments_are_labelled_and_cannot_be_moved(browser, static_base_url):
    report = _report()
    report["items"][0].update(status="applied", applied=1)
    report["items"][2].update(status="stale", stale=1)
    report["items"][3].update(status="failed", failed=1, failure="任务不存在 <b>x</b>")
    stub = Stub(report)
    with opened(browser, static_base_url, stub) as page:
        page.wait_for_selector("#report-panel:not([hidden])")
        page.evaluate("() => document.querySelectorAll('.report-toggle').forEach(b => b.click())")
        seg = lambda sid: page.locator(f'.report-seg[data-sid="{sid}"]')
        assert seg("s0").locator(".report-status").inner_text() == "已入账"
        assert seg("s2").locator(".report-status").inner_text() == "已过期（你已处理过）"
        assert seg("s4").locator(".report-status").inner_text() == "没成：任务不存在 <b>x</b>"
        assert page.locator("#report-panel b").count() == 0
        assert seg("s0").locator(".report-pick").count() == 0                  # 已处理的不能再改
        assert seg("s4").locator(".report-pick").count() == 1                  # 没成的可以改了重试


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
        tog = page.locator(".report-toggle").first
        assert tog.get_attribute("aria-label").startswith("展开窗口：")
        tog.focus()
        page.keyboard.press("Space")
        assert page.locator(".report-seg").count() == 2
        assert page.get_attribute(".report-seg .report-pick", "aria-label").startswith("这一段改记到：")


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("width", [320, 390])
def test_narrow_screen_no_horizontal_scroll_in_both_themes(browser, static_base_url, width, theme):
    report = _report()
    report["items"][0]["suggestions"][0]["title"] = "很长的窗口标题" * 30 + "averyveryverylongunbrokenwindowtitle" * 6
    report["items"][0]["reason"] = "理由" * 80
    report["summary"] = "概述" * 200 + "x" * 200
    with opened(browser, static_base_url, Stub(report), width=width, theme=theme) as page:
        page.wait_for_selector("#report-panel:not([hidden])")
        page.evaluate("() => document.querySelectorAll('.report-toggle').forEach(b => b.click())")
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
