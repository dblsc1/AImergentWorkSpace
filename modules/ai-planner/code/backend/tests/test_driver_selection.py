"""驱动可插拔（module_docs/contract.md「驱动可插拔」）：codex/deepseek 二选一，由
`AI_PLANNER_DRIVER` 决定；默认 deepseek（codex 的宿主 ChatGPT 登录态当前被吊销）。

覆盖点：
- 默认驱动是 deepseek，且缺 key 时 Config.from_env 直接死（不是等到真调用才炸）。
- codex 驱动不受 deepseek 凭据校验干扰（两者必填项互不影响）。
- 非法驱动名直接死。
- DeepSeek `max_tokens` 太小时直接死（实测断言）。
- `bootstrap.select_driver` 按 `config.driver` 真的装出对应的驱动实例。
"""
from __future__ import annotations

import pytest

from ai_planner import bootstrap
from ai_planner.codex_driver import CodexExecDriver
from ai_planner.config import Config, ConfigError
from ai_planner.deepseek_driver import DeepSeekDriver


def _base_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_PLANNER_NEXUS_BASE", "http://127.0.0.1:19999")
    monkeypatch.setenv("AI_PLANNER_NEXUS_COOKIE", "fake-cookie-for-unit-test-only")


def test_default_driver_is_deepseek(monkeypatch: pytest.MonkeyPatch) -> None:
    _base_env(monkeypatch)
    monkeypatch.delenv("AI_PLANNER_DRIVER", raising=False)
    monkeypatch.setenv("AI_PLANNER_DEEPSEEK_API_KEY", "sk-fake-for-unit-test-only")
    cfg = Config.from_env()
    assert cfg.driver == "deepseek"


def test_deepseek_driver_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _base_env(monkeypatch)
    monkeypatch.delenv("AI_PLANNER_DRIVER", raising=False)
    monkeypatch.delenv("AI_PLANNER_DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(ConfigError, match="AI_PLANNER_DEEPSEEK_API_KEY"):
        Config.from_env()


def test_codex_driver_does_not_require_deepseek_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _base_env(monkeypatch)
    monkeypatch.setenv("AI_PLANNER_DRIVER", "codex")
    monkeypatch.delenv("AI_PLANNER_DEEPSEEK_API_KEY", raising=False)
    cfg = Config.from_env()
    assert cfg.driver == "codex"
    assert cfg.deepseek_api_key is None


def test_unknown_driver_dies(monkeypatch: pytest.MonkeyPatch) -> None:
    _base_env(monkeypatch)
    monkeypatch.setenv("AI_PLANNER_DRIVER", "carrier-pigeon")
    with pytest.raises(ConfigError, match="AI_PLANNER_DRIVER"):
        Config.from_env()


def test_deepseek_max_tokens_floor(monkeypatch: pytest.MonkeyPatch) -> None:
    """实测：max_tokens=20 时正文为空，200 才够 —— 断言 <200 直接拒装配。"""
    _base_env(monkeypatch)
    monkeypatch.setenv("AI_PLANNER_DEEPSEEK_API_KEY", "sk-fake-for-unit-test-only")
    monkeypatch.setenv("AI_PLANNER_DEEPSEEK_MAX_TOKENS", "50")
    with pytest.raises(ConfigError, match="max_tokens|AI_PLANNER_DEEPSEEK_MAX_TOKENS"):
        Config.from_env()


def test_deepseek_max_tokens_default_is_safely_above_floor(monkeypatch: pytest.MonkeyPatch) -> None:
    _base_env(monkeypatch)
    monkeypatch.setenv("AI_PLANNER_DEEPSEEK_API_KEY", "sk-fake-for-unit-test-only")
    monkeypatch.delenv("AI_PLANNER_DEEPSEEK_MAX_TOKENS", raising=False)
    cfg = Config.from_env()
    assert cfg.deepseek_max_tokens >= 200


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
        driver="deepseek",
        deepseek_api_key="sk-fake-for-unit-test-only",
    )
    base.update(overrides)
    return Config(**base)  # type: ignore[arg-type]


def test_select_driver_codex() -> None:
    driver = bootstrap.select_driver(_config(driver="codex"), guide_text="guide")
    assert isinstance(driver, CodexExecDriver)


def test_select_driver_deepseek() -> None:
    driver = bootstrap.select_driver(_config(driver="deepseek"), guide_text="guide")
    assert isinstance(driver, DeepSeekDriver)


def test_select_driver_rejects_unknown_value_defensively() -> None:
    """Config.from_env 已经守过合法值；这里直接绕过它构造非法 Config，验证
    select_driver 自己也有防御性兜底，不会静默选错驱动或崩出无关异常。"""
    with pytest.raises(ConfigError, match="AI_PLANNER_DRIVER"):
        bootstrap.select_driver(_config(driver="carrier-pigeon"), guide_text="guide")
