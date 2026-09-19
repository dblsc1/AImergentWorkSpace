"""事件入口的契约级单测：逐条对应 ``contracts/yq-event-v1.md`` §10 边界样例 B1–B10。

断言照抄契约，不发明口径。每个用例名里写明对应样例编号与规格验收号。
"""

from __future__ import annotations

import uuid

API = "/api/core/events"


def _envelope(**overrides) -> dict:
    doc = {
        "spec": "yq-event/v1",
        "id": f"evt_{uuid.uuid4().hex[:8]}",
        "dedupeKey": f"test:{uuid.uuid4().hex[:8]}",
        "type": "growth.granted",
        "user": "u_local",
        "source": "my-study-script",
        "time": "2026-07-29T21:00:00+08:00",
        "subject": {"zone": "z_7f21a4", "project": "p_3c98de"},
        "data": {"amount": 1, "note": "读完一章"},
    }
    doc.update(overrides)
    return doc


def _events_count(**query) -> int:
    from app.repo import get_db

    return get_db()["events"].count_documents(query or {})


def test_b1_s1_same_dedupe_key_different_id_lands_once(client):
    """B1/S1：同 (user,source,dedupeKey) 提交两次、id 不同 → 只落一条，第二次 duplicate:1 且 200。"""
    dk = "timer:sess_b1"
    first = client.post(API, json=_envelope(id="evt_aaa", dedupeKey=dk))
    second = client.post(API, json=_envelope(id="evt_bbb", dedupeKey=dk))

    assert first.status_code == 200
    assert first.json() == {"accepted": 1, "duplicate": 0, "rejected": []}
    assert second.status_code == 200, "重复提交不是错误，必须 200 不是 4xx"
    assert second.json() == {"accepted": 0, "duplicate": 1, "rejected": []}
    assert _events_count(dedupeKey=dk) == 1


def test_b2_s2_same_id_different_dedupe_key_both_land(client):
    """B2/S2：同 id、不同 dedupeKey → 两条都落（id 不是防重键）。"""
    for dk in ("test:b2-one", "test:b2-two"):
        resp = client.post(API, json=_envelope(id="evt_same", dedupeKey=dk))
        assert resp.json()["accepted"] == 1
    assert _events_count(id="evt_same") == 2


def test_b3_s3_missing_dedupe_key_rejected_no_fallback(client):
    """B3/S3：缺 dedupeKey → 拒绝，且不回退成用 id（库里一条都不落）。"""
    doc = _envelope(id="evt_no_dk")
    del doc["dedupeKey"]
    resp = client.post(API, json=doc)

    assert resp.status_code == 200, "部分失败不整批回滚：拒绝走 rejected 数组，不是 4xx"
    body = resp.json()
    assert body["accepted"] == 0
    assert len(body["rejected"]) == 1
    assert "dedupeKey" in body["rejected"][0]["reason"]
    assert _events_count() == 0, "拒绝就是没落库——回退成按 id 落库即防重失效"


def test_b4_r4_missing_zone_or_project_rejected(client):
    """B4/R4：缺 zone 或 project → 拒绝（subject 两个必填 id）。"""
    for missing, subject in (
        ("zone", {"project": "p_3c98de"}),
        ("project", {"zone": "z_7f21a4"}),
    ):
        body = client.post(API, json=_envelope(subject=subject)).json()
        assert body["accepted"] == 0, f"缺 {missing} 应拒绝"
        assert missing in body["rejected"][0]["reason"]
    assert _events_count() == 0


def test_b5_r5_zone_project_without_task_accepted(client):
    """B5/R5：有 zone+project、无 task → 接受（无具体任务的事件，如通用成长点）。"""
    resp = client.post(API, json=_envelope(subject={"zone": "z_7f21a4", "project": "p_3c98de"}))
    assert resp.status_code == 200
    assert resp.json() == {"accepted": 1, "duplicate": 0, "rejected": []}


