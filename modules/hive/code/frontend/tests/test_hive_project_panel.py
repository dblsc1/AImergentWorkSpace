"""项目面板：任务条 + 人 / 代理时间标尺，与拖动时的弹簧展开（hive.stage.v1「七」）。

接口桩与假时钟沿用 test_hive_stage.py 的夹具；``views/project-time`` 在这里按用例单独桩。
"""

from __future__ import annotations

from datetime import timedelta
import json
import os
import re
from typing import Any

from test_hive_focus import base, browser  # noqa: F401  (fixtures)
from test_hive_stage import CELL, TZ, Stage, _busy_day, _iso, open_stage

ROW = '.pr-row[data-task="t_p_math"]'


def _seed(st: Stage) -> None:
    """p_math 的任务 t_p_math 上有：人 2 段（一段今天、一段 2 天前的 1 分钟）、代理 2 段；没挂任务上有代理 1 段。"""
    now = st.now().astimezone(TZ)
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)

    def span(a, b, **extra: Any) -> dict[str, Any]:
        return {"start": _iso(a), "end": _iso(b), **extra}

    tasks = [
        {"taskId": None, "human": [], "agent": [span(day - timedelta(days=1) + timedelta(hours=14), day - timedelta(days=1) + timedelta(hours=16), agent="codex", runId="r9")]},
        {"taskId": "t_p_math",
         "human": [span(day - timedelta(days=2) + timedelta(hours=10), day - timedelta(days=2) + timedelta(hours=10, minutes=1)),
                   span(day - timedelta(days=4) + timedelta(hours=9), day - timedelta(days=4) + timedelta(hours=12)),
                   span(day + timedelta(hours=9), day + timedelta(hours=10, minutes=30))],
         "agent": [span(day - timedelta(days=3) + timedelta(hours=20), day - timedelta(days=3) + timedelta(hours=23), agent="garden", runId="r1"),
                   span(day + timedelta(hours=9, minutes=10), day + timedelta(hours=11), agent="garden", runId="r1")]},
    ]
    st.page.route("**/api/core/views/project-time*", lambda r: r.fulfill(
        status=200, content_type="application/json",
        body=json.dumps({"projectId": "p_math", "tasks": tasks}, ensure_ascii=False)))
    st.project_time_requests = []
    st.page.on("request", lambda r: st.project_time_requests.append(r.url) if "views/project-time" in r.url else None)


def _open(st: Stage, pid: str = "p_math") -> None:
    st.page.click(CELL.format(pid))
    st.page.wait_for_selector(".pr-row")
    st.page.wait_for_selector(ROW + " .pr-seg")


def _box(loc) -> dict[str, float]:
    b = loc.first.bounding_box()
    assert b
    return b


def test_rows_ruler_70_30_and_lanes_top_human_bottom_agent(browser, base) -> None:  # noqa: F811
    with open_stage(browser, base, setup=_busy_day) as st:
        _seed(st)
        _open(st)
        panel = st.page.locator("#stPanel")
        assert panel.locator(".pr-row").count() == 2 and panel.locator(".pr-row b").all_inner_texts() == ["数学的任务", "没挂任务"]
        assert "projectId=p_math" in st.project_time_requests[0] and re.search(r"from=\d{4}-\d\d-\d\d&to=", st.project_time_requests[0])
        # 标尺：一条，今天占 30%，前 6 天共 70%；每行的泳道与它对齐
        ruler = _box(panel.locator(".pr-ruler"))
        today = _box(panel.locator(".pr-day.is-today"))
        assert panel.locator(".pr-day").count() == 7 and panel.locator(".pr-day.is-today").inner_text() == "今天"
        assert abs(today["width"] / ruler["width"] - 0.3) < 0.003
        assert abs((today["x"] - ruler["x"]) / ruler["width"] - 0.7) < 0.003
        for lanes in panel.locator(".pr-lanes").all():
            b = lanes.bounding_box()
            assert abs(b["x"] - ruler["x"]) < 1 and abs(b["width"] - ruler["width"]) < 1
        # 左半任务、右半泳道；泳道上半人、下半代理
        task, lanes = _box(panel.locator(ROW + " .pr-task")), _box(panel.locator(ROW + " .pr-lanes"))
        assert task["x"] + task["width"] <= lanes["x"] + 1
        human, agent = _box(panel.locator(ROW + " .pr-human")), _box(panel.locator(ROW + " .pr-agent"))
        assert human["y"] + human["height"] <= agent["y"] + 1 and abs(human["x"] - agent["x"]) < 1
        assert panel.locator(ROW + " .pr-human .pr-seg").count() == 3 and panel.locator(ROW + " .pr-agent .pr-seg").count() == 2
        assert panel.locator('.pr-row[data-task=""] .pr-agent .pr-seg').count() == 1
        # 段：过去的 1 分钟也至少 1px；颜色 = 人红 / 项目（分区）色；提示
        tiny = panel.locator(ROW + " .pr-human .pr-seg").nth(0)
        assert _box(tiny)["width"] >= 0.99
        colors = st.page.evaluate("""() => [getComputedStyle(document.querySelector('.pr-human .pr-seg')).backgroundColor,
                                            getComputedStyle(document.querySelector('.pr-agent .pr-seg')).backgroundColor,
                                            getComputedStyle(document.documentElement).getPropertyValue('--pr-human').trim()]""")
        assert colors[1] == "rgb(53, 201, 235)" and colors[0] != colors[1] and colors[2].startswith("#")
        assert re.fullmatch(r"\d\d/\d\d \d\d:\d\d–\d\d:\d\d 你", panel.locator(ROW + " .pr-human .pr-seg").nth(2).get_attribute("title"))
        assert re.fullmatch(r"\d\d/\d\d \d\d:\d\d–\d\d:\d\d garden", panel.locator(ROW + " .pr-agent .pr-seg").nth(1).get_attribute("title"))
        # 标尺里有今天的钟点刻度
        assert panel.locator(".pr-tick").all_inner_texts() == ["00", "06", "12", "18", "24"]
        panel.locator(".st-x").click()
        assert not st.page.locator("#stVeil").is_visible()


