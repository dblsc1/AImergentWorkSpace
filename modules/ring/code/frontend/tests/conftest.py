"""ring/frontend 自核套件 —— 真浏览器，但**完全不碰真库**。

2026-08-08 （cockpit-v1 改造）随 F-RING-1 整体重写：旧的「贡献圆环 + 计时
控件」两块区域合并成一件仪器，标记（markup）与文件都变了——`#task-arc`/
`#project-arc`/`#start-btn`/`#timer-idle`/`#timer-running`/`timer-control.css`
均已退役，全部换成 `ring.css` / `ring-instrument.js` 与新的两级选择器
（`#project-select` → `#task-select`）、`#seg-current`/`#seg-rest`、
`#start-big-btn`、`#chrono-center`。夹具随之改写，判据保留的是**行为**
（零任务项目仍要可见不可选、reduced-motion 仍要一步到位、轮询仍不能瞎重绘），
不是旧的 DOM 形状。

## 与真网关 E2E 套件是两回事

那一套打真网关、真后端、真 Mongo，会**往库里写计时记录**。本套件是本地
自核，形状相反：

- 页面由本进程起的**本地静态服务器**提供（直接服 ``code/frontend/``），不经 nginx，
  因此**不需要口令**、不经鉴权。
- 三条业务接口全部由 ``page.route()`` 在**浏览器侧**桩掉，请求根本出不了浏览器。
- ``/api/core/timer/**``（start/stop/cancel，唯一的写入面）被**显式 abort**：
  不是"我们没去调"，是"调了也出不去"。

口令：本套件一个字都不需要，因此也**不读** ``COCKPIT_TEST_PASSWORD``。
真要打真网关的用例应由真网关 E2E 套件覆盖，那边有三层探测和生产库护栏
——**本轮改造已知会让那套 E2E 的选择器全部失配**（`#task-select` 单选下拉
换两级、`#start-btn`/`#timer-idle`/`#timer-running`/`#running-name` 全部
改名或消失、`timer-control.css` 整个文件被删），需要逐条更新旧断言。
"""

from __future__ import annotations

import contextlib
import functools
import http.server
import json
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Iterator

import pytest
from playwright.sync_api import Browser, Page, Route, sync_playwright

FRONTEND_DIR = Path(__file__).resolve().parent.parent
PAGE_NAME = "project-task-contribution-ring.html"
GATEWAY_BASE_URL = "http://127.0.0.1:8800"

# 与 ring-countdown.js 里的 STORAGE_KEY 字面量保持一致（2026-08-09 倒计时任务
# 单）——测试用它在 goto() 之前用 add_init_script 注入/校验倒计时续算记录，
# 不是通过读源码字符串反推，两边各自写一份常量，靠这行注释互相看得见。
COUNTDOWN_STORAGE_KEY = "ring.countdown.v1"

# ── 固定夹具数据 ──────────────────────────────────────────────────────
# 形状照抄 ../../../nexus-core/module_docs/contract-schemas.md 的 TreeOut，
# **额外加了 dependsOn/done 的四种组合**（F-RING-6 防御式判据要靠这批数据）：
#   t_word    —— 没有 dependsOn 字段（旧形状任务，nexus-core 加这字段前就有的）
#   t_read    —— dependsOn=[t_word]，t_word.done=false → 真实未完成前置，该出提示
#   t_legacy  —— 没有 done、没有 dependsOn（两个字段都缺，最原始的旧形状）
#   t_write   —— dependsOn=[t_legacy]，但 t_legacy 没有 done 字段 → 不敢断言"未完成"，
#                防御式要求这里**不出提示**
#   t_dangling —— dependsOn 指向一个压根不存在的任务 id → 静默当没有这条依赖
TREE = {
    "zones": [
        {"id": "z_study", "key": "310", "name": "练琴区", "color": "#35c9eb", "order": 0},
        {"id": "z_work", "key": "320", "name": "制作区", "color": "#ff9d45", "order": 1},
    ],
    "projects": [
        {
            "id": "p_eng", "key": "310-411", "zoneId": "z_study", "name": "吉他练习",
            "status": "active", "progress": 40, "progressSource": "computed", "deadline": None,
            "tasks": [
                {"id": "t_word", "key": "310-411-530-1", "name": "音阶练习",
                 "done": False, "kind": "normal", "flags": []},
                {"id": "t_read", "key": "310-411-530-2", "name": "曲目视奏",
                 "done": False, "dependsOn": ["t_word"], "kind": "normal", "flags": []},
                {"id": "t_legacy", "key": "310-411-530-3", "name": "旧任务（无前置字段）",
                 "kind": "normal", "flags": []},
                {"id": "t_write", "key": "310-411-530-4", "name": "乐句创作",
                 "done": False, "dependsOn": ["t_legacy"], "kind": "normal", "flags": []},
                {"id": "t_dangling", "key": "310-411-530-5", "name": "坏引用任务",
                 "done": False, "dependsOn": ["t_missing_ref"], "kind": "normal", "flags": []},
            ],
        },
        {
            "id": "p_book", "key": "310-412", "zoneId": "z_study", "name": "考级计划",
            "status": "active", "progress": 0, "progressSource": "computed", "deadline": None,
            "tasks": [],
        },
        {
            "id": "p_studio", "key": "320-421", "zoneId": "z_work", "name": "录音棚整备",
            "status": "active", "progress": 0, "progressSource": "computed", "deadline": None,
            "tasks": [],
        },
    ],
}

