"""三级分类（v2.38）测试的共用小工具。"""

from __future__ import annotations

from datetime import datetime, timedelta

from app import config

API = "/api/core"
REC = f"{API}/activity/records"
BLK = f"{API}/activity/blocks"
BR = f"{API}/activity/block-rules"
SET = f"{API}/activity/settings"
INP = f"{API}/activity/input"
RULES = f"{API}/detector/rules"
BEARER = {"Authorization": "Bearer tok_device"}
DEV = "dev_3f9a1c2b7d4e5a60"
TZ = config.settings.tz


def day(offset: int = 0) -> str:
    """本地日（今天 + offset 天），用 NEXUS_TZ 算，和服务端同口径。"""
    return (datetime.now(TZ) + timedelta(days=offset)).date().isoformat()


def at(date: str, hms: str, ms: str = "") -> datetime:
    h, m, s = (int(x) for x in hms.split(":"))
    y, mo, d = (int(x) for x in date.split("-"))
    return datetime(y, mo, d, h, m, s, tzinfo=TZ)


def rec(date, start, end, app="chrome", title="doc", block=None, pseudo=False) -> dict:
    return {"startAt": at(date, start).isoformat(), "endAt": at(date, end).isoformat(), "app": app, "title": title,
            "titleIsPseudonym": pseudo, "block": block}


def upload(client, records, device=DEV, expect=200, headers=None):
    r = client.post(REC, json={"deviceId": device, "records": records}, headers=headers or BEARER)
    assert r.status_code == expect, r.text
    return r.json()


def blocks(client, date, status="all", **q):
    r = client.get(BLK, params={"date": date, "status": status, **q})
    assert r.status_code == 200, r.text
    return r.json()["items"]


def one(client, date, key):
    return next(i for i in blocks(client, date) if i["blockKey"] == key)


def db():
    from app.repo import get_db  # noqa: PLC0415

    return get_db()
