"""集合（suggestions.js，nexus-core v2.10「AI 分集合」+ v2.9 的 confirm {projectId}，仓主 2026-10-08「碎片太多」）。

同本套件其余文件：真浏览器，接口全在浏览器侧桩掉（confirm {projectId} 也是桩：记进项目未分类是 nexus-core v2.9 的事）。
判据落在行为上：怎么分的集合、排在第几、集合的项目预选了谁、行的下拉里有什么、各个按钮发了什么请求。
"""

from __future__ import annotations

import contextlib
import json
from typing import Iterator

import pytest
from conftest import TREE, open_page as _open, overflowing
from playwright.sync_api import Browser, Page
from test_rules import RulesStub
from test_suggestions import SuggestStub

# 多一个带任务的项目：测「集合选了项目，行的下拉只列它的任务」「其他项目…」
TREE2 = json.loads(json.dumps(TREE))
TREE2["projects"].append({"id": "p_mix", "key": "320-1", "zoneId": "z_work", "name": "混音", "tasks": [
    {"id": "t_mix", "key": "320-1-1", "name": "鼓组", "done": False}]})

AGENT = {"key": "claude code · cockpit", "name": "Claude Code · cockpit"}


def seg(i: int, title: str, *, secs: int = 60, app: str = "kitty", task: str | None = None, conf: float = 0.0,
        classifier: str = "rules", coll: dict | None = None, project: str | None = None, idle: bool = False,
        new_task: dict | None = None, rejected: list[str] | None = None) -> dict:
    sug = {"taskId": task, "confidence": conf, "reason": "", "classifier": classifier}
    if coll:
        sug["collection"] = coll
    if project:
        sug["projectId"] = project
    if new_task:
        sug["newTask"] = new_task
    return {"id": f"s{i}", "deviceId": "dev_x", "startAt": f"2026-10-08T10:{i:02d}:00+08:00",
            "endAt": f"2026-10-08T10:{i + 1:02d}:00+08:00", "durationSeconds": secs, "app": app, "title": title,
            "idle": idle, "status": "pending", "suggestion": sug, "rejectedTaskIds": rejected or []}


# 一个助理分好的集合（5 个窗口行、7 段）+ 一个没分过的小窗口
NT = {"proposalId": "tp_1", "projectId": "p_eng", "name": "乐理复习"}
MIXED = [
    seg(1, "✳ 重构存档", coll=AGENT, project="p_eng"),                                 # 没任务，只标了项目
    seg(2, "✳ 重构存档", coll=AGENT, project="p_eng"),
    seg(3, "plot.gd", coll=AGENT, task="t_word", conf=0.9),                            # 规则给的任务
    seg(4, "视奏", coll=AGENT, task="t_read", conf=0.7, classifier="assistant"),        # 助理配的任务（是 / 否）
    seg(5, "✳ 重构存档", coll=AGENT, project="p_eng", idle=True),                       # 无操作
    seg(6, "乐理", coll=AGENT, conf=0.6, classifier="assistant", new_task=NT),          # 提议新任务
    seg(7, "plot.gd", coll=AGENT, task="t_word", conf=0.9),
    seg(8, "docs", app="chrome", secs=30),
]
COLL = '.suggest-coll[data-key="ai:claude code · cockpit"]'


@contextlib.contextmanager
def open_page(browser: Browser, base: str, items, *, tree: dict = TREE, rules: bool = False, expand: bool = True,
              width: int = 1100, theme: str | None = None) -> Iterator[tuple[Page, SuggestStub]]:
    stub = SuggestStub(items)
    routes = {r"/api/core/activity/suggestions([/?]|$)": stub.route,
              r"/api/core/views/tree$": lambda r: r.fulfill(status=200, content_type="application/json",
                                                             body=json.dumps(tree, ensure_ascii=False))}
    if rules:
        routes[r"/api/core/detector/rules"] = RulesStub().route
    with _open(browser, base, routes=routes, width=width, theme=theme, expand=expand) as page:
        page.wait_for_selector(".suggest-coll")
        if rules:
            page.wait_for_selector("#rules-editor:not([hidden])", state="attached")
        yield page, stub


