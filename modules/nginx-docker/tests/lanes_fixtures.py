"""`GET /api/core/views/lanes` 的桩数据（nexus-core 契约 v2.4「读端」节的形状）。

顶栏预览（本目录 test_navbar_lanes.py）与计时页全部泳道（modules/ring 的 test_ring_lanes.py）共用。
「现在」钉在 2026-09-30 10:20（+08:00），测试的浏览器时区也钉 Asia/Shanghai。

剧情（仓主要的一对多）：garden 在干活 → 等批准 → 人回话 → 接着干；plot 在等人说话 → 人去看（attend）
→ 回话 → plot 接着干 → 又在等；codex 报过一次错；docs 早上跑完了；old-job 超时还挂着；tests 空闲。
v2.17「你在看」（agents[].attention，蓝条）：人 09:00–09:02 看过 garden、10:00–10:04 看 plot、10:10 起一直在看 garden
（到「现在」还在看 → 活边）；在场的最后一段因此带着 garden 的 runId。fast_switching() = 每 3 秒切一次窗口的那种人。
"""

from __future__ import annotations

import copy
from typing import Any

TZ = "+08:00"
TODAY = "2026-09-30"


def at(hm: str, day: str = TODAY) -> str:
    return f"{day}T{hm}:00{TZ}"


def run(run_id: str, label: str | None, start: str, *, agent: str = "claude-code", end: str | None = None,
        phases: list[tuple[str, str, str | None]] = (), overdue: bool = False,
        attention: list[tuple[str, str]] = ()) -> dict[str, Any]:
    return {
        "runId": run_id, "agent": agent, "tool": agent, "model": None, "label": label,
        "taskId": None, "projectId": None, "startAt": start, "endAt": end,
        "outcome": "completed" if end else None, "elapsedSeconds": 1200, "overdue": overdue,
        "phases": [{"at": a, "phase": p, "detail": d} for a, p, d in phases],
        "attention": [{"from": a, "to": b} for a, b in attention],
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
             "title": "<b>plot.gd</b> — garden — VS Code", "afk": False, "runId": None},
            {"deviceId": "dev_x", "from": at("10:05"), "to": at("10:10"), "app": "", "title": "", "afk": True,
             "runId": None},
            {"deviceId": "dev_x", "from": at("10:10"), "to": at("10:20"), "app": "ptyxis",
             "title": "garden", "afk": False, "runId": "run_a"},
        ],
    },
    "agents": [
        run("run_e", "old-job", at("21:00", "2026-09-29"), overdue=True),
        run("run_d", "docs", at("07:00"), end=at("09:15"),
            phases=[(at("07:00"), "working", None), (at("08:00"), "idle", None), (at("08:10"), "working", None)]),
        run("run_a", "garden", at("08:30"),
            phases=[(at("08:30"), "working", None), (at("08:55"), "waiting_permission", "Bash"),
                    (at("09:00"), "working", None), (at("09:40"), "idle", None), (at("10:00"), "working", None)],
            attention=[(at("09:00"), at("09:02")), (at("10:10"), at("10:20"))]),
        run("run_b", None, at("09:10"), agent="codex",
            phases=[(at("09:10"), "working", None), (at("09:20"), "working", None),
                    (at("09:50"), "error", "rate_limit"), (at("09:55"), "working", None)]),
        run("run_c", "plot", at("09:30"),
            phases=[(at("09:30"), "working", None), (at("09:58"), "waiting_input", None),
                    (at("10:04"), "working", None), (at("10:12"), "waiting_input", None)],
            attention=[(at("10:00"), at("10:04"))]),
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


def folded() -> dict[str, Any]:
    """nexus-core v2.23：封顶折叠掉了较早的 7 + 5 段（两个身份）。"""
    d = truncated()
    d["dropped"] = [{"agent": "claude-code", "label": "plot", "unverified": False, "runs": 7, "elapsedSeconds": 420},
                    {"agent": "codex", "label": "rev", "unverified": False, "runs": 5, "elapsedSeconds": 150}]
    return d


def just_after_midnight() -> dict[str, Any]:
    """「现在」00:30：最近 1 / 3 小时都跨过了今天零点，前端要补拉 ?from=昨天&to=今天。"""
    d = copy.deepcopy(LANES_EMPTY)
    d["now"] = at("00:30")
    return d


def fast_switching(rounds: int = 40) -> dict[str, Any]:
    """每 3 秒切一次窗口的人（nexus-core v2.17）：10:16:00 起 garden → plot → 编辑器轮着来，每个 3 秒，一直切到「现在」。
    在场是一条不重叠的细时间线；garden / plot 各自的 attention 就是轮到它们的那几段（人同一时刻只看一个）。"""
    d = copy.deepcopy(LANES_FULL)
    d["human"]["running"] = None
    start = 10 * 3600 + 20 * 60 - rounds * 3

    def clock(sec: int) -> str:
        return f"{TODAY}T{sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}{TZ}"

    windows = [("ptyxis", "garden", "run_a"), ("ptyxis", "plot", "run_c"), ("code", "notes.md — 编辑器", None)]
    presence, seen = [], {"run_a": [], "run_c": []}
    for i in range(rounds):
        app, title, run_id = windows[i % 3]
        a, b = clock(start + 3 * i), clock(start + 3 * i + 3)
        presence.append({"deviceId": "dev_x", "from": a, "to": b, "app": app, "title": title, "afk": False,
                         "runId": run_id})
        if run_id:
            seen[run_id].append({"from": a, "to": b})
    d["human"]["presence"] = presence
    for agent in d["agents"]:
        agent["attention"] = seen.get(agent["runId"], [])
    d["interactions"] = [i for i in d["interactions"] if i["kind"] == "reply"]
    return d
