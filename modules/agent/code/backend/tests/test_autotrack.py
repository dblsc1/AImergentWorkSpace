"""后台认窗口的工人（agent.chat.v1 第十节）：假运行时（test_api 的脚本化假货）+ 假 MCP（httpx.MockTransport）。

工人的时钟是注入的；直接调 `once` / `tick`，不靠真等轮询间隔。判据：没窗口不花模型的钱、有窗口恰好跑一轮、
人在聊就让、每小时上限、同一租户不并发、失败全吞、没配模型 / AGENT_AUTOTRACK=0 时根本不存在。
"""
from __future__ import annotations

import asyncio
import time

import httpx
import pytest

from app import autotrack, config
from app.store import Store
from conftest import create_app, settings
from test_api import FakeManager

LOCAL = config.LOCAL_TENANT


class FakeMcp:
    """记下每次调用；`window` 给什么就回什么（None = 没有窗口在等）；`fail` 设了就按它出错。"""

    def __init__(self):
        self.calls, self.window, self.fail, self.gate = [], {"key": "wk_" + "0a" * 10}, None, None

    async def __call__(self, req: httpx.Request) -> httpx.Response:
        body = __import__("json").loads(req.content)
        self.calls.append((req.headers.get("x-nexus-tenant"), body["method"], body["params"]))
        if self.gate:
            await self.gate.wait()
        if self.fail == "down":
            raise httpx.ConnectError("no route")
        if self.fail == "500":
            return httpx.Response(500, json={"detail": "boom"})
        res = {"structuredContent": {"window": self.window}, "isError": False}
        if self.fail == "tool":
            res = {"structuredContent": {"error": {"status": 502, "detail": "数据服务暂时不可用"}}, "isError": True}
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": res})


def make(tmp_path, **kw):
    mgr = kw.pop("manager", None) or FakeManager()
    app = create_app(settings(tmp_path, autotrack=True, **kw), mgr)
    w, mcp = app.state.autotrack, FakeMcp()
    w.http, w.now = httpx.AsyncClient(transport=httpx.MockTransport(mcp)), [1000.0]
    w.clock = lambda: w.now[0]
    return app, w, mgr, mcp


def run(coro):
    return asyncio.run(coro)