def _names(page: Page) -> list[str]:
    return page.eval_on_selector_all(".suggest-coll-name", "els => els.map(e => e.textContent)")


def test_pure_helpers(browser, static_base_url):
    with open_page(browser, static_base_url, MIXED) as (page, _stub):
        assert page.evaluate("""() => ["✳ Claude Code", "⠂  Claude   Code", "(3) Claude Code", "* (12) Claude Code",
            "[2] Claude Code", "Claude Code"].map(window.assistantSuggestions.normTitle)""") == ["Claude Code"] * 6
        # 只去开头的；括号里不是数字的、尖括号、点开头的文件名都不动；只有符号的标题原样留着
        assert page.evaluate("""() => ["[IP] vim (3)", "<b>x</b>", ".bashrc", "✳", ""]
            .map(window.assistantSuggestions.normTitle)""") == ["[IP] vim (3)", "<b>x</b>", ".bashrc", "✳", ""]
        out = page.evaluate("""() => {
            const it = (id, app, title, secs, coll) => ({id, app, title, durationSeconds: secs,
              startAt: "2026-10-08T10:00:00+08:00", endAt: "2026-10-08T10:01:00+08:00",
              suggestion: coll ? {collection: coll} : {}});
            const A = {key: "a", name: "集合 A"};
            return window.assistantSuggestions.collect([
              it("1", "kitty", "✳ build", 10), it("2", "kitty", "(2) build", 10), it("3", "chrome", "build", 10),
              it("4", "kitty", "x", 5, A), it("5", "code", "y", 100, A), it("6", "kitty", "tie", 20),
            ]).map(c => [c.key, c.name, c.seconds, c.items.map(i => i.id), c.groups.length]);
        }""")
        assert out == [
            ["ai:a", "集合 A", 105, ["4", "5"], 2],                               # 助理分的：按 key，不看程序 / 标题
            ['win:["kitty","build"]', "kitty · build", 20, ["1", "2"], 2],        # 没分过的：开头的符号 / 计数不算
            ['win:["kitty","tie"]', "kitty · tie", 20, ["6"], 1],                 # 一样长：先出现的在前
            ['win:["chrome","build"]', "chrome · build", 10, ["3"], 1],           # 程序不同不并
        ]
        proj = """(sugs) => window.assistantSuggestions.suggestedProject(
            {items: sugs.map(s => ({suggestion: s}))}, %s)""" % json.dumps(TREE2, ensure_ascii=False)
        assert page.evaluate(proj, [{"taskId": "t_word"}, {"projectId": "p_eng"}, {},
                                    {"newTask": {"projectId": "p_eng"}}]) == "p_eng"
        assert page.evaluate(proj, [{"taskId": "t_word"}, {"projectId": "p_mix"}]) == ""       # 不一致：让人挑
        assert page.evaluate(proj, [{"projectId": "p_gone"}, {"taskId": "t_gone"}, {}]) == ""  # 不在树里的不算


def test_collections_sorted_by_total_with_header(browser, static_base_url):
    items = [seg(1, "a", secs=60), seg(2, "big", secs=600, coll={"key": "k", "name": "<b>大集合</b>"}),
             seg(3, "(2) a", secs=120), seg(4, "big2", secs=1200, coll={"key": "k", "name": "<b>大集合</b>"})]
    with open_page(browser, static_base_url, items) as (page, _stub):
        assert _names(page) == ["<b>大集合</b>", "kitty · a"]          # 30 分 > 3 分；名字只当文本
        assert page.locator(".suggest-coll b").count() == 0
        metas = page.eval_on_selector_all(".suggest-coll-meta", "els => els.map(e => e.textContent)")
        assert metas == ["共 30 分 · 2 个窗口 · 2 段 · 10-08 10:02–10:05", "共 3 分 · 2 个窗口 · 2 段 · 10-08 10:01–10:04"]
        assert page.inner_text("#suggest-count") == "4"               # 计数仍按段


