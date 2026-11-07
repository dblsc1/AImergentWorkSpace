"""/hive/mini（hex-mini.js，桌宠浮窗里的蜂巢缩略页；desktop.pet.v1「四」，tdd-v0.6 FW-M01–M12）。

接口全在浏览器侧 ``page.route()`` 桩掉（live-ask 按契约形状桩）；页面发出的「完成」消息用 message 监听收。
窗口收起的 2 秒计时在外壳（float-core / 桌宠程序），不在页面里。
"""

from __future__ import annotations

import contextlib
import json
from typing import Any, Iterator

from test_hive_focus import TREE, base, browser  # noqa: F401  (fixtures)

ASK = {"blockKey": "chrome · figma.com · 首页改版", "displayName": "figma.com · 首页改版", "focusedSeconds": 372,
       "since": "2026-11-07T10:06:00+08:00", "date": "2026-11-07", "seq": 3}
CARD, HIVE = "#miniCard", "#miniHive"


class Mini:
    def __init__(self, page: Any, ask: Any, assign: list[int], answer: int, status: int) -> None:
        self.page, self.ask, self.assign_codes, self.answer_code, self.status = page, ask, assign, answer, status
        self.requests: list[dict[str, Any]] = []

    def route(self, route: Any) -> None:
        req = route.request
        path = req.url.split("/api/core/", 1)[1].split("?")[0]
        self.requests.append({"method": req.method, "path": path, "headers": req.headers,
                              "body": json.loads(req.post_data) if req.post_data else None})
        if self.status != 200:
            return route.fulfill(status=self.status, content_type="application/json", body="{}")
        code, body = 200, {}
        if path == "views/tree":
            body = TREE
        elif path == "activity/live-ask":
            body = {"ask": self.ask}
        elif path == "activity/blocks/assign":
            code = self.assign_codes.pop(0) if len(self.assign_codes) > 1 else self.assign_codes[0]
        elif path == "activity/live-ask/answer":
            code = self.answer_code
        route.fulfill(status=code, content_type="application/json", body=json.dumps(body, ensure_ascii=False))

    def posts(self) -> list[dict[str, Any]]:
        return [r for r in self.requests if r["method"] == "POST"]

    def done(self) -> int:
        return self.page.evaluate("() => window.__done")

    def drag_card_to(self, project_id: str | None) -> None:
        box = self.page.locator(CARD).bounding_box()
        if project_id:
            t = self.page.locator(f'{HIVE} [data-project-id="{project_id}"]').bounding_box()
            x, y = t["x"] + t["width"] / 2, t["y"] + t["height"] / 2
        else:
            x, y = 20, 400
        self.page.mouse.move(box["x"] + 20, box["y"] + 10)
        self.page.mouse.down()
        self.page.mouse.move(x, y, steps=6)
        self.page.mouse.up()


@contextlib.contextmanager
def open_mini(browser: Any, base: str, *, ask: Any = ASK, assign: list[int] | None = None, answer: int = 200,
              status: int = 200) -> Iterator[Mini]:
    ctx = browser.new_context(viewport={"width": 360, "height": 420})
    page = ctx.new_page()
    page.add_init_script("window.__done = 0; window.addEventListener('message', e => { if (e.data && e.data.type === 'hive-mini-done') window.__done++; });")
    m = Mini(page, ask, assign or [200], answer, status)
    page.route("**/api/core/**", m.route)
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    try:
        page.goto(base + "/mini.html")
        page.wait_for_load_state("networkidle")
        yield m
        assert not errors, errors
    finally:
        ctx.close()


def test_card_shows_name_and_dwell_but_not_the_block_key(browser, base) -> None:  # noqa: F811
    with open_mini(browser, base) as m:
        text = m.page.locator(CARD).inner_text()
        assert "figma.com · 首页改版" in text and "6 分钟" in text
        assert "chrome · figma" not in m.page.content(), "原始块名不进页面"          # FW-M01


def test_hive_has_only_project_names(browser, base) -> None:  # noqa: F811
    with open_mini(browser, base) as m:
        cells = m.page.locator(f"{HIVE} [data-project-id]")
        assert cells.count() == 3
        assert sorted(cells.evaluate_all("ns => ns.map(n => n.textContent)")) == ["录音棚", "数学", "钢琴"]
        assert m.page.locator(f"{HIVE} li, {HIVE} .hm-dot").count() == 0, "无任务、无时长"   # FW-M02


