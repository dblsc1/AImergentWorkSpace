"""组装根 `ai_planner.bootstrap:build_app` —— uvicorn `--factory` 能直接吃的入口。

覆盖此前的缺口：`create_app(layer, driver)` 要两个参数，无参工厂调用必然 TypeError。
`build_app()` 补上组装（env → Config → 客户端 → 受控层 + codex 驱动 → app），
本文件验证：① env 缺失/非法时该死的地方真的死（不吞不猜）；② 齐了能装出一个
真 FastAPI app，`/health` 200。
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ai_planner import bootstrap
from ai_planner.config import ConfigError


def test_build_app_dies_without_nexus_base(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AI_PLANNER_NEXUS_BASE", raising=False)
    with pytest.raises(ConfigError):
        bootstrap.build_app()


def test_build_app_dies_on_bind_0000(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_PLANNER_NEXUS_BASE", "http://127.0.0.1:19999")
    monkeypatch.setenv("AI_PLANNER_BIND", "0.0.0.0:8700")
    with pytest.raises(ConfigError):
        bootstrap.build_app()


def test_build_app_dies_without_guide_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("AI_PLANNER_NEXUS_BASE", "http://127.0.0.1:19999")
    monkeypatch.setenv("AI_PLANNER_GUIDE_PATH", str(tmp_path / "missing-guide.md"))
    # 默认驱动 deepseek 需要 key 才能装出 Config；这里只想验证 guide 缺失死在
    # load_guide 那一步（Config.from_env 必须先成功），故给一个假 key。
    monkeypatch.setenv("AI_PLANNER_DEEPSEEK_API_KEY", "sk-fake-for-unit-test-only")
    with pytest.raises(FileNotFoundError):
        bootstrap.build_app()


def test_build_app_assembles_real_app(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    guide = tmp_path / "guide.md"
    guide.write_text("你是 GTD 规划助理（测试用假说明书）。", encoding="utf-8")

    monkeypatch.setenv("AI_PLANNER_NEXUS_BASE", "http://127.0.0.1:19999")
    monkeypatch.setenv("AI_PLANNER_GUIDE_PATH", str(guide))
    # 只为满足「二选一」的组装完整性；构造 NexusHttpClient 不会真发请求（惰性鉴权）。
    monkeypatch.setenv("AI_PLANNER_NEXUS_COOKIE", "fake-cookie-for-unit-test-only")
    # 默认驱动 deepseek 需要 key 才能装出 Config（真调用不会发生，只测装配）。
    monkeypatch.setenv("AI_PLANNER_DEEPSEEK_API_KEY", "sk-fake-for-unit-test-only")

    app = bootstrap.build_app()
    client = TestClient(app)

    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
