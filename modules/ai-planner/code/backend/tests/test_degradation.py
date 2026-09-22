"""降级路径（F-AI-6）：codex 不可用 → 明确提示，不拖垮主栈；主流程不抛异常。"""
from __future__ import annotations

from ai_planner.codex_driver import CodexExecDriver, StubDriver
from ai_planner.config import Config
from ai_planner.controlled_tools import ControlledToolLayer
from ai_planner.service import run_planning
from ai_planner.streaming import StreamEvent


def _cfg(codex_bin: str) -> Config:
    return Config(
        bind_host="127.0.0.1", bind_port=8700,
        nexus_base="http://127.0.0.1:8000",
        nexus_cookie=None,
        codex_bin=codex_bin, codex_model=None, codex_timeout_s=5,
        guide_path="contracts/ai-planner-guide-v1.md",
    )


def test_missing_codex_binary_degrades_not_crashes():
    driver = CodexExecDriver(_cfg("codex-does-not-exist-xyz"), guide_text="guide")
    events = list(driver.plan({"tasks": []}, "规划"))
    assert any(e.type == "degraded" for e in events)
    assert all(isinstance(e, StreamEvent) for e in events)
    # 没有 action_plan（降级时不产动作）
    assert not any(e.type == "action_plan" for e in events)


def test_run_planning_survives_degraded_driver(layer: ControlledToolLayer):
    driver = CodexExecDriver(_cfg("codex-does-not-exist-xyz"), guide_text="guide")
    events = list(run_planning(layer, driver, "帮我规划"))
    types = [e.type for e in events]
    assert "degraded" in types
    assert types[-1] == "done"  # 一定收尾，不半途崩


def test_run_planning_survives_nexus_read_failure():
    class BoomClient:
        def get_export(self):
            raise ConnectionError("nexus down")
        # 其余方法不会被调用

    layer = ControlledToolLayer(BoomClient())
    events = list(run_planning(layer, StubDriver([]), "规划"))
    types = [e.type for e in events]
    assert types[0] == "degraded"
    assert types[-1] == "done"