ZERO_TASK_PROJECTS = ("考级计划", "录音棚整备")
PROJECT_IDS = tuple(p["id"] for p in TREE["projects"])

# 空闲态：契约规定 running:false 时四个字段全为 null（不是 0）。
CURRENT_IDLE: dict[str, Any] = {
    "running": False, "zone": None, "project": None, "task": None, "sessionStartAt": None,
}

# ── 「今天」数据夹具（后补的一轮，GET /api/core/views/gantt）─────────
# 形状照抄 nexus-core v1.1 的 GanttOut：projects[].tasks[] 每个带
# actual:[{date,seconds}]。GANTT_TODAY/GANTT_YESTERDAY 是任意选定的两个
# 字符串，与真实挂钟日期无关——判据只测「JS 是否严格按 gantt.today 相等
# 比较来切片」，不测「今天到底是哪天」，这正是不用 new Date() 的意义。
GANTT_TODAY = "2026-08-08"
GANTT_YESTERDAY = "2026-08-07"

# p_eng 今日三档：t_word(当前任务)=600s，t_read(次高)=900s，t_legacy=300s
# （落进「其余任务合计」），t_write 只有**昨天**的 1200s（跨日边界：不许被
# 计进今天），t_dangling 完全没有 actual 记录。合计=600+900+300=1800s。
GANTT_WITH_TODAY_DATA: dict[str, Any] = {
    "today": GANTT_TODAY,
    "projects": [
        {
            "id": "p_eng", "key": "310-411", "name": "吉他练习", "plan": None,
            "actual": [{"date": GANTT_TODAY, "seconds": 1800}],
            "tasks": [
                {"id": "t_word", "key": "310-411-530-1", "name": "音阶练习",
                 "done": False, "plan": None, "dependsOn": [],
                 "actual": [{"date": GANTT_TODAY, "seconds": 600}]},
                {"id": "t_read", "key": "310-411-530-2", "name": "曲目视奏",
                 "done": False, "plan": None, "dependsOn": ["t_word"],
                 "actual": [{"date": GANTT_TODAY, "seconds": 900}]},
                {"id": "t_legacy", "key": "310-411-530-3", "name": "旧任务（无前置字段）",
                 "plan": None, "dependsOn": [],
                 "actual": [{"date": GANTT_TODAY, "seconds": 300}]},
                {"id": "t_write", "key": "310-411-530-4", "name": "乐句创作",
                 "done": False, "plan": None, "dependsOn": ["t_legacy"],
                 # 跨日边界：只有昨天的记录，今天切片必须是 0，不许把昨天的
                 # 1200s 算进今天（这是本轮规格点名要的夹具）。
                 "actual": [{"date": GANTT_YESTERDAY, "seconds": 1200}]},
                {"id": "t_dangling", "key": "310-411-530-5", "name": "坏引用任务",
                 "done": False, "plan": None, "dependsOn": ["t_missing_ref"], "actual": []},
            ],
        },
        {
            # p_studio：今天 0 分（只有昨天的记录）——明文要求「0 也如实
            # 显示」，这个项目专门用来证明它不会被静默隐藏成「无数据」。
            "id": "p_studio", "key": "320-421", "name": "录音棚整备", "plan": None,
            "actual": [{"date": GANTT_YESTERDAY, "seconds": 500}],
            "tasks": [
                {"id": "t_build", "key": "320-421-1-1", "name": "占位任务",
                 "done": False, "plan": None, "dependsOn": [],
                 "actual": [{"date": GANTT_YESTERDAY, "seconds": 500}]},
            ],
        },
        {
            # p_book：真的零任务（对齐 TREE 里的空项目），今天合计也是 0。
            "id": "p_book", "key": "310-412", "name": "考级计划", "plan": None,
            "actual": [], "tasks": [],
        },
    ],
}

