"""泳道偏好（契约 v2.22）：藏起来 / 置顶（按代理身份）、手动排位（按运行）、服务端排好的 rank。

要害：偏好只管**显示**——藏起来的代理时间照记；身份是 (agent, label) 不是 runId，所以重启后仍藏着 / 置顶着，
而手动排位只在这条运行还在跑时有效。
"""

from __future__ import annotations

import pytest

API = "/api/core"
LANES = f"{API}/views/lanes"
PREFS = f"{API}/lanes/prefs"
A = {"X-Nexus-Tenant": "ch_aaaa"}
B = {"X-Nexus-Tenant": "ch_bbbb"}


def _start(client, label, agent="cc", headers=None, **body):
    resp = client.post(f"{API}/agents/start", json={"agent": agent, "tool": "claude-code", "label": label, **body},
                       headers=headers or {})
    assert resp.status_code == 201, resp.text
    return resp.json()["runId"]


def _stop(client, run_id, headers=None):
    assert client.post(f"{API}/agents/{run_id}/stop", json={"outcome": "done"}, headers=headers or {}).status_code == 200


def _lanes(client, headers=None):
    resp = client.get(LANES, headers=headers or {})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _order(client, headers=None):
    """按 rank 排好的 label 列表。"""
    return [a["label"] for a in sorted(_lanes(client, headers)["agents"], key=lambda a: a["rank"])]


def _put(client, path, body, headers=None, expect=200):
    resp = client.put(f"{PREFS}/{path}", json=body, headers=headers or {})
    assert resp.status_code == expect, resp.text
    return resp.json()


def _hide(client, label, hidden=True, **kw):
    return _put(client, "agent", {"agent": "cc", "label": label, "hidden": hidden}, **kw)


def _pin(client, label, pinned=True, **kw):
    return _put(client, "agent", {"agent": "cc", "label": label, "pinned": pinned}, **kw)


# ─────────────────────────────────────────── 权限


def test_prefs_are_human_only(client):
    run = _start(client, "x")
    calls = [("GET", "", None), ("PUT", "/agent", {"agent": "cc", "hidden": True}),
             ("PUT", "/order", {"runId": run, "index": 0}), ("DELETE", "/order", None),
             ("DELETE", f"/order/{run}", None)]
    # 设备令牌 / read 令牌（都带 Bearer）→ 403，report 与匿名被范围中间件拦在外面（也是 403）
    for headers in ({"Authorization": "Bearer t"}, {"X-Nexus-Scope": "read", "Authorization": "Bearer t"},
                    {"X-Nexus-Scope": "read"},  # 没有 Bearer 的 read 范围：范围表自己也不放偏好端点
                    {"X-Nexus-Scope": "report", "Authorization": "Bearer t"},
                    {"X-Nexus-Scope": "report", "X-Nexus-Anonymous": "1"}):
        for method, path, body in calls:
            resp = client.request(method, PREFS + path, json=body, headers=headers)
            assert resp.status_code == 403, (headers, method, path, resp.status_code)
    assert client.get(PREFS).json() == {"agents": [], "order": []}  # 什么都没写成


# ─────────────────────────────────────────── 藏起来 / 置顶：幂等、有界、校验


def test_hide_pin_roundtrip_is_idempotent_and_cleans_up(client):
    first = _hide(client, "Garden  Work")
    assert first["agents"][0]["hidden"] is True and first["agents"][0]["pinned"] is False
    assert first["agents"][0]["label"] == "Garden Work"  # 空白折叠后存
    assert _hide(client, "garden work") == first  # 不分大小写、重复 = 同一个身份、不变
    pinned = _pin(client, "garden work")
    at = pinned["agents"][0]["pinnedAt"]
    assert at and _pin(client, "garden work")["agents"][0]["pinnedAt"] == at  # 再置顶不动先后
    _hide(client, "garden work", False)
    assert [a["label"] for a in client.get(PREFS).json()["agents"]] == ["Garden Work"]  # 还置顶着
    _pin(client, "garden work", False)
    assert client.get(PREFS).json()["agents"] == []  # 藏 / 置顶都没了的身份删掉


