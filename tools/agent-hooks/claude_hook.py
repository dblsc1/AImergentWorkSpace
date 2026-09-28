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


def _save_run_id(session_id: str, run_id: str) -> None:
    path = _state_file(session_id)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"runId": run_id}), encoding="utf-8")
        os.replace(tmp, path)  # 原子替换：不会有人读到"写了一半"的文件
    except OSError:
        pass  # 状态文件写不了也不该拖累会话；下次 SessionEnd 找不到就跳过 stop


def _pop_run_id(session_id: str) -> str | None:
    """取出并删掉这个会话的 runId 记录（SessionEnd 用；用完即清，不会越攒越多）。"""
    path = _state_file(session_id)
    run_id = None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            run_id = data.get("runId")
    except (FileNotFoundError, ValueError, OSError):
        run_id = None
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
    return run_id if isinstance(run_id, str) else None


def handle_session_start(payload: dict) -> None:
    session_id = payload.get("session_id")
    if not session_id:
        return
    try:
        cwd = payload.get("cwd")
        config = cc.load_config()
        task_id = cc.resolve_task(cwd=cwd, config=config)
        agent = cc.default_agent_name(cwd)
        model = payload.get("model")  # 文档：只有 SessionStart 会带，且不保证有
        result = cc.start_run(config, task_id, agent, "claude-code", model=model, timeout=HOOK_TIMEOUT)
    except Exception as e:  # noqa: BLE001 — 配置/网络任何一步出岔子都只是"这次不计时"
        category = e if isinstance(e, cc.CockpitError) else "配置错误"
        _warn(f"SessionStart 上报失败，本次会话不计时（{category}）")
        return
    run_id = result.get("runId")
    if run_id:
        _save_run_id(session_id, run_id)


def handle_session_end(payload: dict) -> None:
    session_id = payload.get("session_id")
    if not session_id:
        return
    run_id = _pop_run_id(session_id)
    if not run_id:
        return
    outcome = _REASON_TO_OUTCOME.get(payload.get("reason"), "done")
    try:
        config = cc.load_config()
        cc.stop_run(config, run_id, outcome, timeout=HOOK_TIMEOUT)
    except Exception as e:  # noqa: BLE001 — 同上，绝不能是"会话结束不了"的理由
        category = e if isinstance(e, cc.CockpitError) else "配置错误"
        _warn(f"SessionEnd 上报失败（{category}）")


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
        _warn(f"未预期的错误，忽略（{type(e).__name__}）")
    return 0  # 无论如何都成功退出：钩子不许拖慢或打断会话


if __name__ == "__main__":
    sys.exit(main())
