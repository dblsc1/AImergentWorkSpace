"""蜂巢新布局的页面行为（hive.stage.v1「一」「二」「四」；用例 HV-B01–B09、B13、B14、B16）。

页面是工作树里的真文件，接口全在浏览器侧 ``page.route()`` 桩掉（包括还没合进后端的新端点：
agents/{runId}/place、timer/*、块的 displayName / legible / kind），时间用假时钟推、不睡；拖动用真鼠标（Pointer Events）。

需要 playwright；CI 在「ring 前端测试」那个装了浏览器的 job 里跑本文件。
"""

from __future__ import annotations

import contextlib
from datetime import datetime, timedelta, timezone
import json
from typing import Any, Iterator
from zoneinfo import ZoneInfo

from test_hive_focus import COCKPIT, TREE, base, browser  # noqa: F401  (fixtures)

from playwright.sync_api import Browser, Page, Route  # noqa: E402

TZ = ZoneInfo("Asia/Shanghai")
CELL = ".hex-cell[data-project-id={}]"


def _iso(t: datetime) -> str:
    return t.astimezone(TZ).isoformat()


def _run(rid: str, agent: str, **over: Any) -> dict[str, Any]:
    return {"runId": rid, "agent": agent, "tool": agent, "model": None, "label": None, "taskId": None, "projectId": None,
            "startAt": None, "endAt": None, "outcome": None, "elapsedSeconds": 0, "overdue": False, "lost": False,
            "lastSeenAt": None, "phases": [], "attention": [], **over}


class Stage:
    def __init__(self, page: Page) -> None:
        self.page = page
        self.lanes: dict[str, Any] = {}
        self.current: dict[str, Any] = {"running": False, "focus": None}
        self.settings: dict[str, Any] = {"tiers": True}
        self.blocks: list[dict[str, Any]] = []
        self.unplaced: list[dict[str, Any]] = []
        self.api: list[str] = []
        self.posts: list[tuple[str, Any]] = []
        self.replies: dict[str, tuple[int, Any]] = {}
        self.requests: list[str] = []
        page.on("request", lambda r: self.requests.append(r.url))

    def now(self) -> datetime:
        return datetime.fromtimestamp(self.page.evaluate("() => Date.now()") / 1000, timezone.utc)

    def set_lanes(self, *, agents: list[dict[str, Any]] | None = None, sessions: list[dict[str, Any]] | None = None,
                  running: dict[str, Any] | None = None, interactions: list[dict[str, Any]] | None = None) -> None:
        now = self.now().astimezone(TZ)
        start = now - timedelta(hours=20)      # 夹具自己定「今天」从哪起，免得测试随一天里的钟点变
        self.lanes = {"today": start.date().isoformat(), "now": _iso(now), "windowStart": _iso(start),
                      "windowEnd": _iso(start + timedelta(days=1)),
                      "human": {"sessions": sessions or [], "running": running, "presence": [], "focus": None},
                      "agents": agents or [], "interactions": interactions or [], "truncated": False}

    def route(self, route: Route) -> None:
        req = route.request
        path = req.url.split("/api/core/", 1)[1]
        key = path.split("?")[0]
        if req.method != "GET":
            body = json.loads(req.post_data) if req.post_data else None
            self.posts.append((key, body))
            status, out = self.replies.get(key, (200, {}))
            route.fulfill(status=status, content_type="application/json", body=json.dumps(out))
            return
        self.api.append(key)
        if key == "views/tree":
            body: Any = TREE
        elif key == "views/lanes":
            body = self.lanes
        elif key == "views/current":
            body = self.current
        elif key == "activity/blocks":
            body = {"date": self.lanes.get("today"), "items": self.blocks, "nextCursor": None}
        elif key == "activity/settings":
            body = self.settings
        elif key == "timer/unplaced":
            body = {"items": self.unplaced}
        elif key == "views/next-actions":
            body = {"today": "2026-10-09", "zones": []}
        elif key == "views/gantt":
            body = {"today": "2026-10-09", "projects": []}
        else:
            body = {"total": 0, "items": []}
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body, ensure_ascii=False))

    def poll(self) -> None:
        """让页面的 5 秒轮询读到桩的新内容，再走半秒让它画完。"""
        with self.page.expect_response("**/api/core/views/lanes"):
            self.page.clock.run_for(5000)
        self.page.clock.run_for(600)

    def box(self, selector: str) -> dict[str, float]:
        b = self.page.locator(selector).first.bounding_box()
        assert b, selector
        return b

    def center(self, selector: str) -> tuple[float, float]:
        b = self.box(selector)
        return b["x"] + b["width"] / 2, b["y"] + b["height"] / 2

    def drag(self, selector: str, to: tuple[float, float], *, release: bool = True) -> None:
        x, y = self.center(selector)
        m = self.page.mouse
        m.move(x, y)
        m.down()
        m.move(x + 8, y + 8, steps=2)
        m.move(to[0], to[1], steps=6)
        if release:
            m.up()
            self.page.clock.run_for(50)

    def settle(self) -> None:
        """放手后等 drop 的请求回来（真时间）、再让页面画完。"""
        self.page.wait_for_timeout(250)
        self.page.clock.run_for(600)

    def toast(self) -> str:
        return self.page.locator("#stToast").inner_text()


