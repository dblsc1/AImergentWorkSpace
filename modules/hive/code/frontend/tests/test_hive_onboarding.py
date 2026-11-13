"""新手引导（hex-onboard.js，onboarding.v1「七」）：显示条件、硬限制、AI 放窗口、结果页撤销、跳过 / 走完都调 onboarding/done。

接口全在浏览器侧 ``page.route()`` 桩掉；candidates / claim / claim/undo 按契约「五」的形状桩（nexus-core 侧尚未合进 v0.6）。
"""

from __future__ import annotations

import contextlib
import json
from typing import Any, Iterator

from test_hive_focus import TREE, base, browser  # noqa: F401  (fixtures)

EMPTY = {"zones": [], "projects": []}
GOOD = {"blockKey": "ptyxis · garden", "displayName": "garden", "match": "agent", "days": 3, "seconds": 1800}
NAMED = {"blockKey": "docs · 刷题", "displayName": "刷题", "match": "name", "days": 1, "seconds": 600}
SHORT = {"blockKey": "tiny · 刷题", "displayName": "tiny", "match": "name", "days": 1, "seconds": 599}
AI = {"blockKey": "zoom · 周会", "displayName": "周会", "match": "ai", "days": 1, "seconds": 5000}


class Stub:
    def __init__(self, tree: dict, done_at: Any, items: list[dict]) -> None:
        self.tree, self.done_at, self.items = tree, done_at, items
        self.reqs: list[tuple[str, str, Any]] = []
        self.n = 0
        self.hold_undo = False
        self.held: list[Any] = []
        self.date_header: str | None = None
        self.direct: list[dict] = []
        self.undo_status = 200
        self.fail_ids: dict[str, int] = {}

    def posts(self, prefix: str) -> list[Any]:
        return [b for m, p, b in self.reqs if m == "POST" and p.startswith(prefix)]

    def route(self, r: Any) -> None:
        req = r.request
        path = req.url.split("/api/core/", 1)[1]
        body = json.loads(req.post_data) if req.post_data else None
        self.reqs.append((req.method, path, body))

        def ok(b: Any, status: int = 200) -> None:
            r.fulfill(status=status, content_type="application/json", body=json.dumps(b, ensure_ascii=False),
                      headers={"Date": self.date_header} if self.date_header else None)

        if path.startswith("views/tree"):
            ok(self.tree)
        elif path == "activity/settings":
            ok({"version": 1, "onboardingDoneAt": self.done_at})
        elif path == "activity/onboarding/done":
            self.done_at = "2026-11-08T00:00:00+08:00"
            ok({"version": 2, "onboardingDoneAt": self.done_at})
        elif path.startswith("activity/unclassified/candidates"):
            # 任务目标给 GOOD + NAMED；项目目标给 GOOD（重复）+ SHORT + AI
            task = "taskId=" in path
            ok({"items": [GOOD, NAMED] if task else [GOOD, SHORT, AI], "totalSeconds": 0})
        elif path == "activity/blocks/claim":
            ok({"claimed": [{"blockKey": k, "days": 1, "seconds": 1800 if k == GOOD["blockKey"] else 600} for k in body["blockKeys"]],
                "totalSeconds": 2400, "skipped": []})
        elif path == "activity/blocks/claim/undo":
            ok({"undone": [{"blockKey": k, "days": 1} for k in body["blockKeys"]]})
        elif path.startswith("activity/direct-log/") and path.endswith("/undo"):
            if self.hold_undo:
                self.held.append(r)
                return
            status = self.fail_ids.get(path.split("/")[2], self.undo_status)
            ok({"undone": True} if status == 200 else {"detail": "已被后来的改动覆盖"}, status)
            if status == 200:
                self.direct = [d for d in self.direct if d["id"] != path.split("/")[2]]
        elif path.startswith("activity/direct-log"):
            ok({"items": self.direct})
        elif path.startswith("activity/blocks?"):
            ok({"items": [{"blockKey": "zoom · 周会", "displayName": "周会", "seconds": 3600}]})
        elif req.method == "POST" and path.startswith("planner/"):
            self.n += 1
            ok({"id": f"{path.split('/')[1][0]}_{self.n}", "name": body.get("name")}, 201)
        elif path.startswith("views/current"):
            ok({"running": False, "zone": None, "project": None, "task": None, "sessionStartAt": None, "agents": [],
                "auto": None, "needsChoice": None, "aiThinking": None, "focus": None})
        elif path.startswith("views/next-actions"):
            ok({"today": "2026-10-09", "zones": []})
        else:
            ok({"total": 0, "items": []})


