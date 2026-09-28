"""「问问助手」面板（ring-chat.js，contracts/agent.chat.v1）。

同本套件其余文件：真浏览器、/api/agent/ 全在浏览器侧桩掉。判据落在行为上：发了什么请求、
显示了什么（且只当文本）、面板在不在、窄屏会不会横滚。
"""

from __future__ import annotations

import contextlib
import json
import re
from typing import Any, Iterator

import pytest
from conftest import CURRENT_IDLE, PAGE_NAME, RingHarness, _install_stub_routes
from playwright.sync_api import Browser, Route


def sse(*events: tuple[str, dict]) -> str:
    return "".join(f"event: {t}\ndata: {json.dumps(d, ensure_ascii=False)}\n\n" for t, d in events)


REPLY = sse(("start", {"userMessageId": "m_u", "messageId": "m_a"}),
            ("tool", {"name": "get_task_tree", "status": "running"}),
            ("tool", {"name": "get_task_tree", "status": "done"}),
            ("future_event", {"whatever": 1}),
            ("delta", {"text": "这周 <b>吉他</b>"}),
            ("delta", {"text": " 最多。"}),
            ("done", {"messageId": "m_a", "reason": "end"})) + ": ping\n\n"


class ChatStub:
    def __init__(self, health: Any = None, sessions: list[dict] | None = None) -> None:
        self.health = {"status": "ok", "configured": True} if health is None else health
        self.sessions = sessions if sessions is not None else []
        self.messages: dict[str, list[dict]] = {s["id"]: [] for s in self.sessions}
        self.calls: list[tuple[str, str, Any, str | None]] = []
        self.reply: str | tuple[int, dict] = REPLY
        self.hold: list[Route] = []   # hold_reply=True 时发消息的请求先挂着，测试自己决定何时回
        self.hold_reply = False

    def route(self, route: Route) -> None:
        req = route.request
        path = req.url.split("/api/agent/", 1)[1].split("?")[0]
        body = json.loads(req.post_data) if req.post_data else None
        self.calls.append((req.method, path, body, req.headers.get("content-type")))
        j = lambda code, obj: route.fulfill(status=code, content_type="application/json",  # noqa: E731
                                            body=json.dumps(obj, ensure_ascii=False))
        if path == "health":
            if isinstance(self.health, int):
                return route.fulfill(status=self.health, body="nope")
            if isinstance(self.health, str):
                return route.fulfill(status=200, content_type="text/html", body=self.health)
            return j(200, self.health)
        if path == "sessions" and req.method == "GET":
            return j(200, {"items": self.sessions})
        if path == "sessions" and req.method == "POST":
            s = {"id": f"ses_{len(self.sessions) + 1}", "title": (body or {}).get("title"),
                 "createdAt": "2026-09-28T01:02:00+00:00", "updatedAt": "2026-09-28T01:02:00+00:00", "busy": False}
            self.sessions.insert(0, s)
            self.messages[s["id"]] = []
            return j(201, s)
        m = re.fullmatch(r"sessions/([^/]+)(?:/(messages|cancel))?", path)
        sid, action = m.group(1), m.group(2)
        if action == "messages":
            if self.hold_reply:
                self.hold.append(route)
                return None
            if isinstance(self.reply, tuple):
                return j(*self.reply)
            return route.fulfill(status=200, content_type="text/event-stream", body=self.reply)
        if action == "cancel":
            return route.fulfill(status=204)
        if req.method == "DELETE":
            self.sessions = [s for s in self.sessions if s["id"] != sid]
            return route.fulfill(status=204)
        s = next(s for s in self.sessions if s["id"] == sid)
        return j(200, {"session": s, "messages": self.messages[sid], "truncated": False})


@contextlib.contextmanager
def open_page(browser: Browser, base: str, stub: ChatStub, *, width: int = 1100,
              theme: str | None = None) -> Iterator[RingHarness]:
    context = browser.new_context(viewport={"width": width, "height": 900}, timezone_id="Asia/Shanghai")
    page = context.new_page()
    harness = RingHarness(page, CURRENT_IDLE)
    _install_stub_routes(page, harness)
    page.route(re.compile(r"/api/agent/"), stub.route)
    page.goto(f"{base}/{PAGE_NAME}")
    page.wait_for_selector("#task-select", state="attached")
    if theme:
        page.evaluate(f"document.documentElement.dataset.theme = {theme!r}")
    try:
        yield harness
    finally:
        context.close()


