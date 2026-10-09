#!/usr/bin/env python3
"""Claude Code 会话钩子：SessionStart 开一条 cockpit run，SessionEnd 关掉它；
v0.3 起中间的事件报**相位**（在干活 / 等你 / 空闲 / 出错，映射表见 README「相位」节）。

在 settings.json 里，所有事件都指向**同一个**脚本——用 stdin JSON 里的
`hook_event_name` 分流，省得配多份路径。见 README 里的配置片段。
相位钩子配成 `"async": true`（绝不让 Claude 等它）；SessionStart 保持同步（先存好 runId）。
**不读** `prompt`、通知的 `message`、工具参数：只报相位、时刻、短标签。
v0.4：泳道名用会话的名字——从 `transcript_path` 末尾只取 `custom-title` 记录的标题（`cc.session_title`），对话内容不读。
v0.4 心跳：钩子只在有事件时才跑，会话空着或 Claude Code 崩了就没人说话。所以每个会话带一个发心跳的小进程
（本文件 `--beat`，见文件末「心跳」）：按服务端给的间隔报「还活着」，发现 Claude Code 没了就替它报 stop。
同一个循环两种起法，**静态配置、没有协商**：缺省是钩子起的脱离伴随进程（companion，装成插件时也是）；
只有用户显式设了 `beat=monitor`（`COCKPIT_BEAT` / 配置文件）才由插件的 monitor 发，那时钩子不起伴随进程。
**锁或者什么都不做**：每个改状态的操作（开 run 并落状态、写相位、改名 / 重开、收尾删状态）都要先拿会话锁；
拿不到就不写状态、也不发依赖它的改状态请求（SessionStart 拿不到锁连 `/start` 都不发）。
**一个总预算**：每次钩子调用一个单调时钟的截止时刻（`HOOK_BUDGET`，3 秒），拿锁等待、`ps`、HTTP 都从里面扣，用完就跳过剩下的步骤、照常 exit 0。

**纪律**（钩子绝不能拖慢或打断会话）：
- Claude Code 文档：`SessionEnd` 钩子默认预算只有 1.5 秒（`SessionStart` 是
  600 秒）。本模块自己有 3 秒总预算（`HOOK_BUDGET`，单次请求 `HOOK_TIMEOUT` 1 秒），超过 1.5 秒——**settings.json 里
  要给 SessionEnd 那条显式设 `"timeout": 5`**（见 README）留够冗余，
  不然偶尔会在我们的超时生效前，Claude Code 自己先把钩子进程杀了。
- 任何异常都吞掉，最后一律 `exit 0`；失败最多在 stderr 留一行——用户能
  看到，Claude 看不到（`SessionStart`/`SessionEnd` 都不能 block 会话，
  这行为在文档里也是这么写的）。配置文件被手改坏、cockpit 返回奇怪的
  形状，都只是"这次不计时"，不是"钩子出错"。
"""

from __future__ import annotations

import time

_T0 = time.monotonic()  # 进程里的第一件事（先于其它 import）：解释器 / 导入的耗时也算进钩子的 3 秒总预算

import argparse
import contextlib
import hashlib
import json
import os
import secrets
import stat
import re
import subprocess
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import cockpit_client as cc  # noqa: E402

HOOK_TIMEOUT = 1.0  # 单次请求的上限（再被总预算裁短）
HOOK_MIN_WORK = 1.5  # 启动之后保证的最短干活时间（秒）：进程启动慢（CPU 被抢）时，预算也至少留够一次拿锁 + 一次请求
HOOK_BUDGET = 3.0  # 一次钩子调用的总预算（秒）：settings.json 里 SessionEnd 要给 `"timeout": 5`（见 README）
_deadline: float | None = None  # 本次钩子调用的截止时刻（`time.monotonic()`），只由 `main()` 设；库式调用 / 心跳进程为 None = 不设总预算


def _remaining(limit: float) -> float:
    """一个步骤能用多久：`min(它自己的上限, 总预算里还剩的)`；没设总预算就是它自己的上限。"""
    return limit if _deadline is None else max(0.0, min(limit, _deadline - time.monotonic()))


def _http_timeout() -> float:
    """钩子里一次请求的时限；总预算用完了 → 按「超时」处理（调用方本来就把它当「这次不计时」跳过）。"""
    t = _remaining(HOOK_TIMEOUT)
    if t < 0.05:
        raise cc.CockpitError("超时")
    return t

# SessionEnd 的 `reason` 说的是「会话为什么结束」，不是「这次工作成没成」——
# Claude Code 不提供成败信号，缺省当正常完成（done）；只有 prompt_input_exit
# （在输入框按 Ctrl-C/Ctrl-D 主动退出）算用户中途打断。
_REASON_TO_OUTCOME = {"prompt_input_exit": "cancelled"}


def _printable(value, limit: int = 64) -> str:
    """外部来源的字符串（文件内容、进程名、地址）打印前先过一遍：保守字符集之外一律换成 `?`，截到 `limit`。
    doctor 的输出可能被 `!` 命令带进模型上下文，不许夹转义序列 / 换行。"""
    return re.sub(r"[^A-Za-z0-9_.:@/-]", "?", str(value))[:limit]


def _safe(value, limit: int = 200) -> str:
    """路径 / 环境来的片段**只用于输出**：控制字符、换行、转义序列、零宽 / 双向控制（`isprintable()` 为假的）换成 `?`，截到 `limit`。
    文件系统调用仍用原始路径；中文 / 常见路径字符保留（不像 `_printable` 那么保守）。"""
    return "".join(c if c.isprintable() else "?" for c in str(value))[:limit]


def _warn(msg: str) -> None:
    print(f"agent-hooks: {_safe(msg, 400)}", file=sys.stderr)  # 所有 stderr 提示统一过一遍：拼进去的路径 / 地址再怪也不带控制字符


# ── 每会话一个状态文件（不是一份共享文件） ────────────────────────
# 旧版本用一份共享 JSON 文件存 {session_id: runId}，靠"读→改→整份覆写"更新。
# 两个会话的 SessionStart/SessionEnd 一旦交错执行（用户开了多个 Claude Code
# 窗口很常见），后写的那次会拿着自己读到的旧版本覆盖掉另一个会话刚写完的
# 记录——经典的丢更新。改成每个 session_id 一个独立文件，天然没有这个问题：
# 两个会话谁也不碰谁的文件，不需要加锁。文件名不能直接拿 session_id 拼
# （可能带路径分隔符等非法字符），所以落一个哈希。
def _state_file(session_id: str) -> Path:
    digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
    return cc.user_dir("state") / f"session-{digest}.json"


_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)  # Windows 没有
_O_NONBLOCK = getattr(os, "O_NONBLOCK", 0)


def _ensure_dir(path: Path) -> None:
    """状态目录只给本人：新建时 0700（里面的状态文件记着 cwd、transcript 路径、地址、pid）。
    已存在的：**必须是真目录**（符号链接一律拒绝——chmod / 写文件会跟着链接动到别处）且**属于本人**，
    权限太宽就修成 0700 继续用（v0.3 在 umask 002 下建的是 0775，升级的人都是这种）；
    符号链接、不属于本人、改不了权限的拒绝使用（`OSError`）：别人能在里面换文件，状态文件就信不得。
    Windows 没有这套属主 / 模式，跳过。"""
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != "nt":
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | _O_NOFOLLOW)  # 末段是符号链接 → ELOOP；以下全对这个 fd 做，不再按路径
        try:
            st = os.fstat(fd)
            if st.st_uid != os.getuid():
                raise OSError("state dir not private")
            if st.st_mode & 0o077:
                os.fchmod(fd, 0o700)  # 改不了就抛 OSError = 拒绝（SessionStart 会在发 start 之前报一行）
        finally:
            os.close(fd)


