#!/usr/bin/env python3
"""Claude Code 会话钩子：SessionStart 开一条 cockpit run，SessionEnd 关掉它；
v0.3 起中间的事件报**相位**（在干活 / 等你 / 空闲 / 出错，映射表见 README「相位」节）。

在 settings.json 里，所有事件都指向**同一个**脚本——用 stdin JSON 里的
`hook_event_name` 分流，省得配多份路径。见 README 里的配置片段。
相位钩子配成 `"async": true`（绝不让 Claude 等它）；SessionStart 保持同步（先存好 runId）。
**不读** `prompt`、通知的 `message`、工具参数：只报相位、时刻、短标签。
v0.4：泳道名用会话的名字——从 `transcript_path` 末尾只取 `custom-title` 记录的标题（`cc.session_title`），对话内容不读。

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

import hashlib
import json
import os
import sys
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


def _write_state(session_id: str, state: dict) -> None:
    path = _state_file(session_id)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # 每个进程一个临时名：异步相位钩子是并行跑的，共用一个 .tmp 会互相写花
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(state), encoding="utf-8")
        os.replace(tmp, path)  # 原子替换：不会有人读到"写了一半"的文件
    except OSError:
        pass  # 状态文件写不了也不该拖累会话；下次 SessionEnd 找不到就跳过 stop


def _save_run_id(session_id: str, run_id: str, last_phase: str | None = None, label: str | None = None) -> None:
    state: dict = {"runId": run_id}
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
        raw = path.read_text(encoding="utf-8")
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
        _save_run_id(session_id, run_id, "idle", label)


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
    decision = _phase_for(payload, state.get("lastPhase"))
    if decision is None:
        return
    phase, detail, reply = decision
    _write_state(session_id, {**state, "lastPhase": phase})
    try:
        config = cc.load_config()
        cc.phase_run(config, state["runId"], phase, at, detail=detail, reply=reply, timeout=HOOK_TIMEOUT)
    except Exception as e:  # noqa: BLE001 — 相位只是记录，失败只留一行固定分类
        category = e if isinstance(e, cc.CockpitError) else "配置错误"
        _warn(f"{payload.get('hook_event_name')} 相位上报失败（{category}）")
    # 会话改名（/rename）不触发任何钩子：趁这次本来就要发请求，看一眼 transcript 末尾，名字变了才多发一次 start。
    # 比的是「有效名字」（标题，没有就是目录名）：标题被清空时要退回目录名。没改名时零额外请求。
    title = cc.session_title(payload.get("transcript_path"))
    if cc.lane_names(payload.get("cwd"), title)[0] != state.get("label"):
        _relabel(payload, session_id, phase, title, state)


def _same_live_run(session_id: str, run_id: str) -> bool:
    cur = _read_state(session_id)
    return bool(cur) and cur["runId"] == run_id


def _relabel(payload: dict, session_id: str, phase: str, title: str | None, state: dict) -> None:
    """改名只许改名，绝不能新开 run：异步钩子可能在 SessionEnd 停掉 run、删了状态之后才跑到这里，
    这时 start 会（clientKey 已停）新建一条 run，且再没有结束钩子去关它。所以发前发后各查一次状态，
    拿回的 runId 不是原来那条、或状态已没了，就当会话已结束：立刻关掉多出来的 run、不写状态。"""
    if not _same_live_run(session_id, state["runId"]):
        return
    try:
        run_id, label = _start(payload, session_id, phase, title)
        if run_id and run_id != state["runId"]:  # 新开出来的：关掉，不跟着换
            cc.stop_run(cc.load_config(), run_id, "cancelled", timeout=HOOK_TIMEOUT)
        elif run_id and _same_live_run(session_id, run_id):
            _write_state(session_id, {**state, "lastPhase": phase, "label": label})
    except Exception as e:  # noqa: BLE001 — 改名没报上去：下一个事件再试
        category = e if isinstance(e, cc.CockpitError) else "配置错误"
        _warn(f"会话改名上报失败（{category}）")


def handle_session_end(payload: dict) -> None:
    session_id = payload.get("session_id")
    if not session_id:
        return
    run_id = _read_run_id(session_id)
    if not run_id:
        return
    outcome = _REASON_TO_OUTCOME.get(payload.get("reason"), "done")
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


def main() -> int:
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
