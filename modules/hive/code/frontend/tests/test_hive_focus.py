"""此刻的焦点在蜂巢页上（nexus-core v2.16 的 views/current.focus；hive 契约「此刻的焦点」节）。

本模块别的测试都是 node 里的纯逻辑；这一条要看 DOM（中心格写了什么、哪个项目格被描边、减少动效时动不动），
所以用真浏览器：页面是工作树里的真文件，接口全在浏览器侧 ``page.route()`` 桩掉，时间用假时钟推、不睡。
共享件 focus.js 照网关的做法先于页面脚本之后注入（这里用 init script：效果相同，只是确定地在场）。

需要 playwright；CI 在「ring 前端测试」那个装了浏览器的 job 里跑本文件。
"""

from __future__ import annotations

import contextlib
from datetime import datetime, timezone
import functools
import http.server
import json
from pathlib import Path
import threading
from typing import Any, Iterator

import pytest

pytest.importorskip("playwright.sync_api", reason="没装 playwright：本文件在 ring 前端测试 job 里跑")
from playwright.sync_api import Browser, Page, Route, sync_playwright  # noqa: E402

FRONTEND = Path(__file__).resolve().parent.parent
COCKPIT = FRONTEND.parent.parent.parent / "nginx-docker" / "static"
EVIL = '<img src=x onerror="window.__pwned=1">'

TREE = {
    "zones": [{"id": "z_a", "key": "Z01", "name": "学习", "color": "#35c9eb", "order": 0},
              {"id": "z_b", "key": "Z02", "name": "制作", "color": "#ff9d45", "order": 1}],
    "projects": [
        {"id": pid, "key": pid, "zoneId": zone, "name": name, "status": "active", "progress": 0,
         "progressSource": "computed", "deadline": None, "unclassifiedTaskId": None,
         "tasks": [{"id": "t_" + pid, "key": "k", "name": name + "的任务", "done": False, "kind": "normal",
                    "flags": [], "plan": None, "dependsOn": []}]}
        for pid, zone, name in (("p_math", "z_a", "数学"), ("p_piano", "z_a", "钢琴"), ("p_studio", "z_b", "录音棚"))
    ],
}
IDLE = {"running": False, "zone": None, "project": None, "task": None, "sessionStartAt": None, "agents": [],
        "auto": None, "needsChoice": None, "aiThinking": None, "focus": None}
CENTER = ".hex-cell.hex-center"


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args: Any) -> None:
        pass


@pytest.fixture(scope="module")
def base() -> Iterator[str]:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(_Quiet, directory=str(FRONTEND)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture(scope="module")
def browser() -> Iterator[Browser]:
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        yield b
        b.close()


class Hive:
    def __init__(self, page: Page) -> None:
        self.page = page
        self.current: dict[str, Any] = dict(IDLE)
        self.api: list[str] = []
        self.writes: list[str] = []

    def route(self, route: Route) -> None:
        path = route.request.url.split("/api/core/", 1)[1]
        self.api.append(path.split("?")[0])
        if route.request.method != "GET":
            self.writes.append(path)
        if path.startswith("views/tree"):
            body: Any = TREE
        elif path.startswith("views/current"):
            body = self.current
        elif path.startswith("views/next-actions"):
            body = {"today": "2026-10-09", "zones": []}
        elif path.startswith("views/gantt"):
            body = {"today": "2026-10-09", "projects": []}
        else:
            body = {"total": 0, "items": []}
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body, ensure_ascii=False))

    def focus(self, seconds_ago: int = 125, **over: Any) -> dict[str, Any]:
        now = self.page.evaluate("() => Date.now()") / 1000
        since = datetime.fromtimestamp(now - seconds_ago, timezone.utc).isoformat()
        return {"state": "present", "app": "code", "title": "plot.gd — garden", "since": since, "projectId": None,
                "projectName": None, "taskId": None, "taskName": None, "source": None, **over}

    def show(self, **extra: Any) -> None:
        """换一份 views/current，等页面的 5 秒轮询读到它（假时钟推过去），再走一秒让中心格重画。"""
        self.current = {**IDLE, **extra}
        with self.page.expect_response("**/api/core/views/current"):
            self.page.clock.run_for(5000)
        self.page.clock.run_for(1000)

    def center(self) -> dict[str, Any]:
        return self.page.eval_on_selector(CENTER, """el => ({
            num: el.querySelector(".tring-num").textContent,
            name: (el.querySelector("#hexCenterMeta .hex-center-focus") || {}).textContent || null,
            tip: (el.querySelector("#hexCenterMeta .hex-center-focus") || {}).title || null,
            meta: (el.querySelector("#hexCenterMeta .hex-center-idle") || {}).textContent || null,
            title: (el.querySelector("#hexCenterTitle .hc-name") || {}).textContent || null,
            focus: el.classList.contains("is-focus"), afk: el.classList.contains("is-afk"),
            ctl: el.classList.contains("has-ctl"), label: el.getAttribute("aria-label") })""")

    def live_cells(self) -> list[str]:
        return self.page.eval_on_selector_all(".hex-cell.is-live-focus", "ns => ns.map(n => n.dataset.projectId)")