# 防御式夹具：p_eng 整个缺 tasks 字段（模拟 nexus-core 灰度上线时新旧形状
# 混杂），其余项目正常——只让 p_eng 这一个项目退回终身累计展示，不拖累别的。
GANTT_MISSING_TASKS_FOR_P_ENG: dict[str, Any] = {
    "today": GANTT_TODAY,
    "projects": [
        {"id": "p_eng", "key": "310-411", "name": "吉他练习", "plan": None, "actual": []},
        GANTT_WITH_TODAY_DATA["projects"][2],  # p_book，原样复用
    ],
}


def current_running(
    *,
    task_id: str = "t_word",
    task_name: str = "音阶练习",
    share_of_project: float = 32.1,
    task_seconds: int = 5400,
    session_start_at: str = "2026-08-03T09:30:00+08:00",
) -> dict[str, Any]:
    """一条 running=true 的 CurrentOut。默认值即"吉他练习 / 音阶练习"。"""
    return {
        "running": True,
        "zone": {"id": "z_study", "key": "310", "name": "练琴区"},
        "project": {"id": "p_eng", "key": "310-411", "name": "吉他练习",
                    "totalSeconds": 50400, "shareOfPlan": 47.5},
        "task": {"id": task_id, "key": "310-411-530-1", "name": task_name,
                 "totalSeconds": task_seconds, "shareOfProject": share_of_project},
        "sessionStartAt": session_start_at,
    }


# ── 本地静态服务器 ────────────────────────────────────────────────────
class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args: Any) -> None:  # noqa: A002 - 压掉每请求一行的噪声
        pass


@pytest.fixture(scope="session")
def static_base_url() -> Iterator[str]:
    """把 code/frontend/ 原样服出去。**服的是工作树里的真文件**，不是副本——
    改了源码不重跑构建就能测（本模块零构建，R9）。"""
    handler = functools.partial(_QuietHandler, directory=str(FRONTEND_DIR))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture(scope="session")
def gateway_navbar_available() -> bool:
    """网关活着就把线上顶栏也注入进来，让被测页面形状与线上一致。"""
    try:
        with urllib.request.urlopen(f"{GATEWAY_BASE_URL}/healthz", timeout=2) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError):
        return False


@pytest.fixture(scope="session")
def browser() -> Iterator[Browser]:
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        yield b
        b.close()


# ── 探针：数"首载动画被触发了几次"+"分段真的被重绘了几次" ─────────────
#
# F-RING-3 起动画语义变简单了：仅首载 350ms，此后（包括真实数据变化）一律
# 直接就位——所以探针不再需要旧版那套"区分重放 vs 首播"的复杂计数，
# 只要盯 #seg-current 的 class 变化（是否出现过 is-first-load）与
# stroke-dasharray 属性写入次数（是否发生了重绘）。
#
# **同一个坑不再踩第二次**：`classList.remove()` 在类本来就不存在时也会产生
# 一条 MutationRecord，而回调是批量投递的——本版仍用 MutationObserver +
# attributeOldValue 还原"这条记录之后的值"，只在 before 没有、after 有时才计数
# 首载动画（上一版在 handoff.md「避坑」段已验证过这个技巧，这里原样复用）。
_PROBE_SCRIPT = """
window.__ringProbe = { firstLoadAdds: 0, segWrites: 0 };
document.addEventListener("DOMContentLoaded", () => {
  const seg = document.getElementById("seg-current");
  if (!seg) return;
  new MutationObserver(records => {
    for (let i = 0; i < records.length; i++) {
      const r = records[i];
      if (r.attributeName === "stroke-dasharray") { window.__ringProbe.segWrites += 1; continue; }
      if (r.attributeName !== "class") continue;
      const before = r.oldValue || "";
      let after = seg.getAttribute("class") || "";
      for (let j = i + 1; j < records.length; j++) {
        if (records[j].attributeName === "class") { after = records[j].oldValue || ""; break; }
      }
      if (before.indexOf("is-first-load") === -1 && after.indexOf("is-first-load") !== -1) {
        window.__ringProbe.firstLoadAdds += 1;
      }
    }
  }).observe(seg, {
    attributes: true, attributeFilter: ["class", "stroke-dasharray"], attributeOldValue: true
  });
});
"""


