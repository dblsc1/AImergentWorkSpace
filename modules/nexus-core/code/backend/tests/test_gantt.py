"""甘特后端三件套（contract.md v0.8「甘特读端」；2026-08-02 验收 G1–G9）：

- daily_stats 投影（``session.completed`` → ``proj_daily_stats``）
- DISPATCH 表新增第二个 handler
- ``GET /api/core/views/gantt``

不测 planner 十二端点/统一入口本身的既有语义（已在 test_planner_crud.py /
test_planner_unified.py 覆盖），也不重复 timer 金链路的既有断言
（test_timer_golden_chain.py）——只测本轮新增的行为。
"""

from __future__ import annotations

from datetime import datetime

from app.config import settings
from app.modules.projector import repo as projector_repo
from app.modules.projector.handlers import current, daily_stats
from app.modules.projector.registry import DISPATCH
from app.timeutil import local_date

API = "/api/core"
PLANNER = f"{API}/planner"


def _mk_zone(client, name="甘特分区", **extra) -> dict:
    resp = client.post(f"{PLANNER}/zones", json={"name": name, **extra})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _mk_project(client, zone_id, name="甘特项目", **extra) -> dict:
    resp = client.post(f"{PLANNER}/projects", json={"zoneId": zone_id, "name": name, **extra})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _mk_task(client, project_id, name="甘特任务", **extra) -> dict:
    resp = client.post(f"{PLANNER}/tasks", json={"projectId": project_id, "name": name, **extra})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _envelope(
    *,
    project_id: str | None,
    task_id: str | None,
    seconds: int,
    start_at: str | None,
    time_: str | None = None,
    dedupe_key: str = "dk_test",
    user: str = "u_local",
) -> dict:
    subject: dict = {"zone": "z_x"}
    if project_id is not None:
        subject["project"] = project_id
    if task_id is not None:
        subject["task"] = task_id
    data: dict = {"durationSeconds": seconds}
    if start_at is not None:
        data["startAt"] = start_at
    return {
        "spec": "yq-event/v1",
        "id": "evt_test",
        "dedupeKey": dedupe_key,
        "type": "session.completed",
        "user": user,
        "source": "test",
        "time": time_ or start_at or "2026-08-01T00:00:00+00:00",
        "subject": subject,
        "data": data,
        "flags": [],
    }


# ------------------------------------------------------------- G1/G2：handler + DISPATCH


def test_g2_dispatch_routes_session_completed_to_both_handlers():
    """G2：DISPATCH 表里 session.completed 同时挂 current 与 daily_stats
    （只验路由表本身，不是靠间接效果反推——DISPATCH 是唯一联动真相，直接读它）。"""
    handlers = DISPATCH["session.completed"]
    assert current.handle in handlers
    assert daily_stats.handle in handlers


def test_g1_handler_writes_expected_shape(seeded):
    """G1：写入 proj_daily_stats，形状 {date, projectId, taskId, seconds}（按用户查询）。"""
    project_id = seeded["projects"]["示例项目三"]["id"]
    task_id = seeded["tasks"]["示例任务三"]["id"]
    envelope = _envelope(
        project_id=project_id, task_id=task_id, seconds=3600,
        start_at="2026-08-01T10:00:00+00:00",
    )

    daily_stats.handle(envelope)

    rows = projector_repo.read_daily_stats("u_local")
    assert rows == [
        {"date": "2026-08-01", "projectId": project_id, "taskId": task_id, "seconds": 3600}
    ]


def test_g3_handler_is_idempotent_on_repeated_dispatch(seeded):
    """G3：同一条事件（同 dedupeKey）重复派发两次，计数不变——
    这是 handler 自身的幂等守卫，不是靠 events 层的防重（那层挡的是另一件事：
    同一事件别落库两次）。"""
    project_id = seeded["projects"]["示例项目三"]["id"]
    task_id = seeded["tasks"]["示例任务三"]["id"]
    envelope = _envelope(
        project_id=project_id, task_id=task_id, seconds=1800,
        start_at="2026-08-01T09:00:00+00:00", dedupe_key="dk_repeat",
    )

    daily_stats.handle(envelope)
    daily_stats.handle(envelope)  # 同一条事件再投一次

    rows = projector_repo.read_daily_stats("u_local")
    assert rows == [
        {"date": "2026-08-01", "projectId": project_id, "taskId": task_id, "seconds": 1800}
    ], "重复派发同一事件不许重复计数"


