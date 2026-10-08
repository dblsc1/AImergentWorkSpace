"""「待分类」面板（unclassified.js，nexus-core v2.11「改挂未分类时间」，仓主 2026-10-08）。

同本套件其余文件：真浏览器、接口全在浏览器侧桩掉，一个字节都不写库。判据落在行为上：
列出了什么、发了什么请求、哪一行拿掉了、失败怎么报、面板在不在、窄屏会不会横滚。
"""

from __future__ import annotations

import contextlib
import json
import re
from typing import Any, Iterator
from urllib.parse import parse_qs, urlparse

import pytest
from conftest import TREE, open_page, overflowing
from playwright.sync_api import Browser, Page, Route

BUCKET_ENG, BUCKET_BOOK = "t_unc_p_eng", "t_unc_p_book"
UNC_TREE = json.loads(json.dumps(TREE))
UNC_TREE["projects"][0]["unclassifiedTaskId"] = BUCKET_ENG
UNC_TREE["projects"][1].update(unclassifiedTaskId=BUCKET_BOOK, name="<i>考级</i>计划",
                               tasks=[{"id": "t_theory", "key": "k", "name": "乐理", "done": True}])
UNC_TREE["projects"].append({"id": "p_none", "key": "k", "zoneId": "z_work", "name": "没有桶", "tasks": [],
                             "unclassifiedTaskId": None})


def _seg(event_id: str, start: str, seconds: int, source: str = "timer-backend") -> dict:
    return {"id": event_id, "type": "session.completed", "source": source, "time": start,
            "subject": {"zone": "z_study", "project": "p_x", "task": "t_unc"},
            "data": {"startAt": start, "durationSeconds": seconds}}


def _sessions() -> dict[str, list[dict]]:
    return {
        BUCKET_ENG: [_seg("evt_1", "2026-10-08T09:05:00+08:00", 1920),
                     _seg("evt_2", "2026-10-07T21:30:00+08:00", 600, "manual-backfill"),
                     _seg("evt_3", "2026-10-07T08:00:00+08:00", 20, "<b>ext</b>")],
        BUCKET_BOOK: [_seg("evt_9", "2026-10-06T10:00:00+08:00", 7200, "activity-confirmed")],
    }


class Stub:
    """events：按 taskId 回那个桶上的段（``sessions=None`` = 404）。reassign：记下请求，成功就把段从桶里拿走
    （``{projectId}`` = 放回那个项目的桶）；``fail`` 里的事件 id 回 409。"""

    def __init__(self, sessions: dict[str, list[dict]] | None) -> None:
        self.sessions = sessions
        self.posts: list[tuple[str, Any]] = []
        self.gets: list[str] = []
        self.fail: set[str] = set()
        self.away: dict[str, dict] = {}
        self.hold = False
        self.held: list[Route] = []
        self.extra_total = 0

    def events(self, route: Route) -> None:
        query = parse_qs(urlparse(route.request.url).query)
        assert (query["type"], query["limit"]) == (["session.completed"], ["200"]), query
        task = query.get("taskId", [""])[0]
        self.gets.append(task)
        if self.sessions is None:
            route.fulfill(status=404, content_type="application/json", body='{"detail":"Not Found"}')
            return
        items = self.sessions.get(task, [])
        route.fulfill(status=200, content_type="application/json", body=json.dumps(
            {"total": len(items) + (self.extra_total if items else 0), "items": items}, ensure_ascii=False))

    def reassign(self, route: Route) -> None:
        if self.hold:
            self.held.append(route)
            return
        self.answer(route)

    def answer(self, route: Route) -> None:
        event_id = route.request.url.rstrip("/").split("/")[-2]
        body = json.loads(route.request.post_data)
        self.posts.append((event_id, body))
        if event_id in self.fail:
            route.fulfill(status=409, content_type="application/json", body=json.dumps(
                {"detail": "这一段正被并发改挂，请重试 <u>x</u>"}, ensure_ascii=False))
            return
        if "projectId" in body:  # 撤回 / 归入某项目的未分类：放进那个项目的桶
            item = self.away.pop(event_id, None)
            if item is None:
                for items in self.sessions.values():
                    for hit in [i for i in items if i["id"] == event_id]:
                        items.remove(hit)
                        item = hit
            self.sessions.setdefault("t_unc_" + body["projectId"], []).append(item)
        else:
            for items in self.sessions.values():
                for item in [i for i in items if i["id"] == event_id]:
                    items.remove(item)
                    self.away[event_id] = item
        route.fulfill(status=200, content_type="application/json", body=json.dumps(
            {"sessionEventId": event_id, "duplicate": event_id == "evt_2", "seq": 1}))


