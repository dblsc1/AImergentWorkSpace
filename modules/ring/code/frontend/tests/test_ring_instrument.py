"""ring 仪表圆环自核套件（2026-08-08 ，cockpit-v1 改造）。

取代 2026-08-03 的 ``test_ring_ui_defects.py``（U1–U5）：F-RING-1 把贡献圆环与
计时控件合并成一件仪器后，DOM 形状与部分行为本身都变了（尤其是动画语义从
"每次真变化都播 900ms" 简化成"仅首载 350ms，此后一律直接就位"），旧文件名与
旧断言已不能诚实描述现在的行为，所以整份重写而不是打补丁。这里仍然是行为级
判据（真浏览器、不碰真库），覆盖面按 F-RING-1..6 + A2/A3/A9(代理)/A13(代理)
重新拉了一遍，具体映射：

  L1  分段仅首载播 350ms 动画一次；此后（含空闲轮询、含真实数据变化）都不再播
  L2  分段仍然"只在数据真变化时重绘"（旧 U1 保留的护栏形状）
  L3  reduced-motion 下不播动画、一步到位（旧 U5）
  L4  零任务项目在任务选择器里可见但不可选（旧 U3/U4，改成两级选择器后的等价物）
  L5  F-RING-6 前置提示：出现 / 不出现的四种字段组合，含防御式缺字段路径
  L6  F-RING-4 URL 预选 ``?task=<id>``，含无效 id 静默忽略
  L7  F-RING-2/A9 代理：取消需要确认，确认前不发任何写请求，确认后只打
      ``timer/cancel``、从不打 ``timer/stop``；停止按钮走 ``timer/stop``
  L8  F-RING-5 390px 容器不横滚 + 关键交互件触控 ≥44px

  LT1-LT6  后补的一轮：F-RING-1「今天」语义（GET /api/core/views/gantt）——
      环分段/图例改用今天切片而不是终身累计、idle 表芯「今天 · N 分」（0 也
      如实显示）、跨日边界（昨天的秒数不许算进今天）、gantt 不可达/单项目缺
      tasks 字段时的防御式回退、今天模式下重绘纪律依旧成立。

真实的 A9「档案计数不变」与 A13 在真后端上的端到端验证不在本套件覆盖范围内。
"""

from __future__ import annotations

import pytest

from conftest import (
    CURRENT_IDLE,
    GANTT_MISSING_TASKS_FOR_P_ENG,
    GANTT_WITH_TODAY_DATA,
    PROJECT_IDS,
    TREE,
    ZERO_TASK_PROJECTS,
    current_running,
    open_ring,
)

POLL_MS = 7000


def _assert_no_timer_writes(ring) -> None:
    assert ring.timer_write_attempts == 0, (
        f"用例试图调用计时写接口 {ring.timer_write_attempts} 次：{ring.timer_write_urls} —— "
        "本套件不许真起停表（会往 append-only 的真库写不可删的记录）"
    )


# ── L1/L2：首载动画仅一次，此后数据不变不重绘、数据真变化仍重绘但不再播动画 ──
def test_l1_first_load_animation_plays_exactly_once(ring) -> None:
    _wait_for_first_load(ring)
    # 350ms 的过渡本身很快，但 rAF 回调何时真的落地取决于合成器/事件循环调度，
    # 在并发跑多个用例时曾观测到超过 600ms 才落地——留够余量再取「已稳定」基线，
    # 避免把调度延迟错判成"多余的重绘"（真金不怕火炼：后面还有 3 个轮询周期的
    # 静置窗口，真的多写一次照样会被抓到）。
    ring.page.wait_for_timeout(1500)

    settled_writes = ring.seg_write_count()
    ring.page.wait_for_timeout(POLL_MS * 3 + 1000)  # 静置 3 个轮询周期，数据没变

    assert ring.current_polls >= 3, (
        f"轮询只跑了 {ring.current_polls} 次，护栏证明不了「数据没变不重绘」（可能是假绿）"
    )
    assert ring.first_load_count() == 1, (
        f"首载动画被触发了 {ring.first_load_count()} 次，应当恰好 1 次——"
        "F-RING-3「仅首载」不许在静置轮询期间再播"
    )
    assert ring.seg_write_count() == settled_writes, (
        "数据没变却仍在改分段的 stroke-dasharray —— 说明 applyState 还是被反复重绘了"
    )
    _assert_no_timer_writes(ring)