def test_g4_date_comes_from_start_at_crosses_midnight(seeded):
    """G4：date 取 data.startAt 不是 time——23:40 开始、跨零点到 00:20 结束的一段
    计时，必须记到「开始那一刻按 NEXUS_TZ 归的日期」，不是结束那天。

    期望值现算，不写死字面日期字符串（陈旧断言 H1/H2）：09:40 UTC 起 startAt
    在 NEXUS_TZ=UTC 下归 08-01、在 NEXUS_TZ=Asia/Shanghai 下已跨到 08-02——
    两个时区下这条断言都要绿，测的是「按配置时区归日」这个行为本身，不是
    UTC 的巧合（v0.9 刚修掉的就是「日界写死 UTC」这个 bug，断言不能把它焊回去）。
    """
    project_id = seeded["projects"]["示例项目三"]["id"]
    task_id = seeded["tasks"]["示例任务三"]["id"]
    start_at = "2026-08-01T23:40:00+00:00"
    envelope = _envelope(
        project_id=project_id, task_id=task_id, seconds=1200,
        start_at=start_at,
        time_="2026-08-02T00:20:00+00:00",  # 结束时刻已跨到第二天
    )

    daily_stats.handle(envelope)

    rows = projector_repo.read_daily_stats("u_local")
    assert len(rows) == 1
    expected_date = local_date(datetime.fromisoformat(start_at), settings.tz)
    assert rows[0]["date"] == expected_date, "必须按 startAt 在 NEXUS_TZ 下归日，不是 time"


def test_current_handle_unaffected_by_new_handler(client, seeded):
    """DISPATCH 新增第二个 handler 不改变 current.handle 的既有行为（零回归的精神；
    P12 已在别的文件覆盖「测试数不减」，这里额外覆盖「新联动不污染旧投影」）。"""
    task_id = seeded["tasks"]["示例任务三"]["id"]
    client.post(f"{API}/timer/start", json={"taskId": task_id})
    stop = client.post(f"{API}/timer/stop")
    assert stop.json()["event"] is not None

    current_projection = projector_repo.read_current("u_local")
    assert current_projection is not None and current_projection["totalSeconds"] >= 1


def test_daily_stats_handler_skips_without_project_id():
    """防御分支：subject 没有 project（理论上 envelope 校验层已挡住，但 handler
    自身也不能假设「上游一定没放过」）——静默跳过，不写任何行。"""
    envelope = _envelope(
        project_id=None, task_id="t_x", seconds=100, start_at="2026-08-01T10:00:00+00:00"
    )
    daily_stats.handle(envelope)
    assert projector_repo.read_daily_stats("u_local") == []


def test_daily_stats_handler_skips_without_start_at():
    """防御分支：外部 source 没有 data.startAt——静默跳过，不拿 time 猜测顶替。"""
    envelope = _envelope(
        project_id="p_x", task_id="t_x", seconds=100, start_at=None,
        time_="2026-08-01T10:00:00+00:00",
    )
    daily_stats.handle(envelope)
    assert projector_repo.read_daily_stats("u_local") == []


def test_daily_stats_handler_skips_non_positive_seconds():
    """防御分支：durationSeconds 非正——没有可累计的东西，静默跳过。"""
    envelope = _envelope(
        project_id="p_x", task_id="t_x", seconds=0, start_at="2026-08-01T10:00:00+00:00"
    )
    daily_stats.handle(envelope)
    assert projector_repo.read_daily_stats("u_local") == []


# ---------------------------------------------------------------- G7–G9：views/gantt


