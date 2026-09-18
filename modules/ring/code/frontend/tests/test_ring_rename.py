"""RN1–RN6：计时台改名（2026-09-08）。

判据落在**行为**上，不在 DOM 形状上：
  · 两个入口都能开编辑层：控件区的「改任务名」按钮（主）与圆环里的任务名（次）
  · 开出来的编辑层预填当前名、焦点进输入框
  · 空名不发请求
  · 提交发的是 PATCH /api/core/planner/tasks/{id}，body 只有 name
  · 提交后重新渲染，中心显示换成新名
  · Esc / 取消不发请求
  · **编辑层开着的时候熬过至少一轮轮询仍然开着** —— 这条是本功能最容易坏的
    地方：#running-task-name 的 textContent 每轮都被 renderRunningCenter 覆写，
    所以编辑框只能盖在上面，不能就地替换那个节点。

写入面：本套件没有后端，planner/** 由 conftest 的 planner_write_route 桩掉并
记账（timer/** 仍然是 abort）。一个字节都不落库。
"""

from __future__ import annotations

import json

import pytest

RENAME_URL_TAIL = "/api/core/planner/tasks/t_word"


def _open_editor(harness) -> None:
    harness.page.click("#rename-open-btn")
    harness.page.wait_for_selector("#rename-input", state="visible")


def test_rn1_click_running_name_opens_editor(ring) -> None:
    """RN1：点任务名 → 编辑层顶掉中心显示，预填当前名，焦点在输入框。"""
    page = ring.page
    assert page.locator("#rename-overlay").is_hidden()
    _open_editor(ring)
    assert page.locator("#chrono-center").is_hidden(), "编辑层与中心显示必须互斥"
    assert page.input_value("#rename-input") == "音阶练习"
    assert page.evaluate("() => document.activeElement.id") == "rename-input"


def test_rn2_empty_name_is_refused_without_any_request(ring) -> None:
    """RN2：空名（含纯空白）不发请求，编辑层不关，报错可见。"""
    page = ring.page
    _open_editor(ring)
    page.fill("#rename-input", "   ")
    page.click("#rename-save")
    page.wait_for_selector("#rename-error", state="visible")
    assert page.text_content("#rename-error") == "任务名不能为空"
    assert page.locator("#rename-overlay").is_visible()
    assert ring.planner_writes == [], "空名一个请求都不许发"


def test_rn3_save_sends_patch_with_only_name(ring) -> None:
    """RN3：提交发 PATCH，路径带任务 id，body 只有 name（TaskUpdate 是 extra=forbid）。"""
    page = ring.page
    _open_editor(ring)
    page.fill("#rename-input", "写练琴日志")
    ring.rename_current_task("写练琴日志")  # 后端已改，下一轮轮询该拿到新名
    page.click("#rename-save")
    page.wait_for_selector("#rename-overlay", state="hidden")

    assert len(ring.planner_writes) == 1, ring.planner_writes
    write = ring.planner_writes[0]
    assert write["method"] == "PATCH"
    assert write["url"].endswith(RENAME_URL_TAIL), write["url"]
    assert json.loads(write["body"]) == {"name": "写练琴日志"}
    assert ring.timer_write_attempts == 0, "改名不许碰计时写入面"


def test_rn4_after_save_center_shows_new_name(ring) -> None:
    """RN4：改完立刻重渲染 —— 不等下一轮轮询，中心显示就该是新名。"""
    page = ring.page
    _open_editor(ring)
    page.fill("#rename-input", "写练琴日志")
    ring.rename_current_task("写练琴日志")
    page.press("#rename-input", "Enter")   # 回车与点保存同一条路径
    page.wait_for_selector("#rename-overlay", state="hidden")
    assert page.locator("#chrono-center").is_visible()
    page.wait_for_function(
        "() => document.getElementById('running-task-name')"
        ".textContent.startsWith('写练琴日志')"
    )


@pytest.mark.parametrize("how", ["escape", "cancel-button"])
def test_rn5_cancel_sends_nothing(ring, how: str) -> None:
    """RN5：Esc 和「取消」都只关层，一个请求不发，中心显示保持原名。"""
    page = ring.page
    _open_editor(ring)
    page.fill("#rename-input", "不该被保存")
    if how == "escape":
        page.press("#rename-input", "Escape")
    else:
        page.click("#rename-cancel")
    page.wait_for_selector("#rename-overlay", state="hidden")
    assert ring.planner_writes == []
    assert page.text_content("#running-task-name").startswith("音阶练习")


def test_rn6_editor_survives_a_poll(ring) -> None:
    """RN6：编辑层开着熬过至少一轮轮询 —— 半路输入的字不许被 renderRunningCenter 冲掉。

    这是整个功能的病根所在：#running-task-name 的 textContent 每轮都被重写。
    如果实现走的是「就地把那个 span 换成 input」，这条必红。
    """
    page = ring.page
    _open_editor(ring)
    page.fill("#rename-input", "打到一半的名字")
    before = ring.current_polls
    ring.wait_for_polls(before + 2)
    assert page.locator("#rename-overlay").is_visible()
    assert page.input_value("#rename-input") == "打到一半的名字"
    assert page.locator("#chrono-center").is_hidden()
    assert ring.planner_writes == []


def test_rn7_rename_button_only_exists_while_running(ring, ring_idle) -> None:
    """RN7：「改任务名」按钮只在计时中可见 —— 空闲态没有可改的任务，按钮不该在。

    它白蹭 #controls-row 的运行态显隐（那条 hidden 由 ring-instrument.js 切）。
    如果哪天有人把按钮挪出 #controls-row 又忘了自己写运行态判断，这条会红。
    """
    assert ring.page.locator("#rename-open-btn").is_visible()
    assert ring_idle.page.locator("#rename-open-btn").is_hidden()


def test_rn8_clicking_task_name_also_opens_editor(ring) -> None:
    """RN8：次入口 —— 直接点圆环里的任务名同样能开编辑层（其余用例走按钮）。"""
    page = ring.page
    page.click("#running-task-name")
    page.wait_for_selector("#rename-input", state="visible")
    assert page.input_value("#rename-input") == "音阶练习"
    assert page.locator("#chrono-center").is_hidden()


def test_rn9_rename_button_is_a_real_tap_target(ring) -> None:
    """RN9：显眼要显眼得实在 —— 按钮得够大能点，且不把控件行撑到横向溢出。

    人类的原话是「做一个显眼的改名按钮」。三个按钮挤一行是本轮唯一的排版风险，
    所以判据落在「触控尺寸达标」+「控件区不横向溢出」两条上。
    """
    page = ring.page
    box = page.locator("#rename-open-btn").bounding_box()
    assert box is not None
    assert box["height"] >= 44, box       # --tap 的下限
    assert box["width"] >= 44, box
    overflow = page.evaluate(
        "() => { const r = document.getElementById('controls-row');"
        " return r.scrollWidth - r.clientWidth; }"
    )
    assert overflow <= 0, f"控件行横向溢出 {overflow}px"
