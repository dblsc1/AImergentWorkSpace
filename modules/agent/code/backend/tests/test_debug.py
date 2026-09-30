"""调试窗口（debug.py，agent.chat.v1 第九节）：录制代理、SSE 拼装、上限、租户隔离、端点。

代理对着真的假模型（fakes/fake_openai.py，子进程）跑；不起 opencode。真 opencode 见 test_integration.py。
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from app import debug
from app.store import Store, tenant_dir
from conftest import create_app, free_port, settings
from test_api import FakeManager, app_env, new_session, send  # noqa: F401（app_env 是夹具）

FAKE = Path(__file__).parent / "fakes" / "fake_openai.py"
KEY = "sk-SECRET-LEAK"       # 假模型 fail 时会把它写进报错正文：录下来的必须是 [已隐去]


@pytest.fixture(scope="module")
def llm():
    port = free_port()
    p = subprocess.Popen([sys.executable, str(FAKE), str(port)])
    url = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            httpx.get(url + "/_log", trust_env=False)
            break
        except httpx.HTTPError:
            time.sleep(0.05)
    yield url
    p.terminate()


def chunk(delta, finish=None, **kw):
    return b"data: " + json.dumps({"choices": [{"index": 0, "delta": delta, "finish_reason": finish}], **kw}).encode()


def test_sse_assembler_split_bytes_and_tool_call_deltas():
    stream = b"\n\n".join([
        chunk({"role": "assistant", "content": ""}, model="m1"),
        chunk({"reasoning_content": "想一"}), chunk({"reasoning_content": "想"}),
        chunk({"content": "你"}), chunk({"content": "好"}),
        chunk({"tool_calls": [{"index": 0, "id": "c0", "type": "function", "function": {"name": "a", "arguments": ""}}]}),
        chunk({"tool_calls": [{"index": 1, "id": "c1", "function": {"name": "b", "arguments": "{\"x\""}}]}),
        chunk({"tool_calls": [{"index": 0, "function": {"arguments": "{}"}}]}),
        chunk({"tool_calls": [{"index": 1, "function": {"arguments": ":1}"}}]}),
        chunk({}, "tool_calls"),
        b'data: {"choices":[],"usage":{"prompt_tokens":3,"completion_tokens":4}}',
        b"data: not json", b": comment", b"data: [DONE]", b""])
    a = debug.SSEAssembler()
    for i in range(0, len(stream), 7):          # 故意从字节中间切
        a.feed(stream[i:i + 7])
    r = a.result()
    assert r["content"] == "你好" and r["reasoning"] == "想一想" and r["model"] == "m1"
    assert r["toolCalls"] == [{"id": "c0", "name": "a", "arguments": "{}"},
                              {"id": "c1", "name": "b", "arguments": "{\"x\":1}"}]
    assert r["finishReason"] == "tool_calls" and r["usage"] == {"prompt_tokens": 3, "completion_tokens": 4}
    assert r["chunks"] == 12 and r["unparsedChunks"] == 1


def _run(coro):
    return asyncio.run(coro)


async def _with_rec(tmp_path, llm, fn, **kw):
    s = settings(tmp_path, api_key=KEY, debug=True, **kw)
    rec = debug.Recorder(s, llm + "/v1")
    await rec.start()
    try:
        return await fn(rec, Store(s.data_dir))
    finally:
        await rec.stop()


def _post(base, oc_id, text, **extra):
    body = {"model": "fake-model", "stream": True, "messages": [
        {"role": "system", "content": "SYS"}, {"role": "user", "content": text}],
        "tools": [{"type": "function", "function": {"name": "honeycomb_get_task_tree", "parameters": {}}}], **extra}
    return body, {"Authorization": f"Bearer {KEY}", "x-session-id": oc_id, "Content-Type": "application/json"}


async def _send(base, oc_id, text, **extra):
    body, h = _post(base, oc_id, text, **extra)
    async with httpx.AsyncClient(trust_env=False, timeout=30) as c:
        r = await c.post(base + "/chat/completions", json=body, headers=h)
        return r.status_code, r.text


def test_proxy_records_turn_strips_key_and_isolates_tenants(tmp_path, llm):
    async def go(rec, store):
        sa = store.create("alice", None)["id"]
        rec.begin("alice", "ocA", sa, "msg_a1", "msg_u1")
        ba, bb = rec.base_for("alice"), rec.base_for("bob")
        # 1) 工具调用一轮：上游照常收到密钥（透传），应答原样回给 opencode
        st, text = await _send(ba, "ocA", "call:get_task_tree")
        assert st == 200 and "tool_calls" in text
        # 2) bob 的进程拿 alice 的 opencode 会话 id 也写不进 alice 的轮次
        assert (await _send(bb, "ocA", "hi"))[0] == 200
        # 3) 不认识的令牌 404，不转发
        assert (await _send(f"http://127.0.0.1:{rec.port}/nope", "ocA", "hi"))[0] == 404
        # 4) 上游报错（正文带密钥原文）也录，但密钥隐去
        assert (await _send(ba, "ocA", "please fail"))[0] == 401
        # 5) 登记过的轮次结束后不再录
        rec.end("alice", "ocA")
        await _send(ba, "ocA", "late")
        return sa
    sa = _run(_with_rec(tmp_path, llm, go))
    log = [e for e in httpx.get(llm + "/_log", trust_env=False).json() if e.get("method") == "POST"]
    assert all(e["auth"] == f"Bearer {KEY}" and e["path"] == "/v1/chat/completions" for e in log)
    s = settings(tmp_path, api_key=KEY, debug=True)
    turns = debug.Recorder(s, "x").read("alice", sa, None)
    assert len(turns) == 1 and turns[0]["messageId"] == "msg_a1" and turns[0]["userMessageId"] == "msg_u1"
    reqs = turns[0]["requests"]
    assert [r["n"] for r in reqs] == [1, 2] and [r["status"] for r in reqs] == [200, 401]
    r1 = reqs[0]
    assert r1["request"]["messages"][0] == {"role": "system", "content": "SYS"}
    assert r1["request"]["tools"][0]["function"]["name"] == "honeycomb_get_task_tree"
    assert r1["response"]["toolCalls"] == [{"id": "call_1", "name": "honeycomb_get_task_tree", "arguments": "{}"}]
    assert r1["response"]["finishReason"] == "tool_calls" and r1["aborted"] is False
    assert reqs[1]["response"]["body"]["error"]["message"] == "invalid api key [已隐去]"
    raw = "".join(p.read_text() for p in tmp_path.rglob("*") if p.is_file())
    assert KEY not in raw and "authorization" not in raw.lower()
    assert not (tenant_dir(str(tmp_path), "bob") / "debug").exists()


def test_caps_record_size_turn_count_and_session_delete(tmp_path, llm, monkeypatch):
    monkeypatch.setattr(debug, "MAX_TURNS", 3)

    async def go(rec, store):
        sid = store.create("alice", None)["id"]
        base = rec.base_for("alice")
        for i in range(5):
            rec.begin("alice", "oc", sid, f"msg_{i}", f"u_{i}")
        await _send(base, "oc", "big " + "字" * (1024 * 1024))       # 3 MiB 的 UTF-8
        return sid
    sid = _run(_with_rec(tmp_path, llm, go))
    s = settings(tmp_path, debug=True)
    rec = debug.Recorder(s, "x")
    turns = rec.read("alice", sid, None)
    assert [t["messageId"] for t in turns] == ["msg_2", "msg_3", "msg_4"]
    f = next((tenant_dir(str(tmp_path), "alice") / "debug" / sid).glob("*_msg_4.json"))
    assert len(f.read_bytes()) <= debug.MAX_RECORD + 4096
    content = turns[-1]["requests"][0]["request"]["messages"][1]["content"]
    assert "截断：原长" in content
    assert rec.read("alice", sid, "msg_3")[0]["messageId"] == "msg_3"
    Store(str(tmp_path)).delete("alice", sid)
    assert not (tenant_dir(str(tmp_path), "alice") / "debug" / sid).exists()


class DebugManager(FakeManager):
    def __init__(self, rec):
        super().__init__()
        self.debug = rec


def test_endpoint_off_on_and_tenant_scoped(app_env, tmp_path):
    srv, _ = app_env()
    c = srv.client("alice")
    sid = new_session(c)
    assert c.get("/api/agent/health").json()["debug"] is False
    r = c.get(f"/api/agent/sessions/{sid}/debug")
    assert r.status_code == 404 and "AGENT_DEBUG" in r.json()["detail"]

    s = settings(tmp_path, debug=True)
    rec = debug.Recorder(s, "http://127.0.0.1:1")      # 不起代理：假运行时不会发请求
    srv2, _ = app_env(manager=DebugManager(rec))
    a, b = srv2.client("alice"), srv2.client("bob")
    assert a.get("/api/agent/health").json()["debug"] is True
    sid = new_session(a)
    ev = send(a, sid, "hi")
    mid = ev[0][1]["messageId"]
    turns = a.get(f"/api/agent/sessions/{sid}/debug").json()["turns"]
    assert [(t["messageId"], t["userMessageId"], t["requests"]) for t in turns] == [
        (mid, ev[0][1]["userMessageId"], [])]
    assert rec.turns == {}                      # 这一轮结束就注销
    assert a.get(f"/api/agent/sessions/{sid}/debug", params={"messageId": mid}).json()["turns"][0]["messageId"] == mid
    assert a.get(f"/api/agent/sessions/{sid}/debug", params={"messageId": "../x"}).status_code == 422
    assert b.get(f"/api/agent/sessions/{sid}/debug").status_code == 404


def test_debug_off_wipes_old_records(tmp_path, serve):
    d = tenant_dir(str(tmp_path), "alice") / "debug" / "ses_x"
    d.mkdir(parents=True)
    (d / "1_msg.json").write_text("{}")
    serve(create_app(settings(tmp_path), FakeManager()))
    assert not d.parent.exists()


def test_upstream_mapping():
    assert debug.upstream_of(settings("/x")) == ""                                   # 没开
    assert debug.upstream_of(settings("/x", debug=True, model="deepseek/deepseek-flash")) == "https://api.deepseek.com"
    assert debug.upstream_of(settings("/x", debug=True, model="fake/m", base_url="http://h:1/v1/")) == "http://h:1/v1"
    assert debug.upstream_of(settings("/x", debug=True, model="anthropic/claude")) == ""   # 不认识：调试不开
    assert debug.upstream_of(settings("/x", debug=True, api_key="", base_url="")) == ""   # 没配模型


def test_response_side_caps_and_value_redaction(tmp_path, monkeypatch):
    monkeypatch.setattr(debug, "MAX_RECORD", 8192)
    a = debug.SSEAssembler()
    for _ in range(100):
        a.feed(chunk({"content": "x" * 200}) + b"\n\n")
    r = a.result()
    assert r["captureTruncated"] is True and len(r["content"]) <= 8192
    one = debug.SSEAssembler()
    one.feed(b"data: " + b"z" * 10000)                  # 单块就超：也截、也标
    assert one.capped and one.fed == 8192
    s = settings(tmp_path, api_key=KEY, debug=True)
    rec = debug.Recorder(s, "x")
    sid = Store(s.data_dir).create("alice", None)["id"]
    rec.begin("alice", "oc", sid, "msg_1", "u_1")
    many = {"toolCalls": [{"arguments": "y" * 100} for _ in range(500)], KEY: "k"}   # 很多短串，单个都不超 MAX_STR
    rec._append("alice", sid, "msg_1", {"request": {"m": KEY}, "response": many})
    f = next((tenant_dir(s.data_dir, "alice") / "debug" / sid).glob("*.json"))
    assert len(f.read_bytes()) <= 8192 + 512 and KEY not in f.read_text()
    saved = rec.read("alice", sid, "msg_1")[0]["requests"][0]
    assert saved["response"]["truncated"] is True and saved["request"] == {"m": debug.REDACTED}