def test_l2_real_change_still_redraws_but_never_replays_animation(ring) -> None:
    _wait_for_first_load(ring)
    ring.page.wait_for_timeout(600)
    writes_after_first_load = ring.seg_write_count()

    # 换任务：真实数据变化，分段必须重绘，但不许再播首载动画。
    ring.set_current(current_running(task_id="t_read", task_name="曲目视奏",
                                     share_of_project=11.0, task_seconds=1800))
    ring.wait_for_polls(ring.current_polls + 1)
    ring.page.wait_for_timeout(500)

    assert ring.seg_write_count() > writes_after_first_load, (
        "任务真的切换了，分段却没有重绘——F-RING-3 第二条「仅数据变化时重绘」的"
        "反向也要成立：真变化必须重绘"
    )
    assert ring.first_load_count() == 1, (
        f"任务切换后首载动画计数变成了 {ring.first_load_count()}——"
        "「仅首载」不该因为后续真实变化又播一次"
    )
    _assert_no_timer_writes(ring)


def _wait_for_first_load(ring, timeout_ms: int = POLL_MS * 2) -> None:
    waited = 0
    while ring.first_load_count() < 1:
        if waited >= timeout_ms:
            raise AssertionError(f"等了 {waited}ms 首载动画都没播（渲染没跑起来）")
        ring.page.wait_for_timeout(250)
        waited += 250


# ── L3：reduced-motion 下一步到位 ──────────────────────────────────────
def test_l3_reduced_motion_draws_directly_no_animation(browser, static_base_url,
                                                        gateway_navbar_available) -> None:
    with open_ring(browser, static_base_url, with_navbar=gateway_navbar_available,
                   reduced_motion="reduce") as ring:
        ring.page.wait_for_function(
            """() => {
                 const d = document.getElementById('seg-current').getAttribute('stroke-dasharray');
                 return d && parseFloat(d) > 0;
               }""",
            timeout=15_000,
        )
        assert ring.first_load_count() == 0, (
            "reduced-motion 下不该出现 is-first-load（不该跑过渡动画）"
        )
        dash = ring.page.evaluate(
            "() => document.getElementById('seg-current').getAttribute('stroke-dasharray')"
        )
        drawn = float(dash.replace(",", " ").split()[0])
        assert drawn == pytest.approx(32.1, abs=0.5), (
            f"reduced-motion 下应当一步到位画到 shareOfProject=32.1，实得 {drawn}（{dash!r}）"
        )
        settled = ring.seg_write_count()
        ring.page.wait_for_timeout(POLL_MS * 2 + 1000)
        assert ring.seg_write_count() == settled, "reduced-motion 下数据没变却仍在重画分段"
        _assert_no_timer_writes(ring)


# ── L4：零任务项目可见不可选（两级选择器版） ────────────────────────────
def test_l4_empty_project_shows_disabled_hint_row(ring_idle) -> None:
    ring = ring_idle
    labels = ring.page.evaluate(
        """() => [...document.querySelectorAll("#project-select option")]
                   .filter(o => o.value).map(o => o.textContent)"""
    )
    assert len(labels) == 3, f"项目选择器应有三个真实项目，实得 {labels}"

    for name in ZERO_TASK_PROJECTS:
        project_id = next(p["id"] for p in TREE["projects"] if p["name"] == name)
        ring.page.select_option("#project-select", project_id)
        options = ring.page.evaluate(
            """() => [...document.querySelectorAll("#task-select option")].map(o => ({
                  value: o.value, disabled: o.disabled, text: o.textContent
               }))"""
        )
        real = [o for o in options if o["value"]]
        assert not real, f"「{name}」应当零任务，实得可选项 {real}"
        hints = [o for o in options if o["disabled"] and o["text"] != "选择任务…"]
        assert len(hints) == 1, f"「{name}」应当恰好一行不可选提示，实得 {options}"
        assert "还没有任务" in hints[0]["text"], f"提示行没写清怎么办：{hints[0]}"

        # 项目本身绝不能被做成任务选择器里的可选项。
        values = {o["value"] for o in options}
        assert project_id not in values


