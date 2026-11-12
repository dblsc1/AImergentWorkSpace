"""右上角「人」的页面（hive.stage.v1「十四」）：左边立绘 + 纵置三张卡；去掉「计时档案」。

接口在浏览器侧桩掉（st.blocks / st.records_by_block 给窗口块与原始记录），时间用假时钟、拖动用真鼠标。
"""

from __future__ import annotations

from datetime import timedelta
import json
import os
from typing import Any

from test_hive_stage import CELL, TZ, Stage, _iso, base, browser, open_stage  # noqa: F401

SHOT = os.environ.get("HUMANPAGE_SHOT", "")
MIN = timedelta(minutes=1)


def _session(now, a: int, b: int, task: str, project: str, source: str = "timer-backend") -> dict[str, Any]:
    return {"startAt": _iso(now - a * MIN), "endAt": _iso(now - b * MIN), "durationSeconds": (a - b) * 60, "taskId": task,
            "projectId": project, "mode": "do", "source": source}


def _block(now, key: str, last_min: int, *, project: str | None = None, seconds: int = 600) -> dict[str, Any]:
    parts = [{"fromTs": None, "toTs": None, "taskId": None, "projectId": project, "seconds": seconds}] if project else []
    return {"blockKey": key, "apps": [key.split(" · ")[0]], "status": "assigned" if project else "pending", "seconds": seconds, "seq": 1,
            "lastAt": _iso(now - last_min * MIN), "parts": parts}


def _rec(now, a: int, b: int, app: str, title: str) -> dict[str, Any]:
    return {"startAt": _iso(now - a * MIN), "endAt": _iso(now - b * MIN), "seconds": (a - b) * 60, "app": app, "title": title}