class RingHarness:
    """一个装好桩和探针的页面。用例通过 :meth:`set_current` 换计时态。"""

    def __init__(
        self,
        page: Page,
        initial_current: dict[str, Any] | None = None,
        initial_gantt: dict[str, Any] | None = None,
    ) -> None:
        self.page = page
        self.current: dict[str, Any] = initial_current or current_running()
        # None=views/gantt 不可达（route abort，模拟 404/网络失败）——这是
        # **默认值**，与本轮改造前的既有行为一致：没有显式给 gantt 夹具的
        # 用例，走的就是「今天数据不可用，退回终身累计」这条路径。
        self.gantt: dict[str, Any] | None = initial_gantt
        self.current_polls = 0
        self.gantt_polls = 0
        self.timer_write_attempts = 0
        self.timer_write_urls: list[str] = []
        self.timer_write_bodies: list[str | None] = []
        # planner 写入面（2026-09-08 计时台改名）。timer/** 那条是
        # abort（"调了也出不去"），这条不同：改名要验的是**改完之后 UI 怎么
        # 走**，所以必须 fulfill 成功，同时把请求原样记下来当判据。一个字节
        # 也到不了真库——本套件根本没有后端在跑。
        self.planner_writes: list[dict[str, Any]] = []

    def rename_current_task(self, name: str) -> None:
        """把桩里 running 任务的名字换掉，模拟"后端已改，下一轮轮询拿到新名"。"""
        self.current["task"]["name"] = name

    # 每次轮询都换一个 sessionStartAt —— 让"整个响应体的深比较"这种实现
    # **必然失败**：与画面无关的字段一直在动，判据只能落在渲染真依赖的值上。
    def _next_current(self) -> dict[str, Any]:
        self.current_polls += 1
        payload = json.loads(json.dumps(self.current))
        if payload.get("running"):
            payload["sessionStartAt"] = f"2026-08-03T09:30:{self.current_polls % 60:02d}+08:00"
        return payload

    def set_current(self, payload: dict[str, Any]) -> None:
        self.current = payload

    def set_gantt(self, payload: dict[str, Any] | None) -> None:
        """None = 模拟 views/gantt 不可达（route abort），触发前端的防御式回退。"""
        self.gantt = payload

    def probe(self) -> dict[str, int]:
        return self.page.evaluate("() => window.__ringProbe")

    def first_load_count(self) -> int:
        return int(self.probe()["firstLoadAdds"])

    def seg_write_count(self) -> int:
        return int(self.probe()["segWrites"])

    def wait_for_polls(self, n: int, timeout_ms: int = 30_000) -> None:
        """等到 current 至少被拉了 n 次（轮询是 7 秒一次）。"""
        waited = 0
        step = 250
        while self.current_polls < n:
            if waited >= timeout_ms:
                raise AssertionError(
                    f"等轮询超时：只等到 {self.current_polls} 次，要求 {n} 次"
                )
            self.page.wait_for_timeout(step)
            waited += step


def _install_stub_routes(page: Page, harness: RingHarness) -> None:
    def tree_route(route: Route) -> None:
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps(TREE, ensure_ascii=False))

    def current_route(route: Route) -> None:
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps(harness._next_current(), ensure_ascii=False))

    def cockpit_current_route(route: Route) -> None:
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps({"running": False}, ensure_ascii=False))

    def gantt_route(route: Route) -> None:
        harness.gantt_polls += 1
        if harness.gantt is None:
            # 模拟不可达（404）：前端的 loadGanttToday() 必须吞掉这个失败、
            # 静默退回终身累计展示，不弹错误、不影响 views/current 的渲染。
            route.fulfill(status=404, content_type="application/json", body='{"detail":"not found"}')
            return
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps(harness.gantt, ensure_ascii=False))

    def timer_write_route(route: Route) -> None:
        # **唯一的写入面**（start/stop/cancel 都落在这条 glob 下）。这里不是
        # "测试碰巧没调用"，是"调用了也出不去"——任何一次落到这里都会让
        # assert_no_timer_writes 失败。记下完整 URL（不止计数），这样 A9 的
        # 代理判据（"确认取消只打 cancel、从不打 stop"）才能按路径区分。
        harness.timer_write_attempts += 1
        harness.timer_write_urls.append(route.request.url)
        harness.timer_write_bodies.append(route.request.post_data)
        route.abort()

    page.route("**/api/core/views/tree", tree_route)
    page.route("**/api/core/views/current", current_route)
    page.route("**/api/core/views/gantt", gantt_route)
    page.route("**/__cockpit/current", cockpit_current_route)
    def planner_write_route(route: Route) -> None:
        harness.planner_writes.append({
            "method": route.request.method,
            "url": route.request.url,
            "body": route.request.post_data,
        })
        route.fulfill(status=200, content_type="application/json", body='{"ok":true}')

    page.route("**/api/core/timer/**", timer_write_route)
    page.route("**/api/core/planner/**", planner_write_route)


