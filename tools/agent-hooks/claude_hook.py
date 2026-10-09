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
同一个循环两种起法：钩子起的脱离伴随进程（companion），或装成插件时 Claude Code 自己起的 monitor。

**纪律**（钩子绝不能拖慢或打断会话）：
- Claude Code 文档：`SessionEnd` 钩子默认预算只有 1.5 秒（`SessionStart` 是
  600 秒）。本模块自己的网络超时（`HOOK_TIMEOUT`）压到 1 秒，但加上 Python
  解释器启动、读写状态文件的开销，1.5 秒总预算并不宽松——**settings.json 里
  仍要给 SessionEnd 那条显式设 `"timeout": 5`**（见 README）留够冗余，
  不然偶尔会在我们的超时生效前，Claude Code 自己先把钩子进程杀了。
- 任何异常都吞掉，最后一律 `exit 0`；失败最多在 stderr 留一行——用户能
  看到，Claude 看不到（`SessionStart`/`SessionEnd` 都不能 block 会话，
  这行为在文档里也是这么写的）。配置文件被手改坏、cockpit 返回奇怪的
  形状，都只是"这次不计时"，不是"钩子出错"。
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import math
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import cockpit_client as cc  # noqa: E402

HOOK_TIMEOUT = 1.0  # 压在 SessionEnd 默认 1.5s 预算之内；settings.json 里调大整体超时时无需跟着改

# SessionEnd 的 `reason` 说的是「会话为什么结束」，不是「这次工作成没成」——
# Claude Code 不提供成败信号，缺省当正常完成（done）；只有 prompt_input_exit
# （在输入框按 Ctrl-C/Ctrl-D 主动退出）算用户中途打断。
_REASON_TO_OUTCOME = {"prompt_input_exit": "cancelled"}


def _warn(msg: str) -> None:
    print(f"agent-hooks: {msg}", file=sys.stderr)


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


def _ensure_dir(path: Path) -> None:
    """状态目录只给本人：新建时 0700（里面的状态文件记着 cwd、transcript 路径、地址、pid）。已存在的不改。"""
    path.mkdir(parents=True, exist_ok=True, mode=0o700)


STATE_MAX_BYTES = 64 * 1024  # 状态文件就几百字节；更大的不是我们写的，不读


