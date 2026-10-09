"""请求级调用方范围（契约 v2.19「调用方范围与匿名上报」）。

门在认证服务（auth.gate v1.4）；这里是第二道：同一张放行表再拦一遍。范围与匿名标记**只**从网关转来的
``X-Nexus-Scope`` / ``X-Nexus-Anonymous`` 来——网关用认证服务 verify 的结果覆盖客户端自带的同名头
（contracts/gateway.v1 第九节），同 ``tenant.py`` 的租户头。

- 两个头都没有：行为与以前完全一样（人的会话、对内直连的 MCP、老网关后面的设备令牌）。
- ``report``（含匿名）：只有四个上报端点。``read``：再加任何 GET。``write``：全部。**缺省拒绝**。
- 取值不认识 → 403，不猜。匿名标记非空 → 匿名，范围一律当 ``report``（不信它自带的范围头）。
- 既有的「只许人」守卫看的是 ``Authorization: Bearer``，这里不碰它们。
"""

from __future__ import annotations

import json
import re
from contextvars import ContextVar
from typing import NamedTuple

SCOPE_HEADER = b"x-nexus-scope"
ANONYMOUS_HEADER = b"x-nexus-anonymous"
SCOPES = ("report", "read", "write")
#: 上报面：恰好这四个 POST，路径全匹配（结尾多一个 / 也不算）。
REPORT = re.compile(r"/api/core/agents/(?:start|[^/]+/(?:phase|stop|heartbeat))")
_EXEMPT = ("/api/core/health",)


class Caller(NamedTuple):
    scope: str | None  # None = 没有范围头（人的会话 / 对内直连 / 老网关）
    anonymous: bool


_current: ContextVar[Caller] = ContextVar("caller", default=Caller(None, False))


def caller() -> Caller:
    return _current.get()


def allowed(scope: str | None, method: str, path: str) -> bool:
    """放行表（契约「放行」）。"""
    if scope in (None, "write"):
        return True
    if method == "POST" and REPORT.fullmatch(path):
        return True
    return scope == "read" and method == "GET"


class ScopeMiddleware:
    """纯 ASGI 中间件：在请求进入任何路由之前按范围拦一遍，并把调用方放进 contextvar。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope_, receive, send):
        if scope_["type"] == "websocket" and any(
                name.lower() in (SCOPE_HEADER, ANONYMOUS_HEADER) for name, _ in scope_.get("headers") or ()):
            await send({"type": "websocket.close", "code": 1008})  # 没有 ws 端点（有测试盯着）；万一加了，带范围头的先拒
            return
        if scope_["type"] != "http" or scope_["path"] in _EXEMPT:
            await self.app(scope_, receive, send)
            return
        scopes, anonymous = [], False
        for name, value in scope_.get("headers") or ():
            if name.lower() == SCOPE_HEADER:
                scopes.append(value.decode("latin-1"))
            elif name.lower() == ANONYMOUS_HEADER:
                anonymous = anonymous or value != b""
        raw = scopes[0] if scopes else ""
        scope = "report" if anonymous else (raw or None)
        # 同一个头出现两次（网关只会给一个）= 有人在网关之外拼请求：不挑，拒绝
        if len(scopes) > 1 or (scope is not None and scope not in SCOPES):
            await _reject(send, f"X-Nexus-Scope 取值不认识：{raw[:40]!r}")
            return
        if not allowed(scope, scope_["method"], scope_["path"]):
            what = "不带凭据的请求" if anonymous else f"范围为 {scope} 的令牌"
            await _reject(send, f"{what}不能调这个接口——"
                          + ("只能上报代理运行（POST /api/core/agents/...）" if scope == "report"
                             else "只读：只能 GET，或上报代理运行"))
            return
        token = _current.set(Caller(scope, anonymous))
        try:
            await self.app(scope_, receive, send)
        finally:
            _current.reset(token)


async def _reject(send, detail: str) -> None:
    body = json.dumps({"detail": detail}, ensure_ascii=False).encode("utf-8")
    await send({"type": "http.response.start", "status": 403,
                "headers": [(b"content-type", b"application/json"),
                            (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})
