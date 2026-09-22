"""nexus 客户端 —— 全服务里**唯一**能触达 nexus 的组件，持有凭据。

刻意收窄公开面（纵深防御）：
- 只有 planner 的**读端**（export / next-actions / review）与 planner **建/改**（POST/PATCH）。
- **没有 delete 方法** —— 高风险删除 AI 永不执行，只提议。
- **没有 events / timer / 任意 URL** 的方法 —— AI 的整条链路里根本不存在写事件的路径。

真正的白名单边界在 controlled_tools 层；本客户端是它下面的底座，
把公开面也收窄是「就算受控层写错了，客户端也没给它 delete/events 的把手」。
"""
from __future__ import annotations

import os
from typing import Any, Protocol

import httpx

from .config import Config, ConfigError


class NexusClientProtocol(Protocol):
    """受控层依赖的最小接口 —— 测试可注入 fake 实现。"""

    def get_export(self) -> dict[str, Any]: ...
    def get_next_actions(self) -> dict[str, Any]: ...
    def get_review(self) -> dict[str, Any]: ...
    def create(self, type_: str, body: dict[str, Any]) -> dict[str, Any]: ...
    def patch(self, type_: str, id_: str, body: dict[str, Any]) -> dict[str, Any]: ...


class NexusHttpClient:
    """基于 httpx 的真实实现。凭据从 Config 注入，绝不落日志。"""

    def __init__(self, config: Config, *, http: httpx.Client | None = None) -> None:
        self._base = config.nexus_base.rstrip("/")
        self._config = config
        # trust_env=False：本客户端只谈 nexus_base 这一个固定内网地址，不该被宿主
        # shell 里的 HTTP_PROXY/ALL_PROXY 悄悄改道（实证：宿主常配 SOCKS 代理供上网用，
        # httpx 默认信任环境变量，构造期就会因缺 socksio 直接炸——这不是我们要的失败，
        # 也不该让内网调用绕代理出去）。
        self._http = http or httpx.Client(
            base_url=self._base, timeout=30.0, trust_env=False
        )
        self._authed = False

    # ---- 鉴权（口令登录 → 会话 cookie，或预置 cookie）----
    def _ensure_auth(self) -> None:
        if self._authed:
            return
        cfg = self._config
        if cfg.nexus_cookie:
            self._http.cookies.set("cockpit_session", cfg.nexus_cookie)
            self._authed = True
            return
        # 口令在登录这一刻才从 env 取，用完即弃，不长期持有（缩短密钥内存存活期）。
        secret = os.environ.get("AI_PLANNER_NEXUS_PASSWORD")
        if secret:
            resp = self._http.post("/api/auth/login", json={"password": secret})
            if resp.status_code != 204:
                raise ConfigError(
                    f"nexus 登录失败：{resp.status_code}（口令错误或 auth 门未起）"
                )
            self._authed = True
            return
        raise ConfigError(
            "未提供 nexus 凭据（AI_PLANNER_NEXUS_PASSWORD 或 AI_PLANNER_NEXUS_COOKIE）"
        )

    def _get(self, path: str) -> dict[str, Any]:
        self._ensure_auth()
        resp = self._http.get(path)
        resp.raise_for_status()
        return resp.json()

    # ---- 读端（无风险）----
    def get_export(self) -> dict[str, Any]:
        return self._get("/api/core/export")

    def get_next_actions(self) -> dict[str, Any]:
        return self._get("/api/core/views/next-actions")

    def get_review(self) -> dict[str, Any]:
        return self._get("/api/core/views/review")

    # ---- 低风险写（建/改）—— actor 由受控层注入进 body，本层原样透传 ----
    def create(self, type_: str, body: dict[str, Any]) -> dict[str, Any]:
        self._ensure_auth()
        resp = self._http.post(f"/api/core/planner/{type_}", json=body)
        resp.raise_for_status()
        return resp.json()

    def patch(self, type_: str, id_: str, body: dict[str, Any]) -> dict[str, Any]:
        self._ensure_auth()
        resp = self._http.patch(f"/api/core/planner/{type_}/{id_}", json=body)
        resp.raise_for_status()
        return resp.json()
