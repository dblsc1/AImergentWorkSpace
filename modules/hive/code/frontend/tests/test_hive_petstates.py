"""agent 小人的状态 → 动作、悬停出卡 / 点击看详情、详情里的大立绘（hive.stage.v1「三十二」）。夹具沿用 test_hive_stage.py。"""

from __future__ import annotations

from datetime import timedelta
import os
from typing import Any
from urllib.parse import unquote

from test_hive_stage import CELL, PET, TZ, Stage, _iso, _run, base, browser, open_stage  # noqa: F401

SHOT = os.environ.get("PETS2_SHOTS")
WAVE = "点击回应-元气挥手.webm"


def _pack(extra: dict[str, list[str]] | None = None) -> dict[str, Any]:
    # 没有 click / happy 以外的临时动作：每个 mood 就一段，src 不会被 3 秒临时动作换掉
    moods = {"working": ["w.webm"], "waiting": ["wait.webm"], "idle": ["i.webm"], "worried": ["worry.webm"], "happy": [WAVE],
             "talk": ["talk.webm"], "bored": ["bored.webm"], "slack": ["slack.webm"], "sleep": ["sleep.webm"], "drag": ["d.webm"], "walk": ["k.webm"]}
    moods.update(extra or {})
    return {"kind": "claude", "name": "Claude", "license": "x", "credits": "x", "source": "x", "moods": moods}


PACKS = {"claude": _pack()}
MOOD_OF_CLIP = {"w": "working", "wait": "waiting", "i": "idle", "worry": "worried", "talk": "talk", "bored": "bored", "slack": "slack", "sleep": "sleep"}


def _agent(st: Stage, rid: str = "r1", *, phase: str, worked_min: float | None, ago_min: float = 0, project: str | None = "p_math", **over: Any) -> dict[str, Any]:
    """phase 是此刻的相位；worked_min = 最后一次干活结束在几分钟前（None = 没干过）。"""
    now = st.now().astimezone(TZ)
    phases = []
    if worked_min is not None:
        phases = [{"at": _iso(now - timedelta(minutes=worked_min + 10)), "phase": "working", "detail": None}]
        if phase != "working":
            phases.append({"at": _iso(now - timedelta(minutes=worked_min)), "phase": phase, "detail": None})
    return _run(rid, "claude-code", label="g" + rid, projectId=project, startAt=_iso(now - timedelta(minutes=(worked_min or 0) + 600 + ago_min)),
                lastSeenAt=_iso(now), phases=phases, **over)


def _lanes(st: Stage, *agents: dict[str, Any], interactions: list[dict[str, Any]] | None = None) -> None:
    st.set_lanes(agents=list(agents), interactions=interactions)


def _clip(st: Stage, rid: str = "r1") -> str:
    """这只小人此刻的主动作（节奏里的 main；3 秒临时动作不算，它每 15 秒插一下）。"""
    return (st.page.evaluate("id => document.querySelector(`.st-pet[data-run-id=\"${id}\"]`).__rhythm.main", rid) or "").removesuffix(".webm")


def _src(st: Stage, rid: str = "r1") -> str:
    return unquote((st.page.locator(f'.st-pet[data-run-id="{rid}"] video.st-art').get_attribute("src") or "").rsplit("/", 1)[-1]).removesuffix(".webm")


def _mood(st: Stage, rid: str = "r1") -> str:
    return st.page.locator(f'.st-pet[data-run-id="{rid}"]').get_attribute("data-mood") or ""


def _show(st: Stage, rid: str = "r1") -> None:
    st.poll()
    st.page.clock.run_for(1200)


