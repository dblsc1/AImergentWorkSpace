"""opencode 运行时：一个租户一个 `opencode serve` 子进程（agent.chat.v1 第七节「结构」）。

- 监听 127.0.0.1 的随机端口，每进程一个随机的 OPENCODE_SERVER_PASSWORD；
- HOME / XDG_* 全指到租户目录下，opencode 的会话库（XDG_DATA_HOME/opencode/opencode.db）落在那里；
- 进程环境从零拼，不继承适配器的环境：只给它密钥/端点（{env:…} 要读）、租户、opencode 开关；
- 按需拉起，空闲 AGENT_IDLE_SECONDS（缺省 15 分钟）收掉，同时最多 AGENT_MAX_RUNTIMES 个。

还有 opencode 事件 → 本契约 SSE 事件的翻译（Translator），纯函数式，单测直接喂录下来的事件。
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import secrets
import time
from pathlib import Path

import httpx

from . import config
from .store import tenant_dir

log = logging.getLogger("agent")

TOOL_PREFIX = "honeycomb_"
# opencode 的开关：不读项目/Claude Code 的配置与技能、不自动更新、不分享、不下 LSP、不拉模型目录
# （用这个版本自带的目录，行为可复现）、不带 web 界面、不加载外部插件。
OPENCODE_FLAGS = {k: "1" for k in (
    "OPENCODE_DISABLE_AUTOUPDATE", "OPENCODE_DISABLE_SHARE", "OPENCODE_DISABLE_PROJECT_CONFIG",
    "OPENCODE_DISABLE_CLAUDE_CODE", "OPENCODE_DISABLE_EXTERNAL_SKILLS", "OPENCODE_DISABLE_LSP_DOWNLOAD",
    "OPENCODE_DISABLE_MODELS_FETCH", "OPENCODE_DISABLE_EMBEDDED_WEB_UI", "OPENCODE_DISABLE_DEFAULT_PLUGINS",
    "OPENCODE_PURE")}
LISTEN_RE = re.compile(rb"listening on http://127\.0\.0\.1:(\d+)")


class RuntimeUnavailable(Exception):
    pass


class Runtime:
    def __init__(self, tenant: str, proc: asyncio.subprocess.Process, port: int, password: str):
        self.tenant, self.proc = tenant, proc
        self.client = httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", auth=("opencode", password),
                                        timeout=httpx.Timeout(30, read=None), trust_env=False)
        self.active = 0
        self.last_used = time.monotonic()

    @property
    def alive(self) -> bool:
        return self.proc.returncode is None

    async def _ok(self, r: httpx.Response) -> httpx.Response:
        if r.status_code >= 400:
            raise RuntimeUnavailable(f"opencode {r.request.method} {r.request.url.path} → {r.status_code}")
        return r

    async def ensure_session(self, oc_id: str | None) -> str:
        if oc_id:
            r = await self.client.get(f"/session/{oc_id}")
            if r.status_code == 200:
                return oc_id
        r = await self._ok(await self.client.post("/session", json={}))
        return r.json()["id"]

    async def prompt(self, oc_id: str, text: str) -> None:
        await self._ok(await self.client.post(f"/session/{oc_id}/prompt_async", json={
            "agent": "honeycomb", "parts": [{"type": "text", "text": text}]}))

    async def abort(self, oc_id: str) -> None:
        with contextlib.suppress(httpx.HTTPError):
            await self.client.post(f"/session/{oc_id}/abort", json={})

    async def delete_session(self, oc_id: str) -> None:
        await self._ok(await self.client.delete(f"/session/{oc_id}"))

    @contextlib.asynccontextmanager
    async def events(self, oc_id: str):
        """订阅 /event，等到 server.connected 再交出去（之后才发 prompt，不丢事件）。只给这个会话的事件。"""
        async with self.client.stream("GET", "/event") as r:
            await self._ok(r)
            lines = r.aiter_lines()

            async def it():
                async for line in lines:
                    if not line.startswith("data:"):
                        continue
                    try:
                        ev = json.loads(line[5:])
                    except ValueError:
                        continue
                    if ev.get("type") == "server.connected":
                        continue
                    if (ev.get("properties") or {}).get("sessionID") == oc_id:
                        yield ev

            async with asyncio.timeout(15):   # 第一条是 server.connected；等不到就算运行时出错
                async for line in lines:
                    if line.startswith("data:") and "server.connected" in line:
                        break
            yield it()

    async def stop(self) -> None:
        await self.client.aclose()
        if self.alive:
            self.proc.terminate()
            try:
                await asyncio.wait_for(self.proc.wait(), 5)
            except asyncio.TimeoutError:
                self.proc.kill()
                await self.proc.wait()


class RuntimeManager:
    def __init__(self, s: config.Settings):
        self.s = s
        self.runtimes: dict[str, Runtime] = {}
        self.lock = asyncio.Lock()
        self._reaper: asyncio.Task | None = None

    def start_reaper(self) -> None:
        self._reaper = asyncio.create_task(self._reap_loop())

    async def _reap_loop(self) -> None:
        while True:
            await asyncio.sleep(min(30, self.s.idle_seconds))
            await self.reap()

    async def reap(self) -> None:
        async with self.lock:
            cutoff = time.monotonic() - self.s.idle_seconds
            for t, rt in list(self.runtimes.items()):
                if not rt.alive or (rt.active == 0 and rt.last_used < cutoff):
                    del self.runtimes[t]
                    await rt.stop()

    def running(self, tenant: str) -> Runtime | None:
        rt = self.runtimes.get(tenant)
        return rt if rt and rt.alive else None

    async def acquire(self, tenant: str) -> Runtime:
        """拿到（必要时拉起）这个租户的运行时，并记一次占用；用完调 release。"""
        # ponytail: 一把全局锁，拉起一个进程（约 1–3 秒）期间别的租户排队；租户多了再改成按租户的锁
        async with self.lock:
            rt = self.running(tenant)
            if rt is None:
                dead = self.runtimes.pop(tenant, None)
                if dead is not None:        # 自己退出了的：关掉它的连接池、收掉子进程
                    await dead.stop()
                if len(self.runtimes) >= self.s.max_runtimes:
                    idle = [x for x in self.runtimes.values() if x.active == 0]
                    if not idle:
                        raise RuntimeUnavailable("full")
                    lru = min(idle, key=lambda x: x.last_used)
                    del self.runtimes[lru.tenant]
                    await lru.stop()
                rt = await self._spawn(tenant)
                self.runtimes[tenant] = rt
                await self._flush_pending_deletes(rt)
            rt.active += 1
            rt.last_used = time.monotonic()
            return rt

    def release(self, rt: Runtime) -> None:
        rt.active -= 1
        rt.last_used = time.monotonic()

    async def _spawn(self, tenant: str) -> Runtime:
        base = tenant_dir(self.s.data_dir, tenant)
        home, work = base / "oc", base / "work"
        logdir = home / "data" / "opencode" / "log"
        for d in (home, work, logdir):
            d.mkdir(parents=True, exist_ok=True)
        # opencode 自己的日志会原样记上游报错（可能带密钥片段、内部地址）——契约第四节「日志脱敏」。
        # 把它接到 /dev/null；我们只在自己的日志里记类别、状态码、关联 id。
        lf = logdir / "opencode.log"
        if not lf.is_symlink():
            lf.unlink(missing_ok=True)
            lf.symlink_to("/dev/null")
        password = secrets.token_urlsafe(24)
        local = tenant == config.LOCAL_TENANT
        env = {
            "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
            "HOME": str(home),
            "XDG_DATA_HOME": str(home / "data"), "XDG_CONFIG_HOME": str(home / "config"),
            "XDG_CACHE_HOME": str(home / "cache"), "XDG_STATE_HOME": str(home / "state"),
            "OPENCODE_CONFIG_CONTENT": json.dumps(config.opencode_config(self.s, not local)),
            "OPENCODE_SERVER_PASSWORD": password,
            **OPENCODE_FLAGS,
        }
        if self.s.api_key:
            env["AGENT_API_KEY"] = self.s.api_key
        if self.s.base_url:
            env["AGENT_BASE_URL"] = self.s.base_url
        if not local:
            env["HC_TENANT"] = tenant
        try:
            proc = await asyncio.create_subprocess_exec(
                self.s.opencode_bin, "serve", "--hostname", "127.0.0.1", "--port", "0",
                cwd=work, env=env, stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        except OSError as e:
            log.error("runtime start failed category=internal sub=spawn errno=%s", e.errno)
            raise RuntimeUnavailable("spawn")
        port = None
        try:
            async with asyncio.timeout(30):
                while port is None:
                    line = await proc.stdout.readline()
                    if not line:
                        break
                    if m := LISTEN_RE.search(line):
                        port = int(m.group(1))
        except TimeoutError:
            pass
        if port is None:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            await proc.wait()
            log.error("runtime start failed category=internal sub=listen rc=%s", proc.returncode)
            raise RuntimeUnavailable("listen")
        asyncio.create_task(_drain(proc.stdout))
        return Runtime(tenant, proc, port, password)

    # ── 删会话时运行时没在跑：记下来，下次拉起时再删 opencode 那边的 ──
    def _pending_file(self, tenant: str) -> Path:
        return tenant_dir(self.s.data_dir, tenant) / "pending_delete.json"

    def defer_delete(self, tenant: str, oc_id: str) -> None:
        f = self._pending_file(tenant)
        ids = json.loads(f.read_text()) if f.exists() else []
        ids.append(oc_id)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(ids))

    async def _flush_pending_deletes(self, rt: Runtime) -> None:
        f = self._pending_file(rt.tenant)
        if not f.exists():
            return
        left = []
        for oc_id in json.loads(f.read_text()):
            try:
                await rt.delete_session(oc_id)
            except Exception:
                left.append(oc_id)          # 下次拉起再试，不丢
        if left:
            f.write_text(json.dumps(left))
        else:
            f.unlink()

    async def shutdown(self) -> None:
        if self._reaper:
            self._reaper.cancel()
        for rt in list(self.runtimes.values()):
            await rt.stop()
        self.runtimes.clear()


async def _drain(stream: asyncio.StreamReader) -> None:
    while await stream.read(65536):
        pass


# ── opencode 事件 → agent.chat.v1 第四节 ────────────────────────────────
ERROR_TEXT = {
    "auth": "模型服务拒绝了密钥：检查 .env 里的 AGENT_API_KEY",
    "quota": "模型服务额度用完或请求太频繁，稍后再试",
    "timeout": "模型服务超时，稍后再试",
    "upstream": "模型服务出错了，稍后再试",
    "internal": "聊天后端内部错误，稍后再试",
}


def classify(err: dict) -> tuple[str, str, int | None]:
    """opencode 的 session.error → (code, 细分类别, 上游状态码)。只看名字与状态码，不碰报错正文。"""
    name = err.get("name") or ""
    status = (err.get("data") or {}).get("statusCode")
    status = status if isinstance(status, int) else None
    if name == "ProviderAuthError" or status in (401, 403):
        return "upstream", "auth", status
    if status in (402, 429):
        return "upstream", "quota", status
    if status in (408, 504):
        return "upstream", "timeout", status
    return "upstream", "upstream", status


class Translator:
    """喂 opencode 事件，吐 (事件类型, data) 列表。`final` 非空即已终止。"""

    def __init__(self):
        self.user_msgs: set[str] = set()
        self.text_parts: dict[str, int] = {}     # 正文 part id → 已吐出的字符数
        self.tools: dict[str, str] = {}          # callID → 已报告的状态
        self.text: list[str] = []
        self.cancelled = False
        self.final: tuple | None = None          # ("done", reason) | ("error", code, sub, status)

    def feed(self, ev: dict) -> list[tuple[str, dict]]:
        t, p = ev.get("type"), ev.get("properties") or {}
        out: list[tuple[str, dict]] = []
        if t == "message.updated":
            info = p.get("info") or {}
            if info.get("role") == "user":
                self.user_msgs.add(info.get("id"))
        elif t == "message.part.updated":
            part = p.get("part") or {}
            if part.get("type") == "text" and part.get("messageID") not in self.user_msgs:
                seen = self.text_parts.setdefault(part["id"], 0)
                full = part.get("text") or ""
                if len(full) > seen:              # 没走增量、整段给的 provider
                    out += self._delta(part["id"], full[seen:])
            elif part.get("type") == "tool" and str(part.get("tool", "")).startswith(TOOL_PREFIX):
                st = {"pending": "running", "running": "running", "completed": "done", "error": "error"}.get(
                    (part.get("state") or {}).get("status"))
                cid = part.get("callID") or part.get("id")
                if st and self.tools.get(cid) != st:
                    self.tools[cid] = st
                    out.append(("tool", {"name": part["tool"][len(TOOL_PREFIX):], "status": st}))
        elif t == "message.part.delta":
            if p.get("field") == "text" and p.get("partID") in self.text_parts:
                out += self._delta(p["partID"], p.get("delta") or "")
        elif t == "session.error":
            err = p.get("error") or {}
            if err.get("name") == "MessageAbortedError":
                self.cancelled = True
            elif self.final is None:
                self.final = ("error", *classify(err))
        elif t == "session.idle" or (t == "session.status" and (p.get("status") or {}).get("type") == "idle"):
            if self.final is None:
                self.final = ("done", "cancelled" if self.cancelled else "end")
        return out

    def _delta(self, pid: str, s: str) -> list[tuple[str, dict]]:
        if not s:
            return []
        self.text_parts[pid] += len(s)
        self.text.append(s)
        return [("delta", {"text": s})]
