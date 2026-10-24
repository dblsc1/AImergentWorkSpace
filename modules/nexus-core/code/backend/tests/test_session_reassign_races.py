"""改挂的三处「刚好撞上」（2026-10-08 波次统一审核；契约 v2.11「改挂未分类时间」节「并发」「投影」「读端」）。

每条先把那个交错钉死再断言——不靠碰运气：
1. 一段已落账、时间线那一行还没写，这时改挂：时间线也得跟到新归属，不等重建；
2. 租户的投影文档 / 时间线那一行还不存在，两条**不同**的写同时首建：输的那个不能被当成「早已应用过」；
3. 信封里客户端自带的 ``currentSubject`` / 自动记录标记不作数：归属只从改挂链算，能不能改挂只看服务端自己写的。
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

import pytest
from pymongo.errors import DuplicateKeyError

from test_session_reassign import API, EVENTS, _db, _post, _read, _reassign, _session, _world
from test_session_reassign_guard import _external
from test_session_reassign_views import _rebuild, _task_seconds, _views


def _repo():
    from app.modules.projector import repo  # noqa: PLC0415

    return repo


# ------------------------------------------------------------------ 1. 改挂赶在时间线那一行之前


@pytest.mark.parametrize("targets", [["a"], ["a", "c"], ["a", "bucket"]])
def test_reassign_before_the_lane_row_exists_still_moves_the_lane(client, monkeypatch, targets):
    from app.modules.projector import registry  # noqa: PLC0415
    from app.modules.projector.handlers import lanes  # noqa: PLC0415

    w = _world(client)
    seqs = []

    def late_lane(envelope):  # 事实已落账、两张汇总投影已写，时间线那一行还没写：人就在这时改挂
        for target in targets:
            body = {"projectId": w["p"]} if target == "bucket" else {"taskId": w[target]}
            seqs.append(_reassign(client, envelope["id"], **body)["seq"])
        assert _repo().lanes_empty(), "记着去向的占位行不算时间线的数据（启动补建看的是它）"
        lanes.handle(envelope)

    with monkeypatch.context() as patch:
        handlers = registry.DISPATCH["session.completed"]
        assert handlers[-1] is lanes.handle
        patch.setitem(registry.DISPATCH, "session.completed", (*handlers[:-1], late_lane))
        _session(client, w["bucket"], 60, 600)
    assert seqs == list(range(1, len(targets) + 1))

    final = w[targets[-1]]
    live = _views(client)
    assert [(s["taskId"], s["durationSeconds"]) for s in live["sessions"]] == [(final, 600)], "时间线跟到最后一次改挂"
    assert _task_seconds(client) == {final: 600}, "合计照旧只算一遍"
    _rebuild()
    assert _views(client) == live, "不用重建就已经是重建出来的样子"


def test_stale_reassign_arriving_after_a_newer_one_does_not_win_the_pending_lane(client):
    """占位行同样按 seq 只进不退：第 2 次改挂先到、第 1 次后到，再写那一行——留下的是第 2 次的去向。"""
    repo = _repo()
    start = datetime.now(timezone.utc) - timedelta(minutes=5)
    row = {"user": "u_local", "key": "k1", "kind": "session", "startAt": start, "endAt": start + timedelta(minutes=1),
           "durationSeconds": 60, "taskId": "t_old", "projectId": "p_old", "mode": "do", "source": "ext"}
    assert repo.reassign_lane("u_local", "k1", "t_two", "p_two", 2) is True
    assert repo.reassign_lane("u_local", "k1", "t_one", "p_one", 1) is False
    assert repo.apply_lane(dict(row)) is True
    assert repo.apply_lane(dict(row)) is False, "重投递仍是 no-op"
    (lane,) = repo.read_lanes("u_local", "session", start - timedelta(hours=1), start + timedelta(hours=1), 10)
    assert (lane["taskId"], lane["projectId"], lane["durationSeconds"], lane["source"]) == ("t_two", "p_two", 60, "ext")
    # 代理的运行不是人的段：对着它的键来的改挂不动它，也不占位
    run = {**row, "key": "k_run", "kind": "run", "taskId": "t_run", "projectId": "p_run"}
    assert repo.apply_lane(dict(run)) is True
    assert repo.reassign_lane("u_local", "k_run", "t_x", "p_x", 1) is False
    (kept,) = repo.read_lanes("u_local", "run", start - timedelta(hours=1), start + timedelta(hours=1), 10)
    assert kept["taskId"] == "t_run"


def test_values_starting_with_a_dollar_are_stored_literally_in_a_lane(client):
    # 那一行现在用聚合管道写：外部事件里以 $ 开头的字符串是数据，不是字段引用
    repo = _repo()
    start = datetime.now(timezone.utc) - timedelta(minutes=5)
    assert repo.apply_lane({"user": "u_local", "key": "k$", "kind": "run", "startAt": start, "endAt": start,
                            "durationSeconds": 1, "taskId": "$key", "projectId": "$$ROOT", "label": "$user",
                            "phases": [{"at": "x", "phase": "$kind"}]}) is True
    (lane,) = repo.read_lanes("u_local", "run", start - timedelta(hours=1), start + timedelta(hours=1), 10)
    assert (lane["taskId"], lane["projectId"], lane["label"], lane["phases"]) == (
        "$key", "$$ROOT", "$user", [{"at": "x", "phase": "$kind"}])


# ------------------------------------------------------------------ 2. 并发首建：输的那个不是「重复」


class _LosesFirstInsert:
    """包住一张集合：第一次带 upsert 的 ``update_one`` 发出之前，让对手的那一笔先落进去，然后照 mongo 在这个交错下的
    行为抛 ``DuplicateKeyError``（它查的时候文档还不在，决定新建，插入时撞了唯一索引；过滤条件里带 ``$ne`` / ``$not``，
    服务端不会替我们重试）。"""

    def __init__(self, col, rival):
        self._col, self._rival = col, rival

    def update_one(self, *args, **kwargs):
        if kwargs.get("upsert") and self._rival:
            rival, self._rival = self._rival, None
            rival()
            raise DuplicateKeyError("E11000 duplicate key error collection (lost the first-insert race)")
        return self._col.update_one(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._col, name)


def _lose_first_insert(monkeypatch, accessor: str, rival) -> None:
    repo = _repo()
    proxy = _LosesFirstInsert(getattr(repo, accessor)(), rival)
    monkeypatch.setattr(repo, accessor, lambda: proxy)


@pytest.mark.parametrize("loser", ["session", "reassign"])
def test_first_write_race_on_proj_current_keeps_both_the_session_and_the_move(client, monkeypatch, loser):
    repo = _repo()

    def session():
        return repo.apply_session("u_race", "timer:s1", "p_1", "t_bucket", 600)

    def move():
        return repo.move_session("u_race", "reassign:evt_1:1", 600, "p_1", "t_bucket", "p_2", "t_a")

    first, rival = (session, move) if loser == "session" else (move, session)
    _lose_first_insert(monkeypatch, "_col", rival)
    assert first() is True, "撞唯一索引的是另一条不同的写，本条还没应用过"
    assert repo.read_current("u_race") == {
        "user": "u_race", "totalSeconds": 600, "projects": {"p_2": 600}, "tasks": {"t_a": 600},
        "appliedKeys": ["timer:s1"]}
    assert first() is False and rival() is False, "各自仍只应用一次"
    assert repo.read_current("u_race")["totalSeconds"] == 600


@pytest.mark.parametrize("loser", ["lane", "reassign"])
def test_first_write_race_on_a_lane_row_keeps_the_row_and_the_assignment(client, monkeypatch, loser):
    repo = _repo()
    start = datetime.now(timezone.utc) - timedelta(minutes=5)
    row = {"user": "u_local", "key": "k1", "kind": "session", "startAt": start, "endAt": start + timedelta(minutes=1),
           "durationSeconds": 60, "taskId": "t_bucket", "projectId": "p_1", "mode": "do", "source": "ext"}

    def lane():
        return repo.apply_lane(dict(row))

    def move():
        return repo.reassign_lane("u_local", "k1", "t_a", "p_2", 1)

    first, rival = (lane, move) if loser == "lane" else (move, lane)
    _lose_first_insert(monkeypatch, "_lanes_col", rival)
    assert first() is True
    (got,) = repo.read_lanes("u_local", "session", start - timedelta(hours=1), start + timedelta(hours=1), 10)
    assert (got["taskId"], got["projectId"], got["durationSeconds"]) == ("t_a", "p_2", 60)


def test_real_threads_racing_the_first_write_never_lose_a_delta(client):
    """不打桩再来一遍：每个租户的 proj_current 都从无到有，落账与改挂两个线程同时撞。循环多轮，不 sleep。"""
    repo = _repo()
    repo._col()  # noqa: SLF001 —— 先把索引建好：要撞的是文档，不是建索引
    for i in range(60):
        user, barrier = f"u_race{i}", threading.Barrier(2)

        def go(write, barrier=barrier):
            barrier.wait()
            write()

        threads = [
            threading.Thread(target=go, args=(lambda u=user: repo.apply_session(u, "timer:s1", "p_1", "t_bucket", 600),)),
            threading.Thread(target=go, args=(
                lambda u=user: repo.move_session(u, "reassign:evt_1:1", 600, "p_1", "t_bucket", "p_2", "t_a"),)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        doc = repo.read_current(user)
        assert (doc["totalSeconds"], doc["projects"], doc["tasks"]) == (600, {"p_2": 600}, {"t_a": 600}), f"第 {i} 轮"


# ------------------------------------------------------------------ 3. 客户端自带的字段不作数


def test_client_supplied_current_subject_is_never_served(client):
    """外部段自带 currentSubject 指着任务 b，真正的 subject 是任务 a，没有任何改挂：读端只认 a。"""
    w = _world(client)
    lie = {"zone": w["zone"], "project": w["p"], "task": w["b"]}
    forged = {**_external(w, "evt_forge1", "forge-1", w["a"]), "currentSubject": lie}
    assert _post(client, EVENTS, forged)["accepted"] == 1
    assert "currentSubject" not in _db()["events"].find_one({"dedupeKey": "forge-1"}), "入口就摘掉，不落库"
    # 快照恢复 / 老数据里已经带着它的：读的时候不认。别的类型的事件也一样
    _db()["events"].insert_many([
        {**forged, "id": "evt_forge2", "dedupeKey": "forge-2"},
        {**forged, "id": "evt_forge3", "dedupeKey": "forge-3", "type": "note.custom"},
    ])

    for type_ in ("session.completed", None):
        params = {"type": type_} if type_ else {}
        assert all("currentSubject" not in e for e in _read(client, **params)["items"])
        assert _read(client, taskId=w["b"], **params)["total"] == 0, "谎报的归属不进 taskId 过滤"
    assert sorted(e["dedupeKey"] for e in _read(client, taskId=w["a"])["items"]) == ["forge-1", "forge-2", "forge-3"]
    assert _task_seconds(client) == {w["a"]: 60}  # 只有 forge-1 走了入口；投影与读端说的是同一个归属

    # 真改挂之后才有这个键，值是链算出来的，不是它自带的那个
    _db()["events"].delete_many({"dedupeKey": {"$in": ["forge-2", "forge-3"]}})
    bucket = {**_external(w, "evt_forge4", "forge-4", w["bucket"], 50), "currentSubject": lie}
    assert _post(client, EVENTS, bucket)["accepted"] == 1
    _reassign(client, "evt_forge4", taskId=w["c"])
    items = {e["dedupeKey"]: e for e in _read(client, type="session.completed")["items"]}
    assert items["forge-4"]["currentSubject"]["task"] == w["c"] and "currentSubject" not in items["forge-1"]


def test_external_ingest_cannot_forge_the_auto_recorded_marker(client):
    """能改挂的只有「原本在桶上的」与「服务端自动记下的」。外部 source 自称 activity-confirmed + ai.auto 不算后者。"""
    w = _world(client)
    ai = {"generated": True, "confidence": 0.99, "confirmed": False, "auto": True}
    forged = {**_external(w, "evt_auto1", "activity:sug_forged", w["a"]), "source": "activity-confirmed", "ai": ai}
    other = {**_external(w, "evt_auto2", "auto-2", w["a"], 50), "ai": ai}  # 别的 source：开放标准允许自报 auto，原样留
    assert _post(client, EVENTS, [forged, other]) == {"accepted": 2, "duplicate": 0, "rejected": []}
    stored = {e["id"]: e["ai"] for e in _db()["events"].find({"id": {"$in": ["evt_auto1", "evt_auto2"]}})}
    assert stored == {"evt_auto1": {"generated": True, "confidence": 0.99, "confirmed": False}, "evt_auto2": ai}
    for event_id in ("evt_auto1", "evt_auto2"):
        assert "自动记下" in _reassign(client, event_id, 409, taskId=w["b"])["detail"]
    assert _db()["events"].count_documents({"type": "session.reassigned"}) == 0
    assert client.get(f"{API}/views/gantt").status_code == 200


def test_external_source_cannot_squat_the_reassign_dedupe_key(client):
    """改挂写台账的防重身份是 (session-reassign, reassign:<段>:<n>)。公开入口上自称这个 source 的信封一律不收——
    否则一条别的类型的外部事件先占住这个键，那一段就再也改挂不了（每次都「重复」，试满 5 次 409）。"""
    w = _world(client)
    sid = _session(client, w["bucket"], 60, 600)
    squat = {**_external(w, "evt_squat", f"reassign:{sid}:1", w["a"]), "source": "session-reassign"}
    out = _post(client, EVENTS, [squat, {**squat, "type": "note.custom"}])
    assert out["accepted"] == 0 and [r["index"] for r in out["rejected"]] == [0, 1]
    assert all("session-reassign" in r["reason"] for r in out["rejected"])
    assert _reassign(client, sid, taskId=w["a"])["seq"] == 1
    assert _task_seconds(client) == {w["a"]: 600}
