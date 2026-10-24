"""mcp.tools.v2 的工具测试：真起 MCP 服务 + tests/fake_nexus.py 里的假 nexus-core，走 HTTP。
覆盖 contracts/mcp.tools.v2 第六节清单，外加每个工具的 happy path、help、v1 名字迁移、address 歧义、范围头转发、部分成功。"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import specs
import tools
import v1_migration
from fake_nexus import EVIL, W, R, Fake, call, err, ok, post, rpc

ALICE = {"X-Nexus-Tenant": "u_alice"}
READS = ["Get_Started", "Get_View", "Get_Arrangements", "Get_Now", "Get_History", "Get_Pending_Classify_Windows", "Get_Rules",
         "Get_Proposals", "Get_Device_Health"]
WRITES = ["CRUD_Windows_Classify_Rules", "CRUD_Windowsblock_Classify_Rules", "CRUD_Windowsblock", "CRUD_Honeycomb",
          "Submit_Report", "Submit_Questions_To_Human"]
HINT = "带 help: true 再调一次看用法；不确定就调 Get_Started"
# 契约登记的下游端点（第三、四节 + 每个工具都读的 views/tree 与 activity/settings）
REGISTERED = {
    "GET": {"/api/core/views/tree", "/api/core/activity/settings", "/api/core/views/arrangements", "/api/core/views/current",
            "/api/core/views/agent-time", "/api/core/views/history", "/api/core/activity/blocks", "/api/core/detector/rules",
            "/api/core/activity/block-rules", "/api/core/activity/ignores", "/api/core/activity/proposals",
            "/api/core/activity/questions", "/api/core/activity/secretary-reports", "/api/core/activity/reports",
            "/api/core/activity/direct-log", "/api/core/detector/devices", "/api/core/detector/incidents"},
    "POST": {"/api/core/activity/proposals/window-rules", "/api/core/activity/proposals/ignores",
             "/api/core/activity/proposals/block-rules", "/api/core/activity/proposals/block-actions",
             "/api/core/activity/proposals/projects", "/api/core/activity/proposals/tasks",
             "/api/core/activity/proposals/changes", "/api/core/activity/proposals/sessions",
             "/api/core/activity/secretary-reports", "/api/core/activity/questions"},
}


def names(url, headers=None):
    return [t["name"] for t in rpc(url, "tools/list", headers=headers)["result"]["tools"]]


def posts():
    return [(p, b) for (m, p, *_), (b, _a) in zip([r for r in Fake.requests if r[0] == "POST"], Fake.bodies)]


# ── tools/list、instructions、help、note、permissions ────────────────


LIVE = ["get_window_awaiting_target", "suggest_window_target"]


@pytest.mark.parametrize("headers,want", [({}, 17), (W, 15), (R, 9), ({"X-Nexus-Scope": ""}, 17)])
def test_tools_list_by_scope(servers, headers, want):
    got = names(servers, headers)
    assert len(got) == want
    assert got == (READS if want == 9 else READS + WRITES + (LIVE if want == 17 else []))


def test_tools_list_schemas_and_annotations(servers):
    for t in rpc(servers, "tools/list", headers=W)["result"]["tools"]:
        s = t["inputSchema"]
        assert s["type"] == "object" and s["additionalProperties"] is False and "help" in s["properties"]
        assert not {"user", "tenant", "owner", "userId", "tenantId"} & set(s["properties"])
        a = t["annotations"]
        assert a["openWorldHint"] is False
        if t["name"] in WRITES:
            assert (a["readOnlyHint"], a["destructiveHint"]) == (False, False)
        else:
            assert a["readOnlyHint"] is True
        assert t["description"]
    assert len(specs.SPECS) == 15 and set(tools.WRITES) == set(WRITES)


def test_server_instructions_is_one_sentence(servers):
    r = rpc(servers, "initialize", {"protocolVersion": "2025-06-18"})["result"]
    assert r["instructions"] == "第一次用先调 Get_Started"


@pytest.mark.parametrize("name", READS + WRITES)
def test_help_never_fetches_data(servers, name):
    h = ok(servers, name, {"help": True}, ALICE)
    assert set(h) == {"tool", "summary", "params", "example", "note", "permissions"}
    assert h["tool"] == name and h["summary"] and len(h["note"]) <= 100
    assert set(h["example"]) == {"request", "response"} and h["permissions"]["scope"] == "full"
    props = tools.TOOLS[name]["inputSchema"]["properties"]
    assert [p["name"] for p in h["params"]] == [k for k in props if k != "help"]
    assert all(set(p) == {"name", "type", "default", "desc"} for p in h["params"])
    # 示例入参能通过自己的 inputSchema
    tools._check(tools.TOOLS[name]["inputSchema"], h["example"]["request"], "")
    # 唯一的下游请求是为 permissions 读设置
    assert {p for _, p, *_ in Fake.requests} <= {"/api/core/activity/settings"}
    assert not Fake.bodies


def test_help_ignores_other_args_but_checks_help_type(servers):
    assert ok(servers, "CRUD_Honeycomb", {"help": True, "op": "bogus"})["tool"] == "CRUD_Honeycomb"
    assert err(servers, "Get_View", {"help": "yes"})["status"] == 400


def test_every_success_has_note_and_permissions(servers):
    cases = [("Get_Started", {}), ("Get_View", {}), ("Get_Arrangements", {}), ("Get_Now", {}), ("Get_History", {}),
             ("Get_Pending_Classify_Windows", {}), ("Get_Rules", {}), ("Get_Proposals", {}), ("Get_Device_Health", {}),
             ("CRUD_Windows_Classify_Rules", {"ops": [{"op": "add", "app": "a", "block": "b"}]}),
             ("CRUD_Windowsblock_Classify_Rules", {"ops": [{"op": "add", "block": "b", "target": "xx-g"}]}),
             ("CRUD_Windowsblock", {"items": [{"action": "generic", "block": "b"}]}),
             ("CRUD_Honeycomb", {"op": "delete", "target": "xx-g-xtsc"}),
             ("Submit_Report", {"text": "t"}), ("Submit_Questions_To_Human", {"questions": [{"title": "q"}]})]
    assert [n for n, _ in cases] == READS + WRITES
    for name, args in cases:
        out = ok(servers, name, args, ALICE if name == "Get_Device_Health" else W)
        assert 0 < len(out["note"]) <= 100, name
        assert set(out["permissions"]) == {"scope", "directApply", "canDirectApply"}, name
    methods_paths = {(m, p) for m, p, *_ in Fake.requests}
    for m, p in methods_paths:
        assert p in REGISTERED[m], (m, p)


def test_permissions_shape_per_scope_and_cache(servers, monkeypatch):
    p = ok(servers, "Get_View", {}, W)["permissions"]
    assert p == {"scope": "write", "directApply": {"enabled": True, "windowsClassify": True, "honeycombRecords": False},
                 "canDirectApply": {"windowsClassify": True, "honeycombRecords": False}}
    assert ok(servers, "Get_View", {})["permissions"] == {**p, "scope": "full", "canDirectApply": {"windowsClassify": False, "honeycombRecords": False}}
    assert ok(servers, "Get_View", {}, R)["permissions"]["scope"] == "read"
    # 缓存：同租户 30 秒内只读一次设置；过了就重读
    Fake.requests.clear()
    tools._SETTINGS.clear()
    clock = [1000.0]
    monkeypatch.setattr(tools, "_clock", lambda: clock[0])
    for _ in range(3):
        ok(servers, "Get_View", {}, W)
    settings = lambda: sum(1 for _, p, *_ in Fake.requests if p.endswith("/settings"))  # noqa: E731
    assert settings() == 1
    clock[0] += 31
    ok(servers, "Get_View", {}, W)
    assert settings() == 2
    # 租户分开缓存
    ok(servers, "Get_View", {}, {**W, "X-Nexus-Tenant": "u_off"})
    assert settings() == 3
    # 读失败（老后端）= 全 false
    old = ok(servers, "Get_View", {}, {**W, "X-Nexus-Tenant": "u_old"})["permissions"]
    assert old["directApply"] == {"enabled": False, "windowsClassify": False, "honeycombRecords": False}
    assert old["canDirectApply"] == {"windowsClassify": False, "honeycombRecords": False}


def test_403_on_write_clears_settings_cache(servers):
    h = {**W, "X-Nexus-Tenant": "u_off"}
    ok(servers, "Get_View", {}, h)   # 缓存里是 u_off 的设置
    assert "u_off" in tools._SETTINGS
    out = ok(servers, "CRUD_Windows_Classify_Rules", {"ops": [{"op": "add", "app": "a", "block": "b"}], "direct_apply": True}, h)
    assert out["results"][0]["status"] == 403 and "总开关" in out["results"][0]["detail"]
    # 清了缓存之后，错误里的 permissions 又重读了一次，所以此刻缓存里是新取的
    assert sum(1 for _, p, *_ in Fake.requests if p.endswith("/settings")) == 2


# ── 错误形状 ───────────────────────────────────────────────────────


@pytest.mark.parametrize("name,args", [
    ("Get_View", {"depth": "branch"}), ("Get_View", {"extra": 1}), ("Get_View", {"includeDone": "yes"}),
    ("Get_Now", {"who": "bob"}), ("Get_History", {"detail": "all"}), ("Get_History", {"fromDate": "2026/10/24"}),
    ("Get_Rules", {"level": 3}), ("Get_Device_Health", {"limit": 0}),
    ("CRUD_Honeycomb", {}), ("CRUD_Honeycomb", {"op": "create"}), ("Submit_Report", {"text": ""}),
    ("Submit_Report", {"text": "x", "direct_apply": True}),   # Submit_* 没有 direct_apply：多余字段
    ("Submit_Questions_To_Human", {"questions": [{"title": "q", "direct_apply": True}]}),
    ("Submit_Questions_To_Human", {"questions": []}), ("CRUD_Windows_Classify_Rules", {"ops": []}),
    ("CRUD_Windows_Classify_Rules", {"ops": [{"op": "add", "app": "a", "block": "b"}] * 51}),
    ("CRUD_Windows_Classify_Rules", {"ops": ["x"]}), ("CRUD_Windows_Classify_Rules", {"ops": [{"op": "zap"}]}),
    ("CRUD_Windowsblock", {"items": [{"action": "assign"}]}),
])
def test_bad_args_400_with_example_and_fixed_hint(servers, name, args):
    e = err(servers, name, args, W)
    assert e["status"] == 400 and e["detail"]
    assert e["example"] == tools.TOOLS[name]["example"]["request"]
    tools._check(tools.TOOLS[name]["inputSchema"], e["example"], "")   # 示例本身合法
    assert not Fake.bodies


def test_null_arg_means_absent(servers):
    assert ok(servers, "Get_View", {"scope": None, "depth": None})["depth"] == "project"


def test_nexus_422_becomes_400_with_example(servers):
    out = ok(servers, "CRUD_Windows_Classify_Rules", {"ops": [{"op": "add", "app": "LONGAPP", "block": "b"}]}, W)
    assert out["results"][0]["ok"] is False and out["results"][0]["status"] == 400
    Fake.override = (422, {"detail": [{"loc": ["query", "from"], "msg": "bad"}]})
    e = err(servers, "Get_History", {"fromDate": "2026-10-24"}, W)
    assert e["status"] == 400 and "bad" in e["detail"] and e["example"] == tools.TOOLS["Get_History"]["example"]["request"]


def test_other_4xx_pass_through_and_5xx_is_502(servers):
    Fake.override = (404, {"detail": "没有这个东西"})
    e = err(servers, "Get_Arrangements")
    assert (e["status"], e["detail"]) == (404, "没有这个东西") and "example" not in e
    Fake.override = (500, {"detail": "Traceback: secret"})
    e = err(servers, "Get_Arrangements")
    assert (e["status"], e["detail"]) == (502, "数据服务暂时不可用")
    Fake.override = (200, {"unexpected": "shape"})
    assert err(servers, "Get_Device_Health")["status"] == 502
    Fake.override = None
    real = tools.NEXUS_CORE_URL
    tools.NEXUS_CORE_URL = "http://127.0.0.1:1"
    try:
        assert err(servers, "Get_View") == {"status": 502, "detail": "数据服务暂时不可用", "hint": HINT}
    finally:
        tools.NEXUS_CORE_URL = real


def test_upstream_body_cap(servers, monkeypatch):
    monkeypatch.setattr(tools, "MAX_UPSTREAM", 100)
    assert err(servers, "Get_View")["status"] == 502


# ── 读工具 ─────────────────────────────────────────────────────────


def test_get_started(servers):
    r = ok(servers, "Get_Started", {}, W)
    assert len(r["about"]) <= 600 and r["tiersOn"] is True and "tiers" in r and len(r["tiers"]) == 3
    assert [t["name"] for t in r["tools"]] == READS + WRITES and all(t["answers"] for t in r["tools"])
    assert len(r["rules"]) == 5 and "address" in r["addressing"]
    assert r["today"] and r["tz"][0] in "+-" and r["tz"][3] == ":"
    assert not Fake.bodies and "三级分类" in r["about"]
    off = ok(servers, "Get_Started", {}, {"X-Nexus-Tenant": "u_tiersoff"})
    assert off["tiersOn"] is False and "没打开" in off["note"]
    # 不教具体怎么调：各工具的入参名不在文档里
    assert "includeDone" not in json.dumps(r, ensure_ascii=False)


def test_get_view_defaults_depth_and_shapes(servers):
    r = ok(servers, "Get_View")
    assert r["scope"] is None and r["depth"] == "project" and r["truncated"] is False
    z = r["tree"][0]
    assert z == {"kind": "zone", "id": "z_7f", "address": "xx", "name": "学习", "children": z["children"]}
    p = z["children"][0]
    assert p["kind"] == "project" and p["address"] == "xx-g" and "children" not in p   # depth 以下不给 children
    assert (p["openTasks"], p["doneTasks"]) == (7, 1) and p["status"] == "active" and p["progress"] == 0.5
    assert [c["id"] for c in z["children"]] == ["p_3c", "p_new", "p_old"] and r["tree"][1]["children"] == []
    # 给了 scope：缺省 depth=task；includeDone 缺省 false，数被藏起来的
    r = ok(servers, "Get_View", {"scope": "XX-G"})
    assert r["depth"] == "task" and r["scope"] == {"kind": "project", "id": "p_3c", "address": "xx-g", "name": "garden"}
    kids = r["tree"][0]["children"]
    assert "t_a0" not in [k["id"] for k in kids] and r["hiddenDone"] == 1
    t1 = next(k for k in kids if k["id"] == "t_a1")
    assert t1 == {"kind": "task", "id": "t_a1", "address": "xx-g-xtsc", "name": "写提示词", "done": False,
                  "plan": {"start": "2026-09-28", "end": "2026-09-30"}, "dependsOn": ["xx-g-ywcd"]}
    assert "t_eph" in [k["id"] for k in kids]   # 临时任务照列
    r = ok(servers, "Get_View", {"scope": " xx-g ", "includeDone": True})
    assert "t_a0" in [k["id"] for k in r["tree"][0]["children"]] and r["hiddenDone"] == 0
    assert ok(servers, "Get_View", {"depth": "zone"})["tree"][0].get("children") is None
    assert ok(servers, "Get_View", {"scope": "z_7f", "depth": "zone"})["tree"][0].get("children") is None
    assert ok(servers, "Get_View", {"scope": "xx-g-xtsc"})["tree"][0]["kind"] == "task"
    assert all(m == "GET" for m, *_ in Fake.requests) and not Fake.bodies
    assert all(s is None for s in Fake.scopes)   # 读端点一律不带范围头


def test_get_view_truncates_at_500_nodes(servers, monkeypatch):
    big = {"zones": [{"id": "z", "name": "z", "address": "z"}],
           "projects": [{"id": "p", "name": "p", "address": "z-p", "zoneId": "z", "tasks": [
               {"id": f"t{i}", "name": f"n{i}", "address": f"z-p-t{i}", "done": False} for i in range(600)]}]}
    monkeypatch.setattr("fake_nexus.tree_for", lambda t: big)
    r = ok(servers, "Get_View", {"depth": "task"})
    n = sum(1 for _ in r["tree"][0]["children"][0]["children"]) + 2
    assert r["truncated"] is True and n == 500 and "更小的 scope" in r["note"]


def test_get_arrangements_passes_range_and_scope_id(servers):
    r = ok(servers, "Get_Arrangements", {"scope": "xx-g", "range": "week"})
    assert r["range"] == "today"   # 下游响应原样（假的固定回 today）
    assert "不相加" in r["note"] and r["actionable"] == []
    q = next(q for _, p, q, _ in Fake.requests if p.endswith("arrangements"))
    assert q == {"range": ["week"], "scope": ["p_3c"]}
    assert err(servers, "Get_Arrangements", {"scope": "xx-g-xtsc"})["status"] == 400   # 只收分区 / 项目


def test_get_now_both_sides(servers):
    r = ok(servers, "Get_Now")
    h = r["human"]
    assert h["state"] == "active" and h["timer"]["address"] == "xx-g-xtsc" and h["timer"]["name"] == "写提示词"
    f = h["focus"]
    assert (f["app"], f["title"], f["address"], f["recording"], f["recordedBy"]) == ("firefox", "邮件", "xx-g-xtsc", True, "timer")
    assert 1100 <= f["seconds"] <= 1400 and f["onAgentRun"] is None
    a = r["agents"][0]
    assert a == {"runId": "run_9", "agent": "codex", "label": "Pub-Coder1", "taskId": None, "address": None, "phase": "working",
                 "phaseSince": a["phaseSince"], "openSeconds": 1200, "workingSeconds": 600, "subagentsRunning": 2}
    assert r["hiddenAgents"] == 1 and "不相加" in r["note"] and "at" in r
    assert "elapsedSeconds" not in json.dumps(r)
    assert set(ok(servers, "Get_Now", {"who": "human"})) == {"at", "human", "note", "permissions"}
    only = ok(servers, "Get_Now", {"who": "agent"})
    assert set(only) == {"at", "agents", "hiddenAgents", "note", "permissions"}


def test_get_now_states_and_screen_text(servers):
    idle = ok(servers, "Get_Now", headers={"X-Nexus-Tenant": "u_idle"})
    assert idle["human"] == {"state": "unknown", "timer": None, "focus": None} and idle["agents"] == []
    assert ok(servers, "Get_Now", headers={"X-Nexus-Tenant": "u_afk"})["human"]["state"] == "afk"
    f = ok(servers, "Get_Now", headers={"X-Nexus-Tenant": "u_evil"})["human"]["focus"]
    assert f["title"].count("\n") == 0 and len(f["title"]) <= 80 and f["app"] == "fire fox"
    assert f["recording"] is False and f["recordedBy"] is None


def test_get_history_params_enrichment_and_screen(servers):
    r = ok(servers, "Get_History", {"fromDate": "2026-10-20", "toDate": "2026-10-24", "who": "human", "detail": "segments",
                                    "scope": "xx-g"})
    q = next(q for _, p, q, _ in Fake.requests if p.endswith("/history"))
    assert q == {"from": ["2026-10-20"], "to": ["2026-10-24"], "who": ["human"], "detail": ["segments"], "scope": ["p_3c"]}
    assert r["human"]["byDay"][0]["byTarget"][0]["address"] == "xx-g-xtsc"   # id 旁补 address
    seg = r["human"]["segments"][0]
    assert "\n" not in seg["window"] and len(seg["window"]) <= 80 and "‮" not in seg["window"]
    assert "不能相加" in r["note"] and "overlap" in r["note"] and "pendingSeconds" in r["note"]
    ok(servers, "Get_History", {})
    q = [q for _, p, q, _ in Fake.requests if p.endswith("/history")][-1]
    assert q == {}   # 缺省日期交给下游（今天）


@pytest.mark.parametrize("args", [
    {"fromDate": "2026-10-01", "toDate": "2027-01-01"},    # summary 93 天 > 92
    {"fromDate": "2026-10-01", "toDate": "2026-10-08", "detail": "segments"},   # 8 天 > 7
    {"fromDate": "2026-10-01", "toDate": "2026-10-08", "detail": "windows"},
    {"fromDate": "2026-10-09", "toDate": "2026-10-08"},
])
def test_get_history_span_limits_400_before_calling_downstream(servers, args):
    e = err(servers, "Get_History", args)
    assert e["status"] == 400 and e["example"]
    assert not any(p.endswith("/history") for _, p, *_ in Fake.requests)


def test_get_history_limits_ok(servers):
    ok(servers, "Get_History", {"fromDate": "2026-07-25", "toDate": "2026-10-24"})   # 92 天
    ok(servers, "Get_History", {"fromDate": "2026-10-18", "toDate": "2026-10-24", "detail": "segments"})   # 7 天


def test_get_pending_classify_windows(servers):
    r = ok(servers, "Get_Pending_Classify_Windows", {"date": "2026-10-24"})
    assert "tiersOff" not in r and r["date"] == "2026-10-24" and r["truncated"] is False
    assert [i["block"] for i in r["items"]] == ["google-chrome · docs.example.com", "firefox · 小块"]   # 只列 auto 且 pending，按秒数降序
    assert r["items"][0] == {"block": "google-chrome · docs.example.com", "app": "google-chrome", "titles": ["docs.example.com"],
                             "seconds": 2520, "records": 3, "status": "pending"}
    assert "CRUD_Windows_Classify_Rules" in r["note"] and "CRUD_Windowsblock" in r["note"]
    q = next(q for _, p, q, _ in Fake.requests if p.endswith("/blocks"))
    assert q["date"] == ["2026-10-24"] and q["status"] == ["pending"]
    assert err(servers, "Get_Pending_Classify_Windows", {"date": "tomorrow"})["status"] == 400


def test_get_pending_classify_windows_tiers_off_does_not_fetch_blocks(servers):
    r = ok(servers, "Get_Pending_Classify_Windows", {}, {"X-Nexus-Tenant": "u_tiersoff"})
    assert r["items"] == [] and r["tiersOff"] is True and "自动记录" in r["note"] and len(r["note"]) <= 100
    assert not any(p.endswith("/blocks") for _, p, *_ in Fake.requests)
    # 读不到设置（老后端）也当没开
    assert ok(servers, "Get_Pending_Classify_Windows", {}, {"X-Nexus-Tenant": "u_old"})["tiersOff"] is True


def test_get_pending_classify_windows_screens_titles(servers, monkeypatch):
    evil = {"date": "2026-10-24", "items": [{"blockKey": "x · " + EVIL, "keySource": "auto", "status": "pending", "seconds": 5,
                                              "records": 1, "apps": ["fire\nfox​"], "titles": [EVIL, EVIL, "b"]}]}
    monkeypatch.setattr("fake_nexus.BLOCKS", evil)
    it = ok(servers, "Get_Pending_Classify_Windows")["items"][0]
    assert it["app"] == "fire fox" and len(it["titles"]) == 2 and all(len(t) <= 200 and "\n" not in t for t in it["titles"])
    assert "\n" not in it["block"] and "‮" not in it["block"]


def test_get_rules_all_levels(servers):
    r = ok(servers, "Get_Rules")
    assert set(r) == {"level1", "level2", "pending", "truncated", "note", "permissions"}
    assert r["level1"]["version"] == 12 and [x["id"] for x in r["level1"]["rules"]] == ["r_1"]   # 旧式规则不列
    assert r["level1"]["rules"][0] == {"id": "r_1", "app": "google-chrome", "title": "docs\\.example\\.com", "block": "示例文档",
                                       "author": "ai", "aiAt": "2026-10-24T01:00:00+00:00"}   # 正则原样
    assert r["level1"]["ignores"] == [{"id": "ig_1", "app": "chrome", "titleContains": "银行"}]   # 没有范围头：给文字
    for hdr in (W, R):   # 外部令牌只知道有没有
        ig = ok(servers, "Get_Rules", {"level": 1}, hdr)["level1"]["ignores"]
        assert ig == [{"id": "ig_1", "app": "chrome", "hasTitleFilter": True}] and "银行" not in json.dumps(ig, ensure_ascii=False)
    assert "1 条旧规则" in r["note"]
    l2 = {x["id"]: x for x in r["level2"]}
    assert l2["br_1"] == {"id": "br_1", "block": "示例文档", "taskId": "t_a1", "projectId": "p_3c", "address": "xx-g-xtsc", "broken": False}
    assert l2["br_2"]["broken"] is True
    assert {p["kind"] for p in r["pending"]} == {"windowRule", "blockRule", "ignore"}
    wr = next(p for p in r["pending"] if p["kind"] == "windowRule")
    assert wr["proposalId"] == "nw_1" and wr["op"] == "add" and "示例文档" in wr["summary"] and "银行" not in json.dumps(r["pending"], ensure_ascii=False)


def test_get_rules_level_filter(servers):
    one = ok(servers, "Get_Rules", {"level": 1})
    assert "level1" in one and "level2" not in one and {p["kind"] for p in one["pending"]} == {"windowRule", "ignore"}
    two = ok(servers, "Get_Rules", {"level": "2"})
    assert "level2" in two and "level1" not in two and [p["kind"] for p in two["pending"]] == ["blockRule"]


def test_get_proposals_kinds_status_and_order(servers):
    r = ok(servers, "Get_Proposals", {"status": "all"})
    kinds = {i["kind"] for i in r["items"]}
    assert kinds == {"project", "task", "change", "ignore", "windowRule", "blockRule", "blockAction", "sessionChange", "question",
                     "report", "direct"}
    times = [i["createdAt"] for i in r["items"]]
    assert times == sorted(times, reverse=True)
    st = {i["id"]: i["status"] for i in r["items"]}
    assert st["dl_1"] == "applied" and st["dl_2"] == "undone" and st["na_1"] == "rejected" and st["ns_1"] == "superseded"
    assert st["aq_1"] == "answered" and st["rp_1"] == "approved"
    q = next(i for i in r["items"] if i["kind"] == "question")
    assert q["answer"]["recorded"] is True and q["answer"]["eventId"] == "evt_9" and "‮" not in q["answer"]["text"]
    assert q["decidedAt"] == "2026-10-24T10:05:00+08:00"
    assert r["secretaryReports"] == [{"date": "2026-10-24", "updatedAt": "2026-10-24T12:00:00+08:00", "revision": 2}]
    nt = next(i for i in r["items"] if i["id"] == "nt_2")   # 项目提议下挂的任务提议也摊平
    assert nt["kind"] == "task"
    assert next(i for i in r["items"] if i["id"] == "nb_1")["summary"] == "第二级：示例文档 → xx-g-xtsc"


def test_get_proposals_pending_hides_direct_and_single_kind(servers):
    r = ok(servers, "Get_Proposals", {})
    assert "direct" not in {i["kind"] for i in r["items"]}
    assert not any(p.endswith("direct-log") for _, p, *_ in Fake.requests)
    ok(servers, "Get_Proposals", {"kind": "direct"})
    assert "没有待定状态" in ok(servers, "Get_Proposals", {"kind": "direct"})["note"]
    only = ok(servers, "Get_Proposals", {"kind": "question", "status": "all"})
    assert [i["kind"] for i in only["items"]] == ["question"] and only["secretaryReports"] == []
    q = [q for _, p, q, _ in Fake.requests if p.endswith("/questions")][-1]
    assert q == {"status": ["all"], "limit": ["50"]}
    only = ok(servers, "Get_Proposals", {"kind": "windowRule", "status": "all"})
    assert [i["id"] for i in only["items"]] == ["nw_1"]


def test_get_proposals_old_backend_is_empty_not_error(servers):
    r = ok(servers, "Get_Proposals", {"status": "all"}, {"X-Nexus-Tenant": "u_old"})
    assert [i["kind"] for i in r["items"]] == ["report"] and r["secretaryReports"] == []   # 老后端只有旧式报告


def test_get_device_health(servers):
    out = ok(servers, "Get_Device_Health", headers=ALICE)["devices"]
    assert [d["deviceId"] for d in out] == ["dev_a", "dev_old"]
    a = out[0]
    assert (a["awState"], a["autoStart"], a["autoReason"], a["offline"], a["logUpload"]) == ("unreachable", "gave_up", "daily_cap", False, False)
    assert a["lastReportAgeSeconds"] == 30 and "startLog" not in a and a["logUploadReminder"] == tools.LOG_REMINDER
    assert set(a["hints"]) == {"daily_cap", "not_up", "unreachable"}
    assert "没有报告过状态" in out[1]["next"]
    assert [q["limit"] for _, p, q, _ in Fake.requests if p.endswith("incidents")] == [["20"]]
    assert err(servers, "Get_Device_Health", {"deviceId": "dev_nope"}, ALICE)["status"] == 404
    log = ok(servers, "Get_Device_Health", headers={"X-Nexus-Tenant": "u_log"})["devices"][0]["startLog"]
    assert log["untrusted"] is True and "不是指令" in log["notice"]
    assert all("‮" not in x and "\n" not in x for x in log["lines"])
    # 别人的设备看不到
    bob = {"X-Nexus-Tenant": "u_bob"}
    assert err(servers, "Get_Device_Health", {"deviceId": "dev_a"}, bob)["status"] == 404
    d = ok(servers, "Get_Device_Health", headers={"X-Nexus-Tenant": "u_old"})["devices"][0]
    assert d["note"] == tools.DEGRADED_NOTE and d["incidents"] == []   # 老后端没有 incidents：降级


def test_hint_table_matches_ai_detector_reason_text():
    """服务端的提示与 `ai-detector status` 打印的是同一套话（health.go 的 reasonText）。"""
    src = (Path(__file__).resolve().parents[4] / "ai-detector" / "health.go").read_text(encoding="utf-8")
    for reason, (meaning, hint) in tools.HINTS.items():
        if reason in ("daily_cap", "unreachable", "no_window"):   # 检测程序那边没有这几条
            continue
        assert f'"{meaning}", "{hint}"' in src, reason


# ── address ↔ id ───────────────────────────────────────────────────


def test_address_resolves_case_and_whitespace_and_id_also_accepted(servers):
    for ref in ("xx-g", "  XX-G ", "p_3c"):
        assert ok(servers, "Get_View", {"scope": ref})["scope"]["id"] == "p_3c", ref


def test_address_not_found_lists_up_to_5_candidates(servers):
    e = err(servers, "Get_View", {"scope": "xx-gg"})
    assert e["status"] == 400 and "找不到" in e["detail"] and "相近的" in e["detail"]
    assert "xx-g（garden）" in e["detail"] and e["detail"].count("（") <= 5 + 1
    e = err(servers, "Get_View", {"scope": "zzzzzz"})
    assert e["status"] == 400 and "找不到" in e["detail"]


def test_address_ambiguous_lists_candidates(servers):
    e = err(servers, "Get_View", {"scope": "xx-g"}, {"X-Nexus-Tenant": "u_dup"})
    assert e["status"] == 400 and "对应多个" in e["detail"] and "p_3c" not in e["detail"].split("：")[0] and "xx-g（garden）" in e["detail"] \
        and "xx-g（刚建的）" in e["detail"]
    assert ok(servers, "Get_View", {"scope": "p_new"}, {"X-Nexus-Tenant": "u_dup"})["scope"]["id"] == "p_new"   # id 照常


def test_target_kind_is_checked_by_segments(servers):
    e = err(servers, "CRUD_Honeycomb", {"op": "create", "kind": "task", "name": "n", "to": "xx"}, W)
    assert e["status"] == 400 and "是分区" in e["detail"]
    e = err(servers, "CRUD_Honeycomb", {"op": "rename", "target": "xx", "name": "n"}, W)
    assert e["status"] == 400 and "是分区" in e["detail"]


def test_tenant_isolation_for_trees(servers):
    alice = ok(servers, "Get_View", {"depth": "task"}, ALICE)
    assert "alice任务" in json.dumps(alice, ensure_ascii=False)
    bob = ok(servers, "Get_View", {"depth": "task"}, {"X-Nexus-Tenant": "u_bob"})
    assert "alice" not in json.dumps(bob, ensure_ascii=False)
    assert err(servers, "Get_View", {"scope": "ae-ax"}, {"X-Nexus-Tenant": "u_bob"})["status"] == 400   # 别人的 address 在这里不存在
    assert {t for *_, t in Fake.requests} == {"u_alice", "u_bob"}


# ── 写工具：转发范围、提议、直接施加 ───────────────────────────────

RULE_ADD = {"ops": [{"op": "add", "app": "google-chrome", "title": "docs", "block": "示例文档"}], "reason": "每天都开"}


def test_scope_header_forwarded_only_to_write_endpoints_and_only_for_write_scope(servers):
    ok(servers, "CRUD_Windows_Classify_Rules", RULE_ADD, W)
    ok(servers, "CRUD_Windowsblock_Classify_Rules", {"ops": [{"op": "add", "block": "b", "target": "xx-g"}]}, W)
    sent = list(zip(Fake.requests, Fake.scopes))
    assert any(m == "POST" for (m, *_), _ in sent)
    for (m, p, *_), s in sent:
        assert s == ("write" if m == "POST" else None), (m, p, s)   # GET 一律不带；POST 在 write 范围带
    Fake.requests.clear()
    Fake.scopes.clear()
    ok(servers, "CRUD_Windows_Classify_Rules", RULE_ADD)   # 没有范围头
    assert any(m == "POST" for m, *_ in Fake.requests) and all(s is None for s in Fake.scopes)
    assert all(a is None for _, a in Fake.bodies)   # 不带 Authorization


def test_direct_apply_without_scope_is_forwarded_unscoped_and_gets_need_write_scope(servers):
    out = ok(servers, "CRUD_Windows_Classify_Rules", {**RULE_ADD, "direct_apply": True})   # 没有范围头
    r = out["results"][0]
    assert r["ok"] is False and r["status"] == 403 and "write 范围" in r["detail"] and "write 范围" in out["note"]
    posts_ = [(s) for (m, *_), s in zip(Fake.requests, Fake.scopes) if m == "POST"]
    assert posts_ == [None]   # 照常转给 nexus-core，没带范围头
    assert Fake.bodies[0][0]["directApply"] is True


def test_direct_apply_with_write_scope_succeeds(servers):
    out = ok(servers, "CRUD_Windows_Classify_Rules", {**RULE_ADD, "direct_apply": True}, W)
    r = out["results"][0]
    assert r["ok"] is True and r["applied"] is True and r["directLogId"].startswith("dl_") and "proposalId" not in r
    assert "撤回" in out["note"] and out["permissions"]["canDirectApply"]["windowsClassify"] is True
    assert posts()[0][1]["directApply"] is True
    # 缺省不带 directApply
    ok(servers, "CRUD_Windows_Classify_Rules", RULE_ADD, W)
    assert "directApply" not in posts()[1][1]


def test_direct_apply_403_mid_batch_stops_and_reports_done_items(servers):
    h = {**W, "X-Nexus-Tenant": "u_off"}
    out = ok(servers, "CRUD_Windowsblock", {"items": [{"action": "generic", "block": "a"}, {"action": "generic", "block": "b"},
                                                       {"action": "generic", "block": "c"}], "direct_apply": True}, h)
    assert [r["ok"] for r in out["results"]] == [False]   # 第一条就 403：停止，后面的没发
    assert out["results"][0]["status"] == 403 and "总开关" in out["results"][0]["detail"] and "自动记录" in out["results"][0]["detail"]
    assert len(posts()) == 1 and "被拒" in out["note"] and "总开关" in out["note"] and len(out["note"]) <= 100
    # 中途才 403：前面做完的保留
    Fake.requests.clear(); Fake.bodies.clear(); Fake.scopes.clear()
    real = Fake.override
    calls = {"n": 0}
    import fake_nexus
    orig = fake_nexus.post_result

    def flaky(path, q, tenant, body, scope):
        calls["n"] += 1
        return orig(path, q, tenant, body, scope) if calls["n"] < 3 else (403, {"detail": "x", "reason": "windowsClassifyOff"})
    fake_nexus.post_result = flaky
    try:
        out = ok(servers, "CRUD_Windowsblock", {"items": [{"action": "generic", "block": b} for b in "abcd"], "direct_apply": True}, W)
    finally:
        fake_nexus.post_result = orig
    assert [r["ok"] for r in out["results"]] == [True, True, False] and out["results"][2]["status"] == 403
    assert "2 条成功后第 3 条被拒" in out["note"] and "窗口分类" in out["note"] and real is None


def test_read_scope_cannot_call_write_tools(servers):
    for name in WRITES:
        e = err(servers, name, {"text": "x", "ops": [], "items": [], "questions": [], "op": "delete", "target": "xx-g"}, R)
        assert e["status"] == 403 and "write" in e["detail"], name
    assert not Fake.bodies and {m for m, *_ in Fake.requests} <= {"GET"}
    # 读工具照常；批量里夹着写工具只拒那一条
    assert ok(servers, "Get_View", {}, R)["tree"]
    status, out = post(servers, [
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "Submit_Report", "arguments": {"text": "x"}}},
        {"jsonrpc": "2.0", "id": 2, "method": "ping"}], R)
    assert status == 200 and out[0]["result"]["isError"] is True and out[1]["result"] == {}


def test_windows_classify_rules_per_item_and_partial_success(servers):
    out = ok(servers, "CRUD_Windows_Classify_Rules", {"ops": [
        {"op": "add", "app": "a", "block": "好"},
        {"op": "add", "app": "a", "block": "CONFLICT"},    # 下游 409
        {"op": "edit", "id": "r_1", "block": "新块名"},
        {"op": "edit", "id": "r_1"},                         # 没有要改的键
        {"op": "delete", "id": "ig_1"},                      # 忽略规则的 id
        {"op": "add", "ignore": True, "app": "chrome", "title": "银行"},
        {"op": "add", "ignore": True, "block": "x", "app": "a"},   # 忽略不要 block
        {"op": "add", "block": "只有块名"},                  # 缺 app / title
        {"op": "delete"}]}, W)
    rs = out["results"]
    assert [r["index"] for r in rs] == list(range(9))
    assert [r["ok"] for r in rs] == [True, False, True, False, False, True, False, False, False]
    assert rs[0]["status"] == "pending" and rs[0]["proposalId"].startswith("n_")
    assert rs[1]["status"] == 409 and "通用窗口" in rs[1]["detail"]
    assert rs[4]["status"] == 400 and "只有人" in rs[4]["detail"]
    assert "3 条已提议" in out["note"] and "6 条失败" in out["note"]
    sent = [(p.rsplit("/", 1)[1], b) for p, b in posts()]
    assert sent[0] == ("window-rules", {"op": "add", "rule": {"app": "a", "block": "好"}})
    assert sent[2] == ("window-rules", {"op": "edit", "rule": {"id": "r_1", "block": "新块名"}})
    assert ("ignores", {"app": "chrome", "titleContains": "银行"}) in sent   # 忽略规则走另一个端点
    assert len(sent) == 4   # 3 条成功 + 1 条 409；其余没发出去


def test_windows_classify_rules_delete_and_reason(servers):
    ok(servers, "CRUD_Windows_Classify_Rules", {"ops": [{"op": "delete", "id": "r_1"}], "reason": "没用了"}, W)
    assert posts()[0][1] == {"op": "delete", "rule": {"id": "r_1"}, "reason": "没用了"}


def test_block_rules_resolve_targets_and_delete(servers):
    out = ok(servers, "CRUD_Windowsblock_Classify_Rules", {"ops": [
        {"op": "add", "block": "示例文档", "target": "xx-g-xtsc"},
        {"op": "edit", "block": "示例文档", "target": "xx-g"},
        {"op": "delete", "block": "示例文档"},
        {"op": "delete", "block": "x", "target": "xx-g"},
        {"op": "add", "block": "x"},
        {"op": "add", "block": "x", "target": "nope-nope"}]}, W)
    assert [r["ok"] for r in out["results"]] == [True, True, True, False, False, False]
    assert "相近" in out["results"][5]["detail"] or "找不到" in out["results"][5]["detail"]
    bodies = [b for _, b in posts()]
    assert bodies == [{"op": "add", "blockKey": "示例文档", "taskId": "t_a1"},
                      {"op": "edit", "blockKey": "示例文档", "projectId": "p_3c"},
                      {"op": "delete", "blockKey": "示例文档"}]
    assert {p for p, _ in posts()} == {"/api/core/activity/proposals/block-rules"}


def test_windowsblock_actions_and_split_conversion(servers):
    out = ok(servers, "CRUD_Windowsblock", {"items": [
        {"action": "assign", "date": "2026-10-23", "block": "示例文档", "target": "xx-g-xtsc"},
        {"action": "generic", "block": "通用"},
        {"action": "ignore", "block": "噪声", "date": "2026-10-24"},
        {"action": "split", "block": "混合", "parts": [
            {"from": "2026-10-24T11:00:00+08:00", "to": "2026-10-24T12:00:00+08:00", "target": "xx-g"},
            {"from": "2026-10-24T09:00:00+08:00", "to": "2026-10-24T11:00:00+08:00", "target": "xx-g-xtsc"}]},
        {"action": "assign", "block": "缺目标"},
        {"action": "generic", "block": "b", "target": "xx-g"},
        {"action": "split", "block": "有空隙", "parts": [
            {"from": "2026-10-24T09:00:00+08:00", "to": "2026-10-24T10:00:00+08:00", "target": "xx-g"},
            {"from": "2026-10-24T10:30:00+08:00", "to": "2026-10-24T12:00:00+08:00", "target": "xx-g"}]},
        {"action": "split", "block": "没偏移", "parts": [
            {"from": "2026-10-24T09:00:00", "to": "2026-10-24T10:00:00", "target": "xx-g"},
            {"from": "2026-10-24T10:00:00", "to": "2026-10-24T12:00:00", "target": "xx-g"}]}]}, W)
    assert [r["ok"] for r in out["results"]] == [True, True, True, True, False, False, False, False]
    assert "拆块不是普通增删改" in out["note"] and "只中间切点起作用" in out["note"] and len(out["note"]) <= 100
    b = [x for _, x in posts()]
    eleven = int(datetime(2026, 10, 24, 11, tzinfo=timezone(timedelta(hours=8))).timestamp() * 1000)
    assert b[0] == {"action": "assign", "date": "2026-10-23", "blockKey": "示例文档", "taskId": "t_a1"}
    assert b[1]["action"] == "generic" and b[1]["date"] and b[1]["blockKey"] == "通用"
    assert b[3]["parts"] == [
        {"fromTs": None, "toTs": eleven, "taskId": "t_a1"},     # 09:00–11:00（+08:00），首段开头放开
        {"fromTs": eleven, "toTs": None, "projectId": "p_3c"}]   # 11:00–12:00，末段结尾放开
    assert "gap" not in json.dumps(b) and "间隙" not in out["note"]
    assert "首尾相接" in out["results"][6]["detail"] and "偏移" in out["results"][7]["detail"]
    assert {p for p, _ in posts()} == {"/api/core/activity/proposals/block-actions"}


def test_honeycomb_create_never_direct_and_duplicate_name_note(servers):
    e = err(servers, "CRUD_Honeycomb", {"op": "create", "kind": "task", "name": "新任务", "to": "xx-g", "direct_apply": True}, W)
    assert e["status"] == 400 and "direct_apply" in e["detail"] and e["example"] and not Fake.bodies
    r = ok(servers, "CRUD_Honeycomb", {"op": "create", "kind": "task", "name": "新任务", "to": "xx-g", "reason": "拆出来"}, W)
    assert r["result"]["ok"] is True and r["result"]["status"] == "pending" and "批准" in r["note"] and "同名" not in r["note"]
    assert posts()[0] == ("/api/core/activity/proposals/tasks", {"projectId": "p_3c", "name": "新任务", "reason": "拆出来"})
    r = ok(servers, "CRUD_Honeycomb", {"op": "create", "kind": "task", "name": " 写提示词 ", "to": "p_3c"}, W)
    assert r["note"].startswith("已有同名任务，要不要改为「") and "写提示词2」" in r["note"]
    assert len(posts()) == 2   # 提议照常提交
    r = ok(servers, "CRUD_Honeycomb", {"op": "create", "kind": "project", "name": "GARDEN", "to": "xx"}, W)
    assert "已有同名项目" in r["note"] and "GARDEN2" in r["note"]
    assert posts()[2] == ("/api/core/activity/proposals/projects", {"zoneId": "z_7f", "name": "GARDEN"})
    assert len(r["note"]) <= 100


def test_honeycomb_rename_move_delete_retime(servers):
    ok(servers, "CRUD_Honeycomb", {"op": "rename", "target": "xx-g-xtsc", "name": "写系统提示词", "reason": "更准"}, W)
    ok(servers, "CRUD_Honeycomb", {"op": "move", "target": "xx-g-xtsc", "to": "xx-gjd"}, W)
    ok(servers, "CRUD_Honeycomb", {"op": "move", "target": "xx-g", "to": "kf"}, W)
    ok(servers, "CRUD_Honeycomb", {"op": "delete", "target": "xx-g"}, W)
    r = ok(servers, "CRUD_Honeycomb", {"op": "retime", "eventId": "evt_7", "to": "xx-g-xtsc"}, W)
    assert r["result"]["status"] == "pending"
    b = [x for _, x in posts()]
    assert b[0] == {"target": "task", "targetId": "t_a1", "op": "rename", "name": "写系统提示词", "reason": "更准"}
    assert b[1] == {"target": "task", "targetId": "t_a1", "op": "move", "toId": "p_new"}
    assert b[2] == {"target": "project", "targetId": "p_3c", "op": "move", "toId": "z_e"}
    assert b[3] == {"target": "project", "targetId": "p_3c", "op": "delete"}
    assert posts()[4] == ("/api/core/activity/proposals/sessions", {"eventId": "evt_7", "op": "reassign", "taskId": "t_a1"})
    # 缺参数
    assert err(servers, "CRUD_Honeycomb", {"op": "move", "target": "xx-g"}, W)["status"] == 400
    assert err(servers, "CRUD_Honeycomb", {"op": "retime", "to": "xx-g"}, W)["status"] == 400
    assert err(servers, "CRUD_Honeycomb", {"op": "move", "target": "xx-g-xtsc", "to": "kf"}, W)["status"] == 400   # 任务只能搬到项目
    r = ok(servers, "CRUD_Honeycomb", {"op": "rename", "target": "xx-g-xtsc", "name": "任务1"}, W)
    assert "同名任务" in r["note"] and "任务12」" in r["note"]
    r = ok(servers, "CRUD_Honeycomb", {"op": "rename", "target": "xx-g-xtsc", "name": "写提示词"}, W)   # 改成自己的名字不算重名
    assert "同名" not in r["note"]


def test_honeycomb_direct_apply_for_changes_and_retime(servers):
    r = ok(servers, "CRUD_Honeycomb", {"op": "rename", "target": "xx-g-xtsc", "name": "x", "direct_apply": True}, W)
    assert r["result"]["applied"] is True and r["result"]["directLogId"] and "撤回" in r["note"]
    r = ok(servers, "CRUD_Honeycomb", {"op": "retime", "eventId": "e", "to": "xx-g", "direct_apply": True}, W)
    assert r["result"]["applied"] is True
    assert all(b["directApply"] is True for _, b in posts())
    e = err(servers, "CRUD_Honeycomb", {"op": "delete", "target": "xx-g", "direct_apply": True})   # 没范围头
    assert e["status"] == 403 and "write 范围" in e["detail"]


def test_honeycomb_downstream_errors(servers):
    Fake.override = (409, {"detail": "目标与当前归属相同"})
    e = err(servers, "CRUD_Honeycomb", {"op": "retime", "eventId": "e", "to": "xx-g"}, W)
    assert (e["status"], e["detail"]) == (409, "目标与当前归属相同")


def test_submit_report_default_date_and_replace(servers):
    r = ok(servers, "Submit_Report", {"text": "上午写 MCP"}, W)
    assert r["result"] == {"ok": True, "id": "sr_1", "date": r["result"]["date"], "revision": 2} and "整份替换" in r["note"]
    assert posts()[0] == ("/api/core/activity/secretary-reports", {"text": "上午写 MCP"})   # 日期缺省交给下游（今天）
    ok(servers, "Submit_Report", {"date": "2026-10-23", "text": "x"}, W)
    assert posts()[1][1] == {"date": "2026-10-23", "text": "x"}
    assert err(servers, "Submit_Report", {"date": "yesterday", "text": "x"}, W)["status"] == 400
    assert err(servers, "Submit_Report", {"text": "x" * 8001}, W)["status"] == 400


def test_submit_questions_per_item_and_target_resolution(servers):
    out = ok(servers, "Submit_Questions_To_Human", {"questions": [
        {"title": "上午做了什么", "from": "2026-10-24T10:00:00+08:00", "to": "2026-10-24T11:00:00+08:00", "allowText": True,
         "options": [{"label": "写 MCP", "target": "xx-g-xtsc"}, {"label": "整个项目", "target": "p_3c"}, {"label": "不记", "ignore": True}]},
        {"title": "坏目标", "options": [{"label": "x", "target": "nope-nope"}]},
        {"title": "又坏", "options": [{"label": "x", "target": "xx-g", "ignore": True}]},
        {"title": "简单"}]}, W)
    assert [r["ok"] for r in out["results"]] == [True, False, False, True]
    assert out["results"][0]["status"] == "pending" and out["results"][0]["questionId"].startswith("aq_")
    sent = [b["questions"][0] for _, b in posts()]
    assert len(sent) == 2
    assert sent[0]["options"] == [{"label": "写 MCP", "target": {"taskId": "t_a1"}}, {"label": "整个项目", "target": {"projectId": "p_3c"}},
                                  {"label": "不记", "ignore": True}]
    assert sent[0]["allowText"] is True and sent[1] == {"title": "简单"}
    assert "Get_Proposals(kind=question)" in out["note"]
    assert err(servers, "Submit_Questions_To_Human", {"questions": [{"title": "q"}] * 11}, W)["status"] == 400


def test_write_tools_have_no_direct_apply_param_for_submit(servers):
    for name in ("Submit_Report", "Submit_Questions_To_Human"):
        assert "direct_apply" not in tools.TOOLS[name]["inputSchema"]["properties"]
        assert "reason" not in tools.TOOLS[name]["inputSchema"]["properties"]
    for name in ("CRUD_Windows_Classify_Rules", "CRUD_Windowsblock_Classify_Rules", "CRUD_Windowsblock", "CRUD_Honeycomb"):
        assert {"direct_apply", "reason"} <= set(tools.TOOLS[name]["inputSchema"]["properties"])


def test_only_registered_endpoints_and_methods(servers):
    """跑一遍全部工具：发出去的方法 + 路径都在契约第三、四节登记的表里；POST 只来自写工具。"""
    test_every_success_has_note_and_permissions(servers)
    for m, p, *_ in Fake.requests:
        assert p in REGISTERED[m], (m, p)
    import inspect  # noqa: PLC0415

    src = inspect.getsource(tools)
    assert src.count("urllib.request.Request(") == 2   # 只有 _get / _post 两处出请求
    assert {n for n, fn in tools._HANDLERS.items() if "_write(" in inspect.getsource(fn)} <= set(WRITES)


# ── v1 名字：迁移说明 ──────────────────────────────────────────────

V1_CASES = {
    "get_task_tree": ({"includeDone": True, "limit": 5}, "Get_View", {"depth": "task", "includeDone": True}),
    "list_projects": ({}, "Get_View", {"depth": "project"}),
    "get_next_actions": ({}, "Get_Arrangements", {}),
    "get_weekly_review": ({}, "Get_Arrangements", {"range": "week"}),
    "get_current_timer": ({}, "Get_Now", {}),
    "list_time_sessions": ({"from": "2026-10-20T00:00:00+08:00", "to": "2026-10-24T23:00:00+08:00"}, "Get_History",
                           {"who": "human", "detail": "segments", "fromDate": "2026-10-20", "toDate": "2026-10-24"}),
    "get_daily_time": ({"fromDate": "2026-10-24", "toDate": "2026-10-24"}, "Get_History",
                       {"fromDate": "2026-10-24", "toDate": "2026-10-24", "who": "human"}),
    "get_agent_time": ({"fromDate": "2026-10-24", "toDate": "2026-10-24"}, "Get_History",
                       {"fromDate": "2026-10-24", "toDate": "2026-10-24", "who": "agent"}),
    "list_activity_suggestions": ({"status": "pending"}, "Get_Pending_Classify_Windows", {}),
    "get_match_history": ({}, "Get_Pending_Classify_Windows", {}),
    "get_window_awaiting_target": ({}, "Get_Pending_Classify_Windows", {}),
    "get_detector_rules": ({}, "Get_Rules", {}),
    "get_report_status": ({}, "Get_Proposals", {"kind": "report"}),
    "get_detector_health": ({"deviceId": "dev_a", "limit": 5}, "Get_Device_Health", {"deviceId": "dev_a", "limit": 5}),
    "propose_detector_rules": ({"rules": [{"app": "x", "taskId": "t"}], "summary": "s"}, "CRUD_Windows_Classify_Rules", {}),
    "apply_detector_rules": ({"rules": [], "summary": "s"}, "CRUD_Windows_Classify_Rules", {}),
    "propose_ignore_rule": ({"app": "chrome", "titleContains": "银行", "reason": "隐私"}, "CRUD_Windows_Classify_Rules",
                            {"ops": [{"op": "add", "ignore": True, "app": "chrome", "title": "银行"}], "reason": "隐私"}),
    "suggest_window_target": ({"key": "k", "none": True, "reason": "r"}, "CRUD_Windows_Classify_Rules", {}),
    "propose_activity_matches": ({"matches": []}, "CRUD_Windowsblock", {}),
    "propose_report": ({"summary": "归类 8 段", "items": []}, "Submit_Report", {"text": "归类 8 段"}),
    "propose_project": ({"zoneId": "z_7f", "name": "新", "reason": "r"}, "CRUD_Honeycomb",
                        {"op": "create", "kind": "project", "to": "z_7f", "name": "新", "reason": "r"}),
    "propose_task": ({"projectId": "p_3c", "name": "新", "reason": "r"}, "CRUD_Honeycomb",
                     {"op": "create", "kind": "task", "to": "p_3c", "name": "新", "reason": "r"}),
    "propose_change": ({"target": "task", "targetId": "t_a1", "op": "move", "toId": "p_new", "reason": "r"}, "CRUD_Honeycomb",
                       {"op": "move", "target": "t_a1", "to": "p_new", "reason": "r"}),
}


def test_v1_cases_cover_all_23_names():
    assert set(V1_CASES) == set(v1_migration.NAMES) and len(V1_CASES) == 23


@pytest.mark.parametrize("name", sorted(V1_CASES))
def test_v1_name_is_hidden_callable_and_returns_only_a_pointer(servers, name):
    args, use, converted = V1_CASES[name]
    live = name in ("get_window_awaiting_target", "suggest_window_target")   # 没有范围头时照旧可用（见 live 测试）
    for hdr in ((W, R) if live else ({}, W, R)):   # 任何范围都一样：不调下游、不回数据（read 范围也不挡迁移说明）
        res = call(servers, name, args, hdr)
        assert res["isError"] is False
        sc = res["structuredContent"]
        assert sc["deprecated"] is True and sc["use"] == use
        assert sc["call"]["name"] == use and sc["call"]["arguments"] == converted
        assert name in sc["note"] and use in sc["note"]
        assert set(sc) == {"deprecated", "use", "call", "note"}
        # 换好的参数能原样喂给新工具
        if use in tools.TOOLS and sc["call"]["arguments"]:
            tools._check(tools.TOOLS[use]["inputSchema"], sc["call"]["arguments"], "")
    assert Fake.requests == [] and Fake.bodies == []   # 一次下游请求都没有
    assert name not in names(servers, W)


def test_v1_migration_survives_junk_arguments(servers):
    assert ok(servers, "get_daily_time", {"fromDate": 1})["call"] == {"name": "Get_History", "arguments": {"who": "human"}}
    res = rpc(servers, "tools/call", {"name": "propose_change", "arguments": ["not", "an", "object"]})["result"]
    assert res["isError"] is False and res["structuredContent"]["use"] == "CRUD_Honeycomb"
    assert rpc(servers, "tools/call", {"name": "nope_tool"})["error"]["code"] == -32602


def test_v1_example_from_contract(servers):
    sc = ok(servers, "get_daily_time", {"fromDate": "2026-10-24", "toDate": "2026-10-24"})
    assert sc["call"] == {"name": "Get_History", "arguments": {"fromDate": "2026-10-24", "toDate": "2026-10-24", "who": "human"}}


def test_get_history_single_date_still_checks_span(servers):
    e = err(servers, "Get_History", {"fromDate": "2020-01-01"})   # 另一端是今天：远超 92 天
    assert e["status"] == 400 and not any(p.endswith("/history") for _, p, *_ in Fake.requests)
    assert err(servers, "Get_History", {"toDate": "2020-01-01"})["status"] == 400   # from(今天) 晚于 to


def test_settings_failure_is_cached_briefly_and_expired_entries_are_dropped(servers, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(tools, "_clock", lambda: clock[0])
    h = {"X-Nexus-Tenant": "u_old"}
    n = lambda: sum(1 for _, p, *_ in Fake.requests if p.endswith("/settings"))  # noqa: E731
    ok(servers, "Get_View", {}, h); ok(servers, "Get_View", {}, h)
    assert n() == 1   # 失败也缓存
    clock[0] += 6
    ok(servers, "Get_View", {}, h)
    assert n() == 2
    clock[0] += 100
    ok(servers, "Get_View", {}, W)   # 插入新租户时清掉过期的 u_old
    assert "u_old" not in tools._SETTINGS


def test_history_screens_apps_label_and_agent(servers, monkeypatch):
    h = {"range": {}, "human": {"blocks": [{"blockKey": "x", "apps": ["fire\nfox\u200b", "b"]}]},
         "agents": {"byAgent": [{"agent": "a\nb" + "x" * 100, "label": "L\u202e" + "y" * 100, "workingSeconds": 1}]}}
    monkeypatch.setattr("fake_nexus.HISTORY", h)
    r = ok(servers, "Get_History", {"detail": "windows"})
    assert r["human"]["blocks"][0]["apps"] == ["fire fox", "b"]
    ag = r["agents"]["byAgent"][0]
    assert "\n" not in ag["agent"] and len(ag["agent"]) <= 64 and len(ag["label"]) <= 64 and "\u202e" not in ag["label"]


def test_live_v1_window_tools_for_no_scope_callers_only(servers):
    assert LIVE == names(servers)[-2:] and not set(LIVE) & set(names(servers, W)) and not set(LIVE) & set(names(servers, R))
    for t in rpc(servers, "tools/list")["result"]["tools"][-2:]:
        assert t["inputSchema"]["additionalProperties"] is False and t["annotations"]["readOnlyHint"] is False
    r = ok(servers, "get_window_awaiting_target", {}, ALICE)   # 没有范围头：照旧可用
    w = r["window"]
    assert w["key"].startswith("wk_") and "internal" not in w and "\n" not in w["title"] and len(w["title"]) <= 200
    assert any(m == "POST" and p.endswith("/ai/claim") for m, p, *_ in Fake.requests)
    assert ok(servers, "get_window_awaiting_target", {}, {"X-Nexus-Tenant": "u_idle"})["window"] is None
    s = ok(servers, "suggest_window_target", {"key": "wk_x", "projectId": "p_3c", "confidence": 0.9, "reason": "r"}, ALICE)
    assert s["outcome"] == "suggested" and "已生效" in s["next"]
    assert posts()[-1] == ("/api/core/activity/ai/suggest", {"key": "wk_x", "projectId": "p_3c", "confidence": 0.9, "reason": "r"})
    assert all(sc is None for sc in Fake.scopes)   # 没有范围头：不转发
    assert err_plain(servers, "suggest_window_target", {"key": "k"})["status"] == 400   # 缺 reason
    # read / write 外部调用方：只回迁移说明，不调下游
    Fake.requests.clear(); Fake.bodies.clear()
    for hdr in (W, R):
        sc = ok(servers, "get_window_awaiting_target", {}, hdr)
        assert sc["deprecated"] is True and sc["use"] == "Get_Pending_Classify_Windows"
    assert Fake.requests == []


def err_plain(url, name, args, headers=None):
    res = call(url, name, args, headers)
    assert res["isError"] is True
    return res["structuredContent"]["error"]


def test_edit_rule_can_clear_title_with_empty_string(servers):
    out = ok(servers, "CRUD_Windows_Classify_Rules", {"ops": [{"op": "edit", "id": "r_1", "title": ""}]}, W)
    assert out["results"][0]["ok"] is True
    assert posts()[0][1]["rule"] == {"id": "r_1", "title": ""}


def test_block_write_maps_screened_name_back_to_exact_key(servers, monkeypatch):
    raw = "a  b" + "x" * 5
    monkeypatch.setattr("fake_nexus.BLOCK_RULES", {"items": [{"id": "br_9", "blockKey": raw, "taskId": "t_a1"}]})
    ok(servers, "CRUD_Windowsblock_Classify_Rules", {"ops": [{"op": "delete", "block": "a b" + "x" * 5}]}, W)
    assert posts()[0][1] == {"op": "delete", "blockKey": raw}


def test_error_detail_does_not_leak_raw_block_key(servers, monkeypatch):
    raw = "ECHO  a  b"
    monkeypatch.setattr("fake_nexus.BLOCK_RULES", {"items": [{"id": "br_9", "blockKey": raw, "taskId": "t_a1"}]})
    out = ok(servers, "CRUD_Windowsblock_Classify_Rules", {"ops": [{"op": "delete", "block": "ECHO a b"}]}, W)
    detail = out["results"][0]["detail"]
    assert out["results"][0]["ok"] is False and raw not in detail and "ECHO a b" in detail


def test_block_mapping_skips_collisions(servers, monkeypatch):
    rules = [{"id": "1", "blockKey": "a  b", "taskId": "t_a1"}, {"id": "2", "blockKey": "a   b", "taskId": "t_a1"},
             {"id": "3", "blockKey": "c d", "taskId": "t_a1"}, {"id": "4", "blockKey": "c  d", "taskId": "t_a1"}]
    monkeypatch.setattr("fake_nexus.BLOCK_RULES", {"items": rules})
    ok(servers, "CRUD_Windowsblock_Classify_Rules", {"ops": [{"op": "delete", "block": "a b"}, {"op": "delete", "block": "c d"}]}, W)
    assert [b["blockKey"] for _, b in posts()] == ["a b", "c d"]   # 两个原样 key 同显示形式 / 显示形式本身是原样 key：都不猜


def test_error_detail_caps_long_raw_block_key(servers, monkeypatch):
    raw = "ECHO " + "x" * 450 + "SECRETTAIL" + "y" * 300
    monkeypatch.setattr("fake_nexus.BLOCK_RULES", {"items": [{"id": "br_9", "blockKey": raw, "taskId": "t_a1"}]})
    shown = tools._screen(raw, tools.MAX_BLOCK)
    out = ok(servers, "CRUD_Windowsblock_Classify_Rules", {"ops": [{"op": "delete", "block": shown}]}, W)
    assert "SECRETTAIL" not in out["results"][0]["detail"] and "SECRETTAIL" not in shown


def test_fail_screens_downstream_errors_list():
    raw = "a‮" + "x" * 450 + "SECRET"
    e = tools.ToolError(422, "bad", errors=[{"loc": ["body", "blockKey"], "msg": raw, "input": raw}])
    errs = tools._screen_all(e.errors)
    assert "SECRET" not in json.dumps(errs) and "‮" not in json.dumps(errs, ensure_ascii=False)
    assert errs[0]["loc"] == ["body", "blockKey"]