def _read_small(path: Path) -> str:
    """读状态文件，带大小上限（状态目录里的东西当不可信：别的进程也写得了）。超了按「读不了」算。"""
    with open(path, "rb") as f:
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
    不会有陈旧锁；锁文件本身留着无害。等锁有上限，超时或锁不了就不带锁继续——钩子绝不能为锁卡住会话
    （残余：这时回到「检查后写」的窄窗口，最坏是一条要等服务端兜底超时才收的 run）。"""
    wait = LOCK_WAIT if wait is None else wait
    f = None
    try:
        path = _state_file(session_id).with_suffix(".lock")
        _ensure_dir(path.parent)
        f = open(path, "a+b")
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
                    _warn("状态锁等待超时，不带锁继续")
                    break
                time.sleep(0.02)
    except (OSError, ImportError):
        held = False
    try:
        yield
    finally:
        if f is not None:
            with contextlib.suppress(Exception):
                if held:
                    unlock()
            with contextlib.suppress(Exception):
                f.close()


def _write_state(session_id: str, state: dict) -> None:
    path = _state_file(session_id)
    try:
        _ensure_dir(path.parent)
        # 每个进程一个临时名：异步相位钩子是并行跑的，共用一个 .tmp 会互相写花
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        with os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as f:  # 只给本人
            f.write(json.dumps(state))
        os.replace(tmp, path)  # 原子替换：不会有人读到"写了一半"的文件
    except OSError:
        pass  # 状态文件写不了也不该拖累会话；下次 SessionEnd 找不到就跳过 stop


def _save_run_id(
    session_id: str, run_id: str, last_phase: str | None = None, label: str | None = None, payload: dict | None = None,
) -> None:
    state: dict = {"runId": run_id}
    if payload is not None:
        state.update(_beat_facts(session_id, payload))
    if last_phase:
        state["lastPhase"] = last_phase  # v0.3：PostToolUse 靠它决定报不报（见 _phase_for）
    if label:
        state["label"] = label  # v0.4：报过的泳道名；会话改名后靠它发现「变了」（见 handle_phase）
    _write_state(session_id, state)


def _read_state(session_id: str) -> dict | None:
    """只读，成功时不删——SessionEnd 得先确认 stop 报成功了才能删（见
    handle_session_end），不然报失败/超时/钩子被杀死的时候，这条记录跟着
    没了，run 就再也关不掉了，只能等 cockpit 服务端自己的兜底超时
    （数小时量级）才会收尾。

    但内容本身读不出可用 runId 的情况要分两种：
    - 文件压根不存在 / 权限问题之类读不了（`FileNotFoundError`/`OSError`）——
      不是"内容坏了"，可能只是还没开始过、或者暂时的 I/O 问题，别删，留给
      下次再试。
    - 文件存在但内容不是合法 JSON，或者形状不对（不是 `{"runId": "..."}`
      这种结构）——这份文件已经没有任何可用信息了，留着就是垃圾，删掉，
      不然它会一直躺在 state 目录里，SessionEnd 每次都白读一遍。
    """
    path = _state_file(session_id)
    try:
        raw = _read_small(path)
    except OSError:  # 含 FileNotFoundError
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        _delete_run_id(session_id)
        return None
    if not isinstance(data, dict) or not isinstance(data.get("runId"), str):
        _delete_run_id(session_id)
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
        config, task_id, cc.default_agent_name(cwd), "claude-code", timeout=HOOK_TIMEOUT,
        model=payload.get("model"),  # 文档：只有 SessionStart 会带，且不保证有
        phase=phase, label=label, match=match,
        client_key=hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:32],
        project_id=project_id,
    )
    return result.get("runId"), label


def handle_session_start(payload: dict) -> None:
    session_id = payload.get("session_id")
    if not session_id:
        return
    try:
        # 会话开着、还没说话 = idle。恢复的会话（--resume）transcript 里已经有名字
        run_id, label = _start(payload, session_id, "idle", cc.session_title(payload.get("transcript_path")))
    except Exception as e:  # noqa: BLE001 — 配置/网络任何一步出岔子都只是"这次不计时"
        category = e if isinstance(e, cc.CockpitError) else "配置错误"
        _warn(f"SessionStart 上报失败，本次会话不计时（{category}）")
        return
    if run_id:
        with _session_lock(session_id):
            _save_run_id(session_id, run_id, "idle", label, payload)
        _spawn_beat(session_id, None)


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
    先写状态、再发请求；并行钩子读到旧状态的窗口里最坏是黄灯留到下一次 Stop——已知上限，不加锁。"""
    session_id = payload.get("session_id")
    if not session_id:
        return
    state = _read_state(session_id)
    if not state:
        return  # 没开过 run（SessionStart 没报成）：这次会话不画相位
    _spawn_beat(session_id, state)  # 没人在发心跳（到了寿命 / 被杀 / monitor 没来 / 钩子升级前开的会话）就补一个伴随进程
    decision = _phase_for(payload, state.get("lastPhase"))
    if decision is None:
        return
    phase, detail, reply = decision
    with _session_lock(session_id):
        if _same_live_run(session_id, state["runId"]):  # SessionEnd 已收尾：别把状态写回来
            _write_state(session_id, {**state, "lastPhase": phase})
    closed = False
    try:
        config = cc.load_config()
        result = cc.phase_run(config, state["runId"], phase, at, detail=detail, reply=reply, timeout=HOOK_TIMEOUT)
        closed = result.get("reason") == "closed"  # 服务端已收掉这条（失联 / 超时）：会话还活着，下面重开
    except Exception as e:  # noqa: BLE001 — 相位只是记录，失败只留一行固定分类
        category = e if isinstance(e, cc.CockpitError) else "配置错误"
        _warn(f"{payload.get('hook_event_name')} 相位上报失败（{category}）")
    # 会话改名（/rename）不触发任何钩子：趁这次本来就要发请求，看一眼 transcript 末尾，名字变了才多发一次 start。
    # 比的是「有效名字」（标题，没有就是目录名）：标题被清空时要退回目录名。没改名时零额外请求。
    title = cc.session_title(payload.get("transcript_path"))
    if closed or cc.lane_names(payload.get("cwd"), title)[0] != state.get("label"):
        _relabel(payload, session_id, phase, title, state)


