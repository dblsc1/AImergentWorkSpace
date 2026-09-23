"""请求级租户（契约 v2.0「按租户分数据」）。

租户 = 这份数据属于谁。它**只**从网关转来的 ``X-Nexus-Tenant`` 头来——网关用认证
服务 verify 的结果覆盖客户端自带的同名头（contracts/gateway.v1 第五节），所以
nexus-core 看到的租户只可能来自认证服务。

- 缺头：单人部署的常态（占位认证服务不给租户）→ ``u_local``，行为与数据都和以前一样。
  严格模式（``NEXUS_TENANT_STRICT=1``）下缺头 → 401：多用户部署里认证服务漏带这个头，
  必须响亮失败，不能静默落到 ``u_local``、让所有人共用一份数据。
- 头不合格式 → 400，点名取值。不清洗、不截断：截断后的 id 可能正好是别人的。
- ``/api/core/health`` 不看租户：它是容器健康检查，直连不经网关。

租户放在 contextvar 里，各子边界的 ``repo.py`` 用 ``scope()`` 过滤，不必把
``user`` 参数穿过每一层函数签名。离线脚本（种子、迁移）不经 HTTP，取默认值 ``u_local``。
"""

from __future__ import annotations

import json
import re
from contextvars import ContextVar

from . import config
from .config import LOCAL_USER

HEADER = "x-nexus-tenant"
#: 租户 id 格式（contracts/gateway.v1 冻结）。
PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
_EXEMPT = ("/api/core/health",)

_current: ContextVar[str] = ContextVar("tenant", default=LOCAL_USER)


def current() -> str:
    return _current.get()


def scope() -> dict:
    """当前租户的查询条件。

    ``u_local`` 同时认**没有 user 字段**的老文档（Mongo 里 ``null`` 匹配缺字段）：
    数据迁移是手动跑的、而且不在镜像里，升级后不跑迁移，单人部署的数据也必须照常可见
    （migrations/migrate.py 纪律 1：读老数据要宽容）。别的租户只认自己的。
    """
    tenant = current()
    if tenant == LOCAL_USER:
        return {"user": {"$in": [LOCAL_USER, None]}}
    return {"user": tenant}


def stamp(doc: dict) -> dict:
    """写入前盖上当前租户。"""
    return {**doc, "user": current()}


class TenantMiddleware:
    """纯 ASGI 中间件：在请求进入任何路由之前定下租户。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope_, receive, send):
        if scope_["type"] != "http" or scope_["path"] in _EXEMPT:
            await self.app(scope_, receive, send)
            return
        raw = None
        for name, value in scope_.get("headers") or ():
            if name.decode("latin-1").lower() == HEADER:
                raw = value.decode("latin-1")
                break
        if raw is None or raw == "":
            if config.settings.tenant_strict:  # 每次请求读：同 guard.py 读 actor_strict 的口径
                await _reject(send, 401, "缺少租户头 X-Nexus-Tenant——严格模式（NEXUS_TENANT_STRICT=1）"
                              "下不落到单人默认租户。检查认证服务的 verify 是否带了这个头")
                return
            tenant = LOCAL_USER
        elif not PATTERN.fullmatch(raw):
            await _reject(send, 400, f"X-Nexus-Tenant 格式非法：{raw[:80]!r}"
                          f"（须匹配 {PATTERN.pattern}）")
            return
        else:
            tenant = raw
        token = _current.set(tenant)
        try:
            await self.app(scope_, receive, send)
        finally:
            _current.reset(token)


async def _reject(send, status: int, detail: str) -> None:
    body = json.dumps({"detail": detail}, ensure_ascii=False).encode("utf-8")
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"),
                            (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})
