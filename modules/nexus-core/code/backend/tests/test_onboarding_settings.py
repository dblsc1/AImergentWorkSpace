"""onboarding.v1「四」「六」「七」：设置新键、browserDomains 校验、onboarding/done、新装缺省（NC-S / NC-N）。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest
from _tier_common import API, BEARER, SET, db

DONE = f"{API}/activity/onboarding/done"
OFF = {"enabled": False, "windowsClassify": False, "honeycombRecords": False}
PACKS = {"claude-code": "claude", "codex": "gpt", "gemini-cli": "gemini", "*": "deepseek"}


def _put(client, version, headers=None, **over):
    body = {"tiers": False, "agentDirect": OFF, **over}
    return client.put(SET, json=body, headers={"If-Match": f'"{version}"', **(headers or {})})


@pytest.fixture()
def old(client):
    """老账号：库里已有事件，设置文档不存在。"""
    db()["events"].insert_one({"user": "u_local", "seq": 1})
    return client


def test_s01_stored_old_doc_gets_defaults(client):
    db()["activity_settings"].insert_one({"user": "u_local", "version": 3, "tiers": False, "agentDirect": OFF})
    s = client.get(SET).json()
    assert (s["liveAskMinutes"], s["liveAskEnabled"], s["browserDomains"], s["onboardingDoneAt"]) == (5, True, [], None)
    assert s["pet"] == {"packsByTool": PACKS} and s["version"] == 3 and s["tiers"] is False


@pytest.mark.parametrize("v", [0, 61, "5", 5.5, True])
def test_s02_minutes_bad(old, v):
    assert _put(old, 0, liveAskMinutes=v).status_code == 422


@pytest.mark.parametrize("v", [1, 60])
def test_s03_minutes_ok(old, v):
    assert _put(old, 0, liveAskMinutes=v).json()["liveAskMinutes"] == v
    assert old.get(SET).json()["liveAskMinutes"] == v


@pytest.mark.parametrize("v", ["yes", 1])
def test_s04_enabled_bad(old, v):
    assert _put(old, 0, liveAskEnabled=v).status_code == 422


@pytest.mark.parametrize("v", [[f"a{i}.com" for i in range(201)], "a.com", ["a.com", 1], [None]])
def test_s05_domains_bad_shape(old, v):
    assert _put(old, 0, browserDomains=v).status_code == 422


def test_s06_200_domains_ok(old):
    assert _put(old, 0, browserDomains=[f"a{i}.com" for i in range(200)]).status_code == 200


@pytest.mark.parametrize("v", ["https://a.com", "a.com/x", "a.com:80", "", "a" * 254, "a b.com", "*.", "a..com", "-a.com"])
def test_s07_domain_bad_entry(old, v):
    assert _put(old, 0, browserDomains=["ok.com", v]).status_code == 422


def test_s08_domains_normalized(old):
    assert _put(old, 0, browserDomains=["A.COM", "a.com", "*.b.com"]).json()["browserDomains"] == ["a.com", "*.b.com"]


def test_s09_omitted_keeps(old):
    assert _put(old, 0, liveAskMinutes=10, liveAskEnabled=False, browserDomains=["a.com"]).status_code == 200
    s = _put(old, 1).json()
    assert (s["liveAskMinutes"], s["liveAskEnabled"], s["browserDomains"]) == (10, False, ["a.com"])


def test_s10_done_at_ignored(old):
    r = _put(old, 0, onboardingDoneAt="2026-01-01T00:00:00Z")
    assert r.status_code == 200 and r.json()["onboardingDoneAt"] is None


@pytest.mark.parametrize("v", ["x", "2026-01-01T00:00:00", [1]])
def test_s11_done_at_bad_422(old, v):
    assert _put(old, 0, onboardingDoneAt=v).status_code == 422


def test_pet_put(old):
    assert _put(old, 0, pet={"packsByTool": {"codex": "x"}}).json()["pet"] == {"packsByTool": {"codex": "x"}}
    for bad in ({"packsByTool": {"codex": 1}}, {"packsByTool": "x"}, {"x": 1}, "x"):
        assert _put(old, 1, pet=bad).status_code == 422


def test_s12_if_match(old):
    assert old.put(SET, json={"tiers": False, "agentDirect": OFF}).status_code == 428
    assert _put(old, 7).status_code == 412


def test_s13_s14_s16_done(old):
    _put(old, 0, liveAskMinutes=9)
    r = old.post(DONE)
    assert r.status_code == 200
    at = datetime.fromisoformat(r.json()["onboardingDoneAt"])
    assert at.utcoffset() is not None and abs((datetime.now(timezone.utc) - at).total_seconds()) < 30
    assert r.json()["liveAskMinutes"] == 9 and r.json()["version"] == 2
    r2 = old.post(DONE).json()
    assert r2["onboardingDoneAt"] == r.json()["onboardingDoneAt"] and r2["version"] == 2
    assert _put(old, 2).json()["onboardingDoneAt"] == r.json()["onboardingDoneAt"]


def test_s15_done_human_only(old):
    assert old.post(DONE, headers=BEARER).status_code == 403


def test_done_on_old_account_without_doc_inserts_defaults(old):
    s = old.post(DONE).json()
    assert s["version"] == 1 and s["tiers"] is False and s["onboardingDoneAt"]


def test_n01_n02_fresh_install(client):
    s = client.get(SET).json()
    assert s["tiers"] is True and s["version"] == 1 and s["tiersSince"]
    assert s["agentDirect"] == {"enabled": True, "windowsClassify": True, "honeycombRecords": False}
    assert abs((datetime.now(timezone.utc) - datetime.fromisoformat(s["tiersSince"])).total_seconds()) < 30
    assert client.get(SET).json() == s and db()["activity_settings"].count_documents({}) == 1


def test_n03_stored_untouched(client):
    db()["activity_settings"].insert_one({"user": "u_local", "version": 1, "tiers": False, "agentDirect": OFF})
    s = client.get(SET).json()
    assert s["tiers"] is False and s["agentDirect"] == OFF and s["version"] == 1


def test_n04_rules_exist_no_doc(client):
    db()["detector_rules"].insert_one({"user": "u_local", "version": 1, "rules": []})
    s = client.get(SET).json()
    assert s["tiers"] is False and s["version"] == 0 and s["agentDirect"] == OFF
    assert db()["activity_settings"].count_documents({}) == 0


def test_n05_device_token_reads_first(client):
    s = client.get(SET, headers=BEARER).json()
    assert s["tiers"] is True and client.get(SET).json() == s


def test_n06_concurrent(client):
    with ThreadPoolExecutor(10) as ex:
        out = list(ex.map(lambda _: client.get(SET).json(), range(10)))
    assert len({o["tiersSince"] for o in out}) == 1 and db()["activity_settings"].count_documents({}) == 1


def test_n07_put_with_etag(client):
    etag = client.get(SET).headers["ETag"]
    assert etag == '"1"'
    r = client.put(SET, json={"tiers": True, "agentDirect": OFF}, headers={"If-Match": etag})
    assert r.status_code == 200 and r.json()["version"] == 2


def test_n08_old_account_with_events(old):
    s = old.get(SET).json()
    assert s["tiers"] is False and s["version"] == 0 and db()["activity_settings"].count_documents({}) == 0
