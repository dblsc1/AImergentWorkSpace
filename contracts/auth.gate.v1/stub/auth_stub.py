#!/usr/bin/env python3
"""auth.gate.v1 的占位实现（STUB）—— 一道门 + 一本小账本，不是账号系统。

这是什么
  开源版 HoneyComb 需要一道登录门，但不该捆绑任何真实账号系统。
  本文件用标准库实现 `auth.gate.v1`（v1.1）契约的端点，零第三方依赖、零数据库。
  契约原文：<总入口>/contracts/auth.gate.v1/contract.md

两种登录，可以同时开
  共享口令   设 AUTH_PASSWORD。所有人一个口令、一份数据（租户 u_local）——v1 的老样子。
  账号+密码  设 AUTH_USERS_FILE，用命令行加账号。每个账号一份自己的数据（verify
             回 X-Nexus-Tenant）。多用户部署请只开这一种，并打开 nexus-core 的
             NEXUS_TENANT_STRICT=1。

它**不**做
  自助注册、找回密码、权限分级、第三方登录、手机号验证、监护人同意。
  需要这些的，把本文件换成实现同一契约的真服务即可 —— nginx 那边一行都不用改。

为什么是标准库
  这个 stub 的全部意义是"拉下来就能跑"。它一旦需要 pip install，
  就失去了当占位件的资格。

跑法
    AUTH_PASSWORD='一个真正的口令' python3 auth_stub.py            # 共享口令
    AUTH_USERS_FILE=./users.json python3 auth_stub.py adduser alice   # 加账号
    AUTH_USERS_FILE=./users.json python3 auth_stub.py                 # 账号模式
    # 本机 HTTP 调试还要加：AUTH_COOKIE_SECURE=false
  账号命令：adduser <名字> [--id <租户id>] / passwd <名字> / deluser <名字> / users
  密码从终端读两遍；非终端（脚本）从标准输入读一行。

环境变量
    AUTH_PASSWORD        共享口令。没设它、账号文件里也没账号 = 拒绝启动
    AUTH_USERS_FILE      账号文件（JSON，0600）。不设 = 不开账号登录
    AUTH_SECRET          可选。签 cookie 用；不给则每次启动随机生成
                         （随机 = 重启即所有会话失效，单机自用可以接受）
    AUTH_COOKIE_SECURE   默认 true。本机 HTTP 调试显式设 false
    AUTH_BIND            默认 127.0.0.1:8010
    AUTH_SESSION_DAYS    默认 30

为什么不给默认口令
  给了就一定会有人原样部署上公网。关键配置不许弱默认值 ——
  两种登录都没配时本进程直接拒绝启动，而不是退回 "admin" 之类。
  这条是故意的，别"为了方便"加回默认值。
"""

from __future__ import annotations

import getpass
import hashlib
import hmac
import json
import os
import re
import secrets
import sys
import threading

try:
    import fcntl  # 容器里（Linux / macOS）
    msvcrt = None
except ImportError:  # pragma: no cover —— Windows 裸跑
    fcntl = None
    import msvcrt
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

COOKIE_NAME = "cockpit_session"
MAX_BODY = 4096  # 登录体就一个账号一个口令，再大一律拒绝
LOCAL_TENANT = "u_local"  # 共享口令登录的 /me 身份；verify 不回头 = nexus-core 的 u_local
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,31}$")
TENANT_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")  # 与 gateway.v1 / nexus-core 同一格式
MIN_PASSWORD = 8
FAIL_WINDOW = 900  # 同一 IP 15 分钟内
FAIL_LIMIT = 10  # 最多错 10 次，之后 429 到窗口滑过去
RELOAD_EVERY = 2.0  # 账号文件改了多久内生效（删账号 / 改密码踢掉旧会话）


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    sys.exit(f"❌ {name} 只能是 true/false，收到 {raw!r}")


PASSWORD = os.environ.get("AUTH_PASSWORD", "").strip()
USERS_FILE = os.environ.get("AUTH_USERS_FILE", "").strip()
SECRET = os.environ.get("AUTH_SECRET", "").strip() or secrets.token_hex(32)
COOKIE_SECURE = _bool("AUTH_COOKIE_SECURE", True)
SESSION_DAYS = int(os.environ.get("AUTH_SESSION_DAYS", "30"))
SESSION_TTL = SESSION_DAYS * 86400


# ── 账号文件 ─────────────────────────────────────────────────────
# {"users": {"<名字>": {"id": 租户, "salt": hex, "hash": hex, "sess": hex}}}
# sess 是这个账号的会话盐：改密码、删账号都换掉它，旧 cookie 的签名随之作废。

def _hash(password: str, salt: bytes) -> str:
    return hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1).hex()


def load_users(path: str = "") -> dict:
    path = path or USERS_FILE
    if not path or not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f).get("users", {})