def test_each_state_plays_its_own_clip(browser, base) -> None:  # noqa: F811  三十二·1
    cases = [("working", None, "w"), ("waiting_input", 1, "wait"), ("error", 3, "worry"), ("waiting_permission", 3, "worry"), ("idle", 0.5, "i"),
             ("idle", 2, "bored"), ("idle", 10, "slack"), ("idle", 40, "sleep")]
    with open_stage(browser, base, pets=PACKS, hold_webm=True) as st:
        for phase, worked, want in cases:
            _lanes(st, _agent(st, phase=phase, worked_min=1 if worked is None else worked))
            _show(st)
            assert (_clip(st), _mood(st)) == (want, MOOD_OF_CLIP[want]), (phase, worked, _clip(st), _mood(st))
            assert "is-work" in st.page.locator(".st-pet").get_attribute("class")        # 满 1 小时之前都站在格子里


def test_idle_tiers_follow_the_fake_clock_from_the_last_work_ending(browser, base) -> None:  # noqa: F811  三十二·1
    with open_stage(browser, base, pets=PACKS, hold_webm=True) as st:
        _lanes(st, _agent(st, phase="idle", worked_min=0.2))
        _show(st)
        seen = [_clip(st)]
        for _ in range(14):                               # 约 70 秒：离最后一次干活结束满 1 分钟
            st.poll()
            seen.append(_clip(st))
        assert seen[0] == "i" and seen[-1] == "bored", seen
        for _ in range(60):                               # 再约 5 分钟：满 5 分钟
            st.poll()
        assert _clip(st) == "slack"
        for _ in range(300):                              # 再约 25 分钟：满 30 分钟
            st.poll()
        assert _clip(st) == "sleep" and "is-work" in st.page.locator(".st-pet").get_attribute("class")


def test_never_worked_counts_from_the_run_start(browser, base) -> None:  # noqa: F811  三十二·1
    with open_stage(browser, base, pets=PACKS, hold_webm=True) as st:
        now = st.now().astimezone(TZ)
        _lanes(st, _run("r1", "claude-code", label="g", projectId="p_math", startAt=_iso(now - timedelta(minutes=7)), lastSeenAt=_iso(now)))
        _show(st)
        assert _mood(st) == "slack"


def test_a_pet_with_a_person_talking_to_it_plays_the_talk_clip_even_when_working(browser, base) -> None:  # noqa: F811  三十二·1
    with open_stage(browser, base, pets=PACKS, hold_webm=True) as st:
        now = st.now().astimezone(TZ)
        _lanes(st, _agent(st, phase="working", worked_min=1),
               interactions=[{"runId": "r1", "kind": "attend", "at": _iso(now), "until": _iso(now + timedelta(seconds=30))}])
        _show(st)
        assert (_mood(st), _clip(st)) == ("talk", "talk")
        _lanes(st, _agent(st, phase="error", worked_min=1),
               interactions=[{"runId": "r1", "kind": "attend", "at": _iso(now), "until": _iso(now + timedelta(seconds=60))}])
        _show(st)
        assert _mood(st) == "worried"                     # 出了问题就先担心，不论人在不在
        _lanes(st, _agent(st, phase="idle", worked_min=1))
        for _ in range(4):
            st.poll()
        assert _mood(st) != "talk"                        # 人不看了：回到状态动作


def test_packs_without_the_new_keys_fall_back(browser, base) -> None:  # noqa: F811  三十二·1
    old = {"claude": _pack({"talk": [], "bored": [], "slack": [], "sleep": [], "idle": ["i.webm", "原地小憩沉眠.webm"], "waiting": ["wait.webm"]})}
    for key in ("talk", "bored", "slack", "sleep"):
        old["claude"]["moods"].pop(key, None)
    with open_stage(browser, base, pets=old, hold_webm=True) as st:
        for worked, want in ((2, "wait"), (40, "原地小憩沉眠")):
            _lanes(st, _agent(st, phase="idle", worked_min=worked))
            _show(st)
            assert _clip(st) == want, (worked, _clip(st))
        _lanes(st, _agent(st, phase="working", worked_min=1), interactions=[{"runId": "r1", "kind": "attend", "at": _iso(st.now()), "until": _iso(st.now() + timedelta(seconds=30))}])
        _show(st)
        assert _clip(st) == WAVE.removesuffix(".webm")     # talk → happy