def _open_regular(path, flags: int, mode: int = 0o600) -> int:
    """打开状态目录里的文件：不跟符号链接，不因 FIFO 之类阻塞（`O_NONBLOCK`），打开后 `fstat` 必须是普通文件，
    否则关掉抛 `OSError`。本人的文件头一次碰到时收紧到 0600（老版本可能留下 0664）；别人的文件不用。"""
    fd = os.open(path, flags | _O_NOFOLLOW | _O_NONBLOCK, mode)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise OSError("not a regular file")
        if os.name != "nt":
            if st.st_uid != os.getuid():
                raise OSError("not our file")
            if st.st_mode & 0o077:
                os.fchmod(fd, 0o600)
    except BaseException:
        os.close(fd)
        raise
    return fd


STATE_MAX_BYTES = 64 * 1024  # 状态文件就几百字节；更大的不是我们写的，不读


def _read_small(path: Path) -> str:
    """读状态文件，带大小上限（状态目录里的东西当不可信：别的进程也写得了）。超了按「读不了」算；
    目录不私有 / 不是普通文件 / 是符号链接也按「读不了」算。"""
    _ensure_dir(path.parent)
    with os.fdopen(_open_regular(path, os.O_RDONLY), "rb") as f:
        raw = f.read(STATE_MAX_BYTES + 1)
    if len(raw) > STATE_MAX_BYTES:
        raise OSError("state file too large")
    return raw.decode("utf-8", errors="replace")


LOCK_WAIT = 2.0  # 拿锁最多等这么久（SessionEnd 钩子总预算 5s，锁内还有一次 HOOK_TIMEOUT 的请求）


@contextlib.contextmanager
def _session_lock(session_id: str, wait: float | None = None):
    """每会话一把排他咨询锁（状态文件旁的 .lock）：状态文件的「读→发请求→写/删」整段串行化，
    否则改名的 start 新开的 run 会被夹在 SessionEnd 的 stop 与删状态之间写回状态，再没人关它。
    钩子是互不相干的短命进程，所以用文件锁（POSIX flock / Windows msvcrt.locking）：进程死了内核自动放锁，
    不会有陈旧锁；锁文件本身留着无害。等锁有上限，超时或锁不了就不再等——钩子绝不能为锁卡住会话。
    **锁或者什么都不做**：`with … as held`，`held` 为 False 时一切改状态的操作（开 run / 写相位 / 改名 / 重开 /
    收尾删状态）一律放弃、也不发依赖它的改状态请求，留给下一个事件（或服务端的失联 / 遗忘超时）。没有「不带锁也往下走」的例外。
    等锁的时间同时受钩子总预算约束（`_remaining`）。"""
    wait = _remaining(LOCK_WAIT if wait is None else wait)
    f, held = None, False
    try:
        path = _state_file(session_id).with_suffix(".lock")
        _ensure_dir(path.parent)
        f = os.fdopen(_open_regular(path, os.O_RDWR | os.O_CREAT | os.O_APPEND), "a+b")  # 只给本人（同状态文件）
        if os.name == "nt":
            import msvcrt

            def try_lock():
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)

            def unlock():
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            def try_lock():
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)

            def unlock():
                fcntl.flock(f, fcntl.LOCK_UN)
        deadline = time.monotonic() + wait
        held = False
        while not held:
            try:
                try_lock()
                held = True
            except OSError:
                if time.monotonic() >= deadline:
                    _warn("状态锁等待超时，不改状态")
                    break
                time.sleep(0.02)
    except (OSError, ImportError):
        held = False
        _warn("状态锁用不了，不改状态")
    try:
        yield held  # False = 没拿到锁：改状态的一方必须放弃
    finally:
        if f is not None:
            with contextlib.suppress(Exception):
                if held:
                    unlock()
            with contextlib.suppress(Exception):
                f.close()


def _write_state(session_id: str, state: dict) -> bool:
    path = _state_file(session_id)
    try:
        _ensure_dir(path.parent)
        # 临时名带随机串 + O_EXCL（不跟符号链接、不截断已有文件）：异步相位钩子并行跑，名字也不可预测
        tmp = path.with_suffix(f".{os.getpid()}.{secrets.token_hex(6)}.tmp")
        try:
            with os.fdopen(_open_regular(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL), "w", encoding="utf-8") as f:  # 只给本人
                f.write(json.dumps(state))
            os.replace(tmp, path)  # 原子替换：不会有人读到"写了一半"的文件
            return True
        except BaseException:
            with contextlib.suppress(OSError):
                tmp.unlink()
            raise
    except OSError:
        return False  # 状态文件写不了也不该拖累会话；下次 SessionEnd 找不到就跳过 stop


def _save_run_id(
    session_id: str, run_id: str, last_phase: str | None = None, label: str | None = None, payload: dict | None = None,
    prev: dict | None = None, probe: tuple[int, str | None] | None = None,
) -> bool:
    """写状态（调用方持会话锁）；`prev` = 写之前的状态（SessionStart 在锁内读的）：同一个 CLI 的 `gen` 沿用，见 `_beat_facts`。"""
    state: dict = {"runId": run_id}
    if payload is not None:
        state.update(_beat_facts(session_id, payload, prev, probe))
    if last_phase:
        state["lastPhase"] = last_phase  # v0.3：PostToolUse 靠它决定报不报（见 _phase_for）
    if label:
        state["label"] = label  # v0.4：报过的泳道名；会话改名后靠它发现「变了」（见 handle_phase）
    return _write_state(session_id, state)


def _read_state(session_id: str) -> dict | None:
    """只读，成功时不删——SessionEnd 得先确认 stop 报成功了才能删（见
    handle_session_end），不然报失败/超时/钩子被杀死的时候，这条记录跟着
    没了，run 就再也关不掉了，只能等 cockpit 服务端自己的兜底超时
    （数小时量级）才会收尾。

    读不出可用 runId（文件不存在 / 读不了 / 不是合法 JSON / 形状不对）一律返回 None，**这里不删文件**
    （删也是改状态，要持锁：坏文件由持锁的 SessionEnd 清掉，或被下一次 SessionStart 覆盖）。
    """
    path = _state_file(session_id)
    try:
        raw = _read_small(path)
    except OSError:  # 含 FileNotFoundError
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(data, dict) or not cc.valid_run_id(data.get("runId")):  # 不是服务端 runId 的样子：不拼进 URL，当没有
        return None
    return data


def _read_run_id(session_id: str) -> str | None:
    state = _read_state(session_id)
    return state["runId"] if state else None


def _delete_run_id(session_id: str) -> None:
    path = _state_file(session_id)
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _start(payload: dict, session_id: str, phase: str, title: str | None) -> tuple[str | None, str]:
    """报 start，返回 `(runId, 报上去的 label)`。泳道名 = 会话的名字（`title`），没起名就是工作目录名。
    clientKey 是 session_id 的哈希（不发原始会话号）：钩子被重试 / 响应丢了时服务端回原运行，不多开一条泳道；
    同一个会话改了名再调一次，服务端给原运行换名字（nexus-core v2.13「会话改名」）。"""
    cwd = payload.get("cwd")
    config = cc.load_config()
    task_id, project_id = cc.resolve_target(cwd=cwd, config=config)
    label, match = cc.lane_names(cwd, title)
    result = cc.start_run(
        config, task_id, cc.default_agent_name(cwd), "claude-code", timeout=_http_timeout(),
        model=payload.get("model"),  # 文档：只有 SessionStart 会带，且不保证有
        phase=phase, label=label, match=match,
        client_key=hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:32],
        project_id=project_id,
    )
    return result.get("runId"), label


def _state_dir_problem() -> str | None:
    """状态目录能用吗（会顺手把本人的 0775 修成 0700）。不能用 → 一行话：哪里不对、怎么修。"""
    path = cc.user_dir("state")
    try:
        _ensure_dir(path)
    except OSError:
        shown = _safe(path)  # 只用于输出（环境 / XDG / home 来的路径可能夹换行 / 转义序列）
        return f"状态目录 {shown} 是符号链接、不属于你或改不了权限，本次会话不计相位、不发心跳。修法：删掉链接 / sudo chown -R \"$USER\" {shown} && chmod 700 {shown}"
    return None


