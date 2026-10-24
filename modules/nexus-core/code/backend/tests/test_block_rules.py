"""第二级规则 CRUD（契约 v2.38 三）：同块名改目标、broken、只许人、读不许设备令牌。"""

from __future__ import annotations

import pytest
from _tier_common import BEARER, BR, db


@pytest.fixture()
def tasks(seeded):
    t = seeded["tasks"]
    return t["示例任务三"]["id"], next(v["id"] for k, v in t.items() if v["id"] != t["示例任务三"]["id"])


def test_create_replace_same_block_and_list(client, tasks):
    a, b = tasks
    r = client.post(BR, json={"blockKey": "块", "taskId": a})
    assert r.status_code == 201, r.text
    first = r.json()
    assert first["via"] == "manual" and first["author"] == "human" and first["taskId"] == a and first["projectId"] is None
    r = client.post(BR, json={"blockKey": "块", "taskId": b})
    assert r.status_code == 200
    second = r.json()
    assert second["id"] == first["id"] and second["taskId"] == b and second["createdAt"] == first["createdAt"]
    assert second["updatedAt"] >= first["updatedAt"]
    assert db()["activity_block_rules"].count_documents({}) == 1
    assert client.get(BR).json()["items"] == [second]
    assert client.get(BR, headers=BEARER).status_code == 403


def test_validation_and_404_and_human_only(client, tasks):
    a, _ = tasks
    assert client.post(BR, json={"blockKey": "块"}).status_code == 422
    assert client.post(BR, json={"blockKey": "块", "taskId": a, "projectId": "p_inbox"}).status_code == 422
    assert client.post(BR, json={"blockKey": "块", "taskId": "t_nope"}).status_code == 404
    assert client.post(BR, json={"blockKey": "块", "projectId": "p_inbox"}).status_code == 201
    assert client.post(BR, json={"blockKey": "块2", "taskId": a}, headers=BEARER).status_code == 403
    rid = client.get(BR).json()["items"][0]["id"]
    assert client.delete(f"{BR}/{rid}", headers=BEARER).status_code == 403
    assert client.delete(f"{BR}/{rid}").status_code == 204
    assert client.delete(f"{BR}/{rid}").status_code == 404
    assert client.get(BR).json()["items"] == []


def test_broken_when_target_deleted_and_block_falls_back_to_pending(client, tasks):
    from _tier_common import day, one, rec, upload  # noqa: PLC0415

    a, _ = tasks
    d = day(-1)
    upload(client, [rec(d, "09:00:00", "09:01:00", title="x", block="B")])
    client.post(BR, json={"blockKey": "B", "taskId": a})
    assert one(client, d, "B")["status"] == "assigned"
    db()["tasks"].delete_one({"id": a})
    item = client.get(BR).json()["items"][0]
    assert item["broken"] is True
    assert one(client, d, "B")["status"] == "pending"
