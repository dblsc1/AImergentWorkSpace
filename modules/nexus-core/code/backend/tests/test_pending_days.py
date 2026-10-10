"""待确认建议按天汇总（v2.26，``GET /activity/suggestions/pending-days``）。

直接往 ``activity_suggestions`` 写文档（固定的过去日期，不依赖真实时刻；该端点不做过期清理）。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app import config
from app.config import load_settings

URL = "/api/core/activity/suggestions/pending-days"
A = {"X-Nexus-Tenant": "ch_aaaa"}
B = {"X-Nexus-Tenant": "ch_bbbb"}
SHANGHAI = load_settings({"NEXUS_MONGO_URI": "mongodb://x", "NEXUS_DB_NAME": "nexus_core_test",
                          "NEXUS_TZ": "Asia/Shanghai"})
CST = timezone(timedelta(hours=8))


@pytest.fixture(autouse=True)
def _tz(monkeypatch):
    monkeypatch.setattr(config, "settings", SHANGHAI)


def _put(n, start, seconds, *, status="pending", user="ch_aaaa"):
    from app.repo import get_db  # noqa: PLC0415

    get_db()["activity_suggestions"].insert_one({
        "user": user, "id": f"sug_{n}", "dedupeKey": f"k{n}", "status": status, "startTs": start,
        "durationSeconds": seconds, "app": "secret-app", "title": "secret-title",
    })


def _get(client, frm, to, headers=A):
    r = client.get(URL, params={"from": frm, "to": to}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def test_per_day_sums_and_only_pending(client):
    _put(1, datetime(2026, 10, 9, 10, 0, tzinfo=CST), 600)
    _put(2, datetime(2026, 10, 9, 11, 0, tzinfo=CST), 300)
    _put(3, datetime(2026, 10, 10, 9, 0, tzinfo=CST), 120)
    _put(4, datetime(2026, 10, 9, 12, 0, tzinfo=CST), 999, status="confirmed")
    _put(5, datetime(2026, 10, 9, 13, 0, tzinfo=CST), 999, status="dismissed")
    out = _get(client, "2026-10-09", "2026-10-10")
    assert out == {"totalSeconds": 1020, "count": 3, "days": [
        {"date": "2026-10-09", "seconds": 900, "count": 2}, {"date": "2026-10-10", "seconds": 120, "count": 1}]}
    assert "secret" not in str(out)


def test_day_boundary_is_local_not_utc(client):
    _put(1, datetime(2026, 10, 9, 23, 59, tzinfo=CST), 60)   # UTC 15:59，本地仍是 9 日
    _put(2, datetime(2026, 10, 10, 0, 1, tzinfo=CST), 30)    # UTC 16:01 的前一天，本地已是 10 日
    _put(3, datetime(2026, 10, 10, 0, 0, tzinfo=CST), 5)     # 恰在零点：属于 10 日
    assert _get(client, "2026-10-09", "2026-10-10")["days"] == [
        {"date": "2026-10-09", "seconds": 60, "count": 1}, {"date": "2026-10-10", "seconds": 35, "count": 2}]
    assert _get(client, "2026-10-10", "2026-10-10")["totalSeconds"] == 35


def test_range_filter_inclusive(client):
    for i, d in enumerate((8, 9, 10, 11)):
        _put(i, datetime(2026, 10, d, 12, 0, tzinfo=CST), 10)
    assert [x["date"] for x in _get(client, "2026-10-09", "2026-10-10")["days"]] == ["2026-10-09", "2026-10-10"]


def test_empty_is_zeros(client):
    assert _get(client, "2026-10-09", "2026-10-10") == {"totalSeconds": 0, "count": 0, "days": []}


def test_tenant_isolation(client):
    _put(1, datetime(2026, 10, 9, 10, 0, tzinfo=CST), 600, user="ch_bbbb")
    assert _get(client, "2026-10-09", "2026-10-09")["count"] == 0
    assert _get(client, "2026-10-09", "2026-10-09", B)["totalSeconds"] == 600


@pytest.mark.parametrize("frm,to", [("2026-10-10", "2026-10-09"), ("2026-01-01", "2026-06-01"), ("x", "2026-10-09")])
def test_bad_range_422(client, frm, to):
    assert client.get(URL, params={"from": frm, "to": to}, headers=A).status_code == 422


def test_booked_views_unchanged(client, seeded):
    """加了待确认之后甘特（已记账）一个字节不变。"""
    before = client.get("/api/core/views/gantt", headers=A).json()
    _put(1, datetime(2026, 10, 9, 10, 0, tzinfo=CST), 600)
    assert client.get("/api/core/views/gantt", headers=A).json() == before