@contextlib.contextmanager
def open_stage(browser: Browser, base: str, *, width: int = 1280, height: int = 900, setup: Any = None,
               pets: dict[str, Any] | None = None, tiers: bool = True, hold_webm: bool = False) -> Iterator[Stage]:
    context = browser.new_context(viewport={"width": width, "height": height}, timezone_id="Asia/Shanghai")
    page = context.new_page()
    page.clock.install()
    st = Stage(page)
    st.set_lanes()
    page.route("**/api/core/**", st.route)
    page.route("**/hive/pets/**", lambda r: None if hold_webm and r.request.url.endswith(".webm") else (      # hold_webm：视频请求挂着不答，页面不会因 404 退回占位
        r.fulfill(status=200, content_type="application/json", body=json.dumps(pets[r.request.url.split("/hive/pets/")[1].split("/")[0]]))
        if pets and r.request.url.split("/hive/pets/")[1].split("/")[0] in pets and r.request.url.endswith("pack.json")
        else r.fulfill(status=404, body="")))
    if setup:
        setup(st)
    page.add_init_script(path=str(COCKPIT / "focus.js"))
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(base + "/index.html")
    page.wait_for_selector(CELL.format("p_math"))
    page.wait_for_selector("#stRing .st-ring-time")
    page.wait_for_timeout(200)
    page.clock.pause_at(st.now() + timedelta(seconds=1))
    if tiers:
        # 三级分类开着（决策栏启动后在 .hd-panel 上挂 data-tiers）：页面才读窗口块；这里直接挂上，推一轮轮询
        page.evaluate("() => document.querySelector('.hd-panel').dataset.tiers = '1'")
        st.poll()
    try:
        yield st
        assert errors == [], errors
    finally:
        context.close()


def _busy_day(st: Stage) -> None:
    now = st.now().astimezone(TZ)
    noon = now - timedelta(hours=2)
    st.set_lanes(
        agents=[_run("r1", "claude-code", label="garden", projectId="p_math", startAt=_iso(now - timedelta(seconds=1200)), lastSeenAt=_iso(now),
                     phases=[{"at": _iso(now - timedelta(seconds=1200)), "phase": "working", "detail": None}]),
                _run("r2", "codex", label="scratch", startAt=_iso(now - timedelta(seconds=600)), lastSeenAt=_iso(now))],
        sessions=[{"startAt": _iso(noon - timedelta(hours=1)), "endAt": _iso(noon), "durationSeconds": 3600, "taskId": "t_p_math",
                   "projectId": "p_math", "mode": "do", "source": "timer-backend"}],
        interactions=[{"runId": "r1", "kind": "attend", "at": _iso(now - timedelta(seconds=60)), "until": _iso(now)}])
    st.blocks = [
        {"blockKey": "gh", "status": "pending", "seconds": 600, "seq": 3, "displayName": "github.com", "legible": True, "kind": "browser"},
        {"blockKey": "ptyxis · x", "status": "pending", "seconds": 300, "seq": 1, "displayName": "终端（未认出）", "legible": False, "kind": "terminal"},
    ]


def test_layout_has_ring_clock_bucket_and_cells_only_name_the_project(browser, base) -> None:  # noqa: F811  HV-B01 / HV-S20
    with open_stage(browser, base, setup=_busy_day) as st:
        assert st.page.locator("#stRing .st-ring-agent").inner_text() == "garden"
        assert st.page.locator("#stRing .st-ring-time").inner_text().startswith("00:20:")
        assert "小时" in st.page.locator("#stClock .st-clock-today").inner_text()
        assert st.page.locator("#stBucketLabel").inner_text() == "未分类 · 今天 15 分"
        assert st.page.locator(CELL.format("p_math")).inner_text().strip() == "数学"
        # 环正好在蜂巢正中心的格子上；时钟在右上角
        rx, ry = st.center("#stRing")
        cx, cy = st.center(".hex-cell.hex-center")
        assert abs(rx - cx) < 3 and abs(ry - cy) < 3
        assert st.box("#stClock")["x"] > 1280 / 2 and st.box("#stClock")["y"] < 200


def test_only_known_read_endpoints_and_no_writes_on_load(browser, base) -> None:  # noqa: F811  HV-B14（页面部分）
    with open_stage(browser, base, setup=_busy_day) as st:
        st.poll()
        assert not [a for a in st.api if a.startswith("agents/") or a.endswith("/usage")], st.api
        assert {"views/lanes", "views/current", "views/tree", "activity/blocks", "activity/settings", "timer/unplaced"} <= set(st.api)
        assert st.posts == []


