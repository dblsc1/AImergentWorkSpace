"""ring 倒计时（番茄钟）自核套件（2026-08-09 任务单）。

倒计时＝预设了目标时长的正计时：「开始倒计时」打的是与「开始计时」完全相同的
``POST /api/core/timer/start``，到点自动打的是与「停止并记录」完全相同的
``POST /api/core/timer/stop``——本套件延用 test_ring_instrument.py 的判据
哲学：**写入面永远被 abort，判据落在"打没打、打了几次、打的是哪个 URL/body"，
不落在"写请求真的成功了"**（那需要真后端，不在本套件范围内）。

覆盖面：

  CD1  选预设/自定义时长后「开始倒计时」——按钮可用性 + 打且仅打一次
       timer/start，body 里 taskId 正确
  CD2  倒计时剩余显示 + 进度弧随时间推进正确收缩（伪造 localStorage 记录）
  CD3  到点：仅触发一次 timer/stop（含冷却期内不重复调用）+ 视觉「时间到」
  CD4  刷新续算：goto 前注入未过期/已过期两种 localStorage 记录
  CD5  互斥：倒计时运行中 #start-big-btn 隐藏；idle 态切 tab 时二者的
       入口互斥；任一模式运行中两个 tab 与时长面板整体隐藏
  CD6  beep/Notification 降级路径不产生任何 console error / page error
"""

from __future__ import annotations

import json
import time

import pytest

from conftest import COUNTDOWN_STORAGE_KEY, open_ring


def _assert_no_timer_writes(ring, *, allow: int = 0) -> None:
    assert ring.timer_write_attempts == allow, (
        f"计时写接口被调用了 {ring.timer_write_attempts} 次（期望 {allow}）："
        f"{ring.timer_write_urls}"
    )


def _parse_hhmmss(text: str) -> int:
    h, m, s = (int(part) for part in text.split(":"))
    return h * 3600 + m * 60 + s


def _seed_script(record: dict) -> str:
    """生成一段在 goto 前把倒计时记录写进 localStorage 的 init script。"""
    payload = json.dumps(json.dumps(record, ensure_ascii=False))
    key = json.dumps(COUNTDOWN_STORAGE_KEY)
    return f"try {{ localStorage.setItem({key}, {payload}); }} catch (e) {{}}"


# ── CD1：开始倒计时——按钮可用性 + 打且仅打一次 timer/start ────────────────
def test_cd1_start_countdown_calls_start_once_with_task_id(ring_idle) -> None:
    ring = ring_idle
    ring.page.select_option("#project-select", "p_eng")
    ring.page.select_option("#task-select", "t_word")
    ring.page.click("#mode-tab-countdown")

    assert ring.page.evaluate("() => document.getElementById('countdown-picker').hidden") is False, (
        "切到「倒计时」tab 后时长面板必须露出"
    )
    assert ring.page.evaluate("() => document.getElementById('start-countdown-btn').disabled") is True, (
        "还没选时长，「开始倒计时」应保持禁用"
    )

    ring.page.click(".duration-chip[data-minutes='25']")
    assert ring.page.evaluate("() => document.getElementById('start-countdown-btn').disabled") is False, (
        "选了任务 + 25 分预设后「开始倒计时」应可点"
    )

    ring.page.click("#start-countdown-btn")
    ring.page.wait_for_timeout(300)

    assert ring.timer_write_attempts == 1, f"应恰好打一次：{ring.timer_write_urls}"
    assert ring.timer_write_urls[0].endswith("/api/core/timer/start"), (
        f"「开始倒计时」必须打 timer/start（与「开始计时」同一接口），实得 {ring.timer_write_urls}"
    )
    body = json.loads(ring.timer_write_bodies[0])
    assert body == {"taskId": "t_word"}, f"body 应为 {{taskId:'t_word'}}，实得 {body}"


