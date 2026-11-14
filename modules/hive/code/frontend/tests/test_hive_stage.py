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
from urllib.parse import parse_qs, urlsplit
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
        self.devices: list[dict[str, Any]] = []
        self.records: list[dict[str, Any]] = []
        self.records_by_block: dict[str, list[dict[str, Any]]] = {}      # 按块名给原始记录；没给的块用 self.records
        self.api: list[str] = []
        self.posts: list[tuple[str, Any]] = []
        self.replies: dict[str, tuple[int, Any]] = {}
        self.tree: Any = TREE                                            # views/tree 的桩；用例要多任务 / 已取消时换它
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
            body: Any = self.tree
        elif key == "views/lanes":
            body = self.lanes
        elif key == "views/current":
            body = self.current
        elif key == "activity/blocks":
            body = {"date": self.lanes.get("today"), "items": self.blocks, "nextCursor": None}
        elif key == "activity/blocks/records":
            block = parse_qs(urlsplit(req.url).query).get("blockKey", [""])[0]
            body = {"items": self.records_by_block.get(block, self.records), "truncated": False}
        elif key == "activity/blocks/range":      # nexus-core v2.56：一段日期里每天的块，带所属日期与原始记录
            q = parse_qs(urlsplit(req.url).query)
            lo, hi = q.get("from", [""])[0], q.get("to", [""])[0]
            items = [{**b, "date": b.get("date") or (datetime.fromisoformat(b["lastAt"]).astimezone(TZ).date().isoformat() if b.get("lastAt") else self.lanes.get("today")),
                      "records": self.records_by_block.get(b["blockKey"], self.records)} for b in self.blocks]
            body = {"from": lo, "to": hi, "items": [b for b in items if lo <= b["date"] <= hi]}
        elif key == "activity/settings":
            body = self.settings
        elif key == "timer/unplaced":
            body = {"items": self.unplaced}
        elif key == "detector/devices":
            body = {"devices": self.devices, "now": _iso(self.now())}
        elif key == "views/time-lanes":
            body = getattr(self, "time_lanes", None) or {"today": "", "now": "", "windowStart": "", "windowEnd": "", "human": [], "agents": []}
        elif key == "views/agent-time":
            body = {"agents": [{"agent": "claude-code", "seconds": 7380, "runs": 3}]}
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

    def long_press(self, selector: str) -> None:
        """按住不动满长按时长（假时钟推过去）再松手。"""
        x, y = self.center(selector)
        self.page.mouse.move(x, y)
        self.page.mouse.down()
        self.page.clock.run_for(700)
        self.page.mouse.up()
        self.page.clock.run_for(50)

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
    page.route("**/__cockpit/clockface.js", lambda r: r.fulfill(path=str(COCKPIT / "clockface.js"), content_type="application/javascript"))   # 共享件由 index.html 自己读（真环境里网关的注入排在页面脚本后面，不能指望它）
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.add_init_script("document.hasFocus = () => !!window.__pageInFront")   # 无头浏览器里恒为真；默认当页面不在最前面（「三十一」）
    page.goto(base + "/index.html")
    page.wait_for_selector(CELL.format("p_math"))
    page.wait_for_selector("#stRing .st-tk", state="attached")
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
        {"blockKey": "ptyxis · x", "status": "pending", "seconds": 240, "seq": 1, "displayName": "终端（未认出）", "legible": False, "kind": "terminal"},
    ]


def test_layout_has_ring_clock_bucket_and_cells_only_name_the_project(browser, base) -> None:  # noqa: F811  HV-B01 / HV-S20
    with open_stage(browser, base, setup=_busy_day) as st:
        lines = json.loads(st.page.locator("#stRing").get_attribute("data-lines"))
        assert any(x.startswith("garden\n20m") for x in lines)      # 人此刻在看的代理，是轮播里的一句
        assert "小时" in st.page.locator("#stClock .st-clock-today").inner_text()
        assert st.page.locator("#stBucketLabel").inner_text() == "未分类 · 今天 14 分"
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


def test_drag_loose_pet_into_a_cell_opens_a_confirm_card_and_places_only_after_a_button(browser, base) -> None:  # noqa: F811  二·11
    with open_stage(browser, base, setup=_busy_day) as st:
        st.replies["agents/r2/place"] = (200, {"runId": "r2", "taskId": None, "projectId": "p_piano", "rule": None})
        pet = '.st-pet.is-loose[data-run-id="r2"]'
        assert st.page.locator(pet).count() == 1
        st.drag(pet, st.center(CELL.format("p_piano")), release=False)
        assert st.page.locator(CELL.format("p_piano")).evaluate("e => e.classList.contains('st-hot')")
        st.page.mouse.up()
        st.settle()
        assert st.posts == []                                  # 先问，不直接挂
        assert st.page.locator(".st-file").count() == 0        # 不再是 5 秒气泡
        card = st.page.locator(".st-confirm")
        assert card.count() == 1
        assert card.locator(".st-cf-title").inner_text() == "把 scratch 挂到 钢琴"
        assert [b.inner_text() for b in card.locator(".st-cf-btns button").all()] == ["之前的时间也算过去", "只算以后的时间"]
        assert card.locator(".st-cf-rem").inner_text() == "以后 scratch 都算 钢琴"
        assert card.locator(".st-cf-box").is_checked() is False
        st.page.clock.run_for(20000)
        assert st.page.locator(".st-confirm").count() == 1, "不会自己消失"
        card.get_by_text("之前的时间也算过去").click()
        st.settle()
        assert st.posts == [("agents/r2/place", {"projectId": "p_piano", "remember": False})]
        assert st.page.locator(".st-confirm").count() == 0
        assert st.page.locator('.st-pet.is-work[data-run-id="r2"]').count() == 1      # 飞到格子边


def test_drop_on_blank_sends_nothing_and_pet_returns(browser, base) -> None:  # noqa: F811  HV-B03
    with open_stage(browser, base, setup=_busy_day) as st:
        pet = '.st-pet.is-loose[data-run-id="r2"]'
        st.page.wait_for_timeout(300)
        before = st.box(pet)
        st.drag(pet, (640, 20))
        st.settle()
        assert st.posts == [] and st.page.locator(".st-confirm").count() == 0
        st.page.clock.run_for(15000)                            # 停 3 秒再走回自己的位
        st.page.wait_for_timeout(300)
        after = st.box(pet)
        assert st.page.locator(pet).evaluate("e => e.style.translate") == ""
        assert abs(after["x"] - before["x"]) < 3 and abs(after["y"] - before["y"]) < 3


def test_failed_drop_returns_the_pet_and_says_why(browser, base) -> None:  # noqa: F811  HV-S18
    with open_stage(browser, base, setup=_busy_day) as st:
        st.replies["agents/r2/place"] = (404, {"detail": "x"})
        pet = '.st-pet.is-loose[data-run-id="r2"]'
        st.page.wait_for_timeout(300)
        before = st.box(pet)
        st.drag(pet, st.center(CELL.format("p_piano")))
        st.settle()
        st.page.locator(".st-confirm").get_by_text("之前的时间也算过去").click()
        st.settle()
        assert "目标不存在" in st.toast()
        st.page.clock.run_for(15000)
        st.page.wait_for_timeout(300)
        after = st.box(pet)
        assert st.page.locator(pet).evaluate("e => e.style.translate") == ""
        assert abs(after["x"] - before["x"]) < 3 and abs(after["y"] - before["y"]) < 3
        assert st.page.locator('.st-pet.is-loose[data-run-id="r2"]').count() == 1


def test_loose_pet_keeps_one_fixed_slot_and_never_wanders(browser, base) -> None:  # noqa: F811  二·11 2
    with open_stage(browser, base, setup=_busy_day) as st:
        start = st.box('.st-pet.is-loose[data-run-id="r2"]')
        for _ in range(40):
            st.page.clock.run_for(500)
        end = st.box('.st-pet.is-loose[data-run-id="r2"]')
        assert (start["x"], start["y"]) == (end["x"], end["y"])
        assert end["x"] < 640 and end["y"] < 450


def test_loose_pet_slots_are_stable_when_others_come_and_go(browser, base) -> None:  # noqa: F811  二·11 2
    def three(st: Stage) -> None:
        now = st.now().astimezone(TZ)
        st.set_lanes(agents=[_run(f"r{i}", "codex", label=f"l{i}", startAt=_iso(now - timedelta(seconds=900 - i * 100)), lastSeenAt=_iso(now)) for i in (1, 2, 3)])

    with open_stage(browser, base, setup=three) as st:
        pos = {i: st.box(f'.st-pet.is-loose[data-run-id="r{i}"]') for i in (1, 2, 3)}
        assert len({(b["x"], b["y"]) for b in pos.values()}) == 3
        st.lanes["agents"] = [a for a in st.lanes["agents"] if a["runId"] != "r1"]
        st.poll()
        for i in (2, 3):
            assert st.box(f'.st-pet.is-loose[data-run-id="r{i}"]') == pos[i]


