"""改挂之后各读端的合计、重建、恢复、并发（契约 v2.11「改挂未分类时间」节「投影」「并发」）。

要害一句：**台账链的最后一条 = 各投影的状态 = 从台账重建出来的状态。** 实时投影是「从旧归属减、往新归属加」，
重建是「按当前归属重放原事实」——两条路算的必须是同一个数，本文件逐个读端比。
"""

from __future__ import annotations

import random
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

from test_restore import _dry_run_checksum, _restore, _wipe
from test_session_reassign import API, _chain, _db, _reassign, _session, _world


def _rebuild(only=None):
    from app.modules.projector.rebuild import rebuild  # noqa: PLC0415

    return rebuild(only)


def _views(client) -> dict:
    """全部会受改挂影响的读端，拼成一份可比较的快照。"""
    gantt = client.get(f"{API}/views/gantt").json()
    today = date.fromisoformat(gantt["today"])
    lanes = client.get(f"{API}/views/lanes", params={"from": str(today - timedelta(days=1)), "to": str(today)})
    assert lanes.status_code == 200, lanes.text
    return {
        "gantt": gantt,
        "review": client.get(f"{API}/views/review").json(),
        "sessions": lanes.json()["human"]["sessions"],
        "projections": client.get(f"{API}/export").json()["projections"],
    }


def _task_seconds(client) -> dict[str, int]:
    """甘特任务层：任务 id → 合计秒（没有事实的任务不出现）。"""
    out = {}
    for project in client.get(f"{API}/views/gantt").json()["projects"]:
        for task in project["tasks"]:
            if task["actual"]:
                out[task["id"]] = sum(d["seconds"] for d in task["actual"])
    return out


def _project_seconds(views: dict) -> dict[str, int]:
    return {p["id"]: sum(d["seconds"] for d in p["actual"]) for p in views["gantt"]["projects"] if p["actual"]}


# ------------------------------------------------------------------ 每个读端都跟着走


def test_time_moves_in_every_view(client):
    w = _world(client)
    first, second = _session(client, w["bucket"], 60, 600), _session(client, w["bucket"], 90, 300)
    _session(client, w["a"], 120, 100)

    _reassign(client, first, taskId=w["a"])  # 同项目内
    v = _views(client)
    assert _task_seconds(client) == {w["bucket"]: 300, w["a"]: 700}
    assert _project_seconds(v) == {w["p"]: 1000}
    current = v["projections"]["proj_current"]
    assert current["tasks"] == {w["bucket"]: 300, w["a"]: 700} and current["projects"] == {w["p"]: 1000}
    assert current["totalSeconds"] == 1000

    _reassign(client, first, taskId=w["c"])   # 再改：跨项目，时间跟着换项目
    _reassign(client, second, taskId=w["b"])  # 桶被归空
    v = _views(client)
    assert _task_seconds(client) == {w["a"]: 100, w["b"]: 300, w["c"]: 600}, "归空的桶不留 seconds:0 的条目"
    assert _project_seconds(v) == {w["p"]: 400, w["q"]: 600}
    current = v["projections"]["proj_current"]
    assert current["tasks"] == {w["a"]: 100, w["b"]: 300, w["c"]: 600}
    assert current["projects"] == {w["p"]: 400, w["q"]: 600} and current["totalSeconds"] == 1000
    assert all(row["seconds"] > 0 for row in v["projections"]["proj_daily_stats"])
    # 时间线：段还是那三段，时刻时长不变，归属换了
    assert sorted((s["durationSeconds"], s["taskId"], s["projectId"]) for s in v["sessions"]) == [
        (100, w["a"], w["p"]), (300, w["b"], w["p"]), (600, w["c"], w["q"])]
    # 回顾：本周实际按项目跟着走；桶上没时间了，c 有了「最近动过」
    week = {p["projectId"]: p["actualSecondsThisWeek"] for p in v["review"]["planVsActual"]}
    assert sum(week.values()) == 1000 and week[w["q"]] in (0, 600) and week[w["p"]] in (0, 400)
    assert w["c"] not in {t["id"] for t in v["review"]["staleTasks"]}
    # 圆环读端：正在计 c 时，它的累计含改挂进来的 600
    client.post(f"{API}/timer/start", json={"taskId": w["c"]})
    ring = client.get(f"{API}/views/current").json()
    assert (ring["task"]["totalSeconds"], ring["project"]["totalSeconds"]) == (600, 600)
    assert ring["project"]["shareOfPlan"] == 60.0
    client.post(f"{API}/timer/cancel")


