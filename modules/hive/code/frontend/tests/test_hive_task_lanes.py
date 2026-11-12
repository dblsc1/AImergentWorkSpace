"""大六边形改版（hive.stage.v1「十五」）：任务泳道默认 3 行 +「展开全部」、排序、点任务名出操作并「取消」、最近完成。

接口桩沿用 test_hive_stage.py；这里换成 5 个未完成任务 + 1 个已取消 + 1 个已完成的 views/tree。
"""

from __future__ import annotations

import copy
from datetime import timedelta
import json
import os
from typing import Any

from test_hive_focus import TREE, base, browser  # noqa: F401  (fixtures)
from test_hive_stage import CELL, TZ, Stage, _busy_day, _iso, open_stage
from test_hive_big_hex import EXPANDED, settle_zoom

SHOT = os.environ.get("HIVE_SHOTS")
NAMES = {"t1": "任务甲", "t2": "任务乙", "t3": "任务丙", "t4": "任务丁", "t5": "任务戊"}


def _task(tid: str, name: str, **extra: Any) -> dict[str, Any]:
    return {"id": tid, "key": "k", "name": name, "done": False, "canceled": False, "kind": "normal", "flags": [], "plan": None, "dependsOn": [], **extra}


def _setup(st: Stage) -> None:
    _busy_day(st)
    tree = copy.deepcopy(TREE)
    math = next(p for p in tree["projects"] if p["id"] == "p_math")
    math["tasks"] = [_task(t, n) for t, n in NAMES.items()] + [_task("t_cx", "已取消的", done=True, canceled=True), _task("t_dn", "做完的", done=True)]
    st.tree = tree
    now = st.now().astimezone(TZ)
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)

    def span(h: int, m: int = 0, minutes: int = 20, **extra: Any) -> dict[str, Any]:
        a = day + timedelta(hours=h, minutes=m)
        return {"start": _iso(a), "end": _iso(a + timedelta(minutes=minutes)), **extra}

    # 最近活动：丙（今天 11 点）> 戊（9 点）> 甲（昨天）；乙、丁没有 → 按名字：丁（拼音 ding）< 乙（yi）
    st.pt = [{"taskId": "t3", "human": [span(11)], "agent": [span(11, 5, agent="garden", runId="r1")]},
             {"taskId": "t5", "human": [span(9)], "agent": []},
             {"taskId": "t1", "human": [], "agent": [{**span(14), "start": _iso(day - timedelta(days=1) + timedelta(hours=14)), "end": _iso(day - timedelta(days=1) + timedelta(hours=15)), "agent": "codex", "runId": "r9"}]},
             {"taskId": None, "human": [span(8)], "agent": []},
             {"taskId": "t_cx", "human": [span(7, minutes=30)], "agent": []}]
    st.audit = [{"seq": 5, "at": _iso(now - timedelta(minutes=5)), "objectType": "tasks", "objectId": "t_cx", "changes": {"canceled": True}},
                {"seq": 4, "at": _iso(now - timedelta(minutes=9)), "objectType": "tasks", "objectId": "t_dn", "changes": {"done": True}}]
    ok = lambda body: (lambda r: r.fulfill(status=200, content_type="application/json", body=json.dumps(body(), ensure_ascii=False)))  # noqa: E731
    st.page.route("**/api/core/views/project-time*", ok(lambda: {"projectId": "p_math", "tasks": st.pt}))
    st.page.route("**/api/core/planner/audit*", ok(lambda: {"total": len(st.audit), "items": st.audit}))
    st.page.route("**/api/core/views/next-actions*", ok(lambda: {"today": "2026-10-09", "zones": []}))


def _open(st: Stage) -> None:
    st.page.click(CELL.format("p_math"))
    st.page.wait_for_selector(EXPANDED + " .pr-row .pr-seg")
    settle_zoom(st)


def _names(st: Stage) -> list[str]:
    return st.page.locator(EXPANDED + " .pr-row b").all_inner_texts()


def test_only_three_task_lanes_by_default_in_activity_order_then_the_unassigned_row(browser, base) -> None:  # noqa: F811  十五·2 3 4
    with open_stage(browser, base, width=1440, height=1100, setup=_setup) as st:
        _open(st)
        assert _names(st) == ["任务丙", "任务戊", "任务甲", "未分配任务用时"]      # 最近活动在前；未分配不占名额；已取消 / 已完成的不画行
        btn = st.page.locator(EXPANDED + " .pr-expand")
        assert btn.inner_text() == "展开全部（5）"
        # 本版没有 priority：行上不带 data-priority
        assert st.page.locator(EXPANDED + " .pr-row[data-priority]").count() == 0
        # 已取消任务的时间不并进「未分配任务用时」（只有 8 点那一段人的时间）
        assert st.page.locator(EXPANDED + ' .pr-row[data-task=""] .pr-human .pr-seg').count() == 1
        btn.click()
        assert _names(st) == ["任务丙", "任务戊", "任务甲", "任务丁", "任务乙", "未分配任务用时"]     # 都没活动：按名字
        assert btn.inner_text() == "收起"
        btn.click()
        assert len(_names(st)) == 4
        st.page.keyboard.press("Escape")
        st.page.click(CELL.format("p_math"))
        settle_zoom(st)
        assert len(_names(st)) == 4                                                              # 重新打开回到只列 3 行
        if SHOT:
            st.page.locator(EXPANDED + " .pr-expand").click()
            st.page.mouse.move(5, 5)
            st.page.screenshot(path=f"{SHOT}/bighex2.png")


