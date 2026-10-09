"""泳道偏好的界面（nexus-core v2.22，ring-lanes.js + 共享 lanes.js）：⋯ 菜单、置顶标记、长按拖动换位、已隐藏。

同本套件其余文件：真浏览器、接口全在浏览器侧桩掉。偏好端点由 PrefsServer 桩成「有状态的小后端」：收到 PUT 就改它手里
那份泳道响应（所以随后的轮询读到的是改过的），并记下每次调用。判据落在行为上：画了什么、顺序、发了什么请求、
失败时退回、拖动时轮询碰不碰它、减少动态效果时动不动。
"""

from __future__ import annotations

import contextlib
import copy
import json
from datetime import datetime, timezone
import sys
from typing import Any

import pytest
from conftest import COCKPIT_STATIC_DIR
from playwright.sync_api import Route
from test_ring_lanes import open_lanes

sys.path.insert(0, str(COCKPIT_STATIC_DIR.parent / "tests"))
import lanes_fixtures as fx  # noqa: E402

#: 服务端排好的先后（与 test_ring_lanes 里前端自己排出来的一致）：等你的 plot → 干活的 old-job / garden / codex → 空闲 tests → 已结束 docs
SERVED = ["run_c", "run_e", "run_a", "run_b", "run_f", "run_d"]


def served_body() -> dict[str, Any]:
    body = copy.deepcopy(fx.LANES_FULL)
    for r in body["agents"]:
        r.update(pinned=False, manualOrder=None, rank=SERVED.index(r["runId"]))
    body.update(hiddenAgents=[], hiddenWaiting=0)
    return body


class PrefsServer:
    """桩住 views/lanes 与 lanes/prefs/**。fail=True：偏好写入回 500。"""

    def __init__(self, body: dict[str, Any]) -> None:
        self.body, self.calls, self.fail, self.lanes_gets = body, [], False, 0
        self.original = copy.deepcopy(body["agents"])

    def _order(self) -> list[dict]:
        return sorted(self.body["agents"], key=lambda r: r["rank"])

    def _renumber(self, rows: list[dict]) -> None:
        for i, r in enumerate(rows):
            r["rank"] = i

    def route(self, route: Route) -> None:
        req = route.request
        if "/views/lanes" in req.url:
            self.lanes_gets += 1
            route.fulfill(status=200, content_type="application/json", body=json.dumps(self.body, ensure_ascii=False))
            return
        payload = json.loads(req.post_data) if req.post_data else None
        self.calls.append((req.method, req.url.split("/lanes/prefs")[1], payload))
        if self.fail:
            route.fulfill(status=500, content_type="application/json", body='{"detail":"x"}')
            return
        rows = self._order()
        if payload and "pinned" in payload:
            for r in rows:
                if (r["agent"], r["label"] or "") == (payload["agent"], payload["label"]):
                    r["pinned"] = payload["pinned"]
            top = [r for r in rows if r["pinned"] and not r["endAt"]]
            self._renumber(top + [r for r in rows if r not in top])
        elif payload and "hidden" in payload:
            hit = [r for r in rows if (r["agent"], r["label"] or "") == (payload["agent"], payload["label"])]
            if payload["hidden"]:
                self.body["agents"] = [r for r in rows if r not in hit]
                self.body["hiddenAgents"].append({"agent": payload["agent"], "label": payload["label"], "live": True,
                                                  "phase": "waiting_input" if payload["label"] == "plot" else "working"})
                self.body["hiddenWaiting"] = sum(h["phase"] == "waiting_input" for h in self.body["hiddenAgents"])
            else:
                self.body["hiddenAgents"] = [h for h in self.body["hiddenAgents"] if h["label"] != payload["label"]]
                self.body["hiddenWaiting"] = 0
                back = [copy.deepcopy(r) for r in self.original if (r["agent"], r["label"] or "") == (payload["agent"], payload["label"])]
                self._renumber(sorted(rows + back, key=lambda r: r["rank"]))
                self.body["agents"] = rows + back
        elif payload and "index" in payload:
            mov = [r for r in rows if not r["endAt"] and not r["pinned"]]
            me = next(r for r in mov if r["runId"] == payload["runId"])
            slots = [rows.index(r) for r in mov]
            mov.remove(me)
            mov.insert(payload["index"], me)
            for pos, r in zip(slots, mov):
                rows[pos] = r
            me["manualOrder"] = payload["index"]
            self._renumber(rows)
        route.fulfill(status=200, content_type="application/json", body="{}")


