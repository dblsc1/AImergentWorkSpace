"""驱动适配器公共接口 —— 驱动可插拔（module_docs/contract.md「驱动可插拔」节）。

codex 与 deepseek 是**同一接口**的两个实现：都只产出结构化动作清单流，自己不执行
任何东西；受控工具层（controlled_tools.py + action_router.py）才是唯一执行者。
换驱动不改变这条安全边界一个字节 —— 边界代码完全不知道、也不需要知道当前是哪个驱动。

`CodexDriver`（`codex_driver.py` 里的别名）保留只是向后兼容旧引用名，新代码请直接用
本模块的 `PlannerDriver`。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Iterator

from .streaming import StreamEvent


class PlannerDriver(ABC):
    """驱动接口。实现者产出 StreamEvent 流，末条 action_plan 带结构化动作清单。"""

    @abstractmethod
    def plan(self, schedule: dict[str, Any], goal: str) -> Iterator[StreamEvent]:
        ...
