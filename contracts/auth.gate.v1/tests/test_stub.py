"""auth.gate.v1.4 占位实现：真起进程、真发 HTTP，按契约逐条核对。"""

from __future__ import annotations

import http.client
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

STUB = Path(__file__).resolve().parents[1] / "stub" / "auth_stub.py"
PW = "correct-horse-1"


def _port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _cli(env: dict, *args: str, password: str = PW) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(STUB), *args], input=password + "\n",
                          env={**os.environ, **env}, capture_output=True, text=True)


class Stub:
    def __init__(self, env: dict):
        self.port = _port()
        self.env = {**env, "AUTH_BIND": f"127.0.0.1:{self.port}", "AUTH_COOKIE_SECURE": "false",
                    "AUTH_SECRET": "s" * 32}
        self.proc = subprocess.Popen([sys.executable, str(STUB)], env={**os.environ, **self.env},
                                     stderr=subprocess.PIPE, text=True)
        for _ in range(100):
            try:
                if self.req("GET", "/api/auth/health")[0] == 200:
                    return
            except OSError:
                time.sleep(0.05)
        raise RuntimeError(self.proc.stderr.read())

    def req(self, method, path, body=None, cookie="", headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = {"Cookie": cookie, **(headers or {})} if cookie else dict(headers or {})
        if body is not None:
            h["Content-Type"] = "application/json"
            body = json.dumps(body)
        c.request(method, path, body=body, headers=h)
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, dict(r.getheaders()), data

    def login(self, **body):
        status, h, _ = self.req("POST", "/api/auth/login", body)
        cookie = h.get("Set-Cookie", "").split(";")[0]
        return status, cookie

    def stop(self):
        self.proc.terminate()
        self.proc.wait(5)


@pytest.fixture
def users(tmp_path):
    env = {"AUTH_USERS_FILE": str(tmp_path / "users.json")}
    assert _cli(env, "adduser", "alice").returncode == 0
    assert _cli(env, "adduser", "bob", "--id", "u_local").returncode == 0
    return env


@pytest.fixture
def stub(users):
    s = Stub(users)
    yield s
    s.stop()


def test_refuses_to_start_without_any_login(tmp_path):
    env = {k: v for k, v in os.environ.items() if not k.startswith("AUTH_")}
    r = subprocess.run([sys.executable, str(STUB)], env=env, capture_output=True, text=True, timeout=10)
    assert r.returncode != 0 and "拒绝启动" in r.stderr
    # 账号文件设了但是空的：一样进不去，一样拒绝启动
    r = subprocess.run([sys.executable, str(STUB)], env={**env, "AUTH_USERS_FILE": str(tmp_path / "u.json")},
                       capture_output=True, text=True, timeout=10)
    assert r.returncode != 0 and "拒绝启动" in r.stderr


def test_account_login_verify_carries_tenant_and_me_shape(stub, users):
    alice = json.loads(Path(users["AUTH_USERS_FILE"]).read_text())["users"]["alice"]["id"]
    status, cookie = stub.login(username="Alice", password=PW)
    assert status == 204 and cookie
    status, h, body = stub.req("GET", "/api/auth/verify", cookie=cookie)
    assert (status, h.get("X-Nexus-Tenant"), body) == (204, alice, b"")
    status, _, body = stub.req("GET", "/api/auth/me", cookie=cookie)
    assert status == 200 and json.loads(body) == {"ok": True, "user": {"id": alice, "name": "alice"}}


def test_wrong_password_and_unknown_user_are_both_401(stub):
    assert stub.login(username="alice", password="nope-nope")[0] == 401
    assert stub.login(username="nobody", password=PW)[0] == 401
    assert stub.login(password=PW)[0] == 401  # 没开共享口令


def test_not_logged_in(stub):
    assert stub.req("GET", "/api/auth/verify")[0] == 401
    status, _, body = stub.req("GET", "/api/auth/me")
    assert status == 401 and json.loads(body)["ok"] is False


def test_forged_tenant_in_cookie_is_rejected(stub):
    _, cookie = stub.login(username="alice", password=PW)
    name, _, token = cookie.partition("=")
    ts, _, rest = token.partition(".")
    forged = f"{name}={ts}.u_local.{rest.rpartition('.')[2]}"
    assert stub.req("GET", "/api/auth/verify", cookie=forged)[0] == 401


def test_passwd_and_deluser_revoke_sessions(stub, users):
    _, a = stub.login(username="alice", password=PW)
    _, b = stub.login(username="bob", password=PW)
    assert _cli(users, "passwd", "alice", password="another-pass-2").returncode == 0
    assert _cli(users, "deluser", "bob").returncode == 0
    time.sleep(2.6)
    assert stub.req("GET", "/api/auth/verify", cookie=a)[0] == 401
    assert stub.req("GET", "/api/auth/verify", cookie=b)[0] == 401
    assert stub.login(username="alice", password="another-pass-2")[0] == 204


def test_shared_password_mode_gives_no_tenant(tmp_path):
    s = Stub({"AUTH_PASSWORD": PW})
    try:
        status, cookie = s.login(password=PW)
        assert status == 204
        status, h, _ = s.req("GET", "/api/auth/verify", cookie=cookie)
        assert status == 204 and "X-Nexus-Tenant" not in h
        assert json.loads(s.req("GET", "/api/auth/me", cookie=cookie)[2])["user"]["id"] == "u_local"
        health = json.loads(s.req("GET", "/api/auth/health")[2])
        assert health == {"status": "ok", "accounts": False, "sharedPassword": True, "anonymousReport": True}
    finally:
        s.stop()


def test_login_failures_are_rate_limited_per_forwarded_ip(stub):
    spoof = {"X-Forwarded-For": "1.2.3.4, 10.0.0.9"}  # 最后一个是网关看到的真对端
    for _ in range(10):
        status, _, _ = stub.req("POST", "/api/auth/login", {"username": "alice", "password": "x"}, headers=spoof)
        assert status == 401
    blocked = stub.req("POST", "/api/auth/login", {"username": "alice", "password": PW}, headers=spoof)
    assert blocked[0] == 429 and blocked[1].get("Retry-After")
    other = {"X-Forwarded-For": "1.2.3.4, 10.0.0.10"}  # 换真对端才算另一个 IP
    assert stub.req("POST", "/api/auth/login", {"username": "alice", "password": PW}, headers=other)[0] == 204


def test_users_file_is_private_and_holds_no_plaintext(users):
    p = Path(users["AUTH_USERS_FILE"])
    assert p.stat().st_mode & 0o077 == 0
    assert PW not in p.read_text()


def test_cli_rejects_bad_names_short_passwords_and_duplicate_ids(users):
    assert _cli(users, "adduser", "Bad Name").returncode != 0
    assert _cli(users, "adduser", "carol", password="short").returncode != 0
    assert _cli(users, "adduser", "dave", "--id", "u_local").returncode != 0
    assert _cli(users, "adduser", "alice").returncode != 0


def test_concurrent_account_commands_do_not_lose_updates(users):
    """账号命令读—改—写持锁：并发 adduser 一个都不能丢（Codex 审核）。"""
    names = [f"user{i}" for i in range(8)]
    procs = [subprocess.Popen([sys.executable, str(STUB), "adduser", n], stdin=subprocess.PIPE,
                              env={**os.environ, **users}, text=True, stdout=subprocess.DEVNULL)
             for n in names]
    for p in procs:  # 先把密码全喂进去，让它们真的同时跑到读—改—写
        p.stdin.write(PW + "\n")
        p.stdin.close()
    for p in procs:
        p.wait(30)
    listed = _cli(users, "users").stdout
    assert all(n in listed for n in names), listed


def test_cookie_path_follows_base_path(tmp_path):
    s = Stub({"AUTH_PASSWORD": PW, "AUTH_BASE_PATH": "/Cockpit/"})
    try:
        _, h, _ = s.req("POST", "/api/auth/login", {"password": PW})
        assert "Path=/Cockpit/;" in h["Set-Cookie"]
    finally:
        s.stop()
    env = {**os.environ, "AUTH_PASSWORD": PW, "AUTH_BASE_PATH": "Cockpit"}
    r = subprocess.run([sys.executable, str(STUB)], env=env, capture_output=True, text=True, timeout=10)
    assert r.returncode != 0 and "AUTH_BASE_PATH" in r.stderr


# ── 设备令牌（v1.2）────────────────────────────────────────────────

SECRET_ENV = {"AUTH_SECRET": "s" * 32}  # 与 Stub 同一把密钥：命令行签的令牌服务端才认
API = {"X-Original-URI": "/api/core/views/tree"}


def _bearer(token: str, uri: str | None = "/api/core/views/tree") -> dict:
    h = {"Authorization": f"Bearer {token}"}
    if uri is not None:
        h["X-Original-URI"] = uri
    return h


def _token_cli(env: dict, *args: str) -> str:
    r = _cli({**env, **SECRET_ENV}, "token", *args)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def _http_token(stub, cookie: str, body: dict | None = None):
    status, _, data = stub.req("POST", "/api/auth/tokens", body if body is not None else {}, cookie=cookie)
    return status, (json.loads(data) if data else None)


def test_bearer_opens_api_with_tenant_but_never_pages(stub, users):
    alice = json.loads(Path(users["AUTH_USERS_FILE"]).read_text())["users"]["alice"]["id"]
    tok = _token_cli(users, "alice")
    time.sleep(2.6)  # 第一个令牌顺带建出令牌文件，服务端 RELOAD_EVERY 内读到
    status, h, body = stub.req("GET", "/api/auth/verify", headers=_bearer(tok))
    assert (status, h.get("X-Nexus-Tenant"), body) == (204, alice, b"")
    for uri in ("/hive/", "/login/", "/api/auth/me", "/api/core/%2e%2e/hive/", "/api/core/../hive/",
                "/api/core/..%2Fhive/", "/api/core/%2e%2e/%2E%2E/hive/", "/api/corex/", None):
        assert stub.req("GET", "/api/auth/verify", headers=_bearer(tok, uri))[0] == 401, uri
    assert stub.req("GET", "/api/auth/verify", headers=_bearer(tok, "/api/core/x?y=../../hive"))[0] == 204


def test_bearer_opens_mcp_but_not_agent(stub, users):
    """v1.3：令牌也认只读的 <前缀>api/mcp/；api/agent/（聊天后端）与形似的前缀照旧 401。"""
    tok = _token_cli(users, "alice")
    time.sleep(2.6)
    for uri in ("/api/mcp/", "/api/mcp/?x=1"):
        assert stub.req("GET", "/api/auth/verify", headers=_bearer(tok, uri))[0] == 204, uri
    for uri in ("/api/agent/", "/api/agent/sessions", "/api/mcp", "/api/mcpx/", "/api/mcp/%2e%2e/%2e%2e/hive/",
                "/api/mcp/../agent/", "/api/mcp/..%2F..%2Fhive/", "/api/mcp/%5c..%5chive/"):
        assert stub.req("GET", "/api/auth/verify", headers=_bearer(tok, uri))[0] == 401, uri


def test_bearer_follows_base_path(tmp_path):
    env = {"AUTH_PASSWORD": PW, "AUTH_BASE_PATH": "/Cockpit/", "AUTH_TOKENS_FILE": str(tmp_path / "t.json")}
    tok = _token_cli(env)
    s = Stub(env)
    try:
        assert s.req("GET", "/api/auth/verify", headers=_bearer(tok, "/Cockpit/api/core/views/tree"))[0] == 204
        assert s.req("GET", "/api/auth/verify", headers=_bearer(tok, "/api/core/views/tree"))[0] == 401
        assert s.req("GET", "/api/auth/verify", headers=_bearer(tok, "/Cockpit/api/mcp/"))[0] == 204
        assert s.req("GET", "/api/auth/verify", headers=_bearer(tok, "/api/mcp/"))[0] == 401
    finally:
        s.stop()


def test_token_and_cookie_domains_are_separate(stub, users):
    tok = _token_cli(users, "alice")
    _, cookie = stub.login(username="alice", password=PW)
    assert stub.req("GET", "/api/auth/verify", cookie=f"cockpit_session={tok}")[0] == 401
    assert stub.req("GET", "/api/auth/verify", headers=_bearer(cookie.partition("=")[2]))[0] == 401
    # 带了 Bearer 就只看令牌，不回落到 cookie
    assert stub.req("GET", "/api/auth/verify", cookie=cookie, headers=_bearer("garbage"))[0] == 401


def test_garbage_and_huge_authorization_is_401(stub):
    for auth in ("Bearer", "Bearer ", "Bearer hct1", "Bearer hct1....", "Bearer hct1.x.y.z.w",
                 "bearer hct1.1.0..deadbeef", "Bearer " + "é" * 50, "Bearer " + "a." * 8000):
        assert stub.req("GET", "/api/auth/verify", headers={**API, "Authorization": auth})[0] == 401, auth[:30]


def test_http_issue_and_revoke(stub, users):
    _, cookie = stub.login(username="alice", password=PW)
    status, body = _http_token(stub, cookie, {"label": "laptop"})
    assert status == 201 and {"token", "tenant", "expiresAt"} <= set(body) and body["tenant"].startswith("u_")
    assert (body["scope"], body["name"]) == ("write", "laptop") and "revoked" not in body  # v1.4：缺省 write，label 存成 name
    old = body["token"]
    assert stub.req("GET", "/api/auth/verify", headers=_bearer(old))[0] == 204
    assert stub.req("POST", "/api/auth/tokens/revoke", {}, cookie=cookie)[0] == 204
    assert stub.req("GET", "/api/auth/verify", headers=_bearer(old))[0] == 401
    status, body = _http_token(stub, cookie)
    assert status == 201 and stub.req("GET", "/api/auth/verify", headers=_bearer(body["token"]))[0] == 204
    # 别人的令牌不受影响
    _, bob = stub.login(username="bob", password=PW)
    bob_tok = _http_token(stub, bob)[1]["token"]
    stub.req("POST", "/api/auth/tokens/revoke", {}, cookie=cookie)
    assert stub.req("GET", "/api/auth/verify", headers=_bearer(bob_tok))[0] == 204
    assert os.stat(stub.env["AUTH_USERS_FILE"].replace("users.json", "tokens.json")).st_mode & 0o077 == 0


def test_http_endpoints_need_cookie_json_and_small_body(stub, users):
    tok = _token_cli(users, "alice")
    assert _http_token(stub, "")[0] == 401
    assert stub.req("POST", "/api/auth/tokens", {}, headers={"Authorization": f"Bearer {tok}"})[0] == 401
    assert stub.req("POST", "/api/auth/tokens/revoke", {}, headers={"Authorization": f"Bearer {tok}"})[0] == 401
    _, cookie = stub.login(username="alice", password=PW)
    assert _http_token(stub, cookie, {"label": "x" * 65})[0] == 400
    assert stub.req("POST", "/api/auth/tokens", None, cookie=cookie)[0] == 415  # 没有 JSON 类型
    # 拒收请求体必须断连（安全约定 5）：读到 EOF 而不是挂住等下一个请求
    with socket.create_connection(("127.0.0.1", stub.port), timeout=10) as sock:
        sock.sendall(f"POST /api/auth/tokens HTTP/1.1\r\nHost: x\r\nCookie: {cookie}\r\n"
                     "Content-Type: application/json\r\nContent-Length: 9000\r\n\r\n".encode())  # 只报长度不发体
        got = b""
        while chunk := sock.recv(65536):
            got += chunk
    assert got.startswith(b"HTTP/1.1 413")


def test_cli_revoke_and_passwd_kill_tokens(stub, users):
    old = _token_cli(users, "alice")
    assert _cli(users, "revoke", "alice").returncode == 0
    new = _token_cli(users, "alice")
    time.sleep(2.6)
    assert stub.req("GET", "/api/auth/verify", headers=_bearer(old))[0] == 401
    assert stub.req("GET", "/api/auth/verify", headers=_bearer(new))[0] == 204
    assert _cli(users, "passwd", "alice", password="another-pass-2").returncode == 0
    time.sleep(2.6)
    assert stub.req("GET", "/api/auth/verify", headers=_bearer(new))[0] == 401


def test_expired_token_is_401(tmp_path):
    env = {"AUTH_PASSWORD": PW, "AUTH_TOKENS_FILE": str(tmp_path / "t.json"), "AUTH_TOKEN_DAYS": "0"}
    tok = _token_cli(env)
    s = Stub(env)
    try:
        time.sleep(1.1)
        assert s.req("GET", "/api/auth/verify", headers=_bearer(tok))[0] == 401
    finally:
        s.stop()


def test_shared_password_token_dies_without_password(tmp_path):
    env = {"AUTH_PASSWORD": PW, "AUTH_TOKENS_FILE": str(tmp_path / "t.json")}
    tok = _token_cli(env)
    s = Stub(env)
    try:
        status, h, _ = s.req("GET", "/api/auth/verify", headers=_bearer(tok))
        assert status == 204 and "X-Nexus-Tenant" not in h
        _, cookie = s.login(password=PW)
        assert _http_token(s, cookie)[1]["tenant"] == "u_local"
    finally:
        s.stop()
    users = {"AUTH_USERS_FILE": str(tmp_path / "u.json"), "AUTH_TOKENS_FILE": env["AUTH_TOKENS_FILE"]}
    assert _cli(users, "adduser", "alice").returncode == 0
    s = Stub(users)  # 同一把密钥，只是关了共享口令
    try:
        assert s.req("GET", "/api/auth/verify", headers=_bearer(tok))[0] == 401
    finally:
        s.stop()


def test_cli_refuses_tokens_without_secret_or_identity(users):
    env = {k: v for k, v in os.environ.items() if k != "AUTH_SECRET"}
    r = subprocess.run([sys.executable, str(STUB), "token", "alice"], env={**env, **users},
                       capture_output=True, text=True)
    assert r.returncode != 0 and "AUTH_SECRET" in r.stderr and not r.stdout
    assert _cli({**users, **SECRET_ENV}, "token", "nobody").returncode != 0
    assert _cli({**users, **SECRET_ENV}, "token").returncode != 0  # 没开共享口令


def test_http_refuses_tokens_without_secret(users):
    s = Stub(users)
    s.proc.terminate(); s.proc.wait(5)
    env = {k: v for k, v in s.env.items() if k != "AUTH_SECRET"}
    s.proc = subprocess.Popen([sys.executable, str(STUB)], env={**{k: v for k, v in os.environ.items()
                              if k != "AUTH_SECRET"}, **env}, stderr=subprocess.PIPE, text=True)
    try:
        for _ in range(100):
            try:
                s.req("GET", "/api/auth/health")
                break
            except OSError:
                time.sleep(0.05)
        _, cookie = s.login(username="alice", password=PW)
        status, body = _http_token(s, cookie)
        assert status == 503 and body["error"] == "tokens_disabled"
    finally:
        s.stop()


def test_revocation_fails_closed(tmp_path):
    """删掉令牌文件不能让吊销过的令牌复活；启动时令牌文件坏着 = 令牌一律 401（Codex 审核）。"""

    tfile = tmp_path / "t.json"
    env = {"AUTH_PASSWORD": PW, "AUTH_TOKENS_FILE": str(tfile)}
    old = _token_cli(env)
    assert _cli({**env, **SECRET_ENV}, "revoke").returncode == 0
    s = Stub(env)
    try:
        assert s.req("GET", "/api/auth/verify", headers=_bearer(old))[0] == 401
        valid = _token_cli(env)
        time.sleep(2.6)
        assert s.req("GET", "/api/auth/verify", headers=_bearer(valid))[0] == 204
        tfile.unlink()  # 删文件 = 这一代全部作废，而不是吊销记录清零、旧令牌复活
        time.sleep(2.6)
        assert s.req("GET", "/api/auth/verify", headers=_bearer(valid))[0] == 401
        assert s.req("GET", "/api/auth/verify", headers=_bearer(old))[0] == 401
        _, cookie = s.login(password=PW)
        fresh = _http_token(s, cookie)[1]["token"]  # 重建一代，新令牌立即可用
        assert s.req("GET", "/api/auth/verify", headers=_bearer(fresh))[0] == 204
        assert s.req("GET", "/api/auth/verify", headers=_bearer(old))[0] == 401
    finally:
        s.stop()
    s = Stub(env)
    try:
        cookie = s.login(password=PW)[1]
        revoked = _http_token(s, cookie)[1]["token"]
        assert s.req("POST", "/api/auth/tokens/revoke", {}, cookie=cookie)[0] == 204  # 本进程立即生效
        gen = json.loads(tfile.read_text())["gen"]
        tfile.write_text(json.dumps({"gen": gen}))  # 半坏（同一代、缺 epochs）：不能当"没吊销过"
        time.sleep(2.6)
        assert s.req("GET", "/api/auth/verify", headers=_bearer(revoked))[0] == 401
    finally:
        s.stop()
    s = Stub(env)  # 带着半坏的文件重启：一样拒绝
    try:
        assert s.req("GET", "/api/auth/verify", headers=_bearer(revoked))[0] == 401
    finally:
        s.stop()
    tfile.write_text("{坏的")
    s = Stub(env)
    try:
        assert s.req("GET", "/api/auth/verify", headers=_bearer(old))[0] == 401
        _, cookie = s.login(password=PW)  # cookie 登录不受影响
        assert s.req("GET", "/api/auth/verify", cookie=cookie)[0] == 204
    finally:
        s.stop()


def test_changing_shared_password_kills_shared_tokens(tmp_path):
    env = {"AUTH_PASSWORD": PW, "AUTH_TOKENS_FILE": str(tmp_path / "t.json")}
    tok = _token_cli(env)
    s = Stub({**env, "AUTH_PASSWORD": "a-new-shared-pw"})
    try:
        assert s.req("GET", "/api/auth/verify", headers=_bearer(tok))[0] == 401
    finally:
        s.stop()


def test_shared_password_with_absent_users_file_does_not_spam_log(tmp_path):
    # 发布版：只开共享口令、AUTH_USERS_FILE 指向还不存在的文件。以前后台线程每 2 秒
    # 报一次「读账号文件失败」（Windows 验收）。
    s = Stub({"AUTH_PASSWORD": PW, "AUTH_USERS_FILE": str(tmp_path / "users.json")})
    try:
        assert s.login(password=PW)[0] == 204
        time.sleep(4.5)  # 跨过两轮 RELOAD_EVERY
    finally:
        s.stop()
    assert "读账号文件失败" not in s.proc.stderr.read()


# ── v1.4：权限范围、匿名上报、单个吊销 ─────────────────────────────────────────────

import hashlib  # noqa: E402
import hmac  # noqa: E402
import importlib.util  # noqa: E402

START = "/api/core/agents/start"
REPORT_URIS = (START, "/api/core/agents/run_0123abcd/phase", "/api/core/agents/run_0123abcd/stop",
               "/api/core/agents/run_0123abcd/heartbeat", START + "?x=1&y=../../hive")


def _v(stub, method, uri, token=None, cookie="", extra=None):
    """像网关那样问 verify：原始方法与 URI 放在 X-Original-* 里。返回 (状态码, 响应头)。"""
    h = {"X-Original-Method": method, "X-Original-URI": uri, **(extra or {})}
    if token is not None:
        h["Authorization"] = f"Bearer {token}"
    status, headers, body = stub.req("GET", "/api/auth/verify", cookie=cookie, headers=h)
    assert body == b""  # 不变量 1
    return status, headers


def _mint(stub, cookie, scope=None, name=None):
    body = {k: v for k, v in (("scope", scope), ("name", name)) if v is not None}
    status, data = _http_token(stub, cookie, body)
    assert status == 201, data
    return data


@pytest.fixture
def single(tmp_path):
    """单人模式：只开共享口令。"""
    s = Stub({"AUTH_PASSWORD": PW, "AUTH_TOKENS_FILE": str(tmp_path / "t.json")})
    yield s
    s.stop()


def test_scope_matrix(stub, users):
    """契约「放行表」：三种范围各能过哪些「方法 + 路径」，过的时候回什么范围头。"""
    alice = json.loads(Path(users["AUTH_USERS_FILE"]).read_text())["users"]["alice"]["id"]
    _, cookie = stub.login(username="alice", password=PW)
    tok = {sc: _mint(stub, cookie, sc)["token"] for sc in ("report", "read", "write")}
    assert all(t.startswith("hct2.") for t in tok.values())
    for sc, t in tok.items():
        for uri in REPORT_URIS:  # 上报面：三种都行
            status, h = _v(stub, "POST", uri, t)
            assert (status, h.get("X-Nexus-Scope"), h.get("X-Nexus-Tenant")) == (204, sc, alice), (sc, uri)
            assert "X-Nexus-Anonymous" not in h
    cases = [  # (方法, 路径, report, read, write)
        ("GET", "/api/core/views/tree", 403, 204, 204),
        ("GET", "/api/core/views/lanes?date=2026-10-09", 403, 204, 204),
        ("GET", "/api/core/events", 403, 204, 204),
        ("GET", START, 403, 204, 204),                       # 上报端点的 GET 不是上报
        ("POST", "/api/core/events", 403, 403, 204),         # 上传事件
        ("POST", "/api/core/activity/presence", 403, 403, 204),
        ("POST", "/api/core/timer/start", 403, 403, 204),
        ("PUT", "/api/core/detector/settings", 403, 403, 204),
        ("DELETE", "/api/core/views/tree", 403, 403, 204),
        ("HEAD", "/api/core/views/tree", 403, 403, 204),
        ("OPTIONS", START, 403, 403, 204),
        ("get", "/api/core/views/tree", 403, 403, 204),      # 方法大小写：不在表里
        ("POST", "/api/mcp/", 403, 204, 204),                # MCP：report 整个不行
        ("GET", "/api/mcp/", 403, 403, 204),
        ("DELETE", "/api/mcp/", 403, 403, 204),
    ]
    for method, uri, *want in cases:
        for sc, expected in zip(("report", "read", "write"), want):
            assert _v(stub, method, uri, tok[sc])[0] == expected, (sc, method, uri)
    # 页面、api/auth、api/agent：任何范围都是 401（令牌只开接口，v1.2）
    for uri in ("/hive/", "/api/auth/tokens", "/api/agent/x", "/"):
        for t in tok.values():
            assert _v(stub, "POST", uri, t)[0] == 401 and _v(stub, "GET", uri, t)[0] == 401
    # 人的会话：不带范围头，哪儿都行
    status, h = _v(stub, "DELETE", "/hive/", cookie=cookie)
    assert status == 204 and "X-Nexus-Scope" not in h and "X-Nexus-Anonymous" not in h


def test_streaming_and_upgrade_requests_get_no_extra_reach(stub, users):
    """SSE / WebSocket 形的请求（Accept: text/event-stream、Upgrade: websocket）在门里与普通 GET 同一张表：
    report / 匿名读不到任何 GET（SSE 就是 GET），聊天后端（api/agent/，SSE）任何令牌都是 401；
    Upgrade / Connection 头不改变判定（nginx 也不透传它们，见 tools/test_install.py）。"""
    _, cookie = stub.login(username="alice", password=PW)
    report, read = _mint(stub, cookie, "report")["token"], _mint(stub, cookie, "read")["token"]
    live = {"Accept": "text/event-stream", "Upgrade": "websocket", "Connection": "Upgrade"}
    for uri in ("/api/core/views/lanes", "/api/core/events/stream", "/api/core/ws"):
        assert _v(stub, "GET", uri, report, extra=live)[0] == 403, uri
        assert _v(stub, "GET", uri, None, extra=live)[0] == 401, uri       # 匿名（这里是多账号，本来就没有）
        assert _v(stub, "GET", uri, read, extra=live)[0] == _v(stub, "GET", uri, read)[0] == 204  # read 本来就能 GET
    for uri in ("/api/agent/chat", "/api/agent/chat/stream", "/api/agent/ws", "/api/agent/"):
        for t in (report, read):
            assert _v(stub, "GET", uri, t, extra=live)[0] == 401, uri
            assert _v(stub, "POST", uri, t, extra=live)[0] == 401, uri


def test_anonymous_gets_no_stream_or_agent_chat(single):
    live = {"Accept": "text/event-stream", "Upgrade": "websocket", "Connection": "Upgrade"}
    for uri in ("/api/core/views/lanes", "/api/agent/chat", "/api/agent/chat/stream", "/api/mcp/"):
        assert _v(single, "GET", uri, extra=live)[0] == 401, uri


def test_allow_table_cannot_be_bypassed(stub, users):
    """上报面按原样字节全匹配：编码、双斜杠、结尾斜杠、大小写、点段、方法覆盖头，全都不在表里。"""
    _, cookie = stub.login(username="alice", password=PW)
    report, read = _mint(stub, cookie, "report")["token"], _mint(stub, cookie, "read")["token"]
    bad = [
        START + "/", "/api/core/agents//start", "//api/core/agents/start", "/api/core//agents/start",
        "/api/core/agents/start/.", "/api/core/agents/./start", "/api/core/agents/x/../start",
        "/api/core/agents/%73tart", "/api/core/agents%2Fstart", "/api/core/agents/start%2F",
        "/api/core/agents/start%3F", "/api/core/agents/start%00", "/api/core/agents/start;x=1",
        "/api/core/agents/Start", "/API/core/agents/start", "/api/core/Agents/start",
        "/api/core/agents/run_1/phase/", "/api/core/agents/run_1/Phase", "/api/core/agents/run_1/stop/extra",
        "/api/core/agents/run_1/cancel", "/api/core/agents/run%201/stop", "/api/core/agents/a.b/stop",
        "/api/core/agents/" + "r" * 65 + "/stop", "/api/core/agents//stop", "/api/core/agents/run_1//stop",
        "/api/core/agents/start#x", "/api/core/agents/start\\", "/api/core/timer/start",
        "/api/core/agents/..%2ftimer/start", "/api/core/agents/%2e%2e/timer/start",
        "/Cockpit/api/core/agents/start", "/api/corex/agents/start", "http://x/api/core/agents/start",
    ]
    for uri in bad:
        for t in (report, None):  # report 令牌与（多账号下的）匿名都过不了
            assert _v(stub, "POST", uri, t)[0] in (401, 403), uri
        assert _v(stub, "POST", uri, report)[0] != 204, uri
        assert _v(stub, "POST", uri, read)[0] != 204, uri  # read 也没有别的 POST
    # read 的 GET 沿用 v1.2 的判法：点段 / 编码的点段 / 反斜杠出不了 api/core
    for uri in ("/api/core/%2e%2e/hive/", "/api/core/../hive/", "/api/core/..%2Fhive/", "/api/mcp/..%2f..%2fhive/",
                "/api/core/%5c..%5chive/"):
        assert _v(stub, "GET", uri, read)[0] == 401, uri
    # 方法只认 X-Original-Method：覆盖头不读，缺了头就什么都过不了
    for name in ("X-HTTP-Method-Override", "X-Method-Override", "X-HTTP-Method"):
        assert _v(stub, "POST", "/api/core/events", read, extra={name: "GET"})[0] == 403
        assert _v(stub, "GET", START, report, extra={name: "POST"})[0] == 403
    for t in (report, read):
        status, _, _ = stub.req("GET", "/api/auth/verify", headers=_bearer(t, START))  # 老网关：没有 X-Original-Method
        assert status == 403
    # verify 自己的请求方法不算数（子请求永远是 GET）
    assert _v(stub, "POST", "/api/core/views/tree", read)[0] == 403


def test_forged_or_edited_tokens_are_rejected(stub, users):
    """范围、令牌 id、到期、租户都在签名里：改任何一段都是 401。"""
    _, cookie = stub.login(username="alice", password=PW)
    minted = _mint(stub, cookie, "report")
    tok = minted["token"]
    prefix, issued, exp, epoch, scope, jti, rest = tok.split(".", 6)
    tenant, sig = rest.rsplit(".", 1)
    assert (prefix, scope, jti) == ("hct2", "report", minted["id"])
    other = _mint(stub, cookie, "write")
    o = other["token"].split(".")
    forged = [
        ".".join([prefix, issued, exp, epoch, "write", jti, tenant, sig]),          # 改范围
        ".".join([prefix, issued, exp, epoch, "read", jti, tenant, sig]),
        ".".join([prefix, issued, exp, epoch, "admin", jti, tenant, sig]),
        ".".join([prefix, issued, exp, epoch, "", jti, tenant, sig]),
        ".".join([prefix, issued, str(int(exp) + 9), epoch, scope, jti, tenant, sig]),  # 延期
        ".".join([prefix, issued, exp, epoch, scope, other["id"], tenant, sig]),      # 换成另一个有效令牌的 id
        ".".join([prefix, issued, exp, epoch, scope, jti, "u_local", sig]),           # 换租户
        ".".join([prefix, issued, exp, epoch, "write", jti, tenant, o[-1]]),          # 拼别的令牌的签名
        ".".join([prefix, issued, exp, epoch, scope, jti, tenant, sig[:-1] + ("0" if sig[-1] != "0" else "1")]),
        ".".join([prefix, issued, exp, epoch, scope, jti, tenant, ""]),
        ".".join([prefix, issued, exp, epoch, scope, jti, tenant]),
        tok + ".", tok[:-8], "hct3" + tok[4:], "hct1" + tok[4:],
        ".".join(["hct1", issued, epoch, tenant, sig]),                                # 把各段挪进 hct1 的外壳
        ".".join(["hct1", issued, epoch, f"write.{jti}.{tenant}", sig]),
    ]
    for f in forged:
        for method, uri in (("POST", START), ("GET", "/api/core/views/tree")):
            assert _v(stub, method, uri, f)[0] == 401, f[:40]
    assert _v(stub, "POST", START, tok)[0] == 204  # 原样的那个照常


def test_hct1_tokens_keep_working_as_write(tmp_path):
    """老格式照常可用 = write；hct1 与 hct2 的签名域不同，互相冒充不了。"""
    tfile = tmp_path / "t.json"
    env = {"AUTH_PASSWORD": PW, "AUTH_TOKENS_FILE": str(tfile)}
    s = Stub(env)
    try:
        _, cookie = s.login(password=PW)
        new = _mint(s, cookie, "report")["token"]  # 顺带把令牌文件建出来
        gen = json.loads(tfile.read_text())["gen"]
        sess = hashlib.sha256(f"shared|{PW}".encode()).hexdigest()
        sign = lambda domain, payload: hmac.new(("s" * 32).encode(), f"{domain}|{payload}|{sess}|{gen}".encode(),  # noqa: E731
                                                hashlib.sha256).hexdigest()
        payload = f"hct1.{int(time.time())}.0."
        old = f"{payload}.{sign('device', payload)}"
        status, h = _v(s, "POST", "/api/core/events", old)
        assert (status, h.get("X-Nexus-Scope")) == (204, "write") and "X-Nexus-Tenant" not in h
        assert s.req("GET", "/api/auth/verify", headers=_bearer(old))[0] == 204  # 老网关（不转方法）也行
        # 用 hct1 的签名域签一个 hct2 形状的载荷（或反过来）：不认
        p2 = new.rsplit(".", 1)[0].replace(".report.", ".write.")
        assert _v(s, "POST", "/api/core/events", f"{p2}.{sign('device', p2)}")[0] == 401
        assert _v(s, "POST", "/api/core/events", f"{payload}.{sign('device2', payload)}")[0] == 401
        # hct1 列不出来、也没有 id
        tokens = json.loads(s.req("GET", "/api/auth/tokens", cookie=cookie)[2])["tokens"]
        assert [t["scope"] for t in tokens] == ["report"]
        # 整个身份吊销照样作废 hct1
        assert s.req("POST", "/api/auth/tokens/revoke", {}, cookie=cookie)[0] == 204
        assert _v(s, "POST", "/api/core/events", old)[0] == 401
    finally:
        s.stop()


def test_client_supplied_headers_do_not_reach_the_decision(stub, users):
    """客户端自己写的范围 / 匿名 / 租户头：verify 一个都不读。"""
    _, cookie = stub.login(username="alice", password=PW)
    report = _mint(stub, cookie, "report")["token"]
    lies = {"X-Nexus-Scope": "write", "X-Nexus-Anonymous": "1", "X-Nexus-Tenant": "u_local"}
    status, h = _v(stub, "GET", "/api/core/views/tree", report, extra=lies)
    assert status == 403
    status, h = _v(stub, "POST", START, report, extra=lies)
    assert status == 204 and h["X-Nexus-Scope"] == "report" and h["X-Nexus-Tenant"] != "u_local"
    assert "X-Nexus-Anonymous" not in h
    assert _v(stub, "POST", START, extra=lies)[0] == 401  # 多账号下没有匿名，自称匿名也没用


def test_anonymous_can_only_report_in_single_user_mode(single):
    for uri in REPORT_URIS:
        status, h = _v(single, "POST", uri)
        assert status == 204, uri
        assert (h.get("X-Nexus-Scope"), h.get("X-Nexus-Anonymous")) == ("report", "1")
        assert "X-Nexus-Tenant" not in h  # = u_local
    # 读不到任何东西、写不了别的、开不了页面
    for method, uri in (("GET", START), ("GET", "/api/core/views/tree"), ("GET", "/api/core/views/lanes"),
                        ("GET", "/api/core/events"), ("POST", "/api/mcp/"), ("GET", "/api/mcp/"),
                        ("POST", "/api/core/events"), ("POST", "/api/core/activity/presence"),
                        ("POST", "/api/core/timer/start"), ("HEAD", START), ("OPTIONS", START), ("PUT", START),
                        ("GET", "/hive/"), ("GET", "/"), ("POST", "/api/agent/x"), ("GET", "/__cockpit/current"),
                        ("POST", START + "/"), ("POST", "/api/core/agents//start"), ("POST", "/api/core/agents/%73tart")):
        assert _v(single, method, uri)[0] == 401, (method, uri)
    assert single.req("GET", "/api/auth/verify")[0] == 401  # 老网关（不转方法 / URI）：没有匿名
    assert single.req("GET", "/api/auth/verify", headers={"X-Original-URI": START})[0] == 401
    assert _v(single, "POST", START, extra={"X-HTTP-Method-Override": "GET"})[0] == 204  # 覆盖头不读，照常是 POST
    assert _v(single, "GET", START, extra={"X-HTTP-Method-Override": "POST"})[0] == 401


def test_presented_bad_credentials_are_never_downgraded_to_anonymous(single):
    """出示了坏凭据 = 401，哪怕不带它本来能匿名上报。否则吊销一个令牌只是把它降成匿名。"""
    _, cookie = single.login(password=PW)
    tok = _mint(single, cookie, "report")
    assert _v(single, "POST", START, tok["token"])[0] == 204
    assert single.req("POST", "/api/auth/tokens/revoke", {"tokenId": tok["id"]}, cookie=cookie)[0] == 204
    assert _v(single, "POST", START, tok["token"])[0] == 401  # 吊销了的令牌：不是「那就当匿名」
    for auth in ("Bearer", "Bearer ", "Bearer garbage", "bearer x.y", "Bearer hct2.1.2.0.report.00.x", "Basic Zm9vOmJhcg==",
                 "Digest x", "x", "Bearer " + "a" * 5000):
        status, h, _ = single.req("GET", "/api/auth/verify", headers={
            "X-Original-Method": "POST", "X-Original-URI": START, "Authorization": auth})
        assert status == 401 and "X-Nexus-Anonymous" not in h, auth[:20]
    # 坏的会话 cookie 同理（过期的会话去登录页）
    for bad in ("cockpit_session=garbage", "cockpit_session=1.2.3", cookie + "x"):
        assert _v(single, "POST", START, cookie=bad)[0] == 401, bad
    # 不相干的 cookie 等于没带；空的会话 cookie、空的 Authorization 是出示了（空的）凭据 = 401
    assert _v(single, "POST", START, cookie="theme=dark")[0] == 204
    assert _v(single, "POST", START, cookie="cockpit_session=")[0] == 401
    assert _v(single, "POST", START, extra={"Authorization": ""})[0] == 401
    # 两个 Authorization 头：不猜看哪个
    c = http.client.HTTPConnection("127.0.0.1", single.port, timeout=10)
    c.putrequest("GET", "/api/auth/verify")
    for k, v in (("X-Original-Method", "POST"), ("X-Original-URI", START), ("Authorization", ""),
                 ("Authorization", "")):
        c.putheader(k, v)
    c.endheaders()
    assert c.getresponse().status == 401
    c.close()
    # 有效的人的会话照常，不被标成匿名
    status, h = _v(single, "POST", START, cookie=cookie)
    assert status == 204 and "X-Nexus-Anonymous" not in h and "X-Nexus-Scope" not in h


def test_anonymous_is_off_with_accounts_and_with_the_switch(stub, tmp_path, users):
    # 多账号：没有可归属的租户
    assert _v(stub, "POST", START)[0] == 401
    assert json.loads(stub.req("GET", "/api/auth/health")[2])["anonymousReport"] is False
    # 共享口令 + 账号同时开：同样不放
    mixed = Stub({**users, "AUTH_PASSWORD": PW})
    try:
        assert _v(mixed, "POST", START)[0] == 401
        assert json.loads(mixed.req("GET", "/api/auth/health")[2])["anonymousReport"] is False
    finally:
        mixed.stop()
    # 单人模式但开关关掉
    off = Stub({"AUTH_PASSWORD": PW, "AUTH_ANONYMOUS_REPORT": "false", "AUTH_TOKENS_FILE": str(tmp_path / "t.json")})
    try:
        assert _v(off, "POST", START)[0] == 401
        assert json.loads(off.req("GET", "/api/auth/health")[2])["anonymousReport"] is False
        _, cookie = off.login(password=PW)  # 令牌不受开关影响
        assert _v(off, "POST", START, _mint(off, cookie, "report")["token"])[0] == 204
    finally:
        off.stop()
    assert "匿名上报开着" not in off.proc.stderr.read()
    r = subprocess.run([sys.executable, str(STUB)], env={**os.environ, "AUTH_PASSWORD": PW, "AUTH_ANONYMOUS_REPORT": "maybe"},
                       capture_output=True, text=True, timeout=10)
    assert r.returncode != 0 and "AUTH_ANONYMOUS_REPORT" in r.stderr  # 看不懂的取值拒绝启动，不静默当开


def test_anonymous_follows_base_path(tmp_path):
    s = Stub({"AUTH_PASSWORD": PW, "AUTH_BASE_PATH": "/Cockpit/"})
    try:
        assert _v(s, "POST", "/Cockpit" + START)[0] == 204
        assert _v(s, "POST", START)[0] == 401
        assert _v(s, "POST", "/Cockpit/Cockpit" + START)[0] == 401
        assert _v(s, "POST", "/cockpit" + START)[0] == 401
        assert _v(s, "GET", "/Cockpit/api/core/views/tree")[0] == 401
    finally:
        s.stop()
    assert "匿名上报开着" in s.proc.stderr.read()  # 启动横幅照直说


def test_list_and_per_token_revoke(stub, users):
    _, alice = stub.login(username="alice", password=PW)
    _, bob = stub.login(username="bob", password=PW)
    a1, a2 = _mint(stub, alice, "report", "沙箱代理"), _mint(stub, alice, "read", "mcp")
    b1 = _mint(stub, bob, "write")
    status, _, data = stub.req("GET", "/api/auth/tokens", cookie=alice)
    listed = json.loads(data)["tokens"]
    assert status == 200 and [(t["id"], t["name"], t["scope"]) for t in listed] == [
        (a1["id"], "沙箱代理", "report"), (a2["id"], "mcp", "read")]
    assert all(set(t) == {"id", "name", "scope", "createdAt", "expiresAt"} for t in listed)
    # 列表、令牌文件里都没有令牌本身，也没有它的签名
    tfile = Path(users["AUTH_USERS_FILE"]).with_name("tokens.json").read_text()
    for minted in (a1, a2, b1):
        sig = minted["token"].rsplit(".", 1)[1]
        assert minted["token"] not in data.decode() and sig not in data.decode()
        assert minted["token"] not in tfile and sig not in tfile
    # 列表只认会话 cookie：设备令牌（哪怕 write）、没登录都看不到
    assert stub.req("GET", "/api/auth/tokens", headers={"Authorization": f"Bearer {a2['token']}"})[0] == 401
    assert stub.req("GET", "/api/auth/tokens")[0] == 401
    # 吊销别人的令牌：与「没有这个 id」同一个 404，且没生效
    for cookie, jti in ((bob, a1["id"]), (alice, "0000000000000000")):
        status, _, body = stub.req("POST", "/api/auth/tokens/revoke", {"tokenId": jti}, cookie=cookie)
        assert status == 404 and json.loads(body)["error"] == "no_such_token"
    assert _v(stub, "POST", START, a1["token"])[0] == 204
    # 给了 tokenId 就必须合格：null / 空串不会悄悄变成「吊销全部」
    for bad in ("", "xyz", 5, None, "AAAAAAAAAAAAAAAA", "00000000000000000", ["x"]):
        status, _, body = stub.req("POST", "/api/auth/tokens/revoke", {"tokenId": bad}, cookie=bob)
        assert status == 400 and json.loads(body)["error"] == "bad_token_id", bad
    assert _v(stub, "POST", "/api/core/events", b1["token"])[0] == 204
    # 吊销自己的一个：立即生效，另一个与别人的不受影响，列表里标出来
    assert stub.req("POST", "/api/auth/tokens/revoke", {"tokenId": a1["id"]}, cookie=alice)[0] == 204
    assert _v(stub, "POST", START, a1["token"])[0] == 401
    assert _v(stub, "GET", "/api/core/views/tree", a2["token"])[0] == 204
    assert _v(stub, "POST", "/api/core/events", b1["token"])[0] == 204
    assert stub.req("POST", "/api/auth/tokens/revoke", {"tokenId": a1["id"]}, cookie=alice)[0] == 404  # 记录已删
    listed = json.loads(stub.req("GET", "/api/auth/tokens", cookie=alice)[2])["tokens"]
    assert [t["id"] for t in listed] == [a2["id"]]  # 吊销的不再列出
    # 设备令牌换不出、也吊销不了令牌
    h = {"Authorization": f"Bearer {a2['token']}"}
    assert stub.req("POST", "/api/auth/tokens", {}, headers=h)[0] == 401
    assert stub.req("POST", "/api/auth/tokens/revoke", {"tokenId": a2["id"]}, headers=h)[0] == 401
    # 整个身份吊销：全部作废，记录清空；别人的还在
    assert stub.req("POST", "/api/auth/tokens/revoke", {}, cookie=alice)[0] == 204
    assert _v(stub, "GET", "/api/core/views/tree", a2["token"])[0] == 401
    assert json.loads(stub.req("GET", "/api/auth/tokens", cookie=alice)[2])["tokens"] == []
    assert _v(stub, "POST", "/api/core/events", b1["token"])[0] == 204


def test_mint_validation(stub, users):
    _, cookie = stub.login(username="alice", password=PW)
    for body, error in (({"scope": "admin"}, "bad_scope"), ({"scope": ""}, "bad_scope"), ({"scope": None}, "bad_scope"),
                        ({"scope": ["write"]}, "bad_scope"), ({"scope": "Write"}, "bad_scope"),
                        ({"name": "x" * 65}, "bad_label"), ({"name": 5}, "bad_label"),
                        ({"name": "a\nb"}, "bad_label"), ({"name": "a\x1b[31m"}, "bad_label"),
                        ({"name": "a\x7f"}, "bad_label")):
        status, data = _http_token(stub, cookie, body)
        assert status == 400 and data["error"] == error, body
    assert json.loads(stub.req("GET", "/api/auth/tokens", cookie=cookie)[2])["tokens"] == []  # 一个都没发出去
    assert _mint(stub, cookie)["scope"] == "write"  # 缺省 write：老脚本不用改
    assert _mint(stub, cookie, name="<b>名字</b>")["name"] == "<b>名字</b>"  # 原样存，转义是显示方的事


def test_token_table_is_bounded_and_expired_entries_are_pruned(tmp_path):
    tfile = tmp_path / "t.json"
    env = {"AUTH_PASSWORD": PW, "AUTH_TOKENS_FILE": str(tfile)}
    s = Stub(env)
    try:
        _, cookie = s.login(password=PW)
        first = _mint(s, cookie, "report")
        for _ in range(99):
            _mint(s, cookie, "report")
        status, data = _http_token(s, cookie, {"scope": "report"})
        assert status == 409 and data["error"] == "too_many_tokens"
        assert len(json.loads(tfile.read_text())["tokens"]) == 100
        r = _cli({**env, **SECRET_ENV}, "token")
        assert r.returncode != 0 and not r.stdout and "100" in r.stderr
        # 单个吊销 = 删记录，名额立即回来（面板与命令行都是这么劝人的）
        assert s.req("POST", "/api/auth/tokens/revoke", {"tokenId": first["id"]}, cookie=cookie)[0] == 204
        assert len(json.loads(tfile.read_text())["tokens"]) == 99
        assert _http_token(s, cookie, {})[0] == 201
        assert _http_token(s, cookie, {})[0] == 409
        assert s.req("POST", "/api/auth/tokens/revoke", {}, cookie=cookie)[0] == 204
        assert json.loads(tfile.read_text())["tokens"] == {}
        assert _http_token(s, cookie, {})[0] == 201
    finally:
        s.stop()
    # 到期的条目在下一次写文件时清掉，列表里也不出现
    tfile2 = tmp_path / "t2.json"
    env2 = {"AUTH_PASSWORD": PW, "AUTH_TOKENS_FILE": str(tfile2), "AUTH_TOKEN_DAYS": "0"}
    s = Stub(env2)
    try:
        _, cookie = s.login(password=PW)
        old = [_mint(s, cookie)["id"] for _ in range(5)]  # 有效期 0：一发出来就到期
        time.sleep(1.1)
        assert json.loads(s.req("GET", "/api/auth/tokens", cookie=cookie)[2])["tokens"] == []
        fresh = _mint(s, cookie)["id"]
        assert set(json.loads(tfile2.read_text())["tokens"]) == {fresh} and fresh not in old
    finally:
        s.stop()


def test_cli_scopes_list_and_revoke_token(stub, users):
    r = _cli({**users, **SECRET_ENV}, "token", "alice", "--scope", "report", "--name", "ci agent")
    assert r.returncode == 0 and r.stdout.startswith("hct2.") and ".report." in r.stdout
    tok = r.stdout.strip()
    assert tok not in r.stderr and tok.rsplit(".", 1)[1] not in r.stderr  # 令牌只在标准输出
    jti = tok.split(".")[5]
    assert jti in r.stderr and "report" in r.stderr
    write = _token_cli(users, "alice")  # 缺省 write
    assert ".write." in write
    time.sleep(2.6)
    assert _v(stub, "POST", START, tok)[0] == 204 and _v(stub, "GET", "/api/core/views/tree", tok)[0] == 403
    listing = _cli(users, "tokens", "alice")
    assert listing.returncode == 0 and f"{jti}\treport\t有效" in listing.stdout and "ci agent" in listing.stdout
    assert tok not in listing.stdout and tok.rsplit(".", 1)[1] not in listing.stdout
    assert _cli(users, "tokens", "bob").stdout == ""
    assert jti in _cli(users, "tokens").stdout  # 不给名字 = 全部身份
    assert _cli(users, "revoke-token", jti).returncode == 0
    time.sleep(2.6)
    assert _v(stub, "POST", START, tok)[0] == 401
    assert _v(stub, "POST", "/api/core/events", write)[0] == 204
    assert jti not in _cli(users, "tokens", "alice").stdout  # 吊销 = 删记录
    for args in (("revoke-token", "nope"), ("revoke-token",), ("revoke-token", "0000000000000000"),
                 ("token", "alice", "--scope", "admin"), ("token", "alice", "--scope"),
                 ("token", "alice", "--name", "x" * 65), ("token", "alice", "--name", "a\x1bb"),
                 ("revoke", "alice", "--scope", "read")):
        r = _cli({**users, **SECRET_ENV}, *args)
        assert r.returncode != 0 and not r.stdout, args


def test_tokens_never_reach_the_log(tmp_path):
    env = {"AUTH_PASSWORD": PW, "AUTH_TOKENS_FILE": str(tmp_path / "t.json")}
    s = Stub(env)
    secrets_seen = []
    try:
        _, cookie = s.login(password=PW)
        for scope in ("report", "read", "write"):
            tok = _mint(s, cookie, scope, "log-check")
            secrets_seen += [tok["token"], tok["token"].rsplit(".", 1)[1]]
            _v(s, "POST", START, tok["token"])
            _v(s, "GET", "/api/core/views/tree", tok["token"])
            s.req("POST", "/api/auth/tokens", {}, headers={"Authorization": f"Bearer {tok['token']}"})
            s.req("POST", "/api/auth/tokens/revoke", {"tokenId": tok["id"]}, cookie=cookie)
            _v(s, "POST", START, tok["token"])
        s.req("GET", "/api/auth/tokens", cookie=cookie)
        _v(s, "POST", START)
        secrets_seen.append(cookie.partition("=")[2])
    finally:
        s.stop()
    log = s.proc.stderr.read()
    assert "/api/auth/tokens" in log and "/api/auth/verify" not in log  # 别的端点照记，verify 不写日志
    for secret in secrets_seen:
        assert secret not in log


# ── 进程内：verify 不碰磁盘、不抛、比较是定时安全的、重读不漏 ────────────────────────


@pytest.fixture
def mod(tmp_path, monkeypatch):
    """把占位件当模块导入（配置在导入时读环境变量）。"""
    for k in [k for k in os.environ if k.startswith("AUTH_")]:
        monkeypatch.delenv(k)
    monkeypatch.setenv("AUTH_PASSWORD", PW)
    monkeypatch.setenv("AUTH_SECRET", "s" * 32)
    monkeypatch.setenv("AUTH_TOKENS_FILE", str(tmp_path / "t.json"))
    spec = importlib.util.spec_from_file_location("auth_stub_under_test", STUB)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    m.ACCOUNTS.refresh()  # main() 在开始服务前做的那次
    return m


def _headers(method="POST", uri=START, **extra):
    h = http.client.HTTPMessage()
    h["X-Original-Method"], h["X-Original-URI"] = method, uri
    for k, v in extra.items():
        h[k] = v
    return h


def test_verify_never_touches_the_disk(mod, monkeypatch):
    """不变量 2：令牌、范围、单个吊销、匿名——verify 的每条路径都只读内存。"""
    report, meta = mod.issue_device_token("", mod._shared_sess(), "report", "x")
    revoked, rmeta = mod.issue_device_token("", mod._shared_sess(), "write", "y")
    assert mod.revoke_token(rmeta["id"])
    mod.EPOCHS.refresh()
    cookie = f"{mod.COOKIE_NAME}={mod.issue_token()}"

    def boom(*_a, **_k):
        raise AssertionError("verify 碰了磁盘")

    import builtins  # noqa: PLC0415
    for target, name in ((builtins, "open"), (os, "open"), (os, "stat"), (os, "replace"), (os, "listdir"),
                         (os.path, "exists"), (os.path, "getsize"), (mod, "load_token_state"),
                         (mod, "token_state"), (mod, "load_users"), (mod, "_locked")):
        monkeypatch.setattr(target, name, boom)
    assert mod.verify_access(_headers(Authorization=f"Bearer {report}")) == (204, {"X-Nexus-Scope": "report"})
    assert mod.verify_access(_headers("GET", "/api/core/views/tree", Authorization=f"Bearer {report}")) == (403, {})
    assert mod.verify_access(_headers(Authorization=f"Bearer {revoked}")) == (401, {})
    assert mod.verify_access(_headers(Authorization="Bearer garbage")) == (401, {})
    assert mod.verify_access(_headers("GET", "/hive/", Cookie=cookie)) == (204, {})
    assert mod.verify_access(_headers()) == (204, {"X-Nexus-Scope": "report", "X-Nexus-Anonymous": "1"})
    assert mod.verify_access(_headers("GET")) == (401, {})


def test_verify_turns_any_internal_error_into_401(mod, monkeypatch):
    """不变量 3：里面哪一步抛了都是 401，不是 500。"""
    tok, _ = mod.issue_device_token("", mod._shared_sess(), "read")
    mod.EPOCHS.refresh()
    assert mod.verify_access(_headers("GET", "/api/core/x", Authorization=f"Bearer {tok}"))[0] == 204

    def boom(*_a, **_k):
        raise RuntimeError("内部错误")

    for name in ("scope_allows", "device_identity", "_api_area", "_report_uri", "token_tenant", "_cookie_value"):
        with monkeypatch.context() as m:
            m.setattr(mod, name, boom)
            for h in (_headers("GET", "/api/core/x", Authorization=f"Bearer {tok}"), _headers(),
                      _headers(Cookie="cockpit_session=1.x")):
                status, extra = mod.verify_access(h)
                assert status in (204, 401, 403) and (status == 204 or extra == {}), name
            assert mod.verify_access(_headers("GET", "/api/core/x", Authorization=f"Bearer {tok}"))[0] in (401, 204)
    # 令牌状态坏了（None / 形状不对）：令牌 401，别的不受影响
    for state in (None, ("gen",), ("gen", {}, None), "x"):
        mod.EPOCHS.state = state
        assert mod.verify_access(_headers("GET", "/api/core/x", Authorization=f"Bearer {tok}")) == (401, {})
        assert mod.verify_access(_headers())[0] == 204
    assert mod.verify_access(None) == (401, {})  # 连请求头对象都不对
    for weird in ("Bearer \x00", "Bearer " + "é" * 40, "Bearer hct2." + "." * 5000, "Bearer hct2.a.b.c.d.e.f.g"):
        assert mod.verify_access(_headers(Authorization=weird)) == (401, {})


def test_signatures_are_compared_in_constant_time(mod, monkeypatch):
    """签名不对的令牌（hct1、hct2）与会话 cookie 都经 hmac.compare_digest，不是 ==。"""
    tok, _ = mod.issue_device_token("", mod._shared_sess(), "report")
    mod.EPOCHS.refresh()
    calls = []
    real = hmac.compare_digest

    def spy(a, b):
        calls.append((a, b))
        return real(a, b)

    monkeypatch.setattr(mod.hmac, "compare_digest", spy)
    bad = tok[:-1] + ("0" if tok[-1] != "0" else "1")
    for candidate, n in ((bad, 1), (tok, 2), (f"hct1.{int(time.time())}.0..deadbeef", 3)):
        mod.verify_access(_headers(Authorization=f"Bearer {candidate}"))
        assert len(calls) == n
    mod.verify_access(_headers("GET", "/hive/", Cookie=f"{mod.COOKIE_NAME}=1.deadbeef"))
    assert len(calls) == 4
    # 范围不对 / 已吊销是在签名**之后**才看的：没有密钥的人分不出这些情形
    forged = tok.replace(".report.", ".write.")
    assert mod.device_identity(forged) is None and calls[-1][0] == forged.rsplit(".", 1)[1]


def test_reload_notices_a_second_write_in_the_same_timestamp_tick(mod):
    """吊销紧跟着发令牌、两次写落在同一个 mtime 刻度里：重读不能漏（否则吊销永远不生效）。"""
    tok, meta = mod.issue_device_token("", mod._shared_sess(), "write")
    mod.EPOCHS.refresh()
    before = os.stat(mod.TOKENS_FILE)
    assert mod.verify_access(_headers(Authorization=f"Bearer {tok}"))[0] == 204
    assert mod.revoke_token(meta["id"])
    os.utime(mod.TOKENS_FILE, ns=(before.st_atime_ns, before.st_mtime_ns))  # 把时间戳拨回上一次写的那一刻
    assert os.stat(mod.TOKENS_FILE).st_mtime_ns == before.st_mtime_ns
    mod.EPOCHS.refresh()
    assert mod.verify_access(_headers(Authorization=f"Bearer {tok}")) == (401, {})


def test_token_table_is_an_allowlist(mod):
    """记录被删（手改文件）= 吊销；记录的租户与令牌里的不一致 = 拒绝。吊销记录回滚不了范围。"""
    tok, meta = mod.issue_device_token("", mod._shared_sess(), "read")
    mod.EPOCHS.refresh()
    gen, epochs, tokens = mod.EPOCHS.state
    ok = _headers("GET", "/api/core/x", Authorization=f"Bearer {tok}")
    assert mod.verify_access(ok)[0] == 204
    mod.EPOCHS.state = (gen, epochs, {})
    assert mod.verify_access(ok) == (401, {})
    mod.EPOCHS.state = (gen, epochs, {meta["id"]: {**tokens[meta["id"]], "tenant": "u_other"}})
    assert mod.verify_access(ok) == (401, {})
    # 表里把范围改成 write 也没用：范围以令牌签名里的为准
    mod.EPOCHS.state = (gen, epochs, {meta["id"]: {**tokens[meta["id"]], "scope": "write"}})
    assert mod.verify_access(_headers("POST", "/api/core/events", Authorization=f"Bearer {tok}")) == (403, {})
    # 到期：看签名里的到期时刻
    assert mod.device_identity(tok, now=int(time.time()) + mod.TOKEN_TTL + 5) is None
    assert mod.device_identity(tok, now=int(time.time()) - 5) is None  # 还没签发



def test_token_table_has_a_global_bound_and_prunes_expired_entries(mod, monkeypatch):
    """全部身份合计有硬顶（每个身份各自 100 不够：身份数无界，含已删账号的残留）；到期的在下一次写时清掉。"""
    monkeypatch.setattr(mod, "MAX_TOKENS_TOTAL", 5)
    metas = [mod.issue_device_token(f"u_{i}", "s", "report")[1] for i in range(5)]  # 每个身份 1 个：各自的上限碰不到
    with pytest.raises(mod.TooManyTokens):
        mod.issue_device_token("u_9", "s", "report")
    assert len(mod.load_token_state()[2]) == 5
    assert mod.revoke_token(metas[0]["id"])  # 吊销腾出名额
    mod.issue_device_token("u_9", "s", "report")
    with pytest.raises(mod.TooManyTokens):
        mod.issue_device_token("u_10", "s", "report")
    # 到期的在下一次写时先清掉：有效期 0 的令牌发多少个都撑不满上限
    monkeypatch.setattr(mod, "MAX_TOKENS_TOTAL", 1)
    monkeypatch.setattr(mod, "TOKEN_TTL", 0)
    for i in range(3):
        mod.revoke_identity(f"u_{i}"), mod.revoke_identity("u_9")
    mod.revoke_identity("u_3"), mod.revoke_identity("u_4")
    assert mod.load_token_state()[2] == {}
    for i in range(4):
        mod.issue_device_token("x", "s", "report")
    assert len(mod.load_token_state()[2]) == 1


def test_revoked_token_record_is_removed_not_kept(mod):
    tok, meta = mod.issue_device_token("", mod._shared_sess(), "write")
    assert mod.revoke_token(meta["id"]) and not mod.revoke_token(meta["id"])
    assert meta["id"] not in mod.load_token_state()[2]
    assert "revoked" not in meta
    # 老文件里开发期写过的墓碑：读进来就丢
    gen, epochs, tokens = mod.load_token_state()
    tokens["0" * 16] = {"tenant": "", "name": "", "scope": "write", "createdAt": 1, "expiresAt": 2 ** 40, "revoked": True}
    Path(mod.TOKENS_FILE).write_text(json.dumps({"gen": gen, "epochs": epochs, "tokens": tokens}))
    assert "0" * 16 not in mod.load_token_state()[2]


def test_present_but_empty_credentials_are_401_not_anonymous(mod):
    """出示了就不降级：空的 cockpit_session、空 / 全空白的 Authorization 都是 401，不是「没带」。"""
    assert mod.verify_access(_headers())[0] == 204  # 对照：真没带 = 匿名上报
    assert mod.verify_access(_headers(Cookie="theme=dark; other=1"))[0] == 204  # 别的 cookie 不算出示
    for extra in ({"Cookie": f"{mod.COOKIE_NAME}="}, {"Cookie": f"a=1; {mod.COOKIE_NAME}=; b=2"},
                  {"Cookie": f"{mod.COOKIE_NAME}"}, {"Authorization": ""}, {"Authorization": "   "},
                  {"Authorization": " \t"}, {"Authorization": "Bearer"}, {"Authorization": "Basic"}):
        assert mod.verify_access(_headers(**extra)) == (401, {}), extra


def test_verify_writes_no_log_but_other_endpoints_do(mod, monkeypatch, capsys):
    """不变量 2：verify 不做同步日志 IO（200 / 401 / 403 / 内部错误→401 全部），别的端点照记。"""
    import threading  # noqa: PLC0415
    from http.server import ThreadingHTTPServer  # noqa: PLC0415

    report, _ = mod.issue_device_token("", mod._shared_sess(), "report")
    mod.EPOCHS.refresh()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), mod.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    def get(path, headers):
        c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=10)
        c.request("GET", path, headers=headers)
        r = c.getresponse()
        r.read()
        c.close()
        return r.status

    api = {"X-Original-Method": "POST", "X-Original-URI": START}
    try:
        capsys.readouterr()
        assert get("/api/auth/verify", {**api, "Authorization": f"Bearer {report}"}) == 204
        assert get("/api/auth/verify", {**api, "Authorization": "Bearer nope"}) == 401
        assert get("/api/auth/verify", {"X-Original-Method": "GET", "X-Original-URI": "/api/core/views/tree",
                                        "Authorization": f"Bearer {report}"}) == 403
        monkeypatch.setattr(mod, "_verify", lambda _h: 1 / 0)
        assert get("/api/auth/verify", api) == 401
        out = capsys.readouterr()
        assert out.err == "" and out.out == ""
        assert get("/api/auth/health", {}) == 200 and get("/nope", {}) == 404  # 对照：别的端点照记
        assert capsys.readouterr().err.count("[auth-stub]") == 2
    finally:
        srv.shutdown()
        srv.server_close()