def test_workers_sit_by_cells_at_most_four_with_plus_n_and_only_a_name_tag(browser, base) -> None:  # noqa: F811  HV-P03 / HV-B16
    def many(st: Stage) -> None:
        now = st.now().astimezone(TZ)
        st.set_lanes(agents=[_run(f"r{i}", "claude-code", label=f"a{i}", projectId=("p_math", "p_piano", "p_studio")[i % 3],
                                  startAt=_iso(now - timedelta(seconds=100 + i)), lastSeenAt=_iso(now - timedelta(seconds=i)),
                                  phases=[{"at": _iso(now), "phase": "working", "detail": None}]) for i in range(5)])

    with open_stage(browser, base, setup=many) as st:
        assert st.page.locator(".st-pet.is-work").count() == 4
        assert st.page.locator("#stMore").inner_text() == "+1"
        tags = st.page.locator(".st-pet.is-work .st-tag").all_inner_texts()
        assert sorted(tags) == ["a0", "a1", "a2", "a3"]       # 最久没动的 a4 被挤去 +1
        pet = st.page.locator(".st-pet.is-work").first
        assert pet.inner_text().strip() in tags                 # 只有名牌，没有别的文字


def test_pet_art_comes_from_the_pack_and_falls_back_to_a_placeholder(browser, base) -> None:  # noqa: F811
    def one(st: Stage) -> None:
        now = st.now().astimezone(TZ)
        st.set_lanes(agents=[_run("r1", "claude-code", label="g", projectId="p_math", startAt=_iso(now), lastSeenAt=_iso(now),
                                  phases=[{"at": _iso(now), "phase": "working", "detail": None}]),
                             _run("r2", "codex", label="h", projectId="p_piano", startAt=_iso(now), lastSeenAt=_iso(now - timedelta(seconds=5)),
                                  phases=[{"at": _iso(now), "phase": "waiting_input", "detail": None}])])

    pack = {"kind": "claude", "name": "Claude", "moods": {"working": ["w/a.webm"], "waiting": ["wait.webm"], "idle": ["i.webm"]},
            "license": "x", "credits": "x", "source": "x"}
    with open_stage(browser, base, setup=one, pets={"claude": pack}) as st:
        st.page.clock.run_for(600)
        assert any(u.endswith("/hive/pets/claude/pack.json") for u in st.requests)
        # 动画文件的请求发出去了（测试里它读不到，页面随后退回占位形象，所以只看请求；r2 的 waiting 包读不到）
        assert any(u.endswith("/hive/pets/claude/w/a.webm") for u in st.requests)
        assert not any("/hive/pets/gpt/" in u and u.endswith(".webm") for u in st.requests)
        assert st.page.locator('.st-pet[data-run-id="r2"] svg.st-art').count() == 1


def test_drag_loose_pet_into_a_cell_posts_place_once_and_says_what_was_remembered(browser, base) -> None:  # noqa: F811  HV-B02 / HV-S15
    with open_stage(browser, base, setup=_busy_day) as st:
        st.replies["agents/r2/place"] = (200, {"runId": "r2", "taskId": None, "projectId": "p_piano", "rule": {"agent": "codex", "projectId": "p_piano"}})
        pet = '.st-pet.is-loose[data-run-id="r2"]'
        assert st.page.locator(pet).count() == 1
        st.drag(pet, st.center(CELL.format("p_piano")), release=False)
        assert st.page.locator(CELL.format("p_piano")).evaluate("e => e.classList.contains('st-hot')")
        st.page.mouse.up()
        st.settle()
        assert st.posts == [("agents/r2/place", {"projectId": "p_piano", "remember": True})]
        assert st.toast() == "已记下：以后 codex 都算 钢琴"
        st.page.clock.run_for(3100)
        assert st.toast() == ""
        assert st.page.locator('.st-pet.is-work[data-run-id="r2"]').count() == 1      # 飞到格子边


def test_drop_on_blank_sends_nothing_and_pet_returns(browser, base) -> None:  # noqa: F811  HV-B03
    with open_stage(browser, base, setup=_busy_day) as st:
        pet = '.st-pet.is-loose[data-run-id="r2"]'
        st.page.wait_for_timeout(300)      # 走动的位置过渡（真时间 100ms）走完
        before = st.box(pet)
        st.drag(pet, (640, 20))
        st.settle()
        assert st.posts == []
        st.page.wait_for_timeout(300)
        after = st.box(pet)
        assert st.page.locator(pet).evaluate("e => e.style.translate") == ""
        assert after["x"] > 300 and after["x"] < 640 and after["y"] < 450      # 从落点（夹进左上角区域）接着走动，不回原位
        assert abs(after["x"] - before["x"]) > 20 or abs(after["y"] - before["y"]) > 20