SESSIONS = [{"id": "ses_old", "title": "上周回顾", "createdAt": "2026-09-20T01:00:00+00:00",
             "updatedAt": "2026-09-21T01:00:00+00:00", "busy": False}]


@pytest.mark.parametrize("health", [404, 502, "<html>login</html>"])
def test_hidden_without_chat_backend(browser, static_base_url, health):
    with open_page(browser, static_base_url, ChatStub(health=health)) as h:
        h.page.wait_for_timeout(400)
        assert h.page.is_hidden("#chat-panel")


def test_unconfigured_explains_env(browser, static_base_url):
    with open_page(browser, static_base_url, ChatStub(health={"status": "ok", "configured": False})) as h:
        page = h.page
        page.wait_for_selector("#chat-panel", state="visible")
        assert "AGENT_API_KEY" in page.inner_text("#chat-message")
        assert page.is_disabled("#chat-input") and page.is_disabled("#chat-send")


def test_send_streams_reply_as_text(browser, static_base_url):
    stub = ChatStub()
    with open_page(browser, static_base_url, stub) as h:
        page = h.page
        page.wait_for_selector("#chat-panel", state="visible")
        page.fill("#chat-input", "这周哪个项目花时间最多？")
        page.press("#chat-input", "Enter")
        page.wait_for_selector(".chat-assistant")
        page.wait_for_function("!document.querySelector('#chat-send').hidden")
        # 没有会话时先建一个，标题取问题开头
        assert ("POST", "sessions", {"title": "这周哪个项目花时间最多？"}, "application/json") in stub.calls
        send = [c for c in stub.calls if c[1].endswith("/messages")]
        assert send == [("POST", "sessions/ses_1/messages", {"text": "这周哪个项目花时间最多？"}, "application/json")]
        assert page.inner_text(".chat-user") == "这周哪个项目花时间最多？"
        # 模型输出只当文本：<b> 原样显示，不变成元素
        assert page.inner_text(".chat-assistant") == "这周 <b>吉他</b> 最多。"
        assert page.locator("#chat-log b").count() == 0
        assert page.is_hidden("#chat-tool") and page.is_hidden("#chat-message")
        assert page.input_value("#chat-input") == ""
        assert page.eval_on_selector("#chat-session", "e => e.selectedOptions[0].textContent") == "这周哪个项目花时间最多？"


def test_errors_show_detail(browser, static_base_url):
    stub = ChatStub(sessions=list(SESSIONS))
    stub.reply = sse(("start", {"userMessageId": "u", "messageId": "a"}),
                     ("error", {"code": "upstream", "detail": "模型服务拒绝了密钥", "correlationId": "c_1"}))
    with open_page(browser, static_base_url, stub) as h:
        page = h.page
        page.wait_for_selector("#chat-panel", state="visible")
        page.fill("#chat-input", "hi")
        page.click("#chat-send")
        page.wait_for_selector("#chat-message.is-error")
        assert page.inner_text("#chat-message") == "模型服务拒绝了密钥（c_1）"
        stub.reply = (409, {"detail": "这个会话正在生成回答"})
        page.fill("#chat-input", "again")
        page.click("#chat-send")
        page.wait_for_function("document.querySelector('#chat-message').textContent.includes('正在生成')")
        assert page.input_value("#chat-input") == "again"      # 没发出去，字留着