def test_cd1b_custom_minutes_must_be_a_positive_integer(ring_idle) -> None:
    ring = ring_idle
    ring.page.select_option("#project-select", "p_eng")
    ring.page.select_option("#task-select", "t_word")
    ring.page.click("#mode-tab-countdown")

    ring.page.fill("#duration-custom", "0")
    assert ring.page.evaluate("() => document.getElementById('start-countdown-btn').disabled") is True, (
        "自定义 0 分不合法，「开始倒计时」应保持禁用"
    )
    ring.page.fill("#duration-custom", "90")
    assert ring.page.evaluate("() => document.getElementById('start-countdown-btn').disabled") is False, (
        "自定义 90 分是合法正整数，应可点"
    )
    # 选自定义之后预设 chip 的高亮应清掉——两者互斥，不能同时"选中"。
    active_chip = ring.page.evaluate(
        "() => [...document.querySelectorAll('.duration-chip')].some(c => c.classList.contains('is-active'))"
    )
    assert active_chip is False, "输入自定义分钟后不应还有预设 chip 处于高亮态"
    _assert_no_timer_writes(ring)


# ── CD2：倒计时剩余显示 + 进度弧随时间推进正确收缩 ─────────────────────────
def test_cd2_remaining_display_and_arc_reflect_local_record(ring) -> None:
    # `ring` 默认 running=true，task_id="t_word"（见 conftest.current_running 默认值）。
    record = {"taskId": "t_word", "targetSeconds": 1500, "endAt": int(time.time() * 1000) + 600_000}
    ring.page.evaluate(f"() => {{ try {{ localStorage.setItem('{COUNTDOWN_STORAGE_KEY}', "
                        f"JSON.stringify({json.dumps(record)})); }} catch (e) {{}} }}")

    ring.page.wait_for_function(
        "() => document.getElementById('countdown-overlay').hidden === false", timeout=8_000
    )
    assert ring.page.evaluate("() => document.getElementById('chrono-center').hidden") is True, (
        "倒计时展示时默认的表芯（已用时长）必须让位，不能两套文字叠在一起"
    )
    remaining_text = ring.page.text_content("#countdown-remaining").strip()
    assert remaining_text == "00:10:00", f"剩余 600s 应显示 00:10:00，实得 {remaining_text!r}"

    dash = ring.page.evaluate(
        "() => parseFloat(document.getElementById('countdown-arc').getAttribute('stroke-dasharray'))"
    )
    assert dash == pytest.approx(40.0, abs=1.0), f"600/1500=40%，进度弧实得 {dash}"
    assert ring.page.evaluate("() => document.getElementById('mode-tabs').hidden") is True, (
        "计时进行中（不论正/倒计时）计时方式 tab 必须整体隐藏"
    )
    _assert_no_timer_writes(ring)


def test_cd2b_mismatched_task_id_record_is_ignored_and_cleared(ring) -> None:
    # 本地记录指向一个当前并不在跑的任务——必须判定"不是我的倒计时"，
    # 展示照旧走正计时的已用时长分支，并顺手清理这条失效记录。
    record = {"taskId": "t_read", "targetSeconds": 900, "endAt": int(time.time() * 1000) + 300_000}
    ring.page.evaluate(f"() => {{ try {{ localStorage.setItem('{COUNTDOWN_STORAGE_KEY}', "
                        f"JSON.stringify({json.dumps(record)})); }} catch (e) {{}} }}")
    ring.page.wait_for_timeout(1500)

    assert ring.page.evaluate("() => document.getElementById('countdown-overlay').hidden") is True, (
        "记录里的 taskId 与当前在跑的任务不一致，不该展示成倒计时"
    )
    assert ring.page.evaluate("() => document.getElementById('chrono-center').hidden") is False
    cleared = ring.page.evaluate(f"() => localStorage.getItem('{COUNTDOWN_STORAGE_KEY}')")
    assert cleared is None, "不匹配的失效记录应被清理，不该无限期留着造成下次误判"
    _assert_no_timer_writes(ring)


