"""MCP 服务的传输层测试（沿用 mcp.tools.v1 第一、二、三节）：协议、Origin、租户、请求体、批量、并发上限、范围头。
工具本身的测试在 test_mcp_v2.py；假 nexus-core 与客户端在 fake_nexus.py。"""

from __future__ import annotations

import json
import urllib.error

import pytest

import mcp_server
import tools
from fake_nexus import NO_PROXY, Fake, ok, post, rpc


def test_initialize_negotiates_and_declares_only_tools(servers):
    r = rpc(servers, "initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                                    "clientInfo": {"name": "t", "version": "1"}})["result"]
    assert r["protocolVersion"] == "2025-03-26"
    assert set(r["capabilities"]) == {"tools"}
    assert rpc(servers, "initialize", {"protocolVersion": "1999-01-01"})["result"]["protocolVersion"] == "2025-06-18"
    assert rpc(servers, "initialize", {"protocolVersion": "2025-11-25"})["result"]["protocolVersion"] == "2025-11-25"
    assert rpc(servers, "ping")["result"] == {}



def test_notification_202_unknown_method_and_tool(servers):
    assert post(servers, {"jsonrpc": "2.0", "method": "notifications/initialized"}) == (202, None)
    assert rpc(servers, "resources/list")["error"]["code"] == -32601
    assert rpc(servers, "tools/call", {"name": "propose_x"})["error"]["code"] == -32602
    assert post(servers, {"jsonrpc": "2.0", "id": 7, "result": {}}) == (202, None)  # 客户端发来的响应
    status, body = post(servers, {"jsonrpc": "2.0", "id": 7})
    assert status == 200 and body["error"]["code"] == -32600 and body["id"] == 7


def test_batch_and_parse_error(servers):
    status, body = post(servers, [{"jsonrpc": "2.0", "id": 1, "method": "ping"},
                                  {"jsonrpc": "2.0", "method": "notifications/initialized"}])
    assert status == 200 and body == [{"jsonrpc": "2.0", "id": 1, "result": {}}]
    status, body = post(servers, None, raw=b"{nope")
    assert status == 400 and body["error"]["code"] == -32700


def test_get_is_405_and_health(servers):
    for url in (servers, servers.rstrip("/")):
        try:
            NO_PROXY.open(url, timeout=5)
            raise AssertionError("GET 应 405")
        except urllib.error.HTTPError as e:
            assert e.code == 405 and e.headers["Allow"] == "POST"
    base = servers.rsplit("/api/", 1)[0]
    assert NO_PROXY.open(base + "/healthz", timeout=5).status == 200


def test_body_cap_413(servers):
    big = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping", "params": {"x": "a" * 270000}}).encode()
    assert post(servers, None, raw=big)[0] == 413


def test_origin(servers, monkeypatch):
    assert post(servers, {"jsonrpc": "2.0", "id": 1, "method": "ping"})[0] == 200  # 不带 Origin 放行
    assert post(servers, {"jsonrpc": "2.0", "id": 1, "method": "ping"}, {"Origin": "https://evil.test"})[0] == 403
    monkeypatch.setattr(mcp_server, "ALLOWED_ORIGINS", {"https://example.com:8443", "null"})
    ping = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
    assert post(servers, ping, {"Origin": "https://example.com:8443"})[0] == 200
    for bad in ("https://example.com", "http://example.com:8443", "https://example.com:8443/", "null"):
        assert post(servers, ping, {"Origin": bad})[0] == 403, bad


def test_protocol_version_header(servers):
    ping = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
    assert post(servers, ping, {"MCP-Protocol-Version": "2025-06-18"})[0] == 200
    assert post(servers, ping, {"MCP-Protocol-Version": "2025-11-25"})[0] == 200  # Hermes 握手发的就是它
    assert post(servers, ping, {"MCP-Protocol-Version": "2020-01-01"})[0] == 400


# ── 租户 ───────────────────────────────────────────────────────────


def test_strict_missing_tenant_401_even_initialize(servers, monkeypatch):
    monkeypatch.setattr(mcp_server, "STRICT", True)
    for h in ({}, {"X-Nexus-Tenant": ""}):
        status, body = post(servers, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}, h)
        assert status == 401 and "detail" in body
    assert {p for _, p, *_ in Fake.requests} <= {"/api/core/activity/settings"}   # 只为 permissions 读设置，数据一个字没取
    assert post(servers, {"jsonrpc": "2.0", "id": 1, "method": "ping"}, {"X-Nexus-Tenant": "u_alice"})[0] == 200


