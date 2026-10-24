"""views/history 与 views/arrangements 测试的共用小工具。"""

from __future__ import annotations

from datetime import datetime, timedelta

from _tier_common import TZ, at, day

API = "/api/core"
PLANNER = f"{API}/planner"
HIST = f"{API}/views/history"
ARR = f"{API}/views/arrangements"
D = day(-1)  # 测试数据都放在昨天：补登不能延伸到未来，也不受「现在几点」影响


def t(hms: str, date: str | None = None) -> datetime:
    return at(date or D, hms)


def iso(hms: str, date: str | None = None) -> str:
    return t(hms, date).isoformat()


def mk(client, path, **body):
    r = client.post(f"{PLANNER}/{path}", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def world(client):
    """一个分区、两个项目、三个任务：A、B 在项目一，C 在项目二。"""
    z = mk(client, "zones", name="教育")
    p1 = mk(client, "projects", zoneId=z["id"], name="结构市场")
    p2 = mk(client, "projects", zoneId=z["id"], name="另一个")
    a = mk(client, "tasks", projectId=p1["id"], name="课程代码")
    b = mk(client, "tasks", projectId=p1["id"], name="写报告")
    c = mk(client, "tasks", projectId=p2["id"], name="杂事")
    return {"z": z, "p1": p1, "p2": p2, "a": a, "b": b, "c": c}


def backfill(client, task_id, start_hms, seconds, date=None):
    r = client.post(f"{API}/timer/backfill", json={"taskId": task_id, "startAt": iso(start_hms, date), "durationSeconds": seconds})
    assert r.status_code == 200, r.text
    return r.json()


def run(client, n, start_hms, end_hms, phases=(), interactions=(), agent="claude-code", label="L1", task=None,
        project="p1", start_date=None, end_date=None):
    """直接投一条 agent.run.completed（外部 source）。phases = [(hms, phase)]；interactions = [("reply", hms) | ("attend", hms, until_hms)]。"""
    start, end = t(start_hms, start_date), t(end_hms, end_date)
    subject = {"zone": "z1", "project": project, **({"task": task} if task else {})}
    ia = []
    for item in interactions:
        ia.append({"kind": "reply", "at": iso(item[1])} if item[0] == "reply"
                  else {"kind": "attend", "at": iso(item[1]), "until": iso(item[2])})
    env = {"spec": "yq-event/v1", "id": f"evt_cv{n}", "dedupeKey": f"cv-{n}", "type": "agent.run.completed",
           "source": "test-hook", "user": "u_local", "time": end.isoformat(), "subject": subject,
           "data": {"agent": agent, "tool": "x", "label": label, "startAt": start.isoformat(),
                    "durationSeconds": int((end - start).total_seconds()), "outcome": "done",
                    "phases": [{"at": iso(h), "phase": p} for h, p in phases], "interactions": ia}}
    r = client.post(f"{API}/events", json=env)
    assert r.json()["accepted"] == 1, r.text
    return f"cv-{n}"


def get(client, url, expect=200, **params):
    r = client.get(url, params=params)
    assert r.status_code == expect, r.text
    return r.json()