def _ours(state: dict, probe: tuple[int, str | None]) -> bool:
    """**调用方持会话锁。** 锁只串行、不授权：发起这次调用的 CLI（`probe` = 钩子本次调用取的 pid + 启动时刻）是不是这份状态记的那个 `cli`。
    不是（CLI A 的迟到事件撞上 CLI B 恢复同一会话后的状态）→ 调用方什么都不做：不停 run、不删状态、不写相位、不改名。
    **限度**：认不出发起的 CLI（不支持的平台 / 取不到启动时刻），或状态里没记 CLI，保持原来的行为（放行）；
    同一个 CLI 更早一代的迟到事件（同进程内 `/clear`、`--resume`）没有事件代号就分不出来。"""
    cli, born = _cli_identity(probe)
    rec = state.get("cli")
    if not cli or not (isinstance(rec, list) and len(rec) == 2 and rec[0]):
        return True
    return rec == [cli, born]


def _unsupervised_reason(probe: tuple[int, str | None] | None = None) -> str | None:
    """认不出 Claude Code（= 没有心跳监督）的原因 + 补救；认得出、或本来就关了心跳 → None。`probe` = 调用方已取的 (pid, 启动时刻)。"""
    if cc.beat_mode() == "off":
        return None
    if os.name == "nt":
        return "Windows 上不做心跳监督，运行靠服务端失联规则（30 分钟）收尾"
    pid, born = probe or _cli_probe()
    if not pid:
        return ("认不出 Claude Code 进程（祖先里没有名字以 claude 开头的，最多看 %d 层）。"
                "补救：COCKPIT_CLI_NAMES=<进程名前缀,…> 或 COCKPIT_CLI_PID=<pid>" % ANCESTOR_DEPTH)
    if born is None:
        return "本机取不到进程启动时刻（macOS 等），分不出 pid 复用，不做心跳监督"
    return None


def handle_session_start(payload: dict) -> None:
    session_id = payload.get("session_id")
    if not session_id:
        return
    problem = _state_dir_problem()  # 先于 start：状态存不下来就不开 run，免得留下没人会关的孤儿
    if problem:
        _warn(problem)
        return
    title = cc.session_title(payload.get("transcript_path"))  # 恢复的会话（--resume）transcript 里已经有名字
    probe = _cli_probe()  # CLI 祖先每次调用只查一次
    # 一个临界区：读状态 → /start → 落状态 → 落不下来就 /stop 这条 run，**全在锁内**。SessionEnd / 发心跳进程的收尾拿同一把锁，
    # 所以不会有「刚落下来的 run 被另一个进程的补偿 stop 停掉」。拿不到锁：不发 /start、不写状态。
    with _session_lock(session_id) as held:
        if not held:
            return
        prev = _read_state(session_id)
        try:
            # 会话开着、还没说话 = idle
            run_id, label = _start(payload, session_id, "idle", title)
        except Exception as e:  # noqa: BLE001 — 配置/网络任何一步出岔子都只是"这次不计时"
            category = e if isinstance(e, cc.CockpitError) else "配置错误"
            _warn(f"SessionStart 上报失败，本次会话不计时（{category}）")
            return
        if not run_id:
            return
        if not _save_run_id(session_id, run_id, "idle", label, payload, prev, probe):
            if not (prev and prev["runId"] == run_id):  # start 成了、状态却落不下来：停掉这条，别留没人关的 run（尽力而为）
                with contextlib.suppress(Exception):
                    cc.stop_run(cc.load_config(), run_id, "cancelled", timeout=_http_timeout())
            return
        cur = _read_state(session_id) or {}
        if not cur.get("warned") and (why := _unsupervised_reason(probe)):
            _warn(f"没有心跳监督：{why}")  # 每个会话只说一次（记进状态）
            _write_state(session_id, {**cur, "warned": True})
    # 无论有没有旧状态：`.beat` 被占着（旧代的发心跳进程睡在里面，醒来发现归属号变了才退）就起一个有界的 `--wait` 伴随进程，
    # 否则「SessionEnd 删状态 → 同会话 SessionStart」会没人接着发。`takeover` 只剩调用习惯，行为已并进 wait=True。
    _spawn_beat(session_id, cur, wait=True, probe=probe)


# Notification 的 notification_type → 相位（其余种类不报）。message 字段不读。
_NOTIFICATION_PHASE = {
    "permission_prompt": "waiting_permission",  # 兜底：沙箱网络请求的批准不触发 PermissionRequest
    "elicitation_dialog": "waiting_input",
    "elicitation_url_dialog": "waiting_input",
    "agent_needs_input": "waiting_input",
    "idle_prompt": "idle",  # 按 Esc 打断时 Stop 不触发，靠它把灯收回来
}


def _tag(value) -> str | None:
    """短标签：只收字符串，截到 64 码点（服务端上限）。"""
    return value[:64] if isinstance(value, str) and value else None


def _phase_for(payload: dict, last_phase: str | None) -> tuple[str, str | None, bool] | None:
    """规范性映射（README「Claude Code 钩子：相位」）。返回 (phase, detail, reply) 或 None=不报。"""
    event = payload.get("hook_event_name")
    if event == "UserPromptSubmit":  # 不读 prompt：人说了话
        return "working", None, True
    if event == "PermissionRequest":
        return "waiting_permission", _tag(payload.get("tool_name")), False
    if event == "Notification":
        kind = payload.get("notification_type")
        phase = _NOTIFICATION_PHASE.get(kind) if isinstance(kind, str) else None
        return (phase, kind, False) if phase else None
    if event in ("PostToolUse", "PostToolUseFailure"):
        # 只在上次报的是「等」时报：人批准 / 回答了，它接着干。不为每次工具调用发一次请求
        if last_phase in ("waiting_input", "waiting_permission"):
            return "working", None, True
        return None
    if event == "Stop":
        return "idle", None, False
    if event == "StopFailure":
        return "error", _tag(payload.get("error")), False
    return None


def handle_phase(payload: dict, at: str) -> None:
    """`at` 是钩子进程开始处理的那一刻（异步钩子并行跑，服务端按 at 排）。
    持会话锁：重读状态 → **在锁内按新状态定相位** → 写 lastPhase → 发相位 →（会话改名 / 原 run 被收了就）改名重开，一整段在锁内；
    拿不到锁什么都不写、不发。发起的 CLI 不是状态记的那个 → 整个事件不做（`_ours`）。
    **限度**：异步钩子的乱序到达在本地不重排（服务端按 `at` 排）。"""
    session_id = payload.get("session_id")
    if not session_id:
        return
    state = _read_state(session_id)
    if not state:
        return  # 没开过 run（SessionStart 没报成）：这次会话不画相位
    probe = _cli_probe()
    _spawn_beat(session_id, state, probe=probe)  # 没人在发心跳（到了寿命 / 被杀 / 钩子升级前开的会话）就补一个伴随进程
    if _phase_for(payload, "waiting_input") is None:
        return  # 与上次相位无关就不报的事件（只有 PostToolUse 看 lastPhase）：不用拿锁
    title = cc.session_title(payload.get("transcript_path"))
    with _session_lock(session_id) as held:
        cur = _fresh(session_id, state) if held else None  # SessionEnd 已收尾 / 换了会话 / 没拿到锁：不写、不发
        if not cur or not _ours(cur, probe):
            return
        decision = _phase_for(payload, cur.get("lastPhase"))  # 锁内的新状态：锁前读到的 lastPhase 可能已被并发的钩子改过
        if decision is None:
            return
        phase, detail, reply = decision
        _write_state(session_id, {**cur, "lastPhase": phase})  # 在锁内重读的状态上只改 lastPhase：归属字段绝不从锁前快照写
        closed = False
        try:
            result = cc.phase_run(cc.load_config(), cur["runId"], phase, at, detail=detail, reply=reply, timeout=_http_timeout())
            closed = result.get("reason") == "closed"  # 服务端已收掉这条（失联 / 超时）：会话还活着，下面重开
        except Exception as e:  # noqa: BLE001 — 相位只是记录，失败只留一行固定分类
            category = e if isinstance(e, cc.CockpitError) else "配置错误"
            _warn(f"{payload.get('hook_event_name')} 相位上报失败（{category}）")
        # 会话改名（/rename）不触发任何钩子：趁这次本来就要发请求，看一眼 transcript 末尾，名字变了才多发一次 start。
        # 比的是「有效名字」（标题，没有就是目录名）：标题被清空时要退回目录名。没改名时零额外请求。
        if closed or cc.lane_names(payload.get("cwd"), title)[0] != cur.get("label"):
            _relabel_locked(payload, session_id, title, cur)