def test_pet_clip_rhythm_main_then_temp_then_back_to_the_same_main_and_a_new_one_after_five_minutes(browser, base) -> None:  # noqa: F811  二十六·2
    pack = {"kind": "gpt", "name": "G", "license": "x", "credits": "x", "source": "x",
            "moods": {"idle": ["i1.webm", "i2.webm", "i3.webm"], "click": ["t.webm"]}}
    with open_stage(browser, base, setup=_busy_day, pets={"gpt": pack}, hold_webm=True) as st:
        def watch(seconds: int) -> list[str]:
            seen: list[str] = []
            for _ in range(seconds):
                st.page.clock.run_for(1000)
                src = st.page.locator('.st-pet.is-loose[data-run-id="r2"] video.st-art').get_attribute("src")
                if src and (not seen or seen[-1] != src):
                    seen.append(src)
            return [u.split("/")[-1] for u in seen]
        seen = watch(60)
        assert "t.webm" in seen                                                # 15 秒主动作后插 3 秒临时动作
        mains = {f for f in seen if f != "t.webm"}
        assert len(mains) == 1, seen                                           # 临时动作完回到原来那个主动作
        st.page.clock.run_for(300000)                                          # 满 5 分钟
        later = {f for f in watch(40) if f != "t.webm"}
        assert later and later != mains, (seen, later)                         # 换了一个主动作


def test_bucket_drawer_drags_a_block_into_a_cell(browser, base) -> None:  # noqa: F811  HV-B04 / HV-B05 / HV-C12
    with open_stage(browser, base, setup=_busy_day) as st:
        st.page.click("#stBucketBtn")
        st.page.clock.run_for(300)
        chips = st.page.locator(".st-chip")
        assert chips.all_inner_texts() == ["github.com\n10 分", "终端（未认出）\n4 分"]
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
        assert st.posts == [("activity/blocks/assign", {"date": today, "blockKey": "gh", "ifSeq": 3, "projectId": "p_math", "remember": False})]
        assert st.toast() == ""
        assert st.page.locator("#stBucketLabel").inner_text() == "未分类 · 今天 4 分"
        assert st.page.locator(".st-chip").count() == 1


def test_grey_row_opens_a_popover_of_same_title_groups_and_a_group_drags_into_a_cell(browser, base) -> None:  # noqa: F811  hive.stage.v1「二」5
    def grey(st: Stage) -> None:
        _busy_day(st)
        st.blocks = [{"blockKey": f"ptyxis · {t}", "status": "pending", "seconds": s, "seq": i, "displayName": None, "legible": False,
                      "kind": "terminal", "apps": ["ptyxis"]} for i, (t, s) in enumerate([("~/work", 120), ("vim a.go", 240)], 1)]

    with open_stage(browser, base, setup=grey) as st:
        st.page.click("#stBucketBtn")
        st.page.clock.run_for(300)
        st.page.click(".st-chip.grey")
        st.page.clock.run_for(300)
        pop = st.page.locator("#stGreyPop .st-chip")
        assert pop.all_inner_texts() == ["vim a.go\n4 分", "~/work\n2 分"]
        st.replies["activity/blocks/assign"] = (200, {})
        st.blocks = st.blocks[:1]
        st.drag("#stGreyPop .st-chip", st.center(CELL.format("p_math")))
        st.settle()
        assert st.posts == [("activity/blocks/assign", {"date": st.lanes["today"], "blockKey": "ptyxis · vim a.go", "ifSeq": 2, "projectId": "p_math", "remember": False})]
        assert st.toast() == ""
        assert st.page.locator(".st-file").inner_text() == "以后这个窗口都算 数学"      # 灰行弹窗里拖出的块同样弹气泡
        st.page.locator(".st-file input").check()
        st.page.wait_for_timeout(250)
        assert st.posts[1][1]["remember"] is True and st.posts[1][1]["ifSeq"] == 3
        assert st.page.locator("#stGreyPop .st-chip").all_inner_texts() == ["~/work\n2 分"]


def test_unrecognised_terminal_of_five_minutes_gets_its_own_loud_card_and_a_short_one_stays_folded(browser, base) -> None:  # noqa: F811  三十三
    def terms(st: Stage) -> None:
        _busy_day(st)
        st.blocks = [{"blockKey": f"ptyxis · {t}", "status": "pending", "seconds": s, "seq": i, "displayName": None, "legible": False,
                      "kind": "terminal", "apps": ["ptyxis"]} for i, (t, s) in enumerate([("ecsagent", 360), ("cfo", 120)], 1)]

    with open_stage(browser, base, setup=terms) as st:
        st.page.click("#stBucketBtn")
        st.page.clock.run_for(300)
        assert st.page.locator("#stDrawer .st-chip").all_inner_texts() == ["ptyxis · ecsagent\n6 分", "终端（未认出）\n2 分"]
        assert st.page.locator(".st-chip.grey").count() == 1 and st.page.locator(".st-chip:not(.nodrag)").count() == 1
        st.page.click(".st-chip.grey")
        st.page.clock.run_for(300)
        assert st.page.locator("#stGreyPop .st-chip").all_inner_texts() == ["cfo\n2 分"]
        st.replies["activity/blocks/assign"] = (200, {})
        st.drag(".st-chip:not(.nodrag)", st.center(CELL.format("p_math")))
        st.settle()
        assert st.posts == [("activity/blocks/assign", {"date": st.lanes["today"], "blockKey": "ptyxis · ecsagent", "ifSeq": 1, "projectId": "p_math", "remember": False})]


def test_block_drop_conflict_keeps_the_block(browser, base) -> None:  # noqa: F811  HV-S13
    with open_stage(browser, base, setup=_busy_day) as st:
        st.replies["activity/blocks/assign"] = (409, {})
        st.page.click("#stBucketBtn")
        st.page.clock.run_for(300)
        st.drag(".st-chip:not(.nodrag)", st.center(CELL.format("p_math")))
        st.settle()
        assert "数据已变化" in st.toast()
        assert st.page.locator(".st-chip").count() == 2
        assert st.page.locator("#stBucketLabel").inner_text() == "未分类 · 今天 14 分"


def _busy_day_unwatched(st: Stage) -> None:
    """_busy_day 里人正看着 r1（带 attend）；「二十一」起这时人的形象会站到 r1 的小人旁边，眼睛停靠位置的用例去掉这条。"""
    _busy_day(st)
    st.lanes["interactions"] = []


def test_eye_follows_focus_between_cells_and_returns_to_the_clock_closed_on_afk(browser, base) -> None:  # noqa: F811  HV-B06 / B07
    with open_stage(browser, base, setup=_busy_day_unwatched) as st:
        def focus(state: str, pid: str | None) -> None:
            st.current = {"running": False, "focus": {"state": state, "app": "code", "title": "t", "since": _iso(st.now()), "projectId": pid,
                                                      "projectName": None, "taskId": None, "taskName": None, "source": "rules"}}

        def near(selector: str) -> bool:
            bx, by = st.center("#stEye")
            b = st.box(selector)
            return b["x"] - 20 <= bx <= b["x"] + b["width"] + 20 and b["y"] - 20 <= by <= b["y"] + b["height"] + 20

        focus("present", "p_math")
        st.poll()
        assert st.page.locator("#stEye").get_attribute("data-host") == "p_math" and near(CELL.format("p_math"))
        assert "is-closed" not in st.page.locator("#stEye").get_attribute("class")
        focus("present", "p_piano")
        with st.page.expect_response("**/api/core/views/current"):
            st.page.clock.run_for(5000)
        st.page.clock.run_for(400)           # 眼睛的检查间隔
        st.page.clock.run_for(600)           # 飞行 ≤ 600ms
        assert st.page.locator("#stEye").get_attribute("data-host") == "p_piano" and near(CELL.format("p_piano"))
        focus("afk", "p_piano")
        with st.page.expect_response("**/api/core/views/current"):
            st.page.clock.run_for(5000)
        st.page.clock.run_for(1000)
        assert st.page.locator("#stEye").get_attribute("data-host") == "clock" and near("#stClock")
        assert "is-closed" in st.page.locator("#stEye").get_attribute("class")           # 离开：闭眼


def test_ring_ticker_line_follows_the_run_the_human_attends(browser, base) -> None:  # noqa: F811  HV-B08（环里轮播的一句）
    with open_stage(browser, base, setup=_busy_day) as st:
        now = st.now().astimezone(TZ)
        st.lanes["interactions"].append({"runId": "r2", "kind": "attend", "at": _iso(now), "until": _iso(now + timedelta(seconds=30))})
        st.poll()
        lines = json.loads(st.page.locator("#stRing").get_attribute("data-lines"))
        assert any(x.startswith("scratch\n") for x in lines) and not any(x.startswith("garden\n") for x in lines)


