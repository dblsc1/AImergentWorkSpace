"""onboarding.v1「五」候选 / claim 测试的共用小工具。"""

from __future__ import annotations

from _reconcile_common import tiers
from _tier_common import API, day, rec, upload

CAND = f"{API}/activity/unclassified/candidates"
CLAIM = f"{API}/activity/blocks/claim"
UNDO = f"{API}/activity/blocks/claim/undo"
PL = f"{API}/planner"


def garden(client, task="写提示词", project="garden", zone="学习") -> dict:
    z = client.post(f"{PL}/zones", json={"name": zone}).json()["id"]
    p = client.post(f"{PL}/projects", json={"zoneId": z, "name": project}).json()["id"]
    t = client.post(f"{PL}/tasks", json={"projectId": p, "name": task}).json()["id"]
    return {"zone": z, "project": p, "task": t}


_n = [0]


def seed_days(client, title: str, days, app="code") -> str:
    """每天一段 4800 秒的记录（每次调用错开时段，免得被当成重复上传），返回块名。"""
    m = (_n[0] % 16) * 90
    _n[0] += 1
    start, end = f"{m // 60:02d}:{m % 60:02d}:00", f"{(m + 80) // 60:02d}:{(m + 80) % 60:02d}:00"
    upload(client, [rec(day(-d), start, end, app=app, title=title) for d in days])
    return f"{app} · {title}"


def on(client, since_days=30):
    tiers(client, True, since_days)


def claim(client, keys, expect=200, **body):
    keys = [keys] if isinstance(keys, str) else keys
    r = client.post(CLAIM, json={"blockKeys": keys, **body})
    assert r.status_code == expect, r.text
    return r.json()
