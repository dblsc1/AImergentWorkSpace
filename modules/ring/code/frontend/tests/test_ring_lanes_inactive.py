"""泳道下方「未显示 n 个」（nexus-core v2.24，共享 lanes.js）：出错 / 空闲满 1 小时的泳道服务端已挪出 agents，这里收着列出。

同 test_ring_lanes_prefs：真浏览器，接口在浏览器侧桩掉。
"""

from __future__ import annotations

from typing import Any

from test_ring_lanes_prefs import open_prefs, served_body, waits


def inactive_body() -> dict[str, Any]:
    body = served_body()
    body["inactiveAgents"] = [
        {"agent": "claude-code", "label": "<img src=x onerror=window.__pwned=1>", "unverified": False, "reason": "error",
         "lastWorkAt": body["now"], "runs": 1, "elapsedSeconds": 600},
        {"agent": "codex", "label": "old-job", "unverified": False, "reason": "idle",
         "lastWorkAt": "2000-01-01T00:00:00+00:00", "runs": 2, "elapsedSeconds": 5400},
        {"agent": "codex", "label": "anon", "unverified": True, "reason": "error",
         "lastWorkAt": body["now"], "runs": 1, "elapsedSeconds": 30},
    ]
    return body


def test_collapsed_with_error_count_and_each_lane_listed_as_text(browser, static_base_url) -> None:
    with open_prefs(browser, static_base_url, body=inactive_body()) as (page, server):
        assert page.text_content("details.hcl-inactive > summary") == "未显示 3 个（2 个出错）"
        assert page.get_attribute("details.hcl-inactive", "open") is None
        page.click("details.hcl-inactive > summary")
        names = page.eval_on_selector_all(".hcl-inactive .hcl-hidden-name", "ns => ns.map(n => n.textContent)")
        assert names == ["<img src=x onerror=window.__pwned=1>", "old-job", "anon"]
        reasons = page.eval_on_selector_all(".hcl-inactive-reason", "ns => ns.map(n => n.textContent)")
        assert reasons[0] == "出错" and reasons[1].startswith("空闲 ") and reasons[1].endswith(" 小时")
        times = page.eval_on_selector_all(".hcl-inactive-time", "ns => ns.map(n => n.textContent)")
        assert times[1] == "本窗口 1 小时 30 分"
        assert page.query_selector(".hcl-inactive img") is None and page.evaluate("() => window.__pwned") is None
        assert len(page.query_selector_all(".hcl-inactive-pin")) == 2  # 匿名的不能置顶
        page.click(".hcl-inactive-pin >> nth=1")
        waits(page, lambda: server.calls)
        assert server.calls[0] == ("PUT", "/agent", {"agent": "codex", "label": "old-job", "pinned": True})


def test_absent_when_server_does_not_send_the_field(browser, static_base_url) -> None:
    with open_prefs(browser, static_base_url) as (page, _server):
        assert page.query_selector("details.hcl-inactive") is None


def test_no_error_count_when_none_errored(browser, static_base_url) -> None:
    body = inactive_body()
    body["inactiveAgents"] = body["inactiveAgents"][1:2]
    with open_prefs(browser, static_base_url, body=body) as (page, _server):
        assert page.text_content("details.hcl-inactive > summary") == "未显示 1 个"


def test_title_still_counts_todays_collapsed_errors(browser, static_base_url) -> None:
    """出错的泳道不画成泳道，但面板标题仍亮「N 个出错」（今天里出的；昨天的 / 空闲的不算）。"""
    body = inactive_body()  # 两条出错（lastWorkAt = now）+ 一条很久以前的空闲
    body["inactiveAgents"].append({"agent": "codex", "label": "yesterday", "unverified": False, "reason": "error",
                                   "lastWorkAt": "2000-01-01T00:00:00+00:00", "runs": 1, "elapsedSeconds": 5})
    with open_prefs(browser, static_base_url, body=body) as (page, _server):
        assert "2 个出错" in page.text_content("#lanes-state")


def test_title_does_not_count_recent_idle_entries_as_errors(browser, static_base_url) -> None:
    """reason=idle 即使 lastWorkAt 是刚才也不算出错（只数 reason=error）。"""
    body = inactive_body()
    body["inactiveAgents"].append({"agent": "codex", "label": "idle-recent", "unverified": False, "reason": "idle",
                                   "lastWorkAt": body["now"], "runs": 1, "elapsedSeconds": 5})
    with open_prefs(browser, static_base_url, body=body) as (page, _server):
        assert "2 个出错" in page.text_content("#lanes-state")