def test_panel_opens_in_both_themes(browser, base) -> None:  # noqa: F811
    """设了 HIVE_SHOTS=<目录> 时顺手存两张图（看效果用）。"""
    shots = os.environ.get("HIVE_SHOTS")
    for theme in ("light", "dark"):
        with open_stage(browser, base, width=1440, height=900, setup=_busy_day) as st:
            _seed(st)
            st.page.evaluate("(t) => document.documentElement.dataset.theme = t", theme)
            _open(st)
            if shots:
                st.page.screenshot(path=f"{shots}/project-panel-{theme}.png")


def _grab_and_hold(st: Stage, selector: str, over: tuple[float, float]) -> None:
    """按住元素拖到 over 上、停 1 秒（假时钟走 1 秒），不松手。"""
    x, y = st.center(selector)
    m = st.page.mouse
    m.move(x, y)
    m.down()
    m.move(x + 8, y + 8, steps=2)
    m.move(over[0], over[1], steps=6)


def _strip(st: Stage) -> None:
    t0 = st.now().astimezone(TZ)
    st.unplaced = [{"id": "u1", "startAt": _iso(t0), "endAt": _iso(t0 + timedelta(minutes=12)), "durationSeconds": 720}]
    st.poll()


def test_drag_over_cell_darkens_it_then_hold_one_second_opens_the_panel(browser, base) -> None:  # noqa: F811
    with open_stage(browser, base, setup=_busy_day) as st:
        _seed(st)
        _strip(st)
        brightness = lambda: st.page.evaluate("(s) => getComputedStyle(document.querySelector(s)).filter", CELL.format("p_math"))  # noqa: E731
        assert brightness() == "none"
        _grab_and_hold(st, ".st-strip", st.center(CELL.format("p_math")))
        assert "brightness(0.78)" in brightness()                 # 变深
        st.page.clock.run_for(900)
        assert not st.page.locator("#stVeil").is_visible()         # 还没满 1 秒
        st.page.clock.run_for(150)
        assert st.page.locator("#stVeil").is_visible() and st.page.locator("#stPanel h2").inner_text() == "数学"
        # 面板开着时拖到任务条上：条变深（is-drop），松手 = 记到这个任务
        st.page.mouse.move(*st.center(ROW), steps=6)
        assert "is-drop" in st.page.locator(ROW).get_attribute("class")
        st.page.mouse.up()
        st.settle()
        assert st.posts == [("timer/unplaced/u1/place", {"taskId": "t_p_math"})]


