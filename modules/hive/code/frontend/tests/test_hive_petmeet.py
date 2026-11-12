"""人看着某个 agent 时，人的形象走到它的小人旁边互动（hive.stage.v1「二十一」）。"""
from __future__ import annotations

from datetime import timedelta

from test_hive_stage import PET, TZ, _iso, _one_worker, base, browser, open_stage  # noqa: F401

HAPPY = ["点击回应-元气挥手.webm", "点击回应-开心跃动.webm"]


def _pack(kind: str, moods: dict) -> dict:
    return {"kind": kind, "name": kind, "license": "x", "credits": "x", "source": "x", "moods": moods}


VIDEO_PACKS = {"claude": _pack("claude", {"working": ["w.webm"], "waiting": ["wait.webm"], "idle": ["i.webm"], "happy": HAPPY}),
               "kimi": _pack("kimi", {"working": ["kw.webm"], "waiting": ["kwait.webm"], "idle": ["ki.webm"], "happy": HAPPY})}
STILL_PACKS = {"claude": {**_pack("claude", {}), "image": "c.png"}, "kimi": {**_pack("kimi", {}), "image": "k.png"}}
EYE_ART = "#stEye video.st-art"


def _watching(st, seconds: int = 30) -> None:
    _one_worker(st)
    now = st.now().astimezone(TZ)
    st.lanes["interactions"] = [{"runId": "r1", "kind": "attend", "at": _iso(now), "until": _iso(now + timedelta(seconds=seconds))}]
    st.current = {"running": False, "focus": {"state": "present", "app": "code", "title": "t", "since": _iso(now), "projectId": "p_math",
                                              "projectName": None, "taskId": None, "taskName": None, "source": "rules"}}


def _meets(st) -> bool:
    return bool(st.page.locator(PET).evaluate("e => e.classList.contains('is-meeting')"))


def _settle_in(st) -> None:
    st.poll()
    st.page.clock.run_for(1200)      # 眼睛飞过去 ≤ 600ms，再等一轮 400ms 检查


def test_human_walks_beside_the_watched_pet_and_both_play_a_greeting_clip(browser, base) -> None:  # noqa: F811
    with open_stage(browser, base, setup=_watching, pets=VIDEO_PACKS, hold_webm=True) as st:
        _settle_in(st)
        assert _meets(st)
        px, py = st.center(PET + " .st-media")
        ex, ey = st.center("#stEye")
        assert 40 < abs(ex - px) < 120 and ey > py - 5, (px, py, ex, ey)           # 挨着站，比小人低一点（不盖名字牌）
        tag = st.box(PET + " .st-tag")
        eb = st.box("#stEye")
        assert eb["y"] >= tag["y"] + tag["height"] - 1 or eb["x"] >= tag["x"] + tag["width"] or eb["x"] + eb["width"] <= tag["x"]
        assert st.page.locator(PET + " video.st-art").get_attribute("src").endswith("/claude/%E7%82%B9%E5%87%BB%E5%9B%9E%E5%BA%94-%E5%BC%80%E5%BF%83%E8%B7%83%E5%8A%A8.webm")
        assert st.page.locator(EYE_ART).get_attribute("src").endswith("/kimi/%E7%82%B9%E5%87%BB%E5%9B%9E%E5%BA%94-%E5%85%83%E6%B0%94%E6%8C%A5%E6%89%8B.webm")
        assert st.page.locator("#stEye").get_attribute("data-host") == "p_math"
        flips = st.page.evaluate("() => [document.querySelector('#stEye').classList.contains('flip'), document.querySelector('.st-pet.is-work').classList.contains('flip')]")
        assert flips[0] != flips[1]                                                   # 两边面对面


def test_no_more_attend_the_human_goes_back_and_the_pet_returns_to_its_rhythm(browser, base) -> None:  # noqa: F811
    with open_stage(browser, base, setup=lambda st: _watching(st, 3), pets=VIDEO_PACKS, hold_webm=True) as st:
        _settle_in(st)
        assert _meets(st)
        for _ in range(4):
            st.poll()                    # 15 秒后 attend 过期
        st.page.clock.run_for(1200)
        assert not _meets(st)
        assert "flip" not in (st.page.locator(PET).get_attribute("class") or "")
        assert not st.page.locator("#stEye").evaluate("e => e.classList.contains('flip')")
        assert "claude/%E7%82%B9" not in (st.page.locator(PET + " video.st-art").get_attribute("src") or "")


def test_no_walk_over_while_timing_or_when_the_agent_page_is_open(browser, base) -> None:  # noqa: F811
    with open_stage(browser, base, setup=_watching, pets=VIDEO_PACKS, hold_webm=True) as st:
        _settle_in(st)
        assert _meets(st)
        st.page.evaluate("() => { const v = document.createElement('div'); v.className = 'ag-veil'; v.id = 'fake'; document.body.appendChild(v); }")
        st.page.clock.run_for(800)
        assert not _meets(st)
        st.page.evaluate("() => document.getElementById('fake').remove()")
        st.page.clock.run_for(1500)
        assert _meets(st)

    def timing(st) -> None:
        _watching(st)
        now = st.now().astimezone(TZ)
        st.lanes["human"]["running"] = {"startAt": _iso(now), "projectId": "p_math", "taskId": "t_p_math", "mode": "do", "source": "timer-backend"}
    with open_stage(browser, base, setup=timing, pets=VIDEO_PACKS, hold_webm=True) as st:
        _settle_in(st)
        assert not _meets(st)


def test_static_packs_use_a_light_css_hop_and_reduced_motion_stands_still(browser, base) -> None:  # noqa: F811
    with open_stage(browser, base, setup=_watching, pets=STILL_PACKS, hold_webm=True) as st:
        _settle_in(st)
        assert _meets(st)
        assert st.page.locator(PET).evaluate("e => e.classList.contains('meet-css')")
        assert st.page.locator("#stEye").evaluate("e => e.classList.contains('meet-css')")
        name = "e => getComputedStyle(e.querySelector('.st-media')).animationName"
        assert st.page.locator(PET).evaluate(name) == "st-meet-hop" and st.page.locator("#stEye").evaluate(name) == "st-meet-hop"
        st.page.emulate_media(reduced_motion="reduce")
        assert st.page.locator(PET).evaluate(name) == "none" and st.page.locator("#stEye").evaluate(name) == "none"