def test_manual_timer_click_start_drag_to_cell_stop_then_drag_the_strip(browser, base) -> None:  # noqa: F811  HV-B09
    with open_stage(browser, base, setup=_busy_day) as st:
        # 没在计时：拖时钟什么都不发
        st.drag("#stClock", st.center(CELL.format("p_math")))
        st.settle()
        assert st.posts == []
        # 短按只开菜单，不开始计时
        st.page.click("#stClock")
        st.settle()
        assert st.posts == [] and st.page.locator(".cm-panel").is_visible()
        st.page.keyboard.press("Escape")
        assert st.page.locator(".cm-panel").count() == 0
        # 长按 → 正计时气泡 → 立刻开始，不选项目
        st.long_press("#stClock")
        assert st.page.locator(".cm-bubble").all_inner_texts() == ["正计时", "倒计时"]
        st.page.click(".cm-bubble >> text=正计时")
        st.settle()
        assert st.posts == [("timer/start-unplaced", None)]
        now = st.now().astimezone(TZ)
        st.lanes["human"]["running"] = {"startAt": _iso(now), "taskId": None, "projectId": None}
        st.poll()
        assert "is-timing" in st.page.locator("#stClock").get_attribute("class")
        # 计时中拖到格子 = 三选一后记到该项目（三十四）
        st.posts.clear()
        st.drag("#stClock", st.center(CELL.format("p_piano")))
        st.settle()
        card = st.page.locator(".st-confirm")
        assert st.posts == [] and card.locator("[data-wr]").all_inner_texts() == ["忽略记录", "我全都要", "填充空隙"]
        card.locator("[data-wr=ignore]").click()
        st.settle()
        assert st.posts == [("timer/place", {"projectId": "p_piano", "windowRecords": "ignore"})]
        # 计时中长按 = 「停止」气泡；没记到项目的变白条
        st.posts.clear()
        st.replies["timer/stop"] = (200, {"unplacedId": "u1"})
        st.unplaced = [{"id": "u1", "startAt": _iso(now), "endAt": _iso(now + timedelta(minutes=12)), "durationSeconds": 720}]
        st.lanes["human"]["running"] = {"startAt": _iso(now), "taskId": None, "projectId": None}
        st.long_press("#stClock")
        assert st.page.locator(".cm-bubble").all_inner_texts() == ["停止"]
        st.page.click(".cm-bubble")
        st.settle()
        assert st.posts == [("timer/stop", None)]
        st.lanes["human"]["running"] = None
        st.poll()
        strip = st.page.locator(".st-strip")
        assert strip.count() == 1 and strip.inner_text().endswith("· 12 分钟")
        # 白条拖进格子
        st.posts.clear()
        st.unplaced = []
        st.drag(".st-strip", st.center(CELL.format("p_studio")))
        st.settle()
        assert st.posts == [] and st.page.locator(".st-confirm .st-cf-title").inner_text() == "把这段计时记到 录音棚"
        st.page.keyboard.press("Enter")                            # 缺省「我全都要」带着焦点
        st.settle()
        assert st.posts == [("timer/unplaced/u1/place", {"projectId": "p_studio", "windowRecords": "both"})]
        assert st.page.locator(".st-strip").count() == 0


def test_click_cell_expands_the_big_hexagon_with_the_panel_inside(browser, base) -> None:  # noqa: F811  HV-B13；hive.stage.v1「十二」取代原「点格子弹出面板」
    with open_stage(browser, base, setup=_busy_day) as st:
        st.page.click(CELL.format("p_math"))
        cell = st.page.locator(".hex-cell.is-expanded")
        cell.wait_for()
        assert cell.locator(".hex-title").inner_text() == "数学" and st.page.locator("#stVeil").count() == 0
        panel = cell.locator(".hex-ruler-slot")
        assert panel.locator(".pr-row").count() == 2 and panel.locator(".pr-ruler").count() == 1   # 任务条 + 没挂任务；标尺在 test_hive_project_panel.py
        st.page.keyboard.press("Escape")
        assert st.page.locator(".hex-cell.is-expanded").count() == 0
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
        st.page.locator(".st-confirm").get_by_text("之前的时间也算过去").click()
        st.settle()
        assert st.posts == [("agents/r2/place", {"projectId": "p_math", "remember": False})]
        assert st.toast() == ""


def _drag_chip_to_blank(st: Stage, selector: str | None = None) -> None:
    """把可拖的块（或 selector 指的元素）拖出去，在蜂巢画布上扫点，直到出现空白处长出来的那一格（不松手）。"""
    if selector is None:
        st.page.click("#stBucketBtn")
        st.page.clock.run_for(300)
    x, y = st.center(selector or ".st-chip:not(.nodrag)")
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
        st.page.clock.run_for(600)       # 停住亮满
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
        assert body == {"date": st.lanes["today"], "blockKey": "gh", "ifSeq": 3, "projectId": "p_new", "remember": False}


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
        st.page.clock.run_for(600)
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
        assert "activity/blocks" not in st.api and st.api.count("activity/settings") == 2   # 决策栏自己的一次 + 蜂巢页启动读形象的一次，之后不再读
        assert st.page.locator("#stBucketLabel").inner_text() == "未分类 · 今天 0 分"


def test_panel_esc_closes(browser, base) -> None:  # noqa: F811  HV-B13；「十二」：关闭 = 收起大六边形
    with open_stage(browser, base, setup=_busy_day) as st:
        st.page.click(CELL.format("p_math"))
        st.page.wait_for_selector(".hex-cell.is-expanded .pr-row")
        st.page.keyboard.press("Escape")
        assert st.page.locator(".hex-cell.is-expanded").count() == 0
        assert st.page.locator(".hex-ruler-slot").count() == 0 or not st.page.locator(".hex-ruler-slot").first.is_visible()


def test_expanded_hexagon_is_on_top_of_zone_outlines(browser, base) -> None:  # noqa: F811  HV-B13；「十二」：盖住蜂巢的是大六边形本身
    with open_stage(browser, base, setup=_busy_day) as st:
        st.page.click(CELL.format("p_math"))
        st.page.wait_for_selector(".hex-cell.is-expanded .pr-row")
        st.page.clock.run_for(1000)
        st.page.wait_for_timeout(500)
        st.page.clock.run_for(600)
        on_top = st.page.evaluate("""() => {
          const cell = document.querySelector('.hex-cell.is-expanded'), r = cell.getBoundingClientRect(), out = [];
          const pill = document.querySelector('.hex-zone-label');   // 分区名牌层级高于舞台层：挪到六边形正中
          if (!pill) return 'no zone label';
          const pr = pill.getBoundingClientRect();
          pill.style.translate = (r.left + r.width / 2 - pr.left - pr.width / 2) + 'px ' + (r.top + r.height / 2 - pr.top - pr.height / 2) + 'px';
          const c = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
          out.push(!!(c && c.closest('.hex-cell.is-expanded')));
          return out.every(Boolean);
        }""")
        assert on_top is True


def test_panel_reads_only_known_endpoints_and_never_agent_usage(browser, base) -> None:  # noqa: F811  HV-B14 / HV-B15
    with open_stage(browser, base, setup=_busy_day) as st:
        st.api.clear()
        st.page.click(CELL.format("p_math"))
        st.poll()
        known = {"views/lanes", "views/current", "views/tree", "activity/blocks", "activity/settings", "timer/unplaced", "views/agent-time", "activity/ledger", "detector/devices", "views/project-time"}
        assert set(st.api) <= known, st.api
        assert not [u for u in st.requests if "/usage" in u or "/agents/" in u], st.requests
        for f in ("hex-stage-page.js", "hex-record.js"):
            assert "usage" not in st.page.evaluate("(f) => fetch(f).then(r => r.text())", f)


def _eye_on_clock_corner(st: Stage) -> bool:
    (ex, ey), c = st.center("#stEye"), st.box("#stClock")
    return abs(ex - c["x"]) < 6 and abs(ey - c["y"]) < 6


def test_dragging_a_timer_shows_a_bin_bottom_right_and_dropping_in_it_throws_it_away(browser, base) -> None:  # noqa: F811  三十四
    with open_stage(browser, base, setup=_busy_day) as st:
        st.drag('.st-pet.is-loose[data-run-id="r2"]', st.center(CELL.format("p_piano")), release=False)   # 别的东西拖起来：没有垃圾桶
        assert st.page.locator(".st-trash.is-on").count() == 0
        st.page.keyboard.press("Escape")
        st.page.mouse.up()
        now = st.now().astimezone(TZ)
        st.unplaced = [{"id": "u1", "startAt": _iso(now), "endAt": _iso(now + timedelta(minutes=12)), "durationSeconds": 720}]
        st.poll()
        st.posts.clear()
        st.drag(".st-strip", st.center(CELL.format("p_piano")), release=False)
        bin_ = st.page.locator(".st-trash.is-on")
        b, vw, vh = st.box(".st-trash.is-on"), st.page.viewport_size["width"], st.page.viewport_size["height"]
        assert vw - (b["x"] + b["width"]) < 40 and vh - (b["y"] + b["height"]) < 40
        st.page.mouse.move(*st.center(".st-trash"), steps=6)
        assert "st-hot" in bin_.get_attribute("class") and st.page.locator(".st-hot.hex-cell").count() == 0
        st.unplaced = []
        st.page.mouse.up()
        st.settle()
        assert st.posts == [("timer/unplaced/u1", None)] and st.page.locator(".st-confirm").count() == 0
        st.page.clock.run_for(600)
        assert st.page.locator(".st-strip").count() == 0 and st.page.locator(".st-trash.is-on").count() == 0
        # 在跑的计时（眼睛跟着时钟）拖进去 = 取消计时
        st.lanes["human"]["running"] = {"startAt": _iso(now), "taskId": None, "projectId": None}
        st.poll()
        st.drag("#stEye", st.center(CELL.format("p_piano")), release=False)
        st.page.mouse.move(*st.center(".st-trash"), steps=6)
        st.page.mouse.up()
        st.settle()
        assert st.posts[-1] == ("timer/cancel", None) and st.page.locator(".st-confirm").count() == 0


