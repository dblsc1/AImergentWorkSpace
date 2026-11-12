"""项目格悬停档（hive.stage.v1「二十五」）：标尺 + 泳道、正在进行的任务、本周 / 本日用时；取代原来的最近三条待办卡片。

接口桩与假时钟沿用 test_hive_stage.py；``views/project-time`` 在这里单独桩并记请求。
"""

from __future__ import annotations

from datetime import timedelta
import json
import os
from typing import Any

from test_hive_focus import base, browser  # noqa: F401  (fixtures)
from test_hive_stage import CELL, TZ, Stage, _busy_day, _iso, open_stage

SHOT = "/tmp/claude-1000/-srv-aimergent-AImergentWorkspace/baa97cf5-622e-4d17-bbdf-495da4f51f9e/scratchpad/shots/hovercard.png"


def _seed(st: Stage) -> None:
    """p_math：人今天在 t_p_math 上计时到此刻（30 分钟），代理 1 小时前干过 20 分钟。"""
    now = st.now().astimezone(TZ)
    tasks = [{"taskId": "t_p_math",
              "human": [{"start": _iso(now - timedelta(minutes=30)), "end": _iso(now)}],
              "agent": [{"start": _iso(now - timedelta(minutes=80)), "end": _iso(now - timedelta(minutes=60)), "agent": "garden", "runId": "r1"}]}]
    st.page.route("**/api/core/views/project-time*", lambda r: r.fulfill(
        status=200, content_type="application/json", body=json.dumps({"projectId": "p_math", "now": _iso(now), "tasks": tasks})))
    st.reads = []
    st.page.on("request", lambda r: st.reads.append(r.url) if "views/project-time" in r.url else None)


def _setup(st: Stage) -> None:
    _busy_day(st)
    _seed(st)


def _hover(st: Stage, selector: str) -> None:
    st.page.mouse.move(3, 3)
    st.page.clock.run_for(400)
    st.page.wait_for_timeout(500)                       # 缩回的动画走真时间，没走完不接新的悬停（「十八」1）
    st.page.mouse.move(*st.center(selector))
    st.page.clock.run_for(400)
    st.page.wait_for_timeout(300)
    st.page.clock.run_for(400)