@contextlib.contextmanager
def open_onboarding(browser: Any, base: str, tree: dict = EMPTY, done_at: Any = None, items: list[dict] | None = None,
                    date_header: str | None = None) -> Iterator[Stub]:
    stub = Stub(tree, done_at, items or [])
    stub.date_header = date_header
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    page = context.new_page()
    stub.page = page
    page.route("**/api/core/**", stub.route)
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.add_init_script("document.hasFocus = () => !!window.__pageInFront")   # 无头浏览器里恒为真；默认当页面不在最前面（「三十一」）
    page.goto(base + "/index.html")
    try:
        yield stub
        assert errors == [], errors
    finally:
        context.close()


def _add(page: Any, placeholder: str, name: str, nth: int = 0) -> None:
    n = page.locator(f'.hob-input[placeholder="{placeholder}"]').nth(nth)
    n.fill(name)
    n.locator("xpath=following-sibling::button").click()


def _wait_count(page: Any, sel: str, n: int) -> None:
    page.wait_for_function("([s, n]) => document.querySelectorAll(s).length === n", arg=[sel, n])


def test_shown_only_for_new_account(browser, base) -> None:  # noqa: F811
    with open_onboarding(browser, base) as s:
        s.page.wait_for_selector(".hob")
    with open_onboarding(browser, base, tree=TREE) as s:   # 老账号：有分区
        s.page.wait_for_selector(".hex-cell[data-project-id=p_math]")
        s.page.wait_for_timeout(200)
        assert s.page.locator(".hob").count() == 0
    with open_onboarding(browser, base, done_at="2026-11-01T00:00:00+08:00") as s:   # 走过了
        s.page.wait_for_function("() => document.querySelector('#hive')")
        s.page.wait_for_timeout(300)
        assert s.page.locator(".hob").count() == 0


def test_card_is_non_blocking_and_closing_does_not_finish(browser, base) -> None:  # noqa: F811
    with open_onboarding(browser, base) as s:
        p = s.page
        p.wait_for_selector(".hob")
        box = p.locator(".hob").bounding_box()
        assert box["x"] > 640 and box["width"] < 420, "角落小卡，不是全屏遮罩"
        assert p.evaluate("() => { const e = document.elementFromPoint(640, 450); return !e.closest('.hob'); }"), "页面中央点得到页面"
        assert p.evaluate("() => getComputedStyle(document.querySelector('.hob')).position") == "fixed"
        _add(p, "分区名", "学习")
        _wait_count(p, ".hob-line", 1)
        p.locator(".hob-close").click()
        _wait_count(p, ".hob", 0)
        assert p.locator(".hob-reopen").is_visible()
        assert s.posts("activity/onboarding/done") == [], "收起不算完成"
        p.locator(".hob-reopen").click()
        _wait_count(p, ".hob", 1)
        assert p.locator(".hob-line").all_inner_texts() == ["学习"], "再打开还在原来的进度上"
        assert not p.locator(".hob-reopen").is_visible()
        p.get_by_role("button", name="跳过").click()
        _wait_count(p, ".hob", 0)
        assert p.locator(".hob-reopen").count() == 0
        assert len(s.posts("activity/onboarding/done")) == 1