@contextlib.contextmanager
def open_hive(browser: Browser, base: str, *, width: int = 1280, height: int = 900, theme: str = "light",
              reduced_motion: str | None = None, helper: bool = True) -> Iterator[Hive]:
    context = browser.new_context(viewport={"width": width, "height": height}, timezone_id="Asia/Shanghai",
                                  reduced_motion=reduced_motion)
    page = context.new_page()
    page.clock.install()
    hive = Hive(page)
    page.route("**/api/core/**", hive.route)
    if helper:
        page.add_init_script(path=str(COCKPIT / "focus.js"))
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(base + "/index.html")
    page.wait_for_selector(CENTER)
    page.wait_for_selector(".hex-cell[data-project-id=p_math]")
    page.evaluate("t => document.documentElement.setAttribute('data-theme', t)", theme)
    page.clock.pause_at(datetime.fromtimestamp(page.evaluate("() => Date.now()") / 1000 + 1, timezone.utc))
    try:
        yield hive
        assert errors == [], errors
    finally:
        context.close()


MATH = {"projectId": "p_math", "projectName": "数学", "taskId": "t_p_math", "taskName": "数学的任务", "source": "history"}


def test_center_shows_live_focus_and_the_project_cell_is_highlighted(browser, base) -> None:
    with open_hive(browser, base) as hive:
        assert hive.center()["meta"] == "空闲" and hive.live_cells() == []
        booted = set(hive.api)
        hive.show(focus=hive.focus(**MATH))
        c = hive.center()
        assert (c["num"], c["name"], c["meta"]) == ("02:11", "数学 / 数学的任务", "正在 · 按以往")
        assert c["tip"] == "正在：数学 / 数学的任务 · code · plot.gd — garden"
        assert c["focus"] and not c["afk"] and not c["ctl"], "不是计时：不出完成 / 暂停 / 取消那三块"
        assert c["label"] == "正在：数学 / 数学的任务，code · plot.gd — garden（没有在计时），点击进入计时台"
        assert hive.live_cells() == ["p_math"]
        # 钟每秒走（假时钟）
        hive.page.clock.run_for(3000)
        assert hive.center()["num"] == "02:14"
        # 换了项目：描边跟着走，同一时刻至多一格
        hive.show(focus=hive.focus(5, projectId="p_studio", projectName="录音棚", source="agent-session"))
        c = hive.center()
        assert (c["name"], c["meta"]) == ("录音棚", "正在 · 来自会话") and hive.live_cells() == ["p_studio"]
        assert hive.writes == [], "只是显示：一个写请求都没有"
        assert set(hive.api) == booted, "没有因为焦点多读任何端点"


def test_window_only_afk_and_none(browser, base) -> None:
    with open_hive(browser, base) as hive:
        hive.show(focus=hive.focus(title=EVIL))
        c = hive.center()
        assert (c["name"], c["meta"]) == ("code · " + EVIL, "正在") and hive.live_cells() == []
        assert hive.page.locator(f"{CENTER} img").count() == 0 and hive.page.evaluate("() => window.__pwned") is None

        hive.show(focus=hive.focus(61, state="afk", app="", title="", **MATH))
        c = hive.center()
        assert (c["num"], c["meta"], c["name"], c["afk"]) == ("01:07", "离开", None, True)
        assert hive.live_cells() == [], "离开：不描项目格"

        hive.show(focus=None)
        c = hive.center()
        assert (c["num"], c["meta"], c["name"], c["focus"]) == ("", "空闲", None, False)
        assert c["label"] == "当前空闲，点击进入计时台"


def test_project_not_on_the_page_is_not_an_error(browser, base) -> None:
    with open_hive(browser, base) as hive:
        hive.show(focus=hive.focus(projectId="p_gone", projectName="已归档的项目", source="history"))
        assert hive.center()["name"] == "已归档的项目" and hive.live_cells() == []