@contextlib.contextmanager
def opened(browser: Browser, base: str, sessions: Any = "default", width: int = 1100,
           tree: dict = UNC_TREE) -> Iterator[tuple[Page, Stub]]:
    stub = Stub(_sessions() if sessions == "default" else sessions)
    routes = {
        r"/api/core/views/tree$": lambda r: r.fulfill(
            status=200, content_type="application/json", body=json.dumps(tree, ensure_ascii=False)),
        r"/api/core/events\?": stub.events,
        r"/api/core/sessions/[^/]+/reassign$": stub.reassign,
    }
    with open_page(browser, base, routes=routes, width=width) as page:
        yield page, stub


ENG = 'section[data-project="p_eng"]'
BOOK = 'section[data-project="p_book"]'


def _rows(page: Page, group: str) -> list[str]:
    return [r.get_attribute("data-id") for r in page.locator(f"{group} .unc-row").all()]


def _gone(page: Page, event_id: str) -> None:
    page.wait_for_function("id => !document.querySelector('.unc-row[data-id=\"' + id + '\"]')", arg=event_id)


def _idle(page: Page) -> None:
    """一次操作（含之后的重拉）做完：下拉重新可用。"""
    page.wait_for_function("() => { const s = document.querySelector('.unc-task'); return !s || !s.disabled; }")


# ------------------------------------------------------------------ 出不出现


@pytest.mark.parametrize("sessions", [None, {}, {BUCKET_ENG: [], BUCKET_BOOK: []}])
def test_panel_stays_hidden_without_anything_to_classify(browser, static_base_url, sessions):
    errors: list[str] = []
    with opened(browser, static_base_url, sessions) as (page, stub):
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.wait_for_timeout(300)
        assert sorted(stub.gets) == [BUCKET_BOOK, BUCKET_ENG]  # 只问有桶的项目
        assert page.is_hidden("#unclassified-panel")
    assert errors == []


def test_no_bucket_in_tree_means_no_request_at_all(browser, static_base_url):
    with opened(browser, static_base_url, tree=TREE) as (page, stub):
        page.wait_for_timeout(300)
        assert stub.gets == [] and page.is_hidden("#unclassified-panel")


# ------------------------------------------------------------------ 列表


def test_groups_by_project_with_totals_rows_and_badges(browser, static_base_url):
    with opened(browser, static_base_url) as (page, stub):
        page.wait_for_selector("#unclassified-panel:not([hidden])")
        assert page.inner_text("#unc-count") == "4"
        # 时间多的项目在前；项目名里的标签只当文本
        assert [s.get_attribute("data-project") for s in page.locator(".unc-group").all()] == ["p_book", "p_eng"]
        assert page.inner_text(f"{BOOK} .unc-project") == "练琴区 / <i>考级</i>计划"
        assert page.locator(f"{BOOK} i").count() == 0
        assert page.inner_text(f"{BOOK} .unc-total") == "1 段 · 共 120 分"
        assert page.inner_text(f"{ENG} .unc-total") == "3 段 · 共 42 分"
        assert [r.inner_text() for r in page.locator(f"{ENG} .unc-what").all()] == [
            "临时任务 10-08 09:05 · 32 分", "临时任务 10-07 21:30 · 10 分", "临时任务 10-07 08:00 · 不到 1 分"]
        assert [b.inner_text() for b in page.locator(".unc-source").all()] == ["电脑检测", "计时", "补登", "<b>ext</b>"]
        assert page.locator(".unc-source b").count() == 0
        # 下拉只列本项目的任务（+ 其他项目…）；没选任务时归入不能点
        assert [o.inner_text() for o in page.locator(f"{ENG} .unc-task option").all()] == [
            "选任务…", "音阶练习", "曲目视奏", "旧任务（无前置字段）", "其他项目…"]
        assert [o.inner_text() for o in page.locator(f"{BOOK} .unc-task option").all()] == [
            "选任务…", "乐理（已完成）", "其他项目…"]
        assert all(b.is_disabled() for b in page.locator(".unc-one, .unc-all").all())
        assert page.is_hidden("#unc-status") and stub.posts == []