def save_users(users: dict, path: str = "") -> None:
    """原子写（先写临时文件再 rename），权限 0600：里面是口令哈希。"""
    path = path or USERS_FILE
    tmp = f"{path}.{os.getpid()}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({"users": users}, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


class Accounts:
    """verify 用的内存视图：租户 → (名字, 会话盐)。

    契约不变量 2：verify 不做 IO。所以不在请求里读文件，而是后台线程每
    RELOAD_EVERY 秒看一眼文件的 mtime，变了才重读。代价：删账号 / 改密码
    最多晚 RELOAD_EVERY 秒生效。
    """

    def __init__(self) -> None:
        self.by_id: dict[str, tuple[str, str]] = {}
        self._mtime = None

    def refresh(self) -> None:
        try:
            mtime = os.stat(USERS_FILE).st_mtime_ns if USERS_FILE else None
            if mtime == self._mtime:
                return
            users = load_users()
            self.by_id = {u["id"]: (name, u["sess"]) for name, u in users.items()}
            self._mtime = mtime
        except Exception as e:  # 文件坏了：保留上一份视图，别把所有人踢下线
            sys.stderr.write(f"[auth-stub] 读账号文件失败，沿用旧视图：{type(e).__name__}\n")

    def watch(self) -> None:
        while True:
            time.sleep(RELOAD_EVERY)
            self.refresh()


ACCOUNTS = Accounts()


# ── 会话令牌 ─────────────────────────────────────────────────────
# <签发时间戳>.<租户>.<HMAC(时间戳.租户 + 会话盐)>。共享口令登录的租户为空。
# 租户里可能有 "."，所以签名从右边切、时间戳从左边切。

def _sign(payload: str, sess: str = "") -> str:
    return hmac.new(SECRET.encode(), (payload + "|" + sess).encode(), hashlib.sha256).hexdigest()


def issue_token(tenant: str = "", sess: str = "", now: int | None = None) -> str:
    """无状态令牌，服务端不存会话表。"""
    payload = f"{int(time.time() if now is None else now)}.{tenant}"
    return f"{payload}.{_sign(payload, sess)}"


def token_tenant(token: str, now: int | None = None) -> str | None:
    """有效则返回租户（共享口令登录为 ""），无效返回 None。

    只做签名与过期校验，账号信息取自内存视图。不查库、不做 IO —— 它在每个
    业务请求的关键路径上。任何异常都收敛成 None（=401）。契约第 3 条：内部
    错误不许裸奔成 500，否则 auth 一抖动，整个 /api/core/* 全挂。
    """
    try:
        payload, _, sig = token.rpartition(".")
        ts_str, _, tenant = payload.partition(".")
        if tenant:
            known = ACCOUNTS.by_id.get(tenant)
            if known is None:  # 账号删了
                return None
            sess = known[1]
        elif PASSWORD:
            sess = ""
        else:  # 共享口令已关：它签过的旧 cookie 一并作废
            return None
        if not hmac.compare_digest(sig, _sign(payload, sess)):  # 定时安全比较，别用 ==
            return None
        issued = int(ts_str)
    except Exception:
        return None
    current = int(time.time() if now is None else now)
    return tenant if 0 <= current - issued <= SESSION_TTL else None


def check_login(username: str, password: str) -> tuple[str, str] | None:
    """口令对就返回 (租户, 会话盐)，不对返回 None。"""
    if not username:
        if PASSWORD and hmac.compare_digest(password, PASSWORD):
            return "", ""
        return None
    user = load_users().get(username.lower()) if USERS_FILE else None
    if user is None:
        # 照样算一遍哈希：否则"账号不存在"比"口令错"快一截，能拿来枚举账号
        _hash(password, b"0" * 16)
        return None
    if not hmac.compare_digest(_hash(password, bytes.fromhex(user["salt"])), user["hash"]):
        return None
    return user["id"], user["sess"]


# ── 登录限次 ─────────────────────────────────────────────────────

class FailLimiter:
    """同一 IP 在 FAIL_WINDOW 秒内错 FAIL_LIMIT 次，之后一律 429。只数失败。"""

    def __init__(self) -> None:
        self._fails: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def _recent(self, ip: str, now: float) -> list[float]:
        kept = [t for t in self._fails.get(ip, []) if now - t < FAIL_WINDOW]
        if kept:
            self._fails[ip] = kept
        else:
            self._fails.pop(ip, None)
        return kept

    def blocked(self, ip: str) -> bool:
        with self._lock:
            return len(self._recent(ip, time.time())) >= FAIL_LIMIT

    def fail(self, ip: str) -> None:
        with self._lock:
            now = time.time()
            self._recent(ip, now)
            self._fails.setdefault(ip, []).append(now)
            if len(self._fails) > 10000:  # 防被海量源 IP 撑爆内存：整体清一遍
                for k in list(self._fails):
                    self._recent(k, now)


