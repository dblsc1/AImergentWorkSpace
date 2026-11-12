"""点项目格 = 原来的大六边形，项目面板放进它里面（hive.stage.v1「十二」）。

接口桩与假时钟沿用 test_hive_stage.py；这里另桩 views/project-time、views/next-actions、planner/audit。
"""

from __future__ import annotations

from datetime import timedelta
import json
import os
import re
from typing import Any

from test_hive_focus import base, browser  # noqa: F401  (fixtures)
from test_hive_stage import CELL, TZ, Stage, _busy_day, _iso, open_stage

EXPANDED = ".hex-cell.is-expanded"
SHOT = os.environ.get("HIVE_SHOTS")


def _seed(st: Stage, *, human_s: int = 180, agent_s: int = 720, cancelled: bool = False) -> None:
    """p_math 的任务 t_p_math：今天人 human_s 秒、代理 agent_s 秒；审计流里它已完成；待办里有「写大纲」。"""
    now = st.now().astimezone(TZ)
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    a = day + timedelta(hours=1)
    tasks = [{"taskId": "t_p_math",
              "human": [{"start": _iso(a), "end": _iso(a + timedelta(seconds=human_s))}] if human_s else [],
              "agent": [{"start": _iso(a), "end": _iso(a + timedelta(seconds=agent_s)), "agent": "garden", "runId": "r1"}] if agent_s else []}]
    st.page.route("**/api/core/views/project-time*", lambda r: r.fulfill(
        status=200, content_type="application/json", body=json.dumps({"projectId": "p_math", "tasks": tasks})))
    audit = {"total": 1, "items": [{"seq": 5, "at": _iso(now - timedelta(minutes=5)), "objectType": "tasks", "objectId": "t_p_math", "changes": {"done": True}}]}
    st.page.route("**/api/core/planner/audit*", lambda r: r.fulfill(status=200, content_type="application/json", body=json.dumps(audit)))
    nxt = {"today": "2026-10-09", "zones": [{"zoneId": "z_a", "actionable": [
        {"id": "t_outline", "name": "写大纲", "projectId": "p_math", "projectName": "数学"}], "waiting": []}]}
    st.page.route("**/api/core/views/next-actions*", lambda r: r.fulfill(status=200, content_type="application/json", body=json.dumps(nxt)))


def _setup(st: Stage) -> None:
    _busy_day(st)
    _seed(st)


def _open(st: Stage) -> None:
    st.page.click(CELL.format("p_math"))
    st.page.wait_for_selector(EXPANDED + " .pr-row .pr-seg")
    settle_zoom(st)


def settle_zoom(st: Stage) -> None:
    """展开动画靠假时钟推（FLIP 与推镜），再给真时间让样式过渡走完。"""
    st.page.clock.run_for(1000)
    st.page.wait_for_timeout(500)
    st.page.clock.run_for(600)


def test_click_expands_the_big_hexagon_with_ruler_tasks_group_and_recent_line_in_order(browser, base) -> None:  # noqa: F811  十二·1 3
    with open_stage(browser, base, setup=_setup) as st:
        _open(st)
        cell = st.page.locator(EXPANDED)
        assert cell.count() == 1 and cell.get_attribute("data-project-id") == "p_math"
        assert cell.locator(".hex-title").inner_text() == "数学"
        assert cell.locator(".hex-ruler-slot .pr-row").count() == 2 and cell.locator(".hex-ruler-slot .pr-ruler").count() == 1
        titles = cell.locator(".hex-group-title").all_inner_texts()
        assert titles == ["任务", "最近完成"] and "下一步" not in cell.inner_text()
        # 从上到下：项目名 < 任务条 < 「任务」组 < 「最近完成」
        ys = [cell.locator(s).first.bounding_box()["y"] for s in (".hex-title", ".hex-ruler-slot .pr-row", ".hex-group-title >> text=任务", ".hex-group-title >> text=最近完成")]
        assert ys == sorted(ys) and len(set(ys)) == 4
        assert cell.locator(".hex-next-list li").first.inner_text().startswith("写大纲")
        # 最近完成：<任务名> 完成 · 人花了 X 分钟 · agent 花了 Y 分钟
        line = cell.locator(".is-done-card").first
        line.locator(".hex-spent:not(:empty)").wait_for()
        text = re.sub(r"\s+", " ", line.inner_text()).replace("撤销", "").strip()
        assert text == "数学的任务 完成 · 人花了 3 分钟 · agent 花了 12 分钟", text
        if SHOT:
            st.page.screenshot(path=f"{SHOT}/bighex.png")


def test_recent_line_says_under_a_minute_and_zero(browser, base) -> None:  # noqa: F811  十二·3
    def setup(st: Stage) -> None:
        _busy_day(st)
        _seed(st, human_s=20, agent_s=0)
    with open_stage(browser, base, setup=setup) as st:
        _open(st)
        line = st.page.locator(EXPANDED + " .is-done-card .hex-spent:not(:empty)")
        line.wait_for()
        assert line.inner_text() == "完成 · 人花了 不到 1 分钟 · agent 花了 0 分钟"