# ------------------------------------------------------------------ 重建 = 实时


def _mixed_history(client) -> dict:
    w = _world(client)
    s1, s2, s3 = (_session(client, w["bucket"], m, secs) for m, secs in ((60, 600), (90, 300), (120, 200)))
    _session(client, w["a"], 150, 100)
    _reassign(client, s1, taskId=w["a"])
    _reassign(client, s1, taskId=w["c"])
    _reassign(client, s2, taskId=w["b"])
    _reassign(client, s2, projectId=w["p"])  # 放回桶
    _reassign(client, s3, projectId=w["q"])  # 另一个项目的桶
    return w


def test_rebuild_from_ledger_equals_live_projection(client):
    _mixed_history(client)
    live = _views(client)
    ledger = list(_db()["events"].find({}))

    assert _rebuild() == {"proj_current": 4, "proj_daily_stats": 4, "proj_agent_daily_stats": 0, "proj_lanes": 4}
    assert _views(client) == live
    for only in ("proj_current", "proj_daily_stats", "proj_lanes"):  # 单独重建一张也一样
        _rebuild(only)
        assert _views(client) == live
    _rebuild()
    assert _views(client) == live, "重建幂等"
    assert list(_db()["events"].find({})) == ledger, "重建只读台账"


def test_rebuild_does_not_depend_on_ledger_order(client):
    _mixed_history(client)
    live = _views(client)
    docs = list(_db()["events"].find({}, {"_id": 0}))
    _db()["events"].delete_many({})
    _db()["events"].insert_many(docs[::-1])  # 改挂排到它的段前面、后一次改挂排到前一次前面
    _rebuild()
    after = _views(client)
    # appliedKeys 的顺序跟着重放顺序走，它是幂等账不是读数；其余逐字段相同
    for snapshot in (live, after):
        snapshot["projections"]["proj_current"]["appliedKeys"].sort()
    assert after == live


def test_reassign_of_a_deleted_session_does_nothing_on_rebuild(client):
    # 运维脚本删掉了那一段、它的改挂还在台账里：重建不能凭空减出负数 / 加出时间
    w = _world(client)
    gone, kept = _session(client, w["bucket"], 60, 600), _session(client, w["bucket"], 90, 300)
    _reassign(client, gone, taskId=w["c"])
    _db()["events"].delete_one({"id": gone})
    _rebuild()
    v = _views(client)
    assert _task_seconds(client) == {w["bucket"]: 300}
    assert v["projections"]["proj_current"]["tasks"] == {w["bucket"]: 300}
    assert [s["taskId"] for s in v["sessions"]] == [w["bucket"]]
    assert _reassign(client, kept, taskId=w["a"])["seq"] == 1  # 其余的段照常


def test_lanes_startup_backfill_uses_the_current_assignment(client):
    from app.modules.projector.rebuild import backfill_lanes_if_empty  # noqa: PLC0415

    w = _world(client)
    _reassign(client, _session(client, w["bucket"]), taskId=w["c"])
    live = _views(client)["sessions"]
    _db()["proj_lanes"].delete_many({})
    assert backfill_lanes_if_empty() == 1
    assert _views(client)["sessions"] == live and live[0]["taskId"] == w["c"]


# ------------------------------------------------------------------ 导出 → 恢复


