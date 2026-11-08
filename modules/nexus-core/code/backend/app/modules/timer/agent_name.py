"""代理名字（契约 hive.stage.v1「代理名」）：人起的名字规则（同 place 规则的代理键）+ 对话名清洗。"""

from __future__ import annotations

from ..planner.errors import NotFoundError
from . import agent_place, repo

#: 终端 / 系统工具名：不能当对话名（比较时去掉空白、连字符与大小写）。
SYSTEM_TOOL_NAMES = frozenset(
    n.replace(" ", "").replace("-", "").lower() for n in (
        "gnome-terminal", "gnome-terminal-server", "ptyxis", "konsole", "xterm", "uxterm", "rxvt", "urxvt", "terminal",
        "terminator", "tilix", "alacritty", "kitty", "wezterm", "wezterm-gui", "iTerm2", "iTerm", "Windows Terminal",
        "cmd", "cmd.exe", "powershell", "pwsh", "Command Prompt", "xfce4-terminal", "mate-terminal",
        "lxterminal", "foot", "hyper", "warp", "terminal.app",
    ))


def _is_tool_name(text: str) -> bool:
    return text.replace(" ", "").replace("-", "").lower() in SYSTEM_TOOL_NAMES


def conversation(label: str | None, match: str | None) -> str | None:
    """label（非空、不是系统工具名）→ match（终端标题）→ None。"""
    for cand in (label, match):
        cand = " ".join(cand.split()) if cand else ""
        if cand and not _is_tool_name(cand):
            return cand
    return None


def name_rules(user: str) -> dict[str, str]:
    return {r["agent"]: r["name"] for r in repo.name_rules(user)}


def set_name(run_id: str, name: str | None, user: str) -> dict:
    run = repo.get_agent_run(user, run_id)
    stored = None if run else agent_place._finished_event(user, run_id)  # noqa: SLF001
    if run is None and stored is None:
        raise NotFoundError(f"代理运行不存在：{run_id!r}")
    agent = run["agent"] if run else (stored.get("data") or {}).get("agent")
    name = " ".join(name.split()) if name else None
    if name:
        repo.upsert_name_rule(user, agent, name)
    else:
        repo.delete_name_rule(user, agent)
    return {"runId": run_id, "agentKey": agent, "name": name}