def test_failed_drop_returns_the_pet_and_says_why(browser, base) -> None:  # noqa: F811  HV-S18
    with open_stage(browser, base, setup=_busy_day) as st:
        st.replies["agents/r2/place"] = (404, {"detail": "x"})
        pet = '.st-pet.is-loose[data-run-id="r2"]'
        st.page.wait_for_timeout(300)
        before = st.box(pet)
        st.drag(pet, st.center(CELL.format("p_piano")))
        st.settle()
        assert "目标不存在" in st.toast()
        st.page.wait_for_timeout(300)
        after = st.box(pet)
        assert st.page.locator(pet).evaluate("e => e.style.translate") == ""
        assert abs(after["x"] - before["x"]) < 20 and abs(after["y"] - before["y"]) < 20      # 回原位（走动每秒 ≤ 18px）
        assert st.page.locator('.st-pet.is-loose[data-run-id="r2"]').count() == 1


def test_loose_pets_wander_inside_their_corner(browser, base) -> None:  # noqa: F811  HV-P17
    with open_stage(browser, base, setup=_busy_day) as st:
        start = st.box('.st-pet.is-loose[data-run-id="r2"]')
        for _ in range(40):
            st.page.clock.run_for(500)
            b = st.box('.st-pet.is-loose[data-run-id="r2"]')
            assert b["x"] < 640 and b["y"] < 450
        end = st.box('.st-pet.is-loose[data-run-id="r2"]')
        assert (start["x"], start["y"]) != (end["x"], end["y"])


def test_bucket_drawer_drags_a_block_into_a_cell(browser, base) -> None:  # noqa: F811  HV-B04 / HV-B05 / HV-C12
    with open_stage(browser, base, setup=_busy_day) as st:
        st.page.click("#stBucketBtn")
        st.page.clock.run_for(300)
        chips = st.page.locator(".st-chip")
        assert chips.all_inner_texts() == ["github.com\n10 分", "终端（未认出）\n5 分"]
        assert "ptyxis" not in st.page.locator("#stDrawer").inner_text()
        # 灰行：不进入拖动状态，无请求
        x, y = st.center(".st-chip.nodrag")
        st.page.mouse.move(x, y)
        st.page.mouse.down()
        st.page.mouse.move(x + 60, y - 60, steps=4)
        assert st.page.locator(".st-dragging").count() == 0
        st.page.mouse.up()
        assert st.posts == []
        # 可拖行：拖进格子
        st.replies["activity/blocks/assign"] = (200, {})
        st.blocks = st.blocks[1:]          # 服务端已把它分走
        st.drag(".st-chip:not(.nodrag)", st.center(CELL.format("p_math")))
        st.settle()
        today = st.lanes["today"]
        assert st.posts == [("activity/blocks/assign", {"date": today, "blockKey": "gh", "ifSeq": 3, "projectId": "p_math", "remember": True})]
        assert st.toast() == "已记下：以后 github.com 都算 数学"
        assert st.page.locator("#stBucketLabel").inner_text() == "未分类 · 今天 5 分"
        assert st.page.locator(".st-chip").count() == 1


def test_block_drop_conflict_keeps_the_block(browser, base) -> None:  # noqa: F811  HV-S13
    with open_stage(browser, base, setup=_busy_day) as st:
        st.replies["activity/blocks/assign"] = (409, {})
        st.page.click("#stBucketBtn")
        st.page.clock.run_for(300)
        st.drag(".st-chip:not(.nodrag)", st.center(CELL.format("p_math")))
        st.settle()
        assert "数据已变化" in st.toast()
        assert st.page.locator(".st-chip").count() == 2
        assert st.page.locator("#stBucketLabel").inner_text() == "未分类 · 今天 15 分"


def test_bubble_follows_focus_between_cells_and_returns_to_the_clock_on_afk(browser, base) -> None:  # noqa: F811  HV-B06 / B07
    with open_stage(browser, base, setup=_busy_day) as st:
        def focus(state: str, pid: str | None) -> None:
            st.current = {"running": False, "focus": {"state": state, "app": "code", "title": "t", "since": _iso(st.now()), "projectId": pid,
                                                      "projectName": None, "taskId": None, "taskName": None, "source": "rules"}}

        def near(selector: str) -> bool:
            bx, by = st.center("#stBubble")
            b = st.box(selector)
            return b["x"] - 20 <= bx <= b["x"] + b["width"] + 20 and b["y"] - 20 <= by <= b["y"] + b["height"] + 20

        focus("present", "p_math")
        st.poll()
        assert st.page.locator("#stBubble").get_attribute("data-host") == "p_math" and near(CELL.format("p_math"))
        focus("present", "p_piano")
        with st.page.expect_response("**/api/core/views/current"):
            st.page.clock.run_for(5000)
        st.page.clock.run_for(400)           # 气泡的检查间隔
        st.page.clock.run_for(600)           # 飞行 ≤ 600ms
        assert st.page.locator("#stBubble").get_attribute("data-host") == "p_piano" and near(CELL.format("p_piano"))
        focus("afk", "p_piano")
        with st.page.expect_response("**/api/core/views/current"):
            st.page.clock.run_for(5000)
        st.page.clock.run_for(1000)
        assert st.page.locator("#stBubble").get_attribute("data-host") == "clock" and near("#stClock .st-dial")


