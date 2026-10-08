"""agent.chat.v1 的缺省实现：opencode 适配器。端点见契约第二节。"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import secrets
import time
from dataclasses import dataclass, field

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, ValidationError
from starlette.background import BackgroundTask
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import autotrack, config, debug
from .runtime import ERROR_TEXT, RuntimeManager, RuntimeUnavailable, Translator
from .store import ID_RE, Store, new_id

log = logging.getLogger("agent")
MAX_BODY = 64 * 1024
MAX_TEXT = 8000
PER_TENANT_GENERATING = 2
PING_SECONDS = 15.0
CANCEL_GRACE = 10.0   # 取消后等运行时回话的上限，过了就自己收尾


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NewSession(_Strict):
    title: str | None = None


class NewMessage(_Strict):
    text: str


class Empty(_Strict):
    pass


@dataclass
class Gen:
    tenant: str
    sid: str
    oc_id: str
    rt: object
    cancelled_at: float | None = None
    abandoned: bool = False
    tr: Translator = field(default_factory=Translator)


def create_app(settings: config.Settings | None = None, manager=None) -> FastAPI:
    s = settings or config.load()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)   # 它的 INFO 行带完整地址（上游、opencode 会话号），不记
    store = Store(s.data_dir)
    mgr = manager or RuntimeManager(s)
    gens: dict[tuple[str, str], Gen] = {}
    rec = getattr(mgr, "debug", None)        # 调试窗口（第九节）；None = 没开
    if rec is None:
        debug.wipe(s.data_dir)

    @contextlib.asynccontextmanager
    async def lifespan(app):
        if hasattr(mgr, "start_reaper"):
            mgr.start_reaper()
        if app.state.autotrack:
            app.state.autotrack.start()
        yield
        if app.state.autotrack:
            await app.state.autotrack.stop()
        await mgr.shutdown()

    app = FastAPI(title="agent.chat.v1 · opencode", lifespan=lifespan, docs_url=None, redoc_url=None,
                  openapi_url=None)
    app.state.gens, app.state.manager, app.state.store = gens, mgr, store

    # ── 错误一律 {"detail": "<人话>"} ──
    @app.exception_handler(StarletteHTTPException)
    async def _http(_req, e: StarletteHTTPException):
        return JSONResponse({"detail": str(e.detail)}, status_code=e.status_code)

    @app.exception_handler(Exception)
    async def _any(_req, _e: Exception):
        log.error("request error category=internal")      # 不记异常对象：里面可能有上游原文
        return JSONResponse({"detail": "聊天后端内部错误，稍后再试"}, status_code=500)

    @app.exception_handler(RequestValidationError)
    async def _val(_req, e: RequestValidationError):
        return JSONResponse({"detail": _explain(e.errors())}, status_code=422)

    def tenant_of(req: Request) -> str:
        t = req.headers.get("x-nexus-tenant", "")
        if not t:
            if s.strict:
                raise HTTPException(401, "缺少租户头 X-Nexus-Tenant（严格模式）")
            return config.LOCAL_TENANT
        if not config.TENANT_RE.fullmatch(t):
            raise HTTPException(400, f"X-Nexus-Tenant 格式不对：{t!r}")
        store.note_tenant(t)     # 后台认窗口的工人（autotrack.py）靠它知道该替哪些租户去问
        return t

    async def body_of(req: Request, model: type[_Strict]):
        cl = req.headers.get("content-length")
        if cl and cl.isdigit() and int(cl) > MAX_BODY:
            raise HTTPException(413, "请求体超过 64 KiB")
        raw = b""
        async for chunk in req.stream():
            raw += chunk
            if len(raw) > MAX_BODY:
                raise HTTPException(413, "请求体超过 64 KiB")
        ctype = req.headers.get("content-type", "").split(";")[0].strip().lower()
        # 带了别的 Content-Type（表单、text/plain）一律 415，空体也一样：跨站表单 POST 进不来
        if (raw or ctype) and ctype != "application/json":
            raise HTTPException(415, "请求体必须是 application/json")
        if not raw:
            return model()
        try:
            data = json.loads(raw)
        except ValueError:
            raise HTTPException(422, "请求体不是合法的 JSON")
        if not isinstance(data, dict):
            raise HTTPException(422, "请求体必须是 JSON 对象")
        try:
            return model.model_validate(data)
        except ValidationError as e:
            raise HTTPException(422, _explain(e.errors()))

    def session_of(tenant: str, sid: str) -> dict:
        sess = store.get(tenant, sid) if re.fullmatch(ID_RE, sid) else None
        if sess is None:
            raise HTTPException(404, "会话不存在")
        return sess

    def public(tenant: str, sess: dict) -> dict:
        return {k: sess[k] for k in ("id", "title", "createdAt", "updatedAt")} | {
            "busy": (tenant, sess["id"]) in gens}

    # ── 端点 ──
    @app.get("/api/agent/health")
    async def health():
        return {"status": "ok", "configured": s.configured, "debug": rec is not None}

    @app.get("/api/agent/sessions")
    async def list_sessions(req: Request):
        t = tenant_of(req)
        return {"items": [public(t, x) for x in store.list(t)]}

    @app.post("/api/agent/sessions", status_code=201)
    async def create_session(req: Request):
        t = tenant_of(req)
        b = await body_of(req, NewSession)
        title = (b.title or "").strip() or None
        if title and len(title) > 100:
            raise HTTPException(422, "title 最多 100 个字符")
        if len(store.list(t)) >= s.max_sessions:
            raise HTTPException(409, f"会话已达上限 {s.max_sessions} 个：先删掉旧的")
        return public(t, store.create(t, title))

    @app.get("/api/agent/sessions/{sid}")
    async def get_session(req: Request, sid: str, limit: int = 100):
        t = tenant_of(req)
        if not 1 <= limit <= 500:
            raise HTTPException(422, "limit 要在 1–500 之间")
        sess = session_of(t, sid)
        msgs = store.messages(t, sid, limit)
        truncated = sess.get("count", 0) > limit or sess.get("dropped", 0) > 0
        return {"session": public(t, sess), "messages": msgs, "truncated": truncated}

    @app.get("/api/agent/sessions/{sid}/debug")
    async def get_debug(req: Request, sid: str, messageId: str | None = None):
        if rec is None:
            raise HTTPException(404, "调试没开：.env 里设 AGENT_DEBUG=1 后重启")
        t = tenant_of(req)
        session_of(t, sid)
        if messageId is not None and not re.fullmatch(ID_RE, messageId):
            raise HTTPException(422, "messageId 格式不对")
        return {"turns": rec.read(t, sid, messageId)}

    @app.delete("/api/agent/sessions/{sid}", status_code=204)
    async def delete_session(req: Request, sid: str):
        t = tenant_of(req)
        session_of(t, sid)
        g = gens.get((t, sid))
        if g:
            await _cancel(g)
        sess = store.delete(t, sid)
        if sess and sess.get("ocId"):
            rt = mgr.running(t)
            try:
                if rt is None:
                    raise RuntimeUnavailable("not running")
                await rt.delete_session(sess["ocId"])
            except Exception:
                mgr.defer_delete(t, sess["ocId"])
        return Response(status_code=204)

    @app.post("/api/agent/sessions/{sid}/cancel", status_code=204)
    async def cancel(req: Request, sid: str):
        t = tenant_of(req)
        await body_of(req, Empty)
        session_of(t, sid)
        g = gens.get((t, sid))
        if g:
            await _cancel(g)
        return Response(status_code=204)

    async def _cancel(g: Gen) -> None:
        if g.cancelled_at is None:
            g.cancelled_at = time.monotonic()
            g.tr.cancelled = True
            if g.rt is not None:           # 还在拉起运行时：stream() 发完 prompt 会看到这个标记
                await g.rt.abort(g.oc_id)

    @app.post("/api/agent/sessions/{sid}/messages")
    async def send(req: Request, sid: str):
        t = tenant_of(req)
        b = await body_of(req, NewMessage)
        session_of(t, sid)
        # background 在响应结束后总会跑，包括「流还没开始客户端就断了」——那时生成器一行没执行、
        # 它的 finally 也不会跑，不兜这一下会话就永远 busy。正常结束时 abandon 什么都不做。
        stream, abandon = await turn(t, sid, b.text)
        return StreamingResponse(stream, media_type="text/event-stream", background=BackgroundTask(abandon),
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    async def turn(t: str, sid: str, raw: str, fresh: bool = False):
        """起一轮回答 → (SSE 事件流, abandon)。人发消息与后台认窗口（autotrack.py）走的是同一条路：
        同样的校验、限流、时限、存档、调试记录。调用方读完流，或不读了就调 abandon()（= 取消）。
        ``fresh``（只有后台认窗口传）：这一轮换一个新的模型上下文，旧的记下来待删。"""
        sess = session_of(t, sid)
        text = raw.strip()
        if not text:
            raise HTTPException(422, "text 不能为空")
        if len(raw) > MAX_TEXT:
            raise HTTPException(422, f"text 最多 {MAX_TEXT} 个字符")
        if not s.configured:
            raise HTTPException(503, "还没配模型：在 .env 里填 AGENT_API_KEY 后重启")
        key = (t, sid)
        if key in gens:
            raise HTTPException(409, "这个会话正在生成回答")
        if sum(1 for (tt, _) in gens if tt == t) >= PER_TENANT_GENERATING:
            raise HTTPException(429, "同时在生成的回答太多了，等一条结束再发")
        g = Gen(t, sid, "", None)
        gens[key] = g            # 先占位再 await，防并发穿过上面的检查
        if fresh and sess.get("ocId"):
            # 占了位才换上下文（从读会话到这里没有 await）：这个会话此刻没有别的生成在用旧上下文。
            # ponytail: 旧上下文只记下来，等下次拉起运行时再删（运行时闲 15 分钟就会收）；一直不闲、攒多了再改成当场删
            mgr.defer_delete(t, sess["ocId"])
            sess["ocId"] = None
            store.save(t, sess)
        try:
            rt = await mgr.acquire(t)
        except Exception as e:           # 任何失败都要把占位拿掉，否则这个会话永远 409
            gens.pop(key, None)
            if not isinstance(e, RuntimeUnavailable):
                log.error("generation error category=internal sub=acquire")
            raise HTTPException(503, "聊天运行时暂时起不来或已满，稍后再试")
        try:
            oc_id = await rt.ensure_session(sess.get("ocId"))
        except Exception:
            gens.pop(key, None)
            mgr.release(rt)
            log.error("generation error category=internal sub=session")
            raise HTTPException(503, "聊天运行时暂时不可用，稍后再试")
        # 上面两次 await 期间会话可能被删了：不能拿旧快照写回去（那会让它复活）
        cur = store.get(t, sid)
        if cur is None:
            gens.pop(key, None)
            if oc_id != sess.get("ocId"):
                with contextlib.suppress(Exception):
                    await rt.delete_session(oc_id)
            mgr.release(rt)
            raise HTTPException(404, "会话不存在")
        if oc_id != cur.get("ocId"):
            cur["ocId"] = oc_id
            store.save(t, cur)
        g.oc_id, g.rt = oc_id, rt
        user_mid, mid, cid = new_id("msg_"), new_id("msg_"), "c_" + secrets.token_hex(8)
        store.add_message(t, sid, user_mid, "user", text)
        if rec:
            rec.begin(t, oc_id, sid, mid, user_mid)

        async def stream():
            finished = False
            try:
                yield _sse("start", {"userMessageId": user_mid, "messageId": mid})
                try:
                    async with rt.events(oc_id) as evs:
                        if g.cancelled_at:        # 运行时还没起来就被取消了：不发 prompt，省一次模型调用
                            g.tr.final = ("done", "cancelled")
                        else:
                            await rt.prompt(oc_id, text)
                        q: asyncio.Queue = asyncio.Queue(maxsize=256)   # 满了就不读上游（背压），不无限攒

                        async def pump():
                            try:
                                async for ev in evs:
                                    await q.put(ev)
                            finally:
                                await q.put(None)

                        pt = asyncio.create_task(pump())
                        deadline = time.monotonic() + s.max_turn_seconds
                        try:
                            while g.tr.final is None:
                                now = time.monotonic()
                                if g.cancelled_at and now - g.cancelled_at > CANCEL_GRACE:
                                    g.tr.final = ("done", "cancelled")      # settle() 会确认或收掉运行时
                                    break
                                if now > deadline:                         # 总时限到了
                                    g.tr.final = ("error", "upstream", "timeout", None)
                                    break
                                if g.tr.overflow and not g.cancelled_at:   # 回答超长：停在这里
                                    await _cancel(g)
                                try:
                                    ev = await asyncio.wait_for(q.get(), min(PING_SECONDS, max(deadline - now, 0.05)))
                                except asyncio.TimeoutError:
                                    if time.monotonic() < deadline:
                                        yield ": ping\n\n"
                                    continue
                                if ev is None:
                                    raise RuntimeError("event stream closed")
                                for typ, data in g.tr.feed(ev):
                                    yield _sse(typ, data)
                        finally:
                            pt.cancel()   # 不 await：这里可能正在被取消（客户端断开），不能吞掉那个取消
                except Exception:
                    if g.tr.final is None:
                        g.tr.final = ("error", "internal", "internal", None)
                fin = g.tr.final
                if fin != ("done", "end"):
                    await settle(abort=True)       # 没正常结束：确认运行时这一轮真停了再放人
                if fin[0] == "done":
                    last = _sse("done", {"messageId": mid, "reason": fin[1]})
                else:
                    _, code, sub, status = fin
                    log.warning("generation error category=%s sub=%s status=%s correlationId=%s",
                                code, sub, status, cid)
                    last = _sse("error", {"code": code, "detail": ERROR_TEXT[sub], "correlationId": cid})
                finished = True
                cleanup()          # 先收尾再发终止事件：客户端一收到就能发下一条，不撞 409
                yield last
            finally:
                if not finished:
                    abandon()

        async def settle(abort: bool):
            """中止并确认（有上限）；确认不了就把这个租户的运行时整个收掉。之后才放开 busy，
            迟到的中止、迟到的事件不会落到下一轮头上。"""
            if abort:
                await rt.abort(oc_id)
            if not await rt.wait_idle(oc_id, CANCEL_GRACE):
                log.warning("generation error category=internal sub=abort_unconfirmed correlationId=%s", cid)
                await mgr.kill(rt)

        def abandon():
            """客户端断开 = 取消（契约第四节）。这里可能在被取消的任务里：另起一个任务中止、确认、收尾，
            确认之前会话一直 busy。"""
            if gens.get(key) is not g or g.abandoned:
                return
            g.abandoned = True
            g.tr.cancelled = True

            async def finish():
                try:
                    await settle(abort=True)
                finally:
                    cleanup()
            asyncio.get_running_loop().create_task(finish())

        def cleanup():
            if gens.get(key) is not g:
                return
            answer = "".join(g.tr.text)
            if answer:
                store.add_message(t, sid, mid, "assistant", answer)
            gens.pop(key, None)
            if rec:
                rec.end(t, oc_id)
            mgr.release(rt)

        return stream(), abandon

    # 后台认窗口（第十节）：配了模型、AGENT_AUTOTRACK 没关才有
    app.state.autotrack = autotrack.Worker(s, store, gens, turn, mgr) if s.configured and s.autotrack else None
    return app


def _sse(typ: str, data: dict) -> str:
    return f"event: {typ}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _explain(errors) -> str:
    parts = []
    for e in errors:
        loc = ".".join(str(x) for x in e.get("loc", ()) if x != "body") or "请求体"
        if e.get("type") == "extra_forbidden":
            parts.append(f"不认识的字段 {loc}")
        else:
            parts.append(f"{loc}：{e.get('msg')}")
    return "；".join(parts) or "请求不合规"