def test_after_an_hour_the_sleeper_walks_back_to_the_top_left_and_sleeps_there(browser, base) -> None:  # noqa: F811  三十二·1
    with open_stage(browser, base, pets=PACKS, hold_webm=True) as st:
        _lanes(st, _agent(st, phase="idle", worked_min=50))
        _show(st)
        assert "is-work" in st.page.locator(".st-pet").get_attribute("class") and _clip(st) == "sleep"
        _lanes(st, _agent(st, phase="idle", worked_min=61))
        st.poll()
        cls = st.page.locator(".st-pet").get_attribute("class")
        assert "is-loose" in cls and "st-away" in cls, cls                # 从格子里出发，走回去
        st.page.clock.run_for(600)
        assert _src(st) == "k"                                             # 走的时候是走路动作
        st.page.clock.run_for(12000)
        home = st.page.evaluate("() => { const e = document.querySelector('.st-pet'); return {home: e.__home, x: parseFloat(e.style.left), y: parseFloat(e.style.top)} }")
        assert 24 <= home["home"]["x"] <= 24 + 340 and 142 <= home["home"]["y"] <= 142 + 160, home      # 同没有去向的那排位置
        assert abs(home["x"] - home["home"]["x"]) < 0.01 and abs(home["y"] - home["home"]["y"]) < 0.01, home
        assert "st-away" not in st.page.locator(".st-pet").get_attribute("class") and _clip(st) == "sleep"


def test_reduced_motion_sleeper_goes_straight_home(browser, base) -> None:  # noqa: F811  三十二·5
    with open_stage(browser, base, pets=PACKS, hold_webm=True) as st:
        st.page.emulate_media(reduced_motion="reduce")
        _lanes(st, _agent(st, phase="idle", worked_min=50))
        _show(st)
        _lanes(st, _agent(st, phase="idle", worked_min=61))
        st.poll()
        cls = st.page.locator(".st-pet").get_attribute("class")
        assert "is-loose" in cls and "st-away" not in cls, cls


def test_a_new_page_with_an_old_sleeper_puts_it_at_the_top_left_without_a_walk(browser, base) -> None:  # noqa: F811  三十二·1
    def setup(st: Stage) -> None:
        _lanes(st, _agent(st, phase="idle", worked_min=120))
    with open_stage(browser, base, setup=setup, pets=PACKS, hold_webm=True) as st:
        _show(st)
        cls = st.page.locator(".st-pet").get_attribute("class")
        assert "is-loose" in cls and "st-away" not in cls and _clip(st) == "sleep", cls