def test_ring_switches_to_the_run_the_human_attends(browser, base) -> None:  # noqa: F811  HV-B08
    with open_stage(browser, base, setup=_busy_day) as st:
        assert st.page.locator("#stRing").get_attribute("data-run-id") == "r1"
        now = st.now().astimezone(TZ)
        st.lanes["interactions"].append({"runId": "r2", "kind": "attend", "at": _iso(now), "until": _iso(now + timedelta(seconds=30))})
        st.poll()
        assert st.page.locator("#stRing").get_attribute("data-run-id") == "r2"
        assert st.page.locator("#stRing .st-ring-agent").inner_text() == "scratch"


def test_manual_timer_click_start_drag_to_cell_stop_then_drag_the_strip(browser, base) -> None:  # noqa: F811  HV-B09
    with open_stage(browser, base, setup=_busy_day) as st:
        # 没在计时：拖时钟什么都不发
        st.drag("#stClock", st.center(CELL.format("p_math")))
        st.settle()
        assert st.posts == []
        # 点一下 = 立刻开始，不选项目
        st.page.click("#stClock")
        st.settle()
        assert st.posts == [("timer/start-unplaced", None)]
        now = st.now().astimezone(TZ)
        st.lanes["human"]["running"] = {"startAt": _iso(now), "taskId": None, "projectId": None}
        st.poll()
        assert "is-timing" in st.page.locator("#stClock").get_attribute("class")
        # 计时中拖到格子 = 记到该项目
        st.posts.clear()
        st.drag("#stClock", st.center(CELL.format("p_piano")))
        st.settle()
        assert st.posts == [("timer/place", {"projectId": "p_piano"})]
        # 再点 = 停；没记到项目的变白条
        st.posts.clear()
        st.replies["timer/stop"] = (200, {"unplacedId": "u1"})
        st.unplaced = [{"id": "u1", "startAt": _iso(now), "endAt": _iso(now + timedelta(minutes=12)), "durationSeconds": 720}]
        st.lanes["human"]["running"] = {"startAt": _iso(now), "taskId": None, "projectId": None}
        st.page.click("#stClock")
        st.settle()
        assert st.posts == [("timer/stop", None)]
        st.lanes["human"]["running"] = None
        st.poll()
        strip = st.page.locator(".st-strip")
        assert strip.count() == 1 and strip.inner_text() == "12 分"
        # 白条拖进格子
        st.posts.clear()
        st.unplaced = []
        st.drag(".st-strip", st.center(CELL.format("p_studio")))
        st.settle()
        assert st.posts == [("timer/unplaced/u1/place", {"projectId": "p_studio"})]
        assert st.page.locator(".st-strip").count() == 0


def test_click_cell_opens_the_completion_panel_and_close_removes_it(browser, base) -> None:  # noqa: F811  HV-B13
    with open_stage(browser, base, setup=_busy_day) as st:
        st.page.click(CELL.format("p_math"))
        panel = st.page.locator("#stPanel")
        assert panel.is_visible()
        assert panel.locator("h2").inner_text() == "数学"
        assert panel.locator('[data-kind="timer"] .st-row span').inner_text() == "1 小时 0 分"
        assert panel.locator('[data-kind="attend"]').count() == 1
        assert panel.locator(".st-agrow").count() == 1 and "garden" in panel.locator(".st-agrow").inner_text()
        assert panel.locator(".st-token").inner_text().replace("\n", "") == "token 花费—"
        assert st.page.locator(".hex-cell.is-expanded").count() == 0      # 没有触发老的展开
        panel.locator(".st-x").click()
        assert not st.page.locator("#stVeil").is_visible()
        assert st.posts == []


def test_1100_wide_scales_the_same_layout_and_dragging_still_hits_cells(browser, base) -> None:  # noqa: F811  1024–1279
    with open_stage(browser, base, width=1100, setup=_busy_day) as st:
        assert st.page.evaluate("() => document.querySelector('.board-shell').style.zoom") not in ("", "1")
        rx, ry = st.center("#stRing")
        cx, cy = st.center(".hex-cell.hex-center")
        assert abs(rx - cx) < 3 and abs(ry - cy) < 3
        clock = st.box("#stClock")
        assert clock["x"] + clock["width"] <= 1100
        st.replies["agents/r2/place"] = (200, {"rule": None})
        st.drag('.st-pet.is-loose[data-run-id="r2"]', st.center(CELL.format("p_math")))
        st.settle()
        assert st.posts == [("agents/r2/place", {"projectId": "p_math", "remember": True})]
        assert st.toast() == ""        # 没有规则就不说「以后…都算」（HV-S16）


def _drag_chip_to_blank(st: Stage) -> None:
    """从抽屉里把可拖的块拖出去，在蜂巢画布上扫点，直到出现空白处长出来的那一格（不松手）。"""
    st.page.click("#stBucketBtn")
    st.page.clock.run_for(300)
    x, y = st.center(".st-chip:not(.nodrag)")
    st.page.mouse.move(x, y)
    st.page.mouse.down()
    st.page.mouse.move(x + 20, y - 20, steps=3)
    cam = st.page.evaluate("() => { const r = document.querySelector('#hive').getBoundingClientRect(); return [r.left, r.top, r.width, r.height]; }")
    for gx in range(0, int(cam[2]), 45):
        for gy in range(0, int(cam[3]), 45):
            st.page.mouse.move(cam[0] + gx, cam[1] + gy)
            st.page.clock.run_for(100)
            if st.page.locator(".hex-sprout").count():
                return
    raise AssertionError("蜂巢里找不到空白处")