@contextlib.contextmanager
def open_prefs(browser, base, *, body=None, clock=False, **kw):
    server = PrefsServer(body or served_body())
    with open_lanes(browser, base, server.body, clock=clock, **kw) as (page, _stub):
        page.route("**/api/core/lanes/prefs/**", server.route)
        page.route("**/api/core/views/lanes*", server.route)
        page.reload()
        page.wait_for_selector("#lanes-view .hcl-card[data-run-id]")
        if clock:  # 之后只有 run_for 推时间
            page.clock.pause_at(datetime.fromtimestamp(page.evaluate("() => Date.now()") / 1000 + 1, timezone.utc))
        yield page, server


def order(page) -> list[str]:
    return page.eval_on_selector_all("#lanes-view .hcl-card[data-run-id]", "ns => ns.map(n => n.dataset.runId)")


def waits(page, cond, tries: int = 40) -> None:
    for _ in range(tries):
        if cond():
            return
        page.wait_for_timeout(50)
    raise AssertionError("条件一直没成立")


def centre(page, run_id: str) -> tuple[float, float]:
    b = page.locator(f"[data-run-id={run_id}]").bounding_box()
    return b["x"] + b["width"] / 2, b["y"] + b["height"] / 2


def drag(page, run_id: str, to_y: float, hold: int = 450) -> None:
    x, y = centre(page, run_id)
    page.mouse.move(x, y)
    page.mouse.down()
    page.clock.run_for(hold)
    page.mouse.move(x, y + (to_y - y) / 2)
    page.mouse.move(x, to_y)


# ─────────────────────────────────────────── 菜单：置顶 / 不再显示 / 恢复


def test_menu_pin_marks_the_card_moves_it_first_and_unpin_removes_the_mark(browser, static_base_url) -> None:
    with open_prefs(browser, static_base_url) as (page, server):
        assert order(page) == SERVED  # 代理卡按服务端的 rank（人那张卡不带 runId）
        page.click("[data-run-id=run_a] .hcl-kebab")
        assert page.get_attribute("[data-run-id=run_a] .hcl-kebab", "aria-expanded") == "true"
        assert page.eval_on_selector_all(".hcl-menu-item", "ns => ns.map(n => n.textContent)") == ["置顶", "不再显示", "上移", "下移"]
        page.click(".hcl-menu-item:text('置顶')")
        waits(page, lambda: server.calls)
        assert server.calls == [("PUT", "/agent", {"agent": "claude-code", "label": "garden", "pinned": True})]
        assert order(page)[0] == "run_a"
        assert page.text_content("[data-run-id=run_a] .hcl-pinmark") == "置顶"
        assert page.query_selector("[data-run-id=run_c] .hcl-pinmark") is None
        # 置顶的卡不能手动排位：菜单里没有上移 / 下移
        page.click("[data-run-id=run_a] .hcl-kebab")
        assert page.eval_on_selector_all(".hcl-menu-item", "ns => ns.map(n => n.textContent)") == ["取消置顶", "不再显示"]
        page.click(".hcl-menu-item:text('取消置顶')")
        waits(page, lambda: len(server.calls) == 2)
        assert server.calls[1][2]["pinned"] is False
        assert page.query_selector("[data-run-id=run_a] .hcl-pinmark") is None


def test_hide_moves_the_card_into_the_hidden_row_and_restore_brings_it_back(browser, static_base_url) -> None:
    with open_prefs(browser, static_base_url) as (page, server):
        assert page.query_selector("details.hcl-hidden") is None  # 没有藏起来的就没有这一行
        page.click("[data-run-id=run_c] .hcl-kebab")
        page.click(".hcl-menu-item:text('不再显示')")
        waits(page, lambda: server.calls)
        assert server.calls[0] == ("PUT", "/agent", {"agent": "claude-code", "label": "plot", "hidden": True, "unverified": False})
        assert "run_c" not in order(page)
        # 标题上的红绿灯只数画出来的；藏起来的 plot 在等你 → 只在已隐藏那一行轻轻提一句
        assert "在等你" not in page.text_content("#lanes-state")
        assert page.text_content("details.hcl-hidden > summary") == "已隐藏 (1) · 1 个在等你"
        assert page.get_attribute("details.hcl-hidden", "open") is None
        page.click("details.hcl-hidden > summary")
        assert page.text_content(".hcl-hidden-item .hcl-hidden-name") == "plot"
        assert page.text_content(".hcl-hidden-state") == "在等你"
        page.click(".hcl-hidden-restore")
        waits(page, lambda: len(server.calls) == 2)
        assert server.calls[1] == ("PUT", "/agent", {"agent": "claude-code", "label": "plot", "hidden": False, "unverified": False})
        waits(page, lambda: "run_c" in order(page))  # 恢复显示没法乐观地画：卡要等服务端重拉才回来
        assert order(page) == SERVED and page.query_selector("details.hcl-hidden") is None


