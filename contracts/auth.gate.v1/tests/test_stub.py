"""auth.gate.v1.1 占位实现：真起进程、真发 HTTP，按契约逐条核对。"""

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
