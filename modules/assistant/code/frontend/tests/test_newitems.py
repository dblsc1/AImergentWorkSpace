"""「新项目与任务」面板（newitems.js，nexus-core v2.30）。接口全在浏览器侧桩掉。

判据：列出了什么（项目卡 + 下面的任务、现有项目下的任务，全是文本）、点一下发了什么请求（顺序）、
项目没批准时任务不能批准、失败怎么报、面板在不在、窄屏不横滚。
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from conftest import open_page, overflowing
from playwright.sync_api import Route

EVIL = '<img src=x onerror="window.__pwned=1">'


def _task(tid: str, name: str, **over: Any) -> dict:
    return {"id": tid, "kind": "task", "status": "pending", "name": name, "reason": "理由 " + EVIL, "createdAt": "x",
            "createdId": None, "projectId": None, "projectName": None, "projectProposalId": "np_1", **over}


def _data() -> dict:
    return {"projects": [{"id": "np_1", "kind": "project", "status": "pending", "name": "新项目 " + EVIL, "reason": "窗口没项目 " + EVIL,
                          "createdAt": "x", "createdId": None, "zoneId": "z_study", "zoneName": "练琴区 " + EVIL,
                          "tasks": [_task("nt_1", "子任务一"), _task("nt_2", "子任务二")]}],
            "tasks": [_task("nt_3", "挂现成项目", projectId="p_eng", projectName="吉他练习", projectProposalId=None)]}


class Stub:
    def __init__(self, data: dict | None) -> None:
        self.data = data
        self.posts: list[str] = []
        self.fail_on: str | None = None

    def listing(self, route: Route) -> None:
        if self.data is None:
            route.fulfill(status=404, content_type="application/json", body='{"detail":"Not Found"}')
        else:
            route.fulfill(status=200, content_type="application/json", body=json.dumps(self.data, ensure_ascii=False))

    def act(self, route: Route) -> None:
        tail = route.request.url.split("/activity/proposals/")[1]
        iid, verb = tail.split("/")
        self.posts.append(tail)
        if iid == self.fail_on:
            route.fulfill(status=409, content_type="application/json", body='{"detail":"它所属的新项目还没批准"}')
            return
        d = self.data
        if verb == "approve":
            for p in d["projects"][:]:
                if p["id"] == iid:
                    d["projects"].remove(p)
                    d["tasks"] += [{**t, "projectId": "p_made", "projectName": "新项目", "projectProposalId": None} for t in p["tasks"]]
            d["tasks"] = [t for t in d["tasks"] if t["id"] != iid]
            for p in d["projects"]:
                p["tasks"] = [t for t in p["tasks"] if t["id"] != iid]
        else:
            d["projects"] = [p for p in d["projects"] if p["id"] != iid]
            d["tasks"] = [t for t in d["tasks"] if t["id"] != iid]
            for p in d["projects"]:
                p["tasks"] = [t for t in p["tasks"] if t["id"] != iid]
        route.fulfill(status=200, content_type="application/json", body="{}")


def opened(browser, base, stub: Stub, width: int = 1100):
    return open_page(browser, base, width=width, routes={
        r"/api/core/activity/proposals\?": stub.listing, r"/api/core/activity/proposals/[^/]+/(approve|reject)$": stub.act})


@pytest.mark.parametrize("mode", ["empty", "missing"])
def test_panel_hidden_without_proposals(browser, static_base_url, mode):
    stub = Stub({"projects": [], "tasks": []} if mode == "empty" else None)
    errors: list[str] = []
    with opened(browser, static_base_url, stub) as page:
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.wait_for_timeout(300)
        assert page.is_hidden("#newitems-panel")
    assert errors == [] and stub.posts == []


def test_lists_projects_with_tasks_and_everything_is_text(browser, static_base_url):
    stub = Stub(_data())
    with opened(browser, static_base_url, stub) as page:
        page.wait_for_selector("#newitems-panel:not([hidden])")
        assert page.inner_text("#newitems-count") == "2"
        card = page.locator('section[data-id="np_1"]')
        assert card.locator(".unc-project").inner_text() == "新项目「新项目 " + EVIL + "」"
        assert "分区：练琴区 " + EVIL in card.locator(".unc-total").inner_text()
        assert "理由：窗口没项目 " + EVIL in card.inner_text()
        # 项目没批准：它下面的任务「批准」禁用，「不要」可点；现有项目下的任务两个都可点
        assert card.locator('li[data-id="nt_1"] .report-ok').is_disabled() and card.locator('li[data-id="nt_1"] .report-no').is_enabled()
        assert "吉他练习" in page.inner_text('li[data-id="nt_3"]') and page.locator('li[data-id="nt_3"] .report-ok').is_enabled()
        assert page.evaluate("window.__pwned") is None and page.locator("#newitems-panel img").count() == 0
        assert overflowing(page, "#newitems-panel") == []


def test_approve_project_and_tasks_posts_in_order(browser, static_base_url):
    stub = Stub(_data())
    with opened(browser, static_base_url, stub) as page:
        page.wait_for_selector("#newitems-panel:not([hidden])")
        page.click('section[data-id="np_1"] .newitems-all')
        page.wait_for_selector("#newitems-message:not([hidden])")
        assert stub.posts == ["np_1/approve", "nt_1/approve", "nt_2/approve"]
        assert "已批准项目" in page.inner_text("#newitems-message") and page.locator("section[data-id]").count() == 0


def test_only_project_then_task_unlocks_and_failure_stops_chain(browser, static_base_url):
    stub = Stub(_data())
    stub.fail_on = "nt_1"
    with opened(browser, static_base_url, stub) as page:
        page.wait_for_selector("#newitems-panel:not([hidden])")
        page.click('section[data-id="np_1"] .newitems-all')
        page.wait_for_selector("#newitems-message.is-error")
        assert stub.posts == ["np_1/approve", "nt_1/approve"]     # 失败就停，nt_2 没发
        assert "没成：它所属的新项目还没批准" in page.inner_text("#newitems-message")
        assert page.locator('li[data-id="nt_2"] .report-ok').is_enabled()   # 项目批准了：任务各自可批准


def test_reject_project_and_single_task(browser, static_base_url):
    stub = Stub(_data())
    with opened(browser, static_base_url, stub) as page:
        page.wait_for_selector("#newitems-panel:not([hidden])")
        page.click('li[data-id="nt_3"] .report-no')
        page.wait_for_selector('li[data-id="nt_3"]', state="detached")
        page.click('section[data-id="np_1"] .newitems-no')
        page.wait_for_selector("section[data-id]", state="detached")
        assert stub.posts == ["nt_3/reject", "np_1/reject"]
        assert "已不要项目" in page.inner_text("#newitems-message")


def test_narrow_screen_does_not_scroll_sideways(browser, static_base_url):
    with opened(browser, static_base_url, Stub(_data()), width=360) as page:
        page.wait_for_selector("#newitems-panel:not([hidden])")
        assert overflowing(page, "#newitems-panel") == []