def test_rows_collapsed_by_default_and_state_survives_redraw(browser, static_base_url):
    with open_page(browser, static_base_url, MIXED, expand=False) as (page, _stub):
        big, small = page.locator(COLL), page.locator(".suggest-coll").nth(1)
        assert big.locator(".suggest-coll-rows > summary").inner_text() == "看这 5 个窗口"
        assert not big.locator("li").first.is_visible()                 # 多个窗口：收着
        assert small.locator("li").first.is_visible()                   # 只有一个窗口：没什么可收的
        assert big.locator(".suggest-project").is_visible() and big.locator(".suggest-confirm-coll").is_visible()
        big.locator(".suggest-coll-rows > summary").click()
        assert big.locator("li").first.is_visible()
        page.locator(".suggest-coll").nth(1).locator(".suggest-dismiss").click()   # 一次操作 → 重拉重绘
        page.wait_for_function("() => document.querySelectorAll('.suggest-coll').length === 1")
        assert page.locator(COLL + " li").first.is_visible()            # 人展开过的还开着


def test_project_preselected_only_when_suggestions_agree(browser, static_base_url):
    items = [seg(1, "a", coll=AGENT, project="p_eng"), seg(2, "b", coll=AGENT, task="t_word", conf=0.9),
             seg(3, "c", coll={"key": "x", "name": "X"}, project="p_eng"),
             seg(4, "d", coll={"key": "x", "name": "X"}, task="t_mix", conf=0.9),
             seg(5, "e", coll={"key": "y", "name": "Y"})]
    with open_page(browser, static_base_url, items, tree=TREE2) as (page, _stub):
        got = {n: page.locator(f'.suggest-coll[data-key="ai:{k}"] .suggest-project').input_value()
               for n, k in (("agree", AGENT["key"]), ("clash", "x"), ("none", "y"))}
        assert got == {"agree": "p_eng", "clash": "", "none": ""}
        opts = page.locator(COLL + " .suggest-project option").all_inner_texts()
        assert opts == ["选择项目…", "练琴区 / 吉他练习", "练琴区 / 考级计划", "制作区 / 混音"]   # 没任务的项目也能选
        # 没项目的集合：行同以前（全部任务的下拉、没选不能确认），整个集合也确认不了
        none = page.locator('.suggest-coll[data-key="ai:y"]')
        assert none.locator("li select option").first.inner_text() == "选择任务…"
        assert none.locator(".suggest-confirm").is_disabled() and none.locator(".suggest-confirm-coll").is_disabled()
        assert none.locator(".suggest-coll-plan").inner_text() == "先给集合选项目，或展开后逐个窗口定"