def _same_live_run(session_id: str, run_id: str) -> bool:
    cur = _read_state(session_id)
    return bool(cur) and cur["runId"] == run_id


def _relabel(payload: dict, session_id: str, phase: str, title: str | None, state: dict) -> None:
    """改名（v0.4 心跳起也用来在原 run 被服务端收掉后重开）。改名只许改名，绝不能新开 run：异步钩子可能在 SessionEnd 停掉 run、删了状态之后才跑到这里，
    这时 start 会（clientKey 已停）新建一条 run，且再没有结束钩子去关它。所以发前发后各查一次状态，
    返回的 runId 不同时：状态还在且仍是原 runId（会话活着、原 run 被服务端收了）就换成新的；状态没了或已是别的 runId 就当会话已结束，关掉多出来的 run、不写状态。"""
    with _session_lock(session_id):
        _relabel_locked(payload, session_id, phase, title, state)


def _relabel_locked(payload: dict, session_id: str, phase: str, title: str | None, state: dict) -> None:
    if not _same_live_run(session_id, state["runId"]):
        return
    try:
        run_id, label = _start(payload, session_id, phase, title)
        if run_id and _same_live_run(session_id, state["runId"]):
            # 状态还在、还是发前读到的那条：会话活着。同一条 run 改名；不同 = 服务端已收掉原 run，跟着换
            _write_state(session_id, {**state, "runId": run_id, "lastPhase": phase, "label": label})
        elif run_id and run_id != state["runId"]:  # 状态没了 / 被别人换了：SessionEnd 等抢先了，关掉多出来的 run
            cc.stop_run(cc.load_config(), run_id, "cancelled", timeout=HOOK_TIMEOUT)
    except Exception as e:  # noqa: BLE001 — 改名没报上去：下一个事件再试
        category = e if isinstance(e, cc.CockpitError) else "配置错误"
        _warn(f"会话改名上报失败（{category}）")


def handle_session_end(payload: dict) -> None:
    session_id = payload.get("session_id")
    if not session_id:
        return
    with _session_lock(session_id):  # 读状态 → stop → 删状态 一整段持锁：停的是锁内读到的那条 run
        _session_end_locked(session_id, _REASON_TO_OUTCOME.get(payload.get("reason"), "done"))