@pytest.mark.parametrize("body", [{}, {"agent": "cc"}, {"agent": "", "hidden": True}, {"agent": "x" * 200, "hidden": True},
                                  {"agent": "cc", "label": "x" * 300, "hidden": True},
                                  {"agent": "cc", "hidden": "yes"}, {"agent": "cc", "hidden": True, "extra": 1}])
def test_bad_bodies_are_422(client, body):
    assert client.put(f"{PREFS}/agent", json=body).status_code == 422


def test_caps_are_enforced(client, monkeypatch):
    from app.modules.prefs import service  # noqa: PLC0415

    monkeypatch.setattr(service, "MAX_PREFS", 2)
    monkeypatch.setattr(service, "MAX_ORDER", 1)
    _hide(client, "a")
    _hide(client, "b")
    assert client.put(f"{PREFS}/agent", json={"agent": "cc", "label": "c", "hidden": True}).status_code == 422
    r1, r2 = _start(client, "r1"), _start(client, "r2")
    _put(client, "order", {"runId": r1, "index": 0})
    assert client.put(f"{PREFS}/order", json={"runId": r2, "index": 0}).status_code == 422
    assert [o["runId"] for o in client.get(PREFS).json()["order"]] == [r1]


def test_tenant_isolation(client):
    ra = _start(client, "mine", headers=A)
    _start(client, "mine", headers=B)
    _hide(client, "mine", headers=A)
    _pin(client, "other", headers=A)
    assert [a["label"] for a in _lanes(client, B)["agents"]] == ["mine"]
    assert client.get(PREFS, headers=B).json() == {"agents": [], "order": []}
    assert _lanes(client, A)["agents"] == []
    # B 够不着 A 的运行
    resp = client.put(f"{PREFS}/order", json={"runId": ra, "index": 0}, headers=B)
    assert resp.status_code == 404


# ─────────────────────────────────────────── 藏起来：只影响显示


def test_hidden_agent_is_omitted_but_listed_for_restore_and_time_is_unchanged(client):
    shown, gone = _start(client, "shown"), _start(client, "gone", phase="waiting_input")
    ended = _start(client, "gone")
    _stop(client, ended)
    before = client.get(f"{API}/views/agent-time").json()
    cur = client.get(f"{API}/views/current").json()
    assert {a["label"] for a in cur["agents"]} == {"shown", "gone"}

    _hide(client, "Gone")
    body = _lanes(client)
    assert [a["label"] for a in body["agents"]] == ["shown"]
    assert {i["runId"] for i in body["interactions"]} <= {shown}
    assert body["hiddenAgents"] == [{"agent": "cc", "label": "Gone", "unverified": False, "live": True, "phase": "waiting_input"}]
    assert body["hiddenWaiting"] == 1
    # 「现在在跑什么」不列它；汇总（含 open 之外的一切）一个数都没变，open 也不再列
    after = client.get(f"{API}/views/agent-time").json()
    assert {o["runId"] for o in after["open"]} == {shown}
    assert {k: v for k, v in after.items() if k not in ("open", "hiddenCount")} == {k: v for k, v in before.items() if k not in ("open", "hiddenCount")}
    assert after["hiddenCount"] == 1
    assert {a["label"] for a in client.get(f"{API}/views/current").json()["agents"]} == {"shown"}
    # 还在跑的没有被动过
    assert gone  # 运行本身没停
    _hide(client, "gone", False)
    body = _lanes(client)
    assert {a["label"] for a in body["agents"]} == {"shown", "gone"} and body["hiddenAgents"] == []
    assert body["hiddenWaiting"] == 0


def test_hidden_without_runs_is_still_listed(client):
    _hide(client, "nobody")
    body = _lanes(client)
    assert body["hiddenAgents"] == [{"agent": "cc", "label": "nobody", "unverified": False, "live": False, "phase": None}]


