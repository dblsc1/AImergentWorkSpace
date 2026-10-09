"""代理运行的关闭边界（契约 v2.4：stop / 超时 / 相位 / attend 共用的原子边界）。

- 关闭前写进去的相位 / 连线一定进 ``agent.run.completed`` 的 data；关闭后到的一律 closed / 不记。
- 所有关闭路径统一按结束时刻裁剪。
- 标记后崩溃：下一次 stop 重试或超时清理按标记里的快照补 ingest，outcome 以标记为准。
- 不报相位的运行写出的 data 与 v2.1 逐字节相同。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

API = "/api/core"
AGENTS = f"{API}/agents"


def _db():
    from app.repo import get_db  # noqa: PLC0415

    return get_db()


def _start(client, **body):
    resp = client.post(f"{AGENTS}/start", json={"agent": "claude-code", "tool": "claude-code", **body})
    assert resp.status_code == 201, resp.text
    return resp.json()


def _phase(client, run_id, phase, at, **extra):
    resp = client.post(f"{AGENTS}/{run_id}/phase", json={"phase": phase, "at": at, **extra})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _at(run, seconds):
    return (datetime.fromisoformat(run["startedAt"]) + timedelta(seconds=seconds)).isoformat()


def _event(run_id):
    return _db()["events"].find_one({"dedupeKey": f"agent:{run_id}"}, {"_id": 0})


@pytest.fixture()
def shift_clock(monkeypatch):
    from app.modules.timer import service  # noqa: PLC0415

    real = service._now

    def shift(**delta):
        monkeypatch.setattr(service, "_now", lambda: real() + timedelta(**delta))

    return shift


def test_plain_run_event_data_unchanged_from_v21(client):
    run = _start(client)
    client.post(f"{AGENTS}/{run['runId']}/stop", json={"outcome": "done"})
    assert set(_event(run["runId"])["data"]) == {"agent", "tool", "startAt", "durationSeconds", "outcome"}


def test_stop_lands_phases_interactions_label_and_trims_after_end(client, shift_clock):
    run = _start(client, phase="idle", label="garden", match="garden")
    shift_clock(seconds=100)
    _phase(client, run["runId"], "working", _at(run, 10), reply=True, detail="Bash")
    _phase(client, run["runId"], "waiting_permission", _at(run, 50))
    _phase(client, run["runId"], "idle", _at(run, 250), reply=True)  # 超前，但在 300s 内：收下
    from app.modules.timer import service  # noqa: PLC0415

    for sec in (60, 90, 200):  # +200：起点在结束后
        moment = datetime.fromisoformat(_at(run, sec))
        service.record_attend("u_local", run["runId"], [(moment, moment)], timedelta(seconds=45))
    client.post(f"{AGENTS}/{run['runId']}/stop", json={"outcome": "done"})  # 结束于 ~+100s

    data = _event(run["runId"])["data"]
    assert data["label"] == "garden"
    assert [(p["phase"], p.get("detail")) for p in data["phases"]] == [
        ("idle", None), ("working", "Bash"), ("waiting_permission", None)]  # +250 那条被裁掉
    kinds = [(i["kind"], i["at"]) for i in data["interactions"]]
    assert kinds == [("reply", _at(run, 10)), ("attend", _at(run, 60))]  # +250 的 reply、+200 的 attend 被裁
    assert data["interactions"][1]["until"] == _at(run, 90)
    assert "match" not in data and "clientKey" not in data


def test_timeout_close_trims_to_cap_and_clamps_attend(client, shift_clock, monkeypatch):
    from app.modules.timer import service  # noqa: PLC0415

    run = _start(client, match="garden")
    cap = 12 * 3600
    shift_clock(seconds=cap + 50)
    for sec in (cap - 10, cap + 20):  # 延长过了上限
        moment = datetime.fromisoformat(_at(run, sec))
        service.record_attend("u_local", run["runId"], [(moment, moment)], timedelta(seconds=45))
    assert client.get(f"{API}/views/current").json()["agents"] == []  # 读时收超时
    data = _event(run["runId"])["data"]
    assert data["outcome"] == "timeout" and data["durationSeconds"] == cap
    ended = (datetime.fromisoformat(run["startedAt"]) + timedelta(seconds=cap)).isoformat()
    assert data["interactions"] == [{"kind": "attend", "at": _at(run, cap - 10), "until": ended}]


def test_phase_after_mark_is_closed_and_not_recorded(client):
    from app.modules.timer import repo  # noqa: PLC0415

    run = _start(client)
    snap = repo.mark_agent_run_closing("u_local", run["runId"],
                                       {"outcome": "done", "endedAt": datetime.now(timezone.utc).isoformat()})
    assert snap is not None
    out = _phase(client, run["runId"], "idle", _at(run, 1), reply=True)
    assert out["reason"] == "closed"
    # 相位端点先做的超时清理顺手按快照补完了落账
    assert _db()["agent_runs"].count_documents({}) == 0
    assert "phases" not in _event(run["runId"])["data"]


def test_crash_between_mark_and_ingest_is_repaired_by_stop_retry(client, monkeypatch):
    from app.modules.events import service as events_service  # noqa: PLC0415

    run = _start(client, phase="working")
    real = events_service.ingest
    monkeypatch.setattr(events_service, "ingest", lambda env: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        client.post(f"{AGENTS}/{run['runId']}/stop", json={"outcome": "failed"})
    doc = _db()["agent_runs"].find_one({"runId": run["runId"]})
    assert doc["closing"]["outcome"] == "failed" and _event(run["runId"]) is None
    # 重试的 stop 换了 outcome：落账的仍是标记里定死的那个
    monkeypatch.setattr(events_service, "ingest", real)
    resp = client.post(f"{AGENTS}/{run['runId']}/stop", json={"outcome": "done"})
    assert resp.status_code == 200 and resp.json()["outcome"] == "failed"
    assert _phase(client, run["runId"], "idle", _at(run, 1))["reason"] == "closed"
    assert _db()["agent_runs"].count_documents({}) == 0
    assert _db()["events"].count_documents({"type": "agent.run.completed"}) == 1
    assert [p["phase"] for p in _event(run["runId"])["data"]["phases"]] == ["working"]


def test_crash_leftover_is_repaired_by_timeout_sweep(client, monkeypatch):
    from app.modules.events import service as events_service  # noqa: PLC0415

    run = _start(client)
    real = events_service.ingest
    monkeypatch.setattr(events_service, "ingest", lambda env: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        client.post(f"{AGENTS}/{run['runId']}/stop", json={"outcome": "cancelled"})
    monkeypatch.setattr(events_service, "ingest", real)
    _start(client)  # start 也会先清理
    assert _event(run["runId"])["data"]["outcome"] == "cancelled"
    assert _db()["agent_runs"].count_documents({"runId": run["runId"]}) == 0


def test_cas_conflict_retries(client, monkeypatch):
    from app.modules.timer import repo  # noqa: PLC0415

    run = _start(client)
    real = repo.cas_agent_run
    calls = {"n": 0}

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            _db()["agent_runs"].update_one({"runId": run["runId"]}, {"$inc": {"v": 1}})  # 并发写抢先
        return real(*args, **kwargs)

    monkeypatch.setattr(repo, "cas_agent_run", flaky)
    assert _phase(client, run["runId"], "idle", _at(run, 1))["applied"] is True
    assert calls["n"] == 2


def test_stop_racing_phase_never_loses_an_acknowledged_write(client, monkeypatch):
    """相位的 CAS 在关闭标记之后必然落空：让 stop 恰好在相位读完文档、写回之前打标记。"""
    from app.modules.timer import repo  # noqa: PLC0415

    run = _start(client)
    real = repo.cas_agent_run

    def close_first(*args, **kwargs):
        repo.mark_agent_run_closing("u_local", run["runId"],
                                    {"outcome": "done", "endedAt": datetime.now(timezone.utc).isoformat()})
        return real(*args, **kwargs)

    monkeypatch.setattr(repo, "cas_agent_run", close_first)
    out = _phase(client, run["runId"], "idle", _at(run, 1), reply=True)
    assert (out["applied"], out["reason"]) == (False, "closed")
    monkeypatch.setattr(repo, "cas_agent_run", real)
    client.get(f"{API}/views/current")
    data = _event(run["runId"])["data"]
    assert "phases" not in data and "interactions" not in data
