"""ring 补登入口自核套件（2026-08-19 任务单）。

补登＝POST /api/core/timer/backfill，与 test_ring_countdown.py 的写入面判据不同
——那套永远 abort（只测「打没打、打的是哪个 URL/body」）；本套件需要验证前端
对**成功/duplicate/失败三种响应体的具体展示**，所以对 `**/api/core/timer/backfill`
单独注册一条 route。Playwright 同一 URL 命中多个 `page.route()` 时**后注册的先
匹配**，本套件的 route 是在 conftest 建好 harness（已注册通配 `**/api/core/timer/**`
全量 abort 桩）**之后**才加的，所以能截住 fulfill、不会落到那条通用 abort 上。

覆盖面：

  BF1  空闲态：入口按钮存在、可点，打开对话框后任务下拉走
       GET /api/core/views/tree 独立加载出真实任务名
  BF2  运行态：入口按钮同样可见可点——补登与活状态计时完全独立，不因为
       #controls-row 顶掉选择器行而被一起隐藏
  BF3  提交请求体：taskId/durationSeconds（分钟×60）正确，startAt 带时区
       偏移（不是裸 ISO，格式 YYYY-MM-DDTHH:MM:SS±HH:MM）
  BF4  duplicate:true → 必须显示「这段已经补过了」，绝不能显示成「已记录」
  BF5  成功 → 回显服务端返回的 date，前端不自己算
  BF6  失败 → 显示服务端 detail 原文，不是笼统的「失败了」
  BF7  取消/关闭对话框不发任何请求
"""

from __future__ import annotations

import json
import re


def _assert_no_timer_writes(ring, *, allow: int = 0) -> None:
    assert ring.timer_write_attempts == allow, (
        f"计时写接口被调用了 {ring.timer_write_attempts} 次（期望 {allow}）："
        f"{ring.timer_write_urls}"
    )


def _open_backfill(ring) -> None:
    ring.page.click("#backfill-open-btn")
    ring.page.wait_for_selector("#backfill-dialog[open]", state="attached")
    ring.page.wait_for_function(
        "() => document.getElementById('backfill-task-select').options.length > 1", timeout=8_000
    )


def _fill_form(ring, *, task_value: str, date: str, time: str, minutes: str) -> None:
    ring.page.select_option("#backfill-task-select", task_value)
    ring.page.fill("#backfill-date", date)
    ring.page.fill("#backfill-time", time)
    ring.page.fill("#backfill-minutes", minutes)


def _stub_backfill(ring, *, status: int, body: dict) -> dict:
    """注册一条会记下请求体、并按给定状态码/响应体 fulfill 的 route。
    返回一个可变字典，key "body" 在请求真正发生后被填入解析后的 JSON。"""
    captured: dict = {}

    def handler(route):
        captured["body"] = json.loads(route.request.post_data)
        route.fulfill(status=status, content_type="application/json",
                      body=json.dumps(body, ensure_ascii=False))

    ring.page.route("**/api/core/timer/backfill", handler)
    return captured


# ── BF1：空闲态入口可点，任务下拉独立加载出真实任务名 ───────────────────
def test_bf1_idle_entry_opens_dialog_and_loads_tasks(ring_idle) -> None:
    ring = ring_idle
    assert ring.page.is_visible("#backfill-open-btn"), "空闲态补登入口必须可见"
    assert ring.page.is_enabled("#backfill-open-btn"), "空闲态补登入口必须可点"

    _open_backfill(ring)
    labels = ring.page.evaluate(
        "() => [...document.getElementById('backfill-task-select').options].map(o => o.textContent)"
    )
    assert "音阶练习" in labels, f"任务下拉应含真实任务名，实得 {labels}"
    _assert_no_timer_writes(ring)


# ── BF2：运行态入口同样可见可点 ─────────────────────────────────────────
def test_bf2_running_state_entry_still_clickable(ring) -> None:
    ring.page.wait_for_selector("#elapsed-display", state="attached")
    assert ring.page.is_visible("#backfill-open-btn"), (
        "运行态补登入口必须仍然可见——补登与活状态计时完全独立，不许被「先停表」的伪约束挡住"
    )
    assert ring.page.is_enabled("#backfill-open-btn")
    _open_backfill(ring)
    assert ring.page.evaluate("() => document.getElementById('backfill-dialog').open") is True
    _assert_no_timer_writes(ring)


# ── BF3：请求体——taskId/durationSeconds 正确，startAt 带时区偏移 ────────
def test_bf3_submit_body_has_offset_startat_and_seconds_from_minutes(ring_idle) -> None:
    ring = ring_idle
    captured = _stub_backfill(ring, status=200, body={
        "recorded": True, "duplicate": False, "date": "2026-08-18",
        "event": {"id": "evt_1", "dedupeKey": "backfill:x", "type": "session.completed"},
    })

    _open_backfill(ring)
    _fill_form(ring, task_value="t_word", date="2026-08-18", time="14:30", minutes="90")
    ring.page.click("#backfill-submit-btn")
    ring.page.wait_for_function("() => document.getElementById('backfill-message').hidden === false",
                                 timeout=8_000)

    body = captured["body"]
    assert body["taskId"] == "t_word", f"taskId 应原样透传，实得 {body}"
    assert body["durationSeconds"] == 5400, f"90 分钟应转成 5400 秒，实得 {body}"
    assert re.fullmatch(r"2026-08-18T14:30:00[+-]\d{2}:\d{2}", body["startAt"]), (
        f"startAt 必须是「日期+时刻+时区偏移」的完整 ISO 字符串，不能是裸时间，实得 {body['startAt']!r}"
    )


