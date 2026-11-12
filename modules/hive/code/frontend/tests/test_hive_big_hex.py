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


def test_click_expands_the_big_hexagon_with_task_lanes_then_a_folded_recent_line(browser, base) -> None:  # noqa: F811  十五·1 5（取代十二·1 的任务组）
    with open_stage(browser, base, setup=_setup) as st:
        _open(st)
        cell = st.page.locator(EXPANDED)
        assert cell.count() == 1 and cell.get_attribute("data-project-id") == "p_math"
        assert cell.locator(".hex-title").inner_text() == "数学"
        assert cell.locator(".hex-ruler-slot .pr-row").count() == 2 and cell.locator(".hex-ruler-slot .pr-ruler").count() == 1
        # 没有「任务」组、「全部待办」、「＋」；任务只以泳道行出现
        assert cell.locator(".hex-group-title, .hex-add, .hex-all, .hex-next-list:not(:empty)").count() == 0
        assert cell.locator(".pr-row b").all_inner_texts() == ["数学的任务", "未分配任务用时"]
        # 从上到下：项目名 < 任务泳道 < 「＋ 新建任务」 < 「最近完成（1）」
        ys = [cell.locator(s).first.bounding_box()["y"] for s in (".hex-title", ".hex-ruler-slot .pr-row", ".pr-newslot", ".pr-donehead")]
        assert ys == sorted(ys) and len(set(ys)) == 4
        # 最近完成默认收起：只有一行标题，没有生成里面的行；点开才有（十五·5）
        head = cell.locator(".pr-donehead")
        assert head.inner_text() == "最近完成（1）" and cell.locator(".is-done-card").count() == 0
        head.click()
        # 最近完成：<任务名> 完成 · 人花了 X 分钟 · agent 花了 Y 分钟
        line = cell.locator(".is-done-card").first
        line.locator(".hex-spent:not(:empty)").wait_for()
        text = re.sub(r"\s+", " ", line.inner_text()).replace("撤销", "").strip()
        assert text == "数学的任务 完成 · 人花了 3 分钟 · agent 花了 12 分钟", text
        if SHOT:
            st.page.screenshot(path=f"{SHOT}/bighex.png")
        head.click()
        assert cell.locator(".is-done-card").count() == 0 and head.get_attribute("aria-expanded") == "false"     # 再点收起


def test_recent_line_says_under_a_minute_and_zero(browser, base) -> None:  # noqa: F811  十二·3
    def setup(st: Stage) -> None:
        _busy_day(st)
        _seed(st, human_s=20, agent_s=0)
    with open_stage(browser, base, setup=setup) as st:
        _open(st)
        st.page.locator(EXPANDED + " .pr-donehead").click()
        line = st.page.locator(EXPANDED + " .is-done-card .hex-spent:not(:empty)")
        line.wait_for()
        assert line.inner_text() == "完成 · 人花了 不到 1 分钟 · agent 花了 0 分钟"


def test_click_on_a_done_line_still_only_offers_undo(browser, base) -> None:  # noqa: F811  十二·3 十五·5
    with open_stage(browser, base, setup=_setup) as st:
        _open(st)
        st.page.locator(EXPANDED + " .pr-donehead").click()
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


ONE_CLOCK = """(how) => {
  const q = s => document.querySelector(s), cell = q('.hex-cell[data-project-id="p_math"]');
  const other = [...document.querySelectorAll('.hex-cell[data-project-id]')].filter(c => c !== cell).pop();
  const c = e => { const r = e.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2, r.width].map(Math.round); };
  const rects = () => [cell, other, q('.hex-borders path'), q('.st-pet.is-work[data-run-id="r1"]')].map(c);
  const before = rects();
  if (how === 'open') cell.click(); else document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
  const a = document.getAnimations().filter(x => x.constructor === Animation && x.effect && x.effect.target.closest('.hive, .st-pet'));   // 只看脚本起的位移动画，不算颜色淡入淡出的 CSS 过渡
  a.forEach(x => { x.pause(); x.currentTime = 0; });
  const at0 = rects();
  a.forEach(x => { x.currentTime = 230; });
  const mid = rects();
  a.forEach(x => x.finish());
  return { before, at0, mid, end: rects(), cells: a.filter(x => x.effect.target.classList.contains('hex-cell')).length, durations: [...new Set(a.map(x => x.effect.getTiming().duration))], who: [...new Set(a.map(x => x.effect.target.classList[0]))] };
}"""