def test_running_agents_folded_by_the_lane_retention_still_get_a_pet(browser, base) -> None:  # noqa: F811  三十二·2
    def setup(st: Stage) -> None:
        now = st.now().astimezone(TZ)
        _lanes(st, _agent(st, "r1", phase="working", worked_min=1))      # r2 / r3 / r4 被折叠：只在 views/current 与 inactiveAgents 里
        st.lanes["inactiveAgents"] = [
            {"agent": "claude-code", "label": "g-sleepy", "reason": "idle", "tier": "low", "lastWorkAt": _iso(now - timedelta(minutes=90)), "runs": 1, "elapsedSeconds": 10},
            {"agent": "claude-code", "label": "g-nap", "reason": "idle", "tier": "low", "lastWorkAt": _iso(now - timedelta(minutes=12)), "runs": 1, "elapsedSeconds": 10}]
        st.current = {"running": False, "focus": None, "hiddenCount": 0, "agents": [
            {"runId": "r1", "taskId": None, "agent": "claude-code", "tool": "claude-code", "model": None, "startedAt": _iso(now - timedelta(hours=3)), "phase": "working", "label": "g1", "elapsedSeconds": 1},
            {"runId": "r2", "taskId": None, "agent": "claude-code", "tool": "claude-code", "model": None, "startedAt": _iso(now - timedelta(hours=5)), "phase": "idle", "label": "g-sleepy", "elapsedSeconds": 1},
            {"runId": "r3", "taskId": "t_p_math", "agent": "claude-code", "tool": "claude-code", "model": None, "startedAt": _iso(now - timedelta(hours=5)), "phase": "idle", "label": "g-nap", "elapsedSeconds": 1},
            {"runId": "r4", "taskId": "t_p_math", "agent": "claude-code", "tool": "claude-code", "model": None, "startedAt": _iso(now - timedelta(hours=1)), "phase": "error", "label": "g-bad", "elapsedSeconds": 1}]}
    with open_stage(browser, base, setup=setup, pets=PACKS, hold_webm=True) as st:
        _show(st)
        assert st.page.locator(".st-pet").count() == 4
        assert (_mood(st, "r2"), "is-loose" in st.page.locator('.st-pet[data-run-id="r2"]').get_attribute("class")) == ("sleep", True)   # 90 分钟前就不干了：在左上角睡
        assert (_mood(st, "r3"), "is-work" in st.page.locator('.st-pet[data-run-id="r3"]').get_attribute("class")) == ("slack", True)     # 12 分钟：在它的项目格里开小差
        assert _mood(st, "r4") == "worried"
        # 失联 / 已结束的照旧不出
        now = st.now().astimezone(TZ)
        st.lanes["agents"].append(_agent(st, "r5", phase="working", worked_min=1, endAt=_iso(now)))
        st.poll()
        assert st.page.locator('.st-pet[data-run-id="r5"]').count() == 0


def _hover_pet(st: Stage, rid: str = "r1") -> tuple[float, float]:
    x, y = st.center(f'.st-pet[data-run-id="{rid}"] .st-media')
    st.page.mouse.move(3, 3)
    st.page.mouse.move(x, y)            # 一步到位：不经过格子，小人还在格子里（从格子上走过去会先把小人引出来，见「二十二」2）
    return x, y


def test_hover_a_pet_resting_in_its_cell_shows_the_card_after_300ms_and_leaving_hides_it(browser, base) -> None:  # noqa: F811  三十二·3
    with open_stage(browser, base, pets=PACKS, hold_webm=True) as st:
        _lanes(st, _agent(st, phase="working", worked_min=1))
        _show(st)
        assert "is-in" in st.page.locator(PET).get_attribute("class")
        x, y = _hover_pet(st)
        assert st.page.evaluate("([x, y]) => document.elementFromPoint(x, y).closest('.st-pet') !== null", [x, y]), "格子里的小人直接接得到指针"
        st.page.clock.run_for(200)
        assert st.page.locator(".st-detail").count() == 0
        st.page.clock.run_for(150)
        assert st.page.locator(".st-detail").count() == 1 and "claude-code" in st.page.locator(".st-detail").inner_text()
        st.page.mouse.move(3, 3, steps=4)
        st.page.clock.run_for(600)
        assert st.page.locator(".st-detail").count() == 0
        assert st.page.locator(".st-pet-in").count() == 1


def test_pointer_on_the_card_keeps_it_and_so_does_renaming(browser, base) -> None:  # noqa: F811  三十二·3
    with open_stage(browser, base, pets=PACKS, hold_webm=True) as st:
        _lanes(st, _agent(st, phase="working", worked_min=1))
        _show(st)
        _hover_pet(st)
        st.page.clock.run_for(350)
        cx, cy = st.center(".st-detail .st-d-title")
        st.page.mouse.move(cx, cy, steps=6)
        st.page.clock.run_for(800)
        assert st.page.locator(".st-detail").count() == 1
        st.page.mouse.click(cx, cy)                          # 点标题 = 改名
        assert st.page.locator(".st-d-name").count() == 1
        st.page.mouse.move(3, 3, steps=4)
        st.page.clock.run_for(800)
        assert st.page.locator(".st-detail").count() == 1, "改名中不收"


