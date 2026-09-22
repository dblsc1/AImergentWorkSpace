"""`NexusHttpClient` 的构造期健壮性 —— 不该被宿主 shell 的代理环境变量绊倒。

**踩过的坑**（本模块 §6 已知坑同款事故，这里落断言防复活）：宿主常配
`ALL_PROXY=socks5://…` 供上网用；`httpx.Client()` 默认 `trust_env=True`，
构造期就会去枚举环境代理、试图为 socks5 建 mount transport，缺 `socksio`
时**在构造函数里直接 ImportError**——这与目标 nexus_base 是内网固定地址、
根本不该走代理毫无关系，却会让真起服务在「装配 NexusHttpClient」这一步
就诈死。修法：客户端固定 `trust_env=False`（本文件的组装根用途只谈
`config.nexus_base` 一个地址）。
"""
from __future__ import annotations

import pytest

from ai_planner.config import Config
from ai_planner.nexus_client import NexusHttpClient


def _config(**overrides: object) -> Config:
    base = dict(
        bind_host="127.0.0.1",
        bind_port=8700,
        nexus_base="http://127.0.0.1:19999",
        nexus_cookie=None,
        codex_bin="codex",
        codex_model=None,
        codex_timeout_s=120,
        guide_path="contracts/ai-planner-guide-v1.md",
    )
    base.update(overrides)
    return Config(**base)  # type: ignore[arg-type]


def test_http_client_disables_trust_env() -> None:
    """回归断言：底层 httpx.Client 必须 trust_env=False（见本文件顶部坑记录）。"""
    client = NexusHttpClient(_config())
    assert client._http.trust_env is False


def test_construction_survives_socks_proxy_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """复现坑的确切触发条件：ALL_PROXY 指向 socks5 且未装 socksio。

    修复前，本测试会在 NexusHttpClient() 这一行直接 ImportError 炸穿——
    不是断言失败，是构造期崩溃，这正是「真起服务却装配不出客户端」的复现。
    """
    monkeypatch.setenv("ALL_PROXY", "socks5://127.0.0.1:7890")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:7890")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7890")

    # 不该抛任何异常——这就是回归点。
    client = NexusHttpClient(_config())
    assert client._http.trust_env is False
