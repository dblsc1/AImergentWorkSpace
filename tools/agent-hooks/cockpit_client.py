"""与 nexus-core `agents/*` 端点对话的最小客户端 —— 纯标准库（urllib），
Windows / mac / Linux 通用。

设计取舍：
- 这是唯一知道 HTTP 细节（Bearer 头、超时、端点形状）的地方。auth 与
  nexus-core 两个契约还在并行开发、随时可能微调，所有调用方
  （`cockpit-run`、Claude Code 钩子）都只经这一个模块，改接口只改这一处。
- 每一次网络调用都设短超时（默认 3 秒）并把失败折成 `CockpitError`——
  调用方（尤其是包装真实命令的 `cockpit-run`）绝不能因为 cockpit 不在线
  或 token 不对就卡住或者不跑用户的命令。
"""

from __future__ import annotations

import json
import os
import platform
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

DEFAULT_TIMEOUT = 3.0
CONFIG_FILENAME = "agent-hooks.json"


class CockpitError(Exception):
    """cockpit 不可达 / 拒绝请求。调用方应当当作"跳过计时"处理，不是致命错误。"""


# ── 每用户目录（跨平台） ──────────────────────────────────────────
def user_dir(purpose: str = "config") -> Path:
    """每用户的 config / state 目录。

    Windows、mac 没有独立的「state」目录约定，跟 config 共用同一个根目录
    （不同用途落不同文件名即可区分）；Linux 遵循 XDG，state 单独放一处，
    因为它是运行期产生的小文件（钩子的 runId 记录），不是用户手写的配置。
    """
    system = platform.system()
    if system == "Windows":
        root = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(root) / "honeycomb"
    if system == "Darwin":
        return Path.home() / "Library" / "Application Support" / "honeycomb"
    if purpose == "state":
        root = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    else:
        root = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(root) / "honeycomb"


# ── 配置：环境变量优先，其次配置文件 ──────────────────────────────
def load_config() -> dict[str, Any]:
    """合并出 `{"url", "token", "tasks"}`。

    `COCKPIT_URL` / `COCKPIT_TOKEN` 环境变量优先于配置文件里的同名字段，
    方便 CI / 容器场景不落文件也能用。`tasks` 只能来自配置文件
    （目录 → taskId 的映射，环境变量不适合表达一份映射表）。
    """
    file_cfg: dict[str, Any] = {}
    cfg_path = user_dir("config") / CONFIG_FILENAME
    try:
        file_cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError, OSError):
        file_cfg = {}
    url = os.environ.get("COCKPIT_URL") or file_cfg.get("url") or ""
    token = os.environ.get("COCKPIT_TOKEN") or file_cfg.get("token") or ""
    tasks = file_cfg.get("tasks") or {}
    return {"url": str(url).rstrip("/"), "token": str(token), "tasks": tasks}


def _is_under(path: str, base: str) -> bool:
    path = os.path.normpath(path)
    base = os.path.normpath(base)
    return path == base or path.startswith(base + os.sep)


def resolve_task(explicit: str | None = None, cwd: str | None = None, config: dict[str, Any] | None = None) -> str | None:
    """任务解析顺序（先到先得）：

    1. `explicit`（比如 `cockpit-run --task` 显式传入的值）
    2. `COCKPIT_TASK` 环境变量
    3. 配置文件 `tasks` 映射里，当前目录最长匹配的那条目录前缀
    4. 都没有 → `None`（收件箱）
    """
    if explicit:
        return explicit
    env_task = os.environ.get("COCKPIT_TASK")
    if env_task:
        return env_task
    cfg = config if config is not None else load_config()
    cwd = cwd or os.getcwd()
    best_task: str | None = None
    best_len = -1
    for dir_path, task_id in (cfg.get("tasks") or {}).items():
        if _is_under(cwd, dir_path) and len(os.path.normpath(dir_path)) > best_len:
            best_task = task_id
            best_len = len(os.path.normpath(dir_path))
    return best_task


def default_agent_name(cwd: str | None = None) -> str:
    """默认 agent 显示名 = 项目目录名。"""
    cwd = cwd or os.getcwd()
    return os.path.basename(os.path.normpath(cwd)) or "agent"


# ── HTTP ───────────────────────────────────────────────────────────
# cockpit 通常是本机/局域网地址（COCKPIT_URL 默认 http://127.0.0.1:8800/）。
# 不走系统代理：用户环境里的 HTTP_PROXY/HTTPS_PROXY（公司代理之类）常常连不到
# 本机地址，硬走代理只会把「~3 秒超时」拖成「代理那边先超时/连不上」，
# 违反"绝不能拖累调用方"这条纪律。空 ProxyHandler = 忽略所有代理设置。
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _request(config: dict[str, Any], method: str, path: str, payload: dict[str, Any] | None, timeout: float) -> dict[str, Any]:
    url = config.get("url")
    if not url:
        raise CockpitError("未配置 COCKPIT_URL，跳过")
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Authorization": f"Bearer {config.get('token', '')}"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url + path, data=data, method=method, headers=headers)
    try:
        with _opener.open(req, timeout=timeout) as resp:
            body = resp.read()
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        raise CockpitError(f"{method} {path} → HTTP {e.code}") from e
    except Exception as e:  # noqa: BLE001 — 这一层是"绝不能拖累调用方"的边界，见模块docstring
        raise CockpitError(f"{method} {path} → {e}") from e


def start_run(
    config: dict[str, Any],
    task_id: str | None,
    agent: str,
    tool: str,
    model: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """`POST /api/core/agents/start` → `{runId, startedAt}`。taskId 缺省 = 收件箱。"""
    payload: dict[str, Any] = {"agent": agent, "tool": tool}
    if task_id:
        payload["taskId"] = task_id
    if model:
        payload["model"] = model
    return _request(config, "POST", "/api/core/agents/start", payload, timeout)


def stop_run(
    config: dict[str, Any],
    run_id: str,
    outcome: str,
    output: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """`POST /api/core/agents/{runId}/stop`，写一条 `agent.run.completed`。"""
    payload: dict[str, Any] = {"outcome": outcome}
    if output is not None:
        payload["output"] = output
    return _request(config, "POST", f"/api/core/agents/{run_id}/stop", payload, timeout)
