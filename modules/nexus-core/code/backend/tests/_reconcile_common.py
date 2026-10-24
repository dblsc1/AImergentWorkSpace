"""对账（v2.38 三级分类「四」）测试的共用小工具：打开三级分类、手动驱动一轮、读台账。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from _tier_common import BLK, SET, at, db
from app.modules.activity import reconcile

OFF = {"enabled": False, "windowsClassify": False, "honeycombRecords": False}


def tiers(client, on=True, since_days=30):
    """开 / 关三级分类；开时把 ``tiersSince`` 拨到 ``since_days`` 天前（测试数据在过去）。"""
    v = client.get(SET).json()["version"]
    r = client.put(SET, json={"tiers": on, "agentDirect": OFF}, headers={"If-Match": f'"{v}"'})
    assert r.status_code == 200, r.text
    if on:
        db().activity_settings.update_one({"user": "u_local"}, {"$set": {"tiersSince": datetime.now(timezone.utc) - timedelta(days=since_days)}})
        reconcile.run_round("u_local")  # 拨完 tiersSince 对一轮（PUT 触发的那轮用的是"此刻"）


def rnd(**kw) -> int:
    """手动驱动一轮（缺省全量；要测只重访脏块传 ``full=False``）。"""
    return reconcile.run_round("u_local", **kw)


def put_doc(date, key, tid, seq=1, **extra):
    """直接写一个已分好的块文档（不触发对账）。"""
    db().activity_blocks.insert_one({"user": "u_local", "date": date, "blockKey": key, "status": "assigned", "via": "human", "ruleId": None,
                                     "seq": seq, "gen": 1, "runs": [], "parts": [{"fromTs": None, "toTs": None, "taskId": tid, "projectId": None}], **extra})


def ev(type_="session.completed", source="activity-block"):
    return sorted(db().events.find({"type": type_, "source": source}, {"_id": 0}), key=lambda d: d["recordedAt"])


def live_sessions():
    """没被作废的 activity-block 的 session.completed。"""
    voided = {e["data"]["voidedEventId"] for e in ev("session.voided")}
    return [e for e in ev() if e["id"] not in voided]


def assign(client, date, key, tid=None, pid=None, seq=0, remember=False):
    r = client.post(f"{BLK}/assign", json={"date": date, "blockKey": key, "taskId": tid, "projectId": pid, "remember": remember, "ifSeq": seq})
    assert r.status_code == 200, r.text
    return r.json()


def doc(date, key):
    return db().activity_blocks.find_one({"date": date, "blockKey": key}, {"_id": 0})


def secs(events) -> int:
    return sum(e["data"]["durationSeconds"] for e in events)