def _fresh(session_id: str, state: dict) -> dict | None:
    """锁内重读状态：还是 `state` 那次会话（runId 与归属号 gen 都对得上）才返回最新的那份，否则 None。"""
    cur = _read_state(session_id)
    return cur if cur and cur["runId"] == state["runId"] and cur.get("gen") == state.get("gen") else None


def _relabel_locked(payload: dict, session_id: str, title: str | None, state: dict) -> None:
    """**调用方持会话锁。** 改名（v0.4 心跳起也用来在原 run 被服务端收掉后重开）：相位取锁内重读的状态里的 `lastPhase`
    （不是调用方手里的旧参数），改名 / 重开**绝不写 `lastPhase`**。同一把锁下 SessionEnd 不可能插进来停 run、删状态，
    所以 start 返回的 runId（同一条改名，或服务端已收了原 run 而换成新的）直接写回；写不下来 → 见下面的补偿 stop。
    调用方负责「发起的 CLI 是不是这份状态的主人」（钩子路径 `_ours`；发心跳的进程靠 `gen` 归属核对，换 CLI 必换 `gen`）。"""
    cur = _fresh(session_id, state)
    if not cur:
        return
    phase = cur.get("lastPhase")
    try:
        run_id, label = _start(payload, session_id, phase if phase in cc.PHASES else "idle", title)
        if run_id and not _write_state(session_id, {**cur, "runId": run_id, "label": label}):
            if run_id != cur["runId"]:  # 重开了新 run 却记不下来：停掉它，保留旧记录（同 SessionStart 的补偿规则；尽力而为）
                with contextlib.suppress(Exception):
                    cc.stop_run(cc.load_config(), run_id, "cancelled", timeout=_http_timeout())
    except Exception as e:  # noqa: BLE001 — 改名没报上去：下一个事件再试
        category = e if isinstance(e, cc.CockpitError) else "配置错误"
        _warn(f"会话改名上报失败（{category}）")


def handle_session_end(payload: dict) -> None:
    session_id = payload.get("session_id")
    if not session_id:
        return
    probe = _cli_probe()
    with _session_lock(session_id) as held:  # 读状态 → stop → 删状态 一整段持锁：停的是锁内读到的那条 run
        if held:  # 拿不到锁：不停不删（留给服务端的失联 / 遗忘超时），不和别的持锁者抢
            st = _read_state(session_id)
            if st and not _ours(st, probe):
                return  # 别的 CLI 恢复了这个会话，这是老 CLI 迟到的 SessionEnd：不停它的 run、不删它的状态
            _session_end_locked(session_id, _REASON_TO_OUTCOME.get(payload.get("reason"), "done"))


def _session_end_locked(session_id: str, outcome: str) -> None:
    """**调用方持会话锁。**"""
    run_id = _read_run_id(session_id)
    if not run_id:
        _delete_run_id(session_id)  # 没有可用 runId（含坏文件）：清掉，不留垃圾
        return
    try:
        config = cc.load_config()
        cc.stop_run(config, run_id, outcome, timeout=_http_timeout())
    except Exception as e:  # noqa: BLE001 — 同上，绝不能是"会话结束不了"的理由
        category = e if isinstance(e, cc.CockpitError) else "配置错误"
        # code == 404 且响应体是 JSON，才是 nexus-core 应用层明确说"这条 run
        # 不存在"；光看状态码不够——COCKPIT_URL 配错了（比如指到了一个完全
        # 不相关的服务，或者网关本身）同样会给 404，但那是网关/nginx 的默认
        # 404 页（HTML），不是 cockpit 说这条 run 没了，不能当"确定丢弃"处理，
        # 不然一次配置错误就会把所有还开着的 run 的本地记录全部冲掉。
        run_confirmed_gone = isinstance(category, cc.CockpitError) and category.code == 404 and category.json_body
        if run_confirmed_gone:
            _warn(f"SessionEnd: cockpit 说这条 run 已经不存在了，清掉本地记录（{category}）")
            _delete_run_id(session_id)
        else:
            # 其余任何失败（连不上/超时/配置错误/别的 HTTP 状态码）都不删：
            # 删了就真丢了，只能等 cockpit 服务端自己的兜底超时。保留本地记录，
            # 下次这个目录/任务再触发 SessionStart 或 SessionEnd 时至少还有机会
            # 看到它（是否重试是以后的事，这里先不做，只求不丢）。
            _warn(f"SessionEnd 上报失败，本地记录先保留（{category}）")
        return
    _delete_run_id(session_id)


# ── 心跳（v0.4，`contracts/agent.lane.v1`「心跳与失联」；README「心跳」节）──────────────────
# 一个循环（`_beat_session`），两种起法（`beat_loop` 的 source），**由静态配置 `beat` 二选一，没有协商**：
#   companion —— 缺省。钩子起的脱离进程（自己一个 session、stdio 全接 /dev/null）。会话号由钩子给；最多活 BEAT_MAX_LIFETIME，
#                到点就退，会话还在的话下一个钩子事件补一个。装成插件时也一样由 SessionStart 起。
#   monitor   —— 用户显式设了 `beat=monitor`（`COCKPIT_BEAT` / 配置文件）时，装成插件的 Claude Code 自己起、随会话结束
#                （monitors/monitors.json）。CLI 不给 monitor 会话号，也不给插件设置：它按「同一个 Claude Code 进程」到状态目录里认会话，
#                /clear 换了会话就换着跟。`beat` 不是 monitor 时它一起来就静悄悄退出。
#                **一个字都不往 stdout / stderr 写**——monitor 的输出会被当成通知送进会话、叫醒模型。
# 循环只做三件事：每 BEAT_CHECK_SECONDS 看一眼 Claude Code 还在不在（不在了：持会话锁报 stop、删状态、退出）；
# 状态文件没了（SessionEnd 收过尾）就放手；按服务端给的间隔发心跳（不持锁、3 秒时限、失败不管）。
# 一个会话同一时刻只有一个在发：`.beat` 文件锁（`_beat_lock`）是唯一的互斥。另一个 CLI 恢复了会话时，新的伴随进程用 `--wait`
# 在有限时间内等老的放锁（每秒重核对归属号 / CLI 身份），这是唯一的交接机制。
# **卡住是终态**：上一下请求连着 BEAT_MAX_STUCK_SKIPS 圈都没返回，`_beat_session` 返回 `END_STUCK`，companion 与 monitor
# 两种进程都**整个退出**（进程一退，卡住的线程跟着没了）；下一个钩子事件会再起伴随进程。
# Windows 上都不起（没有可靠又不伤人的「这个 pid 还活着吗」）：那里的运行不声明心跳，照旧只有遗忘超时兜底。
BEAT_CHECK_SECONDS = 30
BEAT_MAX_LIFETIME = 24 * 3600
BEAT_TIMEOUT = cc.DEFAULT_TIMEOUT
GONE_OUTCOME = "cancelled"  # Claude Code 没发 SessionEnd 就没了（崩了 / 被杀 / 终端被关）
ANCESTOR_DEPTH = 6
TAKEOVER_WAIT = 45  # 另一个 CLI 恢复了会话：新的发心跳进程最多等老的放锁这么多秒（老的每 BEAT_CHECK_SECONDS 看一眼就退）
STOP_ATTEMPTS, STOP_RETRY_SECONDS = 3, 30  # CLI 没了而 stop 没报成：共试 3 次、隔 30 秒（约 1 分钟），之后交给服务端失联规则
BEAT_MAX_STUCK_SKIPS = 3  # 连续这么多圈都因「上一下请求还卡着」跳过心跳 → END_STUCK，整个进程退出
END_GONE, END_STUCK, END_OTHER = "gone", "stuck", "other"  # `_beat_session` 的结局：CLI 没了已收尾 / 请求卡死 / 其它（放手、没轮到、寿命到）