# ── 账号 / 匿名的判定不许往宽的一边漂：任何不确定 = 匿名关（见 Accounts 的文档）──────────────


def _anon(mod) -> bool:
    return mod.verify_access(_headers())[0] == 204


def _health(mod):
    import threading  # noqa: PLC0415
    from http.server import ThreadingHTTPServer  # noqa: PLC0415

    srv = ThreadingHTTPServer(("127.0.0.1", 0), mod.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=10)
        c.request("GET", "/api/auth/health")
        return c.getresponse().read()
    finally:
        srv.shutdown()
        srv.server_close()


@pytest.fixture
def accts(mod, tmp_path, monkeypatch):
    f = tmp_path / "users.json"
    monkeypatch.setattr(mod, "USERS_FILE", str(f))
    return mod, f


def _good_users(f, n=1):
    f.write_text(json.dumps({"users": {f"u{i}": {"id": f"id{i}", "sess": "ab"} for i in range(n)}}))


def test_anonymous_off_when_accounts_file_exists_but_cannot_be_read_at_start(accts):
    mod, f = accts
    for junk in ("{坏的", "", '{"users": ', "{}", '{"users": []}', '{"users": {"a": {}}}'):
        f.write_text(junk)
        mod.ACCOUNTS.__init__()  # 重新开始：从没读成功过
        mod.ACCOUNTS.refresh()
        assert not mod.ACCOUNTS.known and not _anon(mod), junk
        assert json.loads(_health(mod))["anonymousReport"] is False


