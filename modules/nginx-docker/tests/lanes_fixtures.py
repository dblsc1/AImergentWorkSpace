"""`GET /api/core/views/lanes` 的桩数据（nexus-core 契约 v2.4「读端」节的形状）。

顶栏预览（本目录 test_navbar_lanes.py）与计时页全部泳道（modules/ring 的 test_ring_lanes.py）共用。
「现在」钉在 2026-09-30 10:20（+08:00），测试的浏览器时区也钉 Asia/Shanghai。

剧情（仓主要的一对多）：garden 在干活 → 等批准 → 人回话 → 接着干；plot 在等人说话 → 人去看（attend）
→ 回话 → plot 接着干 → 又在等；codex 报过一次错；docs 早上跑完了；old-job 超时还挂着；tests 空闲。
"""

from __future__ import annotations

import copy
from typing import Any

TZ = "+08:00"
TODAY = "2026-09-30"


def at(hm: str, day: str = TODAY) -> str:
    return f"{day}T{hm}:00{TZ}"


def run(run_id: str, label: str | None, start: str, *, agent: str = "claude-code", end: str | None = None,
        phases: list[tuple[str, str, str | None]] = (), overdue: bool = False) -> dict[str, Any]:
    return {
        "runId": run_id, "agent": agent, "tool": agent, "model": None, "label": label,
        "taskId": None, "projectId": None, "startAt": start, "endAt": end,
        "outcome": "completed" if end else None, "elapsedSeconds": 1200, "overdue": overdue,
        "phases": [{"at": a, "phase": p, "detail": d} for a, p, d in phases],
    }


LANES_FULL: dict[str, Any] = {
    "today": TODAY, "now": at("10:20"),
    "windowStart": at("00:00"), "windowEnd": "2026-10-01T00:00:00+08:00",
    "human": {
        "sessions": [
            {"startAt": at("08:00"), "endAt": at("08:50"), "durationSeconds": 3000, "taskId": "t_a",
             "projectId": "p_1", "mode": "do", "source": "timer-backend"},
            {"startAt": at("09:00"), "endAt": at("09:30"), "durationSeconds": 1800, "taskId": "t_a",
             "projectId": "p_1", "mode": "prompt", "source": "timer-backend"},
            {"startAt": at("09:35"), "endAt": at("09:50"), "durationSeconds": 900, "taskId": "t_a",
             "projectId": "p_1", "mode": "review", "source": "timer-backend"},
        ],
        "running": {"startAt": at("10:00"), "taskId": "t_a", "projectId": "p_1"},
        "presence": [
            {"deviceId": "dev_x", "from": at("09:30"), "to": at("10:05"), "app": "code",
             "title": "<b>plot.gd</b> — garden — VS Code", "afk": False},
            {"deviceId": "dev_x", "from": at("10:05"), "to": at("10:10"), "app": "", "title": "", "afk": True},
            {"deviceId": "dev_x", "from": at("10:10"), "to": at("10:20"), "app": "code",
             "title": "garden", "afk": False},
        ],
    },
    "agents": [
        run("run_e", "old-job", at("21:00", "2026-09-29"), overdue=True),
        run("run_d", "docs", at("07:00"), end=at("09:15"),
            phases=[(at("07:00"), "working", None), (at("08:00"), "idle", None), (at("08:10"), "working", None)]),
        run("run_a", "garden", at("08:30"),
            phases=[(at("08:30"), "working", None), (at("08:55"), "waiting_permission", "Bash"),
                    (at("09:00"), "working", None), (at("09:40"), "idle", None), (at("10:00"), "working", None)]),
        run("run_b", None, at("09:10"), agent="codex",
            phases=[(at("09:10"), "working", None), (at("09:20"), "working", None),
                    (at("09:50"), "error", "rate_limit"), (at("09:55"), "working", None)]),
        run("run_c", "plot", at("09:30"),
            phases=[(at("09:30"), "working", None), (at("09:58"), "waiting_input", None),
                    (at("10:04"), "working", None), (at("10:12"), "waiting_input", None)]),
        run("run_f", "tests", at("09:45"),
            phases=[(at("09:45"), "working", None), (at("10:05"), "idle", None)]),
    ],
    "interactions": [
        {"runId": "run_a", "kind": "reply", "at": at("09:00")},
        {"runId": "run_c", "kind": "attend", "at": at("10:00"), "until": at("10:04")},
        {"runId": "run_a", "kind": "reply", "at": at("10:00")},
        {"runId": "run_c", "kind": "reply", "at": at("10:04")},
    ],
    "truncated": False,
}

LANES_EMPTY: dict[str, Any] = {
    "today": TODAY, "now": at("10:20"),
    "windowStart": at("00:00"), "windowEnd": "2026-10-01T00:00:00+08:00",
    "human": {"sessions": [], "running": None, "presence": []},
    "agents": [], "interactions": [], "truncated": False,
}


def truncated() -> dict[str, Any]:
    d = copy.deepcopy(LANES_FULL)
    d["truncated"] = True
    return d


def just_after_midnight() -> dict[str, Any]:
    """「现在」00:30：最近 1 / 3 小时都跨过了今天零点，前端要补拉 ?from=昨天&to=今天。"""
    d = copy.deepcopy(LANES_EMPTY)
    d["now"] = at("00:30")
    return d