def _day(st: Stage) -> None:
    now = st.now().astimezone(TZ)
    st.set_lanes(
        sessions=[_session(now, 150, 120, "t_p_math", "p_math"),                                 # 手动计时 30 分
                  _session(now, 100, 80, "t_p_piano", "p_piano", "activity-block"),             # 按窗口块记下的 20 分
                  _session(now, 3 * 24 * 60 + 60, 3 * 24 * 60, "t_p_math", "p_math")],          # 三天前：只在「周」里
        running={"startAt": _iso(now - 10 * MIN), "taskId": "t_p_studio", "projectId": "p_studio"})
    keys = [("code · main.go", 5, "p_math"), ("firefox · 文档", 12, "p_piano"), ("chrome · 窗口名3", 20, None), ("kitty", 30, None),
            ("slack · 群聊", 40, "p_studio"), ("code · notes.md", 50, "p_math"), ("zoom · 周会", 60, None), ("term · vim", 70, None)]
    st.blocks = [_block(now, k, last, project=p, seconds=600 - i * 10) for i, (k, last, p) in enumerate(keys)]
    st.records_by_block = {k: [_rec(now, last + 8 - i // 2, last, k.split(" · ")[0], k.split(" · ")[1] if " · " in k else "")] for i, (k, last, p) in enumerate(keys)}


def _old_block(st: Stage) -> None:
    """三天前的一块（有自己的原始记录）：只在默认视图 / 周里看得到。"""
    now = st.now().astimezone(TZ)
    st.blocks.append(_block(now, "code · old.go", 3 * 24 * 60 + 30, project="p_math", seconds=900))
    st.records_by_block["code · old.go"] = [_rec(now, 3 * 24 * 60 + 40, 3 * 24 * 60 + 30, "code", "old.go")]


def _open(st: Stage) -> None:
    st.page.click("#stClock")
    st.page.wait_for_selector(".hp-card")
    st.page.wait_for_function("() => document.querySelectorAll('.cm-panel .hp-win').length >= 1")
    st.page.clock.run_for(100)


def test_three_cards_in_order_default_is_the_seven_day_uneven_ruler_and_esc_closes(browser, base) -> None:  # noqa: F811  十六·2
    with open_stage(browser, base, width=1600, setup=_day) as st:
        _open(st)
        assert st.page.locator(".hp-card h3").all_inner_texts() == ["用时", "逐任务用时", "窗口记录"]
        assert st.page.locator('.hp-range-btn[aria-pressed="true"]').count() == 0          # 日 / 周 / 最近 x 小时都不选中
        assert st.page.locator(".hp-hours").count() == 0
        assert st.page.locator('.hp-range-btn[data-range="hours"]').inner_text() == "最近 3 小时"
        assert st.page.locator(".hp-total").inner_text() == "2 小时 0 分钟"        # 7 天内：30 + 20 + 三天前 60 + 正在跑的 10
        days = st.page.locator(".hp-ruler .pr-day")
        assert days.count() == 7 and days.last.inner_text() == "今天"                # 周几标注 + 今天
        widths = days.evaluate_all("els => els.map(e => e.getBoundingClientRect().width)")
        assert max(widths) == widths[-1] and abs(widths[-1] / widths[0] - 0.3 / (0.7 / 6)) < 0.05 and abs(widths[0] - widths[5]) < 1   # 今天 30%，前 6 天各 70%/6
        assert st.page.locator(".hp-ruler .pr-tick").count() == 5                    # 今天的钟点刻度
        if SHOT:
            st.page.screenshot(path=SHOT)
        st.page.keyboard.press("Escape")
        assert st.page.locator(".cm-panel").count() == 0
        assert st.posts == []


def test_range_buttons_roll_back_and_pressing_a_selected_one_again_returns_to_the_default(browser, base) -> None:  # noqa: F811  十六·2
    with open_stage(browser, base, width=1600, setup=_day) as st:
        _open(st)
        st.page.click('.hp-range-btn[data-range="week"]')
        st.page.wait_for_function("() => document.querySelector('.hp-range-btn[data-range=\"week\"]').getAttribute('aria-pressed') === 'true'")
        assert st.page.locator(".hp-total").inner_text() == "2 小时 0 分钟" and st.page.locator(".hp-ruler").count() == 0   # 周：滚动 7 天，等宽
        assert st.page.locator(".hp-hours").count() == 0
        st.page.click('.hp-range-btn[data-range="day"]')
        assert st.page.locator('.hp-range-btn[data-range="day"]').get_attribute("aria-pressed") == "true"
        assert st.page.locator(".hp-total").inner_text() == "1 小时 0 分钟"        # 三天前那 1 小时出了范围
        st.page.click('.hp-range-btn[data-range="day"]')                              # 再点一次 = 回默认视图
        assert st.page.locator('.hp-range-btn[aria-pressed="true"]').count() == 0 and st.page.locator(".hp-ruler .pr-day").count() == 7
        assert st.page.locator(".hp-total").inner_text() == "2 小时 0 分钟"
        st.page.click('.hp-range-btn[data-range="hours"]')
        assert st.page.locator(".hp-range-hours").get_attribute("aria-pressed") == "true"
        inp = st.page.locator(".hp-hours")
        inp.fill("1")
        inp.press("Enter")
        assert st.page.locator(".hp-hours").input_value() == "1"
        assert st.page.locator(".hp-total").inner_text() == "10 分钟"              # 最近 1 小时：两段已记的都出了范围，只剩正在跑的 10 分钟
        inp = st.page.locator(".hp-hours")
        inp.fill("99")
        inp.press("Enter")
        assert st.page.locator(".hp-hours").input_value() == "1"                  # 不合法：退回
        st.page.locator(".hp-range-hours span").first.click()                         # 再点选中的「最近 x 小时」= 回默认
        assert st.page.locator('.hp-range-btn[aria-pressed="true"]').count() == 0 and st.page.locator(".hp-ruler .pr-day").count() == 7
        assert st.posts == []


def test_lane_tooltips_name_where_the_focus_was_and_timer_sits_on_top(browser, base) -> None:  # noqa: F811  十四·5 B
    with open_stage(browser, base, width=1600, setup=_day) as st:
        _open(st)
        assert st.page.locator(".hp-lane-timer .hp-seg").count() == 3              # 手动计时 30 分 + 三天前那段 + 正在跑的
        titles = st.page.locator(".hp-lane-win .hp-seg").evaluate_all("els => els.map(e => e.title)")
        assert any(t.endswith("数学 / code · main.go") for t in titles), titles
        assert any(t.endswith("chrome · 标题已隐去") for t in titles) and not any("窗口名3" in t for t in titles)
        assert any(t.endswith(" kitty") for t in titles)                            # 没归类：只有窗口名
        assert all(len(t.split(" ")[1]) == 11 and t[2] == "/" and t[8] == ":" and t[11] == "–" for t in titles)   # MM/DD HH:MM–HH:MM（默认视图跨天，带日期）
        filed = st.page.locator(".hp-lane-win .hp-seg.is-filed").count()
        assert 0 < filed < st.page.locator(".hp-lane-win .hp-seg").count()          # 已归类的有项目色，没归类的是中性色


def test_per_task_cards_running_one_first_then_most_recent_and_click_opens_the_project(browser, base) -> None:  # noqa: F811  十四·6
    with open_stage(browser, base, width=1600, setup=_day) as st:
        _open(st)
        names = st.page.locator(".hp-task b").all_inner_texts()
        assert names == ["录音棚的任务", "钢琴的任务", "数学的任务"], names          # 在跑的置顶；钢琴比数学更近
        assert "is-running" in st.page.locator(".hp-task").first.get_attribute("class")
        assert st.page.locator(".hp-task .hp-dur").all_inner_texts() == ["10 分钟", "20 分钟", "1 小时 30 分钟"]   # 默认 7 天：数学多了三天前那 1 小时
        st.page.locator(".hp-task", has_text="钢琴").click()
        assert st.page.locator(".cm-panel").count() == 0
        st.page.clock.run_for(900)
        assert st.page.locator('.hex-cell.is-expanded[data-project-id="p_piano"]').count() == 1   # 打开的是项目大六边形（「十二」1）
        assert st.posts == []


def test_running_task_card_drags_to_a_cell_and_asks_before_placing(browser, base) -> None:  # noqa: F811  十四·6
    with open_stage(browser, base, width=1600, setup=_day) as st:
        _open(st)
        st.drag(".hp-task.is-running", st.center(CELL.format("p_piano")))
        st.settle()
        card = st.page.locator(".st-confirm")
        assert st.posts == [] and card.locator(".st-cf-title").inner_text() == "把这次计时记到 钢琴"
        assert st.page.locator(".cm-panel").is_visible() and st.page.locator(".hp-task.is-running").count() == 1   # 卡片回原位
        assert st.page.locator(".hp-task.is-running").evaluate("e => e.style.position === '' && e.style.translate === ''")
        card.locator(".st-cf-ok").click()
        st.settle()
        assert st.posts == [("timer/place", {"projectId": "p_piano"})]


def test_running_task_card_dropped_on_nothing_goes_back_and_sends_nothing(browser, base) -> None:  # noqa: F811  十四·6
    with open_stage(browser, base, width=1600, setup=_day) as st:
        _open(st)
        st.drag(".hp-task.is-running", (40, 600))
        st.settle()
        assert st.posts == [] and st.page.locator(".st-confirm").count() == 0
        assert st.page.locator(".hp-task.is-running").evaluate("e => e.style.position === '' && e.style.translate === ''")
        other = st.page.locator(".hp-task:not(.is-running)").first
        before = other.bounding_box()
        st.drag(".hp-task:not(.is-running)", st.center(CELL.format("p_piano")))   # 其余小卡片不能拖
        st.settle()
        assert st.posts == [] and st.page.locator(".st-confirm").count() == 0 and other.bounding_box() == before


def test_window_records_show_five_then_expand_all_and_filed_ones_are_colored(browser, base) -> None:  # noqa: F811  十四·7
    with open_stage(browser, base, width=1600, setup=_day) as st:
        _open(st)
        assert st.page.locator(".hp-win").count() == 5
        more = st.page.locator(".hp-more")
        assert more.inner_text() == "展开全部"
        more.click()
        assert st.page.locator(".hp-win").count() == 8 and st.page.locator(".hp-more").inner_text() == "收起"
        wins = st.page.locator(".hp-win")
        classes = wins.evaluate_all("els => els.map(e => [e.querySelector('b').textContent, e.classList.contains('is-filed')])")
        filed = {n: f for n, f in classes}
        assert filed["code · main.go"] and filed["firefox · 文档"] and filed["slack · 群聊"]       # 已归到项目：项目色
        assert not filed["chrome · 标题已隐去"] and not filed["kitty"] and not filed["zoom · 周会"]   # 没归类：留白
        assert st.page.locator(".hp-win.is-filed").first.evaluate("e => getComputedStyle(e).backgroundColor") != st.page.locator(".hp-win:not(.is-filed)").first.evaluate("e => getComputedStyle(e).backgroundColor")
        st.page.locator(".hp-more").click()
        assert st.page.locator(".hp-win").count() == 5


def test_source_health_sits_under_the_window_records(browser, base) -> None:  # noqa: F811  十四·7
    def broken(st: Stage) -> None:
        _day(st)
        st.devices = [{"deviceId": "d1", "sourceHealth": {"issues": ["web_extension_missing"], "at": "2026-11-08T10:00:00+00:00"}}]
    with open_stage(browser, base, width=1600, setup=broken) as st:
        _open(st)
        assert st.page.locator('.hp-card[data-card="windows"] .hp-issues').count() == 1
        assert st.page.locator('.hp-card[data-card="time"] .hp-issues, .hp-card[data-card="tasks"] .hp-issues').count() == 0


def test_figure_on_the_left_opens_the_picker_and_there_is_no_timer_archive(browser, base) -> None:  # noqa: F811  十四·2、9
    index = {"packs": [{"kind": "kimi", "name": "Kimi", "cover": "ki.webm"}]}
    with open_stage(browser, base, width=1600, setup=_day) as st:
        st.page.route("**/hive/pets/index.json", lambda r: r.fulfill(status=200, content_type="application/json", body=json.dumps(index)))
        assert st.page.locator("#archiveList, .archive-shell").count() == 0 and "计时档案" not in st.page.content()
        assert not [u for u in st.requests if "/events?type=session.completed&limit=50" in u]       # 档案的那次读取（蜂巢格子自己读的是 limit=200）
        _open(st)
        fig, cards = st.box(".hp-figure"), st.box(".hp-cards")
        assert fig["x"] + fig["width"] <= cards["x"] + 1                              # 立绘在卡片左边
        st.page.locator(".hp-figure").click()
        st.page.wait_for_selector(".fp-tile")
        assert st.page.locator(".fp-panel h3").inner_text() == "我的形象" and st.page.locator(".cm-panel").count() == 0


def test_figure_with_an_animated_pack_is_as_big_as_the_still_one(browser, base) -> None:  # noqa: F811  十六·1
    def setup(st: Stage) -> None:
        _day(st)
        st.settings = {"tiers": True, "pet": {"humanPack": "claude"}}
    pack = {"claude": {"moods": {"waiting": ["w/a.webm"]}}}
    with open_stage(browser, base, width=1600, setup=setup, pets=pack, hold_webm=True) as st:
        _open(st)
        st.page.wait_for_selector(".hp-figure-slot video")
        fig, vid = st.box(".hp-figure"), st.box(".hp-figure-slot video")
        slot = st.box(".hp-figure-slot")
        assert vid["height"] >= fig["height"] * 0.8, (vid, fig)                      # 以前是按宽度缩成一小块
        assert slot["height"] >= fig["height"] * 0.8 and slot["width"] >= fig["width"] * 0.8
        assert abs((vid["x"] + vid["width"] / 2) - (fig["x"] + fig["width"] / 2)) < 2   # 居中
        assert slot["y"] >= fig["y"] and slot["y"] + slot["height"] <= fig["y"] + fig["height"] + 1   # 画面框整个在立绘框里，不被裁
        if SHOT:
            st.page.screenshot(path=SHOT.replace(".png", "-figure.png"))


def _fire_drag(st: Stage, card_text: str, to: tuple[float, float]) -> None:
    st.drag(f'.hp-win:has-text("{card_text}")', to)
    st.settle()


def test_window_card_drags_onto_a_project_cell_and_files_it_then_recolors(browser, base) -> None:  # noqa: F811  十六·4
    with open_stage(browser, base, width=1600, setup=_day) as st:
        _open(st)
        st.page.locator(".hp-more").click()
        card = st.page.locator('.hp-win:has-text("kitty")')
        assert "is-filed" not in card.get_attribute("class")
        st.replies["activity/blocks/assign"] = (200, {"seq": 2})
        st.drag('.hp-win:has-text("kitty")', st.center(CELL.format("p_piano")), release=False)
        for b in st.blocks:                                                          # 松手前让桩的窗口块变成「已归类」，松手后卡片重读
            if b["blockKey"] == "kitty":
                b.update(status="assigned", seq=2, parts=[{"fromTs": None, "toTs": None, "taskId": None, "projectId": "p_piano", "seconds": 600}])
        st.page.mouse.up()
        st.page.clock.run_for(50)
        st.settle()
        posts = [p for p in st.posts if p[0] == "activity/blocks/assign"]
        assert len(posts) == 1 and posts[0][1]["blockKey"] == "kitty" and posts[0][1]["projectId"] == "p_piano" and posts[0][1]["ifSeq"] == 1 and posts[0][1]["date"]
        assert st.page.locator(".st-file").count() == 1                              # 5 秒「以后都算」气泡照旧
        st.page.clock.run_for(100)
        st.page.wait_for_function("() => document.querySelector('.hp-win[data-status=\"assigned\"]') && [...document.querySelectorAll('.hp-win')].find(e => e.textContent.includes('kitty') && e.classList.contains('is-filed'))")
        assert st.page.locator('.hp-win:has-text("kitty")').evaluate("e => e.style.position === '' && e.style.translate === ''")


def test_filed_window_card_can_be_dragged_to_another_project(browser, base) -> None:  # noqa: F811  十六·4
    with open_stage(browser, base, width=1600, setup=_day) as st:
        _open(st)
        st.replies["activity/blocks/assign"] = (200, {"seq": 2})
        _fire_drag(st, "code · main.go", st.center(CELL.format("p_piano")))      # 原来归在数学
        posts = [p for p in st.posts if p[0] == "activity/blocks/assign"]
        assert len(posts) == 1 and posts[0][1]["blockKey"] == "code · main.go" and posts[0][1]["projectId"] == "p_piano" and posts[0][1]["ifSeq"] == 1


def test_window_card_dropped_on_nothing_goes_back_and_sends_nothing(browser, base) -> None:  # noqa: F811  十六·4
    with open_stage(browser, base, width=1600, setup=_day) as st:
        _open(st)
        before = st.box('.hp-win:has-text("kitty")')
        _fire_drag(st, "kitty", (40, 600))
        assert st.posts == [] and st.page.locator(".st-confirm").count() == 0
        assert st.page.locator('.hp-win:has-text("kitty")').evaluate("e => e.style.position === '' && e.style.translate === ''")
        assert st.box('.hp-win:has-text("kitty")') == before and st.page.locator(".cm-panel").is_visible()


def test_three_day_old_window_records_show_in_the_default_view_and_week_but_not_in_3_hours(browser, base) -> None:  # noqa: F811  十六·3
    def setup(st: Stage) -> None:
        _day(st)
        _old_block(st)
    with open_stage(browser, base, width=1600, setup=setup) as st:
        _open(st)
        st.page.locator(".hp-more").click()
        assert "code · old.go" in st.page.locator(".hp-win b").all_inner_texts()
        assert any("code · old.go" in t for t in st.page.locator(".hp-lane-win .hp-seg").evaluate_all("els => els.map(e => e.title)"))
        ranges = [u for u in st.requests if "/activity/blocks/range" in u]
        assert ranges and all("/activity/blocks/records" not in u for u in st.requests[st.requests.index(ranges[0]):])   # 一次读整个范围，不再逐块读
        st.page.click('.hp-range-btn[data-range="week"]')
        st.page.wait_for_function("() => document.querySelector('.hp-range-btn[data-range=\"week\"]').getAttribute('aria-pressed') === 'true'")
        st.page.locator(".hp-more").click()
        assert "code · old.go" in st.page.locator(".hp-win b").all_inner_texts()
        st.page.click('.hp-range-btn[data-range="week"]')
        st.page.click('.hp-range-btn[data-range="hours"]')
        st.page.wait_for_function("() => document.querySelector('.hp-hours')")
        assert "code · old.go" not in st.page.locator(".hp-win b").all_inner_texts()