def test_cells_stay_clickable_around_a_resting_pet(browser, base) -> None:  # noqa: F811  三十二·3
    with open_stage(browser, base, pets=PACKS, hold_webm=True) as st:
        _lanes(st, _agent(st, phase="working", worked_min=1))
        _show(st)
        hit = st.page.evaluate("""() => { const c = document.querySelector('.hex-cell[data-project-id=p_math]').getBoundingClientRect(), m = document.querySelector('.st-pet .st-media').getBoundingClientRect();
            const at = (x, y) => { const e = document.elementFromPoint(x, y); return e && (e.closest('.st-pet') ? 'pet' : e.closest('.hex-cell[data-project-id=p_math]') ? 'cell' : 'other'); };
            return { center: at(c.x + c.width / 2, c.y + c.height / 2), left: at(m.x - 2, m.y + m.height / 2), tag: getComputedStyle(document.querySelector('.st-pet .st-tag')).pointerEvents, body: at(m.x + m.width / 2, m.y + m.height / 2) } }""")
        assert hit == {"center": "cell", "left": "cell", "tag": "none", "body": "pet"}, hit


def _detail_setup(st: Stage) -> None:
    now = st.now().astimezone(TZ)
    st.time_lanes = {"today": now.date().isoformat(), "now": _iso(now), "windowStart": _iso(now - timedelta(hours=3)), "windowEnd": _iso(now), "human": [], "agents": [
        {"agent": "claude-code", "tool": "claude-code", "name": "gr1", "runId": "r1", "laneKey": "cc\ngr1", "projectId": "p_math", "running": True, "lost": False,
         "phase": "working", "segments": [], "attend": [], "retained": False}]}                    # 不留存的卡收在「其余 N 个」里：照样点开
    _lanes(st, _agent(st, phase="working", worked_min=1))


def test_click_a_pet_opens_the_agent_page_detail_and_the_card_goes_away(browser, base) -> None:  # noqa: F811  三十二·3
    with open_stage(browser, base, setup=_detail_setup, pets=PACKS, hold_webm=True) as st:
        _show(st)
        x, y = _hover_pet(st)
        st.page.clock.run_for(350)
        assert st.page.locator(".st-detail").count() == 1
        st.page.mouse.click(x, y)
        st.page.clock.run_for(400)
        detail = st.page.locator("#agPage .ag-detail")
        detail.locator(".ag-dt-title").wait_for()
        assert detail.locator(".ag-dt-title").inner_text() == "gr1" and st.page.locator(".st-detail").count() == 0
        assert st.page.locator(".fp-panel").count() == 0                       # 长按 / 点击都不再直接挑形象


def test_a_long_press_is_just_a_click_now(browser, base) -> None:  # noqa: F811  三十二·3
    with open_stage(browser, base, setup=_detail_setup, pets=PACKS, hold_webm=True) as st:
        _show(st)
        x, y = st.center(PET + " .st-media")
        st.page.mouse.move(x, y)
        st.page.mouse.down()
        st.page.clock.run_for(800)
        assert st.page.locator("#agPage .ag-detail:not([hidden])").count() == 0, "按着不放时什么都没发生"
        st.page.mouse.up()
        st.page.clock.run_for(400)
        st.page.locator("#agPage .ag-detail .ag-dt-title").wait_for()


def _big_src(st: Stage) -> str:
    return unquote((st.page.locator("#agPage .ag-big-slot video.st-art").get_attribute("src") or "").rsplit("/", 1)[-1]).removesuffix(".webm")


def _big_main(st: Stage) -> str:
    """大立绘此刻的主动作（不含 3 秒临时动作）。"""
    return (st.page.evaluate("() => document.querySelector('#agPage .ag-big-slot').__rhythm.main") or "").removesuffix(".webm")


