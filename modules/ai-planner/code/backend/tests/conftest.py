"""测试夹具 —— 一个记录所有调用的 fake nexus 客户端（间谍）。

关键点：fake 只实现 NexusClientProtocol 的 5 个方法（读3 + create + patch）。
它**没有** delete / events / timer / 任意 URL 方法 —— 若受控层胆敢调用这些，
测试会 AttributeError 当场爆掉，这本身就是 A8 的一层证据。
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

# 让 `import ai_planner` 在不装包的情况下可用
_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from ai_planner.controlled_tools import ControlledToolLayer  # noqa: E402


class SpyNexusClient:
    """记录每次读/写调用，供断言 actor 注入与高风险隔离。"""

    def __init__(self) -> None:
        self.creates: list[tuple[str, dict[str, Any]]] = []
        self.patches: list[tuple[str, str, dict[str, Any]]] = []
        self.reads: list[str] = []

    def get_export(self) -> dict[str, Any]:
        self.reads.append("export")
        return {"zones": [], "projects": [], "tasks": [], "events": []}

    def get_next_actions(self) -> dict[str, Any]:
        self.reads.append("next-actions")
        return {"today": "2026-08-10", "zones": []}

    def get_review(self) -> dict[str, Any]:
        self.reads.append("review")
        return {"today": "2026-08-10", "inboxPendingCount": 0}

    def create(self, type_: str, body: dict[str, Any]) -> dict[str, Any]:
        self.creates.append((type_, body))
        return {"id": f"{type_[:1]}_new", "lastWriter": body.get("actor")}

    def patch(self, type_: str, id_: str, body: dict[str, Any]) -> dict[str, Any]:
        self.patches.append((type_, id_, body))
        return {"id": id_, "lastWriter": body.get("actor")}

    # ---- 故意不存在的方法（纵深防御的反证）----
    # delete / write_event / timer / mongo / request 都没有实现。


@pytest.fixture
def spy() -> SpyNexusClient:
    return SpyNexusClient()


@pytest.fixture
def layer(spy: SpyNexusClient) -> ControlledToolLayer:
    return ControlledToolLayer(spy)
