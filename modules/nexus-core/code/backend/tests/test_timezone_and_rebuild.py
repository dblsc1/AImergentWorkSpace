"""日界时区 + 投影重建（契约「日界与时区」/「投影重建」v0.9；2026-08-02 验收 T1–T9）。T1（NEXUS_TZ 必填/非法即 die）已在 test_config.py
覆盖，本文件覆盖 T2–T9：归日按 NEXUS_TZ、today 与归日共用同一函数、事实
不动、重建只读事实/先清后放/幂等，以及重建结果与手工汇总一致。
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.config import load_settings, settings
from app.modules.events import service as events_service
from app.modules.projector import rebuild as rebuild_module
from app.modules.projector import repo as projector_repo
from app.modules.projector.handlers import daily_stats
from app.modules.views import queries
from app.timeutil import local_date

API = "/api/core"
INGEST = f"{API}/events"

#: 与 conftest 的测试默认值（UTC）不同的一个真实时区，专供本文件断言用——
#: 不改全局默认值，避免动到其余 89 条既有断言（它们写死了 UTC/+00:00 口径）。
SHANGHAI = load_settings(
    {"NEXUS_MONGO_URI": "mongodb://x", "NEXUS_DB_NAME": "nexus_core_test", "NEXUS_TZ": "Asia/Shanghai"}
)

#: 显式 UTC 设置，专供下方「对照组」用——**不依赖 conftest 的全局默认值**。
#: conftest 的 `os.environ.setdefault("NEXUS_TZ", "UTC")` 只在外部没传 NEXUS_TZ
#: 时生效；本规格要求整个套件必须在 NEXUS_TZ=Asia/Shanghai 等非 UTC 环境下也
#: 全绿，那种环境里 setdefault 会让路，daily_stats.settings 实际就是外部传入的
#: 时区——如果对照组不显式 monkeypatch 成 UTC，就会在非 UTC 环境下断言出错误的
#: 日期（这正是本轮修的第三条陈旧 UTC 断言：H1 扫出的，不是新引入的）。
UTC = load_settings(
    {"NEXUS_MONGO_URI": "mongodb://x", "NEXUS_DB_NAME": "nexus_core_test", "NEXUS_TZ": "UTC"}
)


def _post_event(client, *, project_id, task_id, start_at, seconds, dedupe_key) -> dict:
    doc = {
        "spec": "yq-event/v1",
        "id": f"evt_{uuid.uuid4().hex[:8]}",
        "dedupeKey": dedupe_key,
        "type": "session.completed",
        "user": "u_local",
        "source": "tz-test",
        "time": start_at,
        "subject": {"zone": "z_x", "project": project_id, "task": task_id},
        "data": {"durationSeconds": seconds, "startAt": start_at},
        "flags": [],
    }
    resp = client.post(INGEST, json=doc)
    assert resp.status_code == 200 and resp.json()["accepted"] == 1, resp.text
    return doc


# ============================================================= T2：归日按 NEXUS_TZ


def test_t2_cross_midnight_boundary_uses_nexus_tz_not_utc(seeded, monkeypatch):
    """要害断言：UTC+8 下 startAt=2026-08-01T17:30:00+00:00（本地 8/2 01:30）
    必须归到 2026-08-02，不是 UTC 日期 08-01。"""
    monkeypatch.setattr(daily_stats, "settings", SHANGHAI)
    project_id = seeded["projects"]["示例项目三"]["id"]
    task_id = seeded["tasks"]["示例任务三"]["id"]
    envelope = {
        "user": "u_local", "dedupeKey": "dk_t2",
        "subject": {"project": project_id, "task": task_id},
        "data": {"durationSeconds": 600, "startAt": "2026-08-01T17:30:00+00:00"},
    }

    daily_stats.handle(envelope)

    rows = projector_repo.read_daily_stats("u_local")
    assert rows == [
        {"date": "2026-08-02", "projectId": project_id, "taskId": task_id, "seconds": 600}
    ], "UTC+8 下本地 8/2 01:30 开始的一段必须归到 08-02，不是 UTC 日期 08-01"


def test_t2_same_moment_under_utc_stays_on_utc_day(seeded, monkeypatch):
    """对照组：同一时刻但 NEXUS_TZ=UTC 时仍归 08-01——证明上一条的差异确实来自
    NEXUS_TZ，不是巧合或别的改动。

    显式 monkeypatch 成 UTC（与上面的 SHANGHAI 用同一手法），**不依赖** conftest
    的全局默认值：旧写法只在「进程实际跑在 UTC 下」才绿，NEXUS_TZ=Asia/Shanghai
    等非 UTC 环境会把这条对照断言也带崩——这正是本轮规格点名要修的陈旧 UTC
    断言之一（H1 扫出的第三条）。
    """
    monkeypatch.setattr(daily_stats, "settings", UTC)
    project_id = seeded["projects"]["示例项目三"]["id"]
    task_id = seeded["tasks"]["示例任务三"]["id"]
    envelope = {
        "user": "u_local", "dedupeKey": "dk_t2_utc",
        "subject": {"project": project_id, "task": task_id},
        "data": {"durationSeconds": 600, "startAt": "2026-08-01T17:30:00+00:00"},
    }

    daily_stats.handle(envelope)

    rows = projector_repo.read_daily_stats("u_local")
    assert rows[0]["date"] == "2026-08-01"


# ============================================================= T3：today 同一函数


def test_t3_today_and_daily_stats_share_the_same_function():
    """契约硬要求：归日与 today 必须用同一个函数，不许各自算一遍。"""
    assert queries.local_date is daily_stats.local_date


def test_t3_today_uses_nexus_tz(client, monkeypatch):
    monkeypatch.setattr(queries, "settings", SHANGHAI)
    expected = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
    resp = client.get(f"{API}/views/gantt")
    assert resp.status_code == 200
    assert resp.json()["today"] == expected


# ============================================================= T4：事实本身不动


def test_t4_rebuild_never_touches_events_collection(client, seeded):
    """重建绝不能碰 events 集合——只读事实、只写投影（契约硬约束）。"""
    project_id = seeded["projects"]["示例项目三"]["id"]
    task_id = seeded["tasks"]["示例任务三"]["id"]
    _post_event(client, project_id=project_id, task_id=task_id,
                start_at="2026-08-01T01:00:00+00:00", seconds=100, dedupe_key="dk_t4")

    before_total, before_items = events_service.list_events()
    rebuild_module.rebuild()
    after_total, after_items = events_service.list_events()

    assert before_total == after_total == 1
    assert before_items == after_items, "重建改变了 events 集合的内容——违反「只读事实」硬约束"


# ============================================================= T5：新投影补齐历史


def test_t5_rebuild_recovers_projection_that_missed_history(client, seeded):
    """v0.8 撞过的坑：新投影错过此前所有历史事实。模拟场景：事实已在库里，
    投影却是空的（就像它是刚加的），rebuild 之后必须补齐全部。

    期望日期现算，不写死字面日期字符串（陈旧断言 H1/H2 同一形状，负偏移下
    01:00 UTC 会跨到前一天）：三个 start 相差整 24 小时，任何固定偏移下都是
    三个不同的本地日期，不依赖具体是哪个时区，所以不需要 monkeypatch，直接用
    当前跑测试时的真实 NEXUS_TZ（``app.config.settings``）现算。
    """
    project_id = seeded["projects"]["示例项目三"]["id"]
    task_id = seeded["tasks"]["示例任务三"]["id"]
    starts = ["2026-08-01T01:00:00+00:00", "2026-08-02T01:00:00+00:00", "2026-08-03T01:00:00+00:00"]
    for i, start in enumerate(starts):
        _post_event(client, project_id=project_id, task_id=task_id,
                    start_at=start, seconds=600 + i, dedupe_key=f"dk_t5_{i}")

    projector_repo.clear_daily_stats()  # 装作这张投影从没跑过
    assert projector_repo.read_daily_stats("u_local") == []

    counts = rebuild_module.rebuild(only="proj_daily_stats")

    assert counts == {"proj_daily_stats": 3}
    rows = projector_repo.read_daily_stats("u_local")
    expected_dates = sorted(local_date(datetime.fromisoformat(s), settings.tz) for s in starts)
    assert sorted(r["date"] for r in rows) == expected_dates
    assert sum(r["seconds"] for r in rows) == 600 + 601 + 602


def test_rebuild_only_scopes_to_a_single_projection(client, seeded):
    """--only 只重建指定投影，不动另一张——不改 DISPATCH 表本身的语义。"""
    project_id = seeded["projects"]["示例项目三"]["id"]
    task_id = seeded["tasks"]["示例任务三"]["id"]
    _post_event(client, project_id=project_id, task_id=task_id,
                start_at="2026-08-01T01:00:00+00:00", seconds=100, dedupe_key="dk_only")
    projector_repo.clear_current()
    projector_repo.clear_daily_stats()

    counts = rebuild_module.rebuild(only="proj_current")

    assert counts == {"proj_current": 1}
    assert projector_repo.read_current("u_local") is not None
    assert projector_repo.read_daily_stats("u_local") == [], "--only=proj_current 不该动 proj_daily_stats"


# ============================================================= T7：先清后放


def test_t7_rebuild_clears_before_replay_not_accumulate(client, seeded):
    """先清目标投影再重放，否则会在已有计数上重复累加。

    期望日期现算，不写死字面日期字符串（陈旧断言 H1/H2 同一形状）——
    只有一条事实，用真实 NEXUS_TZ（``app.config.settings``）现算即可，
    不需要 monkeypatch。
    """
    project_id = seeded["projects"]["示例项目三"]["id"]
    task_id = seeded["tasks"]["示例任务三"]["id"]
    start_at = "2026-08-01T01:00:00+00:00"
    expected_date = local_date(datetime.fromisoformat(start_at), settings.tz)
    _post_event(client, project_id=project_id, task_id=task_id,
                start_at=start_at, seconds=100, dedupe_key="dk_t7")
    # 手工塞一条杂质：模拟「没清空就重放」会遗留的重复累加
    projector_repo.apply_daily_stat(
        user="u_local", dedupe_key="dk_corrupt", date=expected_date,
        project_id=project_id, task_id=task_id, seconds=999999,
    )

    rebuild_module.rebuild(only="proj_daily_stats")

    rows = projector_repo.read_daily_stats("u_local")
    assert rows == [
        {"date": expected_date, "projectId": project_id, "taskId": task_id, "seconds": 100}
    ], "重建后杂质累计的 999999 秒必须消失——证明重放前先清空了投影"


# ============================================================= T8：幂等


def test_t8_rebuild_is_idempotent(client, seeded):
    """连跑两次 rebuild()，proj_current 与 proj_daily_stats 结果必须完全相同。"""
    project_id = seeded["projects"]["示例项目三"]["id"]
    task_id = seeded["tasks"]["示例任务三"]["id"]
    for i, start in enumerate(["2026-08-01T01:00:00+00:00", "2026-08-01T05:00:00+00:00"]):
        _post_event(client, project_id=project_id, task_id=task_id,
                    start_at=start, seconds=100 + i, dedupe_key=f"dk_t8_{i}")

    first_counts = rebuild_module.rebuild()
    daily_first = projector_repo.read_daily_stats("u_local")
    current_first = projector_repo.read_current("u_local")

    second_counts = rebuild_module.rebuild()
    daily_second = projector_repo.read_daily_stats("u_local")
    current_second = projector_repo.read_current("u_local")

    assert first_counts == second_counts
    assert daily_first == daily_second
    assert current_first == current_second


# ==================================================== T9：重建结果与手工汇总一致


def test_t9_rebuild_matches_manual_sum_of_facts(client, seeded, monkeypatch):
    """本条要验证的是「同一天的多条事实汇总求和」这件事本身。

    entries[0]/[1] 相差 4 小时（01:00/05:00 UTC）——这个场景设计**本来就要求**
    两者落在同一个本地日期上，才谈得上「手工汇总」；这只在偏移不太负的时区下
    成立。在足够负的偏移下（比如 America/New_York，UTC-4）01:00 会跨到前一天而
    05:00 不会，两条事实就被拆到了两天，「同一天汇总」这个前提本身就不成立了——
    这不是「算错」，是这条测试的场景设计依赖一个固定时区才有意义（N2 允许的
    「语义本来就必须在特定时区下才成立」的情形）。显式 monkeypatch 成 UTC
    （与 T2 对照组同一手法），期望值仍用 local_date 现算，不写死字面日期字符串。
    """
    monkeypatch.setattr(daily_stats, "settings", UTC)
    project_id = seeded["projects"]["示例项目三"]["id"]
    task_id = seeded["tasks"]["示例任务三"]["id"]
    entries = [
        ("2026-08-01T01:00:00+00:00", 100),
        ("2026-08-01T05:00:00+00:00", 200),
        ("2026-08-02T01:00:00+00:00", 300),
    ]
    for i, (start, secs) in enumerate(entries):
        _post_event(client, project_id=project_id, task_id=task_id,
                    start_at=start, seconds=secs, dedupe_key=f"dk_sum_{i}")

    rebuild_module.rebuild()

    by_date = {r["date"]: r["seconds"] for r in projector_repo.read_daily_stats("u_local")
               if r["projectId"] == project_id}
    day1 = local_date(datetime.fromisoformat(entries[0][0]), UTC.tz)
    day2 = local_date(datetime.fromisoformat(entries[2][0]), UTC.tz)
    assert by_date == {day1: 300, day2: 300}, f"手工汇总：{day1}=100+200，{day2}=300"


def test_rebuild_cli_entrypoint_runs_and_reports_counts(client, seeded):
    """契约给的确切用法：``python -m app.modules.projector.rebuild [--only ...]``
    真跑一次子进程，不只测内部函数。"""
    project_id = seeded["projects"]["示例项目三"]["id"]
    task_id = seeded["tasks"]["示例任务三"]["id"]
    _post_event(client, project_id=project_id, task_id=task_id,
                start_at="2026-08-01T01:00:00+00:00", seconds=100, dedupe_key="dk_cli")

    backend_dir = Path(__file__).resolve().parent.parent
    proc = subprocess.run(
        [sys.executable, "-m", "app.modules.projector.rebuild"],
        cwd=backend_dir, capture_output=True, text=True, env=dict(os.environ),
    )
    assert proc.returncode == 0, proc.stderr
    assert "proj_current" in proc.stdout
    assert "proj_daily_stats" in proc.stdout


def test_rebuild_unknown_only_name_fails_loud():
    with pytest.raises(ValueError, match="不存在的投影"):
        rebuild_module.rebuild(only="不存在的投影")