def test_l4b_hint_row_cannot_be_selected_by_keyboard(ring_idle) -> None:
    ring = ring_idle
    project_id = next(p["id"] for p in TREE["projects"] if p["name"] == "考级计划")
    ring.page.select_option("#project-select", project_id)

    disabled_index = ring.page.evaluate(
        """() => [...document.querySelectorAll("#task-select option")]
                   .findIndex(o => o.disabled)"""
    )
    assert disabled_index >= 0, "夹具前提不成立：应该有一行不可选提示"

    ring.page.focus("#task-select")
    visited = []
    option_count = ring.page.evaluate("() => document.getElementById('task-select').options.length")
    for _ in range(option_count + 2):
        ring.page.keyboard.press("ArrowDown")
        visited.append(ring.page.evaluate("() => document.getElementById('task-select').selectedIndex"))
    assert disabled_index not in visited, "键盘走到了不可选的提示行——提示行必须被跳过"

    for project_id in PROJECT_IDS:
        value = ring.page.evaluate("() => document.getElementById('task-select').value")
        assert value != project_id, "项目 id 绝不能出现在任务选择器的 value 里"


# ── L5：F-RING-6 前置提示，含防御式缺字段路径 ───────────────────────────
@pytest.mark.parametrize(
    "task_id,expect_hint,why",
    [
        ("t_word", False, "没有 dependsOn 字段（旧形状任务）——不显示不报错"),
        ("t_read", True, "dependsOn=[t_word]，t_word.done=false——真实未完成前置"),
        ("t_legacy", False, "没有 done、没有 dependsOn——两个字段都缺"),
        ("t_write", False, "依赖 t_legacy，但 t_legacy 没有 done 字段——不敢断言未完成"),
        ("t_dangling", False, "依赖的任务 id 在树里查不到——静默当没有这条依赖"),
    ],
)
def test_l5_depends_on_hint_defensive_matrix(ring_idle, task_id, expect_hint, why) -> None:
    ring = ring_idle
    ring.page.select_option("#project-select", "p_eng")
    ring.page.select_option("#task-select", task_id)
    hidden = ring.page.evaluate("() => document.getElementById('depends-hint').hidden")
    assert hidden != expect_hint, f"{task_id}：{why}（期望出现提示={expect_hint}，实得 hidden={hidden}）"
    if expect_hint:
        text = ring.page.text_content("#depends-hint")
        assert "音阶练习" in text, f"提示文案应点名未完成的前置任务名，实得 {text!r}"


# ── L6：F-RING-4 URL 预选 ───────────────────────────────────────────────
def test_l6_url_preselect_valid_task(browser, static_base_url, gateway_navbar_available) -> None:
    with open_ring(browser, static_base_url, with_navbar=gateway_navbar_available,
                   initial_current=CURRENT_IDLE, url_suffix="?task=t_read") as ring:
        ring.page.wait_for_function(
            "() => document.getElementById('task-select').value === 't_read'", timeout=8_000
        )
        project_value = ring.page.evaluate("() => document.getElementById('project-select').value")
        assert project_value == "p_eng", f"预选任务所属项目应联动选中 p_eng，实得 {project_value!r}"
        hint_hidden = ring.page.evaluate("() => document.getElementById('depends-hint').hidden")
        assert hint_hidden is False, "预选 t_read 后应联动出前置未完成提示"


def test_l6b_url_preselect_invalid_task_id_is_silently_ignored(
    browser, static_base_url, gateway_navbar_available
) -> None:
    with open_ring(browser, static_base_url, with_navbar=gateway_navbar_available,
                   initial_current=CURRENT_IDLE, url_suffix="?task=t_does_not_exist") as ring:
        ring.page.wait_for_selector("#start-big-btn", state="attached")
        project_value = ring.page.evaluate("() => document.getElementById('project-select').value")
        task_value = ring.page.evaluate("() => document.getElementById('task-select').value")
        assert project_value == "", f"无效 task id 不该联动选中任何项目，实得 {project_value!r}"
        assert task_value == "", f"无效 task id 不该联动选中任何任务，实得 {task_value!r}"
        assert ring.page.is_visible("#start-big-btn"), "无效 id 应静默落回空闲态展示，不报错"


# ── L7：取消确认 + A9 代理判据 ──────────────────────────────────────────
def test_l7_cancel_requires_confirmation_and_only_calls_cancel_endpoint(ring) -> None:
    ring.page.click("#cancel-open-btn")
    assert ring.page.evaluate("() => document.getElementById('cancel-dialog').open") is True, (
        "点「取消，不记录」必须先弹确认层，不能直接发请求"
    )
    _assert_no_timer_writes(ring)

    ring.page.click("#cancel-dialog-back")
    assert ring.page.evaluate("() => document.getElementById('cancel-dialog').open") is False
    _assert_no_timer_writes(ring)  # 「返回」不许打任何请求

    ring.page.click("#cancel-open-btn")
    ring.page.click("#cancel-dialog-confirm")
    ring.page.wait_for_timeout(300)
    assert ring.timer_write_attempts == 1, (
        f"确认取消应当恰好打一次写接口，实得 {ring.timer_write_attempts}：{ring.timer_write_urls}"
    )
    assert ring.timer_write_urls[0].endswith("/api/core/timer/cancel"), (
        f"确认取消必须打 timer/cancel，绝不能打 timer/stop —— 实得 {ring.timer_write_urls}"
    )


