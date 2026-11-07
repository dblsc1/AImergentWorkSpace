"""onboarding.v1 测试的共用小工具。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from _tier_common import db, rec as _rec


def rec(date, start, end, folder=None, **kw) -> dict:
    out = _rec(date, start, end, **kw)
    if folder is not None:
        out["folder"] = folder
    return out


def live_rec(offset: int, seconds: int, app="ptyxis", title="Coder", **extra) -> dict:
    """从「此刻 + offset 秒」起 seconds 秒的记录。"""
    s = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(seconds=offset)
    return {"startAt": s.isoformat(), "endAt": (s + timedelta(seconds=seconds)).isoformat(), "app": app, "title": title,
            "titleIsPseudonym": False, "block": None, **extra}


def set_browser_domains(domains: list[str]) -> None:
    db().activity_settings.update_one({"user": "u_local"}, {"$set": {"browserDomains": domains}}, upsert=True)


def run_attending(client, agent: str, spans: list[tuple[datetime, datetime]], **body) -> str:
    """开一条代理运行，并给它记上 attend 区间。"""
    from app.modules.timer import service  # noqa: PLC0415

    r = client.post("/api/core/agents/start", json={"agent": agent, "tool": "claude-code", **body})
    assert r.status_code == 201, r.text
    run_id = r.json()["runId"]
    if spans:  # 运行要早于记录才与记录重叠
        from _tier_common import db  # noqa: PLC0415

        db().agent_runs.update_one({"runId": run_id}, {"$set": {"startedAt": (min(a for a, _ in spans) - timedelta(seconds=60)).isoformat()}})
    for a, b in spans:
        service.record_attend("u_local", run_id, [(a, b)], timedelta(0))
    return run_id