def test_moving_before_one_second_restarts_the_hold(browser, base) -> None:  # noqa: F811
    with open_stage(browser, base, setup=_busy_day) as st:
        _seed(st)
        _strip(st)
        _grab_and_hold(st, ".st-strip", st.center(CELL.format("p_math")))
        st.page.clock.run_for(700)
        st.page.mouse.move(*st.center(CELL.format("p_piano")), steps=6)    # 换到别的格子：重新数
        st.page.clock.run_for(700)
        assert not st.page.locator("#stVeil").is_visible()
        st.page.clock.run_for(400)
        assert st.page.locator("#stPanel h2").inner_text() == "钢琴"
        st.page.mouse.up()
        st.settle()


def test_agent_pet_dropped_on_task_row_places_to_the_task(browser, base) -> None:  # noqa: F811
    with open_stage(browser, base, setup=_busy_day) as st:
        _seed(st)
        st.replies["agents/r2/place"] = (200, {"rule": None})
        _grab_and_hold(st, '.st-pet.is-loose[data-run-id="r2"]', st.center(CELL.format("p_math")))
        st.page.clock.run_for(1100)
        assert st.page.locator("#stVeil").is_visible()
        st.page.mouse.move(*st.center(ROW), steps=6)
        st.page.mouse.up()
        st.settle()
        assert st.posts == []                                              # 宠物先过确认卡
        card = st.page.locator(".st-confirm")
        assert "挂到 数学的任务" in card.locator(".st-cf-title").inner_text()
        assert card.locator(".st-cf-rem span").inner_text().endswith("都算 数学的任务")
        assert card.locator(".st-cf-btns button").all_inner_texts() == ["之前的时间也算过去", "只算以后的时间"]
        assert st.page.evaluate("() => { const r = document.querySelector('.st-confirm').getBoundingClientRect(); const e = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2); return !!e.closest('.st-confirm'); }")
        card.locator(".st-cf-box").check()
        card.locator('button[data-from="now"]').click()
        st.settle()
        assert st.posts == [("agents/r2/place", {"taskId": "t_p_math", "remember": True, "timeFrom": "now"})]


def test_drop_on_the_no_task_row_files_to_the_project(browser, base) -> None:  # noqa: F811
    with open_stage(browser, base, setup=_busy_day) as st:
        _seed(st)
        _strip(st)
        _grab_and_hold(st, ".st-strip", st.center(CELL.format("p_math")))
        st.page.clock.run_for(1100)
        st.page.mouse.move(*st.center('.pr-row[data-task=""]'), steps=6)
        st.page.mouse.up()
        st.settle()
        assert st.posts == [("timer/unplaced/u1/place", {"projectId": "p_math"})]


def test_hold_one_second_on_blank_of_task_list_creates_a_task_then_files_into_it(browser, base) -> None:  # noqa: F811
    with open_stage(browser, base, setup=_busy_day) as st:
        _seed(st)
        _strip(st)
        st.replies["planner/tasks"] = (201, {"id": "t_new", "name": "新任务"})
        _grab_and_hold(st, ".st-strip", st.center(CELL.format("p_math")))
        st.page.clock.run_for(1100)
        last = _box(st.page.locator(".pr-row").last)
        blank = (last["x"] + last["width"] / 2, last["y"] + last["height"] + 20)
        st.page.mouse.move(*blank, steps=6)
        st.page.clock.run_for(900)
        assert st.page.locator(".pr-new-name").count() == 0
        st.page.clock.run_for(200)
        assert st.page.locator(".pr-new-name").count() == 1
        st.page.mouse.up()
        st.page.locator(".pr-new-name").fill("新任务")
        st.page.keyboard.press("Enter")
        st.settle()
        assert st.posts == [("planner/tasks", {"projectId": "p_math", "name": "新任务"}), ("timer/unplaced/u1/place", {"taskId": "t_new"})]
        assert st.page.locator(".pr-new-name").count() == 0


def test_blank_hold_input_is_dropped_when_released_elsewhere(browser, base) -> None:  # noqa: F811
    with open_stage(browser, base, setup=_busy_day) as st:
        _seed(st)
        _strip(st)
        _grab_and_hold(st, ".st-strip", st.center(CELL.format("p_math")))
        st.page.clock.run_for(1100)
        last = _box(st.page.locator(".pr-row").last)
        st.page.mouse.move(last["x"] + 50, last["y"] + last["height"] + 20, steps=6)
        st.page.clock.run_for(1100)
        assert st.page.locator(".pr-new-name").count() == 1
        st.page.mouse.move(*st.center(".pr-head"), steps=6)               # 移到标题栏上松手：不新建
        st.page.mouse.up()
        st.settle()
        assert st.page.locator(".pr-new-name").count() == 0 and st.posts == []