def _session_end_locked(session_id: str, outcome: str) -> None:
    run_id = _read_run_id(session_id)
    if not run_id:
        return
    try:
        config = cc.load_config()
        cc.stop_run(config, run_id, outcome, timeout=HOOK_TIMEOUT)
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
# 一个循环（`_beat_session`），两种起法（`beat_loop` 的 source）：
#   companion —— 钩子起的脱离进程（自己一个 session、stdio 全接 /dev/null）。会话号由钩子给；最多活 BEAT_MAX_LIFETIME，
#                到点就退，会话还在的话下一个钩子事件补一个。
#   monitor   —— 装成 Claude Code 插件时由 CLI 起、随会话结束（monitors/monitors.json）。CLI 不给 monitor 会话号，
#                也不给插件设置：它按「同一个 Claude Code 进程」到状态目录里认会话，/clear 换了会话就换着跟。
#                **一个字都不往 stdout / stderr 写**——monitor 的输出会被当成通知送进会话、叫醒模型。
# 循环只做三件事：每 BEAT_CHECK_SECONDS 看一眼 Claude Code 还在不在（不在了：持会话锁报 stop、删状态、退出）；
# 状态文件没了（SessionEnd 收过尾）就放手；按服务端给的间隔发心跳（不持锁、3 秒时限、失败不管）。
# 一个会话同一时刻只有一个在发：两种起法抢同一把锁（`_beat_lock`），先到先得，后到的安静地让开。
# Windows 上都不起（没有可靠又不伤人的「这个 pid 还活着吗」）：那里的运行不声明心跳，照旧只有遗忘超时兜底。
BEAT_CHECK_SECONDS = 30
BEAT_MAX_LIFETIME = 24 * 3600
BEAT_TIMEOUT = cc.DEFAULT_TIMEOUT
MONITOR_GRACE_SECONDS = 90  # auto：让 monitor 先来；会话开始这么久还没人发心跳，钩子才补一个伴随进程
GONE_OUTCOME = "cancelled"  # Claude Code 没发 SessionEnd 就没了（崩了 / 被杀 / 终端被关）
ANCESTOR_DEPTH = 6
_SHELLS = frozenset({"sh", "bash", "dash", "zsh", "fish", "ksh", "ash", "busybox", "env"})


def _proc(pid: int) -> tuple[int, str, str | None] | None:
    """`(父 pid, 进程名, 启动时刻)`；不存在 / 已是僵尸 / 查不了 → None。Linux 读 /proc（带启动时刻，pid 被复用也认得出）；
    别的 POSIX 问 `ps`（没有启动时刻）。"""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace")
        rest = stat[stat.rindex(")") + 2:].split()  # 进程名里可以有空格和括号：从最后一个 ) 之后数
        if rest[0] == "Z":
            return None
        return int(rest[1]), stat[stat.index("(") + 1:stat.rindex(")")], rest[19]
    except (OSError, ValueError, IndexError):
        pass
    try:
        out = subprocess.run(["ps", "-o", "ppid=,comm=", "-p", str(pid)], capture_output=True, text=True,
                             stdin=subprocess.DEVNULL, timeout=2).stdout.split(None, 1)
        return int(out[0]), os.path.basename(out[1].strip()).lstrip("-"), None
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return None


def _cli_pid() -> int:
    """本进程的祖先里哪个是 Claude Code：名字以 claude 开头的；没有就取第一个不是 shell 的
    （钩子与 monitor 都经 shell 起，直接父进程多半是那个 shell）。0 = 认不出。"""
    pid, first = os.getppid(), 0
    for _ in range(ANCESTOR_DEPTH):
        info = _proc(pid) if pid > 1 else None
        if info is None:
            break
        if info[1].startswith("claude"):
            return pid
        if not first and info[1] not in _SHELLS:
            first = pid
        pid = info[0]
    return first


def _born(pid: int) -> str | None:
    return (_proc(pid) or (0, "", None))[2] if pid else None


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


def _beat_facts(session_id: str, payload: dict) -> dict:
    """SessionStart 时记进状态文件、给发心跳的进程用的几样（**只存本机，不上报**）：
    运行被服务端收掉后重开要的 cwd / transcript；monitor 认会话要的会话号与 Claude Code 的 (pid, 启动时刻)；
    monitor 拿不到插件设置，所以记下钩子用的地址、钩子有没有带令牌（令牌本身不落盘）；`at` 给 auto 的宽限用。"""
    facts: dict = {"session": session_id, "at": time.time()}
    for key, source in (("cwd", "cwd"), ("transcript", "transcript_path")):
        if isinstance(payload.get(source), str):
            facts[key] = payload[source]
    try:
        cli = _cli_pid()
        config = cc.load_config()
        facts.update(cli=[cli, _born(cli)], url=config["url"], auth=bool(config["token"]))
    except Exception:  # noqa: BLE001 — 少了这几样只是 monitor 认不出这个会话
        pass
    return facts


def _plugin_monitor() -> bool:
    """钩子是不是从一个带 monitor 的插件里跑起来的。"""
    root = os.environ.get("CLAUDE_PLUGIN_ROOT")
    return bool(root) and (Path(root) / "monitors" / "monitors.json").is_file()


