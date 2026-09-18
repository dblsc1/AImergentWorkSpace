"""``POST /timer/backfill`` —— 补登（contract.md「补登（规范性 · v1.8，backfill）」节）。

三个最容易做错的点（规格点名，逐条落测试）：
- **dedupeKey 归一化**：`+08:00` 与等价的 `Z` 写法必须撞同一个键（否则防重完全失效）。
- **不改投影 handler**：本文件只验证「组出的信封落库后，既有 handler 照常处理」，
  不新增任何投影相关断言之外的东西。
- **必须走 events_service.ingest**：由 events 集合里的记录（`recordedAt` 有值、
  唯一索引生效）间接证明，不直接戳内部实现。

7 条拒绝规则逐条一条用例（`test_reject_*`），外加过旧 startAt 的**不拦**正向用例。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

API = "/api/core"


def _db():
    from app.repo import get_db

    return get_db()


def _events(**query) -> list[dict]:
    return list(_db()["events"].find(query or {}, {"_id": 0}))


def _current_projection() -> dict | None:
    from app.modules.projector import repo

    return repo.read_current("u_local")


def _daily_stats() -> list[dict]:
    return list(_db()["proj_daily_stats"].find({}, {"_id": 0}))


def _past_start_at(*, days_ago: int = 3, hour: int = 10, minute: int = 0) -> datetime:
    """构造一个「肯定在过去」、**按 `NEXUS_TZ` 本地时刻**的时间——不写死 UTC。

    本文件会在多个非 UTC 时区（如 Asia/Shanghai、America/New_York）下重跑；
    `hour`/`minute` 说的是「本地墙钟几点」，不是 UTC 几点
    ——固定用 UTC 构造在跨零点用例上会与非 UTC 的 `NEXUS_TZ` 打架（同一个 UTC
    时刻换到 +08:00/-05:00 下可能已经跨了一天，「23:40」就不再是「本地 23:40」）。
    """
    from app.config import settings  # noqa: PLC0415 —— 延迟到测试运行时读，避免与 conftest 的 env 注入时序打架

    tz = settings.tz
    local_date = (datetime.now(timezone.utc) - timedelta(days=days_ago)).astimezone(tz).date()
    return datetime(local_date.year, local_date.month, local_date.day, hour, minute, tzinfo=tz)


def _backfill(client, *, task_id: str, start_at: str, duration_seconds: int):
    return client.post(
        f"{API}/timer/backfill",
        json={"taskId": task_id, "startAt": start_at, "durationSeconds": duration_seconds},
    )


# ───────────────────────────────────────────── 基本成功路径 + 零投影改动


def test_backfill_creates_event_and_updates_projections(client, seeded):
    task_id = seeded["tasks"]["示例任务三"]["id"]
    zone_id = seeded["zones"]["示例分区二"]["id"]
    project_id = seeded["projects"]["示例项目三"]["id"]

    started = _past_start_at()
    start_at_iso = started.isoformat()

    resp = _backfill(client, task_id=task_id, start_at=start_at_iso, duration_seconds=1800)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["recorded"] is True
    assert body["duplicate"] is False
    assert body["date"] == started.date().isoformat()
    assert body["event"]["type"] == "session.completed"
    assert body["event"]["dedupeKey"].startswith("backfill:")

    events = _events(type="session.completed")
    assert len(events) == 1
    event = events[0]

    # 服务端组装的信封（契约「服务端组装的信封」表，逐条核）
    assert event["source"] == "manual-backfill", "source 必须换成 manual-backfill，不是 timer-backend"
    assert event["subject"] == {"zone": zone_id, "project": project_id, "task": task_id}
    assert event["data"] == {"durationSeconds": 1800, "startAt": start_at_iso}
    assert event["flags"] == []
    assert event["recordedAt"], "recordedAt 由事件入口盖章——证明走的是 events_service.ingest"
    normalized = started.astimezone(timezone.utc).isoformat()
    assert event["dedupeKey"] == f"backfill:{task_id}:{normalized}:1800"
    expected_time = (started + timedelta(seconds=1800)).isoformat()
    assert event["time"] == expected_time, "time = startAt + durationSeconds（会话结束时刻）"

    # 零投影改动：本端点没碰任何 handler，proj_current/proj_daily_stats 依旧被既有
    # handler 正确处理——这是「data 形状与 stop() 同形」的直接证明。
    current = _current_projection()
    assert current is not None
    assert current["projects"][project_id] == 1800
    assert current["tasks"][task_id] == 1800

    stats = _daily_stats()
    assert len(stats) == 1
    assert stats[0]["projectId"] == project_id
    assert stats[0]["taskId"] == task_id
    assert stats[0]["date"] == started.date().isoformat()
    assert stats[0]["seconds"] == 1800


# ───────────────────────────────────────────── 防重幂等 + dedupeKey 归一化


def test_backfill_same_request_twice_is_idempotent(client, seeded):
    task_id = seeded["tasks"]["示例任务三"]["id"]
    project_id = seeded["projects"]["示例项目三"]["id"]
    start_at_iso = _past_start_at().isoformat()

    first = _backfill(client, task_id=task_id, start_at=start_at_iso, duration_seconds=1800)
    second = _backfill(client, task_id=task_id, start_at=start_at_iso, duration_seconds=1800)

    assert first.status_code == 200 and second.status_code == 200
    first_body, second_body = first.json(), second.json()

    assert first_body["duplicate"] is False
    assert second_body["duplicate"] is True
    assert second_body["recorded"] is True, "duplicate 时 recorded 依然是 true（同 IngestOut 语义）"
    # 第二次回显的是原来那条：id 必须与第一次相同
    assert second_body["event"]["id"] == first_body["event"]["id"]
    assert second_body["event"]["dedupeKey"] == first_body["event"]["dedupeKey"]

    assert len(_events(type="session.completed")) == 1, "events 只多一条"
    current = _current_projection()
    assert current["projects"][project_id] == 1800, "proj_current 只加一次"


def test_backfill_dedupe_key_normalizes_timezone(client, seeded):
    """归一化要害：`+08:00` 与等价的 `Z` 写法必须撞同一条 dedupeKey。

    这条测的是**客户端传入字符串的偏移写法**，与服务端 `NEXUS_TZ` 无关（那是
    归日用的，两回事）——因此这里不走 `_past_start_at`（那个按 `NEXUS_TZ` 构造
    本地墙钟），直接手搭一个绝对 UTC 时刻，保证在任何 `NEXUS_TZ` 下都成立。
    """
    task_id = seeded["tasks"]["示例任务三"]["id"]

    utc_moment = (datetime.now(timezone.utc) - timedelta(days=3)).replace(
        hour=6, minute=30, second=0, microsecond=0
    )  # 06:30 UTC，绝对时刻，与 NEXUS_TZ 无关
    plus8_moment = utc_moment.astimezone(timezone(timedelta(hours=8)))  # 同一时刻，+08:00 写法
    assert plus8_moment.hour == 14  # 前置：确实是同一时刻的两种写法（14:30+08:00 == 06:30Z）

    z_style = utc_moment.isoformat().replace("+00:00", "Z")
    plus8_style = plus8_moment.isoformat()
    assert z_style != plus8_style, "前置：两个字符串本身不同，不是巧合撞对了"

    first = _backfill(client, task_id=task_id, start_at=plus8_style, duration_seconds=900)
    second = _backfill(client, task_id=task_id, start_at=z_style, duration_seconds=900)

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["duplicate"] is False
    assert second.json()["duplicate"] is True, (
        "不归一化就是同一件事算出两个不同的键，防重会直接失效——"
        "这条必须变红，如果实现直接拼用户传入的原始字符串"
    )
    assert len(_events(type="session.completed")) == 1


# ───────────────────────────────────────────── 跨零点：归到开始那天


def test_backfill_cross_midnight_boundary_lands_on_start_day(client, seeded):
    task_id = seeded["tasks"]["示例任务三"]["id"]

    started = _past_start_at(hour=23, minute=40)  # 本地（=UTC）23:40
    resp = _backfill(
        client, task_id=task_id, start_at=started.isoformat(), duration_seconds=40 * 60
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["date"] == started.date().isoformat(), "归日必须落在开始那天，不是结束那天（跨零点）"

    stats = _daily_stats()
    assert len(stats) == 1
    assert stats[0]["date"] == started.date().isoformat()


# ───────────────────────────────────────────── 补登不改 timer_state


def test_backfill_does_not_touch_timer_state(client, seeded):
    running_task = seeded["tasks"]["示例任务三"]["id"]
    other_task = seeded["tasks"]["示例任务四"]["id"]

    start_resp = client.post(f"{API}/timer/start", json={"taskId": running_task})
    assert start_resp.status_code == 200, start_resp.text
    running_start_at = start_resp.json()["startAt"]

    before = client.get(f"{API}/views/current").json()
    assert before["running"] is True
    assert before["sessionStartAt"] == running_start_at

    backfill_resp = _backfill(
        client,
        task_id=other_task,
        start_at=_past_start_at().isoformat(),
        duration_seconds=600,
    )
    assert backfill_resp.status_code == 200, backfill_resp.text

    after = client.get(f"{API}/views/current").json()
    assert after["running"] is True, "补登不许停掉正在进行的计时"
    assert after["task"]["id"] == running_task, "补登不许把正在计时的任务切走"
    assert after["sessionStartAt"] == running_start_at, "sessionStartAt 必须原封不动"

    from app.modules.timer import service

    state = service.get_running_state()
    assert state is not None and state["taskId"] == running_task


# ───────────────────────────────────────────── 7 条拒绝规则逐条


def test_reject_unknown_task_is_404(client, seeded):
    resp = _backfill(
        client, task_id="t_missing", start_at=_past_start_at().isoformat(), duration_seconds=600
    )
    assert resp.status_code == 404
    assert "t_missing" in resp.json()["detail"]
    assert _events() == []


def test_reject_non_positive_duration_is_400(client, seeded):
    task_id = seeded["tasks"]["示例任务三"]["id"]
    for bad in (0, -100):
        resp = _backfill(
            client, task_id=task_id, start_at=_past_start_at().isoformat(), duration_seconds=bad
        )
        assert resp.status_code == 400, f"duration={bad}：{resp.status_code} {resp.text}"
    assert _events() == []


def test_reject_duration_over_one_day_is_400(client, seeded):
    task_id = seeded["tasks"]["示例任务三"]["id"]
    resp = _backfill(
        client,
        task_id=task_id,
        start_at=_past_start_at(days_ago=5).isoformat(),
        duration_seconds=86401,
    )
    assert resp.status_code == 400, resp.text
    assert _events() == []


def test_reject_start_at_without_timezone_is_400(client, seeded):
    task_id = seeded["tasks"]["示例任务三"]["id"]
    resp = _backfill(client, task_id=task_id, start_at="2026-08-01T10:00:00", duration_seconds=600)
    assert resp.status_code == 400, resp.text
    assert "时区" in resp.json()["detail"], f"错误消息要点名缺时区，实际：{resp.json()}"
    assert _events() == []


def test_reject_start_at_unparseable_is_400(client, seeded):
    task_id = seeded["tasks"]["示例任务三"]["id"]
    resp = _backfill(client, task_id=task_id, start_at="不是时间", duration_seconds=600)
    assert resp.status_code == 400, resp.text
    assert _events() == []


def test_reject_backfill_into_the_future_is_400(client, seeded):
    task_id = seeded["tasks"]["示例任务三"]["id"]
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    resp = _backfill(client, task_id=task_id, start_at=future.isoformat(), duration_seconds=600)
    assert resp.status_code == 400, resp.text
    assert _events() == []


def test_old_start_at_is_not_rejected(client, seeded):
    """过旧的 startAt **不拦**——补三个月前的活是正当需求。"""
    task_id = seeded["tasks"]["示例任务三"]["id"]
    resp = _backfill(
        client, task_id=task_id, start_at=_past_start_at(days_ago=120).isoformat(), duration_seconds=600
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["duplicate"] is False