def _proc(pid: int) -> tuple[int, str, str | None] | None:
    """`(父 pid, 进程名, 启动时刻)`；不存在 / 已是僵尸 / 查不了 → None。Linux 读 /proc（带启动时刻，pid 被复用也认得出）；
    别的 POSIX 问 `ps`（没有启动时刻；受钩子总预算约束）。"""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace")
        rest = stat[stat.rindex(")") + 2:].split()  # 进程名里可以有空格和括号：从最后一个 ) 之后数
        if rest[0] == "Z":
            return None
        return int(rest[1]), stat[stat.index("(") + 1:stat.rindex(")")], rest[19]
    except (OSError, ValueError, IndexError):
        pass
    try:
        if _remaining(2) <= 0:  # 总预算用完了：不再起 ps
            return None
        out = subprocess.run(["ps", "-o", "ppid=,comm=", "-p", str(pid)], capture_output=True, text=True,
                             stdin=subprocess.DEVNULL, timeout=_remaining(2)).stdout.split(None, 1)
        return int(out[0]), os.path.basename(out[1].strip()).lstrip("-"), None
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return None


def _cli_pid() -> int:
    """本进程的祖先里哪个是 Claude Code。**「确认认出」只有两种**：
    1. 环境变量 `COCKPIT_CLI_PID` 明确指了一个活着的进程（用户 / 包装脚本说了算）；
    2. 祖先里（最多 `ANCESTOR_DEPTH` 层）进程名以 `claude` 开头的——或以 `COCKPIT_CLI_NAMES`（逗号分隔的名字前缀）
       里的某个开头的；取最近的一个。
    **不猜**：`CLI → timeout / 辅助进程 → shell → 钩子` 这种祖先里没有认得出的名字，就回 0（= 认不出），
    不再退到「第一个不是 shell 的」——那常常是个短命的辅助进程，它一退出就会替还活着的 CLI 收掉泳道。
    认不出 = 不发心跳（`agent.lane.v1`：发不出死亡就别声明心跳），运行照旧靠遗忘超时兜底。"""
    override = os.environ.get("COCKPIT_CLI_PID", "")
    if override.isdigit() and int(override) > 1:
        return int(override) if _proc(int(override)) else 0
    names = ("claude", *(n.strip().lower() for n in os.environ.get("COCKPIT_CLI_NAMES", "").split(",") if n.strip()))
    pid = os.getppid()
    for _ in range(ANCESTOR_DEPTH):
        info = _proc(pid) if pid > 1 else None
        if info is None:
            break
        if info[1].lower().startswith(names):  # 名字不分大小写
            return pid
        pid = info[0]
    return 0


def _born(pid: int) -> str | None:
    return (_proc(pid) or (0, "", None))[2] if pid else None


def _cli_probe() -> tuple[int, str | None]:
    """`(pid, 启动时刻)`，**在钩子里（启动者）取**，再交给伴随进程——它自己事后再取会留下 pid 被复用的空档。
    一次钩子调用只调一次（祖先要读 /proc 或起 `ps`），结果往下传。认不出 → `(0, None)`。"""
    pid = _cli_pid()
    return pid, _born(pid)


def _cli_identity(probe: tuple[int, str | None] | None = None) -> tuple[int, str | None]:
    """同 `_cli_probe`，但没有启动时刻（macOS 的 `ps` 没有）= 分不出「同一个进程」与「复用了这个 pid 的另一个」，按认不出算，不监督。"""
    pid, born = probe or _cli_probe()
    return (pid, born) if pid and born is not None else (0, None)


def _alive(pid: int, born: str | None) -> bool:
    if born is not None:  # 有启动时刻：pid 还在且还是同一个进程
        info = _proc(pid)
        return info is not None and info[2] == born
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        pass  # 没权限发信号 = 进程在
    return True


def _beat_facts(session_id: str, payload: dict, prev: dict | None = None, probe: tuple[int, str | None] | None = None) -> dict:
    """SessionStart 时记进状态文件、给发心跳的进程用的几样（**只存本机，不上报**）：
    运行被服务端收掉后重开要的 cwd / transcript；monitor 认会话要的会话号与 Claude Code 的 (pid, 启动时刻)；
    monitor 拿不到插件设置，所以记下钩子用的地址、钩子有没有带令牌（令牌本身不落盘）。"""
    # `gen`：这一次 SessionStart 写下状态时随机生成的归属号。发心跳的进程带着它和 CLI 身份，每圈核对；会话被另一个 CLI
    # 恢复（SessionStart 换了状态）后，老进程对不上就放手，不会去停新 CLI 的运行（见 `_owned`）。
    # **同一个 CLI 再来一次 SessionStart（/compact、恢复）沿用原来的 `gen`**：否则在发的老进程反被判成「换了主人」退出，没人接着发。
    # `activatedAt`：**只有 SessionStart 写**（含同会话的再一次 SessionStart = 刷新），别的写入（相位 / 改名 / 重开）一律 `{**cur, …}` 原样带过。
    # `_session_of` 凭它认「这个 CLI 此刻在哪个会话」，不看文件 mtime（迟到的老会话钩子重写旧状态会抬高 mtime）。
    facts: dict = {"session": session_id, "gen": secrets.token_hex(8), "activatedAt": time.time()}
    for key, source in (("cwd", "cwd"), ("transcript", "transcript_path")):
        if isinstance(payload.get(source), str):
            facts[key] = payload[source]
    try:
        cli, born = _cli_identity(probe)
        config = cc.load_config()
        facts.update(cli=[cli, born], url=config["url"], auth=bool(config["token"]))
    except Exception:  # noqa: BLE001 — 少了这几样只是 monitor 认不出这个会话
        pass
    if prev:
        if isinstance(prev.get("gen"), str) and prev.get("cli") == facts.get("cli"):
            facts["gen"] = prev["gen"]
        if prev.get("warned"):
            facts["warned"] = True  # 「没有心跳监督」那行提示每个会话只说一次
    return facts


def _beat_lock(session_id: str):
    """「一个会话一个发心跳的」：状态目录里 .beat 文件上的独占 flock，发多久拿多久——进程死了内核放锁，
    没有陈旧 pidfile 要清。拿到 → 打开着的文件（持有者写进自己的 pid 和起法，给人看）；别人拿着 → None。
    拿到锁后核对「路径上现在的文件就是我锁住的这个 inode」：路径被换过（旧的还被别人锁着）就不算拿到，
    否则第二个发心跳的会锁到另一个 inode。这是两种起法之间**唯一**的互斥。"""
    import fcntl

    path = _state_file(session_id).with_suffix(".beat")
    try:
        _ensure_dir(path.parent)
        f = os.fdopen(_open_regular(path, os.O_RDWR | os.O_CREAT | os.O_APPEND), "a+", encoding="utf-8")  # 只给本人
    except OSError:
        return None  # 目录不私有 / 是符号链接 / 不是普通文件：不当作拿到锁
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        here, there = os.fstat(f.fileno()), os.lstat(path)
        if (here.st_dev, here.st_ino) != (there.st_dev, there.st_ino):
            raise OSError("lock file replaced")
    except OSError:
        f.close()
        return None
    return f


