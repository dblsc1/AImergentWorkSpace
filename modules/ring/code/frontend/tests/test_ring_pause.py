"""PA1–PA6：计时台「暂停 / 继续」（2026-09-12 任务单，ring-pause.js）。

暂停是**纯前端**状态：后端没有"暂停"，暂停 = 调现有的 timer/stop（这一段按真实
起止入账）+ 本机 localStorage 记住停的是哪个任务；继续 = 调现有的 timer/start。
所以判据落在两处：发出去的请求（只许是 stop / start 这两条既有接口），和
localStorage 里那条记忆（键与形状见 contracts/timer-ring-visual-v1.md「暂停（纯前端）」，
hive 蜂巢中心格读写同一个键）。

timer/** 在 conftest 里默认是 abort（调了也出不去）。本套件要验"停成功之后"的 UI，
所以在用例里**后注册**一条 route 盖掉它（Playwright 后注册的先匹配）：照样记账、
不出网，只是回 200 并把桩里的 current 翻成对应状态。一个字节都不落库。
"""

from __future__ import annotations

import json

from conftest import CURRENT_IDLE, current_running, open_ring

PAUSE_KEY = "nexus.timer.paused.v1"


def _fake_timer(harness):
    """timer/stop → 翻成空闲；timer/start → 翻成计时中。返回记账列表。"""
    calls: list[tuple[str, str | None]] = []

    def route(r):
        url, body = r.request.url, r.request.post_data
        calls.append((url.split("/api/core")[-1], body))
        if url.endswith("/timer/stop"):
            harness.set_current(CURRENT_IDLE)
            payload = {"running": False, "event": None}
        else:
            harness.set_current(current_running())
            payload = {"running": True, "taskId": "t_word", "startAt": "2026-08-03T10:00:00+08:00"}
        r.fulfill(status=200, content_type="application/json", body=json.dumps(payload))

    harness.page.route("**/api/core/timer/**", route)
    return calls


def _memo(page):
    raw = page.evaluate(f"() => localStorage.getItem('{PAUSE_KEY}')")
    return json.loads(raw) if raw else None


def test_pa1_pause_stops_and_remembers_task(ring) -> None:
    """PA1：点暂停 → 只发一次既有的 timer/stop，停成功后本机记住任务，出现「继续」。"""
    page = ring.page
    calls = _fake_timer(ring)
    assert page.locator("#pause-btn").is_visible()
    page.click("#pause-btn")
    page.wait_for_selector("#paused-row", state="visible")
    assert calls == [("/timer/stop", None)], "暂停只能走既有的 stop 接口"
    memo = _memo(page)
    assert memo["taskId"] == "t_word" and memo["taskName"] == "音阶练习"
    assert memo["projectName"] == "吉他练习" and memo["pausedAt"]
    assert "音阶练习" in page.text_content("#paused-label")
    assert page.locator("#controls-row").is_hidden(), "暂停后后端就是空闲，计时中那排按钮要收掉"


def test_pa2_resume_starts_same_task_and_forgets(ring) -> None:
    """PA2：继续 → timer/start 带同一个 taskId，成功后记忆清掉、回到计时中。"""
    page = ring.page
    calls = _fake_timer(ring)
    page.click("#pause-btn")
    page.wait_for_selector("#paused-row", state="visible")
    page.click("#resume-btn")
    page.wait_for_selector("#controls-row", state="visible")
    assert calls[-1] == ("/timer/start", json.dumps({"taskId": "t_word"}, separators=(",", ":")))
    assert _memo(page) is None
    assert page.locator("#paused-row").is_hidden()


def test_pa3_paused_done_sends_nothing(ring) -> None:
    """PA3：暂停态下点「完成」只是放下记忆 —— 暂停前的时间早已入账，不再发任何请求。"""
    page = ring.page
    calls = _fake_timer(ring)
    page.click("#pause-btn")
    page.wait_for_selector("#paused-row", state="visible")
    n = len(calls)
    page.click("#paused-done-btn")
    page.wait_for_selector("#paused-row", state="hidden")
    assert len(calls) == n, "暂停态的「完成」一个请求都不许发"
    assert _memo(page) is None