def test_block_over_blank_grows_the_same_sprout_and_leaving_removes_it(browser, base) -> None:  # noqa: F811
    with open_stage(browser, base, width=1900, setup=_busy_day) as st:
        _drag_chip_to_blank(st)
        sp = st.page.locator(".hex-sprout")
        assert sp.count() == 1 and "is-growing" in sp.get_attribute("class"), "同长按建项目一样的那一格"
        assert "is-rising" in sp.get_attribute("class"), "同样的升起"
        st.page.mouse.move(5, 5, steps=3)
        st.page.clock.run_for(100)
        assert st.page.locator(".hex-sprout").count() == 0, "挪开就收掉"
        st.page.mouse.up()
        st.settle()
        assert st.posts == [] and st.page.locator(".hex-newcard").count() == 0
        assert st.page.locator(".st-chip").count() == 2          # 块回原位


def test_block_dropped_on_blank_opens_the_create_form_then_assigns_the_block(browser, base) -> None:  # noqa: F811
    with open_stage(browser, base, width=1900, setup=_busy_day) as st:
        st.replies["planner/projects"] = (200, {"id": "p_new", "name": "github.com", "zoneId": "z_a"})
        _drag_chip_to_blank(st)
        st.page.mouse.up()
        form = st.page.locator(".hex-cell.is-expanded .hex-newcard")
        form.wait_for()
        inp = form.locator('input[name="name"]')
        assert inp.input_value() == "github.com", "名字预填块的显示名"
        st.page.clock.run_for(600)
        assert st.posts == [], "没确认什么都不建"
        form.locator('button[type="submit"]').click()
        st.page.wait_for_timeout(500)
        st.page.clock.run_for(600)
        st.page.wait_for_timeout(300)
        keys = [k for k, _ in st.posts]
        assert keys == ["planner/projects", "activity/blocks/assign"], keys
        body = st.posts[1][1]
        assert body == {"date": st.lanes["today"], "blockKey": "gh", "ifSeq": 3, "projectId": "p_new", "remember": True}


def test_block_blank_drop_cancelled_creates_nothing(browser, base) -> None:  # noqa: F811
    with open_stage(browser, base, width=1900, setup=_busy_day) as st:
        _drag_chip_to_blank(st)
        st.page.keyboard.press("Escape")
        st.page.clock.run_for(100)
        assert st.page.locator(".hex-sprout").count() == 0
        st.page.mouse.up()
        st.settle()
        assert st.posts == [] and st.page.locator(".hex-newcard").count() == 0
    with open_stage(browser, base, width=1900, setup=_busy_day) as st:
        _drag_chip_to_blank(st)
        st.page.mouse.up()
        st.page.locator(".hex-cell.is-expanded .hex-newcard").wait_for()
        st.page.clock.run_for(600)
        st.page.locator('.hex-newcard [data-hex-ui="close"]').click()
        st.page.clock.run_for(800)
        assert st.posts == []


def test_block_dropped_outside_the_hive_returns(browser, base) -> None:  # noqa: F811
    with open_stage(browser, base, setup=_busy_day) as st:
        st.page.click("#stBucketBtn")
        st.page.clock.run_for(300)
        st.drag(".st-chip:not(.nodrag)", (640, 8))
        st.settle()
        assert st.posts == [] and st.page.locator(".hex-sprout").count() == 0
        assert st.page.locator(".st-chip").count() == 2


def test_tiers_off_reads_no_blocks_and_no_extra_settings(browser, base) -> None:  # noqa: F811
    def off(st: Stage) -> None:
        _busy_day(st)
        st.settings = {"tiers": False}

    with open_stage(browser, base, setup=off, tiers=False) as st:
        st.poll()
        assert "activity/blocks" not in st.api and st.api.count("activity/settings") == 1   # 那一次是决策栏自己的
        assert st.page.locator("#stBucketLabel").inner_text() == "未分类 · 今天 0 分"


def test_panel_left_has_two_timelines_right_has_runs_and_esc_closes(browser, base) -> None:  # noqa: F811  HV-B13
    with open_stage(browser, base, setup=_busy_day) as st:
        st.page.click(CELL.format("p_math"))
        panel = st.page.locator("#stPanel")
        assert panel.locator(".st-col-human .st-kind").count() == 2
        assert panel.locator('[data-kind="timer"] .st-track i').count() == 1
        assert panel.locator('[data-kind="attend"] .st-row span').inner_text() == "1 分"
        row = panel.locator(".st-col-agent .st-agrow")
        assert row.count() == 1 and "garden" in row.inner_text() and "20 分" in row.inner_text()
        assert panel.locator(".st-col-agent .st-token").inner_text().replace("\n", "") == "token 花费—"
        st.page.keyboard.press("Escape")
        assert not st.page.locator("#stVeil").is_visible()


