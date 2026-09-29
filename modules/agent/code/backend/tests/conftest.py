"""测试夹具：把 app 用真 uvicorn 起在线程里（SSE、断连要真连接才测得出），httpx 连它。"""
from __future__ import annotations

import json
import socket
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import config  # noqa: E402
from app.main import create_app  # noqa: E402


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Server:
    def __init__(self, app):
        self.port = free_port()
        self.loop = None
        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=self.port,
                                                    log_level="warning", lifespan="on"))
        def run():
            import asyncio
            self.loop = asyncio.new_event_loop()
            self.loop.run_until_complete(self.server.serve())
        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()
        for _ in range(200):
            if self.server.started:
                break
            time.sleep(0.02)
        self.url = f"http://127.0.0.1:{self.port}"

    def client(self, tenant: str | None = None, **kw) -> httpx.Client:
        h = {"X-Nexus-Tenant": tenant} if tenant else {}
        return httpx.Client(base_url=self.url, headers=h, trust_env=False, timeout=60, **kw)

    def stop(self):
        self.server.should_exit = True
        self.thread.join(15)


def settings(tmp_path, **kw) -> config.Settings:
    base = config.Settings(api_key="sk-test", model="deepseek/deepseek-chat", base_url="",
                           max_sessions=50, max_runtimes=4, data_dir=str(tmp_path), mcp_url="http://mcp:8020/api/mcp/",
                           strict=False, opencode_bin="opencode", idle_seconds=900)
    return replace(base, **kw)


@pytest.fixture
def serve():
    started = []

    def start(app):
        s = Server(app)
        started.append(s)
        return s

    yield start
    for s in started:
        s.stop()


def read_sse(resp, stop_after: str | None = None) -> list[tuple[str, dict]]:
    """解析 SSE（传响应，或同一个 iter_lines() 迭代器以便分两次读）；`: ping` 注释记成 ("ping", {})。"""
    out, typ = [], None
    for line in (resp.iter_lines() if hasattr(resp, "iter_lines") else resp):
        if line.startswith(": ping"):
            out.append(("ping", {}))
        elif line.startswith("event: "):
            typ = line[7:]
        elif line.startswith("data: "):
            out.append((typ, json.loads(line[6:])))
            if typ in ("done", "error") or typ == stop_after:
                break
    return out


__all__ = ["create_app", "settings", "read_sse", "free_port"]
