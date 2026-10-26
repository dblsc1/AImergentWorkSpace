"""v0.5 置信度（契约 v2.42）：提议带 confidencePercent、账号设置带 highConfidenceThresholdPercent。"""

from __future__ import annotations

import pytest
from _reconcile_common import OFF, tiers
from _tier_common import SET, day, rec, upload
from test_session_reassign import _session, _world

P = "/api/core/activity/proposals"
LOG = "/api/core/activity/direct-log"
D = day(-1)
K = "chrome · 周报"
WRITE = {"X-Nexus-Scope": "write"}
BAD = [101, -1, 5.5, "80", True]


@pytest.fixture()
def world(client, seeded):
    """每种提议一份合法请求体：[(路径, 体, 列表键)]。"""
    tiers(client)
    upload(client, [rec(D, "09:00:00", "09:10:00", title="周报")])
    zone = next(iter(seeded["zones"].values()))["id"]
    task = seeded["tasks"]["示例任务三"]["id"]
    w = _world(client)
    eid = _session(client, w["bucket"])
    return [
        ("projects", {"zoneId": zone, "name": "置信项目"}, "projects"),
        ("tasks", {"projectId": w["p"], "name": "置信任务"}, "tasks"),
        ("changes", {"target": "task", "targetId": task, "op": "rename", "name": "改个名"}, "changes"),
        ("window-rules", {"op": "add", "rule": {"app": "chrome", "block": "置信块"}}, "windowRules"),
        ("block-rules", {"op": "add", "blockKey": K, "taskId": task}, "blockRules"),
        ("block-actions", {"action": "assign", "date": D, "blockKey": K, "taskId": task}, "blockActions"),
        ("sessions", {"eventId": eid, "op": "reassign", "taskId": w["a"]}, "sessionChanges"),
    ]


def test_422_on_bad_value_every_kind(client, world):
    for path, body, _ in world:
        for bad in BAD:
            r = client.post(f"{P}/{path}", json={**body, "confidencePercent": bad})
            assert r.status_code == 422, (path, bad, r.text)


def test_round_trip_every_kind_and_null_when_absent(client, world):
    for path, body, _ in world:
        r = client.post(f"{P}/{path}", json={**body, "confidencePercent": 73})
        assert r.status_code == 200, (path, r.text)
        assert r.json()["confidencePercent"] == 73, path
    listed = client.get(P).json()
    for _, _, key in world:
        assert [i["confidencePercent"] for i in listed[key]] == [73], key
    for (path, body, _), val in zip(world[:2], (0, 100)):  # 边界合法
        r = client.post(f"{P}/{path}", json={**body, "name": body["name"] + "边界", "confidencePercent": val})
        assert r.json()["confidencePercent"] == val
    r = client.post(f"{P}/tasks", json={**world[1][1], "name": "没给"})
    assert r.status_code == 200 and r.json()["confidencePercent"] is None


def test_direct_apply_records_confidence_in_log(client, world):
    v = client.get(SET).json()["version"]
    ok = client.put(SET, json={"tiers": True, "agentDirect": {"enabled": True, "windowsClassify": True, "honeycombRecords": True}},
                    headers={"If-Match": f'"{v}"'})
    assert ok.status_code == 200
    task = next(b for p, b, _ in world if p == "block-actions")["taskId"]
    body = {"action": "assign", "date": D, "blockKey": K, "taskId": task, "directApply": True}
    r = client.post(f"{P}/block-actions", headers=WRITE, json={**body, "confidencePercent": 91})
    assert r.status_code == 200 and r.json()["applied"] is True, r.text
    assert client.get(LOG).json()["items"][0]["payload"]["confidencePercent"] == 91
    assert client.post(f"{P}/block-actions", headers=WRITE, json={**body, "confidencePercent": 101}).status_code == 422


# ── 账号设置 ──


def _put(client, body, version):
    return client.put(SET, json=body, headers={"If-Match": f'"{version}"'})


def test_threshold_default_keep_on_omit_and_validation(client):
    assert client.get(SET).json()["highConfidenceThresholdPercent"] == 80
    base = {"tiers": False, "agentDirect": OFF}
    r = _put(client, base, 0)  # 省了、没有原值 = 缺省 80
    assert r.status_code == 200 and r.json()["highConfidenceThresholdPercent"] == 80
    r = _put(client, {**base, "highConfidenceThresholdPercent": 65}, 1)
    assert r.json()["highConfidenceThresholdPercent"] == 65 and r.json()["version"] == 2
    r = _put(client, {**base, "tiers": True}, 2)  # 省了：保留 65，不回缺省
    assert r.json()["highConfidenceThresholdPercent"] == 65
    assert client.get(SET).json()["highConfidenceThresholdPercent"] == 65
    for ok in (0, 100):
        v = client.get(SET).json()["version"]
        assert _put(client, {**base, "highConfidenceThresholdPercent": ok}, v).json()["highConfidenceThresholdPercent"] == ok
    v = client.get(SET).json()["version"]
    for bad in BAD:
        assert _put(client, {**base, "highConfidenceThresholdPercent": bad}, v).status_code == 422, bad
    assert client.get(SET).json()["version"] == v
