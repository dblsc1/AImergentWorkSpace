"""与 nexus-core `agents/*` 端点对话的最小客户端 —— 纯标准库（urllib），
Windows / mac / Linux 通用。

设计取舍：
- 这是唯一知道 HTTP 细节（Bearer 头、超时、端点形状）的地方。auth 与
  nexus-core 两个契约还在并行开发、随时可能微调，所有调用方
  （`cockpit-run`、Claude Code 钩子）都只经这一个模块，改接口只改这一处。
- 每一次网络调用都有总时限（默认 3 秒）并把失败折成 `CockpitError`——
  调用方（尤其是包装真实命令的 `cockpit-run`）绝不能因为 cockpit 不在线、
  慢、或者 token 不对就卡住或者不跑用户的命令。
- `CockpitError` 的消息只会是几个固定分类之一，绝不携带任何原始异常文本：
  token 一旦包含非法字符（比如换行），`http.client` 会把整条 header 值
  （含 token）塞进异常信息里，原样打印等于把 token 印到 stderr/日志。
"""

from __future__ import annotations

import json
import os
import platform
import threading
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_TIMEOUT = 3.0
CONFIG_FILENAME = "agent-hooks.json"


class CockpitError(Exception):
    """cockpit 请求失败，固定分类之一：连不上 / 超时 / HTTP <code> / 配置错误 / 响应格式不对。

    调用方应当当作"跳过计时"处理，不是致命错误；`str(e)` 本身不含任何原始异常
    文本（见模块 docstring），直接打印到 stderr 是安全的。

    `code`：HTTP 状态码，仅 HTTP 类失败时非 None。`json_body`：那次 HTTP 错误
    的响应体是否解析成了 JSON。两者搭配起来才能判断"cockpit 应用层明确说
    这个东西不存在"（`code == 404` 且 `json_body`，nexus-core 的错误体是 JSON）
    还是"根本没打到 cockpit"（比如 URL 配错了，命中 nginx/网关自己的默认 404
    页——那是 HTML）——只看状态码分不出这两种，配错地址一样会给 404。
    """

    def __init__(self, category: str, code: int | None = None, json_body: bool = False):
        super().__init__(category)
        self.code = code
        self.json_body = json_body


