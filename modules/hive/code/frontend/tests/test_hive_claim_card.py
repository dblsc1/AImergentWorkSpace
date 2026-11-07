"""新建任务 / 项目后的补记卡（onboarding.v1「五」「八」3；HV-C01..C11）。

candidates / claim 端点在浏览器侧用 page.route() 桩掉；夹具沿用 test_hive_focus.py。
提交表单用页面里现造的 form（hex-crud.js 挂在 #hive 上的委托），不依赖蜂巢布局。
需要 playwright；没装就跳过。
"""

from __future__ import annotations

from contextlib import contextmanager
import json
from typing import Any, Iterator

import pytest

pytest.importorskip("playwright.sync_api", reason="没装 playwright：本文件在 ring 前端测试 job 里跑")

from test_hive_focus import base, browser, open_hive  # noqa: E402,F401
from test_hive_tiers import tiers_setup  # noqa: E402

RAW = "ptyxis · garden"


def _item(key: str, name: str, match: str, seconds: int) -> dict[str, Any]:
    return {"blockKey": key, "displayName": name, "match": match, "days": 1, "seconds": seconds,
            "firstDate": "2026-11-01", "lastDate": "2026-11-01"}


ITEMS = [_item(RAW, "garden", "agent", 3600), _item("b2", "写提示词", "name", 3600), _item("b3", "别的", "ai", 1800)]


class Stub:
    def __init__(self, hive: Any, items: list[dict[str, Any]], claim: tuple[int, Any] = (200, {"totalSeconds": 7200})) -> None:
        self.hive, self.claim, self.asked, self.claims = hive, claim, [], []
        hive.page.route("**/api/core/activity/unclassified/candidates**", self._cand(items))
        hive.page.route("**/api/core/activity/blocks/claim", self._claim)
        hive.page.route("**/api/core/planner/tasks", lambda r: self._ok(r, {"id": "t_new", "projectId": "p_math", "name": "x"}))
        hive.page.route("**/api/core/planner/projects", lambda r: self._ok(r, {"id": "p_new", "zoneId": "z_a", "name": "x"}))

    @staticmethod
    def _ok(route: Any, body: Any) -> None:
        if route.request.method != "POST":
            route.fallback()
            return
        route.fulfill(status=201, content_type="application/json", body=json.dumps(body))

    def _cand(self, items: list[dict[str, Any]]) -> Any:
        def handler(route: Any) -> None:
            self.asked.append(route.request.url.split("candidates?", 1)[1])
            route.fulfill(status=200, content_type="application/json", body=json.dumps({"items": items, "totalSeconds": 0}))
        return handler

    def _claim(self, route: Any) -> None:
        self.claims.append(json.loads(route.request.post_data))
        status, body = self.claim
        route.fulfill(status=status, content_type="application/json", body=json.dumps(body))

    def create_task(self) -> None:
        self.hive.page.evaluate("""() => {
            const cell = document.querySelector('.hex-cell[data-project-id=p_math]');
            const f = document.createElement('form'); f.dataset.hexAction = 'create-task';
            f.innerHTML = '<input name="name" value="写提示词"><button type="submit">增加</button>';
            cell.appendChild(f); f.querySelector('button').click(); }""")

    def create_project(self) -> None:
        self.hive.page.evaluate("""() => {
            const hive = document.querySelector('#hive');
            const cell = document.createElement('div'); cell.className = 'hex-cell'; cell.dataset.zoneId = 'z_a';
            const f = document.createElement('form'); f.dataset.hexAction = 'create-project';
            f.innerHTML = '<input name="name" value="garden"><button type="submit">增加</button>';
            cell.appendChild(f); hive.appendChild(cell); f.querySelector('button').click(); }""")


@contextmanager
def _open(browser: Any, base: str, items: list[dict[str, Any]] = ITEMS) -> Iterator[Any]:
    with open_hive(browser, base, fake_clock=False) as hive:
        yield hive, Stub(hive, items)


def test_new_task_asks_candidates_with_task_id_and_shows_card(browser, base) -> None:  # noqa: F811
    with _open(browser, base) as (hive, stub):
        stub.create_task()
        hive.page.wait_for_selector(".hcc-card")
        assert stub.asked == ["taskId=t_new"]
        assert hive.page.locator(".hcc-title").inner_text() == "找到 3 个窗口块，共 2.5 小时，可能属于它"


def test_new_project_asks_candidates_with_project_id(browser, base) -> None:  # noqa: F811
    with _open(browser, base) as (hive, stub):
        stub.create_project()
        hive.page.wait_for_selector(".hcc-card")
        assert stub.asked == ["projectId=p_new"]


def test_no_items_no_card(browser, base) -> None:  # noqa: F811
    with _open(browser, base, items=[]) as (hive, stub):
        stub.create_task()
        hive.page.wait_for_timeout(300)
        assert stub.asked == ["taskId=t_new"] and hive.page.locator(".hcc-card").count() == 0


def test_default_checked_and_uncheck_then_claim_sends_only_checked(browser, base) -> None:  # noqa: F811
    with _open(browser, base) as (hive, stub):
        stub.create_task()
        hive.page.wait_for_selector(".hcc-card")
        boxes = hive.page.locator(".hcc-row input")
        assert [boxes.nth(i).is_checked() for i in range(3)] == [True, True, False]
        boxes.nth(1).uncheck()
        hive.page.locator("[data-hcc=claim]").click()
        hive.page.wait_for_selector(".hcc-toast")
        assert stub.claims == [{"taskId": "t_new", "blockKeys": [RAW], "remember": True}]
        assert hive.page.locator(".hcc-toast").inner_text() == "已记进 2 小时"
        assert hive.page.locator(".hcc-card").count() == 0


def test_all_unchecked_disables_claim(browser, base) -> None:  # noqa: F811
    with _open(browser, base) as (hive, stub):
        stub.create_task()
        hive.page.wait_for_selector(".hcc-card")
        hive.page.locator(".hcc-row input").nth(0).uncheck()
        hive.page.locator(".hcc-row input").nth(1).uncheck()
        assert hive.page.locator("[data-hcc=claim]").is_disabled()


def test_claim_failure_keeps_card_and_says_why(browser, base) -> None:  # noqa: F811
    with open_hive(browser, base, fake_clock=False) as hive:
        stub = Stub(hive, ITEMS, claim=(422, {"detail": "规则超限"}))
        stub.create_task()
        hive.page.wait_for_selector(".hcc-card")
        hive.page.locator("[data-hcc=claim]").click()
        hive.page.wait_for_selector(".hcc-err:not(:empty)")
        assert "规则超限" in hive.page.locator(".hcc-err").inner_text()
        assert hive.page.locator(".hcc-card").count() == 1


def test_only_display_name_shows_never_the_raw_block_key(browser, base) -> None:  # noqa: F811
    with _open(browser, base) as (hive, stub):
        stub.create_task()
        hive.page.wait_for_selector(".hcc-card")
        text = hive.page.locator(".hcc-card").inner_text()
        assert "garden" in text and "ptyxis" not in text


def test_decision_bar_has_no_pending_block_section(browser, base) -> None:  # noqa: F811
    with open_hive(browser, base, fake_clock=False, setup=lambda h: tiers_setup(h, True, None)) as hive:
        hive.page.evaluate("() => window.NexusTableHexApp.refresh()")
        hive.page.wait_for_selector(".hd-head")
        hive.page.locator(".hd-head").click()
        hive.page.wait_for_selector(".hb-head")
        assert "待分类窗口块" not in hive.page.locator(".hb-title").all_inner_texts()
        assert hive.page.locator(".ht-block").count() == 0