LIMITER = FailLimiter()


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
    server_version = "auth-gate-stub/1.1"
    protocol_version = "HTTP/1.1"

    # ── 响应助手 ────────────────────────────────────────────────
    def _status_only(self, code: int, headers: dict | None = None) -> None:
        """/verify 与 /login /logout 用。**不返回 body** ——
        nginx 的 auth_request 忽略 body，返 body 纯属浪费。"""
        self.send_response(code)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _set_cookie(self, token: str, ttl: int) -> dict:
        bits = [f"{COOKIE_NAME}={token}", "Path=/", "HttpOnly", "SameSite=Lax", f"Max-Age={ttl}"]
        if COOKIE_SECURE:
            bits.append("Secure")
        return {"Set-Cookie": "; ".join(bits)}

    def _tenant(self) -> str | None:
        return token_tenant(_cookie_value(self.headers.get("Cookie")))

    def _client_ip(self) -> str:
        # 网关的 X-Forwarded-For 是 $proxy_add_x_forwarded_for：客户端自带的值在前，
        # 网关亲眼看到的对端地址追加在**最后**。只信最后一个——前面的谁都能伪造。
        xff = self.headers.get("X-Forwarded-For", "")
        return xff.rsplit(",", 1)[-1].strip() or self.client_address[0]

    # ── 路由 ────────────────────────────────────────────────────
    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/api/auth/health":
            # accounts / sharedPassword：登录页据此决定要不要显示「账号」一栏
            self._json(200, {"status": "ok", "accounts": bool(ACCOUNTS.by_id),
                             "sharedPassword": bool(PASSWORD)})
        elif self.path == "/api/auth/verify":
            tenant = self._tenant()
            if tenant is None:
                self._status_only(401)
            else:
                self._status_only(204, {"X-Nexus-Tenant": tenant} if tenant else None)
        elif self.path == "/api/auth/me":
            tenant = self._tenant()
            if tenant is None:
                self._json(401, {"ok": False, "error": "not_logged_in"})
            elif not tenant:
                self._json(200, {"ok": True, "user": {"id": LOCAL_TENANT, "name": "local"}})
            elif known := ACCOUNTS.by_id.get(tenant):
                self._json(200, {"ok": True, "user": {"id": tenant, "name": known[0]}})
            else:  # 账号恰好在这一瞬间被删：按没登录处理
                self._json(401, {"ok": False, "error": "not_logged_in"})
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
            body = json.loads(raw or b"{}")
            username = str(body.get("username") or "").strip()
            supplied = str(body.get("password", ""))
        except Exception:
            self._status_only(400)
            return
        ip = self._client_ip()
        if LIMITER.blocked(ip):
            self._status_only(429, {"Retry-After": str(FAIL_WINDOW)})
            return
        try:
            ok = check_login(username, supplied)
        except Exception as e:  # 账号文件坏了：响亮地 503，别伪装成"口令不对"
            sys.stderr.write(f"[auth-stub] 登录校验出错：{type(e).__name__}\n")
            self._status_only(503)
            return
        if ok is None:
            LIMITER.fail(ip)
            self._status_only(401)
            return
        self._status_only(204, self._set_cookie(issue_token(*ok), SESSION_TTL))

    def log_message(self, fmt: str, *args) -> None:
        # 只记方法与状态码，**不记 cookie、不记 body** ——
        # 账号服务的日志是最容易把凭证漏出去的地方。
        #
        # 截断到 200 字符：BaseHTTPRequestHandler 的 send_error/log_error 会把
        # **攻击者可控的原始字节**（畸形请求行、超长 URL）塞进这里。不截断 =
        # 任何人都能往你的日志里写任意内容、任意长度。
        line = (fmt % args)[:200].replace("\n", " ").replace("\r", " ")
        sys.stderr.write("[auth-stub] %s\n" % line)


# ── 账号命令 ─────────────────────────────────────────────────────

def _read_password() -> str:
    if sys.stdin.isatty():
        pw = getpass.getpass("密码: ")
        if pw != getpass.getpass("再输一遍: "):
            sys.exit("❌ 两次不一样")
    else:
        pw = sys.stdin.readline().rstrip("\n")
    if len(pw) < MIN_PASSWORD:
        sys.exit(f"❌ 密码至少 {MIN_PASSWORD} 位")
    return pw


def _set_password(user: dict, pw: str) -> None:
    salt = secrets.token_bytes(16)
    user.update(salt=salt.hex(), hash=_hash(pw, salt), sess=secrets.token_hex(16))


