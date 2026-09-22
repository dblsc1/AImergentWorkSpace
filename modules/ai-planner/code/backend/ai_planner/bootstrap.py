"""组装根 —— uvicorn 能直接吃的入口（本模块此前缺的那一块）。

`create_app(layer, driver)` 要两个参数，uvicorn 工厂模式无参调用会 TypeError——
这正是本文件存在的理由：从 env 建 `Config`（缺失即 die）→ 建 nexus 客户端
（收窄公开面）→ 受控工具层（安全边界）→ 加载 F-GUIDE → 按 `config.driver` 选驱动
（codex/deepseek，驱动可插拔，见 module_docs/contract.md）→ 交出 FastAPI app。

uvicorn 起法（`--factory` 无参工厂，见 `scripts/up.sh`）：

    uvicorn ai_planner.bootstrap:build_app --factory --host <host> --port <port>
"""
from __future__ import annotations

from .codex_driver import CodexExecDriver
from .config import Config, ConfigError
from .controlled_tools import ControlledToolLayer
from .deepseek_driver import DeepSeekDriver
from .driver_base import PlannerDriver
from .nexus_client import NexusHttpClient
from .service import create_app, load_guide


def select_driver(config: Config, guide_text: str) -> PlannerDriver:
    """按 `config.driver` 选驱动 —— codex 与 deepseek 是同一 `PlannerDriver` 接口的
    两个实现（module_docs/contract.md「驱动可插拔」）。安全边界完全不在这一层：
    无论选哪个，装配根后面接的仍是同一个 ControlledToolLayer + route_actions。

    `Config.from_env()` 已经守过 `driver` 只能是白名单里的值，这里的 else 分支是
    防御性兜底（例如有人绕过 from_env 直接构造 Config 传了非法值），不应该被
    正常路径触发。
    """
    if config.driver == "codex":
        return CodexExecDriver(config, guide_text=guide_text)
    if config.driver == "deepseek":
        return DeepSeekDriver(config, guide_text=guide_text)
    raise ConfigError(
        f"未知 AI_PLANNER_DRIVER={config.driver!r}（合法值：codex|deepseek）"
    )


def build_app():  # pragma: no cover -- 装配壳，被装配的每一块各自已单测
    """uvicorn `--factory` 入口，无参。env 缺失/非法 → 对应异常直接冒出，不吞不猜。

    组装顺序固定：Config.from_env（关键配置缺失即 die）→ load_guide（F-GUIDE 缺失即 die）
    → NexusHttpClient（惰性鉴权，构造时不发请求）→ ControlledToolLayer（安全边界）
    → select_driver（按 env 选 codex/deepseek）→ create_app。
    """
    config = Config.from_env()
    guide_text = load_guide(config.guide_path)
    client = NexusHttpClient(config)
    layer = ControlledToolLayer(client)
    driver = select_driver(config, guide_text)
    return create_app(layer, driver)
