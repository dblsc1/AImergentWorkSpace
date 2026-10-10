"""灵活留存的记账不变量（契约 v2.25）：窗口里每条运行恰好落在 agents / inactiveAgents / dropped / expired 四处之一（隐藏的另算）。

纯管线（``_pipeline`` + ``_finish``），不碰库；封顶、隐藏、置顶、失联、超上限、注意力、未验证全随机；``now`` 在 00:10 / 09:00 / 23:50 轮着钉。
"""

from __future__ import annotations

import random
from datetime import date, datetime, time, timedelta

import pytest

BASE = date(2026, 10, 10)
HOURS = [(0, 10), (9, 0), (23, 50)]
TIERS = {"high", "normal", "low"}
COVER = {"inactive": 0, "dropped": 0, "expired": 0, "hidden": 0, "high": 0, "low": 0, "ended": 0}


def _world(rng: random.Random, now: datetime, start: datetime):
    from app.modules.prefs.service import ident  # noqa: PLC0415

    prefs, closed, live, n = {"agents": [], "order": []}, [], [], 0
    hidden_keys: set[str] = set()
    for i in range(rng.randrange(2, 9)):
        label, unv = f"L{i}", rng.random() < 0.15
        key = ident("cc", label, unv)
        mode = rng.choice(["plain"] * 4 + ["hidden", "pinned"])
        if mode == "hidden":
            hidden_keys.add(key)
        if mode != "plain" and not (mode == "pinned" and unv):
            prefs["agents"].append({"key": key, "agent": "cc", "label": label, "unverified": unv, "hidden": mode == "hidden",
                                    "pinned": mode == "pinned", "pinnedAt": "2026-10-10T00:00:00+08:00"})
        quiet = rng.random() < 0.4  # 临时短运行、没人看：low 档的来源
        for _ in range(rng.randrange(1, 7)):
            n += 1
            st = now - timedelta(seconds=rng.randrange(60, 20000 if quiet and rng.random() < 0.5 else int((now - start).total_seconds()) - 1))
            kind = "closed" if quiet and rng.random() < 0.7 else rng.choice(["closed"] * 4 + ["live", "lost", "overdue"])
            phases = [{"at": (st + timedelta(seconds=rng.randrange(0, 4000))).isoformat(), "phase": rng.choice(
                ["working", "idle", "error", "waiting_input"])} for _ in range(rng.randrange(0, 3))]
            ints = [{"kind": "attend", "at": (a := st + timedelta(seconds=rng.randrange(-900, 600))).isoformat(),
                     "until": (a + timedelta(seconds=rng.choice([5, 40, 700]))).isoformat()}
                    for _ in range(rng.randrange(0, 3))]
            if quiet:
                phases, ints = [], []
            run = {"runId": f"r{n}", "agent": "cc", "tool": "t", "model": None, "label": label, "taskId": None, "projectId": None,
                   "outcome": None, "overdue": kind == "overdue", "beatSource": None, "beatCount": rng.choice([0, 3]),
                   "unverified": unv, "startTs": st, "lost": kind == "lost", "lastSeenTs": None, "phases": phases,
                   "interactions": ints, "_key": key}
            if kind == "closed":
                en = st + timedelta(seconds=rng.choice([30, 90, 250] if quiet else [400, 2000, 5000, 9000]))
                en = min(en, now - timedelta(seconds=1)) if en >= now else en
                run |= {"endTs": en, "elapsedSeconds": int((en - st).total_seconds()),
                        "outcome": rng.choice(["done", "failed"]), "open": False}
                closed.append(run)
            else:
                seen = st + timedelta(seconds=rng.randrange(0, int((now - st).total_seconds())))
                run |= {"endTs": None, "open": True, "lastSeenTs": seen,
                        "elapsedSeconds": int(((seen if kind == "lost" else now) - st).total_seconds())}
                live.append(run)
    return prefs, closed, live, hidden_keys


@pytest.mark.parametrize("seed", range(360))
def test_every_run_is_counted_exactly_once(seed, monkeypatch):
    from app.modules.views import lane_cap, lanes  # noqa: PLC0415

    rng = random.Random(seed)
    for mod in (lane_cap, lanes):
        if hasattr(mod, "MAX_LIVE"):
            monkeypatch.setattr(mod, "MAX_LIVE", rng.choice([1, 2, 4, 500]))
    monkeypatch.setattr(lane_cap, "MAX_RUNS_PER_LANE", rng.choice([1, 2, 3, 100]))
    monkeypatch.setattr(lane_cap, "MAX_AGENTS", rng.choice([2, 3, 200]))
    monkeypatch.setattr(lane_cap, "MAX_RUNS_TOTAL", rng.choice([5, 9, 2000]))
    tz = lanes.settings.tz
    now = datetime.combine(BASE, time(*HOURS[seed % 3]), tz)
    start, end = datetime.combine(BASE - timedelta(days=1), time(), tz), datetime.combine(BASE + timedelta(days=1), time(), tz)
    prefs, closed, live, hidden_keys = _world(rng, now, start)

    p = lanes._pipeline(closed, live, prefs, start, end, now, True)
    agents, hidden, waiting, stale, inactive, dropped, expired = lanes._finish(p, p["kept"])

    mine = [r for r in closed + live if r["_key"] not in hidden_keys]
    assert len(agents) + sum(i["runs"] for i in inactive) + sum(d["runs"] for d in dropped) + expired["runs"] == len(mine)
    assert (sum(a["elapsedSeconds"] for a in agents) + sum(i["elapsedSeconds"] for i in inactive)
            + sum(d["elapsedSeconds"] for d in dropped) + expired["elapsedSeconds"]) == sum(r["elapsedSeconds"] for r in mine)
    assert all(i["tier"] in TIERS and i["reason"] in {"error", "idle", "ended"} for i in inactive)
    ident_of = lambda x: (x["agent"], x["label"], x["unverified"])  # noqa: E731
    shown, listed, dropped_ids = {ident_of(a) for a in agents}, {ident_of(i) for i in inactive}, {ident_of(d) for d in dropped}
    assert not (shown & listed) and not (listed & dropped_ids)  # 折叠的泳道不在 agents，也不在 dropped 里重复数
    COVER["inactive"] += bool(inactive)
    COVER["dropped"] += bool(dropped)
    COVER["expired"] += bool(expired["agents"])
    COVER["hidden"] += bool(hidden_keys)
    COVER["high"] += any(i["tier"] == "high" for i in inactive)
    COVER["low"] += any(i["tier"] == "low" for i in inactive)
    COVER["ended"] += any(i["reason"] == "ended" for i in inactive)
    assert expired["agents"] <= len({r["_key"] for r in mine})
    assert (expired["agents"] == 0) == (expired["runs"] == 0 == expired["elapsedSeconds"])


def test_the_random_worlds_cover_every_branch():
    """上面 360 个种子里每种情形都出现过（单独跑某个种子时跳过）。"""
    if not COVER["inactive"]:
        pytest.skip("种子没跑")
    thin = [k for k, v in COVER.items() if v < 10]
    assert not thin, f"覆盖太少：{thin} {COVER['inactive']}"