def cli(argv: list[str]) -> None:
    if not USERS_FILE:
        sys.exit("❌ 先设 AUTH_USERS_FILE（账号文件路径）")
    # 密码先读（别让人打字时占着锁），读—改—写整段再持锁：两条命令同时跑
    # （deluser 与 passwd），后写的那个会拿旧快照覆盖掉先写的——删掉的账号复活、
    # 该作废的会话不作废（Codex 审核）。
    pw = _read_password() if argv[0] in ("adduser", "passwd") and len(argv) > 1 else ""
    with open(f"{USERS_FILE}.lock", "a+") as lock:
        if fcntl:
            fcntl.flock(lock, fcntl.LOCK_EX)
        else:  # msvcrt 锁第 0 个字节；LK_LOCK 等不到约 10 秒后抛错，不会静默跳过
            if os.path.getsize(lock.name) == 0:  # 锁区不落在空文件外，先垫一个字节
                lock.write("\n")
                lock.flush()
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
        _cli(argv, pw)


def _cli(argv: list[str], pw: str) -> None:
    users = load_users()
    cmd, args = argv[0], argv[1:]
    if cmd == "users":
        for name, u in sorted(users.items()):
            print(f"{name}\t{u['id']}")
        return
    if not args:
        sys.exit(f"用法：{cmd} <名字>")
    name = args[0].lower()
    if cmd == "adduser":
        if not NAME_RE.match(name):
            sys.exit("❌ 名字只能是小写字母、数字、_ . -，1–32 位，字母或数字开头")
        if name in users:
            sys.exit(f"❌ {name} 已存在；改密码用 passwd")
        tenant = args[2] if args[1:2] == ["--id"] and len(args) > 2 else "u_" + secrets.token_hex(8)
        if not TENANT_RE.match(tenant) or any(u["id"] == tenant for u in users.values()):
            sys.exit(f"❌ 租户 id {tenant!r} 不合格式或已被占用")
        users[name] = {"id": tenant}
        _set_password(users[name], pw)
        save_users(users)
        print(f"✅ 已加 {name}（租户 {tenant}）")
    elif cmd == "passwd":
        if name not in users:
            sys.exit(f"❌ 没有 {name}")
        _set_password(users[name], pw)
        save_users(users)
        print(f"✅ 已改 {name} 的密码；它已登录的会话 {RELOAD_EVERY:g} 秒内失效")
    elif cmd == "deluser":
        if users.pop(name, None) is None:
            sys.exit(f"❌ 没有 {name}")
        save_users(users)
        print(f"✅ 已删 {name}；数据留在 nexus-core 里没动，会话 {RELOAD_EVERY:g} 秒内失效")
    else:
        sys.exit(f"❌ 不认识的命令 {cmd}（adduser / passwd / deluser / users）")


def main() -> None:
    if len(sys.argv) > 1:
        cli(sys.argv[1:])
        return
    ACCOUNTS.refresh()
    if not PASSWORD and not ACCOUNTS.by_id:
        sys.exit(
            "❌ 没设 AUTH_PASSWORD，账号文件里也没有账号 —— 拒绝启动。\n"
            "   这不是 bug：一道没有口令的门等于没有门。\n"
            f"   共享口令：AUTH_PASSWORD='<你的口令>' python3 {os.path.basename(__file__)}\n"
            f"   账号模式：AUTH_USERS_FILE=<路径> python3 {os.path.basename(__file__)} adduser <名字>\n"
            "   （compose 里：docker compose run --rm auth python /app/auth_stub.py adduser <名字>）"
        )
    host, _, port = os.environ.get("AUTH_BIND", "127.0.0.1:8010").rpartition(":")
    host = host or "127.0.0.1"
    modes = ([f"账号 {len(ACCOUNTS.by_id)} 个"] if USERS_FILE else []) + (["共享口令"] if PASSWORD else [])
    banner = (
        "\n  auth.gate.v1.1 · 占位实现（STUB）：一道门 + 一本小账本，不是账号系统。\n"
        f"  登录方式：{' + '.join(modes)}\n"
        f"  监听 {host}:{port}   cookie Secure={COOKIE_SECURE}   会话 {SESSION_DAYS} 天\n"
    )
    if USERS_FILE and PASSWORD:
        banner += "  ⚠ 两种登录同时开着：知道共享口令的人都进 u_local。多用户部署请去掉 AUTH_PASSWORD\n"
    if not COOKIE_SECURE:
        banner += "  ⚠ AUTH_COOKIE_SECURE=false —— 只应出现在本机 HTTP 调试，别上公网\n"
    if os.environ.get("AUTH_SECRET", "").strip() == "":
        banner += "  ℹ 未设 AUTH_SECRET，本次随机生成：重启后所有会话失效\n"
    sys.stderr.write(banner + "\n")
    threading.Thread(target=ACCOUNTS.watch, daemon=True).start()
    ThreadingHTTPServer((host, int(port)), Handler).serve_forever()


if __name__ == "__main__":
    main()