def test_failed_request_rolls_the_screen_back_and_says_so(browser, static_base_url) -> None:
    with open_prefs(browser, static_base_url) as (page, server):
        server.fail = True
        before = order(page)
        page.click("[data-run-id=run_e] .hcl-kebab")
        page.click(".hcl-menu-item:text('不再显示')")
        waits(page, lambda: server.calls)
        waits(page, lambda: "没有成功" in page.text_content("#lanes-state"))
        assert order(page) == before and page.query_selector("details.hcl-hidden") is None


def test_old_backend_without_hidden_agents_has_no_entry_points(browser, static_base_url) -> None:
    body = copy.deepcopy(fx.LANES_FULL)
    with open_lanes(browser, static_base_url, body) as (page, _):
        page.wait_for_selector("#lanes-view .hcl-card[data-run-id]")
        assert page.query_selector(".hcl-kebab") is None and page.query_selector("details.hcl-hidden") is None


# ─────────────────────────────────────────── 键盘：菜单里的上移 / 下移


def test_keyboard_menu_move_up_and_down_and_escape(browser, static_base_url) -> None:
    with open_prefs(browser, static_base_url) as (page, server):
        page.focus("[data-run-id=run_a] .hcl-kebab")
        page.keyboard.press("Enter")
        assert page.evaluate("() => document.activeElement.textContent") == "置顶"
        page.keyboard.press("ArrowDown")
        page.keyboard.press("ArrowDown")
        assert page.evaluate("() => document.activeElement.textContent") == "上移"
        page.keyboard.press("Escape")  # 关菜单，焦点回到 ⋯
        assert page.query_selector(".hcl-menu") is None
        assert page.evaluate("() => document.activeElement.className") == "hcl-kebab"
        assert server.calls == []
        page.keyboard.press("Enter")
        page.keyboard.press("End")  # 最后一项 = 下移
        page.keyboard.press("ArrowUp")
        page.keyboard.press("Enter")  # 上移
        waits(page, lambda: server.calls)
        assert server.calls == [("PUT", "/order", {"runId": "run_a", "index": 1})]
        assert order(page)[:3] == ["run_c", "run_a", "run_e"]
        # 第一张没有「上移」，最后一张没有「下移」
        page.click("[data-run-id=run_c] .hcl-kebab")
        assert "上移" not in page.eval_on_selector_all(".hcl-menu-item", "ns => ns.map(n => n.textContent)")
        page.keyboard.press("Escape")
        page.click("[data-run-id=run_f] .hcl-kebab")
        assert "下移" not in page.eval_on_selector_all(".hcl-menu-item", "ns => ns.map(n => n.textContent)")


def test_move_uses_the_rise_motion_unless_reduced(browser, static_base_url) -> None:
    with open_prefs(browser, static_base_url) as (page, server):
        page.click("[data-run-id=run_a] .hcl-kebab")
        page.click(".hcl-menu-item:text('上移')")
        waits(page, lambda: server.calls)
        assert page.evaluate("() => document.getAnimations().filter(a => a.id === 'hcl-move').length") > 0
    with open_prefs(browser, static_base_url, reduced_motion="reduce") as (page, server):
        page.click("[data-run-id=run_a] .hcl-kebab")
        page.click(".hcl-menu-item:text('上移')")
        waits(page, lambda: server.calls)
        assert order(page)[:3] == ["run_c", "run_a", "run_e"]
        assert page.evaluate("() => document.getAnimations().filter(a => a.id === 'hcl-move').length") == 0


# ─────────────────────────────────────────── 长按拖动


def test_long_press_drag_reorders_and_persists_the_drop_index(browser, static_base_url) -> None:
    with open_prefs(browser, static_base_url, clock=True) as (page, server):
        # 把 plot(run_c) 拖到 garden(run_a) 那一格：它前面该有 old-job、garden 两张
        drag(page, "run_c", centre(page, "run_a")[1] + 4)
        assert "is-dragging" in page.get_attribute("[data-run-id=run_c]", "class")
        assert "is-yielding" in page.get_attribute("[data-run-id=run_e]", "class")
        page.mouse.up()
        waits(page, lambda: server.calls)
        assert server.calls == [("PUT", "/order", {"runId": "run_c", "index": 2})]
        assert order(page)[:3] == ["run_e", "run_a", "run_c"]
        assert page.query_selector(".is-dragging") is None and page.evaluate("() => document.body.classList.contains('hcl-dragging')") is False


