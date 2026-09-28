"""auth.gate.v1.2 占位实现：真起进程、真发 HTTP，按契约逐条核对。"""

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
        assert health == {"status": "ok", "accounts": False, "sharedPassword": True}
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


def test_bearer_follows_base_path(tmp_path):
    env = {"AUTH_PASSWORD": PW, "AUTH_BASE_PATH": "/Cockpit/", "AUTH_TOKENS_FILE": str(tmp_path / "t.json")}
    tok = _token_cli(env)
    s = Stub(env)
    try:
        assert s.req("GET", "/api/auth/verify", headers=_bearer(tok, "/Cockpit/api/core/views/tree"))[0] == 204
        assert s.req("GET", "/api/auth/verify", headers=_bearer(tok, "/api/core/views/tree"))[0] == 401
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
    assert status == 201 and set(body) == {"token", "tenant", "expiresAt"} and body["tenant"].startswith("u_")
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