def test_click_on_a_done_line_still_only_offers_undo(browser, base) -> None:  # noqa: F811  十二·3
    with open_stage(browser, base, setup=_setup) as st:
        _open(st)
        card = st.page.locator(EXPANDED + " .is-done-card").first
        card.click()
        assert [b.inner_text() for b in card.locator(".hex-mini").all()] == ["撤销"]


def test_content_scrolls_inside_and_nothing_sticks_out_of_the_hexagon(browser, base) -> None:  # noqa: F811  十二·1
    with open_stage(browser, base, setup=_setup) as st:
        _open(st)
        r = st.page.evaluate("""() => {
          const cell = document.querySelector('.hex-cell.is-expanded'), b = cell.getBoundingClientRect();
          const det = cell.querySelector('.hex-detail');
          const bad = [...det.querySelectorAll('.pr-row, .hex-todo-card, .hex-group-title')].filter(e => {
            const r = e.getBoundingClientRect(), d = det.getBoundingClientRect();
            return r.right > d.right + 1 || r.left < d.left - 1;
          }).length;
          return { cw: b.width, ch: b.height, overflowY: getComputedStyle(det).overflowY, bad, inside: det.getBoundingClientRect().right <= b.right };
        }""")
        assert r["overflowY"] == "auto" and r["bad"] == 0 and r["inside"]


def test_working_pet_of_the_expanded_project_grows_and_stands_outside_the_edge(browser, base) -> None:  # noqa: F811  十二·4
    with open_stage(browser, base, setup=_setup) as st:
        pet, other = '.st-pet.is-work[data-run-id="r1"]', '.st-pet.is-loose[data-run-id="r2"]'
        size = lambda sel: st.page.locator(sel + " .st-pet-in").evaluate("e => e.getBoundingClientRect().height")  # noqa: E731
        small, other_small = size(pet), size(other)
        _open(st)
        st.page.clock.run_for(1000)
        st.page.wait_for_timeout(1200)
        assert size(pet) > small * 1.5 and abs(size(other) - other_small) < 1     # 放大；别的小人不变
        # 浮在大六边形之上、不被盖住
        on_top = st.page.evaluate("""() => {
          const p = document.querySelector('.st-pet.is-work[data-run-id="r1"] .st-pet-in').getBoundingClientRect();
          const e = document.elementFromPoint(p.left + p.width / 2, p.top + p.height / 2);
          return !!(e && e.closest('.st-pet'));
        }""")
        assert on_top
        st.page.keyboard.press("Escape")
        settle_zoom(st)
        assert abs(size(pet) - small) < 1                                          # 收起回到原大小


def test_drag_to_a_task_row_inside_the_big_hexagon_files_to_the_task(browser, base) -> None:  # noqa: F811  十二·2
    with open_stage(browser, base, setup=_setup) as st:
        t0 = st.now().astimezone(TZ)
        st.unplaced = [{"id": "u1", "startAt": _iso(t0), "endAt": _iso(t0 + timedelta(minutes=12)), "durationSeconds": 720}]
        st.poll()
        _open(st)
        row = '.hex-ruler-slot .pr-row[data-task="t_p_math"]'
        st.drag(".st-strip", st.center(row), release=False)
        assert "is-drop" in st.page.locator(row).get_attribute("class")
        st.page.mouse.up()
        st.settle()
        assert st.posts == [("timer/unplaced/u1/place", {"taskId": "t_p_math"})]


def _hover_open(st: Stage) -> None:
    """拖着白条在项目格上停满 1 秒，不松手：弹簧展开。"""
    t0 = st.now().astimezone(TZ)
    st.unplaced = [{"id": "u1", "startAt": _iso(t0), "endAt": _iso(t0 + timedelta(minutes=12)), "durationSeconds": 720}]
    st.poll()
    st.drag(".st-strip", st.center(CELL.format("p_math")), release=False)
    st.page.clock.run_for(1100)
    st.page.wait_for_selector(EXPANDED + " .pr-row .pr-seg")
    settle_zoom(st)


