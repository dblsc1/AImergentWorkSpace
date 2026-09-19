"""档案读端的契约级单测（contract.md v0.6「档案读端」；规格验收 R5/R7/R9/R10）。

覆盖：只读查询参数各自与组合、按 time 倒序、空结果 200、type 过滤、limit/offset
分页、不 join 名字（items 就是信封原样）。不测写路径、不碰 ingest 本身的既有语义
——那些已经在 test_events_contract.py 里验过。
"""

from __future__ import annotations

import uuid

from app.modules.events import service as events_service

INGEST = "/api/core/events"
ARCHIVE = "/api/core/events"


def _post_event(client, *, time: str, type_: str = "session.completed", **overrides) -> dict:
    doc = {
        "spec": "yq-event/v1",
        "id": f"evt_{uuid.uuid4().hex[:8]}",
        "dedupeKey": f"archive-test:{uuid.uuid4().hex[:8]}",
        "type": type_,
        "user": "u_local",
        "source": "archive-test",
        "time": time,
        "subject": {"zone": "z_1", "project": "p_1", "task": "t_1"},
        "data": {"durationSeconds": 900},
    }
    doc.update(overrides)
    resp = client.post(INGEST, json=doc)
    assert resp.status_code == 200 and resp.json()["accepted"] == 1, resp.text
    return doc


# ------------------------------------------------------------------------- R9


def test_r9_empty_result_is_200_not_404(client):
    resp = client.get(ARCHIVE)
    assert resp.status_code == 200
    assert resp.json() == {"total": 0, "items": []}


# ------------------------------------------------------------------------- R7


def test_r7_sorted_by_time_descending(client):
    _post_event(client, time="2026-07-01T08:00:00+08:00", dedupeKey="r7-a")
    _post_event(client, time="2026-07-03T08:00:00+08:00", dedupeKey="r7-b")
    _post_event(client, time="2026-07-02T08:00:00+08:00", dedupeKey="r7-c")

    resp = client.get(ARCHIVE)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 3
    times = [item["time"] for item in body["items"]]
    assert times == [
        "2026-07-03T08:00:00+08:00",
        "2026-07-02T08:00:00+08:00",
        "2026-07-01T08:00:00+08:00",
    ], "R7：必须按 time 倒序，最近的在前"


def test_r7_sort_is_correct_across_differing_timezone_offsets(client):
    """time 字段带任意时区偏移，字典序比较会算错——必须按真实时刻排序。

    2026-07-01T23:00:00+08:00 换成 UTC 是 15:00；
    2026-07-01T10:00:00+00:00 换成 UTC 是 10:00——后者更早，
    但字典序会把 "10:00:00+00:00" 排在 "23:00:00+08:00" 前面（因为 '1'<'2'），
    真实时间顺序却是相反：+08:00 那条更早。用这组数据验证排序没有退化成字典序。
    """
    _post_event(client, time="2026-07-01T23:00:00+08:00", dedupeKey="tz-early")  # UTC 15:00
    _post_event(client, time="2026-07-02T10:00:00+00:00", dedupeKey="tz-late")  # UTC 10:00 次日

    body = client.get(ARCHIVE).json()
    assert [i["dedupeKey"] for i in body["items"]] == ["tz-late", "tz-early"]


# ------------------------------------------------------------------------- type 过滤


def test_type_filter(client):
    _post_event(client, time="2026-07-01T08:00:00+08:00", type_="session.completed", dedupeKey="type-a")
    _post_event(client, time="2026-07-01T09:00:00+08:00", type_="growth.granted", dedupeKey="type-b")

    body = client.get(ARCHIVE, params={"type": "growth.granted"}).json()
    assert body["total"] == 1
    assert body["items"][0]["type"] == "growth.granted"
    assert body["items"][0]["dedupeKey"] == "type-b"


def test_type_filter_no_match_is_empty_not_error(client):
    _post_event(client, time="2026-07-01T08:00:00+08:00", dedupeKey="type-only-completed")
    body = client.get(ARCHIVE, params={"type": "nonexistent.type"}).json()
    assert body == {"total": 0, "items": []}


# ------------------------------------------------------------------------- from/to 过滤