# ── 每用户目录（跨平台） ──────────────────────────────────────────
def user_dir(purpose: str = "config") -> Path:
    """每用户的 config / state 目录。

    `HONEYCOMB_AGENT_HOOKS_HOME` 设了就整个短路——主要给测试用，让三个平台的
    测试都落在临时目录里，不会因为跑在真机上就去动开发者/用户自己的
    `~/.config`、`%APPDATA%` 等真实目录。

    没设的话按平台走正常约定：Windows、mac 没有独立的「state」目录约定，
    跟 config 共用同一个根目录（不同用途落不同文件名/子目录即可区分）；
    Linux 遵循 XDG，state 单独放一处，因为它是运行期产生的小文件（钩子的
    runId 记录），不是用户手写的配置。
    """
    override = os.environ.get("HONEYCOMB_AGENT_HOOKS_HOME")
    if override:
        return Path(override) / purpose
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
    """合并出 `{"url", "token", "tasks", "projects"}`。

    `COCKPIT_URL` / `COCKPIT_TOKEN` 环境变量优先于配置文件里的同名字段，
    方便 CI / 容器场景不落文件也能用。`tasks` / `projects` 只能来自配置文件
    （目录 → taskId / projectId 的映射，环境变量不适合表达一份映射表）。

    配置文件形状不对（顶层不是对象、`tasks` / `projects` 不是对象）一律当没配，不崩——
    读一份手改坏了的配置文件不该让所有调用方跟着炸。
    """
    file_cfg: dict[str, Any] = {}
    cfg_path = user_dir("config") / CONFIG_FILENAME
    try:
        raw = json.loads(cfg_path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            file_cfg = raw
    except (FileNotFoundError, ValueError, OSError):
        file_cfg = {}
    url = os.environ.get("COCKPIT_URL") or file_cfg.get("url") or ""
    token = os.environ.get("COCKPIT_TOKEN") or file_cfg.get("token") or ""
    maps = {key: file_cfg[key] if isinstance(file_cfg.get(key), dict) else {} for key in ("tasks", "projects")}
    return {"url": str(url).rstrip("/"), "token": str(token), **maps}


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
    return _longest_prefix(cfg.get("tasks"), cwd or os.getcwd())


def _longest_prefix(mapping: Any, cwd: str) -> str | None:
    """`{目录: id}` 里包含 `cwd` 的那些目录中最长的一条；值不是非空字符串的条目不算。"""
    best: str | None = None
    best_len = -1
    for dir_path, target in (mapping if isinstance(mapping, dict) else {}).items():
        if isinstance(target, str) and target and _is_under(cwd, dir_path) and len(os.path.normpath(dir_path)) > best_len:
            best, best_len = target, len(os.path.normpath(dir_path))
    return best


def resolve_target(
    task: str | None = None, project: str | None = None, cwd: str | None = None, config: dict[str, Any] | None = None,
) -> tuple[str | None, str | None]:
    """`(taskId, projectId)`，至多一个非空（先到先得）：

    1–3. 任务：显式 `task` > `COCKPIT_TASK` > 配置文件 `tasks` 映射（同 `resolve_task`）
    4. 显式 `project`（`cockpit-run --project`）
    5. `COCKPIT_PROJECT` 环境变量
    6. 配置文件 `projects` 映射里，当前目录最长匹配的那条目录前缀
    7. 都没有 → `(None, None)`（收件箱）

    定得出任务就不带项目：任务本身就在某个项目里，服务端自己取。
    """
    cfg = config if config is not None else load_config()
    cwd = cwd or os.getcwd()
    task_id = resolve_task(task, cwd, cfg)
    if task_id:
        return task_id, None
    return None, project or os.environ.get("COCKPIT_PROJECT") or _longest_prefix(cfg.get("projects"), cwd)


def default_agent_name(cwd: str | None = None) -> str:
    """默认 agent 显示名 = 项目目录名。"""
    cwd = cwd or os.getcwd()
    return os.path.basename(os.path.normpath(cwd)) or "agent"


# ── HTTP ───────────────────────────────────────────────────────────
# cockpit 通常是本机/局域网地址（COCKPIT_URL 默认 http://127.0.0.1:8800/）。
# 不走系统代理：用户环境里的 HTTP_PROXY/HTTPS_PROXY（公司代理之类）常常连不到
# 本机地址，硬走代理只会把「~3 秒超时」拖成「代理那边先超时/连不上」，
# 违反"绝不能拖累调用方"这条纪律。空 ProxyHandler = 忽略所有代理设置。
#
# 不跟重定向：默认 opener 会跟 3xx 并把请求（含 Authorization 头）原样发到
# `Location` 指向的新地址——那可能是另一个源。cockpit 正常协议里没有重定向，
# 出现 3xx 只可能是配置错误或者更糟的情况，一律当错误处理，不跟。
#
# 不能只重写 redirect_request() 让它返回 None——那是"我不重定向，看有没有
# 别的 handler 接手"的信号，最终会不会变成 HTTPError 取决于 opener 里还挂着
# 哪些 handler（碰巧 build_opener() 默认会挂一个 HTTPDefaultErrorHandler 兜底，
# 但这是一条隐式链路，不值得依赖）。直接重写 http_error_30x，自己抛
# HTTPError，行为不再依赖任何"没人接手就摔给默认 handler"的隐式约定。
class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def http_error_301(self, req, fp, code, msg, headers):
        raise urllib.error.HTTPError(req.full_url, code, msg, headers, fp)

    http_error_302 = http_error_303 = http_error_307 = http_error_308 = http_error_301


_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())