def test_hard_limits(browser, base) -> None:  # noqa: F811
    with open_onboarding(browser, base) as s:
        p = s.page
        p.wait_for_selector(".hob")
        assert p.locator(".hob-foot .hob-btn.is-primary").is_disabled(), "没分区不能下一步"
        _add(p, "分区名", "学习")
        _wait_count(p, ".hob-line", 1)
        _add(p, "分区名", "制作")
        _wait_count(p, ".hob-line", 2)
        assert p.locator('.hob-input[placeholder="分区名"]').is_disabled(), "第 3 个分区被拒"
        assert p.locator('.hob-input[placeholder="分区名"] + .hob-btn').is_disabled()
        assert [b for b in s.posts("planner/zones")] == [{"name": "学习"}, {"name": "制作"}]
        p.locator(".hob-foot .is-primary").click()
        p.wait_for_selector('.hob-input[placeholder="项目名"]')
        assert p.locator(".hob-foot .is-primary").is_disabled(), "分区没有项目"
        _add(p, "项目名", "数学", 0)
        _wait_count(p, ".hob-proj", 1)
        _add(p, "项目名", "钢琴", 0)
        _wait_count(p, ".hob-proj", 2)
        assert p.locator('.hob-zone').nth(0).locator('.hob-input[placeholder="项目名"]').is_disabled(), "每分区至多 2 个项目"
        _add(p, "项目名", "录音", 1)
        _wait_count(p, ".hob-proj", 3)
        assert p.locator(".hob-foot .is-primary").is_disabled(), "项目没有任务"
        for i in range(3):
            _add(p, "任务名", f"任务{i}", i)
            _wait_count(p, ".hob-task", i + 1)
        assert not p.locator(".hob-foot .is-primary").is_disabled()


def test_full_flow_claims_undoes_and_marks_done(browser, base) -> None:  # noqa: F811
    with open_onboarding(browser, base) as s:
        p = s.page
        p.wait_for_selector(".hob")
        _add(p, "分区名", "学习")
        _wait_count(p, ".hob-line", 1)
        p.locator(".hob-foot .is-primary").click()
        p.wait_for_selector('.hob-input[placeholder="项目名"]')
        _add(p, "项目名", "数学")
        _wait_count(p, ".hob-proj", 1)
        _add(p, "任务名", "刷题")
        _wait_count(p, ".hob-task", 1)
        p.locator(".hob-foot .is-primary").click()
        p.wait_for_selector(".hob-result")
        cands = [path for m, path, _ in s.reqs if path.startswith("activity/unclassified/candidates")]
        assert sorted(c.split("?")[1].split("&")[0] for c in cands) == ["projectId=p_2", "taskId=t_3"], "任务与项目各调一次"
        claims = s.posts("activity/blocks/claim")
        assert claims == [{"taskId": "t_3", "blockKeys": [GOOD["blockKey"], NAMED["blockKey"]], "remember": True}], "只 claim 一次；599 秒与 ai 的不选"
        rows = p.locator(".hob-result")
        assert rows.count() == 2
        assert p.locator(".hob-name").all_inner_texts() == ["garden", "刷题"], "显示 displayName，不是原始块名"
        assert p.locator(".hob-total").inner_text() == "0.7 小时"
        rows.nth(0).locator("button").click()
        _wait_count(p, ".hob-result", 1)
        assert s.posts("activity/blocks/claim/undo") == [{"blockKeys": [GOOD["blockKey"]]}]
        assert p.locator(".hob-total").inner_text() == "0.2 小时"
        p.locator(".hob-foot .is-primary").click()
        _wait_count(p, ".hob", 0)
        assert len(s.posts("activity/onboarding/done")) == 1
        assert not [p_ for m, p_, _ in s.reqs if m == "POST" and p_.startswith("activity/blocks/") and "claim" not in p_], "其余块不动"


def test_skip_marks_done(browser, base) -> None:  # noqa: F811
    with open_onboarding(browser, base) as s:
        s.page.wait_for_selector(".hob")
        s.page.get_by_role("button", name="跳过").click()
        _wait_count(s.page, ".hob", 0)
        assert len(s.posts("activity/onboarding/done")) == 1
        s.page.reload()
        s.page.wait_for_function("() => document.querySelector('#hive')")
        s.page.wait_for_timeout(300)
        assert s.page.locator(".hob").count() == 0, "刷新后不再出"