def test_project_limits_task_pickers_with_other_project_escape(browser, static_base_url):
    items = [seg(1, "a", coll=AGENT), seg(2, "b", coll=AGENT, task="t_mix", conf=0.9)]
    with open_page(browser, static_base_url, items, tree=TREE2) as (page, stub):
        coll = page.locator(COLL)
        coll.locator(".suggest-project").select_option("p_eng")
        a, b = page.locator('li[data-id="s1"]'), page.locator('li[data-id="s2"]')
        assert a.locator("option").all_inner_texts() == [
            "未分类（只记到这个项目）", "音阶练习", "曲目视奏", "旧任务（无前置字段）", "其他项目…"]
        assert a.locator("select").input_value() == "" and a.locator(".suggest-confirm").is_enabled()
        assert "不选任务就记到这个项目的「未分类」" in a.inner_text()
        # 规则建议的任务在别的项目：这行保留建议（全部任务的下拉），能退回只看集合的项目
        assert b.locator("select").input_value() == "t_mix"
        assert b.locator("option").last.inner_text() == "← 只看集合的项目"
        assert coll.locator(".suggest-coll-plan").inner_text() == "1 段记到选好的任务，1 段记到「练琴区 / 吉他练习」的未分类"
        # 「其他项目…」→ 全部任务，没选不能确认；选别的项目的任务照常发 taskId
        a.locator("select").select_option("__other")
        a = page.locator('li[data-id="s1"]')
        assert a.locator("select").input_value() == "" and a.locator(".suggest-confirm").is_disabled()
        assert page.evaluate("document.activeElement === document.querySelector('li[data-id=\"s1\"] select')")
        a.locator("select").select_option("t_mix")
        a.locator(".suggest-confirm").click()
        page.wait_for_function("() => !document.querySelector('li[data-id=\"s1\"]')")
        assert stub.posts == [("confirm", "s1", {"taskId": "t_mix"})]
        # 「← 只看集合的项目」→ 回到未分类
        b = page.locator('li[data-id="s2"]')
        b.locator("select").select_option("__back")
        b = page.locator('li[data-id="s2"]')
        assert b.locator("option").first.inner_text() == "未分类（只记到这个项目）" and b.locator("select").input_value() == ""
        # 换集合的项目：行的选择重来，下拉跟着换
        page.locator(COLL + " .suggest-project").select_option("p_mix")
        assert page.locator('li[data-id="s2"] select').input_value() == "t_mix"
        assert page.locator('li[data-id="s2"] option').all_inner_texts() == ["未分类（只记到这个项目）", "鼓组", "其他项目…"]


def test_cleared_away_row_is_skipped_by_collection_confirm_until_picked(browser, static_base_url):
    items = [seg(1, "a", coll=AGENT, project="p_eng"), seg(2, "b", coll=AGENT, task="t_mix", conf=0.9),
             seg(3, "c", coll=AGENT, project="p_eng")]
    with open_page(browser, static_base_url, items, tree=TREE2) as (page, stub):
        page.locator(COLL + " .suggest-project").select_option("p_eng")
        b = page.locator('li[data-id="s2"]')
        assert b.locator("select").input_value() == "t_mix"  # 规则的任务在项目 A（这里是 p_mix），集合选的是 p_eng
        b.locator("select").select_option("")  # 清空：行显示「选择任务…」，自己的确认禁用
        assert b.locator("option").first.inner_text() == "选择任务…" and b.locator(".suggest-confirm").is_disabled()
        assert page.locator(COLL + " .suggest-confirm-coll").inner_text() == "确认整个集合 · 2 段"
        page.locator(COLL + " .suggest-confirm-coll").click()
        page.wait_for_function("() => document.querySelectorAll('#suggest-list li').length === 1")
        assert stub.posts == [("confirm", "s1", {"projectId": "p_eng"}), ("confirm", "s3", {"projectId": "p_eng"})]
        # 退回「← 只看集合的项目」= 明说记到未分类：这时才发 {projectId}
        page.locator('li[data-id="s2"] select').select_option("__back")
        page.locator(COLL + " .suggest-confirm-coll").click()
        page.wait_for_function("() => !document.querySelector('li[data-id=\"s2\"]')")
        assert stub.posts[-1] == ("confirm", "s2", {"projectId": "p_eng"})


def test_confirm_collection_sends_task_or_project_per_row(browser, static_base_url):
    with open_page(browser, static_base_url, MIXED) as (page, stub):
        coll = page.locator(COLL)
        assert coll.locator(".suggest-project").input_value() == "p_eng"
        assert coll.locator(".suggest-confirm-coll").inner_text() == "确认整个集合 · 5 段"
        assert coll.locator(".suggest-coll-plan").inner_text() == (
            "3 段记到选好的任务，2 段记到「练琴区 / 吉他练习」的未分类，2 段要展开后单独定")
        coll.locator(".suggest-confirm-coll").click()
        page.wait_for_function("() => document.querySelectorAll('#suggest-list li').length === 3")
        assert stub.posts == [
            ("confirm", "s1", {"projectId": "p_eng"}), ("confirm", "s2", {"projectId": "p_eng"}),   # 没任务 → 未分类
            ("confirm", "s3", {"taskId": "t_word"}), ("confirm", "s7", {"taskId": "t_word"}),       # 规则的任务
            ("confirm", "s4", {"taskId": "t_read"}),                                                # 助理配的任务
        ]
        assert page.inner_text("#suggest-message") == "已确认 5 条。"
        # 无操作的、要新建任务的：留着，等人逐行点；这时整个集合没东西可确认了
        left = page.eval_on_selector_all("#suggest-list li", "els => els.map(e => e.dataset.id)")
        assert left == ["s5", "s6", "s8"]
        assert page.locator(COLL + " .suggest-confirm-coll").is_disabled()
        assert page.locator('li[data-id="s6"] .suggest-newname').input_value() == "乐理复习"
        # 无操作的行自己能确认到未分类
        page.locator('li[data-id="s5"] .suggest-confirm').click()
        page.wait_for_function("() => !document.querySelector('li[data-id=\"s5\"]')")
        assert stub.posts[-1] == ("confirm", "s5", {"projectId": "p_eng"})


