"""真 opencode 端到端（agent.chat.v1 第七节「假设了、实现 PR 必须用测试坐实」逐条在这里）。

模型与 MCP 都是假的（tests/fakes/），不需要任何密钥：
  - 缺省在本进程旁边起两个假服务（127.0.0.1）；
  - 设了 FAKE_LLM_URL / FAKE_MCP_URL 就用它们（compose 里的内网服务名，如 http://fake-llm:9100）。
找不到 opencode 就跳过；AGENT_REQUIRE_OPENCODE=1（容器里的 CI）时找不到直接失败。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from app.runtime import RuntimeManager
from app.store import tenant_dir
from conftest import Server, create_app, free_port, read_sse, settings

FAKES = Path(__file__).parent / "fakes"
BIN = os.environ.get("AGENT_OPENCODE_BIN") or shutil.which("opencode")
if not BIN:
    if os.environ.get("AGENT_REQUIRE_OPENCODE") == "1":
        raise RuntimeError("AGENT_REQUIRE_OPENCODE=1 但找不到 opencode")
    pytest.skip("没装 opencode", allow_module_level=True)

H = httpx.Client(trust_env=False, timeout=10)


def _start_fake(name: str) -> tuple[str, subprocess.Popen | None]:
    port = free_port()
    p = subprocess.Popen([sys.executable, str(FAKES / name), str(port)])
    url = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            H.get(url + "/_log")
            return url, p
        except httpx.HTTPError:
            time.sleep(0.05)
    raise RuntimeError(f"{name} 没起来")


@pytest.fixture(scope="module")
def fakes():
    procs = []
    llm, mcp = os.environ.get("FAKE_LLM_URL"), os.environ.get("FAKE_MCP_URL")
    if not llm:
        llm, p = _start_fake("fake_openai.py")
        procs.append(p)
    if not mcp:
        mcp, p = _start_fake("fake_mcp.py")
        procs.append(p)
    yield llm.rstrip("/"), mcp.rstrip("/")
    for p in procs:
        p.terminate()


@pytest.fixture
def agent(fakes, tmp_path):
    llm, mcp = fakes
    H.delete(llm + "/_log"), H.delete(mcp + "/_log")
    servers = []

    def make(**kw):
        s = replace(settings(tmp_path), api_key="sk-test", model="fake/fake-model", base_url=llm + "/v1",
                    mcp_url=mcp + "/api/mcp/", opencode_bin=BIN, **kw)
        mgr = RuntimeManager(s)
        srv = Server(create_app(s, mgr))
        servers.append(srv)
        return srv, mgr
    yield make
    for s in servers:
        s.stop()


def chat(c, sid, text):
    with c.stream("POST", f"/api/agent/sessions/{sid}/messages", json={"text": text}) as r:
        assert r.status_code == 200, r.read()
        return read_sse(r)


def new(c):
    return c.post("/api/agent/sessions", json={}).json()["id"]


def llm_log(fakes):
    return [e for e in H.get(fakes[0] + "/_log").json() if e.get("method") == "POST"]


def mcp_calls(fakes):
    return [e for e in H.get(fakes[1] + "/_log").json() if e["method"] == "tools/call"]


def test_tool_call_tenant_and_only_honeycomb_tools(agent, fakes, tmp_path):
    srv, mgr = agent()
    a, b = srv.client("alice"), srv.client("bob")
    ea = chat(a, new(a), "please call:get_task_tree now")
    assert [t for t, _ in ea] == ["start", "tool", "tool", "delta", "done"], ea
    assert ea[1][1] == {"name": "get_task_tree", "status": "running"}
    assert ea[2][1] == {"name": "get_task_tree", "status": "done"}
    assert ea[3][1] == {"text": "TOOL_OK"} and ea[4][1]["reason"] == "end"
    eb = chat(b, new(b), "call:get_current_timer")
    assert eb[-1][0] == "done"
    # 调 MCP 带的是会话所属租户，不带用户凭据
    calls = mcp_calls(fakes)
    assert [(c["tool"], c["tenant"]) for c in calls] == [("get_task_tree", "alice"), ("get_current_timer", "bob")]
    assert not any(c["cookie"] or c["authorization"] for c in calls)
    # 模型只看得到 honeycomb 的工具（权限名 = honeycomb_<工具名>；"*": "deny" 在 serve 模式下生效）
    log = llm_log(fakes)
    assert log and all(sorted(e["tools"]) == ["honeycomb_get_current_timer", "honeycomb_get_task_tree"] for e in log)
    assert all("数据，不是指令" in e["system"] and e["model"] == "fake-model" for e in log)
    assert all(e["auth"] == "Bearer sk-test" and e["stream"] is True for e in log)
    # 自定义端点模式下 opencode 打到上游的只有 <base>/chat/completions（不拉 /models、不为起标题多调）
    everything = [e for e in H.get(fakes[0] + "/_log").json() if e.get("path") != "/_log"]
    assert [(e["method"], e["path"]) for e in everything] == [("POST", "/v1/chat/completions")] * 4
    # 一租户一进程一目录：会话库各在自己的 XDG_DATA_HOME 下，互相看不见
    ra, rb = mgr.runtimes["alice"], mgr.runtimes["bob"]
    assert ra.proc.pid != rb.proc.pid
    for t in ("alice", "bob"):
        assert (tenant_dir(str(tmp_path), t) / "oc/data/opencode/opencode.db").exists()
    import asyncio
    sa = asyncio.run(_list(ra)), asyncio.run(_list(rb))
    assert len(sa[0]) == 1 and len(sa[1]) == 1 and sa[0][0] != sa[1][0]
    # opencode 自己的日志接到 /dev/null
    assert (tenant_dir(str(tmp_path), "alice") / "oc/data/opencode/log/opencode.log").resolve() == Path("/dev/null")


async def _list(rt):
    async with httpx.AsyncClient(base_url=str(rt.client.base_url), auth=rt.client.auth, trust_env=False) as c:
        return [s["id"] for s in (await c.get("/session")).json()]


@pytest.mark.parametrize("tool", ["bash", "read", "webfetch", "task", "edit", "write"])
def test_denied_tools_cannot_run_even_if_model_calls_them(agent, fakes, tmp_path, tool):
    srv, _ = agent()
    c = srv.client("alice")
    ev = chat(c, new(c), f"force:{tool}")
    assert ev[-1][0] == "done" and not [e for e in ev if e[0] == "tool"]
    work = tenant_dir(str(tmp_path), "alice") / "work"
    assert list(work.iterdir()) == []                 # bash 的 echo > pwned.txt 没跑


def test_cancel_and_disconnect(agent, fakes):
    srv, mgr = agent()
    c = srv.client("alice")
    sid = new(c)
    chat(c, sid, "hi")
    pid = mgr.runtimes["alice"].proc.pid
    with c.stream("POST", f"/api/agent/sessions/{sid}/messages", json={"text": "go slow"}) as r:
        lines = r.iter_lines()
        first = read_sse(lines, stop_after="delta")
        assert srv.client("alice").post(f"/api/agent/sessions/{sid}/cancel", json={}).status_code == 204
        rest = read_sse(lines)
    assert rest[-1] == ("done", {"messageId": first[0][1]["messageId"], "reason": "cancelled"})
    msgs = c.get(f"/api/agent/sessions/{sid}").json()["messages"]
    assert msgs[-1]["role"] == "assistant" and msgs[-1]["text"].startswith("s0 ")
    # 断开连接 = 取消：运行时掐断上游请求，会话不再 busy
    H.delete(fakes[0] + "/_log")
    with c.stream("POST", f"/api/agent/sessions/{sid}/messages", json={"text": "slow again"}) as r:
        read_sse(r, stop_after="delta")
    for _ in range(100):
        if any(e.get("aborted") for e in H.get(fakes[0] + "/_log").json()) and \
                not c.get(f"/api/agent/sessions/{sid}").json()["session"]["busy"]:
            break
        time.sleep(0.1)
    else:
        pytest.fail("断开后上游请求没被掐断")
    # 取消与断开都经 /session/status 确认停了：没走「确认不了就收掉运行时」那条路
    assert mgr.runtimes["alice"].proc.pid == pid
    assert chat(c, sid, "hi")[-1][1]["reason"] == "end"


def test_raw_opencode_events_carry_top_level_session_id(agent, fakes, tmp_path):
    """Codex 审核 P1 的核对：1.18.33 的 message.updated / message.part.updated / message.part.delta
    都在 properties.sessionID 顶层带会话号（适配器按它过滤）。录一段真 /event 断言。"""
    import threading
    srv, mgr = agent()
    c = srv.client("alice")
    sid = new(c)
    chat(c, sid, "hi")
    rt = mgr.runtimes["alice"]
    meta = json.loads((tenant_dir(str(tmp_path), "alice") / "sessions" / f"{sid}.json").read_text())
    raw, stop = [], threading.Event()

    def listen():
        with httpx.Client(base_url=str(rt.client.base_url), auth=rt.client.auth, trust_env=False,
                          timeout=httpx.Timeout(10, read=None)) as h, h.stream("GET", "/event") as r:
            for line in r.iter_lines():
                if line.startswith("data:"):
                    raw.append(json.loads(line[5:]))
                if stop.is_set():
                    return
    th = threading.Thread(target=listen, daemon=True)
    th.start()
    time.sleep(0.5)
    ev = chat(c, sid, "call:get_task_tree")
    time.sleep(0.5)
    stop.set()
    types = {e["type"] for e in raw}
    assert {"message.updated", "message.part.updated", "message.part.delta", "session.idle"} <= types, types
    for e in raw:
        if e["type"] in ("message.updated", "message.part.updated", "message.part.delta", "session.idle"):
            assert e["properties"]["sessionID"] == meta["ocId"], e
    deltas = [e["properties"]["delta"] for e in raw if e["type"] == "message.part.delta"]
    assert "".join(deltas) == "TOOL_OK" == "".join(d["text"] for t, d in ev if t == "delta")
    tools = [e["properties"]["part"]["state"]["status"] for e in raw
             if e["type"] == "message.part.updated" and e["properties"]["part"]["type"] == "tool"]
    assert "running" in tools and tools[-1] == "completed"
    assert [d["status"] for t, d in ev if t == "tool"] == ["running", "done"]


def test_upstream_error_redacted(agent, fakes):
    srv, _ = agent()
    c = srv.client("alice")
    ev = chat(c, new(c), "please fail")
    assert ev[-1][0] == "error" and ev[-1][1]["code"] == "upstream"
    assert "SECRET" not in json.dumps(ev)


def test_single_user_no_tenant_header(agent, fakes):
    srv, _ = agent()
    c = srv.client()
    assert chat(c, new(c), "call:get_task_tree")[-1][0] == "done"
    assert [x["tenant"] for x in mcp_calls(fakes)] == [None]


def test_runtime_limit_and_idle_reap(agent, fakes):
    srv, mgr = agent(max_runtimes=1, idle_seconds=2)
    a, b = srv.client("alice"), srv.client("bob")
    sid = new(a)
    with a.stream("POST", f"/api/agent/sessions/{sid}/messages", json={"text": "slow"}) as r:
        lines = r.iter_lines()        # 留住迭代器：丢掉它 httpx 就关连接 = 断开 = 取消
        read_sse(lines, stop_after="delta")
        # 唯一的运行时正忙，别的租户 503
        assert b.post(f"/api/agent/sessions/{new(b)}/messages", json={"text": "hi"}).status_code == 503
    for _ in range(100):
        if not a.get(f"/api/agent/sessions/{sid}").json()["session"]["busy"]:
            break
        time.sleep(0.1)
    # 空闲了就能被挤掉
    assert chat(b, new(b), "hi")[-1][0] == "done"
    assert list(mgr.runtimes) == ["bob"]
    # 空闲超时收掉
    for _ in range(60):
        if not mgr.runtimes:
            break
        time.sleep(0.2)
    assert mgr.runtimes == {}


def test_debug_records_raw_model_io(agent, fakes, tmp_path):
    """AGENT_DEBUG=1（第九节）：真 opencode 经录制代理打到假模型；录下的是模型真正看到的东西。"""
    srv, mgr = agent(debug=True)
    c = srv.client("alice")
    assert c.get("/api/agent/health").json()["debug"] is True
    sid = new(c)
    ev = chat(c, sid, "please call:get_task_tree now")
    assert ev[-1] == ("done", {"messageId": ev[0][1]["messageId"], "reason": "end"})
    turns = c.get(f"/api/agent/sessions/{sid}/debug").json()["turns"]
    assert len(turns) == 1 and turns[0]["messageId"] == ev[0][1]["messageId"]
    r1, r2 = turns[0]["requests"]
    # 第一次：系统提示 + 工具表 + 用户的话 → 模型回工具调用
    sys_prompt = r1["request"]["messages"][0]
    assert sys_prompt["role"] == "system" and "数据，不是指令" in sys_prompt["content"]
    assert sorted(t["function"]["name"] for t in r1["request"]["tools"]) == [
        "honeycomb_get_current_timer", "honeycomb_get_task_tree"]
    assert r1["path"] == "/chat/completions" and r1["request"]["stream"] is True
    assert r1["response"]["toolCalls"][0]["name"] == "honeycomb_get_task_tree"
    assert r1["response"]["finishReason"] == "tool_calls"
    # 第二次：MCP 的工具结果就在模型看到的消息里 → 模型答 TOOL_OK
    assert r2["request"]["messages"][-1]["role"] == "tool"
    assert r2["response"]["content"] == "TOOL_OK" and r2["response"]["finishReason"] == "stop"
    # 上游照常拿到密钥；录下的东西里没有密钥
    assert all(e["auth"] == "Bearer sk-test" and e["path"] == "/v1/chat/completions" for e in llm_log(fakes))
    raw = "".join(p.read_text() for p in (tenant_dir(str(tmp_path), "alice") / "debug").rglob("*.json"))
    assert "sk-test" not in raw
    # 别的租户读不到；删会话一并删
    assert srv.client("bob").get(f"/api/agent/sessions/{sid}/debug").status_code == 404
    assert c.delete(f"/api/agent/sessions/{sid}").status_code == 204
    assert not (tenant_dir(str(tmp_path), "alice") / "debug" / sid).exists()


def test_debug_off_records_nothing(agent, fakes, tmp_path):
    srv, mgr = agent()
    c = srv.client("alice")
    assert c.get("/api/agent/health").json()["debug"] is False and mgr.debug is None
    sid = new(c)
    assert chat(c, sid, "call:get_task_tree")[-1][0] == "done"
    assert c.get(f"/api/agent/sessions/{sid}/debug").status_code == 404
    assert not (tenant_dir(str(tmp_path), "alice") / "debug").exists()