def test_g7_g9_gantt_overlays_plan_and_actual(client, seeded):
    """G7 + G9：/views/gantt 叠加计划（读 planner）与事实（读 proj_daily_stats）；
    没排期/没计时的项目也出现（plan=null、actual=[]）。"""
    project = seeded["projects"]["示例项目三"]
    task = seeded["tasks"]["示例任务三"]

    plan_resp = client.patch(
        f"{PLANNER}/projects/{project['id']}",
        json={"plan": {"start": "2026-08-01", "end": "2026-08-10"}},
    )
    assert plan_resp.status_code == 200

    client.post(f"{API}/timer/start", json={"taskId": task["id"]})
    client.post(f"{API}/timer/stop")

    resp = client.get(f"{API}/views/gantt")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"projects", "today"}

    gantt_project = next(p for p in body["projects"] if p["id"] == project["id"])
    assert set(gantt_project) == {"id", "key", "name", "plan", "actual", "tasks"}
    assert gantt_project["plan"] == {"start": "2026-08-01", "end": "2026-08-10"}
    assert len(gantt_project["actual"]) == 1
    assert gantt_project["actual"][0]["seconds"] >= 1

    other = seeded["projects"]["示例项目二"]
    other_gantt = next(p for p in body["projects"] if p["id"] == other["id"])
    assert other_gantt["plan"] is None
    assert other_gantt["actual"] == []


def test_g7_from_to_filters_actual_date_range(client, seeded):
    """G7：from/to 过滤 actual[] 的日期范围，不影响 plan 或项目本身是否出现。"""
    project_id = seeded["projects"]["示例项目三"]["id"]
    task_id = seeded["tasks"]["示例任务三"]["id"]
    daily_stats.handle(_envelope(
        project_id=project_id, task_id=task_id, seconds=100,
        start_at="2026-08-01T10:00:00+00:00", dedupe_key="dk_a",
    ))
    daily_stats.handle(_envelope(
        project_id=project_id, task_id=task_id, seconds=200,
        start_at="2026-08-05T10:00:00+00:00", dedupe_key="dk_b",
    ))

    filtered = client.get(f"{API}/views/gantt", params={"from": "2026-08-02", "to": "2026-08-31"})
    fp = next(p for p in filtered.json()["projects"] if p["id"] == project_id)
    assert [row["date"] for row in fp["actual"]] == ["2026-08-05"], "from 必须过滤掉 08-01"

    unfiltered = client.get(f"{API}/views/gantt")
    up = next(p for p in unfiltered.json()["projects"] if p["id"] == project_id)
    assert [row["date"] for row in up["actual"]] == ["2026-08-01", "2026-08-05"]


def test_g8_today_is_server_clock_ignores_query_params(client):
    """G8：today 完全由服务端给——不受 from/to 影响，且客户端没有任何入参可以覆盖它。

    期望值按 NEXUS_TZ 现算（陈旧断言 H1/H2：不许写死 UTC「今天」），复用生产代码
    同一个 timeutil.local_date，与 views/queries.py::_today() 同源，不各自算一遍。
    """
    resp = client.get(f"{API}/views/gantt", params={"from": "2000-01-01", "to": "2000-01-02"})
    assert resp.status_code == 200
    assert resp.json()["today"] == local_date(datetime.now(settings.tz), settings.tz)


def test_g9_views_router_has_no_write_methods():
    """G9：views 是纯只读层——机械核验 router 上没有挂任何非 GET 方法。"""
    from app.modules.views.router import router

    methods: set[str] = set()
    for route in router.routes:
        methods |= set(getattr(route, "methods", set()) or set())
    assert methods <= {"GET", "HEAD"}, f"views 出现了非只读方法：{methods}"


def test_gantt_actual_sums_across_multiple_tasks_same_day(client, seeded):
    """契约的 actual 是 project 级按天聚合，不按 task 再细分——同一天两个不同
    task 的时长必须求和成一条 {date, seconds}。"""
    project = seeded["projects"]["示例项目三"]
    task_a = seeded["tasks"]["示例任务三"]["id"]
    task_b = seeded["tasks"]["示例任务四"]["id"]
    daily_stats.handle(_envelope(
        project_id=project["id"], task_id=task_a, seconds=600,
        start_at="2026-08-03T09:00:00+00:00", dedupe_key="dk_task_a",
    ))
    daily_stats.handle(_envelope(
        project_id=project["id"], task_id=task_b, seconds=400,
        start_at="2026-08-03T15:00:00+00:00", dedupe_key="dk_task_b",
    ))

    resp = client.get(f"{API}/views/gantt")
    gp = next(p for p in resp.json()["projects"] if p["id"] == project["id"])
    assert gp["actual"] == [{"date": "2026-08-03", "seconds": 1000}]