def test_more_than_a_page_is_said_out_loud(browser, static_base_url):
    with opened(browser, static_base_url) as (page, stub):
        page.wait_for_selector(ENG)
        stub.extra_total = 5
        page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
        page.wait_for_selector(f"{ENG} .suggest-more")
        assert page.inner_text(f"{ENG} .suggest-more") == "还有 5 段没列出来，归完这些再看。"
        assert page.inner_text("#unc-count") == "14"


# ------------------------------------------------------------------ 归入


def test_one_row_goes_to_the_picked_task_and_disappears(browser, static_base_url):
    with opened(browser, static_base_url) as (page, stub):
        page.wait_for_selector(ENG)
        page.select_option(f"{ENG} .unc-task", "t_read")
        assert page.locator(f"{BOOK} .unc-all").is_disabled()  # 别的组没选任务，仍不能点
        page.click('.unc-row[data-id="evt_1"] .unc-one')
        _gone(page, "evt_1")
        _idle(page)
        assert stub.posts == [("evt_1", {"taskId": "t_read"})]
        assert _rows(page, ENG) == ["evt_2", "evt_3"] and page.inner_text("#unc-count") == "3"
        assert page.inner_text("#unc-message") == "已归入 1 段 → 练琴区 / 吉他练习 / 曲目视奏"
        assert "is-error" not in page.get_attribute("#unc-message", "class")
        assert page.input_value(f"{ENG} .unc-task") == "t_read"  # 选的任务留着，接着归下一段


def test_bulk_sends_each_segment_and_reports_partial_failure(browser, static_base_url):
    with opened(browser, static_base_url) as (page, stub):
        page.wait_for_selector(ENG)
        stub.fail = {"evt_3"}
        page.select_option(f"{ENG} .unc-task", "t_word")
        page.click(f"{ENG} .unc-all")
        _gone(page, "evt_2")  # evt_2 的响应是 duplicate:true —— 也算成功
        _idle(page)
        assert stub.posts == [(e, {"taskId": "t_word"}) for e in ("evt_1", "evt_2", "evt_3")]  # 一个接一个，按页面顺序
        assert _rows(page, ENG) == ["evt_3"], "失败的那段留着，成功的不回滚"
        message = page.locator("#unc-message")
        assert message.inner_text() == "1 / 3 段没归入：这一段正被并发改挂，请重试 <u>x</u>"
        assert "is-error" in message.get_attribute("class") and message.locator("u").count() == 0
        assert _rows(page, BOOK) == ["evt_9"]


