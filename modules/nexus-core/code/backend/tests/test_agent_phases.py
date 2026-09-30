"""代理相位 + start 的 v2.4 选填字段 + 关闭边界（契约 v2.4「人一条线、代理多条线的时间线」）。

要害：每条观测按 (at, 到达先后) 原样存、幂等、时钟规则、上限、结束后一律 applied:false，
以及「关闭是一道原子边界」——关闭前写进去的一定进事实，关闭后的一律不记，崩在半路能补。
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

import pytest

API = "/api/core"
AGENTS = f"{API}/agents"
A = {"X-Nexus-Tenant": "ch_aaaa"}
B = {"X-Nexus-Tenant": "ch_bbbb"}


def _db():
    from app.repo import get_db  # noqa: PLC0415

    return get_db()


def _start(client, headers=None, expect=201, **body):
    resp = client.post(f"{AGENTS}/start", json={"agent": "claude-code", "tool": "claude-code", **body},
                       headers=headers or {})
    assert resp.status_code == expect, resp.text
    return resp.json()


def _phase(client, run_id, phase, at, headers=None, expect=200, **extra):
    resp = client.post(f"{AGENTS}/{run_id}/phase", json={"phase": phase, "at": at, **extra}, headers=headers or {})
    assert resp.status_code == expect, resp.text
    return resp.json()


def _at(run, seconds):
    return (datetime.fromisoformat(run["startedAt"]) + timedelta(seconds=seconds)).isoformat()


def _doc(run_id):
    return _db()["agent_runs"].find_one({"runId": run_id}, {"_id": 0})


def _event(run_id):
    return _db()["events"].find_one({"dedupeKey": f"agent:{run_id}"}, {"_id": 0})


@pytest.fixture()
def shift_clock(monkeypatch):
    from app.modules.timer import service  # noqa: PLC0415

    real = service._now

    def shift(**delta):
        monkeypatch.setattr(service, "_now", lambda: real() + timedelta(**delta))

    return shift


# ─────────────────────────────────────────── start 的选填字段


def test_start_optional_fields_and_views_current(client):
    run = _start(client, phase="idle", label="garden", match="garden")
    doc = _doc(run["runId"])
    assert doc["phases"] == [{"at": run["startedAt"], "phase": "idle"}]
    assert (doc["label"], doc["match"]) == ("garden", "garden")
    plain = _start(client)
    assert "phases" not in _doc(plain["runId"]) and "label" not in _doc(plain["runId"])
    agents = client.get(f"{API}/views/current").json()["agents"]
    assert [(a["phase"], a["label"]) for a in agents] == [("idle", "garden"), (None, None)]


@pytest.mark.parametrize("body", [
    {"phase": "busy"}, {"label": ""}, {"label": "x" * 65}, {"match": "ab"}, {"match": "m" * 129},
    {"clientKey": ""}, {"clientKey": "k" * 129},
])
def test_start_rejects_bad_optional_fields(client, body):
    _start(client, expect=422, **body)


def test_client_key_start_is_idempotent_while_running(client):
    first = _start(client, clientKey="k_1")
    again = _start(client, expect=200, clientKey="k_1")
    assert again == first
    assert _db()["agent_runs"].count_documents({}) == 1
    # 别的租户同 key：各开各的
    other = _start(client, headers=B, clientKey="k_1")
    assert other["runId"] != first["runId"]
    # 结束后同 key 再 start = 新运行
    client.post(f"{AGENTS}/{first['runId']}/stop", json={"outcome": "done"})
    fresh = _start(client, clientKey="k_1")
    assert fresh["runId"] != first["runId"]


def test_client_key_concurrent_starts_open_one_run():
    from app.modules.timer import service  # noqa: PLC0415

    results = []

    def go():
        results.append(service.agent_start(None, "a", "t", None, "u_local", client_key="k_race"))

    threads = [threading.Thread(target=go) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len({out["runId"] for out, _ in results}) == 1
    assert sum(created for _, created in results) == 1
    assert _db()["agent_runs"].count_documents({}) == 1


def test_client_key_of_timed_out_run_opens_new_run(client, shift_clock):
    first = _start(client, clientKey="k_t")
    shift_clock(hours=13)
    fresh = _start(client, clientKey="k_t")
    assert fresh["runId"] != first["runId"]
    assert _event(first["runId"])["data"]["outcome"] == "timeout"


# ─────────────────────────────────────────── 排序、幂等、时钟


def test_out_of_order_observations_are_stored_by_at_not_arrival(client):
    run = _start(client)
    rid = run["runId"]
    assert _phase(client, rid, "working", _at(run, 10))["phase"] == "working"
    assert _phase(client, rid, "working", _at(run, 30))["phase"] == "working"
    out = _phase(client, rid, "idle", _at(run, 20))  # 迟到的中间那条
    assert out == {"runId": rid, "phase": "working", "applied": True, "reason": None}
    assert [p["phase"] for p in _doc(rid)["phases"]] == ["working", "idle", "working"]  # 存的时候不合并


def test_same_at_keeps_arrival_order(client):
    run = _start(client)
    at = _at(run, 5)
    _phase(client, run["runId"], "waiting_input", at)
    assert _phase(client, run["runId"], "working", at)["phase"] == "working"
    assert [p["phase"] for p in _doc(run["runId"])["phases"]] == ["waiting_input", "working"]


def test_duplicate_same_instant_even_with_other_offset(client):
    run = _start(client)
    at = datetime.fromisoformat(_at(run, 7))
    _phase(client, run["runId"], "idle", at.isoformat(), detail="x")
    other_offset = at.astimezone(timezone(timedelta(hours=8))).isoformat()
    out = _phase(client, run["runId"], "idle", other_offset)
    assert (out["applied"], out["reason"], out["phase"]) == (False, "duplicate", "idle")
    # 同相位、不同 at：照收（合并是读方的事）
    assert _phase(client, run["runId"], "idle", _at(run, 8))["applied"] is True
    assert len(_doc(run["runId"])["phases"]) == 2


def test_clock_rules(client, shift_clock):
    run = _start(client)
    now = datetime.now(timezone.utc)
    _phase(client, run["runId"], "working", (now + timedelta(seconds=400)).isoformat(), expect=422)
    near_future = (now + timedelta(seconds=200)).isoformat()
    assert _phase(client, run["runId"], "idle", near_future)["applied"] is True
    assert _doc(run["runId"])["phases"][-1]["at"] == near_future  # 不钳，原样
    # 早于 startedAt：钳到起点
    _phase(client, run["runId"], "waiting_input", _at(run, -60))
    assert _doc(run["runId"])["phases"][0] == {"at": run["startedAt"], "phase": "waiting_input"}
    # 两条都早于起点的同相位 → 钳完同一刻 = 重复
    assert _phase(client, run["runId"], "waiting_input", _at(run, -30))["reason"] == "duplicate"


@pytest.mark.parametrize("body", [
    {"phase": "busy", "at": "2026-09-30T10:00:00+08:00"},
    {"phase": "idle", "at": "2026-09-30T10:00:00"},
    {"phase": "idle", "at": "yesterday"},
    {"phase": "idle", "at": "2026-09-30T10:00:00+08:00", "detail": "d" * 65},
    {"phase": "idle", "at": "2026-09-30T10:00:00+08:00", "extra": 1},
    {"at": "2026-09-30T10:00:00+08:00"},
])
def test_phase_body_validation(client, body):
    run = _start(client)
    assert client.post(f"{AGENTS}/{run['runId']}/phase", json=body).status_code == 422


def test_detail_control_chars_stripped(client):
    run = _start(client)
    _phase(client, run["runId"], "error", _at(run, 1), detail="rate\x00_limit\n")
    assert _doc(run["runId"])["phases"][-1]["detail"] == "rate_limit"


def test_unknown_or_foreign_run_is_404(client):
    run = _start(client, headers=A)
    _phase(client, "run_nope", "idle", _at(run, 1), headers=A, expect=404)
    _phase(client, run["runId"], "idle", _at(run, 1), headers=B, expect=404)


def test_closed_and_timed_out_runs_answer_closed(client, shift_clock):
    run = _start(client, phase="working")
    client.post(f"{AGENTS}/{run['runId']}/stop", json={"outcome": "done"})
    out = _phase(client, run["runId"], "idle", _at(run, 1), reply=True)
    assert out == {"runId": run["runId"], "phase": "working", "applied": False, "reason": "closed"}
    late = _start(client)
    shift_clock(hours=13)
    assert _phase(client, late["runId"], "idle", _at(late, 60))["reason"] == "closed"
    assert _event(late["runId"])["data"]["outcome"] == "timeout"  # phase 是写端点：先收了超时


def test_cap_and_reply_on_capped(client, monkeypatch):
    from app.modules.timer import agent_phases  # noqa: PLC0415

    monkeypatch.setattr(agent_phases, "MAX_PHASES", 2)
    run = _start(client)
    _phase(client, run["runId"], "working", _at(run, 1))
    _phase(client, run["runId"], "idle", _at(run, 2))
    out = _phase(client, run["runId"], "working", _at(run, 3), reply=True)
    assert (out["applied"], out["reason"], out["phase"]) == (False, "capped", "idle")
    # capped 的请求重试：reply 不多出一条
    _phase(client, run["runId"], "working", _at(run, 3), reply=True)
    doc = _doc(run["runId"])
    assert len(doc["phases"]) == 2
    assert doc["interactions"] == [{"kind": "reply", "at": _at(run, 3)}]


def test_reply_generation(client):
    run = _start(client)
    _phase(client, run["runId"], "working", _at(run, 5), reply=True)
    _phase(client, run["runId"], "working", _at(run, 5), reply=True)  # 重复：不记第二条
    _phase(client, run["runId"], "idle", _at(run, 9))  # 没 reply
    assert _doc(run["runId"])["interactions"] == [{"kind": "reply", "at": _at(run, 5)}]


def test_concurrent_phase_writes_all_land():
    from app.modules.timer import agent_phases, service  # noqa: PLC0415

    run, _ = service.agent_start(None, "a", "t", None, "u_local")
    base = datetime.fromisoformat(run["startedAt"])
    errors = []

    def go(i):
        try:
            agent_phases.record_phase(run["runId"], "working" if i % 2 else "idle",
                                      (base + timedelta(seconds=i)).isoformat(), None, True, "u_local",
                                      now=lambda: datetime.now(timezone.utc))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=go, args=(i,)) for i in range(1, 21)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    doc = _doc(run["runId"])
    assert [p["at"] for p in doc["phases"]] == [(base + timedelta(seconds=i)).isoformat() for i in range(1, 21)]
    assert len(doc["interactions"]) == 20
