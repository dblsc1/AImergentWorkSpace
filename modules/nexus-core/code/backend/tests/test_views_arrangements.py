"""``GET /api/core/views/arrangements``（契约 v2.39 三）：分组优先级、排序、stale、花费、activeNow、周回顾、上限。"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from bson import ObjectId

from _collab_common import API, ARR, get, mk, run
from _tier_common import day
from app.modules.views import arrangements
from app.repo import get_db

TODAY = date.fromisoformat(day(0))


def d(offset: int) -> str:
    return (TODAY + timedelta(days=offset)).isoformat()


def plan(a: int, b: int | None = None) -> dict:
    return {"start": d(a), "end": d(a if b is None else b)}


@pytest.fixture()
def w(client):
    z = mk(client, "zones", name="教育")
    p = mk(client, "projects", zoneId=z["id"], name="结构市场")
    return {"z": z, "p": p}


def task(client, w, name, **kw):
    return mk(client, "tasks", projectId=w["p"]["id"], name=name, **kw)


def proj(client, zone, name, status):
    p = mk(client, "projects", zoneId=zone["id"], name=name)
    assert client.patch(f"{API}/planner/projects/{p['id']}", json={"status": status}).status_code == 200
    return p


def groups(body):
    return {k: [x["name"] for x in body[k]] for k in ("scheduled", "actionable", "blocked", "overdue", "stale")}


def stat(task_id, project_id, date_str, seconds):
    get_db().proj_daily_stats.insert_one({"user": "u_local", "date": date_str, "projectId": project_id, "taskId": task_id,
                                          "seconds": seconds, "appliedKeys": []})


# ------------------------------------------------------------------ 分组与优先级


def test_empty(client):
    body = get(client, ARR)
    assert body == {"today": d(0), "range": "today", "from": d(0), "to": d(0), "scheduled": [], "actionable": [],
                    "blocked": [], "overdue": [], "stale": [], "truncated": False}


def test_each_group_and_priority(client, w):
    dep = task(client, w, "前置")                                         # 无 plan、无依赖 → actionable
    task(client, w, "逾期", plan=plan(-5, -1))                             # overdue
    task(client, w, "逾期又被卡", plan=plan(-5, -1), dependsOn=[dep["id"]])   # overdue 优先于 blocked
    task(client, w, "被卡又排期", plan=plan(0, 0), dependsOn=[dep["id"]])      # blocked 优先于 scheduled
    task(client, w, "今天", plan=plan(0, 2))                               # scheduled
    task(client, w, "今天结束", plan=plan(-3, 0))                          # 今天到期不算过期 → scheduled
    task(client, w, "下个月", plan=plan(40, 45))                           # 不在范围内 → actionable，plan 照给
    done = task(client, w, "已完成")
    assert client.patch(f"{API}/planner/tasks/{done['id']}", json={"done": True}).status_code == 200
    task(client, w, "临时", kind="ephemeral")
    assert client.post(f"{API}/planner/projects/{w['p']['id']}/unclassified").status_code in (200, 201)
    task(client, w, "后继", dependsOn=[done["id"]])                         # 前置已完成 → 不卡

    body = get(client, ARR)
    g = groups(body)
    assert sorted(g["overdue"]) == sorted(["逾期", "逾期又被卡"]) and g["blocked"] == ["被卡又排期"]
    assert sorted(g["scheduled"]) == ["今天", "今天结束"]
    assert sorted(g["actionable"]) == sorted(["前置", "下个月", "后继"])
    assert "已完成" not in sum(g.values(), []) and "临时" not in sum(g.values(), []) and "未分类" not in sum(g.values(), [])
    one = next(x for x in body["overdue"] if x["name"] == "逾期")
    assert one["planEnd"] == d(-1) and one["plan"] == plan(-5, -1) and one["address"] == "jy-jgsc-yq"
    assert set(one) == {"taskId", "projectId", "address", "name", "plan", "planEnd", "spentThisRange", "activeNow"}
    stuck = next(x for x in body["overdue"] if x["name"] == "逾期又被卡")
    assert stuck["blockedBy"] == ["jy-jgsc-qz"] and "blockedBy" not in one
    blk = body["blocked"][0]
    assert blk["blockedBy"] == ["jy-jgsc-qz"] and set(blk) == {"taskId", "projectId", "address", "name", "plan", "blockedBy", "spentThisRange", "activeNow"}
    act = next(x for x in body["actionable"] if x["name"] == "下个月")
    assert act["plan"] == plan(40, 45) and set(act) == {"taskId", "projectId", "address", "name", "plan", "spentThisRange", "activeNow"}
    assert set(body["scheduled"][0]) == {"taskId", "projectId", "address", "name", "plan", "spentThisRange", "activeNow"}


def test_plan_start_only_means_end_equals_start(client, w):
    t = task(client, w, "只有开始")
    get_db().tasks.update_one({"id": t["id"]}, {"$set": {"plan": {"start": d(-2)}}})
    body = get(client, ARR)
    assert [x["name"] for x in body["overdue"]] == ["只有开始"] and body["overdue"][0]["plan"] == plan(-2, -2)
    get_db().tasks.update_one({"id": t["id"]}, {"$set": {"plan": {"start": d(0)}}})
    assert groups(get(client, ARR))["scheduled"] == ["只有开始"]


def test_cycle_is_not_blocked(client, w):
    a, b = task(client, w, "环A"), task(client, w, "环B")
    get_db().tasks.update_one({"id": a["id"]}, {"$set": {"dependsOn": [b["id"]]}})
    get_db().tasks.update_one({"id": b["id"]}, {"$set": {"dependsOn": [a["id"]]}})
    g = groups(get(client, ARR))
    assert sorted(g["actionable"]) == ["环A", "环B"] and g["blocked"] == []


def test_archived_project_excluded_and_done_project_included(client, w):
    z = w["z"]
    arch = proj(client, z, "归档", "archived")
    fin = proj(client, z, "完结", "done")
    mk(client, "tasks", projectId=arch["id"], name="归档里的")
    mk(client, "tasks", projectId=fin["id"], name="完结里的")
    assert groups(get(client, ARR))["actionable"] == ["完结里的"]


def test_week_range_and_review(client, w):
    monday = TODAY - timedelta(days=TODAY.weekday())
    sunday = monday + timedelta(days=6)
    t = task(client, w, "周末")
    get_db().tasks.update_one({"id": t["id"]}, {"$set": {"plan": {"start": sunday.isoformat(), "end": sunday.isoformat()}}})
    task(client, w, "周一前", plan=plan(-30, -29))
    wk = get(client, ARR, range="week")
    assert (wk["range"], wk["from"], wk["to"]) == ("week", monday.isoformat(), sunday.isoformat())
    assert groups(wk)["scheduled"] == ["周末"] and groups(wk)["overdue"] == ["周一前"]   # overdue 两种 range 都按今天判
    assert wk["review"]["weekStart"] == monday.isoformat() and wk["review"]["weekEnd"] == sunday.isoformat()
    assert set(wk["review"]) == {"today", "weekStart", "weekEnd", "planVsActual", "overdueProjects", "staleTasks", "inboxPendingCount"}
    today = get(client, ARR)
    assert "review" not in today
    assert ("周末" in groups(today)["scheduled"]) == (TODAY == sunday)


def test_scope_zone_or_project(client, w):
    p2 = mk(client, "projects", zoneId=w["z"]["id"], name="另一个")
    z2 = mk(client, "zones", name="生活")
    p3 = mk(client, "projects", zoneId=z2["id"], name="家务")
    task(client, w, "甲")
    mk(client, "tasks", projectId=p2["id"], name="乙")
    mk(client, "tasks", projectId=p3["id"], name="丙")
    assert sorted(groups(get(client, ARR, scope=w["z"]["id"]))["actionable"]) == ["乙", "甲"]
    assert groups(get(client, ARR, scope=p2["id"]))["actionable"] == ["乙"]
    assert groups(get(client, ARR, scope=z2["id"]))["actionable"] == ["丙"]
    r = client.get(ARR, params={"scope": "nope"})
    assert r.status_code == 422 and "scope" in r.json()["detail"]


def test_422(client):
    r = client.get(ARR, params={"range": "month"})
    assert r.status_code == 422 and "range" in r.json()["detail"]


# ------------------------------------------------------------------ 排序


def test_sorting(client, w):
    task(client, w, "过期晚", plan=plan(-10, -2))
    task(client, w, "过期早", plan=plan(-10, -8))
    task(client, w, "排期晚", plan=plan(0, 5))
    task(client, w, "排期早", plan=plan(-2, 5))
    dep = task(client, w, "前置")
    task(client, w, "卡无计划", dependsOn=[dep["id"]])
    task(client, w, "卡晚", plan=plan(3, 9), dependsOn=[dep["id"]])
    task(client, w, "卡早", plan=plan(-4, 9), dependsOn=[dep["id"]])
    g = groups(get(client, ARR))
    assert g["overdue"] == ["过期早", "过期晚"]
    assert g["scheduled"] == ["排期早", "排期晚"]
    assert g["blocked"] == ["卡早", "卡晚", "卡无计划"]


def test_actionable_follows_tree_order(client, w):
    p2 = mk(client, "projects", zoneId=w["z"]["id"], name="第二个项目")
    task(client, w, "一-1")
    mk(client, "tasks", projectId=p2["id"], name="二-1")
    task(client, w, "一-2")
    mk(client, "tasks", projectId=p2["id"], name="二-2")
    tree = client.get(f"{API}/views/tree").json()
    want = [t["name"] for p in tree["projects"] for t in p["tasks"]]   # 与 views/tree 同一份列表的先后
    assert groups(get(client, ARR))["actionable"] == want and len(want) == 4


# ------------------------------------------------------------------ stale


def test_stale_rule_and_order(client, w):
    pid = w["p"]["id"]
    never_old = task(client, w, "从没有-老")
    get_db().tasks.update_one({"id": never_old["id"]}, {"$set": {"plan": None}})
    # 创建时间只有 ObjectId 带：把这条换成 20 天前建的
    doc = get_db().tasks.find_one({"id": never_old["id"]})
    get_db().tasks.delete_one({"id": never_old["id"]})
    doc["_id"] = ObjectId.from_datetime(datetime.now(timezone.utc) - timedelta(days=20))
    get_db().tasks.insert_one(doc)
    task(client, w, "从没有-新")                       # 刚建，不算
    exact = task(client, w, "恰好14天")
    stat(exact["id"], pid, d(-14), 60)
    almost = task(client, w, "13天")
    stat(almost["id"], pid, d(-13), 60)
    old = task(client, w, "40天前")
    stat(old["id"], pid, d(-40), 60)
    mixed = task(client, w, "近期又很久前")
    stat(mixed["id"], pid, d(-50), 60)
    stat(mixed["id"], pid, d(-1), 60)                 # 最后有时间的日子是昨天 → 不算
    body = get(client, ARR)
    assert [(x["name"], x["lastActive"]) for x in body["stale"]] == [("从没有-老", None), ("40天前", d(-40)), ("恰好14天", d(-14))]
    assert set(body["stale"][0]) == {"taskId", "projectId", "address", "name", "lastActive"}
    assert "从没有-老" in groups(body)["actionable"]       # stale 另算，可与上面重复


def test_stale_needs_active_project(client, w):
    fin = proj(client, w["z"], "完结", "done")
    t = mk(client, "tasks", projectId=fin["id"], name="旧事")
    stat(t["id"], fin["id"], d(-40), 60)
    assert groups(get(client, ARR))["stale"] == []


# ------------------------------------------------------------------ 花费 / activeNow


def test_spent_this_range(client, w):
    a, b = task(client, w, "有时间"), task(client, w, "没时间")
    stat(a["id"], w["p"]["id"], d(0), 3600)
    stat(a["id"], w["p"]["id"], d(-30), 999)                              # 范围外
    run(client, 1, "00:00:00", "00:10:00", task=a["id"], project=w["p"]["id"], start_date=d(0), end_date=d(0))
    run(client, 2, "00:00:00", "00:10:00", task=None, project=w["p"]["id"], start_date=d(0), end_date=d(0))  # 没挂任务 → 不算
    body = get(client, ARR)
    spent = {x["name"]: x["spentThisRange"] for x in body["actionable"]}
    assert spent == {"有时间": {"humanSeconds": 3600, "agentWorkingSeconds": 600}, "没时间": {"humanSeconds": 0, "agentWorkingSeconds": 0}}
    # week 把本周内的也算上
    if TODAY.weekday():  # 周一时本周一就是今天，同一 (user,date,project,task) 已有一行，再插会撞唯一索引
        stat(a["id"], w["p"]["id"], (TODAY - timedelta(days=TODAY.weekday())).isoformat(), 100)
    wk = get(client, ARR, range="week")
    expect = 3600 + (100 if TODAY.weekday() else 0)
    assert {x["name"]: x["spentThisRange"]["humanSeconds"] for x in wk["actionable"]}["有时间"] == expect


def test_active_now_human_timer_and_agent_run(client, w):
    a, b, c = task(client, w, "手动"), task(client, w, "代理"), task(client, w, "闲着")
    assert client.post(f"{API}/timer/start", json={"taskId": a["id"]}).status_code == 200
    r1 = client.post(f"{API}/agents/start", json={"agent": "cc", "tool": "claude-code", "taskId": b["id"]}).json()["runId"]
    r2 = client.post(f"{API}/agents/start", json={"agent": "cc2", "tool": "claude-code", "taskId": b["id"]}).json()["runId"]
    # 人也在这条上（手动计时）+ 代理在跑
    body = get(client, ARR)
    now = {x["name"]: x["activeNow"] for x in body["actionable"]}
    assert now == {"手动": ["human"], "代理": sorted([r1, r2]), "闲着": []}
    client.post(f"{API}/agents/{r1}/stop", json={"outcome": "done"})
    assert {x["name"]: x["activeNow"] for x in get(client, ARR)["actionable"]}["代理"] == [r2]


def test_active_now_auto_tracking(client, w, monkeypatch):
    a = task(client, w, "自动")
    from app.modules.activity import service as activity_service  # noqa: PLC0415

    monkeypatch.setattr(activity_service, "auto_state", lambda user, running, now=None: {"auto": {"taskId": a["id"]}})
    assert get(client, ARR)["actionable"][0]["activeNow"] == ["human"]


# ------------------------------------------------------------------ 上限


def test_cap_sets_truncated(client, w, monkeypatch):
    task(client, w, "甲")
    task(client, w, "乙")
    monkeypatch.setattr(arrangements, "CAP", 1)
    body = get(client, ARR)
    assert len(body["actionable"]) == 1 and body["truncated"] is True