def _spawn_beat(session_id: str, state: dict | None, wait: bool = False, probe: tuple[int, str | None] | None = None) -> None:
    """`beat=companion`（缺省）时，没人在发心跳就起一个伴随进程，立刻返回（不等它）。起不来只是「这个会话没有心跳」。
    `state` = 钩子手里的状态（可为 None）。`wait`：另一个 CLI 恢复了这个会话，老的发心跳的还占着锁、
    马上要退——照样起，新进程自己（不是钩子）等锁最多 `TAKEOVER_WAIT` 秒。`beat=monitor` / `off` 时不起。"""
    try:
        if os.name == "nt" or cc.beat_mode() != "companion":
            return
        cli, born = _cli_identity(probe)  # 在这里（启动者）取 CLI 的 pid + 启动时刻，交给伴随进程，它不事后再取
        st = state or _read_state(session_id)
        if not cli or (st and not (_usable(st) and _owned(st, st.get("gen"), cli, born))):
            return  # 认不出 Claude Code（死了看不出来）/ 发了也发不出去 / 状态是另一个 CLI 的：不起
        probe_lock = _beat_lock(session_id)
        if probe_lock is None and not wait:
            return  # 已经有人在发
        if probe_lock:
            probe_lock.close()  # 两个钩子同时走到这里会各起一个：伴随进程自己再抢一次锁，输的那个直接退
        subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "--beat", "--source", "companion",
             "--session", session_id, "--cli", str(cli), "--born", str(born),
             *(["--gen", st["gen"]] if st and isinstance(st.get("gen"), str) else []),
             *(["--wait"] if wait and probe_lock is None else [])],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True, close_fds=True, cwd=str(_state_file(session_id).parent),
        )
    except Exception:  # noqa: BLE001 — 钩子绝不能因为它出错
        pass


def _usable(state: dict) -> bool:
    """这个进程发得了这个会话的心跳吗。monitor 没有钩子的环境：钩子带了令牌而自己没有（令牌只填在插件设置里）
    就发不了——让开。地址同理拿不到时用钩子记在状态文件里的那个，但**只在自己手里没有令牌时**：
    状态文件别的进程也写得了，令牌绝不发往从那里读来的地址。"""
    config = cc.load_config()
    remembered = state.get("url")
    if state.get("beat") == "unsupported":
        return False  # 这个会话的服务端没有心跳路由：不再起也不再发
    if remembered and config["url"] and remembered != config["url"]:
        return False  # 运行活在别的服务器上：往本进程配的这个发会在那里造出一条野运行
    if (not config["url"] and not config["token"] and not os.environ.get("COCKPIT_TOKEN")  # 环境里的令牌会与补进去的地址配成对
            and isinstance(remembered, str) and remembered and not state.get("auth")):
        os.environ["COCKPIT_URL"] = remembered  # 只改本进程：下面的 load_config / 重开都读得到
        config = cc.load_config()
    return bool(config["url"]) and (bool(config["token"]) or not state.get("auth"))


class _MutationStuck(Exception):
    """`_beat_once` 里改状态的请求（重开的 `/start`）超时而工作线程还在：`_beat_session` 据此返回 `END_STUCK`。"""


def _beat_once(session_id: str, state: dict, source: str) -> float | None:
    """发一次心跳，返回下一次隔多少秒（None = 别再发了）。服务端说这条运行已结束（机器睡过头被判了失联）而会话还在 → 重开一条。
    **心跳不持会话锁**（只有 `.beat` 互斥），所以「每个改状态的请求都持锁」不含它。这不会让心跳改坏正在收尾的运行：服务端的
    心跳端点（`modules/nexus-core/.../timer/agent_phases.py::heartbeat` → `repo.touch_agent_run`）只对**未打关闭标记**
    （`closing` 不存在）的运行更新 `lastSeenAt` / `beatCount`，已关闭 / 关闭中的回 `applied:false, reason:"closed"`，既不重开也不改它。"""
    interval, closed = cc.beat(cc.load_config(), state["runId"], BEAT_TIMEOUT, source)
    if closed == cc.BEAT_UNSUPPORTED:  # 服务端没有心跳：记进状态，之后钩子事件不再起发心跳的进程；绝不声明、不重开
        with _session_lock(session_id) as held:
            st = _fresh(session_id, state) if held else None
            if st:
                _write_state(session_id, {**st, "beat": "unsupported"})
        return None
    if closed and state.get("cwd"):
        payload = {"cwd": state["cwd"], "transcript_path": state.get("transcript")}
        with _session_lock(session_id) as held:  # 重开要持锁（拿不到就留给下一圈 / 下一个钩子事件）；相位取锁内状态里的 lastPhase
            if held:
                stuck_before = cc.stuck_requests()
                _relabel_locked(payload, session_id, cc.session_title(state.get("transcript")), state)
                if cc.stuck_requests() > stuck_before:
                    raise _MutationStuck  # /start 超时而线程还活着：它可能在锁放掉之后才到服务端，进程不许接着正常干活
        # 重开成了：新的那条还没声明心跳，下一轮就发。没成：closed 过一会儿再试；服务端不认这个运行（404，
        # 库重置 / 换了租户）就只重开这一次，不成就停，等下一个钩子事件再补一个发心跳的（有界，不是每个间隔都敲）
        if _read_run_id(session_id) != state["runId"]:
            return 0
        return None if closed == cc.BEAT_MISSING else cc.HEARTBEAT_MIN
    return None if closed == cc.BEAT_MISSING else interval


def _lock_current(lock, session_id: str) -> bool:
    """手里锁着的还是路径上现在的那个 `.beat` 文件吗。路径被换了（旧文件成了孤儿 inode）就该放手，
    否则换进来的新文件上还能再起一个发心跳的。"""
    try:
        here, there = os.fstat(lock.fileno()), os.lstat(_state_file(session_id).with_suffix(".beat"))
    except OSError:
        return False
    return (here.st_dev, here.st_ino) == (there.st_dev, there.st_ino)


def _owned(state: dict | None, gen: str | None, cli: int, born: str | None) -> bool:
    """这份状态还是「我」负责的那次会话吗：SessionStart 写的归属号 `gen` 和 CLI 身份 (pid, 启动时刻) 都对得上。
    会话被另一个 CLI 恢复（SessionStart 换了状态）后老的发心跳进程对不上——放手，不停也不删新 CLI 的运行。"""
    return bool(state) and state.get("gen") == gen and state.get("cli", [cli, born]) == [cli, born]


