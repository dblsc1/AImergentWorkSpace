"""`scripts/up.sh` 的前置校验 —— 关键路径缺文件/缺凭据必须 die，
不许静默跳过后报成功。

用子进程真跑脚本（不 mock），断言：非零退出 + 报错信息点名缺的是什么。
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
_UP_SH = _BACKEND / "scripts" / "up.sh"


def _run(env_overrides: dict[str, str]) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("AI_PLANNER_")}
    env.update(env_overrides)
    return subprocess.run(
        ["bash", str(_UP_SH)],
        cwd=_BACKEND,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )


def test_up_sh_exists_and_executable() -> None:
    assert _UP_SH.exists(), "up.sh 缺文件"
    assert os.access(_UP_SH, os.X_OK), "up.sh 缺可执行位"


def test_up_sh_dies_without_nexus_base() -> None:
    result = _run({})
    assert result.returncode != 0
    assert "AI_PLANNER_NEXUS_BASE" in result.stderr


def test_up_sh_dies_without_credential() -> None:
    result = _run({"AI_PLANNER_NEXUS_BASE": "http://127.0.0.1:19999"})
    assert result.returncode != 0
    assert "凭据" in result.stderr


def test_up_sh_dies_on_bind_0000() -> None:
    result = _run(
        {
            "AI_PLANNER_NEXUS_BASE": "http://127.0.0.1:19999",
            "AI_PLANNER_NEXUS_COOKIE": "fake-cookie-for-test",
            "AI_PLANNER_BIND": "0.0.0.0:8700",
        }
    )
    assert result.returncode != 0
    assert "0.0.0.0" in result.stderr


def test_up_sh_dies_on_missing_guide() -> None:
    result = _run(
        {
            "AI_PLANNER_NEXUS_BASE": "http://127.0.0.1:19999",
            "AI_PLANNER_NEXUS_COOKIE": "fake-cookie-for-test",
            "AI_PLANNER_GUIDE_PATH": "/nonexistent/does-not-exist-guide.md",
        }
    )
    assert result.returncode != 0
    assert "F-GUIDE" in result.stderr


def test_up_sh_dies_on_missing_codex_bin() -> None:
    """AI_PLANNER_DRIVER=codex 时缺 codex 可执行必须死（驱动可插拔后，这条校验只在
    选中 codex 驱动时才生效 —— 显式指定驱动，别依赖默认值，见下面 default-driver 测试）。"""
    result = _run(
        {
            "AI_PLANNER_NEXUS_BASE": "http://127.0.0.1:19999",
            "AI_PLANNER_NEXUS_COOKIE": "fake-cookie-for-test",
            "AI_PLANNER_DRIVER": "codex",
            "AI_PLANNER_CODEX_BIN": "definitely-not-a-real-binary-xyz",
        }
    )
    assert result.returncode != 0
    assert "codex" in result.stderr


def test_up_sh_dies_without_deepseek_key_on_default_driver() -> None:
    """驱动可插拔（module_docs/contract.md）：默认驱动是 deepseek（codex ChatGPT 登录态
    当前被吊销），缺 AI_PLANNER_DEEPSEEK_API_KEY 时必须死，不许静默跳过后报成功。"""
    result = _run(
        {
            "AI_PLANNER_NEXUS_BASE": "http://127.0.0.1:19999",
            "AI_PLANNER_NEXUS_COOKIE": "fake-cookie-for-test",
        }
    )
    assert result.returncode != 0
    assert "AI_PLANNER_DEEPSEEK_API_KEY" in result.stderr


def test_up_sh_ok_without_deepseek_key_when_driver_is_codex() -> None:
    """codex 路径不该被 deepseek 的凭据校验绊住 —— 两个驱动的必填项互不干扰。
    （codex_bin 也缺时会在 codex 分支死，这里只验证不会先在 deepseek 分支死。）"""
    result = _run(
        {
            "AI_PLANNER_NEXUS_BASE": "http://127.0.0.1:19999",
            "AI_PLANNER_NEXUS_COOKIE": "fake-cookie-for-test",
            "AI_PLANNER_DRIVER": "codex",
            "AI_PLANNER_CODEX_BIN": "definitely-not-a-real-binary-xyz",
        }
    )
    assert "AI_PLANNER_DEEPSEEK_API_KEY" not in result.stderr


def test_up_sh_dies_on_unknown_driver() -> None:
    result = _run(
        {
            "AI_PLANNER_NEXUS_BASE": "http://127.0.0.1:19999",
            "AI_PLANNER_NEXUS_COOKIE": "fake-cookie-for-test",
            "AI_PLANNER_DRIVER": "carrier-pigeon",
        }
    )
    assert result.returncode != 0
    assert "AI_PLANNER_DRIVER" in result.stderr
