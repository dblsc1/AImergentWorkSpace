#!/usr/bin/env python3
"""auth.gate.v1 的占位实现（STUB）—— 一道门 + 一本小账本，不是账号系统。

这是什么
  开源版 HoneyComb 需要一道登录门，但不该捆绑任何真实账号系统。
  本文件用标准库实现 `auth.gate.v1`（v1.3）契约的端点，零第三方依赖、零数据库。
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
  设备令牌（v1.2）：token [<名字>] 打印一个新令牌 / revoke [<名字>] 作废这个身份的全部令牌
  （不给名字 = 共享口令身份）。登录了的网页用户也能自己发：POST /api/auth/tokens。

设备令牌（v1.2）
  桌面同步程序、AI 代理的钩子没有浏览器 cookie，拿 `Authorization: Bearer <令牌>` 调
  /api/core/*（v1.3 起也可调只读的 /api/mcp/）。令牌**只开接口、不开页面**：verify 只在网关
  转来的 X-Original-URI 落在 <站点前缀>api/core/ 或 api/mcp/ 下时才认它。无状态签名，吊销靠每个租户一个整数"纪元"（令牌文件），
  后台线程每 RELOAD_EVERY 秒重读 —— verify 照旧不做 IO。

环境变量
    AUTH_PASSWORD        共享口令。没设它、账号文件里也没账号 = 拒绝启动
    AUTH_USERS_FILE      账号文件（JSON，0600）。不设 = 不开账号登录
    AUTH_SECRET          可选。签 cookie 与设备令牌用；不给则每次启动随机生成
                         （随机 = 重启即所有会话失效，单机自用可以接受；但发不了设备令牌）
    AUTH_COOKIE_SECURE   默认 true。本机 HTTP 调试显式设 false
    AUTH_BIND            默认 127.0.0.1:8010
    AUTH_SESSION_DAYS    默认 30
    AUTH_BASE_PATH       默认 /。整站挂子路径（如 /Cockpit/）时设，cookie 的 Path 跟着它
    AUTH_TOKENS_FILE     设备令牌的纪元文件（JSON，0600）。默认与账号文件同目录的 tokens.json；
                         两个都没设 = 不能发设备令牌
    AUTH_TOKEN_DAYS      设备令牌有效期天数，默认 365

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
from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote

COOKIE_NAME = "cockpit_session"
MAX_BODY = 4096  # 登录体就一个账号一个口令，再大一律拒绝
LOCAL_TENANT = "u_local"  # 共享口令登录的 /me 身份；verify 不回头 = nexus-core 的 u_local
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,31}$")
TENANT_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")  # 与 gateway.v1 / nexus-core 同一格式
MIN_PASSWORD = 8
FAIL_WINDOW = 900  # 同一 IP 15 分钟内
FAIL_LIMIT = 10  # 最多错 10 次，之后 429 到窗口滑过去
RELOAD_EVERY = 2.0  # 账号 / 令牌文件改了多久内生效（删账号 / 改密码 / 吊销令牌）
TOKEN_PREFIX = "hct1"  # 设备令牌的版本前缀；会话 cookie 永远以数字开头，两者一眼可分
MAX_LABEL = 64


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
# 设备令牌要比进程活得久：随机密钥签出来的令牌重启即废，发了等于骗人，所以没设
# AUTH_SECRET 时命令行和 HTTP 都拒绝发令牌（会话 cookie 照旧可以用随机密钥）。
SECRET_FROM_ENV = bool(os.environ.get("AUTH_SECRET", "").strip())
SECRET = os.environ.get("AUTH_SECRET", "").strip() or secrets.token_hex(32)
COOKIE_SECURE = _bool("AUTH_COOKIE_SECURE", True)
SESSION_DAYS = int(os.environ.get("AUTH_SESSION_DAYS", "30"))
SESSION_TTL = SESSION_DAYS * 86400
# 站点前缀（gateway.v1）：整站挂在子路径下时 cookie 的 Path 跟着它走，同域名下别的
# 站点收不到这个会话。网关转过来时已去掉前缀，本服务的路由永远是 /api/auth/...
BASE_PATH = os.environ.get("AUTH_BASE_PATH", "").strip() or "/"
if not re.fullmatch(r"/(?:[A-Za-z0-9._~-]+/)*", BASE_PATH):
    sys.exit(f"❌ AUTH_BASE_PATH 须以 / 开头、以 / 结尾，如 /Cockpit/，收到 {BASE_PATH!r}")
# 缺省放在账号文件旁边：compose 里就是 auth 的数据卷 /data/tokens.json。只开共享口令
# 的部署镜像里照样设了 AUTH_USERS_FILE（文件可以不存在），所以同样有地方放。
TOKENS_FILE = os.environ.get("AUTH_TOKENS_FILE", "").strip() or (
    os.path.join(os.path.dirname(USERS_FILE) or ".", "tokens.json") if USERS_FILE else "")
TOKEN_TTL = int(os.environ.get("AUTH_TOKEN_DAYS", "365")) * 86400


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


@contextmanager
def _locked(path: str):
    """读—改—写整段持 <path>.lock 的排他锁：两条命令（或命令与 HTTP 请求）同时改
    同一个文件时，后写的那个不会拿旧快照覆盖掉先写的（Codex 审核）。"""
    with open(f"{path}.lock", "a+") as lock:
        if fcntl:
            fcntl.flock(lock, fcntl.LOCK_EX)
        else:  # msvcrt 锁第 0 个字节；LK_LOCK 等不到约 10 秒后抛错，不会静默跳过
            if os.path.getsize(lock.name) == 0:  # 锁区不落在空文件外，先垫一个字节
                lock.write("\n")
                lock.flush()
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
        yield  # 关文件即解锁


# ── 令牌纪元文件 ─────────────────────────────────────────────────
# {"gen": "<随机 hex>", "epochs": {"<租户，共享口令为 ''>": n}}。令牌签发时记下当时的纪元，
# verify 时纪元对不上就作废 —— 吊销 = 纪元 +1，一个整数就作废这个身份的全部令牌，不必存
# 令牌表。gen 是这份文件的"代"，签进每个令牌的 HMAC：文件被删、重建，gen 就变，旧令牌
# 全部作废。否则删一下文件（再重启），吊销过的令牌（纪元回到 0）就复活了（Codex 审核）。
# 所以失败方向一律是拒绝：文件不在 = 没有任何有效令牌。

def load_token_state() -> tuple[str, dict] | None:
    """(gen, epochs)；文件不存在返回 None。文件坏了照常抛。"""
    if not TOKENS_FILE or not os.path.exists(TOKENS_FILE):
        return None
    with open(TOKENS_FILE, encoding="utf-8") as f:
        data = json.load(f)
    gen, epochs = data["gen"], data["epochs"]  # 缺哪个都抛：半坏的文件不能把纪元清零
    if not isinstance(gen, str) or not gen or not isinstance(epochs, dict):
        raise ValueError("tokens file")
    return gen, {str(k): int(v) for k, v in epochs.items()}


def token_state(bump: str | None = None) -> tuple[str, dict]:
    """持锁读—改—写：文件不在就新建一代；bump 给了租户就把它的纪元 +1。
    原子替换、0600。发令牌也走这里 —— 保证签进令牌的 gen 已经落盘。"""
    with _locked(TOKENS_FILE):
        state = load_token_state()
        gen, epochs = state if state else (secrets.token_hex(16), {})
        if bump is not None:
            epochs[bump] = epochs.get(bump, 0) + 1
        if state is None or bump is not None:
            tmp = f"{TOKENS_FILE}.{os.getpid()}.{threading.get_ident()}.tmp"
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"gen": gen, "epochs": epochs}, f, ensure_ascii=False, indent=2)
            os.replace(tmp, TOKENS_FILE)
        return gen, epochs


class Epochs:
    """verify 用的令牌状态内存视图，与 Accounts 同一个套路：后台按 mtime 重读，
    请求里不碰文件（契约不变量 2）。吊销最多晚 RELOAD_EVERY 秒生效。

    state 为 None（文件不在、或一次都没读成功）时所有设备令牌 401（解包 None 抛错 →
    收成 None）。读过之后文件坏了，沿用旧视图（坏文件不该把吊销过的令牌放回来，也不该
    把所有人踢掉）；文件被删 = 这一代作废，跟着变 None。
    """

    def __init__(self) -> None:
        self.state: tuple[str, dict] | None = None
        self._mtime = 0  # 不同于任何真实 mtime，也不同于"文件不存在"的 None
        # 后台线程与发令牌 / 吊销的请求线程都会调 refresh：不串行的话，读到旧内容的那个
        # 可能后写，配上新的 mtime，之后就再也不重读 —— 吊销永久失效（Codex 审核）。
        # verify 只读 self.state（一次引用赋值），不拿这把锁。
        self._lock = threading.Lock()

    def refresh(self) -> None:
        with self._lock:
            self._refresh()

    def _refresh(self) -> None:
        try:
            mtime = os.stat(TOKENS_FILE).st_mtime_ns if TOKENS_FILE and os.path.exists(TOKENS_FILE) else None
            if mtime == self._mtime:
                return
            self.state = load_token_state()
            self._mtime = mtime
        except Exception as e:
            sys.stderr.write(f"[auth-stub] 读令牌文件失败，沿用旧视图：{type(e).__name__}\n")


EPOCHS = Epochs()


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
            # 只开共享口令时账号文件本来就不存在：当成空，别每 2 秒报一次错（Windows 验收）
            mtime = os.stat(USERS_FILE).st_mtime_ns if USERS_FILE and os.path.exists(USERS_FILE) else None
            if mtime == self._mtime:
                return
            users = load_users()
            self.by_id = {u["id"]: (name, u["sess"]) for name, u in users.items()}
            self._mtime = mtime
        except Exception as e:  # 文件坏了：保留上一份视图，别把所有人踢下线
            sys.stderr.write(f"[auth-stub] 读账号文件失败，沿用旧视图：{type(e).__name__}\n")


ACCOUNTS = Accounts()


def _watch() -> None:
    while True:
        time.sleep(RELOAD_EVERY)
        ACCOUNTS.refresh()
        EPOCHS.refresh()


# ── 会话令牌 ─────────────────────────────────────────────────────
# <签发时间戳>.<租户>.<HMAC(时间戳.租户 + 会话盐)>。共享口令登录的租户为空。
# 租户里可能有 "."，所以签名从右边切、时间戳从左边切。

def _sign(payload: str, sess: str = "") -> str:
    return hmac.new(SECRET.encode(), (payload + "|" + sess).encode(), hashlib.sha256).hexdigest()


def issue_token(tenant: str = "", sess: str = "", now: int | None = None) -> str:
    """无状态令牌，服务端不存会话表。"""
    payload = f"{int(time.time() if now is None else now)}.{tenant}"
    return f"{payload}.{_sign(payload, sess)}"


def token_tenant(token: str, now: int | None = None, by_id: dict | None = None) -> str | None:
    """有效则返回租户（共享口令登录为 ""），无效返回 None。

    只做签名与过期校验，账号信息取自内存视图。不查库、不做 IO —— 它在每个
    业务请求的关键路径上。任何异常都收敛成 None（=401）。契约第 3 条：内部
    错误不许裸奔成 500，否则 auth 一抖动，整个 /api/core/* 全挂。
    by_id：调用方要用同一份账号快照接着干活时传进来（见 _tokens）。
    """
    try:
        payload, _, sig = token.rpartition(".")
        ts_str, _, tenant = payload.partition(".")
        if tenant:
            known = (ACCOUNTS.by_id if by_id is None else by_id).get(tenant)
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


# ── 设备令牌（v1.2）──────────────────────────────────────────────
# hct1.<签发时间戳>.<纪元>.<租户>.<HMAC("device|" + 前面这段 + "|" + 会话盐)>
# 与会话 cookie 的签名域分开（"device|" 前缀 + 不同的载荷形状），所以令牌塞进 cookie、
# cookie 当令牌用，签名都对不上。租户里可能有 "."：签名从右切，前三段从左切。
# 账号令牌的签名里带会话盐 —— 改密码 / 删账号时它跟着会话一起作废。

def _shared_sess() -> str:
    """共享口令身份的令牌用口令派生的"会话盐"：换共享口令 = 它的令牌全部作废（Codex
    审核）。会话 cookie 那边不这么做（v1 行为不变，靠换 AUTH_SECRET 整体作废）。"""
    return hashlib.sha256(f"shared|{PASSWORD}".encode()).hexdigest()


def _sign_device(payload: str, sess: str, gen: str) -> str:
    msg = f"device|{payload}|{sess}|{gen}"
    return hmac.new(SECRET.encode(), msg.encode(), hashlib.sha256).hexdigest()


def issue_device_token(tenant: str, sess: str, now: int | None = None) -> str:
    """持锁取（必要时新建）令牌状态再签：纪元直接读文件而不是内存视图 —— 命令行刚
    吊销、视图还没刷新时，用旧纪元签出来的令牌两秒后就会莫名失效。"""
    gen, epochs = token_state()
    payload = f"{TOKEN_PREFIX}.{int(time.time() if now is None else now)}.{epochs.get(tenant, 0)}.{tenant}"
    return f"{payload}.{_sign_device(payload, sess, gen)}"


def _api_uri(uri: str | None) -> bool:
    """X-Original-URI（网关的 $request_uri，未解码、带站点前缀）是否落在 <前缀>api/core/
    或（v1.3）<前缀>api/mcp/ 下。

    $request_uri 是客户端发来的原样字节，而 nginx 是拿**解码并规范化之后**的路径去匹配
    location 的：/api/core/%2e%2e/hive/ 在 nginx 眼里就是 /hive/。所以先按 nginx 的方式
    解一遍 %XX，再拒绝任何 . / .. 段和反斜杠 —— 否则令牌能借 /api/core/ 的外壳打开页面。
    """
    if not uri:
        return False
    path = unquote(uri.split("?", 1)[0].split("#", 1)[0])
    # v1.3：只读的 MCP 入口（contracts/mcp.tools.v1）也认令牌；别的一律不开（api/agent/ 只认 cookie）
    prefix = next((BASE_PATH + p for p in ("api/core/", "api/mcp/") if path.startswith(BASE_PATH + p)), None)
    if prefix is None or "\\" in path:
        return False
    return not any(seg in (".", "..") for seg in path[len(prefix):].split("/"))


def device_tenant(token: str, now: int | None = None) -> str | None:
    """设备令牌有效则返回租户（共享口令身份为 ""），无效 None。

    与 token_tenant 同样的纪律：只切字符串、一次 HMAC、几次整数比较，账号与纪元都来自
    内存视图，不做 IO；任何异常收敛成 None（=401）。
    """
    try:
        prefix, _, rest = token.partition(".")
        if prefix != TOKEN_PREFIX:
            return None
        body, _, sig = rest.rpartition(".")
        ts_str, epoch_str, tenant = body.split(".", 2)
        if tenant:
            known = ACCOUNTS.by_id.get(tenant)
            if known is None:  # 账号删了
                return None
            sess = known[1]
        elif PASSWORD:
            sess = _shared_sess()
        else:  # 共享口令已关：它的令牌一并作废
            return None
        gen, epochs = EPOCHS.state  # None（没有令牌文件）→ 抛 → 401
        if not hmac.compare_digest(sig, _sign_device(f"{TOKEN_PREFIX}.{body}", sess, gen)):
            return None
        if int(epoch_str) != epochs.get(tenant, 0):  # 吊销过
            return None
        issued = int(ts_str)
    except Exception:
        return None
    current = int(time.time() if now is None else now)
    return tenant if 0 <= current - issued <= TOKEN_TTL else None


def verify_tenant(headers) -> str | None:
    """verify 的判据：带了 Bearer 就只看令牌（不回落到 cookie），否则看 cookie。"""
    try:
        scheme, _, token = (headers.get("Authorization") or "").strip().partition(" ")
        if scheme.lower() == "bearer":
            if not _api_uri(headers.get("X-Original-URI")):  # 令牌只开接口，不开页面
                return None
            return device_tenant(token.strip())
        return token_tenant(_cookie_value(headers.get("Cookie")))
    except Exception:
        return None


def _expires_at(now: int) -> str:
    return datetime.fromtimestamp(now + TOKEN_TTL, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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
    server_version = "auth-gate-stub/1.3"
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
        bits = [f"{COOKIE_NAME}={token}", f"Path={BASE_PATH}", "HttpOnly", "SameSite=Lax", f"Max-Age={ttl}"]
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
            tenant = verify_tenant(self.headers)
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
        elif self.path in ("/api/auth/tokens", "/api/auth/tokens/revoke"):
            self._tokens(revoke=self.path.endswith("/revoke"))
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

    def _read_json(self, require_type: bool = False) -> dict | None:
        """读有上限的 JSON 对象体；不合格时已经回过状态码，返回 None。"""
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._reject_body(400)
            return None
        if length < 0 or length > MAX_BODY:
            self._reject_body(413)
            return None
        raw = self.rfile.read(length) if length else b""
        ctype = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if require_type and ctype != "application/json":
            self._status_only(415)
            return None
        try:
            body = json.loads(raw or b"{}")
        except Exception:
            body = None
        if not isinstance(body, dict):
            self._status_only(400)
            return None
        return body

    def _tokens(self, revoke: bool) -> None:
        """登录了的网页用户给自己发 / 吊销设备令牌 —— Windows 用户不必碰 docker 命令行。

        只认会话 **cookie**，不认 Bearer：令牌不能自我繁殖（偷到一个令牌就能换出一串
        新的、吊销前还能先把别人的吊销掉）。必须 `Content-Type: application/json`：
        跨站表单发不出这个类型，跨站 fetch 带它要先过 CORS 预检（本服务不回 CORS 头），
        再加上 cookie 本身 SameSite=Lax，别的站点借用户的 cookie 发令牌这条路就堵死了。
        这两个端点可以在请求线程里写文件（不变量 2 只约束 verify）。
        """
        body = self._read_json(require_type=True)
        if body is None:
            return
        # 验 cookie 与取会话盐用**同一份**账号快照（后台线程是整份替换 by_id 的）：
        # 否则改密码恰好发生在两步之间时，旧 cookie 会换到一个用新盐签的、改完密码
        # 依旧有效的令牌（Codex 审核）。
        by_id = ACCOUNTS.by_id
        tenant = token_tenant(_cookie_value(self.headers.get("Cookie")), by_id=by_id)
        if tenant is None:
            self._json(401, {"ok": False, "error": "not_logged_in"})
            return
        label = body.get("label", "")
        if not isinstance(label, str) or len(label) > MAX_LABEL:
            self._json(400, {"ok": False, "error": "bad_label"})
            return
        if not TOKENS_FILE or not SECRET_FROM_ENV:
            # 没地方记纪元 = 发出去收不回；没固定密钥 = 重启即废。两种都不发，响亮地说。
            self._json(503, {"ok": False, "error": "tokens_disabled"})
            return
        try:
            if revoke:
                token_state(bump=tenant)
                EPOCHS.refresh()  # 本进程立即生效，不等后台线程
                self._status_only(204)
                return
            sess = by_id[tenant][1] if tenant else _shared_sess()
            now = int(time.time())
            token = issue_device_token(tenant, sess, now)
            EPOCHS.refresh()  # 第一次发令牌时文件刚建出来：本进程立即认
        except Exception as e:
            sys.stderr.write(f"[auth-stub] 令牌操作出错：{type(e).__name__}\n")
            self._json(503, {"ok": False, "error": "tokens_unavailable"})
            return
        self._json(201, {"token": token, "tenant": tenant or LOCAL_TENANT, "expiresAt": _expires_at(now)})

    def _login(self) -> None:
        body = self._read_json()
        if body is None:
            return
        try:
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
    if argv[0] in ("token", "revoke"):
        _token_cli(argv[0], argv[1].lower() if len(argv) > 1 else "")
        return
    if not USERS_FILE:
        sys.exit("❌ 先设 AUTH_USERS_FILE（账号文件路径）")
    # 密码先读（别让人打字时占着锁），读—改—写整段再持锁：两条命令同时跑
    # （deluser 与 passwd），后写的那个会拿旧快照覆盖掉先写的——删掉的账号复活、
    # 该作废的会话不作废（Codex 审核）。
    pw = _read_password() if argv[0] in ("adduser", "passwd") and len(argv) > 1 else ""
    with _locked(USERS_FILE):
        _cli(argv, pw)


def _token_cli(cmd: str, name: str) -> None:
    """token [<名字>] 发一个设备令牌；revoke [<名字>] 作废这个身份的全部设备令牌。
    不给名字 = 共享口令身份。令牌只打到标准输出，别处（日志、提示）一概不出现。"""
    if not TOKENS_FILE:
        sys.exit("❌ 先设 AUTH_TOKENS_FILE（或 AUTH_USERS_FILE，令牌文件默认放在它旁边）")
    if cmd == "token" and not SECRET_FROM_ENV:
        sys.exit("❌ 没设 AUTH_SECRET —— 不发令牌。\n"
                 "   没有固定密钥时每个进程随机生成一个，这里签出来的令牌服务端根本不认。\n"
                 "   在 .env 里设 AUTH_SECRET=<一串随机字符>，重启 auth 后再来。")
    if name:
        user = load_users().get(name) if USERS_FILE else None
        if user is None:
            sys.exit(f"❌ 没有 {name}")
        tenant, sess = user["id"], user["sess"]
    else:
        if cmd == "token" and not PASSWORD:
            sys.exit("❌ 没开共享口令（AUTH_PASSWORD），这个身份的令牌用不了；要给账号发请带名字")
        tenant, sess = "", _shared_sess()
    who = name or "共享口令身份"
    if cmd == "revoke":
        token_state(bump=tenant)
        print(f"✅ 已作废 {who} 的全部设备令牌；{RELOAD_EVERY:g} 秒内生效")
        return
    now = int(time.time())
    print(issue_device_token(tenant, sess, now))
    sys.stderr.write(f"✅ 上面是 {who} 的设备令牌，{_expires_at(now)} 过期，{RELOAD_EVERY:g} 秒内可用。"
                     "只能调 /api/core/* 与 /api/mcp/，请求头 Authorization: Bearer <令牌>\n")


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
        sys.exit(f"❌ 不认识的命令 {cmd}（adduser / passwd / deluser / users / token / revoke）")


def main() -> None:
    if len(sys.argv) > 1:
        cli(sys.argv[1:])
        return
    ACCOUNTS.refresh()
    EPOCHS.refresh()
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
    # 配了账号文件但一个账号都没有 = 没开账号登录；别写「账号 0 个 +」再警告两种同时开（Windows 验收）
    modes = ([f"账号 {len(ACCOUNTS.by_id)} 个"] if ACCOUNTS.by_id else []) + (["共享口令"] if PASSWORD else [])
    banner = (
        "\n  auth.gate.v1.3 · 占位实现（STUB）：一道门 + 一本小账本，不是账号系统。\n"
        f"  登录方式：{' + '.join(modes)}\n"
        f"  监听 {host}:{port}   cookie Secure={COOKIE_SECURE}   会话 {SESSION_DAYS} 天\n"
    )
    if ACCOUNTS.by_id and PASSWORD:
        banner += "  ⚠ 两种登录同时开着：知道共享口令的人都进 u_local。多用户部署请去掉 AUTH_PASSWORD\n"
    if not COOKIE_SECURE:
        banner += "  ⚠ AUTH_COOKIE_SECURE=false —— 只应出现在本机 HTTP 调试，别上公网\n"
    if os.environ.get("AUTH_SECRET", "").strip() == "":
        banner += "  ℹ 未设 AUTH_SECRET，本次随机生成：重启后所有会话失效\n"
    sys.stderr.write(banner + "\n")
    threading.Thread(target=_watch, daemon=True).start()
    ThreadingHTTPServer((host, int(port)), Handler).serve_forever()


if __name__ == "__main__":
    main()