def test_l7b_stop_button_calls_stop_endpoint(ring) -> None:
    ring.page.click("#stop-btn")
    ring.page.wait_for_timeout(300)
    assert ring.timer_write_attempts == 1, f"停止应当恰好打一次：{ring.timer_write_urls}"
    assert ring.timer_write_urls[0].endswith("/api/core/timer/stop"), (
        f"「停止并记录」必须打 timer/stop，实得 {ring.timer_write_urls}"
    )


# ── L8：F-RING-5 响应式 + 触控目标 ───────────────────────────────────────
def test_l8_no_horizontal_scroll_at_390px(browser, static_base_url, gateway_navbar_available) -> None:
    with open_ring(browser, static_base_url, with_navbar=gateway_navbar_available) as ring:
        ring.page.set_viewport_size({"width": 390, "height": 844})
        ring.page.wait_for_timeout(200)
        overflow = ring.page.evaluate(
            "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
        )
        assert overflow <= 0, f"390px 容器出现横向溢出 {overflow}px"


def test_l8b_key_controls_meet_tap_target_size(ring) -> None:
    for selector in ("#stop-btn", "#cancel-open-btn", "#project-select", "#task-select"):
        height = ring.page.evaluate(
            f"() => document.querySelector('{selector}').getBoundingClientRect().height"
        )
        assert height >= 44, f"{selector} 高度 {height}px 低于触控最小尺寸 44px"


# ── LT1：环分段与图例改用「今天」切片，不是终身累计 ─────────────────────
def test_lt1_segments_reflect_today_seconds_not_lifetime(ring_today) -> None:
    ring = ring_today
    ring.page.wait_for_timeout(600)  # 首载动画过渡跑完
    # p_eng 今天合计 1800s：t_word(当前,600) / t_read(次高,900) / t_legacy(其余,300)。
    dash_current = ring.page.evaluate(
        "() => parseFloat(document.getElementById('seg-current').getAttribute('stroke-dasharray'))"
    )
    dash_second = ring.page.evaluate(
        "() => parseFloat(document.getElementById('seg-second').getAttribute('stroke-dasharray'))"
    )
    dash_third = ring.page.evaluate(
        "() => parseFloat(document.getElementById('seg-third').getAttribute('stroke-dasharray'))"
    )
    assert dash_current == pytest.approx(600 / 1800 * 100, abs=0.1)
    assert dash_second == pytest.approx(900 / 1800 * 100, abs=0.1)
    assert dash_third == pytest.approx(300 / 1800 * 100, abs=0.1)

    assert ring.page.text_content("#legend-title").strip() == "今日贡献"
    assert ring.page.text_content("#legend-current-name").strip() == "音阶练习"
    assert ring.page.text_content("#legend-current-value").strip() == "10 分"
    assert ring.page.evaluate("() => document.getElementById('legend-second-row').hidden") is False
    assert ring.page.text_content("#legend-second-name").strip() == "曲目视奏"
    assert ring.page.text_content("#legend-second-value").strip() == "15 分"
    assert ring.page.evaluate("() => document.getElementById('legend-third-row').hidden") is False
    assert ring.page.text_content("#legend-third-value").strip() == "5 分"
    _assert_no_timer_writes(ring)


# ── LT2：跨日边界——昨天的秒数不许算进今天 ───────────────────────────────
def test_lt2_cross_day_boundary_excludes_yesterday(ring_today) -> None:
    ring = ring_today
    # t_write 只有**昨天**的 1200s，没有今天的记录——切到它当「当前任务」，
    # 今天切片必须是 0，不是 1200s 折算出来的非零弧长。
    ring.set_current(current_running(task_id="t_write", task_name="乐句创作",
                                     share_of_project=99.0, task_seconds=1200))
    ring.wait_for_polls(ring.current_polls + 1)
    ring.page.wait_for_timeout(500)
    dash_current = ring.page.evaluate(
        "() => parseFloat(document.getElementById('seg-current').getAttribute('stroke-dasharray'))"
    )
    assert dash_current == pytest.approx(0, abs=0.1), (
        f"t_write 只有昨天的记录，今天切片必须是 0，实得弧长 {dash_current}"
        "——如果这里非 0，说明昨天的秒数被错误地算进了今天"
    )
    assert ring.page.text_content("#legend-current-value").strip() == "0 分"
    _assert_no_timer_writes(ring)