def test_drag_the_eye_while_timing_asks_then_places_the_timer(browser, base) -> None:  # noqa: F811  十·3
    with open_stage(browser, base, setup=_busy_day) as st:
        st.drag("#stEye", st.center(CELL.format("p_piano")))      # 没在计时、也没有窗口心跳：拖不动
        st.settle()
        assert st.posts == [] and "st-dragging" not in (st.page.locator("#stEye").get_attribute("class") or "")
        now = st.now().astimezone(TZ)
        st.lanes["human"]["running"] = {"startAt": _iso(now), "taskId": None, "projectId": None}
        st.poll()
        assert _eye_on_clock_corner(st) and "is-timing" in st.page.locator("#stEye").get_attribute("class")
        st.drag("#stEye", st.center(CELL.format("p_piano")))
        st.settle()
        card = st.page.locator(".st-confirm")
        assert st.posts == [] and card.locator(".st-cf-title").inner_text() == "把这次计时记到 钢琴"
        assert card.locator(".st-cf-box").count() == 0            # 计时没有「以后都算」
        card.locator("[data-wr=fill]").click()
        st.settle()
        assert st.posts == [("timer/place", {"projectId": "p_piano", "windowRecords": "fill"})] and st.page.locator(".st-confirm").count() == 0


def test_click_the_eye_on_the_clock_opens_the_menu_not_stop(browser, base) -> None:  # noqa: F811  十·1（点眼睛 = 点时钟）
    with open_stage(browser, base, setup=_busy_day) as st:
        now = st.now().astimezone(TZ)
        st.lanes["human"]["running"] = {"startAt": _iso(now), "taskId": None, "projectId": None}
        st.poll()
        assert _eye_on_clock_corner(st)                     # 眼睛别在时钟卡的左上角
        st.page.mouse.click(*st.center("#stEye"))
        st.settle()
        assert st.posts == [] and st.page.locator(".cm-panel").is_visible()


def test_panel_is_not_rebuilt_by_polling_and_survives_cell_refill(browser, base) -> None:  # noqa: F811  「十二」
    with open_stage(browser, base, setup=_busy_day) as st:
        st.page.click(CELL.format("p_math"))
        st.page.wait_for_selector(".hex-cell.is-expanded .pr-row")
        st.page.evaluate("() => document.querySelector('.hex-ruler-slot .pr-ruler').dataset.mark = '1'")
        st.poll()
        assert st.page.locator(".hex-ruler-slot .pr-ruler[data-mark='1']").count() == 1   # 没变就不重画
        st.page.evaluate("() => document.querySelector('.hex-ruler-slot').innerHTML = ''")   # 格子被 hex-app 重写内容时槽会变空
        st.page.wait_for_selector(".hex-ruler-slot .pr-row")


def test_there_is_no_separate_panel_or_expand_button(browser, base) -> None:  # noqa: F811  「十二」取代原「展开」按钮
    with open_stage(browser, base, setup=_busy_day) as st:
        st.page.click(CELL.format("p_math"))
        st.page.wait_for_selector(".hex-cell.is-expanded[data-project-id=p_math]")
        assert st.page.locator(".st-expand, #stVeil, #stPanel").count() == 0


TOUCH_PACK = {"kind": "claude", "name": "Claude", "license": "x", "credits": "x", "source": "x",
              "moods": {"working": ["w.webm"], "waiting": ["wait.webm"], "idle": ["i.webm"], "click": ["c1.webm", "c2.webm"],
                        "drag": ["d.webm"], "walk": ["k.webm", "k2.webm"]}}
PET = ".st-pet.is-work"
# 停着时按动作节奏可能正在 3 秒临时动作（click），所以 w 或 c1 / c2 都算对（二·11）
STAY_CLIPS = ("w.webm", "c1.webm", "c2.webm")
PET_ART = PET + " video.st-art"


def _one_worker(st: Stage) -> None:
    now = st.now().astimezone(TZ)
    st.set_lanes(agents=[_run("r1", "claude-code", label="g", projectId="p_math", startAt=_iso(now), lastSeenAt=_iso(now),
                              phases=[{"at": _iso(now), "phase": "working", "detail": None}])])


def _src(st: Stage) -> str:
    return st.page.locator(PET_ART).get_attribute("src") or ""


def test_click_a_pet_plays_a_click_clip_once_with_a_squash_then_returns(browser, base) -> None:  # noqa: F811  hive.stage.v1「二」7，「三十二」3（点击同时打开 Agent 页）
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
        assert _src(st).split("/")[-1] in STAY_CLIPS      # 回到状态动作（节奏里 3 秒临时动作也可能正好在播）
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
        assert abs(away["x"] + away["width"] / 2 - 900) < 30 and _src(st).split("/")[-1] in STAY_CLIPS
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
        assert _src(st).split("/")[-1] in STAY_CLIPS
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


def test_grab_right_after_a_click_swaps_to_the_drag_clip_and_the_stay_shows_the_normal_clip(browser, base) -> None:  # noqa: F811  hive.stage.v1「二」7，「三十二」3
    with open_stage(browser, base, setup=_one_worker, pets={"claude": TOUCH_PACK}, hold_webm=True) as st:
        st.page.clock.run_for(600)
        x, y = st.center(PET + " .st-media")
        st.page.mouse.click(x, y)
        assert _src(st).split("/")[-1] in ("c1.webm", "c2.webm")
        st.page.keyboard.press("Escape")                 # 点击同时打开了 Agent 页：关掉再拖
        st.page.wait_for_timeout(450)
        st.page.clock.run_for(300)
        st.drag(PET + " .st-media", (900, 120), release=False)
        assert _src(st).endswith("/claude/d.webm")
        st.page.mouse.up()
        st.page.clock.run_for(1000)
        assert _src(st).split("/")[-1] in STAY_CLIPS
        assert not st.page.locator(PET).evaluate("e => e.classList.contains('st-walking')")
        st.page.clock.run_for(2500)
        assert _src(st).endswith("/claude/k.webm")      # 走路段固定取 walk[0]


def test_clock_card_stays_clear_of_the_collapsed_decide_bar(browser, base) -> None:  # noqa: F811
    with open_stage(browser, base, setup=_busy_day) as st:
        st.page.evaluate("""() => { const p = document.querySelector('.hd-panel'); p.hidden = false; p.dataset.collapsed = '1';
            p.innerHTML = '<button class="hd-head" type="button"><span class="hd-title">AI 决策</span><span class="hd-badge">3</span></button>'; }""")
        st.page.wait_for_timeout(300)
        bar, clock, strips = st.box(".hd-panel"), st.box("#stClock"), st.box("#stStrips")
        assert clock["y"] >= bar["y"] + bar["height"], (bar, clock)
        assert strips["y"] >= clock["y"] + clock["height"], (clock, strips)
        assert _eye_on_clock_corner(st), (st.box("#stClock"), st.box("#stEye"))     # 眼睛跟着时钟，不悬在空档里
        st.page.evaluate("() => { document.querySelector('.hd-panel').hidden = true; }")
        st.page.wait_for_timeout(100)
        assert _eye_on_clock_corner(st)


def test_tray_gets_one_hex_chip_per_new_block_and_loses_one_when_filed(browser, base) -> None:  # noqa: F811  桶=托盘
    def chips(st: Stage) -> dict[str, str]:
        return st.page.evaluate("() => Object.fromEntries([...document.querySelectorAll('.st-tray-chip')].map((g, i) => [i, g.getAttribute('transform')]))")

    with open_stage(browser, base, setup=_busy_day) as st:
        before = chips(st)
        assert len(before) == 2 and st.page.locator(".st-hexchip.st-drop").count() == 0     # 首屏不掉落
        st.blocks = st.blocks + [{"blockKey": "new", "status": "pending", "seconds": 120, "seq": 1, "displayName": "新窗口", "legible": True, "kind": "browser"}]
        st.page.clock.run_for(40000)
        st.page.wait_for_timeout(200)
        after = chips(st)
        assert len(after) == 3 and {k: after[k] for k in before} == before                  # 只多一个，旧的不动
        assert st.page.locator(".st-hexchip.st-drop").count() == 1
        st.blocks = st.blocks[1:]
        st.page.clock.run_for(40000)
        st.page.clock.run_for(600)
        assert len(chips(st)) == 2

# ── 时间元素拖到空白处停住亮满 → 建项目 → 记入；白条点开项目列表与标签（「二」8、9）──
def _create_and_check(st: Stage, assign: tuple[str, Any]) -> None:
    st.replies["planner/projects"] = (200, {"id": "p_new", "name": "x", "zoneId": "z_a"})
    form = st.page.locator(".hex-cell.is-expanded .hex-newcard")
    form.wait_for()
    st.page.clock.run_for(600)
    assert st.posts == [], "没确认什么都不建"
    form.locator('input[name="name"]').fill("新项目")
    form.locator('button[type="submit"]').click()
    st.page.wait_for_timeout(500)
    st.page.clock.run_for(600)
    st.page.wait_for_timeout(300)
    assert [k for k, _ in st.posts] == ["planner/projects", assign[0]], st.posts
    assert st.posts[1][1] == assign[1]