def _to_result(p: Any) -> None:
    _add(p, "分区名", "学习")
    _wait_count(p, ".hob-line", 1)
    p.locator(".hob-foot .is-primary").click()
    p.wait_for_selector('.hob-input[placeholder="项目名"]')
    _add(p, "项目名", "数学")
    _wait_count(p, ".hob-proj", 1)
    _add(p, "任务名", "刷题")
    _wait_count(p, ".hob-task", 1)
    p.locator(".hob-foot .is-primary").click()
    p.wait_for_selector(".hob-title:text-is('结果')")


def _dl(id_: str, at: str, **kw: Any) -> dict:
    return {"id": id_, "at": at, "actor": "ai", "kind": "blockAction", "undoneAt": None,
            "payload": {"action": "assign", "date": "2026-11-08", "blockKey": "zoom · 周会"}, **kw}


def test_result_lists_ai_direct_and_undoes(browser, base) -> None:  # noqa: F811
    with open_onboarding(browser, base) as s:
        s.direct = [_dl("d1", "2099-01-01T00:00:00Z"), _dl("d2", "2000-01-01T00:00:00Z"),
                    _dl("d3", "2099-01-01T00:00:00Z", undoneAt="2099-01-02T00:00:00Z")]
        p = s.page
        p.wait_for_selector(".hob")
        _to_result(p)
        _wait_count(p, ".hob-result", 3)   # 2 个 claim + 1 个直接施加；引导前的、已撤回的不列
        assert p.locator(".hob-name").all_inner_texts() == ["garden", "刷题", "周会"]
        assert p.locator(".hob-total").inner_text() == "1.7 小时", "0.7 + 1"
        p.locator(".hob-result").nth(2).locator("button").click()
        _wait_count(p, ".hob-result", 2)
        assert [pa for m, pa, _ in s.reqs if m == "POST" and "direct-log" in pa] == ["activity/direct-log/d1/undo"]
        assert p.locator(".hob-total").inner_text() == "0.7 小时"


def test_direct_undo_conflict_keeps_row(browser, base) -> None:  # noqa: F811
    with open_onboarding(browser, base) as s:
        s.direct = [_dl("d1", "2099-01-01T00:00:00Z")]
        s.undo_status = 409
        p = s.page
        p.wait_for_selector(".hob")
        _to_result(p)
        _wait_count(p, ".hob-result", 3)
        p.locator(".hob-result").nth(2).locator("button").click()
        p.wait_for_selector(".hob-err")
        assert p.locator(".hob-result").count() == 3


def test_result_rereads_direct_log_every_10s(browser, base) -> None:  # noqa: F811
    with open_onboarding(browser, base) as s:
        p = s.page

        def reads() -> int:
            return len([1 for _, pa, _b in s.reqs if pa.startswith("activity/direct-log")])

        p.wait_for_selector(".hob")
        _to_result(p)
        _wait_count(p, ".hob-result", 2)
        n = reads()
        s.direct = [_dl("d1", "2099-01-01T00:00:00Z")]
        _wait_count(p, ".hob-result", 3)   # 10 秒内重读到
        assert reads() > n
        p.get_by_role("button", name="完成").click()
        _wait_count(p, ".hob", 0)
        k = reads()
        p.wait_for_timeout(11000)
        assert reads() == k, "离开结果页后停止"


def test_same_block_merges_to_one_row_and_undoes_newest_first(browser, base) -> None:  # noqa: F811
    with open_onboarding(browser, base) as s:
        s.direct = [_dl("old", "2099-01-01T00:00:00Z"), _dl("new", "2099-01-02T00:00:00Z"), _dl("mid", "2099-01-01T12:00:00Z")]
        p = s.page
        p.wait_for_selector(".hob")
        _to_result(p)
        _wait_count(p, ".hob-result", 3)   # 2 claim + 合并的 1 行
        assert p.locator(".hob-total").inner_text() == "1.7 小时", "秒数只算一次"
        p.locator(".hob-result").nth(2).locator("button").click()
        _wait_count(p, ".hob-result", 2)
        assert [pa for m, pa, _ in s.reqs if m == "POST" and "direct-log" in pa] == [f"activity/direct-log/{i}/undo" for i in ("new", "mid", "old")]