def test_absent_without_a_model_or_when_switched_off(tmp_path, monkeypatch):
    assert create_app(settings(tmp_path), FakeManager()).state.autotrack is None             # 测试夹具缺省关
    assert create_app(settings(tmp_path, autotrack=True, api_key=""), FakeManager()).state.autotrack is None
    assert create_app(settings(tmp_path, autotrack=True), FakeManager()).state.autotrack is not None
    monkeypatch.setenv("AGENT_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("AGENT_AUTOTRACK", raising=False)
    assert config.load().autotrack is True                                                   # 缺省开
    monkeypatch.setenv("AGENT_AUTOTRACK", "0")
    assert config.load().autotrack is False


def test_nothing_waiting_costs_no_model_call(tmp_path):
    app, w, mgr, mcp = make(tmp_path)
    mcp.window = None
    assert run(w.once(LOCAL)) is False and run(w.once("u_alice")) is False
    assert mcp.calls == [(None, "tools/call", {"name": "get_window_awaiting_target", "arguments": {}}),
                         ("u_alice", "tools/call", {"name": "get_window_awaiting_target", "arguments": {}})]
    assert mgr.rts == {} and app.state.store.list(LOCAL) == []           # 没拉运行时、没建会话


def test_a_waiting_window_runs_exactly_one_turn_in_the_dedicated_session(tmp_path):
    app, w, mgr, mcp = make(tmp_path)
    store = app.state.store
    assert run(w.once(LOCAL)) is True
    rt = mgr.rts[LOCAL]
    assert rt.prompts == [autotrack.PROMPT] and rt.active == 0 and app.state.gens == {}
    (sess,) = store.list(LOCAL)
    assert sess["title"] == autotrack.SESSION_TITLE
    assert [(m["role"], m["text"]) for m in store.messages(LOCAL, sess["id"], 10)] == [
        ("user", autotrack.PROMPT), ("assistant", "Hello")]                 # 人在会话列表里看得到这一轮
    assert "wk_" not in autotrack.PROMPT and "{" not in autotrack.PROMPT    # 固定提示词：窗口由模型自己经 MCP 读
    # 下一个窗口：同一个会话、新的模型上下文（旧的记下来待删）
    assert run(w.once(LOCAL)) is True
    assert len(store.list(LOCAL)) == 1 and len(rt.prompts) == 2
    assert mgr.deferred == [(LOCAL, "oc1")] and store.list(LOCAL)[0]["ocId"] == "oc2"
    assert len(mcp.calls) == 2


def test_waits_while_the_human_is_chatting(tmp_path):
    app, w, mgr, mcp = make(tmp_path)
    app.state.gens[(LOCAL, "ses_human")] = object()
    assert run(w.once(LOCAL)) is False
    assert mcp.calls == [] and mgr.rts == {}             # 连问都不问：认领了又跑不了，白占 nexus-core 的等待
    assert run(w.once("u_other")) is True                # 别的租户不受影响


@pytest.mark.parametrize("where", ["auto", "other"])
def test_human_who_starts_chatting_during_the_poll_keeps_their_context(tmp_path, where):
    """问 MCP 的那一下是 await。这期间人发了消息——发在「自动识别窗口」会话里，或别的会话里。MCP 回来说有窗口在等：
    工人这一次不跑，更不能碰那个会话（它的模型上下文是人这一轮正在用的）。"""
    from fastapi import HTTPException

    app, w, mgr, mcp = make(tmp_path)
    store = app.state.store

    async def go():
        assert await w.once(LOCAL) is True                   # 先有专用会话和它的模型上下文 oc1
        (auto,) = store.list(LOCAL)
        sid = auto["id"] if where == "auto" else store.create(LOCAL, "我的会话")["id"]
        mcp.gate = asyncio.Event()
        w.tick()
        while len(mcp.calls) < 2:                            # 工人看过「没人在聊」，卡在问 MCP 上
            await asyncio.sleep(0)
        stream, _ = await w.turn(LOCAL, sid, "hang")         # 人发消息：走的就是这个函数
        await anext(stream)                                  # start
        await anext(stream)                                  # 第一段正文：提示已经发给模型了
        mcp.gate.set()                                       # MCP 回来：有窗口在等
        await asyncio.gather(*w.busy.values())
        assert mgr.rts[LOCAL].prompts == [autotrack.PROMPT, "hang"]
        assert mgr.deferred == [] and store.get(LOCAL, auto["id"])["ocId"] == "oc1"
        # 换上下文只在占着这个会话的生成位时做：人正在这个会话里生成，后台那一轮进不来，也就动不了它
        with pytest.raises(HTTPException) as busy:
            await w.turn(LOCAL, sid, autotrack.PROMPT, fresh=True)
        assert busy.value.status_code == 409
        assert mgr.deferred == [] and store.get(LOCAL, sid)["ocId"] == ("oc1" if where == "auto" else "oc2")
        await stream.aclose()                                # 人断开 = 取消；等它收尾
        for _ in range(1000):
            if not app.state.gens:
                break
            await asyncio.sleep(0)
        assert app.state.gens == {}
    run(go())


def test_hourly_cap_is_mirrored_here(tmp_path):
    app, w, mgr, mcp = make(tmp_path)

    async def go():
        for _ in range(autotrack.MAX_PER_HOUR):
            assert await w.once(LOCAL) is True
            w.now[0] += 60
        assert await w.once(LOCAL) is False              # 第 13 轮：不问也不跑
        assert len(mcp.calls) == autotrack.MAX_PER_HOUR
        w.now[0] = 1000.0 + 3600
        assert await w.once(LOCAL) is True               # 最早那一轮满一小时了
    run(go())
    assert len(mgr.rts[LOCAL].prompts) == autotrack.MAX_PER_HOUR + 1


@pytest.mark.parametrize("fail", ["down", "500", "tool", "runtime"])
def test_failures_are_swallowed(tmp_path, fail, caplog):
    app, w, mgr, mcp = make(tmp_path, manager=FakeManager(fail=fail == "runtime"))
    mcp.fail = fail

    async def go():
        w.tick()
        await asyncio.gather(*w.busy.values())
    run(go())                                            # 不抛
    assert w.busy == {} and app.state.gens == {}
    assert not any(rt.prompts for rt in mgr.rts.values())
    assert "sk-test" not in caplog.text


def test_turn_time_limit_aborts_the_turn(tmp_path, monkeypatch):
    monkeypatch.setattr(autotrack, "PROMPT", "hang")     # 假运行时的「不结束」脚本
    monkeypatch.setattr(autotrack, "TURN_SECONDS", 0.05)
    app, w, mgr, mcp = make(tmp_path)

    async def go():
        w.tick()
        await asyncio.gather(*w.busy.values())
        deadline = time.monotonic() + 5
        while app.state.gens and time.monotonic() < deadline:    # 中止要等运行时确认停了才放开
            await asyncio.sleep(0.01)
    run(go())
    assert mgr.rts[LOCAL].aborts == ["oc1"] and app.state.gens == {} and w.busy == {}


def test_one_turn_per_tenant_at_a_time_and_tenants_do_not_block_each_other(tmp_path):
    app, w, mgr, mcp = make(tmp_path, strict=True)
    app.state.store.note_tenant("u_alice")
    app.state.store.note_tenant("u_bob")

    async def go():
        mcp.gate = asyncio.Event()
        w.tick()
        while len(mcp.calls) < 2:                        # 两个租户的问询都发出去了，都卡在假 MCP 上
            await asyncio.sleep(0)
        w.tick()                                         # 上一个还没完：不再起
        for _ in range(20):
            await asyncio.sleep(0)
        assert sorted(t for t, _, _ in mcp.calls) == ["u_alice", "u_bob"] and len(w.busy) == 2
        mcp.gate.set()
        await asyncio.gather(*w.busy.values())
    run(go())
    assert {t: rt.prompts for t, rt in mgr.rts.items()} == {"u_alice": [autotrack.PROMPT], "u_bob": [autotrack.PROMPT]}


def test_known_tenants_survive_a_restart(tmp_path):
    app, w, _, _ = make(tmp_path, strict=True)
    assert w.tenants() == []                             # 严格模式：没见过谁就不替谁问
    app.state.store.note_tenant("u_alice")
    assert Store(str(tmp_path)).tenants() == ["u_alice"]              # 新进程也读得到
    (tmp_path / "tenants" / ("0" * 32)).mkdir()
    (tmp_path / "tenants" / ("0" * 32) / "tenant").write_text("u_forged")
    assert Store(str(tmp_path)).tenants() == ["u_alice"]              # 目录对不上的不认
    _, w2, _, _ = make(tmp_path)                         # 非严格：再加单人租户
    assert w2.tenants() == [LOCAL, "u_alice"]


def test_requests_register_their_tenant_and_the_loop_runs_with_the_app(tmp_path, serve, monkeypatch):
    monkeypatch.setattr(autotrack, "POLL_SECONDS", 0.02)
    app, w, mgr, mcp = make(tmp_path, strict=True)
    srv = serve(app)
    with srv.client("u_alice") as c:
        assert c.get("/api/agent/sessions").json() == {"items": []}
    deadline = time.monotonic() + 10
    while not (mgr.rts.get("u_alice") and mgr.rts["u_alice"].prompts) and time.monotonic() < deadline:
        time.sleep(0.02)
    assert mgr.rts["u_alice"].prompts[0] == autotrack.PROMPT
    with srv.client("u_alice") as c:                     # 人在会话列表里看得到
        assert [s["title"] for s in c.get("/api/agent/sessions").json()["items"]] == [autotrack.SESSION_TITLE]