# ── CD3：到点——仅触发一次 timer/stop，冷却期内不重复 ───────────────────────
def test_cd3_time_up_triggers_stop_exactly_once_within_cooldown(ring) -> None:
    record = {"taskId": "t_word", "targetSeconds": 60, "endAt": int(time.time() * 1000) - 1_000}
    ring.page.evaluate(f"() => {{ try {{ localStorage.setItem('{COUNTDOWN_STORAGE_KEY}', "
                        f"JSON.stringify({json.dumps(record)})); }} catch (e) {{}} }}")

    ring.page.wait_for_timeout(1500)  # 让第一次 tick 判到"已过期"并发起自动停止
    assert ring.timer_write_attempts == 1, (
        f"到点应自动打恰好一次 timer/stop，实得 {ring.timer_write_attempts}：{ring.timer_write_urls}"
    )
    assert ring.timer_write_urls[0].endswith("/api/core/timer/stop"), (
        f"到点自动停必须走 timer/stop（与「停止并记录」同一接口），实得 {ring.timer_write_urls}"
    )
    assert ring.page.evaluate(
        "() => document.getElementById('countdown-overlay').classList.contains('is-time-up')"
    ) is True, "到点后必须出现「时间到」的视觉提示（闪烁样式）"

    # 静置到接近但不超过冷却期（RETRY_COOLDOWN_MS=5000），不许出现第二次调用
    # ——本套件的写入面永远 abort，stopTimer 永远"失败"，这条断言真正验证的
    # 是冷却期本身在生效，不是碰巧只失败了一次。
    ring.page.wait_for_timeout(2500)
    assert ring.timer_write_attempts == 1, (
        f"冷却期内不许重复调用，实得 {ring.timer_write_attempts} 次：{ring.timer_write_urls}"
    )


# ── CD4：刷新续算——goto 前注入的记录 ───────────────────────────────────────
def test_cd4_resume_after_reload_computes_remaining_from_local_record(
    browser, static_base_url, gateway_navbar_available
) -> None:
    end_at = int(time.time() * 1000) + 120_000  # 还剩 2 分钟
    record = {"taskId": "t_word", "targetSeconds": 1500, "endAt": end_at}
    with open_ring(
        browser, static_base_url, with_navbar=gateway_navbar_available,
        init_scripts=(_seed_script(record),),
    ) as ring:
        ring.page.wait_for_function(
            "() => document.getElementById('countdown-overlay').hidden === false", timeout=8_000
        )
        remaining_text = ring.page.text_content("#countdown-remaining").strip()
        remaining_seconds = _parse_hhmmss(remaining_text)
        # 允许几秒误差：`end_at` 在 Python 侧算好之后，还要经过浏览器启动/
        # goto/脚本执行的真实耗时才跑到第一次 tick，不是零延迟——断言的是
        # "续算出的剩余接近 2 分钟"，不是"精确到毫秒"（那不是本用例要测的
        # 语义，本用例要测的是"读到了 endAt 并算出了合理剩余"）。
        assert remaining_seconds == pytest.approx(120, abs=3), (
            f"刷新前就有的记录应在页面刚加载完就续算出剩余约 2 分钟，实得 {remaining_text!r}"
        )
        _assert_no_timer_writes(ring)


def test_cd4b_resume_with_already_expired_record_triggers_auto_stop(
    browser, static_base_url, gateway_navbar_available
) -> None:
    end_at = int(time.time() * 1000) - 5_000  # 浏览器关闭期间早就到点了
    record = {"taskId": "t_word", "targetSeconds": 60, "endAt": end_at}
    with open_ring(
        browser, static_base_url, with_navbar=gateway_navbar_available,
        init_scripts=(_seed_script(record),),
    ) as ring:
        ring.page.wait_for_timeout(1500)
        assert ring.timer_write_attempts == 1, (
            f"刷新时记录早已过期，应视为到点立即走自动停止，实得 {ring.timer_write_attempts} 次"
        )
        assert ring.timer_write_urls[0].endswith("/api/core/timer/stop")


