"""泳道下方的折叠菜单「已折叠 n 个」（nexus-core v2.25，共享 lanes.js）：原来的「未显示」「已隐藏」两个展开条合并成一个。

同 test_ring_lanes_prefs：真浏览器，接口在浏览器侧桩掉。
"""

from __future__ import annotations

from typing import Any

from test_ring_lanes_prefs import open_prefs, served_body, waits

OLD = "2000-01-01T00:00:00+00:00"
XSS = "<img src=x onerror=window.__pwned=1>"


def row(body, label, reason, *, tier="normal", last=None, elapsed=600, unverified=False, agent="claude-code"):
    return {"agent": agent, "label": label, "unverified": unverified, "reason": reason, "tier": tier,
            "lastWorkAt": last or body["now"], "runs": 1, "elapsedSeconds": elapsed}


def inactive_body() -> dict[str, Any]:
    body = served_body()
    body["inactiveAgents"] = [
        row(body, XSS, "error", tier="high"),
        row(body, "anon", "error", unverified=True, elapsed=30, agent="codex"),
        row(body, "old-job", "idle", last=OLD, elapsed=5400, agent="codex"),
        row(body, "done-job", "ended", tier="low", elapsed=120, agent="codex"),
    ]
    body["hiddenAgents"] = [{"agent": "claude-code", "label": "plot", "live": True, "phase": "waiting_input"}]
    body["hiddenWaiting"] = 1
    return body


def test_one_collapsed_menu_with_error_count_and_ordered_groups(browser, static_base_url) -> None:
    with open_prefs(browser, static_base_url, body=inactive_body()) as (page, _server):
        assert len(page.query_selector_all("#lanes-view details")) == 1 + len(page.query_selector_all("details.hcl-fold"))
        assert page.query_selector("details.hcl-hidden") is None and page.query_selector("details.hcl-inactive") is None
        assert page.text_content("details.hcl-foldmenu > summary") == "已折叠 5 个（2 个出错） · 1 个在等你"
        assert page.get_attribute("details.hcl-foldmenu", "open") is None
        page.click("details.hcl-foldmenu > summary")
        caps = page.eval_on_selector_all(".hcl-foldmenu-cap", "ns => ns.map(n => n.textContent)")
        assert caps == ["出错 (2)", "空闲 (1)", "已结束 (1)", "手动隐藏 (1)"]
        names = page.eval_on_selector_all(".hcl-foldmenu .hcl-hidden-name", "ns => ns.map(n => n.textContent)")
        assert names == [XSS, "anon", "old-job", "done-job", "plot"]
        reasons = page.eval_on_selector_all(".hcl-inactive-reason", "ns => ns.map(n => n.textContent)")
        assert reasons[0] == "出错 · 刚刚" and reasons[2].startswith("空闲 · ") and reasons[2].endswith(" 小时前") and reasons[3].startswith("已结束 · ")
        assert page.eval_on_selector_all(".hcl-inactive-time", "ns => ns.map(n => n.textContent)")[2] == "本窗口 1 小时 30 分"
        tiers = page.eval_on_selector_all(".hcl-tier", "ns => ns.map(n => n.textContent)")
        assert tiers == ["重点", "临时"]  # 普通档不标
        assert page.query_selector(".hcl-foldmenu img") is None and page.evaluate("() => window.__pwned") is None


def test_pin_from_the_menu_and_unhide_inside_it(browser, static_base_url) -> None:
    with open_prefs(browser, static_base_url, body=inactive_body()) as (page, server):
        page.click("details.hcl-foldmenu > summary")
        assert len(page.query_selector_all(".hcl-inactive-pin")) == 3  # 匿名的不能置顶
        page.click(".hcl-inactive-pin >> nth=1")
        waits(page, lambda: server.calls)
        assert server.calls[0] == ("PUT", "/agent", {"agent": "codex", "label": "old-job", "pinned": True})
        page.click(".is-hidden .hcl-hidden-restore")
        waits(page, lambda: len(server.calls) == 2)
        assert server.calls[1] == ("PUT", "/agent", {"agent": "claude-code", "label": "plot", "hidden": False, "unverified": False})


def test_open_state_survives_a_poll(browser, static_base_url) -> None:
    with open_prefs(browser, static_base_url, body=inactive_body(), clock=True) as (page, server):
        page.click("details.hcl-foldmenu > summary")
        n = server.lanes_gets
        page.clock.run_for(15_500)
        waits(page, lambda: server.lanes_gets > n)
        page.wait_for_timeout(100)
        assert page.get_attribute("details.hcl-foldmenu", "open") is not None
        assert page.text_content("details.hcl-foldmenu > summary").startswith("已折叠 5 个")


def test_expired_footer_inside_the_menu_or_alone(browser, static_base_url) -> None:
    body = inactive_body()
    body.update(expiredAgents=3, expired={"agents": 3, "runs": 4, "elapsedSeconds": 700})
    with open_prefs(browser, static_base_url, body=body) as (page, _server):
        assert page.text_content("details.hcl-foldmenu .hcl-foldmenu-expired") == "另有 3 个临时会话已过期不再列出"
    alone = served_body()
    alone.update(expiredAgents=2, inactiveAgents=[])
    with open_prefs(browser, static_base_url, body=alone) as (page, _server):
        assert page.query_selector("details.hcl-foldmenu") is None
        assert page.text_content("#lanes-view > .hcl-foldmenu-expired") == "另有 2 个临时会话已过期不再列出"


def test_absent_when_server_does_not_send_the_fields(browser, static_base_url) -> None:
    with open_prefs(browser, static_base_url) as (page, _server):
        assert page.query_selector("details.hcl-foldmenu") is None and page.query_selector(".hcl-foldmenu-expired") is None


def test_old_v24_rows_without_tier_still_render(browser, static_base_url) -> None:
    body = served_body()
    body["inactiveAgents"] = [{k: v for k, v in row(body, "x", "idle", last=OLD).items() if k != "tier"}]
    with open_prefs(browser, static_base_url, body=body) as (page, _server):
        assert page.text_content("details.hcl-foldmenu > summary") == "已折叠 1 个"
        assert page.query_selector(".hcl-tier") is None


def test_no_error_count_when_none_errored(browser, static_base_url) -> None:
    body = inactive_body()
    body["inactiveAgents"] = body["inactiveAgents"][2:3]
    body["hiddenAgents"], body["hiddenWaiting"] = [], 0
    with open_prefs(browser, static_base_url, body=body) as (page, _server):
        assert page.text_content("details.hcl-foldmenu > summary") == "已折叠 1 个"


def test_title_still_counts_todays_collapsed_errors(browser, static_base_url) -> None:
    """出错的泳道不画成泳道，但面板标题仍亮「N 个出错」（今天里出的；昨天的 / 空闲的 / 已结束的不算）。"""
    body = inactive_body()  # 两条出错（lastWorkAt = now）+ 一条很久以前的空闲 + 一条刚结束
    body["inactiveAgents"].append(row(body, "yesterday", "error", last=OLD, agent="codex"))
    with open_prefs(browser, static_base_url, body=body) as (page, _server):
        assert "2 个出错" in page.text_content("#lanes-state")