def test_spring_open_treats_pets_like_a_click_open(browser, base) -> None:  # noqa: F811  十二·5
    with open_stage(browser, base, setup=_setup) as st:
        pet, other = '.st-pet.is-work[data-run-id="r1"]', '.st-pet.is-loose[data-run-id="r2"]'
        size = lambda sel: st.page.locator(sel + " .st-pet-in").evaluate("e => e.getBoundingClientRect().height")  # noqa: E731
        small, other_small = size(pet), size(other)
        _hover_open(st)
        st.page.clock.run_for(1000)
        st.page.wait_for_timeout(1200)
        assert size(pet) > small * 1.5 and abs(size(other) - other_small) < 1
        z = st.page.evaluate("""() => { const z = s => +getComputedStyle(document.querySelector(s)).zIndex;
          return [z('.st-pet.is-work[data-run-id="r1"]'), z('.st-pet.is-loose[data-run-id="r2"]'), z('.hive-cam'), z('.st-strip.st-dragging')]; }""")
        assert z[1] < z[2] < z[0] and z[3] > z[0], z       # 别的小人在六边形下、这个项目的小人在上、拖着的东西最上
        # 别的小人即便停在六边形范围内也不盖住它：六边形里任一点取到的都不是别的小人
        covered = st.page.evaluate("""() => { const b = document.querySelector('.hex-cell.is-expanded').getBoundingClientRect(), o = document.querySelector('.st-pet.is-loose[data-run-id="r2"] .st-pet-in').getBoundingClientRect();
          const x = Math.min(Math.max(o.left + o.width / 2, b.left + 5), b.right - 5), y = b.top + b.height / 2;
          return !!document.elementFromPoint(x, y).closest('.st-pet.is-loose'); }""")
        assert covered is False
        st.page.mouse.up()
        st.settle()


def test_new_task_slot_is_last_and_a_drag_released_on_it_asks_for_a_name_then_files(browser, base) -> None:  # noqa: F811  十二·6
    with open_stage(browser, base, setup=_setup) as st:
        st.replies["planner/tasks"] = (201, {"id": "t_new", "name": "新任务"})
        _hover_open(st)
        slot = ".hex-ruler-slot .pr-newslot"
        assert st.page.locator(slot).inner_text() == "＋ 新建任务"
        assert st.page.evaluate("() => document.querySelector('.hex-ruler-slot .pr-list').lastElementChild.className") == "pr-newslot"
        st.page.mouse.move(*st.center(slot), steps=6)
        assert "is-drop" in st.page.locator(slot).get_attribute("class")
        assert st.page.evaluate("""() => { const r = document.querySelector('.st-strip.st-dragging').getBoundingClientRect();
          return !!document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2).closest('.st-strip'); }""") is True   # 拖着的白条在六边形之上
        if SHOT:
            st.page.screenshot(path=f"{SHOT}/bighex-drag.png")
        st.page.mouse.up()
        st.page.locator(".pr-new-name").fill("新任务")
        st.page.keyboard.press("Enter")
        st.settle()
        assert st.posts == [("planner/tasks", {"projectId": "p_math", "name": "新任务"}), ("timer/unplaced/u1/place", {"taskId": "t_new"})]


def test_click_on_the_new_task_slot_creates_a_task_without_filing_anything(browser, base) -> None:  # noqa: F811  十二·6
    with open_stage(browser, base, setup=_setup) as st:
        st.replies["planner/tasks"] = (201, {"id": "t_new", "name": "新任务"})
        _open(st)
        st.page.locator(".hex-ruler-slot .pr-newslot").click()
        st.page.locator(".pr-new-name").fill("新任务")
        st.page.keyboard.press("Enter")
        st.settle()
        assert st.posts == [("planner/tasks", {"projectId": "p_math", "name": "新任务"})]
        assert st.page.locator(".pr-new-name").count() == 0


def test_human_time_is_a_thin_red_strip_on_the_top_edge_of_the_agent_lane(browser, base) -> None:  # noqa: F811  十二·7
    with open_stage(browser, base, setup=_setup) as st:
        _open(st)
        r = st.page.evaluate("""() => { const row = document.querySelector('.pr-row[data-task="t_p_math"]'), lanes = row.querySelector('.pr-lanes').getBoundingClientRect();
          const h = row.querySelector('.pr-human .pr-seg').getBoundingClientRect(), a = row.querySelector('.pr-agent .pr-seg').getBoundingClientRect();
          return { top: h.top - lanes.top, hh: h.height, lh: lanes.height, ah: a.height }; }""")
        assert abs(r["top"]) < 1 and r["hh"] <= 5 and r["ah"] > r["hh"] * 3 and r["lh"] >= 25, r
        title = st.page.locator('.pr-row[data-task="t_p_math"] .pr-human .pr-seg').first.get_attribute("title")
        assert re.fullmatch(r"\d\d/\d\d \d\d:\d\d–\d\d:\d\d 你", title)


def test_human_only_time_still_sits_on_top_with_the_lane_below_empty(browser, base) -> None:  # noqa: F811  十二·7
    def setup(st: Stage) -> None:
        _busy_day(st)
        _seed(st, human_s=600, agent_s=0)
    with open_stage(browser, base, setup=setup) as st:
        _open(st)
        assert st.page.locator('.pr-row[data-task="t_p_math"] .pr-agent .pr-seg').count() == 0
        h = st.page.locator('.pr-row[data-task="t_p_math"] .pr-human .pr-seg').first.bounding_box()
        lanes = st.page.locator('.pr-row[data-task="t_p_math"] .pr-lanes').bounding_box()
        assert abs(h["y"] - lanes["y"]) < 1 and lanes["height"] >= 25
