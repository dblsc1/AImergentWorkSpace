"""调试窗口（agent.chat.v1 v1.3，第九节）：把 opencode 发给模型的原始请求、模型回的原始应答录下来。

只在 AGENT_DEBUG=1 时存在。结构：

    opencode ──POST <代理>/<租户令牌>/chat/completions──▶ 本代理（127.0.0.1，同一进程）──▶ 真上游

- 每个运行时拉起时拿一个随机令牌，配置里的 baseURL 指到 http://127.0.0.1:<端口>/<令牌>；令牌 → 租户。
  代理只听 127.0.0.1，容器外连不上；别的租户的进程不知道这个令牌。
- opencode 每个请求都带 `x-session-id: <opencode 会话 id>`（1.18.33 实测）。适配器在一轮开始时登记
  (租户, opencode 会话) → (我们的会话, 这条回答的 messageId)；请求到达时按它归到那一轮，登记不到的不录。
- 录：请求体整份（系统提示、历史、工具表、参数）；应答——SSE 流拼回正文、思考（reasoning_content）、
  工具调用（带参数）、finish_reason、usage、分块数；非流式的原样。**不录任何请求/应答头**，
  整条记录里出现的模型密钥原文替换成 [已隐去]。
- 存在租户目录的 debug/<会话 id>/ 下，一轮一个文件；每会话留最近 MAX_TURNS 轮，每条请求记录 ≤ MAX_RECORD，
  每轮 ≤ MAX_TURN_BYTES / MAX_REQUESTS；删会话一并删（store.delete）；调试关着启动时整个清掉。
- 不往 stdout / 日志写任何记录内容。
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import secrets
import shutil
import socket
import time
from pathlib import Path

import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from . import config
from .store import Store, now, tenant_dir

log = logging.getLogger("agent")

# 自带 provider 的真上游（opencode 1.18.33 自带目录：deepseek 走 @ai-sdk/openai-compatible，
# api = https://api.deepseek.com，请求打到 <api>/chat/completions）。别的自带 provider 要调试就设 AGENT_BASE_URL。
KNOWN_UPSTREAMS = {"deepseek": "https://api.deepseek.com"}
MAX_TURNS = 20                  # 每个会话留最近这么多轮
MAX_REQUESTS = 50               # 一轮最多录这么多次模型请求
MAX_RECORD = 2 * 1024 * 1024    # 一条请求记录（请求 + 应答）序列化后的上限
MAX_TURN_BYTES = 8 * 1024 * 1024
MAX_STR = 256 * 1024            # 单个字符串超过这么长就截
REDACTED = "[已隐去]"
HOP = {"host", "content-length", "connection", "keep-alive", "transfer-encoding", "accept-encoding",
       "te", "upgrade", "proxy-authorization", "proxy-connection", "trailer"}


def upstream_of(s: config.Settings) -> str:
    """调试要转发到哪。空 = 这个配置下调试做不了（AGENT_DEBUG 被忽略）。"""
    if not s.debug or not s.configured:
        return ""
    return (s.base_url or KNOWN_UPSTREAMS.get(s.model.partition("/")[0], "")).rstrip("/")


def wipe(data_dir: str) -> None:
    """调试关着：把以前录下的全删掉（关了就不该还躺在盘上）。"""
    for d in Path(data_dir, "tenants").glob("*/debug"):
        shutil.rmtree(d, ignore_errors=True)


def _map(v, f):
    """对 JSON 里每个字符串（含对象的键）套 f。"""
    if isinstance(v, str):
        return f(v)
    if isinstance(v, list):
        return [_map(x, f) for x in v]
    if isinstance(v, dict):
        return {f(k): _map(x, f) for k, x in v.items()}
    return v


def _clip(s: str) -> str:
    return s if len(s) <= MAX_STR else s[:MAX_STR] + f"…[截断：原长 {len(s)} 字]"


class SSEAssembler:
    """OpenAI Chat Completions 流（data: {chunk}）拼回一整条应答。喂字节，吐 result()。"""

    def __init__(self):
        self.buf = b""
        self.fed = 0
        self.capped = False       # 应答超过 MAX_RECORD 字节：后面的照转不录（内存有上限）
        self.chunks = 0
        self.unparsed = 0
        self.text: list[str] = []
        self.reasoning: list[str] = []
        self.tools: dict[int, dict] = {}
        self.finish = None
        self.usage = None
        self.model = None

    def feed(self, b: bytes) -> None:
        if self.fed >= MAX_RECORD:
            self.capped = True
            return
        self.fed += len(b)
        self.buf += b
        *lines, self.buf = self.buf.split(b"\n")
        for line in lines:
            self._line(line.strip())

    def _line(self, line: bytes) -> None:
        if not line.startswith(b"data:"):
            return
        data = line[5:].strip()
        if data == b"[DONE]":
            return
        self.chunks += 1
        try:
            c = json.loads(data)
        except ValueError:
            self.unparsed += 1
            return
        if not isinstance(c, dict):
            self.unparsed += 1
            return
        self.model = c.get("model") or self.model
        if c.get("usage"):
            self.usage = c["usage"]
        for ch in c.get("choices") or []:
            d = ch.get("delta") or {}
            if isinstance(d.get("content"), str):
                self.text.append(d["content"])
            r = d.get("reasoning_content") or d.get("reasoning")
            if isinstance(r, str):
                self.reasoning.append(r)
            for tc in d.get("tool_calls") or []:
                t = self.tools.setdefault(tc.get("index", len(self.tools)), {"id": None, "name": None, "arguments": ""})
                t["id"] = tc.get("id") or t["id"]
                fn = tc.get("function") or {}
                t["name"] = fn.get("name") or t["name"]
                if isinstance(fn.get("arguments"), str):
                    t["arguments"] += fn["arguments"]
            if ch.get("finish_reason"):
                self.finish = ch["finish_reason"]

    def result(self) -> dict:
        if self.buf.strip():
            self._line(self.buf.strip())
            self.buf = b""
        return {"stream": True, "model": self.model, "content": "".join(self.text),
                "reasoning": "".join(self.reasoning) or None,
                "toolCalls": [self.tools[k] for k in sorted(self.tools)], "finishReason": self.finish,
                "usage": self.usage, "chunks": self.chunks, "unparsedChunks": self.unparsed,
                "captureTruncated": self.capped}


class _QuietServer(uvicorn.Server):
    def capture_signals(self):      # 主服务管信号；这个内嵌的不抢
        return contextlib.nullcontext()


class Recorder:
    def __init__(self, s: config.Settings, upstream: str):
        self.s, self.upstream = s, upstream
        self.store = Store(s.data_dir)
        self.tokens: dict[str, str] = {}               # 令牌 → 租户
        self.by_tenant: dict[str, str] = {}            # 租户 → 当前令牌
        self.turns: dict[tuple[str, str], tuple[str, str]] = {}   # (租户, oc 会话) → (会话, messageId)
        self.port: int | None = None
        self._server: _QuietServer | None = None
        self._task: asyncio.Task | None = None
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(30, read=self.s.max_turn_seconds), trust_env=False)
        self.app = Starlette(routes=[Route("/{token}/{path:path}", self.proxy, methods=["GET", "POST"])])

    # ── 生命周期 ──
    async def start(self) -> None:
        if self._task:
            return
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        self.port = sock.getsockname()[1]
        self._server = _QuietServer(uvicorn.Config(self.app, lifespan="off", log_config=None,
                                                   log_level="warning", access_log=False))
        self._task = asyncio.create_task(self._server.serve(sockets=[sock]))
        for _ in range(200):
            if self._server.started:
                return
            await asyncio.sleep(0.01)

    async def stop(self) -> None:
        if self._task:
            self._server.should_exit = True
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._task, 5)
        await self.client.aclose()

    def base_for(self, tenant: str) -> str:
        """拉起运行时时调：换一个新令牌（旧的作废），返回给 opencode 配置用的 baseURL。"""
        old = self.by_tenant.pop(tenant, None)
        self.tokens.pop(old, None)
        tok = secrets.token_urlsafe(24)
        self.tokens[tok], self.by_tenant[tenant] = tenant, tok
        return f"http://127.0.0.1:{self.port}/{tok}"

    # ── 一轮 ──
    def begin(self, tenant: str, oc_id: str, sid: str, mid: str, user_mid: str) -> None:
        d = self._dir(tenant, sid)
        d.mkdir(parents=True, exist_ok=True)
        _write(d / f"{time.time_ns():020d}_{mid}.json",
               {"messageId": mid, "userMessageId": user_mid, "startedAt": now(), "requests": [], "omitted": 0})
        for old in sorted(d.glob("*.json"))[:-MAX_TURNS]:
            old.unlink(missing_ok=True)
        self.turns[(tenant, oc_id)] = (sid, mid)

    def end(self, tenant: str, oc_id: str) -> None:
        self.turns.pop((tenant, oc_id), None)

    def read(self, tenant: str, sid: str, mid: str | None) -> list[dict]:
        out = []
        for f in sorted(self._dir(tenant, sid).glob("*.json")):
            if mid and not f.name.endswith(f"_{mid}.json"):
                continue
            with contextlib.suppress(OSError, ValueError):
                out.append(json.loads(f.read_text()))
        return out

    def _dir(self, tenant: str, sid: str) -> Path:
        return tenant_dir(self.s.data_dir, tenant) / "debug" / sid

    def _append(self, tenant: str, sid: str, mid: str, rec: dict) -> None:
        if self.store.get(tenant, sid) is None:        # 会话被删了：不复活
            return
        f = next(iter(self._dir(tenant, sid).glob(f"*_{mid}.json")), None)
        if f is None:                                   # 这一轮已被挤掉
            return
        turn = json.loads(f.read_text())
        rec = _map(rec, self._redact)          # 先逐个字符串隐去密钥，再序列化
        text = json.dumps(rec, ensure_ascii=False)
        if len(text.encode()) > MAX_RECORD:
            rec = _map(rec, _clip)
            text = json.dumps(rec, ensure_ascii=False)
        # 还超：先大的那半（请求或应答）只留开头，还超再截另一半（≤ 4 字节/字 × MAX_RECORD/16）
        for part in sorted(("request", "response"), key=lambda k: -len(json.dumps(rec.get(k), ensure_ascii=False))):
            if len(text.encode()) <= MAX_RECORD:
                break
            raw = json.dumps(rec.get(part), ensure_ascii=False)
            rec[part] = {"truncated": True, "bytes": len(raw.encode()), "head": raw[:MAX_RECORD // 16]}
            text = json.dumps(rec, ensure_ascii=False)
        used = sum(len(json.dumps(r, ensure_ascii=False).encode()) for r in turn["requests"])
        if len(turn["requests"]) >= MAX_REQUESTS or used + len(text.encode()) > MAX_TURN_BYTES:
            turn["omitted"] += 1
        else:
            rec = json.loads(text)
            rec["n"] = len(turn["requests"]) + 1
            turn["requests"].append(rec)
        _write(f, turn)

    def _redact(self, text: str) -> str:
        k = self.s.api_key
        # ponytail: 短于 8 字的「密钥」（本机 Ollama 随手填的 x）不替换，不然整条记录会被打碎
        return text.replace(k, REDACTED) if k and len(k) >= 8 else text

    # ── 代理 ──
    async def proxy(self, req: Request):
        tenant = self.tokens.get(req.path_params["token"])
        if tenant is None:
            return JSONResponse({"detail": "not found"}, status_code=404)
        target = self.turns.get((tenant, req.headers.get("x-session-id", "")))
        body = await req.body()
        path = req.path_params["path"]
        url = f"{self.upstream}/{path}" + (f"?{req.url.query}" if req.url.query else "")
        headers = {k: v for k, v in req.headers.items() if k.lower() not in HOP}
        headers["accept-encoding"] = "identity"        # 要读得懂才录得下
        started = time.monotonic()
        try:
            up = await self.client.send(self.client.build_request(req.method, url, headers=headers, content=body),
                                        stream=True)
        except httpx.HTTPError:
            log.warning("debug proxy upstream unreachable")     # 不记地址、不记异常原文
            return JSONResponse({"error": {"message": "debug proxy: upstream unreachable"}}, status_code=502)
        sse = target is not None and "text/event-stream" in up.headers.get("content-type", "")   # 不录的不攒
        asm, raw = SSEAssembler(), bytearray()

        async def relay():
            aborted = True
            try:
                async for b in up.aiter_bytes():     # 解过压缩的：上游不理 identity 也照样对
                    if sse:
                        asm.feed(b)
                    elif target and len(raw) < MAX_RECORD:
                        raw.extend(b[:MAX_RECORD - len(raw)])
                    yield b
                aborted = False
            finally:
                if target:          # 先录（同步），再关连接：这里可能正在被取消
                    self._record(tenant, target, req.method, path, body, up.status_code, sse, asm, raw,
                                 started, aborted)
                await up.aclose()

        keep = {k: v for k, v in up.headers.items() if k.lower() not in HOP and k.lower() != "content-encoding"}
        return StreamingResponse(relay(), status_code=up.status_code, headers=keep)

    def _record(self, tenant, target, method, path, body, status, sse, asm, raw, started, aborted) -> None:
        try:
            request = json.loads(body) if body else None
        except ValueError:
            request = body.decode(errors="replace")
        if sse:
            response = asm.result()
        else:
            txt = bytes(raw).decode(errors="replace")
            try:
                response = {"stream": False, "body": json.loads(txt)}
            except ValueError:
                response = {"stream": False, "body": txt}
        rec = {"at": now(), "method": method, "path": "/" + path, "status": status,
               "ms": int((time.monotonic() - started) * 1000), "aborted": aborted,
               "request": request, "response": response}
        try:
            self._append(tenant, *target, rec)
        except (OSError, ValueError):
            log.warning("debug record write failed")


def _write(path: Path, obj) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False))
    tmp.replace(path)