def test_from_to_range_filter(client):
    _post_event(client, time="2026-07-01T08:00:00+08:00", dedupeKey="range-before")
    _post_event(client, time="2026-07-15T08:00:00+08:00", dedupeKey="range-in")
    _post_event(client, time="2026-07-30T08:00:00+08:00", dedupeKey="range-after")

    body = client.get(
        ARCHIVE, params={"from": "2026-07-10T00:00:00+08:00", "to": "2026-07-20T00:00:00+08:00"}
    ).json()
    assert body["total"] == 1
    assert body["items"][0]["dedupeKey"] == "range-in"


def test_from_only_is_open_ended_upper_bound(client):
    _post_event(client, time="2026-07-01T08:00:00+08:00", dedupeKey="open-before")
    _post_event(client, time="2026-07-20T08:00:00+08:00", dedupeKey="open-after")

    body = client.get(ARCHIVE, params={"from": "2026-07-10T00:00:00+08:00"}).json()
    assert {i["dedupeKey"] for i in body["items"]} == {"open-after"}


def test_bare_date_bound_is_accepted(client):
    """`from`/`to` 可以是裸日期（不带时间/时区），契约允许「ISO8601 日期或日期时间」。"""
    _post_event(client, time="2026-07-01T08:00:00+08:00", dedupeKey="bare-before")
    _post_event(client, time="2026-07-20T08:00:00+08:00", dedupeKey="bare-after")

    resp = client.get(ARCHIVE, params={"from": "2026-07-10"})
    assert resp.status_code == 200
    assert {i["dedupeKey"] for i in resp.json()["items"]} == {"bare-after"}


def test_invalid_from_is_400_names_the_bad_value(client):
    resp = client.get(ARCHIVE, params={"from": "not-a-date"})
    assert resp.status_code == 400
    assert "not-a-date" in resp.json()["detail"]
    assert "from" in resp.json()["detail"]


# ------------------------------------------------------------------------- limit/offset 分页


def test_limit_and_offset_paginate_without_changing_total(client):
    for i in range(5):
        _post_event(client, time=f"2026-07-{10 + i:02d}T08:00:00+08:00", dedupeKey=f"page-{i}")

    page1 = client.get(ARCHIVE, params={"limit": 2, "offset": 0}).json()
    page2 = client.get(ARCHIVE, params={"limit": 2, "offset": 2}).json()

    assert page1["total"] == page2["total"] == 5
    assert len(page1["items"]) == 2 and len(page2["items"]) == 2
    assert {i["dedupeKey"] for i in page1["items"]} & {i["dedupeKey"] for i in page2["items"]} == set()
    # 倒序分页：page1 是最新两条，page2 紧接着
    assert page1["items"][-1]["time"] > page2["items"][0]["time"] or (
        page1["items"][-1]["time"] == page2["items"][0]["time"]
    )


def test_default_limit_is_100(client):
    """默认 limit=100（不传时）——单元验证归一化函数，不必真造 100+ 条事件。"""
    assert events_service._normalize_limit(0) == 100
    assert events_service._normalize_limit(-5) == 100


def test_limit_capped_at_1000(client):
    """上限 1000——单元验证归一化函数，避免为了触发上限真的写 1000+ 条事件。"""
    assert events_service._normalize_limit(5000) == 1000
    assert events_service._normalize_limit(1000) == 1000
    assert events_service._normalize_limit(1) == 1


# ------------------------------------------------------------------------- R6/R8：不 join、原样返回


def test_items_are_envelope_verbatim_no_id_no_joined_names(client):
    """items 剔除 _id；不含任何 join 出来的名字字段（如 taskName/projectName）；
    subject 仍然只有 opaque id（R8：消费方拿 id 自己去 planner 查当前名字）。
    """
    _post_event(
        client,
        time="2026-07-05T08:00:00+08:00",
        dedupeKey="verbatim",
        subject={"zone": "z_9", "project": "p_9", "task": "t_9"},
    )
    body = client.get(ARCHIVE).json()
    item = body["items"][0]
    assert "_id" not in item
    assert item["subject"] == {"zone": "z_9", "project": "p_9", "task": "t_9"}
    joined_name_keys = {k for k in item if "name" in k.lower() or "Name" in k}
    assert not joined_name_keys, f"不许在后端 join 名字进事件：意外发现 {joined_name_keys}"
    assert item["data"]["durationSeconds"] == 900