class _DeadlineExceeded(Exception):
    """内部哨兵：后台线程在总时限内没跑完，调用方已经放弃等它了。"""


def _run_with_deadline(fn, timeout: float):
    """在 daemon 线程里跑 `fn()`，最多等 `timeout` 秒就是总时限。

    `urllib` 的 `timeout=` 参数只管单次 socket 操作（一次 connect、一次
    recv），不是总时限：DNS 慢、服务器一个字节一个字节地吐 body（每次
    recv 都在超时内完成，攒起来却远超预期），都能在 `timeout=` 之下拖出
    一次总耗时几十秒的调用。这里用线程 + `join(timeout)` 兜底：到点了就
    不再等——线程可能还在后台跑，但它是 daemon，不会拖住调用方或整个
    进程退出；调用方拿到 `_DeadlineExceeded` 后直接往下走（比如去
    `Popen` 真正的命令），不会因为一次挂起的网络调用被卡住。
    """
    box: dict[str, Any] = {}

    def _target():
        try:
            box["value"] = fn()
        except BaseException as e:  # noqa: BLE001 — 原样转交给等它的那一侧分类，这里不判断
            box["error"] = e

    t = threading.Thread(target=_target, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        raise _DeadlineExceeded()
    if "error" in box:
        raise box["error"]
    return box.get("value")


def _request(config: dict[str, Any], method: str, path: str, payload: dict[str, Any] | None, timeout: float) -> dict[str, Any]:
    url = config.get("url")
    if not url:
        raise CockpitError("配置错误")
    try:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
    except (TypeError, ValueError):
        raise CockpitError("配置错误") from None
    headers = {"Authorization": f"Bearer {config.get('token', '')}"}
    if data is not None:
        headers["Content-Type"] = "application/json"

    def _do() -> dict[str, Any]:
        # Request() 本身也可能因为非法 URL / 非法 header 值（比如 token 带换行）
        # 抛异常——连同下面的 open()/read() 一起放进同一个 try，让外层统一分类。
        req = urllib.request.Request(url + path, data=data, method=method, headers=headers)
        with _opener.open(req, timeout=timeout) as resp:
            body = resp.read()
        if not body:
            return {}
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            raise CockpitError("响应格式不对") from None
        if not isinstance(parsed, dict):
            raise CockpitError("响应格式不对")
        return parsed

    try:
        return _run_with_deadline(_do, timeout)
    except CockpitError:
        raise
    except _DeadlineExceeded:
        raise CockpitError("超时") from None
    except urllib.error.HTTPError as e:
        body = b""
        try:
            body = e.read()
        except Exception:
            pass
        json_body = False
        if body:
            try:
                json.loads(body)
                json_body = True
            except ValueError:  # 包括 JSONDecodeError 和坏编码的 UnicodeDecodeError
                json_body = False
        try:
            e.close()  # HTTPError 包着底层响应/连接，raise 出来之后没人再帮它关，自己关掉
        except Exception:
            pass
        raise CockpitError(f"HTTP {e.code}", code=e.code, json_body=json_body) from None
    except urllib.error.URLError as e:
        if isinstance(e.reason, TimeoutError):
            raise CockpitError("超时") from None
        raise CockpitError("连不上") from None
    except TimeoutError:
        raise CockpitError("超时") from None
    except OSError:
        raise CockpitError("连不上") from None
    except Exception:  # noqa: BLE001 — 兜底分类，绝不透出原始异常文本（可能含 token）
        raise CockpitError("配置错误") from None


def start_run(
    config: dict[str, Any],
    task_id: str | None,
    agent: str,
    tool: str,
    model: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    *,
    phase: str | None = None,
    label: str | None = None,
    match: str | None = None,
    client_key: str | None = None,
    project_id: str | None = None,
) -> dict[str, Any]:
    """`POST /api/core/agents/start` → `{runId, startedAt}`。taskId 缺省 = 收件箱。

    `project_id`（nexus-core v2.13）：没有任务时只挂项目；有任务就不发（任务定项目）。

    v2.4 选填：`phase`（开跑时的相位）、`label`/`match`（目录名这一级，见 `lane_names`）、
    `client_key`（不透明哈希：同 key 的运行还在跑时服务端回原运行，重试不多开一条泳道）。没给的键不发。"""
    payload: dict[str, Any] = {"agent": agent, "tool": tool}
    if task_id:
        payload["taskId"] = task_id
    elif project_id:
        payload["projectId"] = project_id
    if model:
        payload["model"] = model
    for key, value in (("phase", phase), ("label", label), ("match", match), ("clientKey", client_key)):
        if value:
            payload[key] = value
    return _request(config, "POST", "/api/core/agents/start", payload, timeout)


PHASES = ("working", "waiting_input", "waiting_permission", "idle", "error")


def now_iso() -> str:
    """相位的 `at`：本机时钟、带本地偏移。**在事件发生时取**，不是发请求时补（服务端按 at 排序）。"""
    return datetime.now(timezone.utc).astimezone().isoformat()


def lane_names(cwd: str | None = None, title: str | None = None) -> tuple[str, str | None]:
    """`(label, match)`：缺省都是工作目录名（不是完整路径）；`title`（会话的名字，见 `session_title`）
    给了就用它。label 截到 64 码点，match 截到 128、不足 3 个字符不带（契约：match 3–128 码点）。"""
    name = title or default_agent_name(cwd)
    return name[:64], (name[:128] if len(name) >= 3 else None)


TITLE_TAIL_BYTES = 256 * 1024


def session_title(transcript_path: Any) -> str | None:
    """Claude Code 会话的名字：transcript（JSONL）里**最后一条** `{"type":"custom-title","customTitle":…}`
    （用户 `/rename` 起的，终端标签页显示的就是它）。没有 / 读不了 → None，调用方退回目录名。

    只读文件末尾 `TITLE_TAIL_BYTES`、从后往前找：transcript 动辄几十 MB，钩子每个事件都跑；Claude Code 会把
    标题记录在文件里反复重写，末尾找得到。截断的第一行、坏 JSON、别的记录一律跳过。不读 `ai-title`
    （自动标题：改过名的标签页不显示它）。**只取这一个字段**，对话内容不读、不上报。
    """
    if not isinstance(transcript_path, str) or not transcript_path:
        return None
    try:
        with open(transcript_path, "rb") as f:
            size = f.seek(0, os.SEEK_END)
            start = max(size - TITLE_TAIL_BYTES, 0)
            f.seek(start)
            lines = f.read(TITLE_TAIL_BYTES).split(b"\n")
    except OSError:
        return None
    if start:
        lines = lines[1:]  # 从行中间切进来的那半行
    for line in reversed(lines):
        if b"custom-title" not in line:
            continue
        try:
            record = json.loads(line)
        except ValueError:  # 含坏编码的 UnicodeDecodeError
            continue
        if isinstance(record, dict) and record.get("type") == "custom-title":
            title = record.get("customTitle")
            title = " ".join(title.split()) if isinstance(title, str) else ""
            return title or None  # 最后一条为准：清空了名字 = 没有名字
    return None


def phase_run(
    config: dict[str, Any],
    run_id: str,
    phase: str,
    at: str,
    detail: str | None = None,
    reply: bool = False,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """`POST /api/core/agents/{runId}/phase`（v2.4）。`detail` 只放短标签（工具名、通知种类、错误种类），
    截到 64 码点；`reply` 只在 true 时发。"""
    payload: dict[str, Any] = {"phase": phase, "at": at}
    if detail:
        payload["detail"] = detail[:64]
    if reply:
        payload["reply"] = True
    return _request(config, "POST", f"/api/core/agents/{run_id}/phase", payload, timeout)


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