def test_short_press_is_a_plain_click_and_moving_early_cancels(browser, static_base_url) -> None:
    with open_prefs(browser, static_base_url, clock=True) as (page, server):
        x, y = centre(page, "run_c")
        page.mouse.move(x, y)
        page.mouse.down()
        page.clock.run_for(100)  # 不到 400 毫秒
        page.mouse.up()
        page.clock.run_for(1000)
        assert page.query_selector(".is-dragging") is None and server.calls == []
        # 按住前先动了（在滚动 / 选字）：不进入拖动
        page.mouse.move(x, y)
        page.mouse.down()
        page.mouse.move(x, y + 30)
        page.clock.run_for(600)
        page.mouse.move(x, y + 90)
        assert page.query_selector(".is-dragging") is None
        page.mouse.up()
        assert server.calls == [] and order(page) == SERVED


def test_escape_cancels_the_drag_without_a_request(browser, static_base_url) -> None:
    with open_prefs(browser, static_base_url, clock=True) as (page, server):
        drag(page, "run_c", centre(page, "run_b")[1])
        assert page.query_selector(".is-dragging") is not None
        page.keyboard.press("Escape")
        assert page.query_selector(".is-dragging") is None
        assert set(page.eval_on_selector_all("#lanes-view .hcl-card", "ns => ns.map(n => n.style.transform)")) == {""}
        page.mouse.up()
        page.wait_for_timeout(100)
        assert server.calls == [] and order(page) == SERVED


def test_a_poll_does_not_disturb_a_drag_in_progress(browser, static_base_url) -> None:
    with open_prefs(browser, static_base_url, clock=True) as (page, server):
        drag(page, "run_c", centre(page, "run_a")[1] + 4)
        page.evaluate("() => { window.__dragged = document.querySelector('[data-run-id=run_c]'); }")
        n = server.lanes_gets
        page.clock.run_for(15_500)  # 一次轮询
        waits(page, lambda: server.lanes_gets > n)  # 确实拉了
        assert page.evaluate("() => window.__dragged.isConnected && window.__dragged.classList.contains('is-dragging')")
        page.mouse.up()
        waits(page, lambda: server.calls)
        assert server.calls[0][2] == {"runId": "run_c", "index": 2}


def test_open_menu_survives_a_poll(browser, static_base_url) -> None:
    with open_prefs(browser, static_base_url, clock=True) as (page, server):
        page.click("[data-run-id=run_a] .hcl-kebab")
        page.clock.run_for(15_500)
        page.wait_for_timeout(150)
        assert page.query_selector(".hcl-menu") is not None
        page.keyboard.press("Escape")  # 收尾时补画
        page.wait_for_timeout(100)
        assert page.query_selector(".hcl-menu") is None


def test_reduced_motion_gives_the_dragged_cards_no_transition(browser, static_base_url) -> None:
    with open_prefs(browser, static_base_url, clock=True, reduced_motion="reduce") as (page, server):
        drag(page, "run_c", centre(page, "run_a")[1] + 4)
        assert page.evaluate("() => getComputedStyle(document.querySelector('[data-run-id=run_e]')).transitionDuration") == "0s"
        page.keyboard.press("Escape")
    with open_prefs(browser, static_base_url, clock=True) as (page, server):
        drag(page, "run_c", centre(page, "run_a")[1] + 4)
        assert page.evaluate("() => getComputedStyle(document.querySelector('[data-run-id=run_e]')).transitionDuration") != "0s"
        page.keyboard.press("Escape")


def test_unverified_run_card_only_offers_hide_and_is_not_draggable(browser, static_base_url) -> None:
    body = served_body()
    next(r for r in body["agents"] if r["runId"] == "run_f")["unverified"] = True
    with open_prefs(browser, static_base_url, body=body) as (page, server):
        page.click("[data-run-id=run_f] .hcl-kebab")
        assert page.eval_on_selector_all(".hcl-menu-item", "ns => ns.map(n => n.textContent)") == ["不再显示"]
        assert "is-movable" not in page.get_attribute("[data-run-id=run_f]", "class")
        page.click(".hcl-menu-item")
        waits(page, lambda: server.calls)
        assert server.calls[0][2]["unverified"] is True
