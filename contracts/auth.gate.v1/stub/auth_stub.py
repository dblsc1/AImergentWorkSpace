#!/usr/bin/env python3
"""auth.gate.v1 的占位实现（STUB）—— 一道门 + 一本小账本，不是账号系统。

这是什么
  开源版 HoneyComb 需要一道登录门，但不该捆绑任何真实账号系统。
  本文件用标准库实现 `auth.gate.v1`（v1.4）契约的端点，零第三方依赖、零数据库。
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
  设备令牌（v1.2）：token [<名字>] [--scope report|read|write] [--name <备注>] 打印一个新令牌 /
  revoke [<名字>] 作废这个身份的全部令牌 / revoke-token <令牌 id> 只作废这一个 / tokens [<名字>] 列出
  （不给名字 = 共享口令身份）。登录了的网页用户也能自己发：POST /api/auth/tokens。
  **令牌从不自动生成**：只有人（网页会话或这里的命令行）发，发给谁由人决定。

设备令牌（v1.2）
  桌面同步程序、AI 代理的钩子没有浏览器 cookie，拿 `Authorization: Bearer <令牌>` 调
  /api/core/*（v1.3 起也可调只读的 /api/mcp/）。令牌**只开接口、不开页面**：verify 只在网关
  转来的 X-Original-URI 落在 <站点前缀>api/core/ 或 api/mcp/ 下时才认它。无状态签名，吊销靠每个租户一个整数"纪元"（令牌文件），
  后台线程每 RELOAD_EVERY 秒重读 —— verify 照旧不做 IO。

权限范围与匿名上报（v1.4）
  新发的令牌是 hct2 格式，签名里带着范围：report（只能上报代理运行）⊂ read（再加只读）⊂ write
  （设备令牌原来的全部）。hct1 老令牌照常可用 = write。verify 按「调用方 × 方法 × 路径」的小表放行，
  表外一律拒绝；放行时回 X-Nexus-Scope，网关覆盖转给后端，后端再拦一遍。
  什么凭据都不带的请求（没有会话 cookie、没有 Authorization）只能上报，而且只在单人模式
  （只开共享口令、没有账号）下；带了坏令牌是 401，绝不降级成匿名。AUTH_ANONYMOUS_REPORT=false 关掉。

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
    AUTH_ANONYMOUS_REPORT  默认 true：单人模式下不带凭据的请求可以上报代理运行（只写泳道，读不到任何东西）。
                         **能连到端口的人都能写**——端口对外开放时请设 false

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
TOKEN2_PREFIX = "hct2"  # v1.4：签名里带范围、令牌 id、到期时刻。新发的都是它；hct1 只验不发
SCOPES = ("report", "read", "write")  # 严格嵌套：后一个包含前一个
MAX_LABEL = 64
MAX_TOKENS = 100  # 每个身份名下未到期的 hct2 令牌上限（吊销 = 删记录，立即腾出名额）
MAX_TOKENS_TOTAL = 1000  # 全部身份加起来的上限：令牌文件与内存视图有硬顶，不随身份数（含已删账号的残留）无限长
TOKEN_ID_RE = re.compile(r"^[0-9a-f]{16}$")


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
# v1.4 匿名上报：仓主定缺省开（「没有 token 的请求只能上报」）。只在单人模式生效，见 _verify。
ANON_REPORT = _bool("AUTH_ANONYMOUS_REPORT", True)
# 上报面（report 范围与匿名的全部权限）：恰好这四个 POST。对 X-Original-URI 的**原样字节**做全匹配——
# 不解码、不规范化：带 %XX、//、结尾斜杠、大小写不同的写法一律不在表里。这样门认的路径与 nginx
# 规范化后转给后端的路径只可能是同一个，没有「门看是 A、后端收到 B」的缝。
REPORT_RE = re.compile(re.escape(BASE_PATH) + r"api/core/agents/(?:start|[A-Za-z0-9_-]{1,64}/(?:phase|stop|heartbeat))")


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


# ── 令牌文件 ─────────────────────────────────────────────────────
# {"gen": "<随机 hex>", "epochs": {"<租户，共享口令为 ''>": n},
#  "tokens": {"<令牌 id>": {tenant, name, scope, createdAt, expiresAt}}}
# 令牌签发时记下当时的纪元，verify 时纪元对不上就作废 —— 吊销整个身份 = 纪元 +1。
# gen 是这份文件的"代"，签进每个令牌的 HMAC：文件被删、重建，gen 就变，旧令牌
# 全部作废。否则删一下文件（再重启），吊销过的令牌（纪元回到 0）就复活了（Codex 审核）。
# 所以失败方向一律是拒绝：文件不在 = 没有任何有效令牌。
# tokens（v1.4）：每个 hct2 令牌一条，**不含令牌本身**（只有 id 与备注）。它同时是列表的数据与
# verify 的白名单——id 不在表里的 hct2 一律 401，所以单个吊销 = 删这条记录（不留墓碑）。到期的条目
# 在下一次写文件时清掉，每个身份至多 MAX_TOKENS 条、全部合计至多 MAX_TOKENS_TOTAL 条，所以这张表有界。

class TooManyTokens(Exception):
    pass


def _parse_token_state(raw: bytes) -> tuple[int, str, dict, dict]:
    """(rev, gen, epochs, tokens)。``rev`` 是每次写都 +1 的计数（老文件没有 = 0）：视图按它只进不退。坏了照常抛。"""
    data = json.loads(raw.decode("utf-8"))
    gen, epochs = data["gen"], data["epochs"]  # 缺哪个都抛：半坏的文件不能把纪元清零
    tokens = data.get("tokens", {})  # v1.2 / v1.3 写的文件没有这个键
    rev = data.get("rev", 0)
    if (not isinstance(gen, str) or not gen or not isinstance(epochs, dict) or not isinstance(tokens, dict)
            or type(rev) is not int or rev < 0):
        raise ValueError("tokens file")
    tokens = {str(k): {"tenant": str(t["tenant"]), "name": str(t["name"]), "scope": str(t["scope"]),
                       "createdAt": int(t["createdAt"]), "expiresAt": int(t["expiresAt"])}
              for k, t in tokens.items() if not t.get("revoked")}  # 开发期写过墓碑的文件：墓碑当场丢掉
    return rev, gen, {str(k): int(v) for k, v in epochs.items()}, tokens


def _load_token_file() -> tuple[int, str, dict, dict] | None:
    if not TOKENS_FILE:
        return None
    try:
        with open(TOKENS_FILE, "rb") as f:
            raw = f.read()
    except FileNotFoundError:
        return None
    return _parse_token_state(raw)


def load_token_state() -> tuple[str, dict, dict] | None:
    """(gen, epochs, tokens)；文件不存在返回 None。文件坏了照常抛。"""
    full = _load_token_file()
    return full[1:] if full else None


def token_state(change=None):
    """持锁读—改—写：文件不在就新建一代；change(epochs, tokens) 就地改，返回值原样带出。
    每次改都顺手清掉到期的条目。原子替换、0600。发令牌也走这里 —— 保证签进令牌的 gen 已经落盘。
    返回 (gen, change 的返回值)。"""
    with _locked(TOKENS_FILE):
        state = _load_token_file()
        rev, gen, epochs, tokens = state if state else (0, secrets.token_hex(16), {}, {})
        out = None
        if change is not None:
            now = int(time.time())
            for jti in [j for j, t in tokens.items() if t["expiresAt"] <= now]:
                del tokens[jti]
            out = change(epochs, tokens)
        if state is None or change is not None:
            tmp = f"{TOKENS_FILE}.{os.getpid()}.{threading.get_ident()}.tmp"
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"rev": rev + 1, "gen": gen, "epochs": epochs, "tokens": tokens}, f, ensure_ascii=False, indent=2)
            os.replace(tmp, TOKENS_FILE)
        return gen, out


def revoke_identity(tenant: str) -> None:
    """这个身份的全部令牌作废：纪元 +1（hct1 与 hct2 都签着纪元），它名下的令牌条目一并清掉。"""
    def change(epochs: dict, tokens: dict) -> None:
        epochs[tenant] = epochs.get(tenant, 0) + 1
        for jti in [j for j, t in tokens.items() if t["tenant"] == tenant]:
            del tokens[jti]
    token_state(change)


def revoke_token(jti: str, tenant: str | None = None) -> bool:
    """只作废这一个 hct2 令牌：删它的记录（白名单里没有 = 401），名额立即回来。tenant 给了就只认这个身份
    名下的（网页）；None = 命令行，不限。False = 没有这个 id（或不是你的；吊销过的也已经没了）。"""
    def change(_epochs: dict, tokens: dict) -> bool:
        meta = tokens.get(jti)
        if meta is None or (tenant is not None and meta["tenant"] != tenant):
            return False
        del tokens[jti]
        return True
    return token_state(change)[1]


def _iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def list_tokens(tokens: dict, tenant: str | None) -> list[dict]:
    """列表用的形状（**没有令牌本身**，它哪里都没存）。tenant=None = 全部。"""
    return [{"id": jti, "name": t["name"], "scope": t["scope"], "createdAt": _iso(t["createdAt"]),
             "expiresAt": _iso(t["expiresAt"])}
            for jti, t in sorted(tokens.items(), key=lambda kv: kv[1]["createdAt"])
            if tenant is None or t["tenant"] == tenant]


class Epochs:
    """verify 用的令牌状态内存视图，与 Accounts 同一个套路：后台每 RELOAD_EVERY 秒重读，请求里不碰文件
    （契约不变量 2）。吊销最多晚 RELOAD_EVERY 秒生效（文档写明的延迟，不是绕过）。

    **失败方向是拒绝**（和账号视图相反：账号沿用旧视图是更严的一边，令牌的旧视图里还有已被别的进程吊销的令牌）：
    state 为 None 时所有设备令牌 401（解包 None 抛错 → 收成 None）。文件内容变了却读不出 / 解析不了 / 版本倒退、
    或文件没了 → None；文件内容没变才沿用缓存的视图。文件被换成新的一代 → 视图跟着换（旧令牌的签名带着旧一代，对不上）。
    不看 mtime / inode：每轮读整份（≤ MAX_TOKENS_TOTAL 条）比字节，粗粒度时间戳、同大小重写、inode 复用都漏不掉。
    ``rev`` 每次写 +1；同一代里 rev 比见过的最大值小 = 回滚到旧内容（比如备份被拷回来），当作读不出。
    """

    _UNSET = object()

    def __init__(self) -> None:
        self.state: tuple[str, dict, dict] | None = None
        self._raw: object = self._UNSET  # 上次处理过的文件字节（None = 文件不在）
        self._seen: tuple[str, int] | None = None  # 见过的 (gen, 最大 rev)
        # 后台线程与发令牌 / 吊销的请求线程都会调 refresh：读和替换在同一把锁里，所以不会有人拿读到的旧内容
        # 盖掉别人刚换上的新视图。verify 只读 self.state（一次引用赋值），不拿这把锁。
        self._lock = threading.Lock()

    def refresh(self) -> None:
        with self._lock:
            self._refresh()

    def _refresh(self) -> None:
        raw: bytes | None = None
        try:
            if TOKENS_FILE:
                try:
                    with open(TOKENS_FILE, "rb") as f:
                        raw = f.read()
                except FileNotFoundError:
                    raw = None
            if raw is not None and raw == self._raw:
                return  # 没变：沿用（上次读坏的字节没变 = state 仍是 None）
            if raw is None:
                self.state, self._raw = None, None  # 文件没了：这一代作废，不复活
                return
            rev, gen, epochs, tokens = _parse_token_state(raw)
            if self._seen and self._seen[0] == gen and rev < self._seen[1]:
                raise ValueError("tokens file rolled back")
            self._seen = (gen, max(rev, self._seen[1]) if self._seen and self._seen[0] == gen else rev)
            self.state, self._raw = (gen, epochs, tokens), raw
        except Exception as e:
            self.state = None  # 变了却用不了：不再信旧视图（别的进程可能刚吊销过东西）
            self._raw = raw if raw is not None else self._UNSET
            sys.stderr.write(f"[auth-stub] 令牌文件变了但读不出，设备令牌暂时全部拒绝：{type(e).__name__}\n")


EPOCHS = Epochs()


class Accounts:
    """verify 用的内存视图：租户 → (名字, 会话盐)。

    契约不变量 2：verify 不做 IO。所以不在请求里读文件，而是后台线程每
    RELOAD_EVERY 秒看一眼文件的指纹，变了才重读。代价：删账号 / 改密码
    最多晚 RELOAD_EVERY 秒生效。

    ``known``：账号模式「有没有」已经确定——读成功过一次，或确认文件不存在（只认 ENOENT）。
    匿名上报只在 ``known`` 且没有账号时才开（``anonymous_allowed``）。任何不确定都是 False：文件在但读不出 /
    解析不了（含写了一半、空文件、目录）/ 权限或 IO 错误 → 匿名关。读成功过的视图，之后文件坏了 / 没了 / 读不了，
    一律沿用（账号模式不会因此退回「没有账号」）；只有「读成功过且账号为空」之后又读坏，才回到 ``known=False``。
    """

    def __init__(self) -> None:
        self.by_id: dict[str, tuple[str, str]] = {}
        self.known = False
        self._mtime = None

    def refresh(self) -> None:
        try:
            if not USERS_FILE:
                self.known = True  # 没配账号文件 = 确定没有账号
                return
            try:
                st = os.stat(USERS_FILE)
            except FileNotFoundError:
                # 只开共享口令时账号文件本来就不存在：当成空，别每 2 秒报一次错（Windows 验收）。
                # 但读成功过账号之后文件没了：沿用旧视图（删文件不能让门退回匿名）
                if not self.by_id:
                    self.by_id, self.known, self._mtime = {}, True, None
                return
            mtime = (st.st_mtime_ns, st.st_ino, st.st_size)
            if mtime == self._mtime:
                return
            with open(USERS_FILE, encoding="utf-8") as f:
                users = json.load(f)["users"]  # 缺 users 键 / 不是对象都抛：半坏的文件不能当「没有账号」
            if not isinstance(users, dict):
                raise ValueError("users file")
            by_id = {u["id"]: (name, u["sess"]) for name, u in users.items()}
            self.by_id, self.known, self._mtime = by_id, True, mtime  # 先放账号再放 known：中途被读到也只会更严
        except Exception as e:  # 文件坏了：保留上一份视图，别把所有人踢下线
            if not self.by_id:
                self.known = False  # 没有可沿用的账号视图：不知道有没有账号，匿名关
            sys.stderr.write(f"[auth-stub] 读账号文件失败，沿用旧视图：{type(e).__name__}\n")

    def anonymous_allowed(self) -> bool:
        """匿名上报开不开：开关开着、单人模式（共享口令、确定没有账号）。"""
        return bool(ANON_REPORT and PASSWORD and self.known and not self.by_id)


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


def _sign_device(payload: str, sess: str, gen: str, domain: str = "device") -> str:
    msg = f"{domain}|{payload}|{sess}|{gen}"
    return hmac.new(SECRET.encode(), msg.encode(), hashlib.sha256).hexdigest()


def issue_device_token(tenant: str, sess: str, scope: str = "write", name: str = "",
                       now: int | None = None) -> tuple[str, dict]:
    """发一个 hct2 令牌，返回 (令牌, 列表里的那一条)。

    hct2.<签发>.<到期>.<纪元>.<范围>.<令牌 id>.<租户>.<HMAC("device2|" + 前面这段 + "|" + 会话盐 + "|" + 代)>
    范围、id、到期都在签名里：改任何一段签名就对不上。签名域 "device2|" 与 hct1 的 "device|"、
    会话 cookie 的都不同，三者互不通用。持锁取（必要时新建）令牌状态再签：纪元直接读文件而不是
    内存视图 —— 命令行刚吊销、视图还没刷新时，用旧纪元签出来的令牌两秒后就会莫名失效。"""
    issued = int(time.time() if now is None else now)
    jti = secrets.token_hex(8)
    meta = {"tenant": tenant, "name": name, "scope": scope, "createdAt": issued,
            "expiresAt": issued + TOKEN_TTL}

    def change(epochs: dict, tokens: dict) -> int:
        if len(tokens) >= MAX_TOKENS_TOTAL or sum(1 for t in tokens.values() if t["tenant"] == tenant) >= MAX_TOKENS:
            raise TooManyTokens
        tokens[jti] = meta
        return epochs.get(tenant, 0)

    gen, epoch = token_state(change)
    payload = f"{TOKEN2_PREFIX}.{issued}.{meta['expiresAt']}.{epoch}.{scope}.{jti}.{tenant}"
    return f"{payload}.{_sign_device(payload, sess, gen, 'device2')}", list_tokens({jti: meta}, None)[0]


def _api_area(uri: str | None) -> str | None:
    """X-Original-URI（网关的 $request_uri，未解码、带站点前缀）落在 <前缀>api/core/ 下回 "core"，
    （v1.3）<前缀>api/mcp/ 下回 "mcp"，别处 None。

    $request_uri 是客户端发来的原样字节，而 nginx 是拿**解码并规范化之后**的路径去匹配
    location 的：/api/core/%2e%2e/hive/ 在 nginx 眼里就是 /hive/。所以先按 nginx 的方式
    解一遍 %XX，再拒绝任何 . / .. 段和反斜杠 —— 否则令牌能借 /api/core/ 的外壳打开页面。
    """
    if not uri:
        return None
    path = unquote(uri.split("?", 1)[0].split("#", 1)[0])
    # v1.3：MCP 入口（contracts/mcp.tools.v1）也认令牌；别的一律不开（api/agent/ 只认 cookie）
    area = next((a for a in ("core", "mcp") if path.startswith(f"{BASE_PATH}api/{a}/")), None)
    if area is None or "\\" in path:
        return None
    if any(seg in (".", "..") for seg in path[len(f"{BASE_PATH}api/{area}/"):].split("/")):
        return None
    return area


def _report_uri(method: str | None, uri: str | None) -> bool:
    """是不是上报面（见 REPORT_RE）。方法只认网关转来的 X-Original-Method，缺了就不是。"""
    return method == "POST" and bool(uri) and REPORT_RE.fullmatch(uri.split("?", 1)[0]) is not None


def scope_allows(scope: str, method: str | None, uri: str | None, area: str) -> bool:
    """「范围 × 方法 × 路径」的放行表（契约「权限范围」的矩阵）。write = 设备令牌原来的全部，不看方法
    （老网关不转 X-Original-Method 时照常）；其余**缺省拒绝**：
      report  只有上报面
      read    上报面 + api/core/ 的 GET + api/mcp/ 的 POST（MCP 只收 POST；哪些工具能调由 MCP 按范围再拦）
    HEAD / OPTIONS / 别的方法都不在表里。方法覆盖头（X-HTTP-Method-Override 之类）不读。"""
    if scope == "write":
        return True
    if _report_uri(method, uri):
        return True
    return scope == "read" and ((area == "core" and method == "GET") or (area == "mcp" and method == "POST"))


def device_identity(token: str, now: int | None = None) -> tuple[str, str] | None:
    """设备令牌有效则返回 (租户（共享口令身份为 ""）, 范围)，无效 None。hct1 = write。

    与 token_tenant 同样的纪律：只切字符串、一次 HMAC、几次整数比较与查内存里的表，账号、纪元、
    令牌表都来自内存视图，不做 IO；任何异常收敛成 None（=401）。先验签名，再看别的：没有密钥的人
    分不出「签名不对」之外的任何一种拒绝。
    """
    try:
        prefix, _, rest = token.partition(".")
        body, _, sig = rest.rpartition(".")
        if prefix == TOKEN2_PREFIX:
            ts_str, exp_str, epoch_str, scope, jti, tenant = body.split(".", 5)
            domain = "device2"
        elif prefix == TOKEN_PREFIX:
            ts_str, epoch_str, tenant = body.split(".", 2)
            exp_str, scope, jti, domain = "", "write", None, "device"
        else:
            return None
        if tenant:
            known = ACCOUNTS.by_id.get(tenant)
            if known is None:  # 账号删了
                return None
            sess = known[1]
        elif PASSWORD:
            sess = _shared_sess()
        else:  # 共享口令已关：它的令牌一并作废
            return None
        gen, epochs, tokens = EPOCHS.state  # None（没有令牌文件）→ 抛 → 401
        if not hmac.compare_digest(sig, _sign_device(f"{prefix}.{body}", sess, gen, domain)):
            return None
        if scope not in SCOPES or int(epoch_str) != epochs.get(tenant, 0):  # 吊销过（整个身份）
            return None
        issued = int(ts_str)
        current = int(time.time() if now is None else now)
        if jti is None:
            return (tenant, scope) if 0 <= current - issued <= TOKEN_TTL else None
        meta = tokens.get(jti)  # 白名单：不在表里（表被改过）或单个吊销了 → 拒绝
        if meta is None or meta["tenant"] != tenant:
            return None
        return (tenant, scope) if issued <= current < int(exp_str) else None
    except Exception:
        return None


def verify_access(headers) -> tuple[int, dict]:
    """verify 的判据 → (状态码, 响应头)。任何异常 401（不变量 3）。"""
    try:
        return _verify(headers)
    except Exception:
        return 401, {}


def _verify(headers) -> tuple[int, dict]:
    """调用方分四类，按出示的凭据定，**出示了就只看它，绝不降级**：
      Bearer        设备令牌。无效 / 过期 / 吊销 401；有效但范围不够 403
      会话 cookie   人。原样放行（不带范围头）
      别的方案      （如 Basic）不算令牌，照旧只看 cookie
      什么都没带    匿名：单人模式 + 开关开着 + 请求落在上报面 → 放行并标 X-Nexus-Anonymous；否则 401
    """
    auths = headers.get_all("Authorization") or []
    if len(auths) > 1:  # 两个 Authorization：网关与这里可能各看各的那一个，不猜
        return 401, {}
    auth = auths[0].strip(" \t") if auths else ""  # 出示了（哪怕是空的）看 auths，不看解析出来的值
    method, uri = headers.get("X-Original-Method"), headers.get("X-Original-URI")
    scheme, _, token = auth.partition(" ")
    if scheme.lower() == "bearer":
        area = _api_area(uri)  # 令牌只开接口，不开页面
        ident = device_identity(token.strip()) if area else None
        if ident is None:
            return 401, {}
        tenant, scope = ident
        if not scope_allows(scope, method, uri, area):
            return 403, {}
        return 204, {"X-Nexus-Scope": scope, **({"X-Nexus-Tenant": tenant} if tenant else {})}
    cookie = _cookie_value(headers.get("Cookie"))
    if cookie or auths or _has_cookie(headers.get("Cookie")):  # 出示了就不降级：空 cookie / 空 Authorization 也是 401，不是匿名
        tenant = token_tenant(cookie)
        return (401, {}) if tenant is None else (204, {"X-Nexus-Tenant": tenant} if tenant else {})
    # 匿名。单人模式 = 开着共享口令、一个账号都没有：只有这时「不带租户 = u_local」是唯一的那份数据；
    # 有账号就没有可归属的租户，一律 401（宁可拒绝）。
    if ACCOUNTS.anonymous_allowed() and _report_uri(method, uri):
        return 204, {"X-Nexus-Scope": "report", "X-Nexus-Anonymous": "1"}
    return 401, {}


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


def _has_cookie(header: str | None) -> bool:
    """会话 cookie 出现了（值可以是空的）。别的 cookie 不算。"""
    return any(p.strip().partition("=")[0] == COOKIE_NAME for p in (header or "").split(";"))


class Handler(BaseHTTPRequestHandler):
    server_version = "auth-gate-stub/1.4"
    protocol_version = "HTTP/1.1"

    def log_request(self, code="-", size="-") -> None:
        # 不变量 2：verify 是每个请求都要过的热路径，不做同步日志 IO（日志管道堵了会拖死所有认证）
        if getattr(self, "path", "") != "/api/auth/verify":
            super().log_request(code, size)

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
                             "sharedPassword": bool(PASSWORD),
                             # v1.4：此刻不带凭据能不能上报（开关开着且是单人模式）
                             "anonymousReport": ACCOUNTS.anonymous_allowed()})
        elif self.path == "/api/auth/verify":
            self._status_only(*verify_access(self.headers))
        elif self.path == "/api/auth/tokens":
            self._list_tokens()
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
        name = body.get("name", body.get("label", ""))  # label 是 v1.2 的叫法，照收
        if not isinstance(name, str) or len(name) > MAX_LABEL or any(ord(c) < 32 or ord(c) == 127 for c in name):
            self._json(400, {"ok": False, "error": "bad_label"})
            return
        scope, jti = body.get("scope", "write"), body.get("tokenId")
        if scope not in SCOPES:
            self._json(400, {"ok": False, "error": "bad_scope"})
            return
        # 给了 tokenId 这个键就必须是一个合格的 id：null / 空串不能悄悄变成「吊销全部」
        one = "tokenId" in body
        if one and not (isinstance(jti, str) and TOKEN_ID_RE.match(jti)):
            self._json(400, {"ok": False, "error": "bad_token_id"})
            return
        if not TOKENS_FILE or not SECRET_FROM_ENV:
            # 没地方记纪元 = 发出去收不回；没固定密钥 = 重启即废。两种都不发，响亮地说。
            self._json(503, {"ok": False, "error": "tokens_disabled"})
            return
        try:
            if revoke:
                # 带 tokenId = 只作废这一个（v1.4，只认自己名下的）；不带 = 这个身份的全部（v1.2）
                found = revoke_token(jti, tenant) if one else revoke_identity(tenant)
                EPOCHS.refresh()  # 本进程立即生效，不等后台线程
                if found is False:
                    self._json(404, {"ok": False, "error": "no_such_token"})
                else:
                    self._status_only(204)
                return
            sess = by_id[tenant][1] if tenant else _shared_sess()
            token, meta = issue_device_token(tenant, sess, scope, name)
            EPOCHS.refresh()  # 本进程立即认（令牌表是白名单）
        except TooManyTokens:
            self._json(409, {"ok": False, "error": "too_many_tokens"})
            return
        except Exception as e:
            sys.stderr.write(f"[auth-stub] 令牌操作出错：{type(e).__name__}\n")
            self._json(503, {"ok": False, "error": "tokens_unavailable"})
            return
        self._json(201, {"token": token, "tenant": tenant or LOCAL_TENANT, **meta})

    def _list_tokens(self) -> None:
        """GET /api/auth/tokens（v1.4）：自己名下的 hct2 令牌。只认会话 cookie；回的是 id、备注、范围、
        时间 —— **没有令牌本身**（哪里都没存）。hct1 老令牌没有 id，列不出来。"""
        tenant = self._tenant()
        if tenant is None:
            self._json(401, {"ok": False, "error": "not_logged_in"})
        elif not TOKENS_FILE or not SECRET_FROM_ENV:
            self._json(503, {"ok": False, "error": "tokens_disabled"})
        else:
            EPOCHS.refresh()  # 不在 verify 的路径上，可以看一眼文件
            state = EPOCHS.state
            now = int(time.time())
            live = {j: t for j, t in (state[2] if state else {}).items() if t["expiresAt"] > now}
            self._json(200, {"tokens": list_tokens(live, tenant)})

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


TOKEN_CMDS = ("token", "revoke", "revoke-token", "tokens")


def cli(argv: list[str]) -> None:
    if argv[0] in TOKEN_CMDS:
        _token_cli(argv[0], argv[1:])
        return
    if not USERS_FILE:
        sys.exit("❌ 先设 AUTH_USERS_FILE（账号文件路径）")
    # 密码先读（别让人打字时占着锁），读—改—写整段再持锁：两条命令同时跑
    # （deluser 与 passwd），后写的那个会拿旧快照覆盖掉先写的——删掉的账号复活、
    # 该作废的会话不作废（Codex 审核）。
    pw = _read_password() if argv[0] in ("adduser", "passwd") and len(argv) > 1 else ""
    with _locked(USERS_FILE):
        _cli(argv, pw)


def _token_cli(cmd: str, args: list[str]) -> None:
    """token [<名字>] [--scope report|read|write] [--name <备注>] 发一个设备令牌；
    revoke [<名字>] 作废这个身份的全部设备令牌；revoke-token <令牌 id> 只作废这一个；
    tokens [<名字>] 列出（不给名字 = 全部身份）。
    不给名字 = 共享口令身份。令牌只打到标准输出，别处（日志、提示）一概不出现。"""
    opts = {"--scope": "write", "--name": ""}
    rest: list[str] = []
    while args:
        arg = args.pop(0)
        if arg in opts:
            if cmd != "token" or not args:
                sys.exit(f"用法：token [<名字>] [--scope {'|'.join(SCOPES)}] [--name <备注>]")
            opts[arg] = args.pop(0)
        else:
            rest.append(arg)
    scope, label = opts["--scope"], opts["--name"]
    if scope not in SCOPES or len(label) > MAX_LABEL or any(ord(c) < 32 or ord(c) == 127 for c in label):
        sys.exit(f"❌ --scope 只能是 {' / '.join(SCOPES)}；--name 至多 {MAX_LABEL} 个字符、不含控制字符")
    name = rest[0].lower() if rest else ""
    if not TOKENS_FILE:
        sys.exit("❌ 先设 AUTH_TOKENS_FILE（或 AUTH_USERS_FILE，令牌文件默认放在它旁边）")
    if cmd == "revoke-token":
        if not TOKEN_ID_RE.match(name):
            sys.exit("用法：revoke-token <令牌 id>（16 位十六进制，tokens 命令或网页上能看到）")
        if not revoke_token(name):
            sys.exit(f"❌ 没有令牌 {name}（已到期的不在表里）")
        print(f"✅ 已作废令牌 {name}；{RELOAD_EVERY:g} 秒内生效")
        return
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
    if cmd == "tokens":
        state = load_token_state()
        now = int(time.time())
        live = {j: t for j, t in (state[2] if state else {}).items() if t["expiresAt"] > now}
        for t in list_tokens(live, tenant if rest else None):
            print("\t".join([t["id"], t["scope"], "有效", t["expiresAt"], t["name"]]))
        return
    if cmd == "revoke":
        revoke_identity(tenant)
        print(f"✅ 已作废 {who} 的全部设备令牌；{RELOAD_EVERY:g} 秒内生效")
        return
    try:
        token, meta = issue_device_token(tenant, sess, scope, label)
    except TooManyTokens:
        sys.exit(f"❌ {who} 名下已有 {MAX_TOKENS} 个未到期的令牌（或全部身份合计已到 {MAX_TOKENS_TOTAL} 个）；"
                 "先 revoke-token 用不着的（吊销就腾出名额），或 revoke 全部作废")
    print(token)
    sys.stderr.write(f"✅ 上面是 {who} 的设备令牌（id {meta['id']}，范围 {scope}），{meta['expiresAt']} 过期，"
                     f"{RELOAD_EVERY:g} 秒内可用。请求头 Authorization: Bearer <令牌>；"
                     + {"report": "只能上报代理运行（POST /api/core/agents/...）",
                        "read": "能上报、能读 /api/core/* 与 MCP 的只读工具",
                        "write": "能调 /api/core/* 与 /api/mcp/"}[scope] + "\n")


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
        sys.exit(f"❌ 不认识的命令 {cmd}（adduser / passwd / deluser / users / token / revoke / revoke-token / tokens）")


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
        "\n  auth.gate.v1.4 · 占位实现（STUB）：一道门 + 一本小账本，不是账号系统。\n"
        f"  登录方式：{' + '.join(modes)}\n"
        f"  监听 {host}:{port}   cookie Secure={COOKIE_SECURE}   会话 {SESSION_DAYS} 天\n"
    )
    if ACCOUNTS.by_id and PASSWORD:
        banner += "  ⚠ 两种登录同时开着：知道共享口令的人都进 u_local。多用户部署请去掉 AUTH_PASSWORD\n"
    if ACCOUNTS.anonymous_allowed():
        banner += ("  ⚠ 匿名上报开着：能连到这个端口的人不带任何凭据就能往泳道里加代理运行记录（读不到任何东西）。\n"
                   "    端口对外开放时请设 AUTH_ANONYMOUS_REPORT=false\n")
    if not COOKIE_SECURE:
        banner += "  ⚠ AUTH_COOKIE_SECURE=false —— 只应出现在本机 HTTP 调试，别上公网\n"
    if os.environ.get("AUTH_SECRET", "").strip() == "":
        banner += "  ℹ 未设 AUTH_SECRET，本次随机生成：重启后所有会话失效\n"
    sys.stderr.write(banner + "\n")
    threading.Thread(target=_watch, daemon=True).start()
    ThreadingHTTPServer((host, int(port)), Handler).serve_forever()


if __name__ == "__main__":
    main()