def test_export_restore_round_trips_the_new_fact(client):
    w = _mixed_history(client)
    before = client.get(f"{API}/export").json()
    assert sum(e["type"] == "session.reassigned" for e in before["events"]) == 5
    views = _views(client)
    _wipe()

    resp = _restore(client, before, dry_run=False, checksum=_dry_run_checksum(client, before))
    assert resp.status_code == 200, resp.text
    assert resp.json()["summary"]["events"] == 9
    after = client.get(f"{API}/export").json()
    for snapshot in (before, after):
        snapshot.pop("exportedAt")
        # 导出的事件顺序是索引序、不是落账序（既有行为），恢复后幂等账的顺序跟着变；它不是读数
        snapshot["projections"]["proj_current"]["appliedKeys"].sort()
    assert after == before, "台账原样、投影重建后与导出时相同"
    restored = _views(client)
    for snapshot in (views, restored):
        snapshot["projections"]["proj_current"]["appliedKeys"].sort()
    assert restored == views
    # 恢复后链接着走：下一次改挂是第 3 次
    s1 = next(e["id"] for e in before["events"] if e["type"] == "session.completed"
              and e["data"]["durationSeconds"] == 600)
    assert _reassign(client, s1, taskId=w["b"])["seq"] == 3


# ------------------------------------------------------------------ 并发


def test_racing_reassigns_leave_ledger_order_equal_to_projection(client):
    w = _world(client)
    sid = _session(client, w["bucket"], 60, 600)
    _session(client, w["a"], 90, 100)
    rng = random.Random(7)
    targets = [rng.choice([{"taskId": w["a"]}, {"taskId": w["b"]}, {"taskId": w["c"]}, {"projectId": w["p"]}])
               for _ in range(24)]

    def go(body):
        return client.post(f"{API}/sessions/{sid}/reassign", json=body).status_code

    with ThreadPoolExecutor(8) as pool:
        codes = list(pool.map(go, targets))
    assert set(codes) <= {200, 409} and codes.count(200) > 0  # 409 = 连续几次没抢到，重试即可

    chain = _chain(sid)
    assert [f["data"]["seq"] for f in chain] == list(range(1, len(chain) + 1)), "seq 连续、不重不漏"
    previous = w["bucket"]
    for fact in chain:  # 链连续：谁也没有从一个过时的归属出发
        assert fact["data"]["fromTaskId"] == previous
        previous = fact["data"]["toTaskId"]

    expected = {w["a"]: 100}
    expected[previous] = expected.get(previous, 0) + 600
    assert _task_seconds(client) == expected, "投影 = 链的最后一条"
    assert [s["taskId"] for s in _views(client)["sessions"] if s["durationSeconds"] == 600] == [previous]
    live = _views(client)
    _rebuild()
    assert _views(client) == live


def test_many_sessions_reassigned_at_once_to_one_task_all_count(client):
    # 几段同时归到同一任务同一天：那一行并发首建，一段都不能漏
    w = _world(client)
    sessions = [_session(client, w["bucket"], 30 + i, 60) for i in range(12)]
    with ThreadPoolExecutor(8) as pool:
        codes = list(pool.map(lambda s: client.post(
            f"{API}/sessions/{s}/reassign", json={"taskId": w["c"]}).status_code, sessions))
    assert codes == [200] * 12
    assert _task_seconds(client) == {w["c"]: 720}
    live = _views(client)
    _rebuild()
    assert _views(client) == live


# ------------------------------------------------------------------ 运维脚本


def test_prune_judges_orphans_by_current_assignment(client):
    import sys  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import prune_orphan_events as prune  # noqa: PLC0415

    w = _world(client)
    rescued, left = _session(client, w["bucket"], 60), _session(client, w["bucket"], 90)
    _reassign(client, rescued, taskId=w["c"])  # 归到别的项目的任务上
    for collection, entity in (("projects", w["p"]), ("tasks", w["bucket"]), ("tasks", w["a"]), ("tasks", w["b"])):
        _db()[collection].delete_one({"id": entity})  # 原项目连桶一起没了

    plan = prune.collect(_db(), "nexus_core_test")
    assert [e["id"] for e in plan.orphans] == [left], "归走的那段不是孤儿"
    assert [e["id"] for e in plan.kept] == [rescued]