def test_drag_to_project_assigns_and_signals_done(browser, base) -> None:  # noqa: F811
    with open_mini(browser, base) as m:
        m.drag_card_to("p_piano")
        m.page.wait_for_function("window.__done === 1")
        assert m.posts() == [{"method": "POST", "path": "activity/blocks/assign", "headers": m.posts()[0]["headers"],
                              "body": {"date": "2026-11-07", "blockKey": ASK["blockKey"], "ifSeq": 3,
                                       "projectId": "p_piano", "remember": True}}]   # FW-M03


def test_skip_answers_unmanaged_and_signals_done(browser, base) -> None:  # noqa: F811
    with open_mini(browser, base) as m:
        m.page.get_by_role("button", name="不管它").click()
        m.page.wait_for_function("window.__done === 1")
        assert m.posts()[0]["body"] == {"blockKey": ASK["blockKey"], "answer": "unmanaged"}
        assert m.posts()[0]["path"] == "activity/live-ask/answer"                      # FW-M05


def test_assign_409_shows_reason_keeps_open_and_card_returns(browser, base) -> None:  # noqa: F811
    with open_mini(browser, base, assign=[409]) as m:
        m.drag_card_to("p_math")
        m.page.wait_for_selector("#miniNote:not([hidden])")
        assert m.done() == 0
        assert m.page.locator(CARD).evaluate("n => n.style.transform") == ""
        assert m.page.get_by_role("button", name="不管它").is_visible()                 # FW-M06


def test_assign_409_retries_once_with_new_seq(browser, base) -> None:  # noqa: F811
    with open_mini(browser, base, assign=[409, 200]) as m:
        m.ask = {**ASK, "seq": 4}      # 重新取 live-ask 时块还在、seq 变了
        m.drag_card_to("p_math")
        m.page.wait_for_function("window.__done === 1")
        assigns = [r["body"]["ifSeq"] for r in m.posts()]
        assert assigns == [3, 4]


def test_assign_409_block_gone_shows_left_and_signals_done(browser, base) -> None:  # noqa: F811
    with open_mini(browser, base, assign=[409]) as m:
        m.ask = None
        m.drag_card_to("p_math")
        m.page.wait_for_function("window.__done === 1")
        assert "这个窗口已经离开" in m.page.locator(CARD).inner_text()


def test_answer_404_shows_reason_keeps_open(browser, base) -> None:  # noqa: F811
    with open_mini(browser, base, answer=404) as m:
        m.page.get_by_role("button", name="不管它").click()
        m.page.wait_for_selector("#miniNote:not([hidden])")
        assert m.done() == 0                                                            # FW-M07


def test_not_logged_in_shows_login_only(browser, base) -> None:  # noqa: F811
    with open_mini(browser, base, status=401) as m:
        m.page.wait_for_selector("a.hmi-login")
        assert m.page.locator(f"{CARD}, {HIVE}").count() == 0                           # FW-M08


def test_requests_use_session_not_device_token(browser, base) -> None:  # noqa: F811
    with open_mini(browser, base) as m:
        m.page.get_by_role("button", name="不管它").click()
        m.page.wait_for_function("window.__done === 1")
        assert m.requests
        assert all("authorization" not in r["headers"] for r in m.requests)             # FW-M09


def test_ask_null_shows_gone_without_drag_card(browser, base) -> None:  # noqa: F811
    with open_mini(browser, base, ask=None) as m:
        assert "这个窗口已经离开" in m.page.locator(CARD).inner_text()
        assert m.page.get_by_role("button", name="不管它").count() == 0                 # FW-M10
        m.drag_card_to("p_math")
        assert m.posts() == []


def test_shell_left_message_shows_gone(browser, base) -> None:  # noqa: F811
    with open_mini(browser, base) as m:
        m.page.evaluate("() => window.postMessage({type: 'hive-mini-card', state: 'left'}, '*')")
        m.page.wait_for_function("document.querySelector('#miniCard').textContent.includes('这个窗口已经离开')")
        m.drag_card_to("p_math")
        assert m.posts() == []


def test_drop_on_blank_sends_nothing_and_card_returns(browser, base) -> None:  # noqa: F811
    with open_mini(browser, base) as m:
        m.drag_card_to(None)
        assert m.posts() == [] and m.done() == 0
        assert m.page.locator(CARD).evaluate("n => n.style.transform") == ""            # FW-M12
