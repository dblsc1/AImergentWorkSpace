#!/usr/bin/env python3
"""Claude Code 会话钩子：SessionStart 开一条 cockpit run，SessionEnd 关掉它。

在 settings.json 里，`SessionStart` 与 `SessionEnd` 两个事件都指向**同一个**
脚本——用 stdin JSON 里的 `hook_event_name` 分流，省得配两份路径。见 README
里的配置片段。

**纪律**（钩子绝不能拖慢或打断会话）：
- Claude Code 文档：`SessionEnd` 钩子默认预算只有 1.5 秒（`SessionStart` 是
  600 秒）。本模块自己的网络超时（`HOOK_TIMEOUT`）压到 1 秒，但加上 Python
  解释器启动、读写状态文件的开销，1.5 秒总预算并不宽松——**settings.json 里
  仍要给 SessionEnd 那条显式设 `"timeout": 5`**（见 README）留够冗余，
  不然偶尔会在我们的超时生效前，Claude Code 自己先把钩子进程杀了。
- 任何异常都吞掉，最后一律 `exit 0`；失败最多在 stderr 留一行——用户能
  看到，Claude 看不到（`SessionStart`/`SessionEnd` 都不能 block 会话，
  这行为在文档里也是这么写的）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import cockpit_client as cc  # noqa: E402

HOOK_TIMEOUT = 1.0  # 压在 SessionEnd 默认 1.5s 预算之内；settings.json 里调大整体超时时无需跟着改
STATE_FILENAME = "agent-hooks-sessions.json"

# SessionEnd 的 `reason` 说的是「会话为什么结束」，不是「这次工作成没成」——
# Claude Code 不提供成败信号，缺省当正常完成（done）；只有 prompt_input_exit
# （在输入框按 Ctrl-C/Ctrl-D 主动退出）算用户中途打断。
_REASON_TO_OUTCOME = {"prompt_input_exit": "cancelled"}


def _state_path() -> Path:
    return cc.user_dir("state") / STATE_FILENAME


def _load_state() -> dict:
    try:
        return json.loads(_state_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError, OSError):
        return {}


def _save_state(state: dict) -> None:
    path = _state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state), encoding="utf-8")
    except OSError:
        pass  # 状态文件写不了也不该拖累会话；下次 SessionEnd 找不到 runId 就跳过 stop


def _warn(msg: str) -> None:
    print(f"agent-hooks: {msg}", file=sys.stderr)


def handle_session_start(payload: dict) -> None:
    session_id = payload.get("session_id")
    if not session_id:
        return
    cwd = payload.get("cwd")
    config = cc.load_config()
    task_id = cc.resolve_task(cwd=cwd, config=config)
    agent = cc.default_agent_name(cwd)
    model = payload.get("model")  # 文档：只有 SessionStart 会带，且不保证有
    try:
        result = cc.start_run(config, task_id, agent, "claude-code", model=model, timeout=HOOK_TIMEOUT)
    except cc.CockpitError as e:
        _warn(f"SessionStart 上报失败，本次会话不计时（{e}）")
        return
    run_id = result.get("runId")
    if not run_id:
        return
    state = _load_state()
    state[session_id] = run_id
    _save_state(state)


def handle_session_end(payload: dict) -> None:
    session_id = payload.get("session_id")
    if not session_id:
        return
    state = _load_state()
    run_id = state.pop(session_id, None)
    _save_state(state)  # 不管下面 stop 成不成，先把这条记录清掉，状态文件不会越攒越大
    if not run_id:
        return
    outcome = _REASON_TO_OUTCOME.get(payload.get("reason"), "done")
    config = cc.load_config()
    try:
        cc.stop_run(config, run_id, outcome, timeout=HOOK_TIMEOUT)
    except cc.CockpitError as e:
        _warn(f"SessionEnd 上报失败（{e}）")


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        payload = {}
    try:
        event = payload.get("hook_event_name")
        if event == "SessionStart":
            handle_session_start(payload)
        elif event == "SessionEnd":
            handle_session_end(payload)
    except Exception as e:  # noqa: BLE001 — 钩子绝不能把异常抛给 Claude Code，见模块 docstring
        _warn(f"未预期的错误，忽略（{e}）")
    return 0  # 无论如何都成功退出：钩子不许拖慢或打断会话


if __name__ == "__main__":
    sys.exit(main())