def test_expand_and_collapse_run_cells_camera_borders_and_pets_on_one_timeline(browser, base) -> None:  # noqa: F811  十八·1 2 3
    with open_stage(browser, base, setup=_setup) as st:
        st.page.mouse.move(2, 2)
        for how in ("open", "close"):
            r = st.page.evaluate(ONE_CLOCK, how)
            assert r["durations"] == [460], r                                        # 同一个时长
            assert {"hex-cell", "hive-cam", "st-pet"} <= set(r["who"]) and "hex-borders" not in r["who"], r["who"]   # 分区线没有自己的动画：跟着镜头走
            assert r["cells"] == 1, r["cells"]                                       # 晶格上的格子也只跟镜头走，单独动的只有点开 / 收起的那一格
            for was, now in zip(r["before"], r["at0"]):                              # 起点 = 动之前屏幕上的样子
                assert all(abs(x - y) <= 2 for x, y in zip(was, now)), r
            assert r["end"][0] != r["before"][0] and r["end"][3] != r["before"][3], r   # 格子和小人都真的动了
            for was, mid, end in zip(r["before"][:3], r["mid"][:3], r["end"][:3]):         # 中途在起点和终点之间：路线是直的，不先甩出去再回来
                assert all(min(x, z) - 2 <= y <= max(x, z) + 2 for x, y, z in zip(was, mid, end)), r
            st.page.clock.run_for(1000)
            st.page.wait_for_timeout(300)


INTERRUPT = """() => {
  const q = s => document.querySelector(s), cell = q('.hex-cell[data-project-id="p_math"]');
  const other = [...document.querySelectorAll('.hex-cell[data-project-id]')].filter(c => c !== cell).pop();
  const c = e => { const r = e.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2, r.width].map(Math.round); };
  const rects = () => [cell, other, q('.hex-borders path')].map(c);
  const moving = () => document.getAnimations().filter(x => x.constructor === Animation && x.effect && x.effect.target.closest('.hive'));
  const esc = () => document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
  cell.click(); moving().forEach(x => x.finish());
  const opened = rects();
  esc(); moving().forEach(x => x.finish());
  cell.click();                                  // 展开的动画刚起步……
  esc();                                         // ……就收起
  const a = moving(); a.forEach(x => { x.pause(); x.currentTime = 0; });
  const at0 = rects();
  a.forEach(x => x.finish());
  return { opened, at0 };
}"""


def test_collapse_that_interrupts_an_expand_starts_from_the_expanded_layout_not_from_the_corner(browser, base) -> None:  # noqa: F811  十八·1
    with open_stage(browser, base, setup=_setup) as st:
        st.page.mouse.move(2, 2)
        r = st.page.evaluate(INTERRUPT)
        for was, now in zip(r["opened"], r["at0"]):                                  # 各物都从上一段的终点起步，没有谁对不上
            assert all(abs(x - y) <= 2 for x, y in zip(was, now)), r


def test_hover_waits_for_the_collapse_animation_instead_of_cutting_it(browser, base) -> None:  # noqa: F811  十八·1
    with open_stage(browser, base, setup=_setup) as st:
        _open(st)
        st.page.clock.run_for(1000)
        st.page.wait_for_timeout(600)
        st.page.mouse.move(3, 3)
        st.page.keyboard.press("Escape")
        over = "e => e.dispatchEvent(new MouseEvent('mouseover', { bubbles: true }))"   # 收起的动画里鼠标扫到一格
        st.page.locator(CELL.format("p_math")).evaluate(over)
        st.page.clock.run_for(300)
        assert st.page.locator(".hex-cell.is-hover").count() == 0                    # 收起还没走完：不长大
        st.page.clock.run_for(600)
        st.page.wait_for_timeout(300)
        assert st.page.locator(CELL.format("p_math") + ".is-hover").count() == 1     # 走完了：照常长大