def test_blank_release_before_the_ghost_is_lit_creates_nothing(browser, base) -> None:  # noqa: F811
    with open_stage(browser, base, width=1900, setup=_busy_day) as st:
        _drag_chip_to_blank(st)
        st.page.clock.run_for(100)
        st.page.mouse.up()
        st.settle()
        assert st.posts == [] and st.page.locator(".hex-newcard").count() == 0
        assert st.page.locator(".hex-sprout").count() == 0 and st.page.locator(".st-chip").count() == 2


def test_loose_pet_held_on_blank_creates_a_project_and_is_placed_there(browser, base) -> None:  # noqa: F811
    with open_stage(browser, base, width=1900, setup=_busy_day) as st:
        _drag_chip_to_blank(st, '.st-pet.is-loose[data-run-id="r2"]')
        st.page.clock.run_for(600)
        st.page.mouse.up()
        _create_and_check(st, ("agents/r2/place", {"projectId": "p_new", "remember": False}))


def test_timing_clock_held_on_blank_retargets_the_running_timer_to_the_new_project(browser, base) -> None:  # noqa: F811
    with open_stage(browser, base, width=1900, setup=_busy_day) as st:
        st.lanes["human"]["running"] = {"startAt": _iso(st.now().astimezone(TZ)), "taskId": None, "projectId": None}
        st.poll()
        _drag_chip_to_blank(st, "#stEye")
        st.page.clock.run_for(600)
        st.page.mouse.up()
        _create_and_check(st, ("timer/place", {"projectId": "p_new"}))


def _with_strip(st: Stage) -> None:
    _busy_day(st)
    t0 = datetime(2026, 10, 9, 14, 5, tzinfo=TZ)
    st.unplaced = [{"id": "u1", "startAt": _iso(t0), "endAt": _iso(t0 + timedelta(minutes=12)), "durationSeconds": 720},
                   {"id": "u2", "startAt": _iso(t0 + timedelta(hours=1)), "endAt": _iso(t0 + timedelta(hours=1, minutes=30)), "title": "散步"}]


def test_strip_shows_a_manual_timer_label_with_start_time_or_title(browser, base) -> None:  # noqa: F811
    with open_stage(browser, base, setup=_with_strip) as st:
        assert st.page.locator(".st-strip").all_inner_texts() == ["手动计时 · 14:05 开始 · 12 分钟", "手动计时 · 散步 · 30 分钟"]
        assert st.page.locator(".st-strip").first.evaluate("e => getComputedStyle(e).textOverflow") == "ellipsis"


def test_clicking_a_strip_lists_projects_and_picking_one_places_the_strip(browser, base) -> None:  # noqa: F811
    with open_stage(browser, base, setup=_with_strip) as st:
        x, y = st.center(".st-strip")
        st.page.mouse.click(x, y)
        pop = st.page.locator(".st-pop")
        assert pop.count() == 1 and pop.locator(".st-pop-item").all_inner_texts() == ["数学", "钢琴", "录音棚"]
        assert pop.locator(".st-pop-q").count() == 0 and st.posts == []
        st.page.keyboard.press("Escape")
        assert st.page.locator(".st-pop").count() == 0
        st.page.mouse.click(x, y)
        st.page.keyboard.press("ArrowDown")
        st.page.keyboard.press("Enter")
        st.settle()
        assert st.posts == [] and st.page.locator(".st-pop").count() == 0   # 三十四：选了项目也先三选一
        st.page.locator(".st-confirm [data-wr=ignore]").click()
        st.settle()
        assert st.posts == [("timer/unplaced/u1/place", {"projectId": "p_piano", "windowRecords": "ignore"})]


def test_pet_released_on_blank_before_lit_or_with_esc_leaves_no_ghost_and_sends_nothing(browser, base) -> None:  # noqa: F811
    for sel in ('.st-pet.is-loose[data-run-id="r2"]', '.st-pet.is-work[data-run-id="r1"]'):
        with open_stage(browser, base, width=1900, setup=_busy_day) as st:
            _drag_chip_to_blank(st, sel)
            st.page.clock.run_for(100)
            st.page.mouse.up()
            st.settle()
            assert st.page.locator(".hex-sprout").count() == 0 and st.page.locator(".hex-newcard").count() == 0 and st.posts == [], sel
            _drag_chip_to_blank(st, sel)
            st.page.clock.run_for(600)
            st.page.keyboard.press("Escape")
            st.page.mouse.up()
            st.settle()
            assert st.page.locator(".hex-sprout").count() == 0 and st.page.locator(".hex-newcard").count() == 0 and st.posts == [], sel


def test_strip_held_on_blank_creates_a_project_and_is_placed_there(browser, base) -> None:  # noqa: F811
    with open_stage(browser, base, width=1900, setup=_with_strip) as st:
        _drag_chip_to_blank(st, ".st-strip")
        st.page.clock.run_for(600)
        st.page.mouse.up()
        _create_and_check(st, ("timer/unplaced/u1/place", {"projectId": "p_new"}))


def test_cell_pet_held_on_blank_waits_while_the_form_is_open_then_walks_back_on_cancel(browser, base) -> None:  # noqa: F811
    sel = '.st-pet.is-work[data-run-id="r1"]'
    with open_stage(browser, base, width=1900, setup=_busy_day) as st:
        _drag_chip_to_blank(st, sel)
        st.page.clock.run_for(600)
        st.page.mouse.up()
        st.page.locator(".hex-cell.is-expanded .hex-newcard").wait_for()
        st.page.clock.run_for(1000)
        pos = "e => e.style.left + ',' + e.style.top"      # 页面会因表单聚焦滚动，比层内坐标不比屏幕坐标
        a = st.page.locator(sel).evaluate(pos)
        st.page.clock.run_for(6000)
        assert st.page.locator(sel).evaluate(pos) == a, "表单开着：原地等"
        st.page.locator('.hex-newcard [data-hex-ui="close"]').click()
        st.page.clock.run_for(8000)
        assert st.page.locator(sel).evaluate(pos) != a, "取消后走回"
        assert st.posts == []


def test_cell_pet_held_on_blank_is_placed_after_the_form(browser, base) -> None:  # noqa: F811
    with open_stage(browser, base, width=1900, setup=_busy_day) as st:
        _drag_chip_to_blank(st, '.st-pet.is-work[data-run-id="r1"]')
        st.page.clock.run_for(600)
        st.page.mouse.up()
        _create_and_check(st, ("agents/r1/place", {"projectId": "p_new", "remember": False}))


def test_strip_blank_create_then_place_fails_with_409_puts_the_strip_back_with_a_toast(browser, base) -> None:  # noqa: F811
    with open_stage(browser, base, width=1900, setup=_with_strip) as st:
        st.replies["timer/unplaced/u1/place"] = (409, {})
        st.replies["planner/projects"] = (200, {"id": "p_new", "name": "x", "zoneId": "z_a"})
        _drag_chip_to_blank(st, ".st-strip")
        st.page.clock.run_for(600)
        st.page.mouse.up()
        form = st.page.locator(".hex-cell.is-expanded .hex-newcard")
        form.wait_for()
        st.page.clock.run_for(600)
        form.locator('input[name="name"]').fill("新项目")
        form.locator('button[type="submit"]').click()
        st.page.wait_for_timeout(600)
        st.page.clock.run_for(600)
        assert st.toast().startswith("失败")
        assert st.page.locator(".st-strip").count() == 2 and st.page.locator(".st-strip").first.evaluate("e => e.style.translate") == ""


def test_strip_popover_toggles_opens_from_keyboard_and_closes_when_its_strip_goes(browser, base) -> None:  # noqa: F811
    with open_stage(browser, base, setup=_with_strip) as st:
        x, y = st.center(".st-strip")
        st.page.mouse.click(x, y)
        assert st.page.locator(".st-pop").count() == 1
        st.page.mouse.click(x, y)
        assert st.page.locator(".st-pop").count() == 0, "再点同一条 = 收起"
        st.page.locator(".st-strip").first.focus()
        st.page.keyboard.press("Enter")
        assert st.page.locator(".st-pop").count() == 1
        st.page.keyboard.press("Escape")
        assert st.page.locator(".st-pop").count() == 0
        assert st.page.evaluate("() => document.activeElement.className") == "st-strip", "Esc 把焦点还给白条"
        st.page.locator(".st-strip").first.focus()
        st.page.keyboard.press("Space")
        assert st.page.locator(".st-pop").count() == 1
        st.unplaced = [u for u in st.unplaced if u["id"] != "u1"]
        st.poll()
        assert st.page.locator(".st-pop").count() == 0, "白条没了，列表跟着收"


# ── 归档气泡与详情卡（hive.stage.v1「二」10，2026-11-08）──