def test_auto_track_manual_timer_and_pause(browser, base) -> None:
    with open_hive(browser, base) as hive:
        page = hive.page
        auto = {"taskId": None, "projectId": "p_piano", "taskName": None, "projectName": "钢琴",
                "since": hive.focus(65)["since"], "app": "code", "title": "x", "source": "rules", "key": "wk_1"}
        hive.show(auto=auto, focus=hive.focus(600, projectId="p_piano", projectName="钢琴", source="rules"))
        c = hive.center()
        assert (c["num"], c["name"], c["meta"]) == ("01:11", "钢琴", "自动 · 规则")
        assert hive.live_cells() == ["p_piano"]

        # 手动计时在每一处都优先：中心格回到计时的画法，项目格不描
        start = datetime.fromtimestamp(page.evaluate("() => Date.now()") / 1000 - 10, timezone.utc).isoformat()
        hive.show(running=True, sessionStartAt=start, zone={"id": "z_a", "key": "Z01", "name": "学习"},
                  project={"id": "p_math", "key": "k", "name": "数学", "totalSeconds": 1, "shareOfPlan": 1},
                  task={"id": "t_p_math", "key": "k", "name": "手动的任务", "totalSeconds": 1, "shareOfProject": 1,
                        "kind": "normal"},
                  focus=hive.focus(**MATH))
        c = hive.center()
        assert c["title"] == "手动的任务" and not c["focus"] and c["ctl"] and hive.live_cells() == []
        assert c["label"].startswith("正在计时：手动的任务")

        # 本机暂停着（停的是别的任务）：也不让焦点顶掉
        page.evaluate("""() => localStorage.setItem('nexus.timer.paused.v1', JSON.stringify(
            {taskId: 't_p_piano', taskName: '暂停着的任务', projectName: '钢琴', carriedSeconds: 30}))""")
        hive.show(focus=hive.focus(**MATH))
        c = hive.center()
        assert c["title"] == "暂停着的任务" and c["ctl"] and not c["focus"] and hive.live_cells() == []


@pytest.mark.parametrize("reduced", [None, "reduce"])
def test_breathing_respects_reduced_motion(browser, base, reduced) -> None:
    with open_hive(browser, base, reduced_motion=reduced) as hive:
        hive.show(focus=hive.focus(**MATH))
        anim = "n => getComputedStyle(n).animationName"
        assert hive.page.eval_on_selector(f"{CENTER} .tring-track", anim) == ("none" if reduced else "hex-focus-breathe")
        assert hive.page.eval_on_selector(".hex-cell.is-live-focus", anim) == ("none" if reduced else "hex-live-focus")
        # 静态的描边还在：格子自己的底色是 --fact，内层往里收了
        inset = hive.page.eval_on_selector(".hex-cell.is-live-focus", "n => getComputedStyle(n, '::before').top")
        assert inset == "3px"
        dash = hive.page.eval_on_selector(f"{CENTER} .tring-track", "n => getComputedStyle(n).strokeDasharray")
        assert dash != "none", "虚线轨道：和手动计时的实线分得开"


def test_without_the_shared_helper_the_page_is_unchanged(browser, base) -> None:
    """共享件没加载到（老网关）：照旧「空闲」，不报错。"""
    with open_hive(browser, base, helper=False) as hive:
        hive.show(focus=hive.focus(**MATH))
        assert hive.center()["meta"] == "空闲" and hive.live_cells() == []


@pytest.mark.parametrize("width", [320, 390])
def test_fits_narrow_screens(browser, base, width) -> None:
    with open_hive(browser, base, width=width, height=760) as hive:
        before = hive.page.evaluate("() => document.documentElement.scrollWidth")
        hive.show(focus=hive.focus(3700, title="很长很长的窗口标题" * 12, projectId="p_math",
                                   projectName="很长很长的项目名字" * 6, source="history"))
        assert hive.center()["num"] == "1:01:46" and hive.live_cells() == ["p_math"]
        assert hive.page.evaluate("() => document.documentElement.scrollWidth") <= before, "不比没有焦点时更宽"
        box = hive.page.eval_on_selector(CENTER, "n => { const r = n.getBoundingClientRect(); return [r.left, r.right]; }")
        name = hive.page.eval_on_selector(f"{CENTER} .hex-center-focus",
                                          "n => { const r = n.getBoundingClientRect(); return [r.left, r.right]; }")
        assert box[0] <= name[0] and name[1] <= box[1], "名字截在中心格里"