def test_anonymous_off_when_accounts_path_is_a_directory(accts):
    mod, f = accts
    f.mkdir()
    mod.ACCOUNTS.refresh()
    assert not mod.ACCOUNTS.known and not _anon(mod)


def test_anonymous_off_when_accounts_file_is_unreadable_or_parent_not_a_directory(accts, tmp_path):
    mod, f = accts
    _good_users(f)
    f.chmod(0)
    try:
        if not os.access(f, os.R_OK):
            mod.ACCOUNTS.refresh()
            assert not mod.ACCOUNTS.known and not _anon(mod)
    finally:
        f.chmod(0o600)
    blocker = tmp_path / "blocker"
    blocker.write_text("x")
    mod.USERS_FILE = str(blocker / "users.json")  # ENOTDIR：不是 ENOENT，不算「确定没有」
    mod.ACCOUNTS.__init__()
    mod.ACCOUNTS.refresh()
    assert not mod.ACCOUNTS.known and not _anon(mod)


def test_confirmed_absent_file_keeps_single_user_anonymous_until_it_appears(accts):
    mod, f = accts
    mod.ACCOUNTS.refresh()
    assert mod.ACCOUNTS.known and _anon(mod)  # 对照：确定没有账号文件 = 单人模式
    f.write_text('{"users": {"u": {"id": "i", "sess"')  # 创建到一半：不是「没有」
    mod.ACCOUNTS.refresh()
    assert not mod.ACCOUNTS.known and not _anon(mod)
    _good_users(f)  # 写完：可读了，账号模式，匿名关
    mod.ACCOUNTS.refresh()
    assert mod.ACCOUNTS.by_id and not _anon(mod)


