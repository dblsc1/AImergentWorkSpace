"""运行时配置 —— 关键配置缺失立即 die，禁止弱默认值。

密钥（登录口令 / 会话 cookie）只从 env 读，绝不进代码默认值、日志或仓。
绑定地址不得默认 0.0.0.0。
"""
from __future__ import annotations

import os
from dataclasses import dataclass


class ConfigError(RuntimeError):
    """关键配置缺失/非法 —— 立即失败，不猜。"""


def _require(name: str) -> str:
    val = os.environ.get(name)
    if val is None or val.strip() == "":
        raise ConfigError(
            f"缺少必填环境变量 {name}（禁止弱默认值，缺了就 die，不猜）"
        )
    return val


# 驱动可插拔（module_docs/contract.md「驱动可插拔」节）：合法驱动名唯一事实源。
_ALLOWED_DRIVERS = frozenset({"codex", "deepseek"})

# 实测断言：DeepSeek 是推理模型，max_tokens 是「推理+正文」
# 合计预算，<200 时推理会吃光配额、正文拿不到内容（实测 20 时正文为空，200 才够）。
# 给一个明确下限，别让「配置了却拿不到正文」看起来像别的故障（模型不支持/网络问题）。
_MIN_DEEPSEEK_MAX_TOKENS = 200


@dataclass(frozen=True)
class Config:
    # 对前端出的绑定地址；默认 127.0.0.1（不得默认 0.0.0.0）
    bind_host: str
    bind_port: int
    # nexus 写入口基址（必填，避免默认指向错误的库/host）
    nexus_base: str
    # nexus 会话 cookie（可选，从 env）。口令**不在此长期持有** ——
    # 见 nexus_client._ensure_auth：口令在登录那一刻才从 env 读，用完不留在对象里，
    # 缩短密钥在内存里的存活期，也避免 Config 被 repr/日志时带出口令。
    nexus_cookie: str | None
    # codex 驱动
    codex_bin: str
    codex_model: str | None
    codex_timeout_s: int
    # F-GUIDE 说明书（system prompt 唯一事实）落点，仓根相对或绝对
    guide_path: str
    # ---- 驱动可插拔：codex | deepseek，选谁由 AI_PLANNER_DRIVER 决定 ----
    # 默认 deepseek（而非 codex）：codex 复用的宿主 ChatGPT 登录态当前已被吊销
    # （refresh_token_invalidated，需人类交互式 codex login 才能恢复，不在自动化范围内），
    # DeepSeek 已实调验证可用。default 只是「新装一套环境时的开箱行为」，
    # 任何时候都能显式 AI_PLANNER_DRIVER=codex 切回去（codex login 修复后建议切回，
    # 因为它免 API key、成本模型不同）。
    driver: str = "deepseek"
    # DeepSeek 驱动配置。api_key 不给默认值（密钥永不进代码）；
    # 其余是端点/模型/超时/预算，非密钥，允许合理默认值。
    deepseek_api_key: str | None = None
    deepseek_base: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-v4-flash"
    deepseek_timeout_s: int = 120
    deepseek_max_tokens: int = 2000

    @staticmethod
    def from_env() -> "Config":
        bind = os.environ.get("AI_PLANNER_BIND", "127.0.0.1:8700")
        if bind.strip().startswith("0.0.0.0"):
            raise ConfigError("AI_PLANNER_BIND 不得绑定 0.0.0.0")
        try:
            host, port_s = bind.rsplit(":", 1)
            port = int(port_s)
        except ValueError as exc:
            raise ConfigError(f"AI_PLANNER_BIND 格式应为 host:port，得到 {bind!r}") from exc

        cookie = os.environ.get("AI_PLANNER_NEXUS_COOKIE") or None
        # 鉴权方式（cookie 或口令，真集成时必需）在 nexus_client 发起写请求时才校验/取用；
        # 允许纯单测不带凭据跑。口令不在此读取、不在 Config 持有（见 nexus_cookie 注释）。

        driver = (os.environ.get("AI_PLANNER_DRIVER") or "deepseek").strip().lower()
        if driver not in _ALLOWED_DRIVERS:
            raise ConfigError(
                f"AI_PLANNER_DRIVER 非法：{driver!r}（合法值：{'|'.join(sorted(_ALLOWED_DRIVERS))}）"
            )

        deepseek_api_key = os.environ.get("AI_PLANNER_DEEPSEEK_API_KEY") or None
        if driver == "deepseek" and not deepseek_api_key:
            raise ConfigError(
                "缺少必填环境变量 AI_PLANNER_DEEPSEEK_API_KEY"
                "（AI_PLANNER_DRIVER=deepseek 时必填，禁止弱默认值）"
            )

        deepseek_max_tokens = int(
            os.environ.get("AI_PLANNER_DEEPSEEK_MAX_TOKENS", "2000")
        )
        if deepseek_max_tokens < _MIN_DEEPSEEK_MAX_TOKENS:
            raise ConfigError(
                f"AI_PLANNER_DEEPSEEK_MAX_TOKENS={deepseek_max_tokens} 太小：DeepSeek 是"
                "推理模型，max_tokens 是推理+正文合计预算，实测 <"
                f"{_MIN_DEEPSEEK_MAX_TOKENS} 时推理会吃光配额、正文拿不到内容"
                "（实证：20 时正文为空，200 才够），至少给"
                f" {_MIN_DEEPSEEK_MAX_TOKENS}（默认 2000 留了余量）"
            )

        return Config(
            bind_host=host,
            bind_port=port,
            nexus_base=_require("AI_PLANNER_NEXUS_BASE"),
            nexus_cookie=cookie,
            codex_bin=os.environ.get("AI_PLANNER_CODEX_BIN", "codex"),
            codex_model=os.environ.get("AI_PLANNER_CODEX_MODEL") or None,
            codex_timeout_s=int(os.environ.get("AI_PLANNER_CODEX_TIMEOUT_S", "120")),
            guide_path=os.environ.get(
                "AI_PLANNER_GUIDE_PATH", "contracts/ai-planner-guide-v1.md"
            ),
            driver=driver,
            deepseek_api_key=deepseek_api_key,
            deepseek_base=os.environ.get(
                "AI_PLANNER_DEEPSEEK_BASE", "https://api.deepseek.com"
            ),
            deepseek_model=os.environ.get(
                "AI_PLANNER_DEEPSEEK_MODEL", "deepseek-v4-flash"
            ),
            deepseek_timeout_s=int(
                os.environ.get("AI_PLANNER_DEEPSEEK_TIMEOUT_S", "120")
            ),
            deepseek_max_tokens=deepseek_max_tokens,
        )