def test_hovering_a_project_cell_shows_ruler_lane_current_task_and_week_day_totals(browser, base) -> None:  # noqa: F811  二十五·1 2 3
    with open_stage(browser, base, setup=_setup) as st:
        _hover(st, CELL.format("p_math"))
        cell = st.page.locator(CELL.format("p_math") + ".is-hover")
        assert cell.count() == 1
        st.page.wait_for_selector(".is-hover .hv-card .hv-sum")
        card = cell.locator(".hv-card")
        assert card.locator(".pr-ruler").count() == 1 and card.locator(".pr-lanes .pr-human .pr-seg").count() == 1
        assert card.locator(".pr-lanes .pr-agent .pr-seg").count() == 1 and card.locator(".pr-now").count() == 2
        assert card.locator(".hv-task").inner_text() == "数学的任务"
        sums = card.locator(".hv-sum").all_inner_texts()
        # 午夜前后跑也成立：本日按本地 0 点裁剪；本周按周一 0 点（周一凌晨也只会更少），这里只验本日与本周的分钟数
        now = st.now().astimezone(TZ)
        since = int((now - now.replace(hour=0, minute=0, second=0, microsecond=0)).total_seconds() // 60)
        day_h, day_a = min(30, since), max(0, min(20, since - 60))
        assert len(sums) == 2 and "本周" in sums[0] and "本日" in sums[1]
        assert sums[1].split("\n")[1:] == [f"{day_h} 分", f"{day_a} 分"], sums
        assert cell.locator(".hex-todo-card").count() == 0           # 取代原来的三条待办卡片
        assert cell.locator(".hex-title").inner_text() == "数学"
        os.makedirs(os.path.dirname(SHOT), exist_ok=True)
        b = st.box(CELL.format("p_math") + ".is-hover")
        st.page.screenshot(path=SHOT, clip={"x": b["x"] - 40, "y": b["y"] - 40, "width": b["width"] + 80, "height": b["height"] + 80})


def test_hover_card_stays_inside_the_hexagon_width(browser, base) -> None:  # noqa: F811  二十五·5
    with open_stage(browser, base, setup=_setup) as st:
        _hover(st, CELL.format("p_math"))
        st.page.wait_for_selector(".is-hover .hv-card .hv-sum")
        bad = st.page.evaluate("""() => {
          const cell = document.querySelector('.hex-cell.is-hover'), c = cell.getBoundingClientRect();
          const hw = c.width / 2, cx = c.left + hw, cy = c.top + c.height / 2, hh = c.height / 2;
          // 六边形（尖顶）：|dx| <= hw 且 |dy| <= hh - |dx| * (hh/2)/hw
          return [...cell.querySelectorAll('.hex-title, .hv-ruler, .pr-ruler, .pr-lanes, .hv-task, .hv-sum')].filter(e => {
            const r = e.getBoundingClientRect();
            return [[r.left, r.top], [r.right, r.top], [r.left, r.bottom], [r.right, r.bottom]].some(([x, y]) =>
              Math.abs(x - cx) > hw + 0.5 || Math.abs(y - cy) > hh - Math.abs(x - cx) * (hh / 2) / hw + 0.5);
          }).map(e => e.className + ':' + e.textContent.slice(0, 12));
        }""")
        assert bad == [], bad


def test_reads_only_when_the_cell_has_grown_and_not_again_within_30_seconds(browser, base) -> None:  # noqa: F811  二十五·4
    with open_stage(browser, base, setup=_setup) as st:
        st.page.mouse.move(3, 3)
        st.page.mouse.move(*st.center(CELL.format("p_math")))
        st.page.clock.run_for(30)                          # 还没长出来：不读
        st.page.wait_for_timeout(150)
        assert st.page.locator(".hex-cell.is-hover").count() == 0 and st.reads == []
        st.page.mouse.move(*st.center(CELL.format("p_piano")))   # 扫到别的格又走开
        st.page.mouse.move(3, 3)
        st.page.clock.run_for(600)
        assert st.reads == []
        st.page.wait_for_timeout(300)
        _hover(st, CELL.format("p_math"))
        st.page.wait_for_selector(".is-hover .hv-card .hv-sum")
        assert len(st.reads) == 2 and "projectId=p_math" in st.reads[0]   # 一次读 = 两个请求（后 7 天 + 最早那一天，「二十八」6）
        st.page.mouse.move(3, 3)
        st.page.clock.run_for(700)
        _hover(st, CELL.format("p_math"))                  # 30 秒内再来：直接用上次的
        st.page.wait_for_selector(".is-hover .hv-card .hv-sum")
        assert len(st.reads) == 2
        st.page.mouse.move(3, 3)
        st.page.clock.run_for(31000)
        _hover(st, CELL.format("p_math"))
        st.page.wait_for_selector(".is-hover .hv-card .hv-sum")
        st.page.wait_for_timeout(200)
        assert len(st.reads) == 4


def test_skeleton_ruler_is_drawn_before_the_data_arrives(browser, base) -> None:  # noqa: F811  二十五·2
    with open_stage(browser, base, setup=_setup) as st:
        st.page.unroute("**/api/core/views/project-time*")
        st.page.route("**/api/core/views/project-time*", lambda r: None)       # 一直不回
        _hover(st, CELL.format("p_math"))
        card = st.page.locator(".is-hover .hv-card")
        assert card.locator(".pr-ruler").count() == 1 and card.locator(".pr-lanes").count() == 1
        assert card.locator(".hv-sum, .hv-task").count() == 0


def test_center_and_placeholder_and_expanded_cells_get_no_card(browser, base) -> None:  # noqa: F811  二十五·6
    with open_stage(browser, base, setup=_setup) as st:
        st.page.mouse.move(3, 3)
        st.page.locator(".hex-cell.hex-center").evaluate("e => e.dispatchEvent(new MouseEvent('mouseover', { bubbles: true }))")   # 中心格在舞台页不接指针
        st.page.clock.run_for(600)
        st.page.wait_for_timeout(300)
        assert st.page.locator(".hex-cell.hex-center.is-hover").count() == 1
        assert st.page.locator(".hv-card .pr-ruler").count() == 0 and st.reads == []
        st.page.click(CELL.format("p_math"))
        st.page.wait_for_timeout(300)
        st.page.clock.run_for(1500)
        st.page.wait_for_timeout(500)
        assert st.page.locator(".hex-cell.is-expanded").count() == 1
        assert st.page.locator(".hex-cell.is-expanded.is-hover").count() == 0
        assert st.page.locator(".hex-cell.is-expanded .hv-card").evaluate("e => getComputedStyle(e).display") == "none"


def test_pointer_landing_on_a_pet_resting_inside_the_cell_still_grows_the_cell(browser, base) -> None:  # noqa: F811  二十五·1 + 二十二·2
    with open_stage(browser, base, setup=_setup) as st:
        st.page.wait_for_selector(".st-pet.is-in.is-work", state="attached")
        pid = st.page.get_attribute(".st-pet.is-in.is-work", "data-cell")
        box = st.page.evaluate("() => { var r = document.querySelector('.st-pet.is-in.is-work .st-pet-in').getBoundingClientRect(); return {x: r.x, y: r.y, width: r.width, height: r.height} }")
        assert box["width"] > 0
        st.page.mouse.move(3, 3)
        st.page.clock.run_for(400)
        st.page.wait_for_timeout(500)
        st.page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2, steps=5)   # 指针正落在小人身上
        st.page.clock.run_for(400)
        st.page.wait_for_timeout(300)
        st.page.clock.run_for(400)
        st.page.wait_for_selector(CELL.format(pid) + ".is-hover")