def _beat_lock(session_id: str):
    """「一个会话一个发心跳的」：状态目录里 .beat 文件上的独占 flock，发多久拿多久——进程死了内核放锁，
    没有陈旧 pidfile 要清。拿到 → 打开着的文件（持有者写进自己的 pid 和起法，给人看）；别人拿着 → None。"""
    import fcntl

    path = _state_file(session_id).with_suffix(".beat")
    _ensure_dir(path.parent)
    f = open(path, "a+", encoding="utf-8")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return None
    return f


def _spawn_beat(session_id: str, state: dict | None) -> None:
    """该由伴随进程来、又没人在发心跳，就起一个，立刻返回（不等它）。起不来只是「这个会话没有心跳」。
    `state` = 钩子手里的状态（SessionStart 刚开跑时给 None）。"""
    try:
        mode = cc.beat_mode()
        if os.name == "nt" or mode in ("off", "monitor"):
            return
        if mode == "auto" and _plugin_monitor() and (state is None or _in_grace(state.get("at"))):
            return  # 让 monitor 先来
        cli = _cli_pid()
        st = state or _read_state(session_id)
        if not cli or (st and not _usable(st)):
            return  # 认不出 Claude Code（死了看不出来）/ 发了也发不出去：不起，免得每个钩子事件起一个就退的进程
        probe = _beat_lock(session_id)
        if probe is None:
            return  # 已经有人在发
        probe.close()  # 两个钩子同时走到这里会各起一个：伴随进程自己再抢一次锁，输的那个直接退
        subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "--beat", "--source", "companion",
             "--session", session_id, "--cli", str(cli)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True, close_fds=True, cwd=str(_state_file(session_id).parent),
        )
    except Exception:  # noqa: BLE001 — 钩子绝不能因为它出错
        pass


def _in_grace(at) -> bool:
    """会话是不是刚开始（auto 留给 monitor 的宽限）。`at` 来自状态文件，当不可信：不是有限的数、在将来、
    或已过宽限，都算「不在宽限里」——坏值只会让伴随进程早点补上，不会让它永远不来。"""
    if type(at) not in (int, float) or not math.isfinite(at):
        return False
    return 0 <= time.time() - at < MONITOR_GRACE_SECONDS


def _usable(state: dict) -> bool:
    """这个进程发得了这个会话的心跳吗。monitor 没有钩子的环境：钩子带了令牌而自己没有（令牌只填在插件设置里）
    就发不了——让开，由伴随进程来。地址同理拿不到时用钩子记在状态文件里的那个，但**只在自己手里没有令牌时**：
    状态文件别的进程也写得了，令牌绝不发往从那里读来的地址。"""
    config = cc.load_config()
    remembered = state.get("url")
    if (not config["url"] and not config["token"] and not os.environ.get("COCKPIT_TOKEN")  # 环境里的令牌会与补进去的地址配成对
            and isinstance(remembered, str) and remembered and not state.get("auth")):
        os.environ["COCKPIT_URL"] = remembered  # 只改本进程：下面的 load_config / 重开都读得到
        config = cc.load_config()
    return bool(config["url"]) and (bool(config["token"]) or not state.get("auth"))


def _beat_once(session_id: str, state: dict, source: str) -> float | None:
    """发一次心跳，返回下一次隔多少秒（None = 别再发了）。服务端说这条运行已结束（机器睡过头被判了失联）而会话还在 → 重开一条。"""
    interval, closed = cc.beat(cc.load_config(), state["runId"], BEAT_TIMEOUT, source)
    if closed and state.get("cwd"):
        payload = {"cwd": state["cwd"], "transcript_path": state.get("transcript")}
        _relabel(payload, session_id, state.get("lastPhase") or "idle", cc.session_title(state.get("transcript")), state)
        # 重开成了：新的那条还没声明心跳，下一轮就发。没成：closed 过一会儿再试；服务端不认这个运行（404，
        # 库重置 / 换了租户）就只重开这一次，不成就停，等下一个钩子事件再补一个发心跳的（有界，不是每个间隔都敲）
        if _read_run_id(session_id) != state["runId"]:
            return 0
        return None if closed == cc.BEAT_MISSING else cc.HEARTBEAT_MIN
    return None if closed == cc.BEAT_MISSING else interval