def _beat_session(
    session_id: str, source: str, cli: int, born: str | None, budget: list[int], sleep, clock,
    gen: str | None = None, identify=None, wait: bool = False,
) -> str:
    """给一个会话发心跳，直到它结束 / 换了主人 / `budget`（剩余圈数，循环间共享）用尽 / 轮不到自己 / 请求卡死。
    返回 `END_GONE`（Claude Code 没了，已替它收尾）、`END_STUCK`（请求连着几圈卡着：调用方**必须让整个进程退出**）、
    其它一律 `END_OTHER`。`identify`：monitor 每圈重认一次 CLI，对不上就退。"""
    state = _read_state(session_id)
    if not cli or born is None or not state or not _usable(state):
        return END_OTHER  # 认不出 Claude Code = 死了也看不出来：不发（agent.lane.v1「发不出死亡就别声明心跳」），运行照旧 12 小时兜底
    gen = state.get("gen") if gen is None else gen  # monitor 头一次认会话时取；companion 由启动它的钩子给
    if not _owned(state, gen, cli, born):
        return END_OTHER
    lock = _beat_lock(session_id)
    for _ in range(TAKEOVER_WAIT if wait else 0):  # 另一个 CLI 时代的老发心跳进程一圈内会退，等它放锁；每秒重核对归属，会话没了 / 又换了主人就不等
        if lock is not None:
            break
        sleep(1)
        if not _owned(_read_state(session_id), gen, cli, born):
            return END_OTHER
        lock = _beat_lock(session_id)
    if lock is None:
        return END_OTHER  # 另一个已经在给它发
    with lock:
        lock.truncate(0)
        lock.write(f"{os.getpid()} {source}")
        lock.flush()
        skips = 0
        next_beat = clock()  # 第一下马上发：声明这条运行会发心跳。限度：`clock` 是单调钟，机器休眠期间不走，睡醒后下一下可能晚到一个间隔内
        while budget[0] > 0:
            budget[0] -= 1  # 寿命按圈数算（每圈睡 BEAT_CHECK_SECONDS），不受墙钟跳变影响
            state = _read_state(session_id)
            if not _owned(state, gen, cli, born):
                return END_OTHER  # SessionEnd 收过尾了 / 会话被别的 CLI 恢复了：放手，不停
            if not _lock_current(lock, session_id):
                return END_OTHER
            if not _alive(cli, born):  # 先看死活：CLI 一死 monitor 就被收养、再也认不出祖先，先 identify 会静悄悄退掉、谁也不收尾
                gone = _stop_gone(session_id, gen, cli, born, sleep)
                return END_STUCK if gone is None else END_GONE if gone else END_OTHER
            if (now := _session_of(cli, born, proven=True)) and now != session_id:  # 同一个 CLI 已经在另一个会话上（/clear、恢复，而 SessionEnd 没拿到锁）：这个会话早结束了
                moved = _stop_gone(session_id, gen, cli, born, sleep, outcome="done", moved=True)  # 收的结果同 SessionEnd 的缺省；锁内再核对一次
                return END_STUCK if moved is None else END_OTHER
            if identify is not None and identify() not in ((cli, born), (0, None)):
                return END_OTHER  # 认出的是另一个 CLI；认不出但 pid + 启动时刻仍活着 = 没变
            if clock() >= next_beat:
                if cc.stuck_requests():  # 上一下还卡在后台：不叠新的；连着卡几圈就是终态，整个进程退出（线程随进程没），下一个钩子事件起新的
                    skips += 1
                    if skips >= BEAT_MAX_STUCK_SKIPS:
                        return END_STUCK
                    next_beat = clock() + cc.HEARTBEAT_MIN
                else:
                    skips = 0
                    attempt_at = clock()  # 从**这一次尝试开始**的时刻排下一次：请求慢不会把间隔越拉越长（间隔 ≤ 服务端给的）
                    try:
                        wait_s = _beat_once(session_id, state, source)
                    except _MutationStuck:
                        return END_STUCK
                    if wait_s is None:
                        return END_OTHER  # 重开不成：停，下一个钩子事件再起
                    next_beat = attempt_at + wait_s
            sleep(BEAT_CHECK_SECONDS)
    return END_OTHER


def _stop_gone(
    session_id: str, gen: str | None, cli: int, born: str | None, sleep, outcome: str = GONE_OUTCOME, moved: bool = False,
) -> bool | None:
    """CLI 没了：持会话锁、核对归属后报 stop。没报成（服务端暂时不可达、没拿到锁）就隔 `STOP_RETRY_SECONDS` 再试，
    最多 `STOP_ATTEMPTS` 次，之后不管了（CLI 都没了，兜底是服务端的失联规则）。会话被别的 CLI 恢复了 → False（不停）。
    `moved`：调用方是因为「CLI 已在别的会话上」才来收的——**锁内、停之前再核对一遍**这个会话还是不是被别人顶替了
    （锁前的判断可能已过时），不是 → False（不停）。
    **返回 None = `/stop` 超时而它的工作线程还活着**：已发出的请求可能在锁放掉之后才到服务端（见 README「已知限制」），
    调用方必须让整个进程退出（`END_STUCK`），不许放锁后接着正常干活、也不重试。"""
    for attempt in range(STOP_ATTEMPTS):
        if attempt:
            sleep(STOP_RETRY_SECONDS)
        with _session_lock(session_id) as held:
            if held:
                if not _owned(_read_state(session_id), gen, cli, born):
                    return False
                if moved and (now := _session_of(cli, born, proven=True)) in (None, session_id):
                    return False  # 这个会话（又）成了该 CLI 的当前会话，或已认不出别的：不停
                stuck_before = cc.stuck_requests()
                _session_end_locked(session_id, outcome)
                if cc.stuck_requests() > stuck_before:
                    return None
                if _read_state(session_id) is None:  # 成了（或服务端说这条早没了）：状态已删
                    return True
    return True


def _session_of(cli: int, born: str | None, proven: bool = False) -> str | None:
    """这个 Claude Code 进程此刻的会话：状态目录里记着同一个 (pid, 启动时刻) 的状态文件中**激活戳 `activatedAt` 最大**的那个
    （SessionStart 才写；没有戳的老状态按 0）；戳相同 / 都没有时按状态文件 mtime 定（只作平局裁决），再相同才按会话号。
    `proven`：调用方要据此断言「CLI 已换到别的会话」——最大的候选没有戳（老格式状态证明不了激活先后）就返回 None，不当作换了。**限度**：墙钟，时钟被往回拨时可能选错，之后的 SessionStart 会纠正。"""
    best: tuple[float, int, str] | None = None
    try:
        for path in cc.user_dir("state").glob("session-*.json"):
            try:
                data = json.loads(_read_small(path))
                stamp = data.get("activatedAt")
                found = (float(stamp) if type(stamp) in (int, float) else 0.0, path.stat().st_mtime_ns, data["session"]) if data.get("cli") == [cli, born] else None
            except (OSError, ValueError, KeyError, AttributeError):
                continue
            if found and isinstance(found[2], str) and (best is None or found > best):
                best = found
    except OSError:
        pass
    return best[2] if best and not (proven and best[0] == 0.0) else None