def test_panel_covers_the_whole_hive_including_zone_outlines(browser, base) -> None:  # noqa: F811  HV-B13
    with open_stage(browser, base, setup=_busy_day) as st:
        st.page.click(CELL.format("p_math"))
        on_top = st.page.evaluate("""() => {
          const r = document.getElementById('stPanel').getBoundingClientRect(), out = [];
          const pill = document.querySelector('.hex-zone-label');   // 分区名牌层级高于舞台层：挪到面板正中
          if (!pill) return 'no zone label';
          const pr = pill.getBoundingClientRect();
          pill.style.translate = (r.left + r.width / 2 - pr.left - pr.width / 2) + 'px ' + (r.top + r.height / 2 - pr.top - pr.height / 2) + 'px';
          const c = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
          out.push(!!(c && c.closest('#stPanel')));
          for (let i = 1; i < 10; i++) for (let j = 1; j < 6; j++) {
            const e = document.elementFromPoint(r.left + r.width * i / 10, r.top + r.height * j / 6);
            out.push(!!(e && e.closest('#stPanel')));
          }
          return out.every(Boolean);
        }""")
        assert on_top is True


def test_panel_reads_only_known_endpoints_and_never_agent_usage(browser, base) -> None:  # noqa: F811  HV-B14 / HV-B15
    with open_stage(browser, base, setup=_busy_day) as st:
        st.api.clear()
        st.page.click(CELL.format("p_math"))
        st.poll()
        known = {"views/lanes", "views/current", "views/tree", "activity/blocks", "activity/settings", "timer/unplaced", "views/agent-time", "activity/ledger"}
        assert set(st.api) <= known, st.api
        assert not [u for u in st.requests if "/usage" in u or "/agents/" in u], st.requests
        for f in ("hex-stage-page.js", "hex-record.js"):
            assert "usage" not in st.page.evaluate("(f) => fetch(f).then(r => r.text())", f)


def test_drag_the_bubble_while_timing_places_the_timer_like_the_clock(browser, base) -> None:  # noqa: F811  二·2
    with open_stage(browser, base, setup=_busy_day) as st:
        st.drag("#stBubble", st.center(CELL.format("p_piano")))      # 没在计时：拖不动
        st.settle()
        assert st.posts == [] and "st-dragging" not in (st.page.locator("#stBubble").get_attribute("class") or "")
        now = st.now().astimezone(TZ)
        st.lanes["human"]["running"] = {"startAt": _iso(now), "taskId": None, "projectId": None}
        st.poll()
        st.drag("#stBubble", st.center(CELL.format("p_piano")))
        st.settle()
        assert st.posts == [("timer/place", {"projectId": "p_piano"})]


def test_click_the_bubble_on_the_clock_stops_the_timer(browser, base) -> None:  # noqa: F811  二·2
    with open_stage(browser, base, setup=_busy_day) as st:
        now = st.now().astimezone(TZ)
        st.lanes["human"]["running"] = {"startAt": _iso(now), "taskId": None, "projectId": None}
        st.poll()
        bx, by = st.center("#stBubble")
        cx, cy = st.center("#stClock .st-dial")
        assert abs(bx - cx) < 3 and abs(by - cy) < 3        # 气泡压在表盘中心
        st.page.mouse.click(bx, by)
        st.settle()
        assert st.posts == [("timer/stop", None)]


def test_panel_keeps_focus_on_close_button_across_renders(browser, base) -> None:  # noqa: F811
    with open_stage(browser, base, setup=_busy_day) as st:
        st.page.click(CELL.format("p_math"))
        assert st.page.evaluate("() => document.activeElement.className") == "st-x"
        st.poll()
        assert st.page.evaluate("() => document.activeElement.className") == "st-x"


def test_panel_expand_button_closes_it_and_expands_the_cell(browser, base) -> None:  # noqa: F811
    with open_stage(browser, base, setup=_busy_day) as st:
        st.page.click(CELL.format("p_math"))
        st.page.click(".st-expand")
        assert not st.page.locator("#stVeil").is_visible()
        st.page.wait_for_selector(".hex-cell.is-expanded[data-project-id=p_math]")


TOUCH_PACK = {"kind": "claude", "name": "Claude", "license": "x", "credits": "x", "source": "x",
              "moods": {"working": ["w.webm"], "waiting": ["wait.webm"], "idle": ["i.webm"], "click": ["c1.webm", "c2.webm"],
                        "drag": ["d.webm"], "walk": ["k.webm", "k2.webm"]}}
PET = ".st-pet.is-work"
PET_ART = PET + " video.st-art"


def _one_worker(st: Stage) -> None:
    now = st.now().astimezone(TZ)
    st.set_lanes(agents=[_run("r1", "claude-code", label="g", projectId="p_math", startAt=_iso(now), lastSeenAt=_iso(now),
                              phases=[{"at": _iso(now), "phase": "working", "detail": None}])])