def _named_run(st: Stage) -> None:
    _busy_day(st)
    now = st.now().astimezone(TZ)
    st.lanes["agents"][0].update(conversation="改错题", model="opus", match="ptyxis · garden", humanName=None)
    st.lanes["agents"][1].update(conversation=None, humanName=None)
    st.lanes["agents"][0]["phases"].append({"at": _iso(now - timedelta(seconds=300)), "phase": "waiting_input", "detail": None})


def _drop_pet(st: Stage, runid: str = "r2", cell: str = "p_piano") -> None:
    st.replies[f"agents/{runid}/place"] = (200, {"runId": runid, "projectId": cell, "rule": None})
    sel = f'.st-pet[data-run-id="{runid}"]'
    st.drag(sel, st.center(CELL.format(cell)))
    st.settle()
    st.page.mouse.move(5, 5)


def test_confirm_card_buttons_send_the_right_body_and_the_checkbox_means_remember(browser, base) -> None:  # noqa: F811  二·11 6
    with open_stage(browser, base, setup=_busy_day) as st:
        _drop_pet(st)
        st.page.locator(".st-confirm .st-cf-box").check()
        st.page.locator(".st-confirm").get_by_text("只算以后的时间").click()
        st.settle()
        assert st.posts == [("agents/r2/place", {"projectId": "p_piano", "remember": True, "timeFrom": "now"})]


def test_confirm_card_cancel_by_x_or_esc_sends_nothing_and_the_pet_walks_back(browser, base) -> None:  # noqa: F811  二·11 6
    with open_stage(browser, base, setup=_busy_day) as st:
        pet = '.st-pet.is-loose[data-run-id="r2"]'
        st.page.wait_for_timeout(300)
        before = st.box(pet)
        _drop_pet(st)
        assert st.page.locator(".st-confirm").count() == 1
        st.page.locator(".st-cf-x").click()
        assert st.page.locator(".st-confirm").count() == 0
        _drop_pet(st)
        st.page.keyboard.press("Escape")
        assert st.page.locator(".st-confirm").count() == 0
        st.page.clock.run_for(15000)
        st.page.wait_for_timeout(300)
        after = st.box(pet)
        assert st.posts == []
        assert abs(after["x"] - before["x"]) < 3 and abs(after["y"] - before["y"]) < 3


def test_a_pet_sitting_in_a_cell_can_be_dragged_to_another_project_with_a_card(browser, base) -> None:  # noqa: F811  二·11 6
    with open_stage(browser, base, setup=_busy_day) as st:
        _drop_pet(st, "r1", "p_piano")
        assert st.page.locator(".st-confirm .st-cf-title").inner_text() == "把 garden 挂到 钢琴"
        st.page.locator(".st-confirm").get_by_text("之前的时间也算过去").click()
        st.settle()
        assert st.posts == [("agents/r1/place", {"projectId": "p_piano", "remember": False})]


def test_dropping_on_the_pets_own_cell_asks_nothing(browser, base) -> None:  # noqa: F811  二·11 6
    with open_stage(browser, base, setup=_busy_day) as st:
        _drop_pet(st, "r1", "p_math")
        assert st.page.locator(".st-confirm").count() == 0 and st.posts == []


def test_two_pets_on_one_cell_stack_two_cards_answered_independently(browser, base) -> None:  # noqa: F811  二·11 6
    with open_stage(browser, base, setup=_busy_day) as st:
        _drop_pet(st, "r2", "p_piano")
        _drop_pet(st, "r1", "p_piano")
        cards = st.page.locator(".st-confirm")
        assert cards.count() == 2
        assert st.page.locator(".st-cf-stack").count() == 1
        a, b = cards.nth(0).bounding_box(), cards.nth(1).bounding_box()
        assert b["y"] >= a["y"] + a["height"] - 1 and abs(a["x"] - b["x"]) < 1        # 新的在下面
        assert cards.nth(0).locator(".st-cf-rem").inner_text() == "以后 scratch 都算 钢琴"
        assert cards.nth(1).locator(".st-cf-rem").inner_text() == "以后 garden 都算 钢琴"
        cards.nth(1).get_by_text("只算以后的时间").click()
        st.settle()
        assert st.posts == [("agents/r1/place", {"projectId": "p_piano", "remember": False, "timeFrom": "now"})]
        assert st.page.locator(".st-confirm").count() == 1
        st.page.locator(".st-confirm").get_by_text("之前的时间也算过去").click()
        st.settle()
        assert st.posts[1] == ("agents/r2/place", {"projectId": "p_piano", "remember": False})
        assert st.page.locator(".st-confirm").count() == 0


def test_esc_cancels_only_the_newest_card_and_outside_click_cancels_none(browser, base) -> None:  # noqa: F811  二·11 6
    with open_stage(browser, base, setup=_busy_day) as st:
        _drop_pet(st, "r2", "p_piano")
        _drop_pet(st, "r1", "p_studio")
        assert st.page.locator(".st-confirm").count() == 2 and st.page.locator(".st-cf-stack").count() == 2
        st.page.mouse.click(5, 5)
        assert st.page.locator(".st-confirm").count() == 2
        st.page.keyboard.press("Escape")
        assert st.page.locator(".st-confirm").count() == 1
        assert st.page.locator(".st-confirm .st-cf-title").inner_text() == "把 scratch 挂到 钢琴"
        assert st.posts == []


def test_422_from_an_old_server_shows_the_failure_and_keeps_the_pet(browser, base) -> None:  # noqa: F811  二·11 6
    with open_stage(browser, base, setup=_busy_day) as st:
        _drop_pet(st)
        st.replies["agents/r2/place"] = (422, {"detail": "x"})
        st.page.locator(".st-confirm").get_by_text("只算以后的时间").click()
        st.settle()
        assert "不能记到这里" in st.toast()
        st.page.clock.run_for(15000)
        assert st.page.locator('.st-pet.is-loose[data-run-id="r2"]').count() == 1


def _drop_block(st: Stage) -> None:
    st.page.click("#stBucketBtn")
    st.page.clock.run_for(300)
    st.replies["activity/blocks/assign"] = (200, {"seq": 4})
    st.drag(".st-chip:not(.nodrag)", st.center(CELL.format("p_math")))
    st.settle()
    st.page.mouse.move(5, 5)


def test_file_bubble_counts_down_five_seconds_and_hover_pauses_it(browser, base) -> None:  # noqa: F811
    with open_stage(browser, base, setup=_busy_day) as st:
        _drop_block(st)
        st.page.clock.run_for(4000)
        assert st.page.locator(".st-file").count() == 1
        arc = st.page.locator(".st-file-arc").get_attribute("stroke-dashoffset")
        assert 0 < float(arc) < 62.9
        x, y = st.center(".st-file")
        st.page.mouse.move(x, y)
        st.page.clock.run_for(6000)
        assert st.page.locator(".st-file").count() == 1, "悬停时暂停"
        st.page.mouse.move(5, 5)
        st.page.clock.run_for(1500)
        assert st.page.locator(".st-file").count() == 0


def test_block_drop_shows_a_bubble_with_the_window_wording(browser, base) -> None:  # noqa: F811
    with open_stage(browser, base, setup=_busy_day) as st:
        st.page.click("#stBucketBtn")
        st.page.clock.run_for(300)
        st.replies["activity/blocks/assign"] = (200, {"seq": 4})
        st.drag(".st-chip:not(.nodrag)", st.center(CELL.format("p_math")))
        st.settle()
        st.page.mouse.move(5, 5)
        assert st.page.locator(".st-file").inner_text() == "以后这个窗口都算 数学"
        st.page.locator(".st-file input").check()
        st.page.wait_for_timeout(250)
        assert st.posts[1][0] == "activity/blocks/assign"
        assert st.posts[1][1] == {"date": st.lanes["today"], "blockKey": "gh", "ifSeq": 4, "projectId": "p_math", "remember": True}


def test_strip_drop_shows_no_bubble(browser, base) -> None:  # noqa: F811
    def setup(st: Stage) -> None:
        _busy_day(st)
        now = st.now().astimezone(TZ)
        st.unplaced = [{"id": "u1", "startAt": _iso(now - timedelta(minutes=30)), "endAt": _iso(now - timedelta(minutes=10)), "title": "x"}]

    with open_stage(browser, base, setup=setup) as st:
        st.poll()
        st.drag(".st-strip", st.center(CELL.format("p_math")))
        st.settle()
        st.page.locator(".st-confirm [data-wr=both]").click()
        st.settle()
        assert st.posts == [("timer/unplaced/u1/place", {"projectId": "p_math", "windowRecords": "both"})]
        assert st.page.locator(".st-file").count() == 0


