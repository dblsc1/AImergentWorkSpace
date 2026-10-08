"""assistant/frontend 自核套件 —— 真浏览器，接口全在浏览器侧 page.route() 桩掉，一个字节都不写库。

同 ring 的套件：页面由本进程起的本地静态服务器直接服 ``code/frontend/``（不经网关、不要口令）。
缺省四类接口都回 404（= 老后端 / 没装），各测试自己盖上要测的那一类：
``/api/agent/``（聊天）、``/api/core/activity/suggestions``（待确认建议）、``/api/core/detector/``（检测设置）；
``/api/core/views/tree`` 回下面的 TREE。
"""

from __future__ import annotations

import contextlib
import functools
import http.server
import json
import re
import threading
from pathlib import Path
from typing import Any, Callable, Iterator

import pytest
from playwright.sync_api import Browser, Page, Route, sync_playwright

FRONTEND_DIR = Path(__file__).resolve().parent.parent
PAGE_NAME = "index.html"

# 形状照 nexus-core 的 TreeOut（与 ring 套件同一份数据）
TREE = {
    "zones": [
        {"id": "z_study", "key": "310", "name": "练琴区", "color": "#35c9eb", "order": 0},
        {"id": "z_work", "key": "320", "name": "制作区", "color": "#ff9d45", "order": 1},
    ],
    "projects": [
        {"id": "p_eng", "key": "310-411", "zoneId": "z_study", "name": "吉他练习", "tasks": [
            {"id": "t_word", "key": "310-411-530-1", "name": "音阶练习", "done": False},
            {"id": "t_read", "key": "310-411-530-2", "name": "曲目视奏", "done": False, "dependsOn": ["t_word"]},
            {"id": "t_legacy", "key": "310-411-530-3", "name": "旧任务（无前置字段）"},
        ]},
        {"id": "p_book", "key": "310-412", "zoneId": "z_study", "name": "考级计划", "tasks": []},
    ],
}


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args: Any) -> None:  # noqa: A002
        pass


@pytest.fixture(scope="session")
def static_base_url() -> Iterator[str]:
    handler = functools.partial(_QuietHandler, directory=str(FRONTEND_DIR))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture(scope="session")
def browser() -> Iterator[Browser]:
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        yield b
        b.close()


# 「待确认建议」的集合（v2.10）缺省把多个窗口的行收着。既有用例断言的是行本身，所以缺省一出现就展开；
# 测「缺省收着」的用例传 expand=False。
_EXPAND = """new MutationObserver(ms => ms.forEach(m => m.addedNodes.forEach(n => {
  if (n.querySelectorAll) n.querySelectorAll('details.suggest-coll-rows').forEach(d => { d.open = true; });
}))).observe(document, {childList: true, subtree: true});"""


def _not_found(route: Route) -> None:
    route.fulfill(status=404, content_type="application/json", body='{"detail":"Not Found"}')


@contextlib.contextmanager
def open_page(browser: Browser, base: str, *, routes: dict[str, Callable[[Route], None]] | None = None,
              width: int = 1100, theme: str | None = None, init: str | None = None,
              reduced_motion: str | None = None, expand: bool = True) -> Iterator[Page]:
    """``routes``：正则 → 处理函数，盖在缺省的 404 上（Playwright 后注册的先匹配）。"""
    context = browser.new_context(viewport={"width": width, "height": 900}, timezone_id="Asia/Shanghai",
                                  reduced_motion=reduced_motion)
    page = context.new_page()
    for pat in (r"/api/agent/", r"/api/core/activity/suggestions", r"/api/core/detector/"):
        page.route(re.compile(pat), _not_found)
    page.route(re.compile(r"/api/core/views/tree(\?includeEphemeral=true)?$"), lambda r: r.fulfill(
        status=200, content_type="application/json", body=json.dumps(TREE, ensure_ascii=False)))
    for pat, fn in (routes or {}).items():
        page.route(re.compile(pat), fn)
    if expand:
        page.add_init_script(_EXPAND)
    if init:
        page.add_init_script(init)
    page.goto(f"{base}/{PAGE_NAME}")
    page.wait_for_selector("#settings-panel", state="attached")
    if theme:  # 线上由顶栏 navbar.js 设这个属性；本套件不注入顶栏，直接设
        page.evaluate(f"document.documentElement.dataset.theme = {theme!r}")
    try:
        yield page
    finally:
        context.close()


def overflowing(page: Page, root: str) -> list[str]:
    """root 及其子孙里右边缘超出视口的元素（窄屏不横滚的判据）。"""
    return page.evaluate(
        "(root) => [...document.querySelectorAll(root + ', ' + root + ' *')]"
        ".filter(e => e.getBoundingClientRect().right > document.documentElement.clientWidth + 0.5)"
        ".map(e => e.tagName + '#' + e.id + '.' + e.className)", root)
