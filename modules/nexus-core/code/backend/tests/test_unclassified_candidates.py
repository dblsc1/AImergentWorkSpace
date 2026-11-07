"""onboarding.v1「五」补记候选（NC-C）。"""

from __future__ import annotations

import pytest
from _claim_common import CAND, PL, garden, on, seed_days
from _onboarding_common import run_attending, set_browser_domains
from _reconcile_common import assign, tiers
from _tier_common import BEARER, at, day, db, rec, upload


@pytest.fixture()
def w(client):
    on(client)
    return garden(client)


def _get(client, expect=200, **q):
    r = client.get(CAND, params=q)
    assert r.status_code == expect, r.text
    return r.json()


def test_c01_c02_c11_c12_agent_match_and_shape(client, w):
    spans = [(at(day(-d), "09:59:00"), at(day(-d), "11:30:00")) for d in range(1, 10)]
    run_attending(client, "garden", spans, taskId=w["task"])
    upload(client, [rec(day(-d), "10:00:00", "11:20:00", app="ptyxis", title="Coder") for d in range(1, 10)])
    r = _get(client, taskId=w["task"])
    assert r["target"] == {"taskId": w["task"], "projectId": w["project"], "name": "写提示词", "path": "学习 / garden / 写提示词"}
    assert r["totalSeconds"] == 43200
    it = r["items"][0]
    assert (it["blockKey"], it["displayName"], it["match"], it["days"], it["seconds"]) == ("ptyxis · garden", "garden", "agent", 9, 43200)
    assert (it["firstDate"], it["lastDate"]) == (day(-9), day(-1))
    r2 = _get(client, projectId=w["project"])  # 运行挂在本项目下的任务：算 agent
    assert r2["target"]["taskId"] is None and r2["target"]["path"] == "学习 / garden" and r2["items"][0]["match"] == "agent"


def test_c03_c04_c05_validation(client, w):
    _get(client, 422)
    _get(client, 422, taskId=w["task"], projectId=w["project"])
    _get(client, 404, taskId="t_nope")
    _get(client, 404, projectId="p_nope")
    for bad in ("0", "36", "abc"):
        _get(client, 422, taskId=w["task"], days=bad)
    for ok in ("1", "35"):
        _get(client, taskId=w["task"], days=ok)


def test_c06_days_window(client, w):
    seed_days(client, "garden-notes", [10])
    assert _get(client, projectId=w["project"], days=7)["items"] == []
    assert len(_get(client, projectId=w["project"])["items"]) == 1


def test_c07_union_over_days(client, w):
    upload(client, [rec(day(-d), "10:00:00", "10:10:00", app="code", title="garden-notes") for d in (1, 2, 3)])
    upload(client, [rec(day(-d), "10:05:00", "10:15:00", app="code", title="garden-notes") for d in (1, 2, 3)], device="dev_aaaaaaaaaaaaaaaa")
    it = _get(client, projectId=w["project"])["items"][0]
    assert it["days"] == 3 and it["seconds"] == 3 * 900


def test_c08_c09_not_pending_excluded(client, w):
    ka, kb, kc, kd, ke = (seed_days(client, f"garden-{x}", [1], app="chrome" if x == "d" else "code") for x in "abcde")
    assign(client, day(-1), ka, w["task"])
    assert client.post("/api/core/activity/blocks/ignore", json={"date": day(-1), "blockKey": kb, "ifSeq": 0}).status_code == 200
    db().activity_generic.insert_one({"user": "u_local", "blockKey": kc, "since": day(-5)})
    assert [i["blockKey"] for i in _get(client, projectId=w["project"])["items"]] == [ke]  # kd 浏览器看不懂


def test_c10_c13_c14_priority_and_ai(client, w):
    k = seed_days(client, "garden-notes", [1])
    assert _get(client, projectId=w["project"])["items"][0]["match"] == "name"
    db().activity_new_items.insert_one({"user": "u_local", "id": "nb_x", "kind": "blockRule", "status": "pending", "op": "add",
                                        "blockKey": k, "taskId": None, "projectId": w["project"]})
    assert _get(client, projectId=w["project"])["items"][0]["match"] == "ai"
    for st in ("accepted", "rejected", "superseded"):
        db().activity_new_items.update_one({"id": "nb_x"}, {"$set": {"status": st}})
        assert _get(client, projectId=w["project"])["items"][0]["match"] == "name"


def test_c15_c17_c18_c19_name_words(client):
    on(client)
    w = garden(client, task="写 Garden 提示", project="学习计划", zone="Z")
    upload(client, [rec(day(-1), "10:00:00", "10:10:00", app="ptyxis", title="x") | {"folder": "garden-notes"},
                    rec(day(-1), "11:00:00", "11:10:00", app="code", title="提示 文档"),
                    rec(day(-1), "12:00:00", "12:10:00", app="code", title="学习 笔记"),
                    rec(day(-1), "13:00:00", "13:10:00", app="code", title="a 写")])
    got = {i["blockKey"]: i["match"] for i in _get(client, taskId=w["task"])["items"]}
    assert got == {"ptyxis · x": "name", "code · 提示 文档": "name"}
    assert _get(client, projectId=w["project"])["items"] == []


def test_c16_domain_in_name(client):
    on(client)
    w = garden(client, task="figma", project="设计")
    set_browser_domains(["figma.com"])
    upload(client, [rec(day(-1), "10:00:00", "10:10:00", app="chrome", title="figma.com · 首页")])
    assert _get(client, taskId=w["task"])["items"][0]["match"] == "name"


def test_c20_task_target_uses_both_names(client):
    on(client)
    w = garden(client, task="garden", project="qqq")
    seed_days(client, "garden-x", [1])
    assert len(_get(client, taskId=w["task"])["items"]) == 1
    assert _get(client, projectId=w["project"])["items"] == []
    w2 = garden(client, task="zzz", project="garden", zone="Z2")
    assert len(_get(client, taskId=w2["task"])["items"]) == 1


def test_c21_c23_nothing_matches(client, w):
    seed_days(client, "other-thing", [1])
    r = _get(client, projectId=w["project"])
    assert r["items"] == [] and r["totalSeconds"] == 0


def test_c22_cap_100(client, w):
    upload(client, [rec(day(-1), f"{i // 60:02d}:{i % 60:02d}:00", f"{i // 60:02d}:{i % 60:02d}:{30 + i % 20}", app="code", title=f"garden-{i}")
                    for i in range(120)])
    r = _get(client, projectId=w["project"])
    secs = [i["seconds"] for i in r["items"]]
    assert len(secs) == 100 and secs == sorted(secs, reverse=True) and r["totalSeconds"] == sum(secs)


def test_c24_tiers_off(client, w):
    seed_days(client, "garden-notes", [1])
    tiers(client, False)
    r = _get(client, projectId=w["project"])
    assert r["items"] == [] and r["totalSeconds"] == 0


def test_c25_device_token_403(client, w):
    assert client.get(CAND, params={"projectId": w["project"]}, headers=BEARER).status_code == 403


def test_c26_unclassified_task_is_target(client, w):
    t = client.post(f"{PL}/tasks", json={"projectId": w["project"], "name": "未分类"}).json()
    seed_days(client, "garden-x", [1])
    r = _get(client, taskId=t["id"])
    assert r["target"]["path"] == "学习 / garden / 未分类" and len(r["items"]) == 1