def _inject_navbar(page: Page) -> None:
    """按 nginx cockpit.conf.template 的 sub_filter 原样注入那两个标签。"""
    page.evaluate(
        """(base) => {
            const link = document.createElement("link");
            link.rel = "stylesheet";
            link.href = base + "/__cockpit/navbar.css";
            document.body.appendChild(link);
            const script = document.createElement("script");
            script.src = base + "/__cockpit/navbar.js";
            script.defer = true;
            document.body.appendChild(script);
        }""",
        GATEWAY_BASE_URL,
    )


@contextlib.contextmanager
def open_ring(
    browser: Browser,
    static_base_url: str,
    *,
    with_navbar: bool,
    reduced_motion: str | None = None,
    initial_current: dict[str, Any] | None = None,
    initial_gantt: dict[str, Any] | None = None,
    url_suffix: str = "",
    init_scripts: tuple[str, ...] = (),
) -> Iterator[RingHarness]:
    """``init_scripts``：在 ``goto()`` 之前额外跑的 JS（``add_init_script``，
    每次导航前执行）——LT/倒计时的刷新续算用例靠它在页面自己的脚本跑之前把
    ``localStorage`` 记录种好，比 goto 之后再 evaluate 更贴近真实的"刷新前就
    已经有记录"场景（2026-08-09 倒计时新增，之前没有这个需求）。"""
    context = browser.new_context(
        viewport={"width": 1100, "height": 900},
        reduced_motion=reduced_motion,
    )
    page = context.new_page()
    harness = RingHarness(page, initial_current, initial_gantt)
    _install_stub_routes(page, harness)
    page.add_init_script(_PROBE_SCRIPT)
    for script in init_scripts:
        page.add_init_script(script)
    page.goto(f"{static_base_url}/{PAGE_NAME}{url_suffix}")
    page.wait_for_selector("#task-select", state="attached")
    if with_navbar:
        _inject_navbar(page)
    try:
        yield harness
    finally:
        context.close()


@pytest.fixture
def ring(browser: Browser, static_base_url: str, gateway_navbar_available: bool) -> Iterator[RingHarness]:
    """计时进行中的页面（圆环可见，控件区顶掉选择器行，露出停止/取消两个按钮）。"""
    with open_ring(browser, static_base_url, with_navbar=gateway_navbar_available) as harness:
        harness.page.wait_for_selector("#elapsed-display", state="attached")
        yield harness


@pytest.fixture
def ring_idle(browser: Browser, static_base_url: str, gateway_navbar_available: bool) -> Iterator[RingHarness]:
    """空闲态的页面 —— 两级任务选择器可见可用，表芯是大按钮「开始计时」。"""
    with open_ring(browser, static_base_url, with_navbar=gateway_navbar_available,
                   initial_current=CURRENT_IDLE) as harness:
        harness.page.wait_for_selector("#start-big-btn", state="attached")
        yield harness


@pytest.fixture
def ring_today(browser: Browser, static_base_url: str, gateway_navbar_available: bool) -> Iterator[RingHarness]:
    """计时进行中 + views/gantt 今天数据可用——三段式圆环该露出的那条分支。"""
    with open_ring(browser, static_base_url, with_navbar=gateway_navbar_available,
                   initial_gantt=GANTT_WITH_TODAY_DATA) as harness:
        harness.page.wait_for_selector("#elapsed-display", state="attached")
        yield harness


@pytest.fixture
def ring_idle_today(browser: Browser, static_base_url: str, gateway_navbar_available: bool) -> Iterator[RingHarness]:
    """空闲态 + views/gantt 今天数据可用——表芯该显示「今天 · N 分」的那条分支。"""
    with open_ring(browser, static_base_url, with_navbar=gateway_navbar_available,
                   initial_current=CURRENT_IDLE, initial_gantt=GANTT_WITH_TODAY_DATA) as harness:
        harness.page.wait_for_selector("#start-big-btn", state="attached")
        yield harness
