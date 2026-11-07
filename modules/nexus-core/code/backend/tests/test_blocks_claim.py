"""onboarding.v1「五」一次性补记 claim / undo（NC-K）。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from _claim_common import CAND, CLAIM, UNDO, claim, garden, on, seed_days
from _onboarding_common import set_browser_domains
from _reconcile_common import assign, live_sessions, rnd, tiers
from _tier_common import BEARER, BLK, BR, day, db, one, rec, upload


@pytest.fixture()
def w(client):
    on(client)
    return garden(client)


def _rules():
    return list(db().activity_block_rules.find({"user": "u_local"}))


def _asked():
    return {a["blockKey"]: a["answer"] for a in db().activity_asked.find({"user": "u_local"})}


def _domains(client):
    return client.get("/api/core/activity/settings").json()["browserDomains"]


def test_k01_k02_k03_k04_claim_and_ledger(client, w):
    k = seed_days(client, "garden-notes", [1, 2, 3])
    r = claim(client, k, projectId=w["project"])
    assert r == {"claimed": [{"blockKey": k, "days": 3, "seconds": 3 * 4800}], "totalSeconds": 3 * 4800, "skipped": []}
    rnd()
    got = live_sessions()
    assert len(got) == 3 and all(e["ai"]["confirmed"] is True for e in got)
    assert all(one(client, day(-d), k)["via"] == "human" and one(client, day(-d), k)["status"] == "assigned" for d in (1, 2, 3))
    rules = _rules()
    assert len(rules) == 1 and rules[0]["blockKey"] == k and rules[0]["via"] == "assign" and rules[0]["projectId"] == w["project"]
    k2 = seed_days(client, "garden-two", [1])
    claim(client, k2, taskId=w["task"])
    rnd()
    assert any(e["subject"]["task"] == w["task"] for e in live_sessions())


def test_k05_k06_rule_replaced_or_not(client, w):
    other = garden(client, project="other", zone="Z")
    k2 = seed_days(client, "garden-two", [20])  # 14 天之外，不受规则影响
    client.post(BR, json={"blockKey": k2, "projectId": other["project"]})
    claim(client, k2, projectId=w["project"])
    r = next(x for x in _rules() if x["blockKey"] == k2)
    assert r["projectId"] == w["project"] and len(_rules()) == 1
    k3 = seed_days(client, "garden-three", [20])
    claim(client, k3, projectId=w["project"], remember=False)
    assert len(_rules()) == 1


def test_k07_k08_k09_k10_skipped_reasons(client, w, monkeypatch):
    ka = seed_days(client, "garden-a", [1])
    assign(client, day(-1), ka, w["task"])
    kb = seed_days(client, "garden-b", [1])
    r = claim(client, [ka, kb, "code · nope"], projectId=w["project"])
    assert r["skipped"] == [{"blockKey": ka, "reason": "notPending"}, {"blockKey": "code · nope", "reason": "notFound"}]
    assert [c["blockKey"] for c in r["claimed"]] == [kb]
    assert _asked()[kb] == "assigned"
    kc = seed_days(client, "garden-c", [1])
    from app.modules.activity import blocks  # noqa: PLC0415

    real = blocks.today
    monkeypatch.setattr(blocks, "today", lambda: real() + timedelta(days=40))
    assert claim(client, kc, projectId=w["project"])["skipped"] == [{"blockKey": kc, "reason": "expired"}]


def test_k09_before_tiers(client, w):
    k = seed_days(client, "garden-a", [5])
    db().activity_settings.update_one({"user": "u_local"}, {"$set": {"tiersSince": datetime.now(timezone.utc) - timedelta(days=1)}})
    assert claim(client, k, projectId=w["project"])["skipped"] == [{"blockKey": k, "reason": "beforeTiers"}]
    assert one(client, day(-5), k)["status"] == "pending" and k not in _asked()


def test_k11_partial_days(client, w, monkeypatch):
    k = seed_days(client, "garden-a", [1, 2, 3, 4, 5])
    assign(client, day(-3), k, w["task"])
    db().activity_settings.update_one({"user": "u_local"}, {"$set": {"tiersSince": datetime.now(timezone.utc) - timedelta(days=3, hours=1)}})
    r = claim(client, k, projectId=w["project"])
    # 第 5、4 天在 tiersSince 之前；第 3 天已 assigned；记 1、2 两天
    assert r["claimed"] == [{"blockKey": k, "days": 2, "seconds": 9600}] and r["skipped"] == []


def _conflicts(monkeypatch, n):
    from app.modules.activity import blocks_repo  # noqa: PLC0415

    real, left = blocks_repo.put_block, [n]

    def flaky(user, date, key, fields, if_seq, old):
        if left[0] > 0 and fields.get("status") == "assigned":
            left[0] -= 1
            db().activity_blocks.update_one({"user": user, "date": date, "blockKey": key},
                                            {"$inc": {"seq": 1}, "$setOnInsert": {"status": None, "parts": None, "via": None, "gen": 0, "runs": []}},
                                            upsert=True)
        return real(user, date, key, fields, if_seq, old)

    monkeypatch.setattr(blocks_repo, "put_block", flaky)


def test_k12_conflict_retried(client, w, monkeypatch):
    k = seed_days(client, "garden-a", [1])
    _conflicts(monkeypatch, 1)
    assert claim(client, k, projectId=w["project"])["claimed"][0]["days"] == 1


def test_k13_conflict_three_times(client, w, monkeypatch):
    k = seed_days(client, "garden-a", [1, 2])
    _conflicts(monkeypatch, 3)
    r = claim(client, k, projectId=w["project"])
    assert r["claimed"] == [{"blockKey": k, "days": 1, "seconds": 4800}] and r["skipped"] == []


def _fill_rules(n):
    now = datetime.now(timezone.utc)
    db().activity_block_rules.insert_many([{"user": "u_local", "id": f"br_{i}", "blockKey": f"x{i}", "taskId": None, "projectId": "p_inbox",
                                            "author": "human", "via": "assign", "createdAt": now, "updatedAt": now} for i in range(n)])


def test_k14_rules_full_422_changes_nothing(client, w):
    _fill_rules(1999)
    k1, k2 = seed_days(client, "garden-a", [1]), seed_days(client, "garden-b", [1])
    claim(client, [k1, k2], 422, projectId=w["project"])
    assert one(client, day(-1), k1)["status"] == "pending" and _asked() == {} and len(_rules()) == 1999


def test_k15_replace_at_cap(client, w):
    _fill_rules(1999)
    k = seed_days(client, "garden-a", [20])
    other = garden(client, project="other", zone="Z")
    client.post(BR, json={"blockKey": k, "projectId": other["project"]})
    assert len(_rules()) == 2000
    claim(client, k, projectId=w["project"])
    assert len(_rules()) == 2000


def test_k16_k17_k18_validation(client, w):
    k = seed_days(client, "garden-a", [1])
    for body in ({"blockKeys": [k]}, {"blockKeys": [k], "taskId": w["task"], "projectId": w["project"]},
                 {"projectId": w["project"], "blockKeys": []}, {"projectId": w["project"], "blockKeys": [str(i) for i in range(101)]},
                 {"projectId": w["project"], "blockKeys": "x"}, {"projectId": w["project"], "blockKeys": [1]}):
        assert client.post(CLAIM, json=body).status_code == 422, body
    assert client.post(CLAIM, json={"projectId": "p_nope", "blockKeys": [k]}).status_code == 404
    assert client.post(CLAIM, json={"taskId": "t_nope", "blockKeys": [k]}).status_code == 404
    assert client.post(CLAIM, json={"projectId": w["project"], "blockKeys": [k]}, headers=BEARER).status_code == 403
    assert client.post(CLAIM, json={"projectId": w["project"], "blockKeys": [k]}, headers={"X-Nexus-Scope": "write"}).status_code == 403
    assert client.post(UNDO, json={"blockKeys": [k]}, headers=BEARER).status_code == 403


def test_k19_k20_asked(client, w):
    k1, k2 = seed_days(client, "garden-a", [1, 2]), seed_days(client, "garden-b", [1])
    claim(client, [k1, k2, "code · nope"], projectId=w["project"])
    assert _asked() == {k1: "assigned", k2: "assigned"}


def test_k21_total_equals_candidates(client, w):
    k = seed_days(client, "garden-d", [3, 4])
    cand = client.get(CAND, params={"projectId": w["project"]}).json()
    assert claim(client, k, projectId=w["project"])["totalSeconds"] == cand["totalSeconds"] == 9600


def test_k23_k24_k26_browser_domain(client, w):
    set_browser_domains([])
    k = seed_days(client, "figma.com · x", [1], app="chrome")
    claim(client, k, projectId=w["project"])
    assert _domains(client) == ["figma.com"]
    k2 = seed_days(client, "figma.com · y", [2], app="chrome")
    claim(client, k2, projectId=w["project"])
    assert _domains(client) == ["figma.com"]
    k3 = seed_days(client, "InPrivate", [1], app="chrome")
    claim(client, k3, projectId=w["project"])
    assert _domains(client) == ["figma.com"]
    set_browser_domains([f"d{i}.com" for i in range(200)])
    k4 = seed_days(client, "new.com · z", [3], app="chrome")
    assert claim(client, k4, projectId=w["project"])["claimed"][0]["days"] == 1
    assert len(_domains(client)) == 200


def test_k25_assign_and_split_add_domain(client, w):
    set_browser_domains([])
    k = seed_days(client, "figma.com · x", [1], app="chrome")
    assign(client, day(-1), k, w["task"])
    assert _domains(client) == ["figma.com"]
    set_browser_domains([])
    k2 = seed_days(client, "other.com · y", [1], app="chrome")
    parts = [{"taskId": w["task"]}, {"taskId": w["task"]}]
    midnight = int(datetime.fromisoformat(day(-1) + "T12:00:00+00:00").timestamp() * 1000)
    parts[0]["toTs"], parts[1]["fromTs"] = midnight, midnight
    r = client.post(f"{BLK}/split", json={"date": day(-1), "blockKey": k2, "parts": parts, "ifSeq": 0})
    assert r.status_code == 200, r.text
    assert _domains(client) == ["other.com"]


def test_k27_tiers_off_409(client, w):
    k = seed_days(client, "garden-a", [1])
    tiers(client, False)
    assert client.post(CLAIM, json={"projectId": w["project"], "blockKeys": [k]}).status_code == 409
    assert client.post(UNDO, json={"blockKeys": [k]}).status_code == 409


def test_undo_resets_claimed_days_rule_and_asked(client, w):
    k = seed_days(client, "garden-a", [1, 2, 3])
    plain = seed_days(client, "garden-p", [1])
    assign(client, day(-1), plain, w["task"])  # 非 claim 的 assign 不撤
    claim(client, k, projectId=w["project"])
    r = client.post(UNDO, json={"blockKeys": [k, plain, k]})
    assert r.status_code == 200 and r.json() == {"undone": [{"blockKey": k, "days": 3}]}
    assert all(one(client, day(-d), k)["status"] == "pending" for d in (1, 2, 3))
    assert one(client, day(-1), plain)["status"] == "assigned"
    assert not any(x["blockKey"] == k for x in _rules()) and k not in _asked()
    rnd()
    assert live_sessions() == [] or all(e["subject"]["task"] != w["project"] for e in live_sessions())


def test_undo_skips_days_changed_after_claim(client, w):
    k = seed_days(client, "garden-a", [1, 2])
    claim(client, k, projectId=w["project"])
    s = one(client, day(-1), k)
    r = client.post(f"{BLK}/assign", json={"date": day(-1), "blockKey": k, "taskId": w["task"], "remember": False, "ifSeq": s["seq"]})
    assert r.status_code == 200
    assert client.post(UNDO, json={"blockKeys": [k]}).json() == {"undone": [{"blockKey": k, "days": 1}]}
    assert one(client, day(-1), k)["status"] == "assigned"