def test_accounts_view_survives_file_turning_bad_or_vanishing(accts):
    mod, f = accts
    _good_users(f, 2)
    mod.ACCOUNTS.refresh()
    good = dict(mod.ACCOUNTS.by_id)
    assert good and not _anon(mod)
    for bad in ("{坏的", "", "{}"):
        f.write_text(bad)
        mod.ACCOUNTS.refresh()
        assert mod.ACCOUNTS.by_id == good and mod.ACCOUNTS.known and not _anon(mod), bad
    f.unlink()
    mod.ACCOUNTS.refresh()
    assert mod.ACCOUNTS.by_id == good and not _anon(mod)  # 删了也不退回「没有账号」
    f.mkdir()
    mod.ACCOUNTS.refresh()
    assert mod.ACCOUNTS.by_id == good and not _anon(mod)


def test_failed_refresh_never_resets_known_to_permissive_nor_half_updates(accts):
    mod, f = accts
    _good_users(f)
    mod.ACCOUNTS.refresh()
    before = (dict(mod.ACCOUNTS.by_id), mod.ACCOUNTS.known, mod.ACCOUNTS._mtime)
    f.write_text(json.dumps({"users": {"ok": {"id": "x", "sess": "s"}, "bad": {"id": "y"}}}))  # 第二个缺 sess：整份不用
    mod.ACCOUNTS.refresh()
    assert (mod.ACCOUNTS.by_id, mod.ACCOUNTS.known, mod.ACCOUNTS._mtime) == before
    # 读成功过且账号为空，之后读坏：回到「不知道」，匿名关（不沿用「没有账号」）
    f.write_text('{"users": {}}')
    mod.ACCOUNTS.refresh()
    assert mod.ACCOUNTS.known and not mod.ACCOUNTS.by_id and _anon(mod)
    f.write_text("{坏的")
    mod.ACCOUNTS.refresh()
    assert not mod.ACCOUNTS.known and not _anon(mod)