def test_stop_calls_cancel(browser, static_base_url):
    stub = ChatStub(sessions=list(SESSIONS))
    stub.hold_reply = True
    with open_page(browser, static_base_url, stub) as h:
        page = h.page
        page.wait_for_selector("#chat-panel", state="visible")
        page.fill("#chat-input", "slow")
        page.click("#chat-send")
        page.wait_for_selector("#chat-stop", state="visible")
        assert page.is_hidden("#chat-send") and page.is_disabled("#chat-input") and page.is_disabled("#chat-session")
        page.click("#chat-stop")
        page.wait_for_function("1")
        for _ in range(50):
            if any(c[1] == "sessions/ses_old/cancel" for c in stub.calls):
                break
            page.wait_for_timeout(50)
        assert ("POST", "sessions/ses_old/cancel", {}, "application/json") in stub.calls
        stub.hold.pop().fulfill(status=200, content_type="text/event-stream", body=sse(
            ("start", {"userMessageId": "u", "messageId": "a"}), ("delta", {"text": "半句"}),
            ("done", {"messageId": "a", "reason": "cancelled"})))
        page.wait_for_selector(".chat-note")
        assert page.inner_text(".chat-note") == "（已停止）"
        assert page.is_visible("#chat-send") and page.is_hidden("#chat-stop")


def test_sessions_new_switch_delete(browser, static_base_url):
    stub = ChatStub(sessions=list(SESSIONS))
    stub.messages["ses_old"] = [{"id": "1", "role": "user", "text": "上周呢", "createdAt": "…"},
                                {"id": "2", "role": "assistant", "text": "<i>很多</i>", "createdAt": "…"}]
    with open_page(browser, static_base_url, stub) as h:
        page = h.page
        page.wait_for_selector(".chat-assistant")
        assert page.inner_text(".chat-assistant") == "<i>很多</i>"
        page.click("#chat-new")
        page.wait_for_function("document.querySelector('#chat-session').options.length === 2")
        assert page.input_value("#chat-session") == "ses_2"
        assert page.locator(".chat-msg").count() == 0
        # 无标题的会话按创建时刻（本地时区）起名
        assert page.eval_on_selector("#chat-session", "e => e.selectedOptions[0].textContent") == "09-28 09:02 的对话"
        page.select_option("#chat-session", "ses_old")
        page.wait_for_selector(".chat-assistant")
        page.once("dialog", lambda d: d.accept())
        page.click("#chat-delete")
        page.wait_for_function("document.querySelector('#chat-session').options.length === 1")
        assert ("DELETE", "sessions/ses_old", None, None) in stub.calls


@pytest.mark.parametrize("width", [320, 390])
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_narrow_no_horizontal_scroll(browser, static_base_url, width, theme):
    stub = ChatStub(sessions=list(SESSIONS))
    stub.messages["ses_old"] = [{"id": "1", "role": "user", "text": "x" * 300, "createdAt": "…"},
                                {"id": "2", "role": "assistant", "text": "很长的回答" * 60, "createdAt": "…"}]
    with open_page(browser, static_base_url, stub, width=width, theme=theme) as h:
        page = h.page
        page.wait_for_selector(".chat-assistant")
        overflow = page.evaluate(
            "() => [...document.querySelectorAll('#chat-panel, #chat-panel *')]"
            ".filter(e => e.getBoundingClientRect().right > document.documentElement.clientWidth + 0.5)"
            ".map(e => e.id || e.className)")
        assert overflow == []
        assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
        bg = page.eval_on_selector("#chat-panel", "e => getComputedStyle(e).backgroundColor")
        assert bg == ("rgb(21, 28, 46)" if theme == "dark" else "rgb(255, 255, 255)")


def test_parse_sse(browser, static_base_url):
    with open_page(browser, static_base_url, ChatStub(health=404)) as h:
        r = h.page.evaluate("""() => {
          const P = window.ringChat.parseSSE;
          const a = P('event: delta\\ndata: {"text":"a"}\\n\\nevent: del');
          const b = P(a.rest + 'ta\\r\\ndata: {"text":"b"}\\r\\n\\r\\n: ping\\n\\nevent: x\\ndata: {bad\\n\\n');
          const c = P('event: delta\\ndata: {"text":\\ndata: "c"}\\n\\n');
          return {a: a.events, rest: a.rest, b: b.events, bRest: b.rest, c: c.events};
        }""")
        assert r["a"] == [{"type": "delta", "data": {"text": "a"}}] and r["rest"] == "event: del"
        assert r["b"] == [{"type": "delta", "data": {"text": "b"}}] and r["bRest"] == ""
        assert r["c"] == [{"type": "delta", "data": {"text": "c"}}]