# ── BF4：duplicate:true → 「这段已经补过了」，绝不能是「已记录」 ─────────
def test_bf4_duplicate_response_shows_exact_wording_never_recorded(ring_idle) -> None:
    ring = ring_idle
    _stub_backfill(ring, status=200, body={
        "recorded": True, "duplicate": True, "date": "2026-08-18",
        "event": {"id": "evt_1", "dedupeKey": "backfill:x", "type": "session.completed"},
    })
    _open_backfill(ring)
    _fill_form(ring, task_value="t_word", date="2026-08-18", time="14:30", minutes="30")
    ring.page.click("#backfill-submit-btn")
    ring.page.wait_for_function("() => document.getElementById('backfill-message').hidden === false",
                                 timeout=8_000)

    text = ring.page.text_content("#backfill-message").strip()
    assert text == "这段已经补过了。", f"duplicate 分支必须显示这句原话，实得 {text!r}"
    assert "已记录" not in text, "duplicate 绝不许显示成「已记录」——那是骗用户，落库的是上一次那条"


# ── BF5：成功 → 回显服务端返回的 date，不是前端自己算的 ─────────────────
def test_bf5_success_echoes_server_date_verbatim(ring_idle) -> None:
    ring = ring_idle
    # 故意让服务端归日结果与前端表单填的「日期」字段不一致（模拟跨时区/夏令时
    # 场景下两者本该不同）——断言的是页面显示的是**响应体里的** date，不是
    # 表单输入的 2026-08-18，证明前端确实没有自己再算一遍。
    _stub_backfill(ring, status=200, body={
        "recorded": True, "duplicate": False, "date": "2026-08-19",
        "event": {"id": "evt_2", "dedupeKey": "backfill:y", "type": "session.completed"},
    })
    _open_backfill(ring)
    _fill_form(ring, task_value="t_word", date="2026-08-18", time="23:50", minutes="20")
    ring.page.click("#backfill-submit-btn")
    ring.page.wait_for_function("() => document.getElementById('backfill-message').hidden === false",
                                 timeout=8_000)

    text = ring.page.text_content("#backfill-message").strip()
    assert text == "已记到 2026-08-19。", f"成功分支必须原样回显服务端 date，实得 {text!r}"
    is_error = ring.page.evaluate(
        "() => document.getElementById('backfill-message').classList.contains('is-error')"
    )
    assert is_error is False, "成功分支不该带 is-error 样式"


# ── BF6：失败 → 显示服务端 detail 原文，不是笼统的「失败了」 ────────────
def test_bf6_failure_shows_server_detail_verbatim(ring_idle) -> None:
    ring = ring_idle
    detail = "开始时间加时长不能晚于现在"
    _stub_backfill(ring, status=400, body={"detail": detail})
    _open_backfill(ring)
    _fill_form(ring, task_value="t_word", date="2026-08-18", time="14:30", minutes="30")
    ring.page.click("#backfill-submit-btn")
    ring.page.wait_for_function("() => document.getElementById('backfill-message').hidden === false",
                                 timeout=8_000)

    text = ring.page.text_content("#backfill-message").strip()
    assert text == detail, f"失败必须显示服务端 detail 原文，不许吞成笼统文案，实得 {text!r}"
    is_error = ring.page.evaluate(
        "() => document.getElementById('backfill-message').classList.contains('is-error')"
    )
    assert is_error is True, "失败分支必须带 is-error 样式"


# ── BF7：取消/关闭对话框不发任何请求 ─────────────────────────────────────
def test_bf7_cancel_dialog_sends_no_request(ring_idle) -> None:
    ring = ring_idle
    _open_backfill(ring)
    ring.page.click("#backfill-cancel-btn")
    assert ring.page.evaluate("() => document.getElementById('backfill-dialog').open") is False
    _assert_no_timer_writes(ring)


# ── BF8：页面加载后、未点任何按钮时，补登弹层必须不可见（2026-08-20 回归） ─
# 背景：`.backfill-dialog { display: grid; }`（无 [open] 限定）会无条件压过 UA 样式表
# 的 `dialog:not([open]){display:none}`，导致弹层从加载起就常显在文档流左上角——
# 人类真浏览器上把这个躺在左上角的下拉当成了「补登栏选择不了任务」。
# BF1-BF7 全部测的是「打开之后」的行为，没有一条测「没打开时应当不可见」，
# 因此都抓不住这个 bug。本用例专测未打开态。
def test_bf8_dialog_not_visible_before_any_click(ring_idle) -> None:
    ring = ring_idle
    is_open = ring.page.evaluate("() => document.getElementById('backfill-dialog').open")
    assert is_open is False, "页面加载后弹层不应处于 open 状态"

    display = ring.page.evaluate(
        "() => getComputedStyle(document.getElementById('backfill-dialog')).display"
    )
    rect = ring.page.evaluate(
        "() => { const r = document.getElementById('backfill-dialog')"
        ".getBoundingClientRect(); return {w: r.width, h: r.height}; }"
    )
    assert display == "none" or (rect["w"] == 0 and rect["h"] == 0), (
        f"未打开的 #backfill-dialog 必须不可见（display:none 或零尺寸），"
        f"实得 display={display!r} rect={rect}"
    )
    _assert_no_timer_writes(ring)