def beat_loop(
    source: str, session_id: str | None = None, cli: int = 0, *, sleep=time.sleep, clock=time.monotonic,
    born: str | None = None, gen: str | None = None, identify=None, wait: bool = False,
) -> None:
    """发心跳的进程的主体。`cli` = Claude Code 的 pid，`born` = 它的启动时刻（companion 由启动它的钩子给；
    monitor 自己从刚起它的 CLI 上取）。两种起法都有寿命上限 `BEAT_MAX_LIFETIME`（按圈数）：到了就退，会话还在的话
    下一个钩子事件补一个伴随进程；monitor 由 CLI 随会话重起。认不出 CLI（pid 或启动时刻缺）就不发。
    `END_STUCK` 是终态：返回 = 进程退出（两种起法一样）。"""
    born = _born(cli) if born is None and source == "monitor" else born
    budget = [BEAT_MAX_LIFETIME // BEAT_CHECK_SECONDS]
    if source != "monitor":
        _beat_session(session_id or "", source, cli, born, budget, sleep, clock, gen, wait=wait)
        return
    while cli and born is not None and budget[0] > 0 and _alive(cli, born):  # 认不出 Claude Code 就认不出会话：直接退，由伴随进程来
        budget[0] -= 1
        session_id = _session_of(cli, born)
        if session_id and _beat_session(session_id, source, cli, born, budget, sleep, clock, identify=identify) in (END_GONE, END_STUCK):
            return
        sleep(BEAT_CHECK_SECONDS)


def _silence() -> None:
    """monitor 的每一行输出都会被送进会话：把 stdout / stderr 整个接到 /dev/null（连子进程、解释器的告警一起）。"""
    null = os.open(os.devnull, os.O_WRONLY)
    for fd in (1, 2):
        os.dup2(null, fd)


def _beat_main(argv: list[str]) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--beat", action="store_true")
    parser.add_argument("--source", choices=("companion", "monitor"), default="companion")
    parser.add_argument("--session")
    parser.add_argument("--cli", type=int, default=0)
    parser.add_argument("--born")  # companion：钩子取的 CLI 启动时刻（没有 = 不监督）
    parser.add_argument("--gen")  # companion：钩子读到的状态归属号
    parser.add_argument("--wait", action="store_true")  # 另一个 CLI 恢复了会话：先等老的发心跳的放锁（有界）
    if "monitor" in argv:
        _silence()  # 先于解析参数：argparse 报错也不许出声
    args = parser.parse_args(argv)
    if os.name == "nt" or cc.beat_mode() != args.source:  # 静态配置：不是自己这条路就立刻、静悄悄地退出（off 也不等于任何一条）
        return
    if args.source == "companion":
        if args.born:
            beat_loop("companion", args.session, args.cli, born=args.born, gen=args.gen, wait=args.wait)
        return
    beat_loop("monitor", None, _cli_pid(), identify=_cli_identity)


def _dir_status(path: Path) -> str:
    """状态目录的现状，**只看不动**（`lstat`，不建、不改权限）。"""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return "还没有；钩子第一次跑时会建"
    except OSError:
        return "读不了"
    if stat.S_ISLNK(st.st_mode):
        return "是符号链接 → 钩子拒绝使用（不计相位、不发心跳）；修法：删掉链接"
    if not stat.S_ISDIR(st.st_mode):
        return "不是目录 → 钩子拒绝使用"
    mode = stat.S_IMODE(st.st_mode)
    if os.name != "nt" and st.st_uid != os.getuid():
        return f"权限 {mode:04o}，不属于你 → 钩子拒绝使用；修法：sudo chown -R \"$USER\" {_safe(path)} && chmod 700 {_safe(path)}"
    return f"权限 {mode:04o}，可用" + ("（钩子下次会把它修成 0700）" if mode & 0o077 else "")


def _doctor() -> int:
    """`--doctor`：「心跳到底有没有在发」一屏看完。**只读**：不建目录、不改权限、不建 / 不锁任何文件，缺什么就报「还没有」；绝不打印令牌。"""
    env = os.environ.get
    cfg = cc.user_dir("config") / cc.CONFIG_FILENAME
    config = cc.load_config()
    source = ("环境变量 COCKPIT_URL" if env("COCKPIT_URL") else "插件设置 CLAUDE_PLUGIN_OPTION_COCKPIT_URL"
              if env("CLAUDE_PLUGIN_OPTION_COCKPIT_URL") else f"配置文件 {_safe(cfg)}" if config["url"] else "没配")
    try:
        parts = urllib.parse.urlsplit(config["url"])  # 只取主机和端口：地址里万一带了 user:pass@ 也不外露
        host = _printable((parts.hostname or "-") + (f":{parts.port}" if parts.port else ""))
    except ValueError:
        host = "-"
    print(f"配置来源：{source}；服务器 {host}；令牌 {'有' if config['token'] else '无'}")
    state_dir = cc.user_dir("state")
    status = _dir_status(state_dir)
    print(f"状态目录：{_safe(state_dir)}（{status}）")
    chain, pid = [], os.getppid()
    for _ in range(ANCESTOR_DEPTH):
        info = _proc(pid) if pid > 1 else None
        if info is None:
            break
        chain.append(f"{pid}:{_printable(info[1])}")
        pid = info[0]
    probe = _cli_probe()
    cli = probe[0]
    print(f"祖先进程：{' → '.join(chain) or '（没有）'}")
    print(f"Claude Code：pid {cli or '认不出'}" + (f"，名字 {_printable((_proc(cli) or (0, '?'))[1])}，启动时刻{'有' if probe[1] else '无'}" if cli else ""))
    mode = cc.beat_mode()
    why = _unsupervised_reason(probe)
    print(f"心跳监督：{'可用' if not why and mode != 'off' else why or '已关（COCKPIT_BEAT=off）'}")
    print(f"心跳方式：{mode}（" + {"companion": "缺省：SessionStart 钩子起伴随进程；插件的 monitor 一起来就退出", "monitor": "显式：只有插件的 monitor 发，钩子不起伴随进程",
                              "off": "不发心跳"}[mode] + "）")
    newest, best = None, -1.0
    if state_dir.is_dir() and not state_dir.is_symlink():
        for p in state_dir.glob("session-*.json"):
            try:
                st = p.lstat()  # 不跟链接：悬空链接 / 读不了的条目跳过
            except OSError:
                continue
            if stat.S_ISREG(st.st_mode) and st.st_mtime > best:
                newest, best = p, st.st_mtime
    if newest is None:
        print("最新会话：还没有")
        return 0
    holder = None
    try:
        data, _cut = cc.read_regular(newest.with_suffix(".beat"), 256)  # 只读已有的 .beat；没有就是没有，不创建、不加锁
        words = data.decode("utf-8", errors="replace").split()
        if words and words[0].isascii() and words[0].isdecimal():  # 文件内容不可信：只认 pid 和两种已知起法
            pid = int(words[0])
            kind = f" {words[1]}" if len(words) > 1 and words[1] in ("companion", "monitor") else ""
            holder = f"记录的持有者 {pid}{kind}（进程{'还在' if _proc(pid) is not None else '已不在'}）"
    except OSError:
        pass
    print(f"最新会话的心跳：{holder or '没有记录（下一个钩子事件会补一个伴随进程）'}")
    return 0


STDIN_MAX = 1024 * 1024  # 钩子的事件 JSON 才几百字节；更大的不是 Claude Code 发的，当没有事件


def _read_stdin() -> str | None:
    """读事件 JSON：最多 `STDIN_MAX` 字节，且受总预算约束（POSIX：`select` 轮询，父进程一直不关管道也只等到预算用完）。
    超时 / 超限 → None（= 没有事件，exit 0）。Windows（或 stdin 没有文件描述符）退回普通的带上限读取，**没有时限**——已知限制。"""
    if sys.stdin is None:  # fd 0 被关了（`<&-`）：没有事件
        return None
    try:
        if os.name == "nt":
            raise OSError
        import select

        fd = sys.stdin.fileno()
    except (OSError, ValueError, AttributeError, ImportError):
        data = sys.stdin.read(STDIN_MAX + 1)
        return None if len(data) > STDIN_MAX else data
    chunks, size = [], 0
    while True:
        left = _remaining(HOOK_BUDGET)
        if left <= 0 or not select.select([fd], [], [], left)[0]:
            return None
        chunk = os.read(fd, 65536)
        if not chunk:
            return b"".join(chunks).decode("utf-8", errors="replace")
        size += len(chunk)
        if size > STDIN_MAX:
            return None
        chunks.append(chunk)


def main(start: float | None = None) -> int:
    global _deadline
    if "--doctor" in sys.argv[1:]:
        return _doctor()
    if "--beat" in sys.argv[1:]:
        try:
            _beat_main(sys.argv[1:])
        except BaseException:  # noqa: BLE001 — 含 argparse 的 SystemExit：没人看它的输出，出错就是这个会话没有心跳
            pass
        return 0
    now = time.monotonic()
    _deadline = max((now if start is None else start) + HOOK_BUDGET, now + HOOK_MIN_WORK)  # 总预算从进程启动起算，但启动之后至少留 HOOK_MIN_WORK（只有钩子路径设；心跳进程是长命的）
    at = cc.now_iso()  # 事件发生的那一刻，先于读 stdin / 网络
    try:
        payload = json.loads(_read_stdin() or "{}")
    except ValueError:
        payload = {}
    try:
        if not isinstance(payload, dict):
            payload = {}
        event = payload.get("hook_event_name")
        if event == "SessionStart":
            handle_session_start(payload)
        elif event == "SessionEnd":
            handle_session_end(payload)
        else:
            handle_phase(payload, at)
    except Exception as e:  # noqa: BLE001 — 钩子绝不能把异常抛给 Claude Code，见模块 docstring
        _warn(f"未预期的错误，忽略（{type(e).__name__}）")
    return 0  # 无论如何都成功退出：钩子不许拖慢或打断会话


if __name__ == "__main__":
    sys.exit(main(_T0))
