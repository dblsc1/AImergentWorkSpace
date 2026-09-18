#!/usr/bin/env python3
"""auth.gate.v1 的占位实现（STUB）—— 一道门，不是账号系统。

这是什么
  开源版 HoneyComb 需要一道登录门，但不该捆绑任何真实账号系统。
  本文件用标准库实现 `auth.gate.v1` 契约的四个端点，零第三方依赖、零数据库。
  契约原文：<总入口>/contracts/auth.gate.v1/contract.md

它**不**做
  注册、多用户、找回密码、权限分级、第三方登录、手机号验证、监护人同意。
  需要这些的，把本文件换成实现同一契约的真服务即可 —— nginx 那边一行都不用改。

为什么是标准库
  这个 stub 的全部意义是"拉下来就能跑"。它一旦需要 pip install，
  就失去了当占位件的资格。

跑法
    AUTH_PASSWORD='一个真正的口令' python3 auth_stub.py
    # 本机 HTTP 调试还要加：AUTH_COOKIE_SECURE=false

环境变量
    AUTH_PASSWORD        必填。没有默认值 —— 见下面「为什么不给默认口令」
    AUTH_SECRET          可选。签 cookie 用；不给则每次启动随机生成
                         （随机 = 重启即所有会话失效，单机自用可以接受）
    AUTH_COOKIE_SECURE   默认 true。本机 HTTP 调试显式设 false
    AUTH_BIND            默认 127.0.0.1:8010
    AUTH_SESSION_DAYS    默认 30

为什么不给默认口令
  给了就一定会有人原样部署上公网。关键配置不许弱默认值 ——
  缺 AUTH_PASSWORD 时本进程直接拒绝启动，而不是退回 "admin" 之类。
  这条是故意的，别"为了方便"加回默认值。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

COOKIE_NAME = "cockpit_session"
MAX_BODY = 4096  # 登录体就一个口令，再大一律拒绝


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        sys.exit(
            f"❌ 缺少环境变量 {name} —— 拒绝启动。\n"
            f"   这不是 bug：一道没有口令的门等于没有门。\n"
            f"   跑法：{name}='<你的口令>' python3 {os.path.basename(__file__)}"
        )
    return value


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    sys.exit(f"❌ {name} 只能是 true/false，收到 {raw!r}")


PASSWORD = _require("AUTH_PASSWORD")
SECRET = os.environ.get("AUTH_SECRET", "").strip() or secrets.token_hex(32)
COOKIE_SECURE = _bool("AUTH_COOKIE_SECURE", True)
SESSION_DAYS = int(os.environ.get("AUTH_SESSION_DAYS", "30"))
SESSION_TTL = SESSION_DAYS * 86400


def _sign(payload: str) -> str:
    return hmac.new(SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()


def issue_token(now: int | None = None) -> str:
    """无状态令牌：<签发时间戳>.<HMAC-SHA256(时间戳)>。服务端不存会话表。"""
    ts = str(int(time.time() if now is None else now))
    return f"{ts}.{_sign(ts)}"


def token_valid(token: str, now: int | None = None) -> bool:
    """只做签名与过期校验。不查库、不做 IO —— 它在每个业务请求的关键路径上。

    任何异常都收敛成 False（=401）。契约第 3 条：内部错误不许裸奔成 500，
    否则 auth 一抖动，整个 /api/core/* 全挂。
    """
    try:
        ts_str, sig = token.split(".", 1)
        if not hmac.compare_digest(sig, _sign(ts_str)):  # 定时安全比较，别用 ==
            return False
        issued = int(ts_str)
    except Exception:
        return False
    current = int(time.time() if now is None else now)
    return 0 <= current - issued <= SESSION_TTL


def _cookie_value(header: str | None) -> str:
    """自己解 Cookie 头，不依赖 http.cookies —— 畸形头在那个模块里会抛。"""
    if not header:
        return ""
    for part in header.split(";"):
        name, _, value = part.strip().partition("=")
        if name == COOKIE_NAME:
            return value
    return ""


class Handler(BaseHTTPRequestHandler):
    server_version = "auth-gate-stub/1"
    protocol_version = "HTTP/1.1"

    # ── 响应助手 ────────────────────────────────────────────────
    def _status_only(self, code: int, cookie: str | None = None) -> None:
        """/verify 与 /login /logout 用。**不返回 body** ——
        nginx 的 auth_request 忽略 body，返 body 纯属浪费。"""
        self.send_response(code)
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _set_cookie(self, token: str, ttl: int) -> str:
        bits = [
            f"{COOKIE_NAME}={token}",
            "Path=/",
            "HttpOnly",
            "SameSite=Lax",
            f"Max-Age={ttl}",
        ]
        if COOKIE_SECURE:
            bits.append("Secure")
        return "; ".join(bits)

    # ── 路由 ────────────────────────────────────────────────────
    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/api/auth/health":
            self._json(200, {"status": "ok"})
        elif self.path == "/api/auth/verify":
            token = _cookie_value(self.headers.get("Cookie"))
            self._status_only(204 if token_valid(token) else 401)
        else:
            self._status_only(404)

    def do_POST(self) -> None:  # noqa: N802
        if self.path == "/api/auth/login":
            self._login()
        elif self.path == "/api/auth/logout":
            # 过期 cookie 覆盖掉现有的
            self._status_only(204, self._set_cookie("", 0))
        else:
            self._status_only(404)

    def _reject_body(self, code: int) -> None:
        """拒收请求体时必须断连，不能保持 keep-alive。

        实测（2026-09-16）：早退而不读完 body，剩下的字节会被当成**下一个请求行**
        解析，于是 9KB 的 'aaaa...' 变成一条 400 日志把整个载荷打进日志里 ——
        既是协议错位，也是攻击者可控的日志写入。断连是唯一干净的出路。
        """
        self.close_connection = True
        self._status_only(code)

    def _login(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._reject_body(400)
            return
        if length < 0 or length > MAX_BODY:
            self._reject_body(413)
            return
        raw = self.rfile.read(length) if length else b""
        try:
            supplied = str(json.loads(raw or b"{}").get("password", ""))
        except Exception:
            self._status_only(400)
            return
        # 定时安全比较：用 == 会让口令长度和前缀从响应时间里漏出来
        if not hmac.compare_digest(supplied, PASSWORD):
            self._status_only(401)
            return
        self._status_only(204, self._set_cookie(issue_token(), SESSION_TTL))

    def log_message(self, fmt: str, *args) -> None:
        # 只记方法与状态码，**不记 cookie、不记 body** ——
        # 账号服务的日志是最容易把凭证漏出去的地方。
        #
        # 截断到 200 字符：BaseHTTPRequestHandler 的 send_error/log_error 会把
        # **攻击者可控的原始字节**（畸形请求行、超长 URL）塞进这里。不截断 =
        # 任何人都能往你的日志里写任意内容、任意长度。
        line = (fmt % args)[:200].replace("\n", " ").replace("\r", " ")
        sys.stderr.write("[auth-stub] %s\n" % line)


def main() -> None:
    host, _, port = os.environ.get("AUTH_BIND", "127.0.0.1:8010").rpartition(":")
    host = host or "127.0.0.1"
    banner = (
        "\n"
        "  ┌──────────────────────────────────────────────────────────┐\n"
        "  │  auth.gate.v1 · 占位实现（STUB）                         │\n"
        "  │  单一共享口令的一道门，不是账号系统。                    │\n"
        "  │  多用户 / 注册 / 找回密码 / 权限分级，一概没有。          │\n"
        "  └──────────────────────────────────────────────────────────┘\n"
        f"  监听 {host}:{port}   cookie Secure={COOKIE_SECURE}   会话 {SESSION_DAYS} 天\n"
    )
    if not COOKIE_SECURE:
        banner += "  ⚠ AUTH_COOKIE_SECURE=false —— 只应出现在本机 HTTP 调试，别上公网\n"
    if os.environ.get("AUTH_SECRET", "").strip() == "":
        banner += "  ℹ 未设 AUTH_SECRET，本次随机生成：重启后所有会话失效\n"
    sys.stderr.write(banner + "\n")
    ThreadingHTTPServer((host, int(port)), Handler).serve_forever()


if __name__ == "__main__":
    main()
