"""proj_lanes 启动时自动补建（契约 v2.4「上线」补注）：空而台账有事实 → 补；非空 / 无事实 → 不动；
并发启动只做一遍、结果不重不漏；持锁者崩了留下的过期锁可以被接管。"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

API = "/api/core"


def _db():
    from app.repo import get_db  # noqa: PLC0415

    return get_db()


def _facts(client, seeded):
    task = next(iter(seeded["tasks"].values()))
    client.post(f"{API}/timer/start", json={"taskId": task["id"]})
    client.post(f"{API}/timer/stop")
    for tenant in ("u_local", "ch_bbbb"):
        run = client.post(f"{API}/agents/start", json={"agent": "a", "tool": "t"},
                          headers={"X-Nexus-Tenant": tenant}).json()
        client.post(f"{API}/agents/{run['runId']}/stop", json={"outcome": "done"}, headers={"X-Nexus-Tenant": tenant})
    assert _db()["proj_lanes"].count_documents({}) == 3


def test_backfills_when_empty_then_noop(client, seeded):
    from app.modules.projector.rebuild import backfill_lanes_if_empty  # noqa: PLC0415

    _facts(client, seeded)
    before = sorted(d["key"] for d in _db()["proj_lanes"].find({}))
    _db()["proj_lanes"].delete_many({})  # 模拟：v2.4 之前的库升级上来
    assert backfill_lanes_if_empty() == 3
    assert sorted(d["key"] for d in _db()["proj_lanes"].find({})) == before  # 全体租户
    assert backfill_lanes_if_empty() == 0  # 非空：no-op
    assert _db()["_startup_locks"].count_documents({}) == 0  # 锁已释放


def test_noop_without_relevant_facts():
    from app.modules.projector.rebuild import backfill_lanes_if_empty  # noqa: PLC0415

    assert backfill_lanes_if_empty() == 0
    assert _db()["proj_lanes"].count_documents({}) == 0


def test_startup_runs_it(client, seeded):
    from fastapi.testclient import TestClient  # noqa: PLC0415

    from app.main import app  # noqa: PLC0415

    _facts(client, seeded)
    _db()["proj_lanes"].delete_many({})
    with TestClient(app) as fresh:  # 再走一次 lifespan
        assert len(fresh.get(f"{API}/views/lanes").json()["agents"]) == 1
    assert _db()["proj_lanes"].count_documents({}) == 3


def test_fresh_lock_skips_stale_lock_is_taken_over(client, seeded):
    from app.modules.projector.rebuild import backfill_lanes_if_empty  # noqa: PLC0415

    _facts(client, seeded)
    _db()["proj_lanes"].delete_many({})
    now = datetime.now(timezone.utc)
    _db()["_startup_locks"].insert_one({"_id": "proj_lanes", "at": now})
    assert backfill_lanes_if_empty() == 0 and _db()["proj_lanes"].count_documents({}) == 0
    _db()["_startup_locks"].update_one({"_id": "proj_lanes"}, {"$set": {"at": now - timedelta(minutes=11)}})
    assert backfill_lanes_if_empty() == 3


def test_concurrent_startups_converge(client, seeded):
    from app.modules.projector.rebuild import backfill_lanes_if_empty  # noqa: PLC0415

    _facts(client, seeded)
    _db()["proj_lanes"].delete_many({})
    results, errors = [], []

    def go():
        try:
            results.append(backfill_lanes_if_empty())
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=go) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors and max(results) == 3
    assert _db()["proj_lanes"].count_documents({}) == 3


def test_failure_does_not_block_startup(monkeypatch):
    from fastapi.testclient import TestClient  # noqa: PLC0415

    from app import main  # noqa: PLC0415

    def boom():
        raise RuntimeError("bad ledger")

    monkeypatch.setattr(main, "backfill_lanes_if_empty", boom)
    with TestClient(main.app) as fresh:
        assert fresh.get(f"{API}/health").status_code == 200