def test_other_projects_escape_and_back(browser, static_base_url):
    with opened(browser, static_base_url) as (page, stub):
        page.wait_for_selector(BOOK)
        sel = f"{BOOK} .unc-task"
        page.select_option(sel, "__other")
        assert [g.get_attribute("label") for g in page.locator(f"{sel} optgroup").all()] == [
            "练琴区 / 吉他练习", "练琴区 / <i>考级</i>计划", "制作区 / 没有桶"]   # 没有任务的项目也在
        assert page.locator(f"{sel} option").first.inner_text() == "选任务…" and page.input_value(sel) == ""
        assert page.locator(f"{BOOK} .unc-all").is_disabled()
        page.select_option(sel, "t_word")  # 别的项目的任务
        page.click(f"{BOOK} .unc-all")
        _gone(page, "evt_9")
        _idle(page)
        assert stub.posts == [("evt_9", {"taskId": "t_word"})]
        assert page.locator(BOOK).count() == 0  # 这个项目归完了，组没了
        assert page.inner_text("#unc-message") == "已归入 1 段 → 练琴区 / 吉他练习 / 音阶练习"
        # 另一组：展开后可以换回只看本项目，之前的选择作废
        page.select_option(f"{ENG} .unc-task", "__other")
        page.select_option(f"{ENG} .unc-task", "t_theory")
        page.select_option(f"{ENG} .unc-task", "__own")
        assert page.input_value(f"{ENG} .unc-task") == "" and page.locator(f"{ENG} .unc-all").is_disabled()
        assert page.locator(f"{ENG} .unc-task optgroup").count() == 0


def test_undo_puts_the_last_batch_back(browser, static_base_url):
    with opened(browser, static_base_url) as (page, stub):
        page.wait_for_selector(BOOK)
        assert page.is_hidden("#unc-undo")
        page.select_option(f"{BOOK} .unc-task", "t_theory")
        page.click(f"{BOOK} .unc-all")
        _gone(page, "evt_9")
        _idle(page)
        page.click("#unc-undo")
        page.wait_for_selector('.unc-row[data-id="evt_9"]')
        assert stub.posts == [("evt_9", {"taskId": "t_theory"}), ("evt_9", {"projectId": "p_book"})]
        assert page.inner_text("#unc-message") == "已撤回 1 段，放回未分类"
        assert page.is_hidden("#unc-undo")  # 只管最近一次


def test_everything_classified_keeps_the_message_then_hides_on_reload(browser, static_base_url):
    with opened(browser, static_base_url, {BUCKET_BOOK: _sessions()[BUCKET_BOOK], BUCKET_ENG: []}) as (page, stub):
        page.wait_for_selector(BOOK)
        page.select_option(f"{BOOK} .unc-task", "t_theory")
        page.click(f"{BOOK} .unc-one")
        _gone(page, "evt_9")
        page.wait_for_selector("#unc-undo:not([hidden]):not([disabled])")
        assert page.is_visible("#unclassified-panel") and page.locator(".unc-group").count() == 0
        assert page.inner_text("#unc-count") == ""
        page.reload()
        page.wait_for_selector("#settings-panel", state="attached")
        page.wait_for_timeout(300)
        assert page.is_hidden("#unclassified-panel")


def test_busy_guard_blocks_second_action_until_reload_done(browser, static_base_url):
    with opened(browser, static_base_url) as (page, stub):
        page.wait_for_selector(ENG)
        page.select_option(f"{ENG} .unc-task", "t_word")
        page.select_option(f"{BOOK} .unc-task", "t_theory")
        stub.hold = True
        page.click(f"{ENG} .unc-all")
        page.wait_for_function("() => document.querySelector('.unc-all').disabled")
        assert len(stub.held) == 1, "一个接一个发：第一段没回来不发第二段"
        assert all(c.is_disabled() for c in page.locator(".unc-one, .unc-all, .unc-task").all())
        page.locator(f"{BOOK} .unc-all").click(force=True)  # 禁用着：点不动
        page.locator('.unc-row[data-id="evt_2"] .unc-one').click(force=True)
        page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")  # 忙的时候不重拉
        gets = len(stub.gets)
        while True:
            page.wait_for_timeout(100)
            if not stub.held:
                break
            stub.answer(stub.held.pop(0))
        _idle(page)
        assert [e for e, _ in stub.posts] == ["evt_1", "evt_2", "evt_3"] and len(stub.gets) == gets + 2
        assert page.locator(ENG).count() == 0 and _rows(page, BOOK) == ["evt_9"]


