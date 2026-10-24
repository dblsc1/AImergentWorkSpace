"""窗口块读写（契约 v2.38 二）：聚合、并集、状态解析、分页、五个写端点的成功与 403 / 404 / 409 / 422、过期日。"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from _tier_common import BEARER, BLK, BR, DEV, TZ, at, blocks, day, db, one, rec, upload

D = day(-1)
K = "chrome · 周报"


@pytest.fixture()
def tid(seeded):
    return seeded["tasks"]["示例任务三"]["id"]


@pytest.fixture()
def pid(seeded):
    return seeded["projects"][next(iter(seeded["projects"]))]["id"]


def _seed(client):
    upload(client, [rec(D, "09:00:00", "09:10:00", title="周报"), rec(D, "10:00:00", "10:05:00", title="周报"),
                    rec(D, "11:00:00", "11:01:00", app="code", title="")])


def _post(client, op, **body):
    return client.post(f"{BLK}/{op}", json=body)


def _ms(date, hms):
    return round(at(date, hms).timestamp() * 1000)


def test_read_shape_order_and_union_across_devices(client):
    _seed(client)
    upload(client, [rec(D, "09:05:00", "09:15:00", title="周报")], device="dev_second")  # 与第一台重叠 5 分钟
    items = blocks(client, D)
    assert [i["blockKey"] for i in items] == [K, "code"]  # 秒数从多到少
    b = items[0]
    assert b["seconds"] == 15 * 60 + 5 * 60 and b["records"] == 3 and b["apps"] == ["chrome"]
    assert b["status"] == "pending" and b["via"] is None and b["parts"] == [] and b["seq"] == 0
    assert b["keySource"] == "auto" and b["generic"] is False and b["expired"] is False
    assert b["firstAt"].startswith(D) and b["lastAt"].startswith(D)
    assert [i["blockKey"] for i in blocks(client, D, "pending")] == [K, "code"] and blocks(client, D, "assigned") == []
    page = client.get(BLK, params={"date": D, "limit": 1}).json()
    assert len(page["items"]) == 1 and page["nextCursor"]
    page2 = client.get(BLK, params={"date": D, "limit": 1, "cursor": page["nextCursor"]}).json()
    assert page2["items"][0]["blockKey"] == "code" and page2["nextCursor"] is None
    for bad in ("1", "9" * 5000, "!!!", "bm90LWpzb24=", "W10="):  # 坏 cursor：422，不 500
        assert client.get(BLK, params={"date": D, "cursor": bad}).status_code == 422, bad
    assert client.get(BLK, params={"date": D, "limit": 1001}).status_code == 422
    assert client.get(BLK, params={"status": "bogus"}).status_code == 422


def test_block_records(client):
    _seed(client)
    r = client.get(f"{BLK}/records", params={"date": D, "blockKey": K}).json()
    assert r["truncated"] is False and [x["seconds"] for x in r["items"]] == [600.0, 300.0]
    assert set(r["items"][0]) == {"startAt", "endAt", "seconds", "app", "title", "deviceId"}


def test_assign_remember_writes_rule_and_resolves_docless_blocks(client, tid):
    _seed(client)
    r = _post(client, "assign", date=D, blockKey=K, taskId=tid, remember=True, ifSeq=0)
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["status"] == "assigned" and b["via"] == "human" and b["seq"] == 1
    assert b["parts"] == [{"fromTs": None, "toTs": None, "taskId": tid, "projectId": None, "seconds": 900}]
    rules = client.get(BR).json()["items"]
    assert len(rules) == 1 and rules[0]["blockKey"] == K and rules[0]["via"] == "assign" and rules[0]["broken"] is False
    # 另一天（最近 14 天内、没有文档）按第二级规则算
    upload(client, [rec(day(-2), "09:00:00", "09:01:00", title="周报")])
    other = one(client, day(-2), K)
    assert (other["status"], other["via"], other["ruleId"]) == ("assigned", "rule", rules[0]["id"])
    assert other["parts"][0]["taskId"] == tid and other["parts"][0]["seconds"] == 60
    # 14 天以前的没文档的块不按规则算
    upload(client, [rec(day(-20), "09:00:00", "09:01:00", title="周报")])
    assert one(client, day(-20), K)["status"] == "pending"


def test_assign_to_project_inbox_and_targets_404_and_exactly_one(client, tid, pid):
    _seed(client)
    assert _post(client, "assign", date=D, blockKey=K, projectId="p_inbox", ifSeq=0).status_code == 200
    assert _post(client, "assign", date=D, blockKey=K, projectId=pid, ifSeq=1).json()["parts"][0]["projectId"] == pid
    assert _post(client, "assign", date=D, blockKey=K, taskId="t_nope", ifSeq=2).status_code == 404
    assert _post(client, "assign", date=D, blockKey=K, projectId="p_nope", ifSeq=2).status_code == 404
    assert _post(client, "assign", date=D, blockKey=K, ifSeq=2).status_code == 422
    assert _post(client, "assign", date=D, blockKey=K, taskId=tid, projectId=pid, ifSeq=2).status_code == 422
    assert _post(client, "assign", date=D, blockKey=K, taskId=tid).status_code == 422  # ifSeq 必填


def test_if_seq_conflict_returns_current(client, tid):
    _seed(client)
    assert _post(client, "assign", date=D, blockKey=K, taskId=tid, ifSeq=0).status_code == 200
    r = _post(client, "assign", date=D, blockKey=K, taskId=tid, ifSeq=0)
    assert r.status_code == 409 and r.json()["current"]["seq"] == 1 and r.json()["current"]["status"] == "assigned"
    assert _post(client, "reset", date=D, blockKey=K, ifSeq=0).status_code == 409
    assert _post(client, "ignore", date=D, blockKey=K, ifSeq=5).status_code == 409
    assert _post(client, "split", date=D, blockKey=K, ifSeq=0, parts=[{"taskId": tid}, {"taskId": tid}]).status_code in (409, 422)


def test_writes_are_human_only(client, tid):
    _seed(client)
    for op, body in (("assign", {"taskId": tid, "ifSeq": 0}), ("split", {"parts": [], "ifSeq": 0}), ("ignore", {"ifSeq": 0}),
                     ("reset", {"ifSeq": 0}), ("generic", {"generic": True})):
        r = client.post(f"{BLK}/{op}", json={"date": D, "blockKey": K, **body}, headers=BEARER)
        assert r.status_code == 403, op
    r = client.post(f"{BLK}/assign", content=b"not json", headers=BEARER)  # 坏请求体也先 403
    assert r.status_code == 403
    r = client.post(f"{BLK}/assign", json={"date": D, "blockKey": K, "taskId": tid, "ifSeq": 0}, headers={"X-Nexus-Scope": "write"})
    assert r.status_code == 403
    assert db()["activity_blocks"].count_documents({}) == 0


def test_split_validation_and_seconds_per_part(client, tid, pid):
    _seed(client)
    cut = _ms(D, "09:05:00")
    parts = [{"taskId": tid}, {"fromTs": cut, "projectId": pid}]
    r = _post(client, "split", date=D, blockKey=K, ifSeq=0, parts=[{"toTs": cut, "taskId": tid}, {"fromTs": cut, "projectId": pid}])
    assert r.status_code == 200, r.text
    ps = r.json()["parts"]
    assert [(p["fromTs"], p["toTs"]) for p in ps] == [(None, cut), (cut, None)]
    assert [p["seconds"] for p in ps] == [300, 600]  # 09:00-09:10 的记录在 09:05 切开
    assert db()["activity_blocks"].find_one({"blockKey": K})["gen"] == 1
    bad = [
        [{"toTs": cut, "taskId": tid}],                                            # 少于 2 段
        [{"toTs": cut, "taskId": tid}, {"fromTs": cut + 1, "projectId": pid}],     # 不相接
        [{"taskId": tid}, {"projectId": pid}],                                     # 没有拆分点
        [{"toTs": _ms(D, "10:00:00"), "taskId": tid}, {"fromTs": _ms(D, "10:00:00"), "toTs": cut, "projectId": pid},
         {"fromTs": cut, "taskId": tid}],                                          # 不递增
        [{"fromTs": cut, "toTs": cut, "taskId": tid}, {"fromTs": cut, "projectId": pid}],  # 首段 from 不是当天开头
        [{"toTs": cut}, {"fromTs": cut, "projectId": pid}],                        # 没目标
        [{"toTs": cut, "taskId": "t_nope"}, {"fromTs": cut, "projectId": pid}],   # 404
    ]
    for i, p in enumerate(bad):
        r = _post(client, "split", date=D, blockKey=K, ifSeq=1, parts=p)
        assert r.status_code == (404 if i == 6 else 422), (i, r.text)
    too_many = [{"toTs": _ms(D, f"{h:02d}:00:00"), "fromTs": _ms(D, f"{h - 1:02d}:00:00") if h > 1 else None, "taskId": tid}
                for h in range(1, 22)]
    assert _post(client, "split", date=D, blockKey=K, ifSeq=1, parts=too_many).status_code == 422
    # 同样的拆分点再写一次：gen 不变；换拆分点：gen +1
    _post(client, "split", date=D, blockKey=K, ifSeq=1, parts=[{"toTs": cut, "taskId": tid}, {"fromTs": cut, "projectId": pid}])
    assert db()["activity_blocks"].find_one({"blockKey": K})["gen"] == 1


def test_generic_marker_blocks_rules_and_split_keeps_generic(client, tid):
    _seed(client)
    _post(client, "assign", date=D, blockKey=K, taskId=tid, remember=True, ifSeq=0)
    upload(client, [rec(day(-2), "09:00:00", "09:01:00", title="周报")])
    r = _post(client, "generic", blockKey=K, generic=True)
    assert r.json()["generic"] is True and r.json()["since"] == day(0)
    assert one(client, day(-2), K)["status"] == "assigned"  # since 之前的日期不受影响
    upload(client, [rec(day(0), "00:00:01", "00:00:30", title="周报")])
    today_b = one(client, day(0), K)
    assert (today_b["status"], today_b["generic"], today_b["via"]) == ("generic", True, None)
    # 通用窗口 assign 不写规则（remember 无效），状态仍是 generic
    r = _post(client, "assign", date=day(0), blockKey=K, taskId=tid, remember=True, ifSeq=0)
    assert r.json()["status"] == "generic" and len(client.get(BR).json()["items"]) == 1
    assert _post(client, "generic", blockKey=K, generic=False).json() == {"blockKey": K, "generic": False}
    assert db()["activity_generic"].count_documents({}) == 0


def test_ignore_day_and_forever(client):
    _seed(client)
    b = _post(client, "ignore", date=D, blockKey=K, ifSeq=0).json()
    assert b["status"] == "ignored" and b["parts"] == [] and "scope" not in b
    assert client.get("/api/core/activity/ignores").json()["total"] == 0
    b = _post(client, "ignore", date=D, blockKey=K, forever=True, ifSeq=1).json()
    ig = client.get("/api/core/activity/ignores").json()["items"]
    assert [(i["app"], i["titleContains"]) for i in ig] == [("chrome", "周报")] and "scope" not in b
    b = _post(client, "ignore", date=D, blockKey="code", forever=True, ifSeq=0).json()
    assert b["scope"] == "app"
    assert {(i["app"], i["titleContains"]) for i in client.get("/api/core/activity/ignores").json()["items"]} == {("chrome", "周报"), ("code", None)}


def test_ignore_forever_refused_for_rule_blocks_and_empty_blocks(client):
    upload(client, [rec(D, "09:00:00", "09:01:00", block="规则块")])
    assert _post(client, "ignore", date=D, blockKey="规则块", forever=True, ifSeq=0).status_code == 422
    assert _post(client, "ignore", date=D, blockKey="规则块", ifSeq=0).status_code == 200
    assert _post(client, "ignore", date=D, blockKey="nothing", forever=True, ifSeq=0).status_code == 422


def test_reset_keeps_document_and_seq_never_goes_back(client, tid):
    _seed(client)
    _post(client, "assign", date=D, blockKey=K, taskId=tid, ifSeq=0)
    r = _post(client, "reset", date=D, blockKey=K, ifSeq=1)
    assert r.status_code == 200 and r.json()["status"] == "pending" and r.json()["seq"] == 2
    doc = db()["activity_blocks"].find_one({"blockKey": K})
    assert doc["status"] is None and doc["parts"] is None and doc["via"] is None and doc["seq"] == 2
    assert _post(client, "reset", date=D, blockKey=K, ifSeq=0).status_code == 409
    assert _post(client, "assign", date=D, blockKey=K, taskId=tid, ifSeq=2).json()["seq"] == 3
    r = _post(client, "reset", date=D, blockKey="never", ifSeq=0)  # 没文档：也建一份，seq 单调
    assert r.status_code == 200 and r.json()["seq"] == 1 and r.json()["status"] == "pending"
    assert _post(client, "reset", date=D, blockKey="never", ifSeq=0).status_code == 409


def test_expired_day_is_readonly(client, tid):
    old = day(-40)
    now = datetime.now(TZ)
    db()["activity_records"].insert_one({"user": "u_local", "dedupeKey": "rec:x", "date": old, "startTs": now, "endTs": now,
                                         "blockKey": K, "keySource": "auto", "app": "chrome", "seconds": 1.0,
                                         "titleIsPseudonym": False, "block": None, "rv": 0})
    # 读时的惰性清理会清掉它；直接写端点
    r = _post(client, "assign", date=old, blockKey=K, taskId=tid, ifSeq=0)
    assert r.status_code == 409 and "过期" in r.json()["detail"]
    assert _post(client, "reset", date=old, blockKey=K, ifSeq=0).status_code == 409
    assert one_expired(client, old) is True


def one_expired(client, d):
    from app.modules.activity import blocks as b  # noqa: PLC0415

    return b.expired(datetime.fromisoformat(d).date())


def test_keyset_cursor_is_stable_when_blocks_change_between_pages(client):
    upload(client, [rec(D, "09:00:00", "09:10:00", app="a"), rec(D, "10:00:00", "10:05:00", app="b"),
                    rec(D, "11:00:00", "11:01:00", app="c")])
    p1 = client.get(BLK, params={"date": D, "limit": 1}).json()
    assert [i["blockKey"] for i in p1["items"]] == ["a · doc"]
    upload(client, [rec(D, "12:00:00", "12:20:00", app="big")])  # 翻页途中冒出一个更大的块：不应让后面的重复
    p2 = client.get(BLK, params={"date": D, "limit": 5, "cursor": p1["nextCursor"]}).json()
    assert [i["blockKey"] for i in p2["items"]] == ["b · doc", "c · doc"]


def test_ignore_forever_needs_app_shaped_name(client):
    upload(client, [rec(D, "09:00:00", "09:01:00", app="x")])
    db()["activity_records"].update_many({}, {"$set": {"blockKey": "odd"}})  # auto 来源但名字不是「app · …」
    assert _post(client, "ignore", date=D, blockKey="odd", forever=True, ifSeq=0).status_code == 422


def test_expired_beats_missing_target_and_rule_cap_checked_before_block_write(client, tid, monkeypatch):
    r = _post(client, "assign", date=day(-40), blockKey=K, taskId="t_nope", ifSeq=0)
    assert r.status_code == 409
    _seed(client)
    from app.modules.activity import blocks_repo  # noqa: PLC0415

    monkeypatch.setattr(blocks_repo, "MAX_RULES", 0)
    assert _post(client, "assign", date=D, blockKey=K, taskId=tid, remember=True, ifSeq=0).status_code == 422
    assert db()["activity_blocks"].count_documents({}) == 0
    assert _post(client, "assign", date=D, blockKey=K, taskId=tid, remember=False, ifSeq=0).status_code == 200


def test_generic_without_since_race_does_not_500(client, monkeypatch):
    from app.modules.activity import blocks_repo  # noqa: PLC0415

    monkeypatch.setattr(blocks_repo, "generic_since", lambda user: {})  # 并发取消：读回时已经没有了
    assert _post(client, "generic", blockKey=K, generic=True).json()["since"] == day(0)