def test_confirm_all_by_threshold_never_sends_project_only(browser, static_base_url):
    with open_page(browser, static_base_url, MIXED) as (page, stub):
        page.fill("#suggest-threshold", "0")
        assert page.inner_text("#suggest-confirm-all").endswith("· 3")
        page.click("#suggest-confirm-all")
        page.wait_for_function("() => !document.querySelector('li[data-id=\"s3\"]')")
        assert stub.posts == [("confirm", "s3", {"taskId": "t_word"}), ("confirm", "s7", {"taskId": "t_word"}),
                              ("confirm", "s4", {"taskId": "t_read"})]
        # 人把规则建议的任务改成「未分类」的行也不进「全部确认」
    with open_page(browser, static_base_url, MIXED) as (page, stub):
        page.fill("#suggest-threshold", "0")
        page.locator('li[data-id="s3"] select').select_option("")
        assert page.inner_text("#suggest-confirm-all").endswith("· 1")
        assert page.locator(COLL + " .suggest-confirm-coll").inner_text() == "确认整个集合 · 5 段"   # 集合里它记到未分类


def test_row_confirm_unclassified_and_no_rule_without_task(browser, static_base_url):
    with open_page(browser, static_base_url, MIXED, rules=True) as (page, stub):
        li = page.locator('li[data-id="s1"]')
        li.locator(".suggest-rule-cb").check()
        li.locator(".suggest-confirm").click()
        page.wait_for_function("() => !document.querySelector('li[data-id=\"s1\"]')")
        assert stub.posts == [("confirm", "s1", {"projectId": "p_eng"}), ("confirm", "s2", {"projectId": "p_eng"})]
        assert page.inner_text("#suggest-message") == "记到了未分类，没有具体任务，这个窗口的规则没加。"
        assert not page.locator("#suggest-message").evaluate("e => e.classList.contains('is-error')")


def test_partial_failure_reported_per_row_and_rejected_task_never_sent(browser, static_base_url):
    items = [seg(1, "a", coll=AGENT, project="p_eng"), seg(2, "a", coll=AGENT, project="p_eng"),
             seg(3, "b", coll=AGENT, task="t_word", conf=0.9),
             seg(4, "c", coll=AGENT, project="p_eng", rejected=["t_read"]), seg(5, "c", coll=AGENT, project="p_eng")]
    with open_page(browser, static_base_url, items) as (page, stub):
        stub.fail_ids.add("s2")
        page.locator('li[data-id="s4"] select').select_option("t_read")   # 这个窗口有一段否掉过 t_read
        page.locator(COLL + " .suggest-confirm-coll").click()
        page.wait_for_selector("#suggest-message.is-error")
        assert [(i, b) for _, i, b in stub.posts] == [
            ("s1", {"projectId": "p_eng"}), ("s2", {"projectId": "p_eng"}), ("s3", {"taskId": "t_word"}),
            ("s5", {"taskId": "t_read"})]                                 # s4 否掉过 t_read：不发
        msg = page.inner_text("#suggest-message")
        assert msg.startswith("kitty · a：1 / 2 段失败，任务不存在：'t_gone'") and "另有 1 处失败" in msg
        left = page.eval_on_selector_all("#suggest-list li", "els => els.map(e => e.dataset.id)")
        assert left == ["s2", "s4"]                                       # 成功的不回滚，失败的留下