# ── LT3：idle 表芯「今天 · N 分」，0 也如实显示 ──────────────────────────
def test_lt3_idle_shows_today_total_including_zero(ring_idle_today) -> None:
    ring = ring_idle_today
    # 未选项目：退回全部项目今日合计（本夹具里只有 p_eng 今天非 0，合计 1800s=30分）。
    ring.page.wait_for_function(
        "() => document.getElementById('idle-total').hidden === false", timeout=8_000
    )
    assert ring.page.text_content("#idle-total").strip() == "今天 · 30 分"

    # 选中 p_studio：今天真的是 0 分（只有昨天的 500s），必须显示「0 分」
    # 而不是隐藏成「没有数据」——这是本轮明文要求的那句话。
    ring.page.select_option("#project-select", "p_studio")
    ring.page.wait_for_function(
        "() => document.getElementById('idle-total').textContent.trim() === '今天 · 0 分'",
        timeout=8_000,
    )
    assert ring.page.evaluate("() => document.getElementById('idle-total').hidden") is False, (
        "今天 0 分也必须显示，不能因为『是 0』就隐藏——0 和『没有今天数据』是两件不同的事"
    )

    # 切回 p_eng：应变回 30 分。
    ring.page.select_option("#project-select", "p_eng")
    ring.page.wait_for_function(
        "() => document.getElementById('idle-total').textContent.trim() === '今天 · 30 分'",
        timeout=8_000,
    )
    _assert_no_timer_writes(ring)


# ── LT4：views/gantt 不可达时静默退回终身累计，不报错 ────────────────────
def test_lt4_gantt_unreachable_falls_back_to_lifetime(ring) -> None:
    # `ring` fixture 默认不给 gantt 夹具（route 直接 404），这正是本条要测的场景。
    ring.page.wait_for_timeout(600)
    assert ring.page.text_content("#legend-title").strip() == "项目累计贡献", (
        "views/gantt 不可达时应退回终身累计口径的图例标题，不是「今日贡献」"
    )
    status_hidden = ring.page.evaluate("() => document.getElementById('status-message').hidden")
    assert status_hidden is True, "gantt 不可达不许弹出任何错误提示（不打断 views/current 的渲染）"
    _assert_no_timer_writes(ring)


def test_lt4b_idle_gantt_unreachable_hides_total_keeps_caption(ring_idle) -> None:
    ring = ring_idle
    ring.page.wait_for_timeout(300)
    assert ring.page.evaluate("() => document.getElementById('idle-total').hidden") is True, (
        "今天数据不可用时不许显示任何数字（哪怕是编出来的 0）"
    )
    assert ring.page.text_content("#idle-caption").strip() == "当前没有进行中的计时"


# ── LT5：单个项目缺 tasks 字段只让那一个项目退回，不拖累其它项目 ─────────
def test_lt5_defensive_missing_tasks_field_falls_back_per_project(ring) -> None:
    ring.set_gantt(GANTT_MISSING_TASKS_FOR_P_ENG)  # 当前任务所在的 p_eng 缺 tasks 字段
    ring.wait_for_polls(ring.current_polls + 1)
    ring.page.wait_for_timeout(500)
    assert ring.page.text_content("#legend-title").strip() == "项目累计贡献", (
        "p_eng 缺 tasks 字段：只这一个项目的今天数据不可用，运行态（当前任务恰好在 "
        "p_eng 下）必须退回终身累计，不能整页崩掉或报错"
    )
    status_hidden = ring.page.evaluate("() => document.getElementById('status-message').hidden")
    assert status_hidden is True
    _assert_no_timer_writes(ring)


# ── LT6：今天模式下重绘纪律依旧成立（数据不变不重绘） ────────────────────
def test_lt6_redraw_gate_holds_in_today_mode(ring_today) -> None:
    ring = ring_today
    ring.page.wait_for_timeout(600)
    settled = ring.seg_write_count()
    ring.page.wait_for_timeout(POLL_MS * 2 + 1000)  # 静置两个轮询周期，gantt 夹具没变
    assert ring.current_polls >= 2
    assert ring.gantt_polls >= 2, "本条用例要证明 gantt 真的被轮询到了，不是没跑起来"
    assert ring.seg_write_count() == settled, (
        "今天模式下数据没变（同一份 gantt 夹具）却仍在重画分段"
    )
    _assert_no_timer_writes(ring)