def test_bad_tenant_400(servers):
    status, body = post(servers, {"jsonrpc": "2.0", "id": 1, "method": "ping"}, {"X-Nexus-Tenant": "a b/c"})
    assert status == 400 and "a b/c" in body["detail"]


def test_inflight_cap_503(servers, monkeypatch):
    import threading
    full = threading.BoundedSemaphore(1)
    full.acquire()
    monkeypatch.setattr(mcp_server, "SLOTS", full)
    monkeypatch.setattr(mcp_server, "SLOT_WAIT", 0.05)
    assert post(servers, {"jsonrpc": "2.0", "id": 1, "method": "ping"})[0] == 503


def test_batch_cap_and_isolation(servers, monkeypatch):
    ping = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
    status, body = post(servers, [ping] * 17)
    assert status == 400 and body["error"]["code"] == -32600
    assert post(servers, [])[0] == 400
    real = tools.call
    monkeypatch.setattr(tools, "call", lambda n, a, t, sc="write": 1 / 0 if n == "Get_View" else real(n, a, t, sc))
    status, body = post(servers, [
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": ["x"]}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "Get_View"}},
        {"jsonrpc": "2.0", "id": 3, "method": "ping"}])
    assert status == 200
    assert [(r["id"], r.get("error", {}).get("code")) for r in body] == [(1, -32602), (2, -32603), (3, None)]



@pytest.mark.parametrize("headers", [
    {"X-Nexus-Scope": "report"},
    {"X-Nexus-Scope": "report", "X-Nexus-Anonymous": "1"},
    {"X-Nexus-Anonymous": "1"},
    {"X-Nexus-Scope": "write", "X-Nexus-Anonymous": "1"},   # 匿名标记赢过它自带的范围
    {"X-Nexus-Scope": "admin"},
    {"X-Nexus-Scope": "READ"},
])
def test_report_anonymous_and_unknown_scopes_get_403_for_the_whole_endpoint(servers, headers):
    for body in ({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                 {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                 {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "Get_View"}},
                 {"jsonrpc": "2.0", "method": "notifications/initialized"}):
        status, out = post(servers, body, headers)
        assert status == 403 and set(out) == {"detail"}, (headers, body)
    assert Fake.requests == []


def test_doubled_scope_header_is_403(servers):
    """同名头两行（网关只会给一行）：不挑其中一个，拒绝。"""
    import http.client
    from urllib.parse import urlsplit

    u = urlsplit(servers)
    body = b'{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
    c = http.client.HTTPConnection(u.hostname, u.port, timeout=10)
    c.putrequest("POST", u.path)
    for k, v in (("Content-Type", "application/json"), ("Content-Length", str(len(body))),
                 ("X-Nexus-Scope", "write"), ("X-Nexus-Scope", "read")):
        c.putheader(k, v)
    c.endheaders(body)
    status = c.getresponse().status
    c.close()
    assert status == 403


def test_non_strict_missing_tenant_is_local(servers):
    ok(servers, "Get_View")
    assert Fake.requests and all(t is None for *_, t in Fake.requests)


def test_tools_take_no_tenant_argument(servers):
    res = rpc(servers, "tools/call", {"name": "Get_View", "arguments": {"tenant": "u_alice"}})["result"]
    assert res["isError"] and res["structuredContent"]["error"]["status"] == 400
    assert {p for _, p, *_ in Fake.requests} <= {"/api/core/activity/settings", "/api/core/views/tree"}   # 只为 permissions 读设置，数据一个字没取