def _beat_session(session_id: str, source: str, cli: int, born: str | None, until: float, sleep, clock) -> bool:
    """给一个会话发心跳，直到它结束 / 到 `until` / 轮不到自己。True = Claude Code 没了（已替它收尾）。"""
    state = _read_state(session_id)
    if not cli or not state or not _usable(state):
        return False  # 认不出 Claude Code = 死了也看不出来：不发（agent.lane.v1「发不出死亡就别声明心跳」），运行照旧 12 小时兜底
    lock = _beat_lock(session_id)
    if lock is None:
        return False  # 另一种起法已经在给它发
    with lock:
        lock.truncate(0)
        lock.write(f"{os.getpid()} {source}")
        lock.flush()
        next_beat = clock()  # 第一下马上发：声明这条运行会发心跳。用墙钟排：机器睡醒后马上补一下
        while clock() < until:
            state = _read_state(session_id)
            if not state:
                return False  # SessionEnd 收过尾了
            if cli and not _alive(cli, born):
                with _session_lock(session_id):
                    _session_end_locked(session_id, GONE_OUTCOME)
                return True
            if clock() >= next_beat:
                wait = _beat_once(session_id, state, source)
                if wait is None:
                    return False  # 重开不成：停，下一个钩子事件再起
                next_beat = clock() + wait
            sleep(BEAT_CHECK_SECONDS)
    return False


def _session_of(cli: int, born: str | None) -> str | None:
    """这个 Claude Code 进程此刻的会话：状态目录里记着同一个 (pid, 启动时刻) 的状态文件中最新的那个。"""
    best: tuple[float, str] | None = None
    try:
        for path in cc.user_dir("state").glob("session-*.json"):
            try:
                data = json.loads(_read_small(path))
                found = (path.stat().st_mtime, data["session"]) if data.get("cli") == [cli, born] else None
            except (OSError, ValueError, KeyError, AttributeError):
                continue
            if found and isinstance(found[1], str) and (best is None or found > best):
                best = found
    except OSError:
        pass
    return best[1] if best else None


def beat_loop(source: str, session_id: str | None = None, cli: int = 0, *, sleep=time.sleep, clock=time.time) -> None:
    """发心跳的进程的主体。`cli` = Claude Code 的 pid（companion 认不出时给 0：只看状态文件）。"""
    born = _born(cli)
    if source != "monitor":
        _beat_session(session_id or "", source, cli, born, clock() + BEAT_MAX_LIFETIME, sleep, clock)
        return
    while cli and _alive(cli, born):  # 认不出 Claude Code 就认不出会话：直接退，由伴随进程来
        session_id = _session_of(cli, born)
        if session_id and _beat_session(session_id, source, cli, born, float("inf"), sleep, clock):
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
    if "monitor" in argv:
        _silence()  # 先于解析参数：argparse 报错也不许出声
    args = parser.parse_args(argv)
    mode = cc.beat_mode()
    if os.name == "nt" or mode == "off" or mode not in ("auto", args.source):
        return
    beat_loop(args.source, args.session, args.cli if args.source == "companion" else _cli_pid())


def main() -> int:
    if "--beat" in sys.argv[1:]:
        try:
            _beat_main(sys.argv[1:])
        except BaseException:  # noqa: BLE001 — 含 argparse 的 SystemExit：没人看它的输出，出错就是这个会话没有心跳
            pass
        return 0
    at = cc.now_iso()  # 事件发生的那一刻，先于读 stdin / 网络
    try:
        payload = json.loads(sys.stdin.read() or "{}")
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
    sys.exit(main())