def test_pa4_failed_stop_does_not_remember(ring) -> None:
    """PA4：stop 失败（conftest 默认 abort）→ 不记忆。否则「继续」会指向一段没停下的计时。"""
    page = ring.page
    page.click("#pause-btn")
    page.wait_for_selector("#timer-error", state="visible")
    assert ring.timer_write_attempts == 1
    assert _memo(page) is None
    assert page.locator("#paused-row").is_hidden()


def test_pa5_memo_written_by_hive_shows_resume_here(browser, static_base_url, gateway_navbar_available) -> None:
    """PA5：蜂巢那边暂停（同源同键写进 localStorage）→ 计时台空闲态直接出现「继续」。"""
    memo = {"taskId": "t_word", "taskName": "音阶练习", "projectName": "吉他练习",
            "pausedAt": "2026-09-12T08:00:00Z"}
    script = f"localStorage.setItem('{PAUSE_KEY}', {json.dumps(json.dumps(memo, ensure_ascii=False))});"
    with open_ring(browser, static_base_url, with_navbar=gateway_navbar_available,
                   initial_current=CURRENT_IDLE, init_scripts=(script,)) as h:
        h.page.wait_for_selector("#paused-row", state="visible")
        assert "音阶练习 · 吉他练习" in h.page.text_content("#paused-label")


def test_pa6_running_same_task_clears_memo(browser, static_base_url, gateway_navbar_available) -> None:
    """PA6：记忆里的任务已经在别处继续了（current 正在计它）→ 记忆作废，不再挂「继续」。"""
    memo = {"taskId": "t_word", "taskName": "音阶练习", "projectName": "吉他练习",
            "pausedAt": "2026-09-12T08:00:00Z"}
    script = f"localStorage.setItem('{PAUSE_KEY}', {json.dumps(json.dumps(memo, ensure_ascii=False))});"
    with open_ring(browser, static_base_url, with_navbar=gateway_navbar_available,
                   init_scripts=(script,)) as h:
        h.page.wait_for_selector("#elapsed-display", state="attached")
        h.page.wait_for_function(f"() => localStorage.getItem('{PAUSE_KEY}') === null", timeout=5000)
        assert h.page.locator("#paused-row").is_hidden()


def test_pa7_resume_carries_previous_time(ring) -> None:
    """PA7：继续之后走秒接着之前的时间数（人类：「暂停后继续需要继续之前时间」）。
    暂停记忆里的 carriedSeconds 在继续成功后转存进 carry 键，表芯显示 = 累计 + 本段。"""
    page = ring.page
    _fake_timer(ring)
    page.click("#pause-btn")
    page.wait_for_selector("#paused-row", state="visible")
    carried = _memo(page)["carriedSeconds"]
    assert carried > 0
    page.click("#resume-btn")
    page.wait_for_selector("#controls-row", state="visible")
    carry = json.loads(page.evaluate("() => localStorage.getItem('nexus.timer.carry.v1')"))
    assert carry["taskId"] == "t_word" and carry["carriedSeconds"] == carried
    assert carry["startedAt"], "最初的开始时刻要跟着带过去（契约可选字段 startedAt）"
    assert page.evaluate("() => window.ringCarrySeconds()") == carried
    page.wait_for_timeout(1100)
    shown = page.text_content("#elapsed-display")
    h, m, s = (int(x) for x in shown.split(":"))
    assert h * 3600 + m * 60 + s >= carried, f"走秒 {shown} 没有接上之前的 {carried} 秒"


def test_pa8_drop_while_paused_sends_nothing(ring) -> None:
    """PA8：暂停态「取消，不记录」照常显示；点了只放下"继续"、不发请求，并说清已入账的撤不回。"""
    page = ring.page
    calls = _fake_timer(ring)
    page.click("#pause-btn")
    page.wait_for_selector("#paused-row", state="visible")
    assert page.locator("#paused-drop-btn").is_visible()
    n = len(calls)
    page.click("#paused-drop-btn")
    page.wait_for_selector("#paused-row", state="hidden")
    assert len(calls) == n
    assert _memo(page) is None
    assert "撤不回" in page.text_content("#paused-note")