def test_garbage_anonymous_switch_refuses_to_start_but_unset_is_on():
    base = {k: v for k, v in os.environ.items() if not k.startswith("AUTH_")}
    for raw in ("maybe", "2", "enabled", "tru"):
        r = subprocess.run([sys.executable, str(STUB)], env={**base, "AUTH_PASSWORD": PW, "AUTH_ANONYMOUS_REPORT": raw},
                           capture_output=True, text=True, timeout=10)
        assert r.returncode != 0 and "AUTH_ANONYMOUS_REPORT" in r.stderr, raw  # 不静默当开，也不起来
    s = Stub({"AUTH_PASSWORD": PW})  # 没设：缺省开
    try:
        assert json.loads(s.req("GET", "/api/auth/health")[2])["anonymousReport"] is True
    finally:
        s.stop()


def test_token_state_view_never_goes_permissive(mod):
    """令牌状态：坏文件沿用旧视图（吊销不会被忘掉、也不会变成「全收」）；从没读成功过 / 被删 = 全部 401。"""
    tok, meta = mod.issue_device_token("", mod._shared_sess(), "report")
    mod.EPOCHS.refresh()
    assert mod.device_identity(tok) is not None
    f = Path(mod.TOKENS_FILE)
    good = f.read_text()
    for bad in ("{坏的", "", json.dumps({"gen": "g"})):
        f.write_text(bad)
        mod.EPOCHS.refresh()
        assert mod.device_identity(tok) is not None, bad  # 沿用旧视图
    f.write_text(good)
    assert mod.revoke_token(meta["id"])
    mod.EPOCHS.refresh()
    assert mod.device_identity(tok) is None
    f.write_text("{坏的")
    mod.EPOCHS.refresh()
    assert mod.device_identity(tok) is None  # 吊销没被「忘掉」
    f.unlink()
    mod.EPOCHS.refresh()
    assert mod.EPOCHS.state is None and mod.device_identity(tok) is None
    mod.EPOCHS.__init__()  # 从没读成功过：什么令牌都不认
    f.write_text("{坏的")
    mod.EPOCHS.refresh()
    assert mod.EPOCHS.state is None and mod.device_identity(tok) is None