def test_group_undo_stops_at_409_and_keeps_row(browser, base) -> None:  # noqa: F811
    with open_onboarding(browser, base) as s:
        s.direct = [_dl("old", "2099-01-01T00:00:00Z"), _dl("new", "2099-01-02T00:00:00Z")]
        s.fail_ids = {"old": 409}
        p = s.page
        p.wait_for_selector(".hob")
        _to_result(p)
        _wait_count(p, ".hob-result", 3)
        p.locator(".hob-result").nth(2).locator("button").click()
        p.wait_for_selector(".hob-err")
        assert p.locator(".hob-result").count() == 3
        p.locator(".hob-result").nth(2).locator("button").click()   # 再点：new 已撤，不重复
        p.wait_for_function("() => document.querySelectorAll('.hob-err').length === 1")
        undos = [pa for m, pa, _ in s.reqs if m == "POST" and "direct-log" in pa]
        assert undos == ["activity/direct-log/new/undo", "activity/direct-log/old/undo", "activity/direct-log/old/undo"]


def test_started_time_uses_server_date_header(browser, base) -> None:  # noqa: F811
    with open_onboarding(browser, base, tree=EMPTY, date_header="Thu, 01 Jan 2099 00:00:00 GMT") as s:
        s.direct = [_dl("d1", "2098-12-31T23:59:00Z"), _dl("d2", "2099-01-01T00:00:05Z")]
        p = s.page
        p.wait_for_selector(".hob")
        _to_result(p)
        _wait_count(p, ".hob-result", 3)   # 浏览器时钟是 2026，但按服务端时间只有 d2 算引导后；同块合一行
        undo = p.locator(".hob-result").nth(2).locator("button")
        undo.click()
        _wait_count(p, ".hob-result", 2)
        assert [pa for m, pa, _ in s.reqs if m == "POST" and "direct-log" in pa] == ["activity/direct-log/d2/undo"]


def test_tick_during_undo_neither_rereads_nor_repeats_undo(browser, base) -> None:  # noqa: F811
    with open_onboarding(browser, base) as s:
        s.direct = [_dl("d1", "2099-01-01T00:00:00Z")]
        p = s.page
        p.wait_for_selector(".hob")
        _to_result(p)
        _wait_count(p, ".hob-result", 3)

        def reads() -> int:
            return len([1 for m, pa, _ in s.reqs if m == "GET" and pa.startswith("activity/direct-log")])

        s.hold_undo = True
        p.locator(".hob-result").nth(2).locator("button").click()
        p.wait_for_function("() => document.querySelectorAll('.hob-result button:disabled').length === 1")
        k = reads()
        p.wait_for_timeout(11000)   # 至少一次 tick
        assert reads() == k, "撤销进行中不重读"
        assert len([1 for m, pa, _ in s.reqs if m == "POST" and "direct-log" in pa]) == 1
        s.hold_undo = False
        s.direct = []
        s.held[0].fulfill(status=200, content_type="application/json", body='{"undone": true}')
        _wait_count(p, ".hob-result", 2)
        assert len([1 for m, pa, _ in s.reqs if m == "POST" and "direct-log" in pa]) == 1


def test_claimed_block_also_direct_is_one_row(browser, base) -> None:  # noqa: F811
    with open_onboarding(browser, base) as s:
        s.direct = [dict(_dl("d1", "2099-01-01T00:00:00Z"), payload={"action": "assign", "date": "2026-11-08", "blockKey": GOOD["blockKey"]})]
        p = s.page
        p.wait_for_selector(".hob")
        _to_result(p)
        _wait_count(p, ".hob-result", 2)
        assert p.locator(".hob-name").all_inner_texts() == ["garden", "刷题"]
        assert p.locator(".hob-total").inner_text() == "0.7 小时"
        p.locator(".hob-result").nth(0).locator("button").click()
        _wait_count(p, ".hob-result", 2)   # claim 撤掉后直接施加的那条露出来
        assert s.posts("activity/blocks/claim/undo") == [{"blockKeys": [GOOD["blockKey"]]}]
        assert not [1 for m, pa, _ in s.reqs if m == "POST" and "direct-log" in pa]
