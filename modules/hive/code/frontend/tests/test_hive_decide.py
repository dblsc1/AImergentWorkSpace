"""蜂巢右上角「AI 决策」面板（hex-decide.js）：待批准卡左滑 / 右滑 + 5 秒撤销、同名窗口合并、点卡指给人看，
以及分类历史卡拖进蜂巢改归属 / 新建任务。

复用 test_hive_focus.py 的页面夹具（假时钟已暂停：推进用 page.clock.run_for），端点全在浏览器侧桩掉。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from typing import Any

from playwright.sync_api import Route

from test_hive_focus import base, browser, open_hive  # noqa: F401  (fixtures)

PROPOSALS = {
    "projects": [{"id": "np_1", "kind": "project", "name": "写书", "reason": "最近常开文档", "zoneId": "z_a",
                  "zoneName": "学习", "createdAt": "2026-10-20T00:00:00Z",
                  "tasks": [{"id": "nt_9", "name": "列大纲", "status": "pending"}]}],
    "tasks": [{"id": "nt_2", "kind": "task", "name": "调音", "reason": "钢琴相关", "projectId": "p_piano",
               "projectName": "钢琴", "projectProposalId": None}],
}


def _item(i: str, kind: str = "assign", **kw: Any) -> dict:
    return {"id": i, "kind": kind, "status": "pending", "taskId": "t_p_math", "projectId": None, "newTask": None, "reason": "在做数学", **kw}


def _report(items: list[dict], titles: dict[str, list[str]]) -> tuple[dict, dict]:
    head = {"id": "r1", "author": "hermes", "summary": "今天的整理", "status": "pending", "counts": {"items": len(items)}}
    detail = {**head, "items": [{**it, "suggestions": [{"id": "s" + it["id"] + str(n), "app": "code", "title": t, "durationSeconds": 600}
                                                        for n, t in enumerate(titles[it["id"]])]} for it in items]}
    return {"items": [{**head, "items": items}]}, detail


def _hist(now: str) -> dict:
    return {"date": "2026-10-23", "items": [
        {"id": "a1", "eventId": "e1", "startAt": now, "endAt": now, "durationSeconds": 720, "app": "code", "title": "HoneyCombPub代码开发",
         "taskId": "t_p_piano", "projectId": "p_piano", "reassigned": False, "source": "rules", "ai": False},
        {"id": "a2", "eventId": "e2", "startAt": now, "endAt": now, "durationSeconds": 120, "app": "chrome", "title": "某个网页",
         "taskId": "t_unc_p_piano", "projectId": "p_piano", "reassigned": False, "source": "ai", "ai": True}]}


YESTERDAY = {"date": "2026-10-22", "items": [{"id": "a0", "eventId": "e0", "startAt": (datetime.now(timezone.utc) - timedelta(hours=20)).isoformat(),
                        "endAt": "x", "durationSeconds": 60, "app": "code", "title": "昨天的窗口", "taskId": "t_p_math",
                        "projectId": "p_math", "reassigned": True, "source": "rules", "ai": False}]}


def _install(hive: Any, proposals: dict = PROPOSALS, items: list[dict] | None = None, titles: dict | None = None, y_ok: bool = False) -> None:
    hive.auto_dates = []
    hive.hold = None            # 列表 = 把 planner/tasks 的响应扣下，由测试自己放
    hive.reassign_fail = False
    hive.proposals_fail = False
    items = items if items is not None else [_item("i0"), _item("i1", "dismiss", taskId=None)]
    titles = titles or {"i0": ["数学作业"], "i1": ["刷视频"]}
    reports, detail = _report(items, titles)
    now = datetime.now(timezone.utc).isoformat()

    def route(r: Route) -> None:
        path = r.request.url.split("/api/core/", 1)[1]
        ok = lambda body: r.fulfill(status=200, content_type="application/json", body=json.dumps(body))  # noqa: E731
        if path.startswith("activity/proposals?"):
            if hive.proposals_fail:
                r.fulfill(status=500, content_type="application/json", body="{}")
                return
            ok(proposals)
        elif path == "activity/reports/r1" and r.request.method == "GET":
            ok(detail)
        elif path.startswith("activity/reports?"):
            ok(reports)
        elif path.startswith("activity/auto?date="):
            hive.auto_dates.append(path.split("=", 1)[1])
            ok(YESTERDAY if y_ok else _hist(now))   # y_ok=False：像老服务端一样忽略 date，又回今天
        elif path.startswith("activity/auto"):
            ok(_hist(now))
        elif r.request.method == "POST" and path.startswith("activity/proposals/ch_409/"):
            r.fulfill(status=409, content_type="application/json", body=json.dumps({"detail": "目标已经变了"}))
        elif r.request.method == "POST" and path.startswith("sessions/") and hive.reassign_fail:
            hive.writes.append(path)
            r.fulfill(status=403, content_type="application/json", body=json.dumps({"detail": "设备令牌不能改归属"}))
        elif r.request.method == "POST" and path == "planner/tasks" and hive.hold is not None:
            hive.writes.append(path)
            hive.hold.append(r)
        elif r.request.method == "POST":
            hive.writes.append(path)
            ok({"id": "t_new", "projectId": "p_piano", "name": "x"} if path == "planner/tasks" else {})
        else:
            hive.route(r)
    hive.page.route("**/api/core/activity/**", route)
    hive.page.route("**/api/core/sessions/**", route)
    hive.page.route("**/api/core/planner/tasks", lambda r: route(r) if r.request.method == "POST" else hive.route(r))
    na = {"today": "2026-10-09", "zones": [{"actionable": [
        {"id": "t_p_math", "name": "数学的任务", "projectId": "p_math", "projectName": "数学"},
        {"id": "t_p_piano", "name": "钢琴的任务", "projectId": "p_piano", "projectName": "钢琴"}], "waiting": []}]}
    hive.page.route("**/api/core/views/next-actions*", lambda r: r.fulfill(
        status=200, content_type="application/json", body=json.dumps(na)))


def _until(page: Any, cond: Any, ms: int = 3000) -> None:
    """条件轮询（不是定长睡眠）：cond() 为真就返回，超时断言失败。"""
    for _ in range(ms // 25):
        if cond():
            return
        page.wait_for_timeout(25)
    assert cond(), "等条件超时"


def _open(hive: Any, **kw: Any) -> None:
    _install(hive, **kw)
    hive.page.evaluate("() => window.NexusTableHexApp.refresh()")
    hive.page.wait_for_selector(".hd-head")
    hive.page.locator(".hd-head").click()
    hive.page.wait_for_selector(".hd-card")


def _swipe(page: Any, key: str, dx: float) -> None:
    box = page.locator(f'.hd-card[data-key="{key}"] .hd-face').bounding_box()
    x, y = box["x"] + box["width"] / 2, box["y"] + 20
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x + dx / 2, y, steps=3)
    page.mouse.move(x + dx, y, steps=3)
    page.mouse.up()


def test_collapsed_badge_then_two_columns(browser, base) -> None:  # noqa: F811
    with open_hive(browser, base) as hive:
        _install(hive)
        hive.page.evaluate("() => window.NexusTableHexApp.refresh()")
        hive.page.wait_for_selector(".hd-head")
        assert hive.page.locator(".hd-card").count() == 0, "默认折叠，只有角标"
        assert hive.page.locator(".hd-badge").inner_text() == "4"
        hive.page.locator(".hd-head").click()
        assert hive.page.locator(".hd-col-pend .hd-card").count() == 4
        assert hive.page.locator(".hd-col-hist .hd-card").count() == 2
        h = hive.page.locator(".hd-hcard").nth(1).inner_text()
        assert "某个网页" in h and "未分类" in h and "AI" in h


def test_swipe_left_approves_after_five_seconds_only(browser, base) -> None:  # noqa: F811
    with open_hive(browser, base) as hive:
        _open(hive)
        _swipe(hive.page, "nt_2", -400)
        assert hive.page.locator(".hd-toast").inner_text().startswith("已批准：调音")
        assert hive.page.locator(".hd-badge").inner_text() == "3"
        hive.page.clock.run_for(4900)
        assert hive.writes == [], "5 秒前不发"
        hive.page.clock.run_for(200)
        _until(hive.page, lambda: len(hive.writes) >= 1)
        assert hive.writes == ["activity/proposals/nt_2/approve"]


def test_undo_restores_card_and_sends_nothing(browser, base) -> None:  # noqa: F811
    with open_hive(browser, base) as hive:
        _open(hive)
        _swipe(hive.page, "nt_2", -400)
        hive.page.locator(".hd-toast-undo").click()
        assert hive.page.locator('.hd-card[data-key="nt_2"]').count() == 1
        assert hive.page.locator(".hd-badge").inner_text() == "4"
        hive.page.clock.run_for(6000)
        assert hive.writes == []


def test_short_swipe_snaps_back_and_swipe_right_rejects(browser, base) -> None:  # noqa: F811
    with open_hive(browser, base) as hive:
        _open(hive)
        _swipe(hive.page, "nt_2", -40)
        assert hive.page.locator(".hd-toast").count() == 0
        _swipe(hive.page, "np_1", 400)
        assert hive.page.locator(".hd-toast").inner_text().startswith("已拒绝：写书")
        hive.page.clock.run_for(5100)
        _until(hive.page, lambda: len(hive.writes) >= 1)
        assert hive.writes == ["activity/proposals/np_1/reject"]


def test_keyboard_a_and_r(browser, base) -> None:  # noqa: F811
    with open_hive(browser, base) as hive:
        _open(hive)
        hive.page.locator('.hd-card[data-key="nt_2"]').focus()
        hive.page.keyboard.press("a")
        hive.page.locator('.hd-card[data-key="np_1"]').focus()
        hive.page.keyboard.press("r")
        hive.page.clock.run_for(5100)
        _until(hive.page, lambda: len(hive.writes) >= 2)
        assert sorted(hive.writes) == ["activity/proposals/np_1/reject", "activity/proposals/nt_2/approve"]


def test_same_window_items_merge_into_one_card_and_post_each(browser, base) -> None:  # noqa: F811
    items = [_item("i0"), _item("i1"), _item("i2", taskId="t_p_piano")]
    titles = {"i0": ["✳ 数学作业"], "i1": ["数学作业", "(2) 数学作业"], "i2": ["数学作业"]}
    with open_hive(browser, base) as hive:
        _open(hive, proposals={}, items=items, titles=titles)
        cards = hive.page.locator(".hd-col-pend .hd-card")
        assert cards.count() == 2, "同名同去向合并，去向不同不并"
        first = cards.first.inner_text()
        assert "数学作业" in first and "3 段合并" in first and "↓ 数学 - 数学的任务" in first
        _swipe(hive.page, cards.first.get_attribute("data-key"), -400)
        hive.page.clock.run_for(5100)
        _until(hive.page, lambda: len(hive.writes) >= 2)
        assert hive.writes == ["activity/reports/r1/items/i0/approve", "activity/reports/r1/items/i1/approve"]


def test_failed_item_keeps_card_with_backend_detail(browser, base) -> None:  # noqa: F811
    props = {"projects": [], "tasks": [], "ignores": [], "changes": [
        {"kind": "change", "status": "pending", "reason": "", "createdAt": "x", "createdId": None, "name": None, "toId": None, "toName": None,
         "id": "ch_409", "op": "delete", "target": "project", "targetId": "p_math", "targetName": "数学", "projectId": None},
        {"id": "ch_x", "op": "teleport", "target": "task", "targetId": "t", "targetName": "x", "projectId": None}]}
    with open_hive(browser, base) as hive:
        _open(hive, proposals=props, items=[], titles={})
        assert hive.page.locator('.hd-card[data-key="ch_x"]').count() == 0, "未知操作不画"
        assert "↓ 删除" in hive.page.locator('.hd-card[data-key="ch_409"]').inner_text()
        hive.page.locator('.hd-card[data-key="ch_409"] .hd-win').click()
        assert hive.page.locator(".hex-cell.is-pending-del.is-pending-hl").get_attribute("data-project-id") == "p_math"
        hive.page.locator('.hd-card[data-key="ch_409"] [data-hd=approve]').click()
        hive.page.clock.run_for(5100)
        hive.page.wait_for_selector(".hd-err")
        assert hive.page.locator(".hd-err").inner_text() == "目标已经变了"
        assert hive.page.locator('.hd-card[data-key="ch_409"]').count() == 1


def test_click_card_highlights_in_hive_and_escape_cleans(browser, base) -> None:  # noqa: F811
    with open_hive(browser, base) as hive:
        _open(hive)
        hive.page.locator('.hd-card[data-key="r1/i0"] .hd-win, .hd-card .hd-win >> text=数学作业').first.click()
        assert hive.page.locator(".hex-cell.is-expanded").get_attribute("data-project-id") == "p_math"
        assert hive.page.locator(".is-pending-hl").get_attribute("data-task-id") == "t_p_math"
        hive.page.locator('.hd-card[data-key="nt_2"] .hd-win').click()
        assert hive.page.locator(".hex-cell.is-expanded .hex-todo-card.is-ghost").inner_text().startswith("调音")
        hive.page.locator('.hd-card[data-key="np_1"] .hd-win').click()
        assert "列大纲" in hive.page.locator(".hex-cell.is-ghost.is-expanded").inner_text()
        hive.page.keyboard.press("Escape")
        assert hive.page.locator(".hex-cell.is-ghost").count() == 0
        assert hive.page.locator(".is-pending-hl, .is-pending-del").count() == 0


def _drag_to(page: Any, hist_idx: int, target: Any, steps: int = 4) -> None:
    _until(page, lambda: page.evaluate("getComputedStyle(document.querySelector('.hd-panel')).transform") == "none")   # 上一轮拖拽后面板滑回来
    box = page.locator(".hd-hcard .hd-face").nth(hist_idx).bounding_box()
    page.mouse.move(box["x"] + 30, box["y"] + 20)
    page.mouse.down()
    page.mouse.move(box["x"] - 40, box["y"] + 20, steps=3)
    if target is not None:
        t = target.bounding_box()
        page.mouse.move(t["x"] + t["width"] / 2, t["y"] + t["height"] / 2, steps=steps)


def _onto(page: Any, loc: Any) -> None:
    """把按住的指针挪到 loc 上；格子的展开 / 悬停动画是真时间，等它落定再瞄一次（和人一样）。"""
    for _ in range(2):
        page.clock.run_for(600); page.wait_for_timeout(500)   # 假时钟暂停着：FLIP 的下一帧要自己推
        loc.evaluate("e => e.scrollIntoView({block: 'center'})")
        t = loc.bounding_box()
        page.mouse.move(t["x"] + 30, t["y"] + t["height"] / 2, steps=3)


def test_drag_history_expands_cell_and_drop_on_task_reassigns(browser, base) -> None:  # noqa: F811
    with open_hive(browser, base, width=1900) as hive:
        _open(hive)
        cell = hive.page.locator('.hex-cell[data-project-id=p_math]')
        _drag_to(hive.page, 0, cell)
        assert hive.page.locator(".hex-cell.is-expanded").get_attribute("data-project-id") == "p_math", "拖过去格子自动展开"
        assert hive.page.locator(".hex-cell.is-expanded .hd-newtask").inner_text() == "＋ 新建任务", "拖拽时才有新建行"
        hive.page.clock.run_for(600); hive.page.wait_for_timeout(400)   # 展开的 FLIP 动画
        li = hive.page.locator('.hex-cell.is-expanded .hex-detail li[data-task-id=t_p_math]')
        _onto(hive.page, li)
        assert li.get_attribute("class").find("hd-over") >= 0
        hive.page.mouse.up()
        _until(hive.page, lambda: len(hive.writes) >= 1)
        assert hive.writes == ["sessions/e1/reassign"]
        assert hive.page.locator(".hex-cell.is-expanded").count() == 1, "放下后没有被点空白收起"
        assert hive.page.locator(".hd-newtask").count() == 0
        assert "已改归属" in hive.page.locator(".hd-toast").last.inner_text()


def test_drop_on_cell_reassigns_project_and_elsewhere_cancels(browser, base) -> None:  # noqa: F811
    with open_hive(browser, base, width=1280, height=800) as hive:
        _open(hive)
        _drag_to(hive.page, 0, None)
        hive.page.mouse.move(5, 500, steps=3)
        hive.page.mouse.up()
        assert hive.writes == [], "放在别处 = 取消"
        bodies: list[str] = []
        hive.page.on("request", lambda r: bodies.append(r.post_data or "") if "/sessions/" in r.url else None)
        _drag_to(hive.page, 1, hive.page.locator('.hex-cell[data-project-id=p_math]'))
        _onto(hive.page, hive.page.locator(".hex-cell.is-expanded .hex-group-head").first)   # 格子里不是任务行的地方
        pid = hive.page.locator(".hex-cell.is-expanded").get_attribute("data-project-id")   # 路上经过的格子也会展开：放在哪个就是哪个
        hive.page.mouse.up()
        _until(hive.page, lambda: len(hive.writes) >= 1)
        assert hive.writes == ["sessions/e2/reassign"] and json.loads(bodies[0]) == {"projectId": pid}


def test_drop_on_new_task_row_creates_then_reassigns(browser, base) -> None:  # noqa: F811
    bodies: list[str] = []
    with open_hive(browser, base, width=1280, height=800) as hive:
        _open(hive)
        hive.page.on("request", lambda r: bodies.append(r.post_data or "") if r.method == "POST" and "/sessions/" in r.url else None)
        _drag_to(hive.page, 0, hive.page.locator('.hex-cell[data-project-id=p_math]'))
        hive.page.clock.run_for(600); hive.page.wait_for_timeout(400)
        _onto(hive.page, hive.page.locator(".hex-cell.is-expanded .hd-newtask"))
        hive.page.mouse.up()
        inp = hive.page.locator(".hd-newtask input")
        assert inp.input_value() == "HoneyCombPub代码开发"
        hive.page.keyboard.press("Escape")
        assert hive.page.locator(".hd-newtask").count() == 0 and hive.writes == [], "Esc 取消"
        assert hive.page.locator(".hex-cell.is-expanded").count() == 1, "Esc 只取消输入，不收起格子"
        _drag_to(hive.page, 0, hive.page.locator('.hex-cell.is-expanded'))
        hive.page.clock.run_for(600); hive.page.wait_for_timeout(400)
        _onto(hive.page, hive.page.locator(".hex-cell.is-expanded .hd-newtask"))
        hive.page.mouse.up()
        hive.page.locator(".hd-newtask input").fill("新任务名")
        hive.page.keyboard.press("Enter")
        _until(hive.page, lambda: len(hive.writes) >= 2)
        assert hive.writes[:2] == ["planner/tasks", "sessions/e1/reassign"]
        assert json.loads(bodies[0]) == {"taskId": "t_new"}


def test_history_merges_yesterday_and_dedupes_old_servers(browser, base) -> None:  # noqa: F811
    with open_hive(browser, base) as hive:
        _install(hive, y_ok=True)
        hive.page.evaluate("() => window.NexusTableHexApp.refresh()")
        hive.page.wait_for_selector(".hd-head")
        hive.page.locator(".hd-head").click()
        assert hive.page.locator(".hd-hcard").count() == 3
        assert "昨天的窗口" in hive.page.locator(".hd-hcard").last.inner_text(), "昨天的在后（新的在前）"
        assert "已改" in hive.page.locator(".hd-hcard").last.inner_text()
        assert hive.auto_dates[0] == "2026-10-22", "昨天按服务端响应的 date 推，不是浏览器时区"
    with open_hive(browser, base) as hive:
        _install(hive)   # 老服务端：date 被忽略、又回今天
        hive.page.evaluate("() => window.NexusTableHexApp.refresh()")
        hive.page.wait_for_selector(".hd-head")
        hive.page.locator(".hd-head").click()
        assert hive.page.locator(".hd-hcard").count() == 2, "按 id 去重"


def test_panel_steps_aside_while_dragging(browser, base) -> None:  # noqa: F811
    with open_hive(browser, base) as hive:
        _open(hive)
        box = hive.page.locator(".hd-hcard .hd-face").first.bounding_box()
        hive.page.mouse.move(box["x"] + 30, box["y"] + 20)
        hive.page.mouse.down()
        hive.page.mouse.move(box["x"] - 40, box["y"] + 20, steps=3)
        hive.page.wait_for_timeout(400)
        assert hive.page.evaluate("getComputedStyle(document.querySelector('.hd-panel')).pointerEvents") == "none"
        assert hive.page.locator(".hd-panel").bounding_box()["x"] > 1100, "几乎滑出右边"
        hive.page.mouse.up()
        hive.page.wait_for_timeout(400)
        assert hive.page.evaluate("getComputedStyle(document.querySelector('.hd-panel')).pointerEvents") != "none"
        assert hive.page.locator(".hd-panel").bounding_box()["x"] < 700, "放下后复位"


# ───────────────────────── 复审补的用例 ─────────────────────────


def _key_of(page: Any, text: str) -> str:
    return page.locator(".hd-col-pend .hd-card", has_text=text).first.get_attribute("data-key")


def test_approve_all_is_disabled_while_a_card_of_that_report_is_held(browser, base) -> None:  # noqa: F811
    with open_hive(browser, base) as hive:
        _open(hive)
        b = hive.page.locator("[data-hd=approve-all]")
        assert b.is_enabled()
        _swipe(hive.page, _key_of(hive.page, "数学作业"), -400)
        assert b.is_disabled() and b.get_attribute("title") == "等撤销倒计时结束"
        hive.page.locator(".hd-toast-undo").click()
        assert b.is_enabled()
        _swipe(hive.page, _key_of(hive.page, "数学作业"), -400)
        hive.page.clock.run_for(5100)
        _until(hive.page, lambda: len(hive.writes) >= 1)
        assert hive.writes == ["activity/reports/r1/items/i0/approve"], "只发那张卡自己的，没有越过它批整份"


def _to_new_task_input(hive: Any) -> Any:
    _drag_to(hive.page, 0, hive.page.locator('.hex-cell[data-project-id=p_math]'))
    _onto(hive.page, hive.page.locator(".hex-cell.is-expanded .hd-newtask"))
    hive.page.mouse.up()
    inp = hive.page.locator(".hd-newtask input")
    inp.wait_for()
    return inp


def test_new_task_input_ime_blur_and_in_flight(browser, base) -> None:  # noqa: F811
    with open_hive(browser, base, width=1280, height=800) as hive:
        _open(hive)
        inp = _to_new_task_input(hive)
        inp.evaluate("e => e.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', keyCode: 229, isComposing: true, bubbles: true}))")
        assert hive.writes == [], "输入法选字的回车不提交"
        hive.page.mouse.click(5, 500)   # 点到别处 = 失焦 = 取消
        _until(hive.page, lambda: hive.page.locator(".hd-newtask").count() == 0)
        assert hive.writes == []
        # 请求在路上：Esc / 失焦都不取消；放行后照常改归属
        hive.hold = []
        _install_hold = hive.hold
        inp = _to_new_task_input(hive)
        hive.page.keyboard.press("Enter")
        _until(hive.page, lambda: len(_install_hold) == 1)
        hive.page.keyboard.press("Escape")
        assert hive.page.locator(".hd-newtask input").count() == 1 and inp.evaluate("e => e.readOnly"), "在路上不取消"
        _install_hold[0].fulfill(status=200, content_type="application/json", body=json.dumps({"id": "t_new", "projectId": "p_math", "name": "x"}))
        _until(hive.page, lambda: "sessions/e1/reassign" in hive.writes)
        assert hive.page.locator(".hd-newtask").count() == 0


def test_created_task_but_reassign_failed_says_so(browser, base) -> None:  # noqa: F811
    with open_hive(browser, base, width=1280, height=800) as hive:
        _open(hive)
        hive.reassign_fail = True
        inp = _to_new_task_input(hive)
        inp.fill("新任务名")
        hive.page.keyboard.press("Enter")
        _until(hive.page, lambda: "sessions/e1/reassign" in hive.writes)
        _until(hive.page, lambda: hive.page.locator(".hd-toast.is-error").count() == 1)
        msg = hive.page.locator(".hd-toast.is-error").inner_text()
        assert "任务已建好（新任务名）" in msg and "改归属没成：设备令牌不能改归属" in msg
        assert hive.writes.count("planner/tasks") == 1, "没有重复建任务"


def test_escape_blur_and_hidden_cancel_a_drag(browser, base) -> None:  # noqa: F811
    with open_hive(browser, base, width=1280, height=800) as hive:
        _open(hive)
        for how in ("Escape", "blur", "hidden"):
            _drag_to(hive.page, 0, hive.page.locator('.hex-cell[data-project-id=p_math]'))
            assert hive.page.locator(".hd-ghost").count() == 1
            if how == "Escape":
                hive.page.keyboard.press("Escape")
            elif how == "blur":
                hive.page.evaluate("() => window.dispatchEvent(new Event('blur'))")
            else:
                hive.page.evaluate("() => { Object.defineProperty(document, 'visibilityState', {value: 'hidden', configurable: true}); document.dispatchEvent(new Event('visibilitychange')); Object.defineProperty(document, 'visibilityState', {value: 'visible', configurable: true}); }")
            assert hive.page.locator(".hd-ghost").count() == 0 and hive.page.locator(".hd-newtask").count() == 0, how
            assert hive.page.evaluate("document.body.classList.contains('hd-dragging')") is False, how
            assert hive.page.evaluate("getComputedStyle(document.querySelector('.hd-panel')).pointerEvents") != "none", how
            hive.page.mouse.up()
            _until(hive.page, lambda: hive.page.locator(".hd-panel").bounding_box()["x"] < 700)   # 面板滑回来再拖下一轮
            assert hive.writes == [], how
        assert hive.page.locator(".hex-cell.is-expanded").count() == 1, "Esc 只取消拖拽，没有把格子收起"


def _touch(page: Any, kind: str, x: float, y: float, target: str = "window") -> None:
    page.evaluate("""([kind, x, y, target]) => {
        const ev = new PointerEvent(kind, {pointerType: 'touch', pointerId: 7, clientX: x, clientY: y, button: 0, bubbles: true, cancelable: true});
        (target === 'face' ? document.querySelector('.hd-hcard .hd-face') : document).dispatchEvent(ev);
    }""", [kind, x, y, target])


def test_touch_drag_needs_a_long_press(browser, base) -> None:  # noqa: F811
    with open_hive(browser, base) as hive:
        _open(hive)
        box = hive.page.locator(".hd-hcard .hd-face").first.bounding_box()
        x, y = box["x"] + 30, box["y"] + 20
        _touch(hive.page, "pointerdown", x, y, "face")
        _touch(hive.page, "pointermove", x - 60, y)
        assert hive.page.locator(".hd-ghost").count() == 0, "没按够就动 = 滚动列表，不拖"
        _touch(hive.page, "pointerup", x - 60, y)
        _touch(hive.page, "pointerdown", x, y, "face")
        hive.page.clock.run_for(350)
        assert "is-armed" in hive.page.locator(".hd-hcard .hd-face").first.get_attribute("class")
        _touch(hive.page, "pointermove", x - 60, y)
        assert hive.page.locator(".hd-ghost").count() == 1, "长按之后才拖"
        _touch(hive.page, "pointerup", 5, 500)
        assert hive.page.locator(".hd-ghost").count() == 0 and hive.writes == []


def test_swipe_flag_is_cleared_by_the_click_it_swallows(browser, base) -> None:  # noqa: F811
    with open_hive(browser, base) as hive:
        _open(hive)
        _swipe(hive.page, "nt_2", -40)
        assert hive.page.evaluate("document.querySelector('.hd-card[data-key=nt_2]').__swiped") is False
        hive.page.locator('.hd-card[data-key="nt_2"] .hd-win').click()
        assert hive.page.locator(".hex-cell.is-expanded .hex-todo-card.is-ghost").inner_text().startswith("调音"), "下一次真点击不被吞"


def test_undo_during_a_drag_restores_the_card_in_place(browser, base) -> None:  # noqa: F811
    with open_hive(browser, base, width=1280, height=800) as hive:
        _open(hive)
        _swipe(hive.page, "nt_2", -400)
        assert "is-gone" in hive.page.locator('.hd-card[data-key="nt_2"]').get_attribute("class")
        _drag_to(hive.page, 0, hive.page.locator('.hex-cell[data-project-id=p_math]'))
        hive.page.evaluate("() => document.querySelector('.hd-toast-undo').click()")
        cls = hive.page.locator('.hd-card[data-key="nt_2"]').get_attribute("class")
        assert "is-gone" not in cls, "拖拽中撤销：卡片直接放回"
        hive.page.keyboard.press("Escape")
        hive.page.mouse.up()
        assert hive.page.locator(".hd-badge").inner_text() == "4"
        hive.page.clock.run_for(6000)
        assert hive.writes == []


def test_hidden_tab_flushes_in_order_project_first(browser, base) -> None:  # noqa: F811
    with open_hive(browser, base) as hive:
        _open(hive)
        _swipe(hive.page, "nt_2", -400)    # 任务提议先滑
        _swipe(hive.page, "np_1", -400)    # 项目提议后滑
        hive.page.evaluate("() => { Object.defineProperty(document, 'visibilityState', {value: 'hidden', configurable: true}); document.dispatchEvent(new Event('visibilitychange')); }")
        _until(hive.page, lambda: len(hive.writes) >= 2)
        assert hive.writes == ["activity/proposals/np_1/approve", "activity/proposals/nt_2/approve"], "项目在前，一条接一条"


def test_keyboard_moves_focus_to_next_card_and_failed_fetch_keeps_cards(browser, base) -> None:  # noqa: F811
    with open_hive(browser, base) as hive:
        _open(hive)
        hive.page.locator('.hd-card[data-key="nt_2"]').focus()
        hive.page.keyboard.press("a")
        active = hive.page.evaluate("document.activeElement.className + '|' + document.activeElement.dataset.key")
        assert active.startswith("hd-card") and not active.endswith("|nt_2"), active
        hive.page.locator('.hd-toast-undo').first.click()
        assert hive.page.locator(".hd-col-pend .hd-card").count() == 4
        hive.proposals_fail = True
        hive.page.evaluate("() => window.NexusTableHexApp.refresh()")
        assert hive.page.locator(".hd-col-pend .hd-card:not(.is-gone)").count() >= 2, "取不到提议：留着上一份"