def test_hover_a_pet_opens_the_card_after_300ms(browser, base) -> None:  # noqa: F811  三十二·3（原「点击小人出卡」）
    with open_stage(browser, base, setup=_named_run, pets={"claude": TOUCH_PACK}, hold_webm=True) as st:
        st.page.clock.run_for(600)
        x, y = st.center('.st-pet[data-run-id="r1"] .st-media')
        st.page.mouse.move(3, 3)
        st.page.mouse.move(x, y)
        st.page.clock.run_for(250)
        assert st.page.locator(".st-detail").count() == 0
        st.page.clock.run_for(100)
        card = st.page.locator(".st-detail")
        assert card.count() == 1
        text = card.inner_text()
        assert "数学·改错题" in text and "claude-code · opus" in text and "归在 数学" in text and "ptyxis · garden" in text
        assert st.page.locator('.st-pet[data-run-id="r1"] .st-tag').inner_text() == "数学·改错题"
        st.page.keyboard.press("Escape")
        assert st.page.locator(".st-detail").count() == 0
        st.page.mouse.move(3, 3)
        st.page.clock.run_for(100)
        st.page.mouse.move(x, y)
        st.page.clock.run_for(350)
        assert st.page.locator(".st-detail").count() == 1
        st.page.mouse.move(5, 5, steps=3)
        st.page.clock.run_for(400)
        assert st.page.locator(".st-detail").count() == 0


def _open_card(st: Stage) -> None:
    x, y = st.center('.st-pet[data-run-id="r1"] .st-media')
    st.page.mouse.move(3, 3)
    st.page.mouse.move(x, y)
    st.page.clock.run_for(350)


def test_rename_by_clicking_the_title_puts_the_name_and_the_tag_updates(browser, base) -> None:  # noqa: F811
    with open_stage(browser, base, setup=_named_run) as st:
        _open_card(st)
        assert st.page.locator(".st-d-name").count() == 0, "没有单独的改名框"
        st.page.locator(".st-d-title").click()
        assert st.page.locator(".st-d-name").input_value() == "数学·改错题"      # 预填当前标题
        st.replies["agents/r1/name"] = (200, {})
        st.lanes["agents"][0]["humanName"] = "园丁"      # 桩替服务端记住
        st.page.locator(".st-d-name").fill("园丁")
        st.page.locator(".st-d-name").press("Enter")
        st.page.wait_for_timeout(250)
        assert st.posts == [("agents/r1/name", {"name": "园丁"})]
        assert st.page.locator('.st-pet[data-run-id="r1"] .st-tag').inner_text() == "园丁"
        assert st.page.locator(".st-detail").count() == 1 and st.page.locator(".st-d-title").inner_text() == "园丁"
        assert st.page.locator(".st-d-name").count() == 0


def test_rename_esc_cancels_blur_cancels_empty_clears_and_failure_marks(browser, base) -> None:  # noqa: F811
    with open_stage(browser, base, setup=_named_run) as st:
        _open_card(st)
        st.page.locator(".st-d-title").click()
        st.page.locator(".st-d-name").fill("x")
        st.page.keyboard.press("Escape")
        assert st.page.locator(".st-detail").count() == 1 and st.page.locator(".st-d-name").count() == 0
        assert st.page.locator(".st-d-title").inner_text() == "数学·改错题"
        st.page.locator(".st-d-title").click()
        st.page.locator(".st-detail .st-d-tool").click()                       # 失焦
        assert st.page.locator(".st-d-name").count() == 0 and st.posts == []
        st.lanes["agents"][0]["humanName"] = "旧名"
        st.poll()
        st.page.locator(".st-d-title").click()
        st.page.locator(".st-d-name").fill("")
        st.replies["agents/r1/name"] = (500, {})
        st.page.locator(".st-d-name").press("Enter")
        st.page.wait_for_timeout(250)
        assert st.posts == [("agents/r1/name", {"name": None})]
        assert "is-bad" in st.page.locator(".st-d-name").get_attribute("class")


def test_card_title_shows_the_full_long_name(browser, base) -> None:  # noqa: F811
    def setup(st: Stage) -> None:
        _named_run(st)
        st.lanes["agents"][0]["humanName"] = "这是一个非常非常长的代理名字用来确认卡片标题不会被截断成省略号" * 2
    with open_stage(browser, base, setup=setup) as st:
        _open_card(st)
        t = st.page.locator(".st-d-title")
        assert t.evaluate("e => e.scrollWidth <= e.clientWidth + 1 && e.scrollHeight <= e.clientHeight + 1")
        assert t.evaluate("e => getComputedStyle(e).textOverflow") != "ellipsis"
        assert t.inner_text() == st.lanes["agents"][0]["humanName"]


def test_bubble_body_click_opens_the_card(browser, base) -> None:  # noqa: F811
    with open_stage(browser, base, setup=_busy_day) as st:
        _drop_block(st)
        st.page.locator(".st-file-tx").click()
        assert st.page.locator(".st-detail").count() == 1
        assert st.page.locator(".st-file-box").evaluate("e => e.checked") is False and len(st.posts) == 1
        assert st.box(".st-file-box")["width"] >= 20


def test_card_shows_a_three_hour_lane_and_stays_live_keeping_the_typed_name(browser, base) -> None:  # noqa: F811
    with open_stage(browser, base, setup=_named_run) as st:
        st.api.clear()
        _open_card(st)
        st.page.wait_for_timeout(250)
        text = st.page.locator(".st-detail").inner_text()
        assert "本周" not in text and "views/agent-time" not in st.api
        assert "等你 · 近 3 小时工作 15 分" in text
        assert st.page.locator(".st-d-hist").count() == 0
        assert st.page.locator(".st-lane-ticks span").count() == 4
        segs = st.page.locator(".st-lane i")
        assert segs.count() == 2
        assert segs.nth(0).get_attribute("class") == "is-working" and segs.nth(1).get_attribute("class") == "is-waiting"
        import re
        assert re.fullmatch(r"\d\d:\d\d–\d\d:\d\d 工作中", segs.nth(0).get_attribute("title"))
        before = st.page.locator(".st-d-name").count()
        st.page.locator(".st-d-title").click()
        st.page.locator(".st-d-name").fill("半截")
        st.lanes["agents"][0]["model"] = "sonnet"
        st.poll()
        assert "claude-code · sonnet" in st.page.locator(".st-detail").inner_text()
        assert st.page.locator(".st-d-name").input_value() == "半截" and before == 0
        assert st.page.evaluate("() => document.activeElement.className") == "st-d-name"


def test_inbox_runs_are_treated_as_loose_everywhere(browser, base) -> None:  # noqa: F811  二·11 1
    def setup(st: Stage) -> None:
        _busy_day(st)
        st.lanes["agents"][1].update(projectId="p_inbox", conversation="散活对话")
    with open_stage(browser, base, setup=setup) as st:
        pet = '.st-pet.is-loose[data-run-id="r2"]'
        assert st.page.locator(pet + " .st-tag").inner_text() == "散活对话"
        st.page.mouse.move(*st.center(pet + " .st-media"))
        st.page.clock.run_for(350)
        text = st.page.locator(".st-detail").inner_text()
        assert "还没归到项目" in text and "收件箱" not in text


def test_source_health_is_only_inside_the_clock_menu_with_a_dot_on_the_clock(browser, base) -> None:  # noqa: F811  信息源体检
    def broken(st: Stage) -> None:
        st.devices = [{"deviceId": "d1", "sourceHealth": {"issues": ["web_extension_missing", "window_wayland"], "at": "2026-11-08T10:00:00+00:00"}}]
    for setup, shown in ((lambda st: None, False), (lambda st: setattr(st, "devices", [{"deviceId": "d1", "sourceHealth": {"issues": [], "at": "2026-11-08T10:00:00+00:00"}}]), False), (broken, True)):
        with open_stage(browser, base, setup=setup) as st:
            assert st.page.locator("#stSrcHint, #stSrcPop").count() == 0            # 页面上不再常驻提示
            assert ("has-issue" in st.page.locator("#stClock").get_attribute("class")) is shown
            assert "窗口信息不全" not in st.page.locator("#stageLayer").inner_text()
            st.page.click("#stClock")
            menu = st.page.locator(".cm-panel")
            assert menu.is_visible()
            assert ("窗口信息不全" in menu.inner_text()) is shown
            if shown:
                assert "awatcher" in menu.inner_text()
                assert menu.locator("a[href*='addons.mozilla.org']").count() == 1
            assert st.posts == []


# ── 右上角时钟（hive.stage.v1「九」）──


def _focus(st: Stage, project: str | None, app: str = "firefox", title: str = "某文档", state: str = "present") -> None:
    st.current = {"running": False, "focus": {"state": state, "app": app, "title": title, "since": _iso(st.now()), "projectId": project,
                                              "projectName": None, "taskId": None, "taskName": None, "source": None,
                                              "displayName": title, "blockKey": None if state == "afk" else app + " · " + title}}


def test_clock_short_press_opens_the_human_page_below_the_clock_and_closes_on_outside_click(browser, base) -> None:  # noqa: F811  十四·1（取代「九」2 的菜单内容；三张卡的细节见 test_hive_human_page.py）
    with open_stage(browser, base, setup=_busy_day) as st:
        st.page.click("#stClock")
        menu = st.page.locator(".cm-panel")
        assert menu.is_visible()
        assert st.page.locator(".cm-panel .hp-card h3").all_inner_texts() == ["用时", "逐任务用时", "窗口记录"]
        top = st.box(".cm-panel")
        clock = st.box("#stClock")
        assert top["y"] >= clock["y"] + clock["height"]                                          # 在时钟下方
        assert "数学" in menu.inner_text() and ("1 小时" in menu.inner_text() or "59 分钟" in menu.inner_text())   # 逐任务：p_math 一小时（夹具时刻早于页面几秒，开头会被范围起点裁掉一点）
        st.page.mouse.click(300, 300)                                                           # 点外面收起
        assert st.page.locator(".cm-panel").count() == 0
        assert st.posts == []