def test_hidden_restart_keeps_hidden_and_pinned_but_not_manual_order(client):
    r1 = _start(client, "alpha")
    _start(client, "beta")
    _start(client, "gamma")
    _hide(client, "alpha")
    _pin(client, "beta")
    _stop(client, r1)
    again = _start(client, "alpha")  # 同名重启：新的 runId，仍然藏着
    assert again != r1 and "alpha" not in _order(client)
    assert next(a for a in _lanes(client)["agents"] if a["label"] == "beta")["pinned"] is True
    # 手动排位按运行：停了就不再占位
    gamma = next(a["runId"] for a in _lanes(client)["agents"] if a["label"] == "gamma")
    _put(client, "order", {"runId": gamma, "index": 0})
    _stop(client, gamma)
    restarted = _start(client, "gamma")
    row = next(a for a in _lanes(client)["agents"] if a["runId"] == restarted)
    assert row["manualOrder"] is None
    ended = next(a for a in _lanes(client)["agents"] if a["runId"] == gamma)
    assert ended["manualOrder"] is None and ended["endAt"] is not None


# ─────────────────────────────────────────── 排序矩阵


def test_default_order_is_the_activity_ranking(client):
    _start(client, "idle", phase="idle")
    _start(client, "working", phase="working")
    _start(client, "error", phase="error")
    _start(client, "waiting", phase="waiting_permission")
    ended = _start(client, "ended")
    _stop(client, ended)
    assert _order(client) == ["waiting", "working", "idle", "ended"]  # v2.24：出错的泳道不显示
    assert [a["label"] for a in _lanes(client)["inactiveAgents"]] == ["error"]
    ranks = sorted(a["rank"] for a in _lanes(client)["agents"])
    assert ranks == list(range(4))


def test_pinned_then_manual_then_activity_then_ended(client):
    _start(client, "waiting", phase="waiting_input")
    _start(client, "working")
    _start(client, "idle", phase="idle")
    _start(client, "pin-late")
    _start(client, "pin-first")
    done = _start(client, "done")
    _stop(client, done)
    _pin(client, "pin-first")
    _pin(client, "pin-late")
    assert _order(client) == ["pin-first", "pin-late", "waiting", "working", "idle", "done"]  # 按置顶先后
    # idle 手动拖到未置顶里的第 0 位；working 放到第 2 位
    ids = {a["label"]: a["runId"] for a in _lanes(client)["agents"]}
    _put(client, "order", {"runId": ids["idle"], "index": 0})
    assert _order(client) == ["pin-first", "pin-late", "idle", "waiting", "working", "done"]
    _put(client, "order", {"runId": ids["working"], "index": 1})
    assert _order(client) == ["pin-first", "pin-late", "idle", "working", "waiting", "done"]
    rows = {a["label"]: a for a in _lanes(client)["agents"]}
    assert (rows["idle"]["manualOrder"], rows["working"]["manualOrder"], rows["waiting"]["manualOrder"]) == (0, 1, None)
    assert rows["pin-first"]["pinned"] and not rows["idle"]["pinned"]
    # 超出个数的 slot 夹在在跑的之内，不会跑到已结束的后面
    _put(client, "order", {"runId": ids["idle"], "index": 99})
    assert _order(client)[-1] == "done"
    # 清掉一条 / 全清：回到活跃排
    assert client.delete(f"{PREFS}/order/{ids['idle']}").status_code == 200
    assert _lanes(client)["agents"] and next(a for a in _lanes(client)["agents"] if a["label"] == "idle")["manualOrder"] is None
    client.delete(f"{PREFS}/order")
    assert client.get(PREFS).json()["order"] == []
    assert _order(client) == ["pin-first", "pin-late", "waiting", "working", "idle", "done"]


def test_manual_order_is_idempotent_and_rejects_runs_that_are_not_live(client):
    run = _start(client, "one")
    first = _put(client, "order", {"runId": run, "index": 0})
    assert _put(client, "order", {"runId": run, "index": 0}) == first
    assert client.put(f"{PREFS}/order", json={"runId": "nope", "index": 0}).status_code == 404
    _stop(client, run)
    assert client.put(f"{PREFS}/order", json={"runId": run, "index": 0}).status_code == 404
    assert client.delete(f"{PREFS}/order/nope").status_code == 200  # 清一条没有的也成功
    assert client.put(f"{PREFS}/order", json={"runId": run, "index": -1}).status_code == 422