def _src(st: Stage) -> str:
    return st.page.locator(PET_ART).get_attribute("src") or ""


def test_click_a_pet_plays_a_click_clip_once_with_a_squash_then_returns(browser, base) -> None:  # noqa: F811  hive.stage.v1「二」7
    with open_stage(browser, base, setup=_one_worker, pets={"claude": TOUCH_PACK}, hold_webm=True) as st:
        st.page.clock.run_for(600)
        assert _src(st).endswith("/claude/w.webm")
        x, y = st.center(PET + " .st-media")
        st.page.mouse.click(x, y)
        assert _src(st).split("/")[-1] in ("c1.webm", "c2.webm")
        assert st.page.locator(PET_ART).get_attribute("loop") is None
        assert st.page.locator(PET).evaluate("e => e.classList.contains('st-squash')")
        st.page.clock.run_for(400)
        assert not st.page.locator(PET).evaluate("e => e.classList.contains('st-squash')")
        assert _src(st).split("/")[-1] in ("c1.webm", "c2.webm")      # 压扁完了，片子还在播；轮询重画也不换掉它
        st.page.clock.run_for(6000)
        assert _src(st).split("/")[-1] in ("c1.webm", "c2.webm")
        st.page.locator(PET_ART).dispatch_event("ended")
        assert _src(st).endswith("/claude/w.webm")
        assert st.posts == []


def test_drag_a_cell_pet_away_it_waits_three_seconds_then_walks_home_without_writing(browser, base) -> None:  # noqa: F811  hive.stage.v1「二」7
    with open_stage(browser, base, setup=_one_worker, pets={"claude": TOUCH_PACK}, hold_webm=True) as st:
        st.page.clock.run_for(600)
        home = st.box(PET + " .st-media")
        st.drag(PET + " .st-media", (900, 120), release=False)
        assert _src(st).endswith("/claude/d.webm")
        assert st.page.locator(PET_ART).get_attribute("loop") is not None
        st.page.clock.run_for(1000)      # 拖着的时候轮询不把它拉回去
        b = st.box(PET + " .st-media")
        assert abs(b["x"] + b["width"] / 2 - 900) < 30   # 按框中心：框宽随裁剪变
        st.page.mouse.up()
        st.page.clock.run_for(1500)
        away = st.box(PET + " .st-media")
        assert abs(away["x"] + away["width"] / 2 - 900) < 30 and _src(st).endswith("/claude/w.webm")
        st.page.clock.run_for(2500)      # 3 秒到了：换走路的片、开始动
        assert _src(st).endswith("/claude/k.webm")
        assert st.box(PET + " .st-media")["x"] < away["x"] - 5
        assert st.page.locator(PET).evaluate("e => e.classList.contains('flip')") is False    # 往左走 = 不翻转
        st.page.clock.run_for(9000)
        for _ in range(40):              # 到家后的 left/top 过渡按真实时间走，不受假时钟控制
            back = st.box(PET + " .st-media")
            if abs(back["x"] - home["x"]) < 3 and abs(back["y"] - home["y"]) < 3:
                break
            st.page.wait_for_timeout(50)
        assert abs(back["x"] - home["x"]) < 3 and abs(back["y"] - home["y"]) < 3
        assert _src(st).endswith("/claude/w.webm")
        assert st.posts == [] and not any("/place" in u for u in st.requests)


def test_esc_cancels_a_pet_drag_and_it_walks_home_at_once(browser, base) -> None:  # noqa: F811  hive.stage.v1「二」7
    with open_stage(browser, base, setup=_one_worker, pets={"claude": TOUCH_PACK}, hold_webm=True) as st:
        st.page.clock.run_for(600)
        st.drag(PET + " .st-media", (900, 120), release=False)
        st.page.keyboard.press("Escape")
        st.page.clock.run_for(500)
        assert _src(st).endswith("/claude/k.webm")
        st.page.clock.run_for(9000)
        assert st.posts == []


def test_grab_right_after_a_click_swaps_to_the_drag_clip_and_the_stay_shows_the_normal_clip(browser, base) -> None:  # noqa: F811  hive.stage.v1「二」7
    with open_stage(browser, base, setup=_one_worker, pets={"claude": TOUCH_PACK}, hold_webm=True) as st:
        st.page.clock.run_for(600)
        x, y = st.center(PET + " .st-media")
        st.page.mouse.click(x, y)
        assert _src(st).split("/")[-1] in ("c1.webm", "c2.webm")
        st.page.clock.run_for(300)
        st.drag(PET + " .st-media", (900, 120), release=False)
        assert _src(st).endswith("/claude/d.webm")
        st.page.mouse.up()
        st.page.clock.run_for(1000)
        assert _src(st).endswith("/claude/w.webm")
        assert not st.page.locator(PET).evaluate("e => e.classList.contains('st-walking')")
        st.page.clock.run_for(2500)
        assert _src(st).endswith("/claude/k.webm")      # 走路段固定取 walk[0]
