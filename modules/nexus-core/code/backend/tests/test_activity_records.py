"""原始记录上传（契约 v2.38 一）：鉴权、校验、部分成功、防重、跨午夜、忽略闸门、blockKey 两步、重算、留存。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from _tier_common import BEARER, BLK, DEV, INP, REC, TZ, at, blocks, day, db, one, rec, upload, RULES

D = day(-1)


def test_bearer_required_for_upload_and_forbidden_for_reads(client):
    body = {"deviceId": DEV, "records": [rec(D, "09:00:00", "09:01:00", block=None)]}
    assert client.post(REC, json=body).status_code == 403
    assert client.post(REC, json=body, headers=BEARER).status_code == 200
    assert client.get(BLK, params={"date": D}, headers=BEARER).status_code == 403
    assert client.get(f"{BLK}/records", params={"date": D, "blockKey": "x"}, headers=BEARER).status_code == 403
    assert client.get(BLK, params={"date": D}).status_code == 200


def test_batch_shape_is_422(client):
    assert client.post(REC, json={"deviceId": DEV, "records": []}, headers=BEARER).status_code == 422
    assert client.post(REC, json={"deviceId": "a:b", "records": [{}]}, headers=BEARER).status_code == 422


def test_partial_success_and_document_shape(client):
    good = rec(D, "09:00:00", "09:00:40", app="chrome", title="  (3) Report ", block="示例文档")
    bad_secs = rec(D, "09:00:00", "09:00:00")
    too_long = {**rec(D, "00:00:00", "01:00:00"), "endAt": at(day(0), "00:00:01").isoformat()}
    no_block_key = {k: v for k, v in rec(D, "10:00:00", "10:01:00").items() if k != "block"}
    naive = {**rec(D, "11:00:00", "11:01:00"), "startAt": "2026-01-01T11:00:00"}
    out = upload(client, [good, bad_secs, too_long, no_block_key, naive])
    assert out["accepted"] == 1 and out["duplicates"] == 0 and out["ignored"] == 0
    assert sorted(r["index"] for r in out["rejected"]) == [1, 2, 3, 4]
    doc = db()["activity_records"].find_one({"user": "u_local"})
    assert doc["blockKey"] == "示例文档" and doc["keySource"] == "rule" and doc["block"] == "示例文档"
    assert doc["seconds"] == 40.0 and doc["date"] == D and doc["ledgerEventId"] is None
    assert doc["dedupeKey"].startswith(f"rec:{DEV}:") and doc["id"].startswith("rec_")
    assert doc["titleIsPseudonym"] is False and doc["endTs"] > doc["startTs"]


def test_dedupe_first_wins_and_cross_midnight_splits_into_two(client):
    r = rec(D, "09:00:00", "09:00:10")
    assert upload(client, [r])["accepted"] == 1
    out = upload(client, [{**r, "endAt": at(D, "09:00:30").isoformat()}])  # 同起点不同终点：后到者算重复
    assert (out["accepted"], out["duplicates"]) == (0, 1)
    assert db()["activity_records"].find_one({"user": "u_local"})["seconds"] == 10.0

    night = {**rec(D, "23:59:00", "23:59:30", app="code", title="night"),
             "endAt": (at(D, "23:59:00") + timedelta(seconds=120)).isoformat()}
    out = upload(client, [night])
    assert out["accepted"] == 1  # 跨午夜两半仍算一条
    docs = list(db()["activity_records"].find({"app": "code"}).sort("startTs", 1))
    assert [d["date"] for d in docs] == [D, day(0)] and [d["seconds"] for d in docs] == [60.0, 60.0]
    assert docs[1]["dedupeKey"] == f"rec:{DEV}:{int(docs[1]['startTs'].timestamp() * 1000)}"
    assert upload(client, [night])["duplicates"] == 1


def test_ignore_gate_drops_before_storing(client):
    assert client.post("/api/core/activity/ignores", json={"app": "secret"}).status_code == 201
    out = upload(client, [rec(D, "09:00:00", "09:01:00", app="secret"), rec(D, "09:02:00", "09:03:00", app="ok")])
    assert (out["accepted"], out["ignored"]) == (1, 1)
    assert db()["activity_records"].count_documents({"app": "secret"}) == 0


def test_block_key_two_steps(client):
    upload(client, [rec(D, "09:00:00", "09:01:00", app="chrome", title="✳ (2) 周报草稿", block=None),
                    rec(D, "09:02:00", "09:03:00", app="code", title="", block=None),
                    rec(D, "09:04:00", "09:05:00", app="chrome", title="x", block="  组块  ")])
    keys = {d["app"]: (d["blockKey"], d["keySource"]) for d in db()["activity_records"].find({})}
    assert keys["code"] == ("code", "auto")
    got = {i["blockKey"]: i["keySource"] for i in blocks(client, D)}
    assert got == {"chrome · 周报草稿": "auto", "code": "auto", "组块": "rule"}


def test_server_does_not_match_rules_on_upload_but_recomputes_on_new_version(client, seeded):
    tid = seeded["tasks"]["示例任务三"]["id"]
    rule = {"kind": "block", "app": "^chrome$", "title": "周报", "block": "周报块"}
    client.put(RULES, json={"rules": [rule]}, headers={"If-Match": '"0"'})
    upload(client, [rec(D, "09:00:00", "09:01:00", title="周报 v1"), rec(D, "09:05:00", "09:06:00", title="周报 v2", pseudo=True)])
    # 上传时不匹配：规则已在，但记录按 auto 存（检测程序没给 block = 没命中）
    assert {d["blockKey"] for d in db()["activity_records"].find({})} == {"chrome · 周报 v1", "chrome · 周报 v2"}
    # 规则版本变了（+1）：读块时惰性重算——pending 的、标题不是代号的才改
    r = client.put(RULES, json={"rules": [rule, {"kind": "block", "app": "zzz", "block": "别的"}]}, headers={"If-Match": '"1"'})
    assert r.status_code == 200
    got = {i["blockKey"]: i["keySource"] for i in blocks(client, D)}
    assert got == {"周报块": "rule", "chrome · 周报 v2": "auto"}


def test_recompute_skips_classified_blocks_and_old_records(client):
    upload(client, [rec(D, "09:00:00", "09:01:00", title="周报", block=None)])
    key = "chrome · 周报"
    tid = None
    assert client.post(f"{BLK}/ignore", json={"date": D, "blockKey": key, "ifSeq": 0}).status_code == 200  # 有分类了
    client.put(RULES, json={"rules": [{"kind": "block", "title": "周报", "block": "新名"}]}, headers={"If-Match": '"0"'})
    assert [i["blockKey"] for i in blocks(client, D)] == [key]  # 已分类的块不动
    old = day(-20)
    upload(client, [rec(old, "09:00:00", "09:01:00", title="周报")])
    client.put(RULES, json={"rules": []}, headers={"If-Match": '"1"'})
    assert [i["blockKey"] for i in blocks(client, old)] == ["chrome · 周报"]  # 14 天以前的不重算


def test_recompute_is_bounded_and_continues(client, monkeypatch):
    from app.modules.activity import block_key  # noqa: PLC0415

    monkeypatch.setattr(block_key, "RECOMPUTE_BATCH", 2)
    upload(client, [rec(D, f"09:0{i}:00", f"09:0{i}:30", title="周报") for i in range(4)])
    client.put(RULES, json={"rules": [{"kind": "block", "title": "周报", "block": "新名"}]}, headers={"If-Match": '"0"'})
    blocks(client, D)
    assert db()["activity_records"].count_documents({"blockKey": "新名"}) == 2
    blocks(client, D)
    assert db()["activity_records"].count_documents({"blockKey": "新名"}) == 4


def test_retention_purges_lazily(client):
    old = datetime.now(timezone.utc) - timedelta(days=40)
    db()["activity_records"].insert_one({"user": "u_local", "dedupeKey": "rec:old", "date": "2000-01-01", "startTs": old,
                                         "endTs": old, "blockKey": "k", "app": "a", "seconds": 1.0, "titleIsPseudonym": False})
    upload(client, [rec(D, "09:00:00", "09:01:00")])
    assert db()["activity_records"].count_documents({"dedupeKey": "rec:old"}) == 0
    assert client.get(BLK, params={"date": "2000-01-01"}).json()["items"] == []


def _rules(client, rules, version):
    assert client.put(RULES, json={"rules": rules}, headers={"If-Match": f'"{version}"'}).status_code == 200


def test_recompute_keeps_detector_names_skips_empty_titles_and_stamps_rv_only_after_checking(client):
    upload(client, [rec(D, "09:00:00", "09:01:00", title="周报", block="检测给的"), rec(D, "09:02:00", "09:03:00", title="", app="code"),
                    rec(D, "09:04:00", "09:05:00", title="周报")])
    rvs = {d["keySource"]: d["rv"] for d in db()["activity_records"].find({})}
    assert rvs["rule"] is None and rvs["auto"] is None
    _rules(client, [{"kind": "block", "app": "zzz", "block": "无关"}], 0)  # 版本 1，没有规则命中任何记录
    got = {i["blockKey"]: i["keySource"] for i in blocks(client, D)}
    assert got == {"检测给的": "rule", "code": "auto", "chrome · 周报": "auto"}  # 检测程序给的名字保留
    assert all(d["rv"] == 1 for d in db()["activity_records"].find({"title": {"$ne": ""}}))  # 检查过一次就盖章
    _rules(client, [{"kind": "block", "title": ".", "block": "全吃"}], 1)  # 版本 2：命中的改，空标题不动
    got = {i["blockKey"] for i in blocks(client, D)}
    assert got == {"全吃", "code"}


def test_recompute_skips_blocks_taken_by_rules_or_generic(client, seeded):
    tid = seeded["tasks"]["示例任务三"]["id"]
    upload(client, [rec(D, "09:00:00", "09:01:00", title="周报"), rec(D, "09:02:00", "09:03:00", title="日报")])
    client.post("/api/core/activity/block-rules", json={"blockKey": "chrome · 周报", "taskId": tid})  # 被第二级规则分走
    client.post(f"{BLK}/generic", json={"blockKey": "chrome · 日报", "generic": True})
    db()["activity_generic"].update_many({}, {"$set": {"since": D}})
    _rules(client, [{"kind": "block", "title": ".", "block": "全吃"}], 0)
    assert {i["blockKey"] for i in blocks(client, D)} == {"chrome · 周报", "chrome · 日报"}


def test_truncated_titles_are_not_recomputed(client):
    upload(client, [rec(D, "09:00:00", "09:01:00", title="周" * 600)])
    _rules(client, [{"kind": "block", "title": "周", "block": "命中"}], 0)
    assert [i["blockKey"].startswith("chrome") for i in blocks(client, D)] == [True]


def test_upload_never_stamps_a_rule_version_it_did_not_match_against(client):
    _rules(client, [{"kind": "block", "title": "周报", "block": "新名"}], 0)  # 检测程序还没拉到这条：上传 block=null
    upload(client, [rec(D, "09:00:00", "09:01:00", title="周报")])
    assert db()["activity_records"].find_one({})["rv"] is None
    assert [i["blockKey"] for i in blocks(client, D)] == ["新名"]  # 读时检查：按当前规则重算