def test_b5b_r3_retired_tier_fields_rejected(client):
    """B5b/R3：subject 里出现 tier2/tier3（J10 已废）→ 进 rejected，不落库。

    静默忽略是错的：老客户端会以为分类还生效，两套模型并存历史必分叉。
    """
    doc = _envelope(
        subject={"zone": "z_7f21a4", "project": "p_3c98de", "tier2": "学习"}
    )
    resp = client.post(API, json=doc)
    assert resp.status_code == 200, "拒绝走 rejected 数组，不是 4xx"
    body = resp.json()
    assert body["accepted"] == 0
    assert "tier2" in body["rejected"][0]["reason"]
    assert _events_count() == 0, "被拒的事件一条都不许落库"


def test_b6_s4_recorded_at_stamped_by_server(client):
    """B6/S4：客户端自带 recordedAt → 服务端覆盖，以服务端为准。"""
    from app.modules.events import repo

    client_stamp = "2020-01-01T00:00:00+00:00"
    doc = _envelope(dedupeKey="test:b6", recordedAt=client_stamp)
    assert client.post(API, json=doc).json()["accepted"] == 1

    stored = repo.find_by_dedupe("u_local", "my-study-script", "test:b6")
    assert stored is not None
    assert stored["recordedAt"] != client_stamp, "客户端的 recordedAt 必须被覆盖"
    assert stored["recordedAt"] >= "2026", "服务端盖章应是当下时间，不是客户端伪造的过去"
    assert stored["time"] == doc["time"], "time 用客户端给的，两个时间字段不许合并"


def test_b7_time_in_past_accepted(client):
    """B7：time 在过去（补交）→ 接受。"""
    resp = client.post(API, json=_envelope(time="2025-01-01T08:00:00+08:00"))
    assert resp.json()["accepted"] == 1


def test_time_without_timezone_rejected(client):
    """契约 §2：time 必须 ISO8601 带时区——裸时间戳拒绝。"""
    body = client.post(API, json=_envelope(time="2026-07-29T21:00:00")).json()
    assert body["accepted"] == 0
    assert "时区" in body["rejected"][0]["reason"]


def test_b8_unknown_type_stored_no_projection(client):
    """B8：不认识的 type → 静默忽略不报错；事件照常落库，投影不动。"""
    from app.modules.projector import repo as proj_repo

    resp = client.post(API, json=_envelope(type="totally.unknown.type"))
    assert resp.status_code == 200
    assert resp.json()["accepted"] == 1
    assert proj_repo.read_current("u_local") is None


def test_b9_unknown_envelope_field_preserved(client):
    """B9：信封多出未知字段 → 接受并原样保留（只增不改不删的另一面）。"""
    from app.modules.events import repo

    doc = _envelope(dedupeKey="test:b9")
    doc["futureField"] = {"whatever": 42}
    assert client.post(API, json=doc).json()["accepted"] == 1

    stored = repo.find_by_dedupe("u_local", "my-study-script", "test:b9")
    assert stored["futureField"] == {"whatever": 42}


def test_b10_no_growth_flag_from_external_source_accepted(client):
    """B10：外部 source 贴 no_growth → 接受并入库生效（§7 暂行条款）。"""
    from app.modules.events import repo

    doc = _envelope(dedupeKey="test:b10", flags=["no_growth"], source="student-script")
    assert client.post(API, json=doc).json()["accepted"] == 1
    stored = repo.find_by_dedupe("u_local", "student-script", "test:b10")
    assert stored["flags"] == ["no_growth"]


def test_batch_partial_failure_no_rollback(client):
    """契约语义 4：批量里一条坏数据不拖累其余——accepted/duplicate/rejected 各归各。"""
    good = _envelope(dedupeKey="test:batch-good")
    bad = _envelope()
    del bad["dedupeKey"]
    dup = _envelope(id="evt_retry", dedupeKey="test:batch-good")

    body = client.post(API, json=[good, bad, dup]).json()
    assert body["accepted"] == 1
    assert body["duplicate"] == 1
    assert [r["index"] for r in body["rejected"]] == [1]
    assert _events_count() == 1