# ── CD5：互斥 ─────────────────────────────────────────────────────────────
def test_cd5_idle_tab_switch_toggles_which_start_entry_is_visible(ring_idle) -> None:
    ring = ring_idle
    ring.page.select_option("#project-select", "p_eng")
    ring.page.select_option("#task-select", "t_word")

    assert ring.page.is_visible("#start-big-btn"), "默认「正计时」tab：开始计时按钮应可见"
    assert ring.page.evaluate("() => document.getElementById('countdown-picker').hidden") is True

    ring.page.click("#mode-tab-countdown")
    assert ring.page.evaluate("() => document.getElementById('start-big-btn').hidden") is True, (
        "切到「倒计时」后「开始计时」入口必须隐藏——不给同时点两种开始的机会"
    )
    assert ring.page.evaluate("() => document.getElementById('countdown-picker').hidden") is False

    ring.page.click("#mode-tab-stopwatch")
    assert ring.page.is_visible("#start-big-btn"), "切回「正计时」应恢复可见"
    assert ring.page.evaluate("() => document.getElementById('countdown-picker').hidden") is True
    _assert_no_timer_writes(ring)


def test_cd5b_running_state_hides_both_mode_tabs_and_picker(ring) -> None:
    # `ring` fixture 就是运行态（正计时口径，未注入任何倒计时本地记录）——
    # 计时方式 tab 与时长面板必须整体隐藏，不给"运行中还能切模式"的错觉。
    # ring-countdown.js 的主循环每 1 秒一次（TICK_MS），第一次同步执行的
    # tick() 跑在 window.ringCurrentState 写入之前，必须等到下一次定时 tick
    # 才会读到 running=true——这里等够 1.2s（>1 个 tick 周期），不是碰运气。
    ring.page.wait_for_function(
        "() => document.getElementById('mode-tabs').hidden === true", timeout=8_000
    )
    assert ring.page.evaluate("() => document.getElementById('countdown-picker').hidden") is True
    _assert_no_timer_writes(ring)


def test_cd5c_countdown_running_also_removes_stopwatch_entry(ring) -> None:
    # 运行中的这段恰好是"倒计时启动的"（本地记录与当前任务匹配）：
    # #start-big-btn 本就不在运行态的 DOM 里（renderRunningCenter 不生成它），
    # 这条用例是给这个"结构性互斥"钉一条断言，不是靠猜的。
    record = {"taskId": "t_word", "targetSeconds": 1500, "endAt": int(time.time() * 1000) + 600_000}
    ring.page.evaluate(f"() => {{ try {{ localStorage.setItem('{COUNTDOWN_STORAGE_KEY}', "
                        f"JSON.stringify({json.dumps(record)})); }} catch (e) {{}} }}")
    ring.page.wait_for_timeout(1200)
    exists = ring.page.evaluate("() => document.getElementById('start-big-btn') !== null")
    assert exists is False, "运行态（无论正/倒计时）DOM 里都不该存在「开始计时」大按钮"
    _assert_no_timer_writes(ring)


# ── CD6：到点提醒降级路径零 console/page error ─────────────────────────────
def test_cd6_time_up_reminder_degrades_silently_no_console_errors(ring) -> None:
    console_errors: list[str] = []
    page_errors: list[str] = []
    ring.page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
    ring.page.on("pageerror", lambda exc: page_errors.append(str(exc)))

    record = {"taskId": "t_word", "targetSeconds": 60, "endAt": int(time.time() * 1000) - 1_000}
    ring.page.evaluate(f"() => {{ try {{ localStorage.setItem('{COUNTDOWN_STORAGE_KEY}', "
                        f"JSON.stringify({json.dumps(record)})); }} catch (e) {{}} }}")
    ring.page.wait_for_timeout(2000)  # 覆盖 startTimeUpEffects()->playBeep()/sendNotification() 全程

    # 这条用例本身会顺带触发一次真实的（被浏览器 abort 的）timer/stop 请求
    # ——`route.abort()` 让 Chromium 自己打一条 `net::ERR_FAILED` 的资源加载
    # 错误到 console，这是**测试哈内斯故意 abort 写请求**的副作用（同一套
    # abort 机制在 L7/L7b 里也会触发，只是那两条用例没去看 console），与本
    # 用例要验证的"beep/Notification 降级不报错"是两件不同的事，过滤掉。
    real_errors = [msg for msg in console_errors if "net::ERR_FAILED" not in msg]
    assert real_errors == [], (
        f"到点提醒（beep/Notification 降级路径）不许有 console error：{real_errors}"
    )
    assert page_errors == [], f"到点提醒不许有未捕获异常：{page_errors}"