def _open_from_pet(st: Stage, rid: str = "r1") -> None:
    st.page.mouse.click(*st.center(f'.st-pet[data-run-id="{rid}"] .st-media'))
    st.page.clock.run_for(400)
    st.page.locator("#agPage .ag-detail .ag-dt-title").wait_for()
    st.page.wait_for_selector("#agPage .ag-big-slot video.st-art")


def test_big_figure_greets_once_then_follows_the_state_and_stays_inside_the_card(browser, base) -> None:  # noqa: F811  三十二·4
    with open_stage(browser, base, setup=_detail_setup, pets=PACKS, hold_webm=True, height=620) as st:
        _show(st)
        _open_from_pet(st)
        assert _big_src(st) == WAVE.removesuffix(".webm"), "点开那一下先问候"
        st.page.clock.run_for(4500)
        assert _big_main(st) == "w", "之后回到状态动作"
        for _ in range(4):
            st.page.clock.run_for(4000)
            assert _big_main(st) == "w"
            assert "greet" not in (st.page.locator("#agPage .ag-big-slot").get_attribute("data-key") or ""), "不再问候"
        card, big = st.box("#agPage .ag-detail"), st.box("#agPage .ag-big")
        assert big["y"] >= card["y"] - 0.5 and big["y"] + big["height"] <= card["y"] + card["height"] + 0.5, (card, big)
        assert big["x"] + big["width"] <= card["x"] + card["width"] + 0.5
        if SHOT:
            st.page.screenshot(path=os.path.join(SHOT, "pets2-detail.png"))
        # 状态变了，大立绘跟着变
        _lanes(st, _agent(st, phase="idle", worked_min=40))
        st.poll()
        st.page.clock.run_for(16000)                       # Agent 页每 15 秒自己重读重画
        assert _big_main(st) == "sleep" and _big_src(st) == "sleep"
        # 关掉再点开：再问候一次
        st.page.keyboard.press("Escape")
        st.page.wait_for_timeout(450)
        st.page.clock.run_for(600)
        st.page.locator("#agPage .ag-more").click() if st.page.locator("#agPage .ag-card").count() == 0 else None
        st.page.locator("#agPage .ag-card").first.click()
        st.page.clock.run_for(400)
        st.page.wait_for_selector("#agPage .ag-detail:not([hidden]) .ag-big-slot video.st-art")
        assert _big_src(st) == WAVE.removesuffix(".webm")


def test_big_figure_fits_short_and_tall_cards(browser, base) -> None:  # noqa: F811  三十二·4
    for height in (520, 1000):
        with open_stage(browser, base, setup=_detail_setup, pets=PACKS, hold_webm=True, height=height) as st:
            _show(st)
            _open_from_pet(st)
            card, big = st.box("#agPage .ag-detail"), st.box("#agPage .ag-big")
            assert big["y"] + big["height"] <= card["y"] + card["height"] + 0.5 and big["width"] <= 300.5, (height, card, big)


def test_shots_of_states_in_the_cells(browser, base) -> None:  # noqa: F811  截图用
    if not SHOT:
        return
    def setup(st: Stage) -> None:
        now = st.now().astimezone(TZ)
        _lanes(st, _agent(st, "r1", phase="working", worked_min=1), _agent(st, "r2", phase="error", worked_min=3, project="p_math"),
               _agent(st, "r3", phase="idle", worked_min=10, project="p_piano"), _agent(st, "r4", phase="idle", worked_min=100, project=None),
               interactions=[{"runId": "r2", "kind": "attend", "at": _iso(now), "until": _iso(now + timedelta(seconds=30))}])
    with open_stage(browser, base, setup=setup, pets=PACKS) as st:
        _show(st)
        st.page.screenshot(path=os.path.join(SHOT, "pets2-cells.png"))
