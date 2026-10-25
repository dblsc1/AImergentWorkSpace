"""v2.40 / v0.4.2：``activity/gaps`` 与 ``activity/blocks/history``。"""

from __future__ import annotations

import pytest
from _tier_common import API, BLK, at, day, rec, upload

from app.modules.detector import repo as detector_repo

G = f"{API}/activity/gaps"
H = f"{BLK}/history"
D = day(-1)  # 过去的一天：区间固定到当天结束，不随当前时刻变
K = "chrome · 周报"


def test_gaps_hole_over_10min_offline_annotation_and_short_hole_ignored(client):
    upload(client, [rec(D, "08:00:00", "09:00:00"), rec(D, "09:05:00", "09:20:00"),  # 5 分钟的缝 → 忽略
                    rec(D, "09:40:00", "12:00:00"), rec(D, "12:30:00", "23:50:00")])  # 20 分钟、30 分钟的洞；尾部正好 10 分钟 → 不算
    detector_repo.add_events("u_local", "dev_x", [{"kind": "detector_offline", "at": at(D, "09:15:00"), "endAt": at(D, "09:45:00")},
                                                 {"kind": "aw_paused", "at": at(D, "12:10:00"), "endAt": at(D, "12:20:00")}])
    r = client.get(G, params={"date": D})
    assert r.status_code == 200, r.text
    gaps = r.json()["gaps"]
    assert [(g["from"][11:16], g["to"][11:16], g["seconds"], g["detectorOffline"]) for g in gaps] == [
        ("09:20", "09:40", 1200, True), ("12:00", "12:30", 1800, False)]
    assert gaps[0]["from"].endswith(at(D, "09:20:00").isoformat()[-6:])  # 带偏移


def test_gaps_leading_hole_and_empty_day_and_history_segments(client):
    assert client.get(G, params={"date": D}).json()["gaps"][0]["seconds"] == 16 * 3600  # 没记录：08:00 到当天结束
    upload(client, [rec(D, "08:30:00", "23:59:00")])
    assert [(g["from"][11:16], g["seconds"]) for g in client.get(G, params={"date": D}).json()["gaps"]] == [("08:00", 1800)]
    h = client.get(f"{API}/views/history", params={"from": D, "to": D, "who": "human", "detail": "segments"}).json()
    assert h["human"]["gaps"][0]["seconds"] == 1800
    assert "gaps" not in client.get(f"{API}/views/history", params={"from": D, "to": D, "who": "human"}).json()["human"]


@pytest.fixture()
def tasks(seeded):
    t = seeded["tasks"]
    return t["示例任务三"]["id"], next(v["id"] for k, v in t.items() if v["id"] != t["示例任务三"]["id"])


def _assign(client, d, key, tid):
    r = client.post(f"{BLK}/assign", json={"date": d, "blockKey": key, "taskId": tid, "ifSeq": 0})
    assert r.status_code == 200, r.text


def test_match_history_aggregates_by_destination(client, tasks):
    a, b = tasks
    for d in (day(-1), day(-2), day(-3)):
        upload(client, [rec(d, "09:00:00", "09:10:00", title="周报")])
    upload(client, [rec(day(-40), "09:00:00", "09:10:00", title="周报")])  # 超出 days
    _assign(client, day(-1), K, b)
    _assign(client, day(-2), K, a)
    _assign(client, day(-3), K, a)
    body = client.get(H, params={"block": K, "days": 30}).json()
    assert body["query"] == {"block": K} and body["days"] == 30
    dest = body["destinations"]
    assert [(x["taskId"], x["times"], x["seconds"], x["via"]) for x in dest] == [(a, 2, 1200, "human"), (b, 1, 600, "human")]
    assert dest[0]["address"] and dest[0]["name"] and dest[0]["projectId"] and dest[1]["lastAt"].startswith(day(-1))
    # days 截断：只剩今天和前两天
    assert [(x["taskId"], x["times"]) for x in client.get(H, params={"block": K, "days": 3}).json()["destinations"]] == [(b, 1), (a, 1)]


def test_match_history_by_app_title_pending_empty_and_errors(client, tasks):
    a, _ = tasks
    upload(client, [rec(D, "09:00:00", "09:10:00", title="周报"), rec(D, "10:00:00", "10:10:00", title="别的")])
    assert client.get(H, params={"app": "chrome", "title": "周报"}).json()["destinations"] == []  # 还没分类：不猜
    _assign(client, D, K, a)
    r = client.get(H, params={"app": "chrome", "title": "周报"}).json()
    assert r["query"] == {"app": "chrome", "title": "周报"} and [x["taskId"] for x in r["destinations"]] == [a]
    assert client.get(H, params={"app": "chrome", "title": "不存在"}).json()["destinations"] == []
    for bad in ({}, {"app": "chrome"}, {"block": K, "app": "chrome", "title": "x"}):
        assert client.get(H, params=bad).status_code == 422, bad
    assert client.get(H, params={"block": K, "days": 91}).status_code == 422
    assert client.get(H, params={"block": K, "days": 0}).status_code == 422