def test_clock_long_press_countdown_presets_custom_and_end_reminder(browser, base) -> None:  # noqa: F811
    with open_stage(browser, base, setup=_busy_day) as st:
        st.long_press("#stClock")
        st.page.click(".cm-bubble >> text=倒计时")
        assert st.page.locator(".cm-presets .cm-bubble").all_inner_texts() == ["25 分", "45 分", "60 分"]
        inp = st.page.locator(".cm-custom input")
        inp.fill("0")
        st.page.click(".cm-custom button")
        assert "is-bad" in inp.get_attribute("class") and st.posts == []
        inp.fill("7")
        st.page.click(".cm-custom button")
        st.settle()
        assert st.posts == [("timer/start-unplaced", {"countdownSeconds": 420})]
        # 服务端回读：在跑、带 deadlineAt → 时钟显示剩余
        now = st.now().astimezone(TZ)
        st.posts.clear()
        st.lanes["human"]["running"] = {"startAt": _iso(now), "taskId": None, "projectId": None, "deadlineAt": _iso(now + timedelta(minutes=7))}
        st.poll()
        today = st.page.locator("#stClock .st-clock-today")
        assert today.inner_text().startswith("06:") and "is-countdown" in today.get_attribute("class")
        # 到点：页面提醒，并重读（服务端已记成白条）
        st.page.clock.run_for(7 * 60 * 1000 - 3000)
        st.lanes["human"]["running"] = None
        st.unplaced = [{"id": "u1", "startAt": _iso(now), "endAt": _iso(now + timedelta(minutes=7)), "durationSeconds": 420}]
        st.page.clock.run_for(4000)
        assert st.page.locator("#cmAlert").inner_text() == "倒计时结束 · 7 分钟已记下"
        st.poll()
        assert "is-timing" not in st.page.locator("#stClock").get_attribute("class")
        assert st.page.locator(".st-strip").count() == 1


def _clock_top_right(st: Stage) -> bool:
    c = st.box("#stClock")
    return c["x"] > 1280 / 2 and c["y"] < 200


def test_eye_sits_by_the_tray_on_unclassified_window_and_the_clock_never_moves(browser, base) -> None:  # noqa: F811  十·1 / 十·2
    with open_stage(browser, base, setup=_busy_day_unwatched) as st:
        chips = st.page.locator(".st-tray-chip").count()
        _focus(st, None, "firefox", "某文档")
        st.poll()
        st.page.clock.run_for(1000)
        eye, tray = st.box("#stEye"), st.box("#stBucketBtn")
        assert st.page.locator("#stEye").get_attribute("data-host") == "tray"
        assert eye["x"] >= tray["x"] + tray["width"] - 1 and eye["y"] > 600                      # 在托盘右边、左下
        assert st.page.locator("#stEye .st-eye-win").inner_text() == "firefox · 某文档"
        assert st.box("#stEye .st-eye-win")["x"] >= eye["x"] + eye["width"] - 1                  # 窗口名在眼睛右侧
        assert _clock_top_right(st) and st.page.locator(".st-tray-chip").count() == chips
        # 标题被隐去
        _focus(st, None, "firefox", "窗口名3")
        st.poll()
        assert st.page.locator("#stEye .st-eye-win").inner_text() == "firefox · 标题已隐去"
        # 切到已分类窗口：眼睛飞去那一格，托盘先掉一块（块还没读到）；窗口名收起
        _focus(st, "p_math")
        st.poll()
        st.page.clock.run_for(1000)
        assert st.page.locator("#stEye").get_attribute("data-host") == "p_math" and st.page.locator("#stEye .st-eye-win").inner_text() == ""
        assert _clock_top_right(st)
        assert st.page.locator(".st-tray-chip").count() == chips + 1
        assert st.page.locator(".st-tray-chip .st-drop").count() >= 1
        # 真块到了：认领占位那一格，不出现第二块
        st.blocks.append({"blockKey": "firefox · 某文档", "status": "pending", "seconds": 60, "seq": 1, "displayName": "firefox", "legible": True, "kind": "browser"})
        st.poll()
        st.page.wait_for_timeout(400)
        assert st.page.locator(".st-tray-chip").count() == chips + 1


def test_eye_drag_from_tray_to_a_cell_asks_before_filing_the_window(browser, base) -> None:  # noqa: F811  十·3
    with open_stage(browser, base, setup=_busy_day_unwatched) as st:   # 人看着 r1 时形象会站到小人旁边（「二十一」），松手是回那里不是回托盘
        st.blocks.append({"blockKey": "firefox · 某文档", "status": "pending", "seconds": 120, "seq": 5, "displayName": "某文档", "legible": True, "kind": "browser"})
        _focus(st, None, "firefox", "某文档")
        st.poll()
        st.page.clock.run_for(1000)
        chips = st.page.locator(".st-tray-chip").count()
        today, cell = st.lanes["today"], st.center(CELL.format("p_piano"))
        # 放空（不在格子上）：什么都不问、不发，飞回托盘
        st.drag("#stEye", (cell[0], 30))
        st.settle()
        st.page.clock.run_for(800)
        assert st.posts == [] and st.page.locator(".st-confirm").count() == 0
        assert st.box("#stEye")["y"] > 600
        # 拖到格子：弹确认卡，眼睛留在落点；取消 = 不发、飞回
        st.drag("#stEye", cell)
        st.settle()
        card = st.page.locator(".st-confirm")
        assert card.locator(".st-cf-title").inner_text() == "把 firefox · 某文档 算到 钢琴"
        assert card.locator(".st-cf-rem").inner_text() == "以后这个窗口都算 钢琴" and card.locator(".st-cf-box").is_checked() is False
        assert st.posts == [] and st.box("#stEye")["y"] < 600
        st.page.keyboard.press("Escape")
        st.page.clock.run_for(800)
        assert st.posts == [] and st.page.locator(".st-confirm").count() == 0 and st.box("#stEye")["y"] > 600
        # 确认（勾上「以后都算」）：按这个窗口今天的块发 assign；服务端随后认出项目，眼睛停在那一格，托盘不掉占位
        st.replies["activity/blocks/assign"] = (200, {"seq": 6})
        st.drag("#stEye", cell)
        st.settle()
        st.page.locator(".st-confirm .st-cf-box").check()
        st.blocks[-1]["status"] = "assigned"          # 服务端：这个块归了类，下一次读就不在未分类里了
        _focus(st, "p_piano", "firefox", "某文档")
        st.page.locator(".st-confirm .st-cf-ok").click()
        st.settle()
        st.page.clock.run_for(1000)
        assert st.posts == [("activity/blocks/assign", {"date": today, "blockKey": "firefox · 某文档", "ifSeq": 5, "remember": True, "projectId": "p_piano"})]
        assert st.page.locator("#stEye").get_attribute("data-host") == "p_piano" and st.page.locator(".st-confirm").count() == 0
        assert st.page.locator(".st-tray-chip").count() == chips - 1


def test_eye_stays_on_the_clock_while_timer_runs_or_afk(browser, base) -> None:  # noqa: F811  十·2
    with open_stage(browser, base, setup=_busy_day) as st:
        _focus(st, None, state="afk")
        st.poll()
        assert _eye_on_clock_corner(st) and "is-closed" in st.page.locator("#stEye").get_attribute("class")
        now = st.now().astimezone(TZ)
        st.lanes["human"]["running"] = {"startAt": _iso(now), "taskId": None, "projectId": None}
        _focus(st, None)
        st.poll()
        cls = st.page.locator("#stEye").get_attribute("class")
        assert _eye_on_clock_corner(st) and "is-closed" not in cls and "is-timing" in cls       # 计时中没记到项目：停时钟、睁眼
        assert _clock_top_right(st)


def test_eye_stays_put_while_the_hive_page_itself_is_in_front(browser, base) -> None:  # noqa: F811  三十一
    with open_stage(browser, base, setup=_busy_day_unwatched) as st:
        _focus(st, "p_math", app="code", title="plot.gd")
        st.poll()
        st.page.clock.run_for(1500)
        assert st.page.locator("#stEye").get_attribute("data-host") == "p_math"
        st.page.evaluate("() => { window.__pageInFront = true }")            # 人切到蜂巢页
        _focus(st, None, app="google-chrome", title="")                     # 后端报的焦点 = 这个浏览器
        st.poll()
        st.page.clock.run_for(1500)
        assert st.page.locator("#stEye").get_attribute("data-host") == "p_math", "页面在最前面：眼睛不动"
        _focus(st, None, state="afk")
        st.poll()
        st.page.clock.run_for(1500)
        assert st.page.locator("#stEye").get_attribute("data-host") != "p_math", "离开照常显示离开"
        st.page.evaluate("() => { window.__pageInFront = false }")
        _focus(st, None, app="google-chrome", title="新闻")
        st.poll()
        st.page.clock.run_for(1500)
        assert st.page.locator("#stEye").get_attribute("data-host") == "tray", "页面不在最前面：照常跟"