def test_hovering_the_expand_button_while_dragging_for_06s_expands_all(browser, base) -> None:  # noqa: F811  十五·3
    with open_stage(browser, base, setup=_setup) as st:
        t0 = st.now().astimezone(TZ)
        st.unplaced = [{"id": "u1", "startAt": _iso(t0), "endAt": _iso(t0 + timedelta(minutes=12)), "durationSeconds": 720}]
        st.poll()
        _open(st)
        st.drag(".st-strip", st.center(EXPANDED + " .pr-expand"), release=False)
        st.page.clock.run_for(400)
        assert len(_names(st)) == 4                      # 不到 0.6 秒还没展开
        st.page.clock.run_for(300)
        assert len(_names(st)) == 6                      # 停满 0.6 秒：展开
        st.page.mouse.move(*st.center(EXPANDED + ' .pr-row[data-task="t2"]'), steps=6)
        st.page.mouse.up()
        st.settle()
        assert st.posts == [("timer/unplaced/u1/place", {"taskId": "t2"})]                     # 放到了后面的任务上


def test_click_task_name_offers_cancel_which_files_it_under_recent_as_canceled_and_undo_brings_it_back(browser, base) -> None:  # noqa: F811  十五·6
    with open_stage(browser, base, width=1440, height=1100, setup=_setup) as st:
        def patch(route) -> None:
            body = json.loads(route.request.post_data)
            tid = route.request.url.rsplit("/", 1)[1]
            st.posts.append((f"planner/tasks/{tid}", body))
            task = next(t for p in st.tree["projects"] for t in p["tasks"] if t["id"] == tid)
            if body.get("canceled") is True:
                task.update(done=True, canceled=True)
                st.audit.insert(0, {"seq": 9, "at": _iso(st.now()), "objectType": "tasks", "objectId": tid, "changes": {"canceled": True}})
            else:
                task.update(done=False, canceled=False)
                st.audit.insert(0, {"seq": 10, "at": _iso(st.now()), "objectType": "tasks", "objectId": tid, "changes": body})
            route.fulfill(status=200, content_type="application/json", body=json.dumps(task))
        st.page.route("**/api/core/planner/tasks/*", patch)
        _open(st)
        row = EXPANDED + ' .pr-row[data-task="t3"]'
        assert st.page.locator(row + " .pr-menu").count() == 0
        st.page.locator(row + " .pr-name").click()
        assert st.page.locator(row + " .pr-menu .hex-mini").all_inner_texts() == ["完成", "改名", "取消", "删"]
        st.page.locator(row + " .pr-name").click()
        assert st.page.locator(row + " .pr-menu").count() == 0                                   # 再点合上
        st.page.locator(row + " .pr-name").click()
        st.page.locator(row + ' [data-hex-action="cancel-task"]').click()
        st.page.wait_for_timeout(300)
        st.page.clock.run_for(600)
        assert st.posts == [("planner/tasks/t3", {"canceled": True})]
        assert "任务丙" not in _names(st) and _names(st)[:2] == ["任务戊", "任务甲"]
        head = st.page.locator(EXPANDED + " .pr-donehead")
        assert head.inner_text() == "最近完成（3）"
        head.click()
        lines = st.page.locator(EXPANDED + " .is-done-card")
        lines.first.locator(".hex-spent:not(:empty)").wait_for()
        texts = [" ".join(l.inner_text().replace("撤销", "").split()) for l in lines.all()]
        assert texts[0].startswith("任务丙 取消 · 人花了 20 分钟 · agent 花了 20 分钟"), texts       # 最近的在前，写「取消」
        assert any(t.startswith("已取消的 取消 ·") for t in texts) and any(t.startswith("做完的 完成 ·") for t in texts)
        if SHOT:
            st.page.evaluate("() => { const d = document.querySelector('.hex-cell.is-expanded .hex-detail'); d.scrollTop = d.scrollHeight; }")
            st.page.mouse.move(5, 5)
            st.page.screenshot(path=f"{SHOT}/bighex2-done.png")
        # 撤销：老的 PATCH done:false，任务回到泳道
        lines.first.click()
        st.page.clock.run_for(50)        # 假时钟下事件时间戳不走：两次点击之间让它走一步
        lines.first.locator('[data-hex-action="toggle-task"]').click()
        st.page.wait_for_timeout(300)
        st.page.clock.run_for(600)
        assert st.posts[-1] == ("planner/tasks/t3", {"done": False})
        assert "任务丙" in _names(st)


def test_task_name_menu_is_not_offered_on_the_unassigned_row(browser, base) -> None:  # noqa: F811  十五·6
    with open_stage(browser, base, setup=_setup) as st:
        _open(st)
        st.page.locator(EXPANDED + ' .pr-row[data-task=""] b').click()
        assert st.page.locator(EXPANDED + " .pr-menu").count() == 0