def test_post_failure_without_json_and_old_backend(browser, static_base_url):
    # reassign 404（后端早于 v2.11）：按失败显示，行留着
    with opened(browser, static_base_url) as (page, stub):
        page.wait_for_selector(BOOK)
        page.route(re.compile(r"/api/core/sessions/"), lambda r: r.fulfill(status=404, body="nope"))
        page.select_option(f"{BOOK} .unc-task", "t_theory")
        page.click(f"{BOOK} .unc-one")
        page.wait_for_selector("#unc-message.is-error")
        _idle(page)
        assert page.inner_text("#unc-message") == "1 / 1 段没归入：请求失败（404）"
        assert _rows(page, BOOK) == ["evt_9"] and page.is_hidden("#unc-undo")


@pytest.mark.parametrize("width", [320, 390])
def test_narrow_screens_do_not_scroll_sideways(browser, static_base_url, width):
    sessions = _sessions()
    sessions[BUCKET_ENG][2]["source"] = "a-very-long-external-source-name-" * 3
    tree = json.loads(json.dumps(UNC_TREE))
    tree["projects"][0]["name"] = "一个名字特别特别特别特别特别特别特别特别特别特别长的项目"
    with opened(browser, static_base_url, sessions, width=width, tree=tree) as (page, stub):
        page.wait_for_selector(ENG)
        page.select_option(f"{ENG} .unc-task", "__other")
        assert overflowing(page, "#unclassified-panel") == []
        page.select_option(f"{ENG} .unc-task", "t_word")
        stub.fail = {"evt_2"}
        page.click(f"{ENG} .unc-all")
        page.wait_for_selector("#unc-undo:not([hidden]):not([disabled])")
        assert overflowing(page, "#unclassified-panel") == []
        assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
        box = page.locator(".unc-one").first.bounding_box()
        assert box["height"] >= 44  # 触控目标


def test_wide_list_has_unclassified_target_for_projects_without_tasks(browser, static_base_url):
    with opened(browser, static_base_url) as (page, stub):
        page.wait_for_selector(BOOK)
        sel = f"{ENG} .unc-task"
        page.select_option(sel, "__other")
        values = page.eval_on_selector_all(f"{sel} option", "os => os.map(o => o.value)")
        assert "p:p_book" in values and "p:p_none" in values
        assert "p:p_eng" not in values                                 # 自己的未分类就是现在的位置，不是目标
        assert page.locator(f'{sel} optgroup[label="制作区 / 没有桶"] option').all_inner_texts() == [
            "未分类（只记到这个项目）"]
        page.select_option(sel, "p:p_none")
        assert page.locator(f"{ENG} .unc-all").is_enabled()
        page.click(f"{ENG} .unc-all")
        _gone(page, "evt_1")
        _idle(page)
        # 发的是 {projectId}，不是 {taskId: "p:…"}
        assert stub.posts == [("evt_1", {"projectId": "p_none"}), ("evt_2", {"projectId": "p_none"}),
                              ("evt_3", {"projectId": "p_none"})]
        assert page.inner_text("#unc-message") == "已归入 3 段 → 制作区 / 没有桶 · 未分类"


def test_rows_sorted_longest_first_newest_on_ties_stable_across_reload(browser, static_base_url):
    sessions = {BUCKET_ENG: [_seg("a", "2026-10-01T10:00:00+08:00", 600), _seg("b", "2026-10-02T10:00:00+08:00", 600),
                             _seg("c", "2026-10-03T10:00:00+08:00", 60), _seg("d", "2026-10-04T10:00:00+08:00", 1200)]}
    with opened(browser, static_base_url, sessions) as (page, _stub):
        page.wait_for_selector(ENG)
        assert _rows(page, ENG) == ["d", "b", "a", "c"]
        page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
        page.wait_for_timeout(300)
        assert _rows(page, ENG) == ["d", "b", "a", "c"]