def test_ended_pinned_run_falls_to_the_end_group(client):
    old = _start(client, "p")
    _start(client, "q")
    _pin(client, "p")
    assert _order(client) == ["p", "q"]
    _stop(client, old)
    body = {a["label"]: a for a in _lanes(client)["agents"]}
    assert _order(client) == ["q", "p"] and body["p"]["pinned"] is True


# ─────────────────────────────────────────── 身份是自报的：未验证（匿名）的运行自成一类


ANON = {"X-Nexus-Scope": "report", "X-Nexus-Anonymous": "1"}


def test_anonymous_run_with_a_cloned_name_inherits_nothing_and_ranks_after_verified(client):
    _start(client, "plot", phase="waiting_input")
    _start(client, "other")
    fake = _start(client, "plot", headers=ANON)  # 同名的匿名运行
    _pin(client, "plot")
    rows = {a["runId"]: a for a in _lanes(client)["agents"]}
    assert rows[fake]["unverified"] is True and rows[fake]["pinned"] is False  # 不继承置顶
    assert _order(client)[-1] == "plot" and rows[fake]["rank"] == 2  # 排在所有已验证的后面
    # 匿名运行不能手动排位（和不存在 / 已结束同一个 404），也不能置顶
    assert client.put(f"{PREFS}/order", json={"runId": fake, "index": 0}).status_code == 404
    assert client.put(f"{PREFS}/agent", json={"agent": "cc", "label": "plot", "pinned": True, "unverified": True}).status_code == 422


def test_hiding_is_keyed_per_class(client):
    real = _start(client, "plot")
    fake = _start(client, "plot", headers=ANON)
    _hide(client, "plot")  # 藏已验证的 plot
    body = _lanes(client)
    assert [a["runId"] for a in body["agents"]] == [fake]  # 匿名同名的没被一起藏
    assert body["hiddenAgents"][0]["unverified"] is False
    _hide(client, "plot", False)
    _put(client, "agent", {"agent": "cc", "label": "plot", "hidden": True, "unverified": True})
    body = _lanes(client)
    assert [a["runId"] for a in body["agents"]] == [real] and body["hiddenAgents"][0]["unverified"] is True
    cur = client.get(f"{API}/views/current").json()["agents"]
    assert [a["runId"] for a in cur] == [real] and "unverified" not in cur[0]
    assert {o["runId"] for o in client.get(f"{API}/views/agent-time").json()["open"]} == {real}


# ─────────────────────────────────────────── 手动排位：每次拖动重写所有手动 slot（最近一次拖动一定落在目标位）


def _runs(client, *labels):
    for label in labels:
        _start(client, label)
    ids = {a["label"]: a["runId"] for a in _lanes(client)["agents"]}
    assert _order(client) == list(labels)  # 起点：活跃排 = 开始先后
    return ids


def _drag(client, ids, label, index):
    _put(client, "order", {"runId": ids[label], "index": index})
    return "".join(_order(client))


@pytest.mark.parametrize("drags,expected", [
    ([("D", 0)], ["DABC"]),
    ([("D", 0), ("C", 0)], ["DABC", "CDAB"]),  # 老实现：D:0、C:0 撞槽 → DCAB
    ([("D", 0), ("C", 1), ("A", 0)], ["DABC", "DCAB", "ADCB"]),  # 老实现：DCAB
    ([("B", 3), ("A", 3)], ["ACDB", "CDBA"]),
    ([("A", 99)], ["BCDA"]),  # 超出夹到最后
])
def test_manual_drags_land_on_their_target_and_keep_earlier_placements(client, drags, expected):
    ids = _runs(client, "A", "B", "C", "D")
    assert [_drag(client, ids, label, index) for label, index in drags] == expected
    slots = [o["slot"] for o in client.get(PREFS).json()["order"]]
    assert len(slots) == len(set(slots))  # 存的 slot 两两不同：状态没有歧义