def test_busy_guard_and_project_choice_dropped_with_collection(browser, static_base_url):
    items = [seg(1, "a", coll=AGENT), seg(2, "b", coll=AGENT)]
    with open_page(browser, static_base_url, items) as (page, stub):
        page.locator(COLL + " .suggest-project").select_option("p_book")
        assert page.evaluate("document.activeElement.classList.contains('suggest-project')")   # 重绘后焦点还在
        assert page.locator('li[data-id="s1"] option').all_inner_texts() == ["未分类（只记到这个项目）", "其他项目…"]
        stub.hold_get = True                                              # 确认后的重拉先压着
        page.locator(COLL + " .suggest-confirm-coll").click()
        for _ in range(100):                                              # 两个 confirm 发完、重拉的 GET 被压住
            if stub.held:
                break
            page.wait_for_timeout(50)
        assert stub.posts == [("confirm", "s1", {"projectId": "p_book"}), ("confirm", "s2", {"projectId": "p_book"})]
        # 重拉还在路上：旧列表上的控件全禁用（再点就是重复提交）
        assert page.locator(COLL + " .suggest-confirm-coll").is_disabled()
        assert page.locator(COLL + " .suggest-project").is_disabled()
        assert page.locator('li[data-id="s1"] .suggest-confirm').is_disabled()
        stub.hold_get = False
        for r in stub.held:
            stub.route(r)
        page.wait_for_selector("#suggest-empty:not([hidden])")
        assert len(stub.posts) == 2
        # 集合没了，给它选的项目也丢：同名集合再出现时不沿用
        stub.items = json.loads(json.dumps(items))
        page.evaluate("document.dispatchEvent(new Event('assistant:turn-done'))")
        page.wait_for_selector(COLL)
        assert page.locator(COLL + " .suggest-project").input_value() == ""


def test_reload_regroups_by_ai_collections(browser, static_base_url):
    with open_page(browser, static_base_url, [seg(1, "a"), seg(2, "b")]) as (page, stub):
        assert len(_names(page)) == 2                                     # 助理还没分：两个窗口各是各的
        stub.items[0]["suggestion"]["collection"] = AGENT
        stub.items[1]["suggestion"].update(collection=AGENT, taskId="t_read", confidence=0.7, classifier="assistant")
        page.evaluate("document.dispatchEvent(new Event('assistant:turn-done'))")
        page.wait_for_selector(COLL)
        assert _names(page) == ["Claude Code · cockpit"]
        assert page.locator(COLL + " .suggest-project").input_value() == "p_eng"
        assert [b.inner_text() for b in page.locator('li[data-id="s2"] button').all()] == ["是 ✓", "否 ✗"]


@pytest.mark.parametrize("width", [320, 390])
@pytest.mark.parametrize("theme", [None, "dark"])
def test_narrow_no_horizontal_scroll(browser, static_base_url, width, theme):
    long = {"key": "long", "name": "很长的集合名ClaudeCodeSessionWithoutAnySpaces" * 2}
    items = [seg(i, "路径很长的窗口标题/without/spaces/" + "x" * 80 + str(i % 2), coll=long, project="p_eng") for i in range(1, 5)]
    with open_page(browser, static_base_url, items + MIXED[:1], width=width, theme=theme) as (page, _stub):
        assert overflowing(page, "#suggest-panel") == []
        coll = page.locator(".suggest-coll").first
        for sel in (".suggest-project", ".suggest-confirm-coll", ".suggest-coll-rows > summary"):
            assert coll.locator(sel).first.bounding_box()["height"] >= 44   # 手指点得中
        page.locator(".suggest-coll").first.locator("li select").first.select_option("__other")
        assert overflowing(page, "#suggest-panel") == []
