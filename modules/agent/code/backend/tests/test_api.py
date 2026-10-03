"""agent.chat.v1 的 HTTP 行为，运行时换成脚本化的假货（不起 opencode）。真 opencode 见 test_integration.py。"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import threading

import pytest

from app import config
from app.runtime import RuntimeUnavailable, Translator, classify
from app.store import tenant_dir
from conftest import create_app, read_sse, settings

U = "user_msg"


def ev(t, **p):
    return {"type": t, "properties": {"sessionID": "oc1", **p}}


def text_part(pid, text=""):
    return ev("message.part.updated", part={"id": pid, "messageID": "a1", "type": "text", "text": text})


def tool(status, name="honeycomb_get_task_tree"):
    return ev("message.part.updated", part={"id": "t1", "messageID": "a1", "type": "tool", "tool": name,
                                            "callID": "call_1", "state": {"status": status}})


USER = [ev("message.updated", info={"id": U, "role": "user"}),
        ev("message.part.updated", part={"id": "u1", "messageID": U, "type": "text", "text": "hi"})]
SCRIPTS = {
    "ok": USER + [tool("pending"), tool("running"), tool("completed"), text_part("p1"),
                  ev("message.part.delta", partID="p1", field="text", delta="Hel"),
                  ev("message.part.delta", partID="p1", field="text", delta="lo"),
                  ev("session.status", status={"type": "idle"}), ev("session.idle")],
    "hang": USER + [text_part("p1"), ev("message.part.delta", partID="p1", field="text", delta="part")],
    "fail": USER + [ev("session.error", error={"name": "APIError", "data": {
        "message": "invalid api key sk-SECRET-LEAK", "statusCode": 401, "responseBody": "sk-SECRET-LEAK"}}),
        ev("session.idle")],
    "quota": USER + [ev("session.error", error={"name": "APIError", "data": {"statusCode": 429}}),
                     ev("session.idle")],
}
ABORTED = [ev("session.error", error={"name": "MessageAbortedError", "data": {"message": "Aborted"}}),
           ev("session.idle")]


class FakeRuntime:
    def __init__(self, tenant):
        self.tenant, self.active, self.alive = tenant, 0, True
        self.qs: dict[str, asyncio.Queue] = {}
        self.prompts, self.aborts, self.deleted, self.n = [], [], [], 0
        self.idle_ok = True           # False = 中止之后确认不了它停了
        self.ensure_gate: asyncio.Event | None = None   # 设了就让 ensure_session 卡在这里

    async def ensure_session(self, oc_id):
        if self.ensure_gate:
            await self.ensure_gate.wait()
        if not oc_id:
            self.n += 1
            oc_id = f"oc{self.n}"
        return oc_id

    @contextlib.asynccontextmanager
    async def events(self, oc_id):
        q = self.qs[oc_id] = asyncio.Queue()

        async def it():
            while True:
                yield await q.get()
        yield it()

    async def prompt(self, oc_id, text):
        self.prompts.append(text)
        key = next((k for k in SCRIPTS if k in text), "ok")
        for e in SCRIPTS[key]:
            self.qs[oc_id].put_nowait(e)

    async def abort(self, oc_id):
        self.aborts.append(oc_id)
        if oc_id in self.qs and self.idle_ok:
            for e in ABORTED:
                self.qs[oc_id].put_nowait(e)

    async def wait_idle(self, oc_id, seconds):
        if not self.idle_ok:
            await asyncio.sleep(seconds)     # 同真货：等满时限才放弃
        return self.idle_ok

    async def delete_session(self, oc_id):
        self.deleted.append(oc_id)


class FakeManager:
    def __init__(self, fail=False):
        self.rts: dict[str, FakeRuntime] = {}
        self.fail, self.deferred = fail, []

    async def acquire(self, tenant):
        if self.fail == "boom":
            raise OSError("disk full")          # 不是 RuntimeUnavailable 的意外失败
        if self.fail:
            raise RuntimeUnavailable("full")
        rt = self.rts.setdefault(tenant, FakeRuntime(tenant))
        rt.active += 1
        return rt

    def release(self, rt):
        rt.active -= 1

    def running(self, tenant):
        return self.rts.get(tenant)

    async def kill(self, rt):
        self.killed = getattr(self, "killed", []) + [rt.tenant]
        self.rts.pop(rt.tenant, None)

    def defer_delete(self, tenant, oc_id):
        self.deferred.append((tenant, oc_id))

    async def shutdown(self):
        pass


@pytest.fixture
def app_env(tmp_path, serve):
    def make(**kw):
        mgr = kw.pop("manager", None) or FakeManager()
        srv = serve(create_app(settings(tmp_path, **kw), mgr))
        return srv, mgr
    return make


def new_session(c, **body):
    r = c.post("/api/agent/sessions", json=body)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def send(c, sid, text):
    with c.stream("POST", f"/api/agent/sessions/{sid}/messages", json={"text": text}) as r:
        assert r.status_code == 200, r.read()
        assert r.headers["content-type"].startswith("text/event-stream")
        assert r.headers["cache-control"] == "no-cache"
        return read_sse(r)


# ── 健康与配置 ──
def test_health_configured(app_env):
    srv, _ = app_env()
    assert srv.client().get("/api/agent/health").json() == {"status": "ok", "configured": True, "debug": False}


def test_unconfigured_only_messages_503(app_env):
    srv, _ = app_env(api_key="", base_url="")
    c = srv.client("alice")
    assert c.get("/api/agent/health").json()["configured"] is False
    sid = new_session(c)
    assert c.get("/api/agent/sessions").status_code == 200
    r = c.post(f"/api/agent/sessions/{sid}/messages", json={"text": "hi"})
    assert r.status_code == 503 and "AGENT_API_KEY" in r.json()["detail"]


def test_base_url_alone_counts_as_configured(app_env):
    srv, _ = app_env(api_key="", base_url="http://fake-llm:9100/v1")
    assert srv.client().get("/api/agent/health").json()["configured"] is True


# ── 租户 ──
def test_tenant_rules(app_env):
    srv, _ = app_env(strict=True)
    assert srv.client().get("/api/agent/sessions").status_code == 401
    r = srv.client("bad/tenant").get("/api/agent/sessions")
    assert r.status_code == 400 and "bad/tenant" in r.json()["detail"]


def test_tenants_isolated(app_env, tmp_path):
    srv, _ = app_env()
    a, b = srv.client("alice"), srv.client("bob")
    sid = new_session(a, title="alice 的")
    assert b.get("/api/agent/sessions").json()["items"] == []
    for r in (b.get(f"/api/agent/sessions/{sid}"), b.delete(f"/api/agent/sessions/{sid}"),
              b.post(f"/api/agent/sessions/{sid}/cancel", json={}),
              b.post(f"/api/agent/sessions/{sid}/messages", json={"text": "x"})):
        assert r.status_code == 404 and r.json() == {"detail": "会话不存在"}
    assert a.get(f"/api/agent/sessions/{sid}").status_code == 200
    # 存储按目录分：sha256 前 32 位，租户 id 不直接当目录名
    assert list((tenant_dir(str(tmp_path), "alice") / "sessions").glob("*.json"))
    assert not (tenant_dir(str(tmp_path), "bob") / "sessions").exists() or \
        not list((tenant_dir(str(tmp_path), "bob") / "sessions").glob("*.json"))
    assert len(tenant_dir(str(tmp_path), "..").name) == 32


def test_no_header_is_local_tenant(app_env):
    srv, _ = app_env()
    sid = new_session(srv.client())
    assert srv.client("u_local").get(f"/api/agent/sessions/{sid}").status_code == 200


# ── 请求体 ──
def test_body_rules(app_env):
    srv, _ = app_env()
    c = srv.client("alice")
    r = c.post("/api/agent/sessions", content=b'{"title":"x"}', headers={"Content-Type": "text/plain"})
    assert r.status_code == 415
    assert c.post("/api/agent/sessions", json={"title": "x" * 70000}).status_code == 413
    r = c.post("/api/agent/sessions", json={"model": "gpt-4"})
    assert r.status_code == 422 and isinstance(r.json()["detail"], str)
    assert c.post("/api/agent/sessions", json={"title": "x" * 101}).status_code == 422
    assert c.post("/api/agent/sessions", content=b"[1]", headers={"Content-Type": "application/json"}).status_code == 422
    assert c.post("/api/agent/sessions").status_code == 201          # 空体、不带 Content-Type = {}
    # 跨站表单那种：空体但 Content-Type 不是 JSON，一样 415
    assert c.post("/api/agent/sessions", headers={"Content-Type": "application/x-www-form-urlencoded"}).status_code == 415
    sid = new_session(c)
    url = f"/api/agent/sessions/{sid}/messages"
    for body in ({"text": "hi", "model": "x"}, {"text": "hi", "system": "x"}, {"text": "hi", "tools": []},
                 {"text": "hi", "files": []}, {"text": "hi", "agent": "build"}, {"text": 1}, {},
                 {"text": "   "}, {"text": "字" * 8001}):
        assert c.post(url, json=body).status_code == 422, body
    assert c.get(f"/api/agent/sessions/{sid}?limit=0").status_code == 422
    assert c.get(f"/api/agent/sessions/{sid}?limit=abc").status_code == 422
    assert c.get("/api/agent/sessions/..%2Fx").status_code == 404


def test_8000_code_points_ok(app_env):
    srv, mgr = app_env()
    c = srv.client("alice")
    sid = new_session(c)
    events = send(c, sid, "😀" * 8000)
    assert events[-1][0] == "done"


def test_session_limit(app_env):
    srv, _ = app_env(max_sessions=2)
    c = srv.client("alice")
    new_session(c), new_session(c)
    r = c.post("/api/agent/sessions", json={})
    assert r.status_code == 409 and "删" in r.json()["detail"]
    assert new_session(srv.client("bob"))       # 按租户计


# ── 发消息 ──
def test_stream_happy_path(app_env):
    srv, mgr = app_env()
    c = srv.client("alice")
    sid = new_session(c)
    events = send(c, sid, "hi")
    types = [t for t, _ in events]
    assert types == ["start", "tool", "tool", "delta", "delta", "done"]
    start, done = events[0][1], events[-1][1]
    assert events[1][1] == {"name": "get_task_tree", "status": "running"}
    assert events[2][1] == {"name": "get_task_tree", "status": "done"}
    assert done == {"messageId": start["messageId"], "reason": "end"}
    got = c.get(f"/api/agent/sessions/{sid}").json()
    assert [(m["role"], m["text"]) for m in got["messages"]] == [("user", "hi"), ("assistant", "Hello")]
    assert got["messages"][0]["id"] == start["userMessageId"]
    assert got["messages"][1]["id"] == start["messageId"]
    assert got["session"]["busy"] is False
    # 第二条消息沿用同一个 opencode 会话
    send(c, sid, "again")
    assert mgr.rts["alice"].prompts == ["hi", "again"]
    assert mgr.rts["alice"].active == 0


def test_history_limit(app_env):
    srv, _ = app_env()
    c = srv.client("alice")
    sid = new_session(c)
    send(c, sid, "one"), send(c, sid, "two")
    got = c.get(f"/api/agent/sessions/{sid}?limit=3").json()
    assert got["truncated"] is True and [m["text"] for m in got["messages"]] == ["Hello", "two", "Hello"]


@pytest.mark.parametrize("fail", [True, "boom"])
def test_runtime_unavailable_503(app_env, fail):
    srv, _ = app_env(manager=FakeManager(fail=fail))
    c = srv.client("alice")
    sid = new_session(c)
    for _ in range(3):      # 占位一定被拿掉：不会变成 409 / 429
        r = c.post(f"/api/agent/sessions/{sid}/messages", json={"text": "hi"})
        assert r.status_code == 503 and isinstance(r.json()["detail"], str)
    assert c.get(f"/api/agent/sessions/{sid}").json()["session"]["busy"] is False


def _open_hanging(c, sid):
    """后台线程开一条挂着的流，等到收到第一个 delta。"""
    box, ready = {}, threading.Event()

    def run():
        with c.stream("POST", f"/api/agent/sessions/{sid}/messages", json={"text": "hang"}) as r:
            lines = r.iter_lines()
            evs = read_sse(lines, stop_after="delta")
            ready.set()
            evs += read_sse(lines)
            box["events"] = evs
    th = threading.Thread(target=run)
    th.start()
    assert ready.wait(10)
    return th, box


def test_busy_409_tenant_429_and_cancel(app_env):
    srv, mgr = app_env()
    c = srv.client("alice")
    s1, s2, s3 = new_session(c), new_session(c), new_session(c)
    th1, box1 = _open_hanging(srv.client("alice"), s1)
    assert c.get(f"/api/agent/sessions/{s1}").json()["session"]["busy"] is True
    assert c.post(f"/api/agent/sessions/{s1}/messages", json={"text": "x"}).status_code == 409
    th2, box2 = _open_hanging(srv.client("alice"), s2)
    assert c.post(f"/api/agent/sessions/{s3}/messages", json={"text": "x"}).status_code == 429
    # 别的租户不受影响
    b = srv.client("bob")
    assert send(b, new_session(b), "hi")[-1][0] == "done"
    for sid in (s1, s2):
        assert c.post(f"/api/agent/sessions/{sid}/cancel", json={}).status_code == 204
    th1.join(10), th2.join(10)
    assert box1["events"][-1] == ("done", {"messageId": box1["events"][0][1]["messageId"], "reason": "cancelled"})
    # 被取消的回答保留已生成的部分
    msgs = c.get(f"/api/agent/sessions/{s1}").json()["messages"]
    assert [(m["role"], m["text"]) for m in msgs] == [("user", "hang"), ("assistant", "part")]
    # 幂等：没在生成也 204
    assert c.post(f"/api/agent/sessions/{s1}/cancel", json={}).status_code == 204
    assert c.post(f"/api/agent/sessions/{s1}/cancel").status_code == 204


def test_disconnect_cancels(app_env):
    srv, mgr = app_env()
    c = srv.client("alice")
    sid = new_session(c)
    with srv.client("alice").stream("POST", f"/api/agent/sessions/{sid}/messages", json={"text": "hang"}) as r:
        read_sse(r, stop_after="delta")
    for _ in range(100):
        if mgr.rts["alice"].aborts:
            break
        threading.Event().wait(0.05)
    assert mgr.rts["alice"].aborts == ["oc1"]
    for _ in range(100):
        if not c.get(f"/api/agent/sessions/{sid}").json()["session"]["busy"]:
            break
        threading.Event().wait(0.05)
    got = c.get(f"/api/agent/sessions/{sid}").json()
    assert got["session"]["busy"] is False and got["messages"][-1]["text"] == "part"


def test_upstream_error_redacted(app_env, caplog):
    srv, _ = app_env()
    c = srv.client("alice")
    sid = new_session(c)
    with caplog.at_level(logging.INFO, logger="agent"):
        events = send(c, sid, "fail")
    assert [t for t, _ in events] == ["start", "error"]
    err = events[-1][1]
    assert err["code"] == "upstream" and err["correlationId"].startswith("c_") and "密钥" in err["detail"]
    assert "SECRET" not in repr(events)
    logs = "\n".join(r.getMessage() for r in caplog.records)
    assert err["correlationId"] in logs and "sub=auth" in logs and "status=401" in logs
    assert "SECRET" not in logs and "fail" not in logs.replace("sub=", "")
    assert [m["role"] for m in c.get(f"/api/agent/sessions/{sid}").json()["messages"]] == ["user"]
    assert send(c, sid, "quota")[-1][1]["detail"].startswith("模型服务额度")


def test_delete(app_env):
    srv, mgr = app_env()
    c = srv.client("alice")
    sid = new_session(c)
    send(c, sid, "hi")
    assert c.delete(f"/api/agent/sessions/{sid}").status_code == 204
    assert c.get(f"/api/agent/sessions/{sid}").status_code == 404
    assert mgr.rts["alice"].deleted == ["oc1"]
    # 运行时没在跑：记下来以后删
    sid2 = new_session(c)
    send(c, sid2, "hi")
    mgr.rts.clear()
    assert c.delete(f"/api/agent/sessions/{sid2}").status_code == 204
    assert mgr.deferred == [("alice", "oc2")]


def test_list_order_and_shape(app_env):
    srv, _ = app_env()
    c = srv.client("alice")
    s1 = new_session(c, title="  第一  ")
    s2 = new_session(c)
    send(c, s1, "hi")
    items = c.get("/api/agent/sessions").json()["items"]
    assert [i["id"] for i in items] == [s1, s2]
    assert items[0]["title"] == "第一" and items[1]["title"] is None
    assert set(items[0]) == {"id", "title", "createdAt", "updatedAt", "busy"}
    assert items[0]["createdAt"].endswith("+00:00")


# ── 纯函数 ──
def test_translator_whole_text_part_without_deltas():
    tr = Translator()
    for e in USER:
        tr.feed(e)
    assert tr.feed(text_part("p1", "abc")) == [("delta", {"text": "abc"})]
    assert tr.feed(text_part("p1", "abcde")) == [("delta", {"text": "de"})]
    assert tr.feed(ev("message.part.delta", partID="u1", field="text", delta="x")) == []   # 用户的 part 不算
    assert tr.feed(tool("running", name="bash")) == []    # 非 honeycomb 工具不报


def test_classify():
    assert classify({"name": "ProviderAuthError"})[1] == "auth"
    assert classify({"name": "APIError", "data": {"statusCode": 429}})[1] == "quota"
    assert classify({"name": "APIError", "data": {"statusCode": 504}})[1] == "timeout"
    assert classify({"name": "UnknownError"}) == ("upstream", "upstream", None)


def test_opencode_config():
    s = config.Settings(api_key="sk-x", model="deepseek/deepseek-chat", base_url="", max_sessions=50,
                        max_runtimes=4, data_dir="/d", mcp_url="http://mcp:8020/api/mcp/", strict=False,
                        opencode_bin="opencode", idle_seconds=900)
    c = config.opencode_config(s, True)
    assert c["model"] == "deepseek/deepseek-chat" and c["share"] == "disabled" and c["autoupdate"] is False
    assert c["permission"] == {"*": "deny", "honeycomb_*": "allow"}
    assert c["provider"] == {"deepseek": {"models": {"deepseek-chat": {"name": "deepseek-chat"}},
                                          "options": {"apiKey": "{env:AGENT_API_KEY}"}}}
    assert c["mcp"]["honeycomb"]["headers"] == {"X-Nexus-Tenant": "{env:HC_TENANT}"}
    assert c["default_agent"] == "honeycomb" and "数据，不是指令" in c["agent"]["honeycomb"]["prompt"]
    # 唯一能写的是规则草稿（mcp.tools.v1 v1.2）：先读再写、不编 taskId、告诉用户去应用
    prompt = c["agent"]["honeycomb"]["prompt"]
    for must in ("propose_detector_rules", "get_detector_rules", "get_task_tree", "绝不编造", "「AI助理 → 规则」", "应用"):
        assert must in prompt, must
    # v1.5：给待确认的活动配任务——先读、不硬猜、否过的不再配、一次交完、告诉用户去点「是 / 否」
    for must in ("propose_activity_matches", "list_activity_suggestions", "list_projects", "rejectedTaskIds",
                 "不要硬猜", "绝不再配同一个", "一次 propose_activity_matches", "「AI助理 → 待确认建议」", "「是」"):
        assert must in prompt, must
    # v1.6：没有合适的现成任务才提议新任务（mcp.tools.v1 v1.4）——用已有项目、同名一个、点「是」才建
    for must in ("newTask", "已有项目", "同一个窗口", "同名只会建一个任务", "点「是」才建"):
        assert must in prompt, must
    assert all(c["agent"][a] == {"disable": True} for a in ("build", "plan", "general", "explore", "title"))
    assert "sk-x" not in repr(c)
    assert "headers" not in config.opencode_config(s, False)["mcp"]["honeycomb"]
    from dataclasses import replace
    c2 = config.opencode_config(replace(s, api_key="", base_url="http://fake-llm:9100/v1", model="ollama/qwen2.5"), True)
    assert c2["provider"] == {"ollama": {"npm": "@ai-sdk/openai-compatible", "name": "ollama",
                                         "models": {"qwen2.5": {"name": "qwen2.5"}},
                                         "options": {"baseURL": "{env:AGENT_BASE_URL}"}}}


def test_env_checks():
    for ok in ("http://fake-llm:9100/v1", "https://api.example.com/v1", "http://host.docker.internal:11434/v1"):
        assert config.check_base_url(ok) == ok
    for bad in ("ftp://x/v1", "fake-llm:9100/v1", "http://", "javascript:alert(1)"):
        with pytest.raises(SystemExit):
            config.check_base_url(bad)
    with pytest.raises(SystemExit):
        config.check_model("deepseek-chat")
    assert config.check_model("openrouter/anthropic/claude") == "openrouter/anthropic/claude"


def test_ping_while_idle(app_env, monkeypatch):
    import app.main
    monkeypatch.setattr(app.main, "PING_SECONDS", 0.2)
    srv, _ = app_env()
    c = srv.client("alice")
    sid = new_session(c)
    with c.stream("POST", f"/api/agent/sessions/{sid}/messages", json={"text": "hang"}) as r:
        lines = r.iter_lines()
        read_sse(lines, stop_after="delta")
        assert next(l for l in lines if l) == ": ping"
        assert c.post(f"/api/agent/sessions/{sid}/cancel", json={}).status_code == 204
        assert read_sse(lines)[-1][1]["reason"] == "cancelled"


def test_turn_deadline_and_unconfirmed_abort_kills_runtime(app_env, monkeypatch):
    import app.main
    monkeypatch.setattr(app.main, "CANCEL_GRACE", 0.3)
    srv, mgr = app_env(max_turn_seconds=1)
    c = srv.client("alice")
    sid = new_session(c)
    mgr.rts.setdefault("alice", FakeRuntime("alice")).idle_ok = False     # 上游卡死、中止也不回话
    ev = send(c, sid, "hang")
    assert ev[-1][0] == "error" and ev[-1][1]["detail"].startswith("模型服务超时")
    assert mgr.rts == {} and mgr.killed == ["alice"]                        # 运行时整个收掉
    assert c.get(f"/api/agent/sessions/{sid}").json()["session"]["busy"] is False
    assert send(c, sid, "hi")[-1][0] == "done"                               # 下一轮换新运行时


def test_disconnect_keeps_busy_until_abort_confirmed(app_env, monkeypatch):
    import app.main
    monkeypatch.setattr(app.main, "CANCEL_GRACE", 1.0)
    srv, mgr = app_env()
    c = srv.client("alice")
    sid = new_session(c)
    rt = mgr.rts.setdefault("alice", FakeRuntime("alice"))
    rt.idle_ok = False
    with srv.client("alice").stream("POST", f"/api/agent/sessions/{sid}/messages", json={"text": "hang"}) as r:
        read_sse(r, stop_after="delta")
    threading.Event().wait(0.3)
    # 断开了，但运行时还没确认停：会话仍 busy，下一条 409
    assert c.get(f"/api/agent/sessions/{sid}").json()["session"]["busy"] is True
    assert c.post(f"/api/agent/sessions/{sid}/messages", json={"text": "x"}).status_code == 409
    for _ in range(60):
        if not c.get(f"/api/agent/sessions/{sid}").json()["session"]["busy"]:
            break
        threading.Event().wait(0.05)
    assert mgr.killed == ["alice"]


def test_delete_during_first_send_does_not_resurrect(app_env):
    srv, mgr = app_env()
    c = srv.client("alice")
    sid = new_session(c)
    rt = mgr.rts.setdefault("alice", FakeRuntime("alice"))
    rt.ensure_gate = asyncio.Event()
    box = {}
    th = threading.Thread(target=lambda: box.setdefault(
        "r", srv.client("alice").post(f"/api/agent/sessions/{sid}/messages", json={"text": "hi"})))
    th.start()
    threading.Event().wait(0.3)
    assert c.delete(f"/api/agent/sessions/{sid}").status_code == 204
    asyncio.run_coroutine_threadsafe(_set(rt.ensure_gate), srv.loop).result(5)
    th.join(10)
    assert box["r"].status_code == 404
    assert c.get(f"/api/agent/sessions/{sid}").status_code == 404
    assert c.get("/api/agent/sessions").json()["items"] == []
    assert rt.deleted == ["oc1"]                    # 刚建的 opencode 会话也删掉


async def _set(ev):
    ev.set()


def test_history_capped_and_list_reads_metadata_only(app_env, tmp_path, monkeypatch):
    import app.store as st
    monkeypatch.setattr(st, "MAX_MESSAGES", 4)
    monkeypatch.setattr(st, "COMPACT_SLACK", 2)
    srv, _ = app_env()
    c = srv.client("alice")
    sid = new_session(c)
    for i in range(5):
        send(c, sid, f"q{i}")
    got = c.get(f"/api/agent/sessions/{sid}?limit=500").json()
    assert got["truncated"] is True and len(got["messages"]) <= 6
    assert got["messages"][-2]["text"] == "q4"
    d = tenant_dir(str(tmp_path), "alice") / "sessions"
    meta = json.loads((d / f"{sid}.json").read_text())
    assert "messages" not in meta and meta["dropped"] > 0
    assert sum(1 for _ in open(d / f"{sid}.jsonl")) == meta["count"]


def test_answer_length_capped(app_env, monkeypatch):
    import app.runtime as rtm
    monkeypatch.setattr(rtm, "MAX_ANSWER_CHARS", 4)
    srv, mgr = app_env()
    c = srv.client("alice")
    sid = new_session(c)
    ev = send(c, sid, "hi")          # 脚本回 "Hel" + "lo"：只转前 4 个字，然后中止
    assert "".join(d["text"] for t, d in ev if t == "delta") == "Hell"
    assert ev[-1][1]["reason"] == "cancelled"
    assert c.get(f"/api/agent/sessions/{sid}").json()["messages"][-1]["text"] == "Hell"
