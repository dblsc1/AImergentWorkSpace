"""键盘鼠标活动（契约 v2.38）：只有时间格；合并去重；校验整请求 422；留存。"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from _tier_common import BEARER, DEV, INP, TZ, db


def _bin(offset_s: int = -3600) -> int:
    return (int(time.time()) + offset_s) // 10 * 10


def test_auth_both_ways(client):
    assert client.post(INP, json={"deviceId": DEV, "bins": []}).status_code == 403
    assert client.get(INP, params={"date": "2026-10-24"}, headers=BEARER).status_code == 403


def test_merge_dedupe_across_devices_and_days(client):
    b = _bin()
    d = datetime.fromtimestamp(b, TZ).date().isoformat()
    assert client.post(INP, json={"deviceId": DEV, "bins": [b + 20, b, b, b + 10]}, headers=BEARER).status_code == 200
    client.post(INP, json={"deviceId": "dev_other", "bins": [b + 10, b + 30]}, headers=BEARER)
    client.post(INP, json={"deviceId": DEV, "bins": [b]}, headers=BEARER)  # 重复上传不重复
    out = client.get(INP, params={"date": d}).json()
    assert out["binSeconds"] == 10 and out["date"] == d
    day_bins = [x for x in out["bins"] if b <= x <= b + 30]
    assert day_bins == [b, b + 10, b + 20, b + 30]
    assert db()["activity_input"].count_documents({"deviceId": DEV}) >= 1


def test_validation_is_all_or_nothing(client):
    b = _bin()
    for bad in ([b + 1], [-10], [_bin(-8 * 86400)], [_bin(3600)], ["x"], [True]):
        r = client.post(INP, json={"deviceId": DEV, "bins": [b, *bad]}, headers=BEARER)
        assert r.status_code == 422, bad
    assert db()["activity_input"].count_documents({}) == 0
    r = client.post(INP, json={"deviceId": DEV, "bins": [b] * 8641}, headers=BEARER)
    assert r.status_code == 422


def test_retention(client):
    db()["activity_input"].insert_one({"user": "u_local", "deviceId": DEV, "date": "2000-01-01", "bins": [10]})
    client.post(INP, json={"deviceId": DEV, "bins": [_bin()]}, headers=BEARER)
    assert client.get(INP, params={"date": "2000-01-01"}).json()["bins"] == []