def test_random_drags_property(client):
    import random  # noqa: PLC0415

    rng = random.Random(7)
    labels = ["A", "B", "C", "D", "E"]
    ids = _runs(client, *labels)
    placed: list[str] = []  # 按先后：手动排过位的
    for _ in range(25):
        label, index = rng.choice(labels), rng.randrange(0, 5)
        before = _order(client)
        _put(client, "order", {"runId": ids[label], "index": index})
        after = _order(client)
        assert after.index(label) == index  # 最近一次拖动落在目标位
        placed = [x for x in placed if x != label] + [label]
        manual_before = [x for x in before if x in placed and x != label]
        assert [x for x in after if x in manual_before] == manual_before  # 之前排过位的相对先后不变


def test_pinning_a_manually_placed_run_clears_its_slot(client):
    ids = _runs(client, "A", "B", "C")
    _drag(client, ids, "C", 0)
    _pin(client, "C")
    assert client.get(PREFS).json()["order"] == []
    _pin(client, "C", False)
    assert _order(client) == ["A", "B", "C"]  # 取消置顶回到活跃排，不带着旧 slot
    assert client.put(f"{PREFS}/order", json={"runId": ids["A"], "index": 0}).status_code == 200
    _pin(client, "A")
    assert client.put(f"{PREFS}/order", json={"runId": ids["A"], "index": 0}).status_code == 404  # 置顶的不能手动排位


def test_cas_exhaustion_is_a_409_not_a_500(client, monkeypatch):
    from app.modules.prefs import repo  # noqa: PLC0415

    monkeypatch.setattr(repo, "cas", lambda *a, **k: False)
    resp = client.put(f"{PREFS}/agent", json={"agent": "cc", "label": "x", "hidden": True})
    assert resp.status_code == 409


# ─────────────────────────────────────────── 身份归一化（服务端唯一一份）


@pytest.mark.parametrize("a,b", [("Ａｒｔ", "art"), ("ze​ro", "zero"), ("Straße", "STRASSE"), ("a \t b", "A B")])
def test_identity_folding(client, a, b):
    _hide(client, a)
    assert len(_hide(client, b)["agents"]) == 1  # 同一个身份
    run_a = _start(client, a)
    assert _lanes(client)["agents"] == [] and run_a


# ─────────────────────────────────────────── 置顶但没在跑（改名后留下的）、hiddenCount、失联


def test_stale_pinned_is_listed_and_removable(client):
    r = _start(client, "old")
    _pin(client, "old")
    assert _lanes(client)["stalePinned"] == []
    _stop(client, r)
    _start(client, "new")  # 改名重启
    assert _lanes(client)["stalePinned"] == [{"agent": "cc", "label": "old"}]
    _pin(client, "old", False)
    assert _lanes(client)["stalePinned"] == []


def test_hidden_count_in_current_and_agent_time(client):
    _start(client, "a")
    _start(client, "b")
    assert client.get(f"{API}/views/current").json()["hiddenCount"] == 0
    _hide(client, "b")
    assert client.get(f"{API}/views/current").json()["hiddenCount"] == 1
    body = client.get(f"{API}/views/agent-time").json()
    assert body["hiddenCount"] == 1 and len(body["open"]) == 1


def test_lost_hidden_run_is_not_waiting():
    from datetime import datetime, timezone  # noqa: PLC0415

    from app.modules.views.lane_order import arrange  # noqa: PLC0415

    now = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    t = "2026-01-01T10:00:00+00:00"
    item = {"runId": "r", "agent": "cc", "label": "x", "unverified": False, "startAt": t, "endAt": None, "lost": True,
            "lastSeenAt": t, "phases": [{"at": t, "phase": "waiting_input", "detail": None}]}
    prefs = {"agents": [{"key": "cc\nx", "agent": "cc", "label": "x", "hidden": True, "pinned": False}], "order": []}
    _, hidden, waiting, _ = arrange([item], prefs, now)
    assert waiting == 0 and hidden[0]["live"] is True and hidden[0]["phase"] != "waiting_input"
