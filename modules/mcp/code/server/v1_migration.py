"""v1 的 23 个工具名（mcp.tools.v2 第五节）：不调下游、不回数据，只回一段迁移说明和换好参数的新调用。"""

from __future__ import annotations

from datetime import datetime

from specs import V1_USE

NAMES = frozenset(V1_USE)


def _day(raw):
    """v1 的带偏移时刻 → 它自己偏移下的日期；转不了返回 None。"""
    try:
        return datetime.fromisoformat(raw).date().isoformat()
    except (TypeError, ValueError):
        return None


def _pick(a: dict, *keys: str) -> dict:
    return {k: a[k] for k in keys if a.get(k) is not None}


def _history(who: str, detail: str | None = None):
    def conv(a):
        args = {**{k: a[k] for k in ("fromDate", "toDate") if isinstance(a.get(k), str)}, "who": who, **({"detail": detail} if detail else {})}
        return args, ""
    return conv


def _from_to(a):
    args = {"who": "human", "detail": "segments"}
    for src, dst in (("from", "fromDate"), ("to", "toDate")):
        if _day(a.get(src)):
            args[dst] = _day(a[src])
    return args, "v2 按日期查，时刻已换成日期。"


def _tree(depth):
    return lambda a: ({"depth": depth, **_pick(a, "includeDone")}, "分页、includeEphemeral 没有了（v2 不分页，临时任务照列）。")


def _rules(a):
    return {}, "v1 的规则带 taskId；v2 先把窗口收进块（本工具），再用 CRUD_Windowsblock_Classify_Rules 把块记到任务，参数没法直接换。"


def _ignore(a):
    args = {"ops": [{"op": "add", "ignore": True, **{k: a[k] for k in ("app",) if a.get(k)},
                     **({"title": a["titleContains"]} if a.get("titleContains") else {})}]}
    return {**args, **_pick(a, "reason")}, ""


def _create(kind, parent):
    def conv(a):
        args = {"op": "create", "kind": kind, "to": a.get(parent), **_pick(a, "name", "reason")}
        extra = "项目提议下挂任务（projectProposalId）v2 没有，先等项目被批准。" if a.get("projectProposalId") else ""
        return {k: v for k, v in args.items() if v is not None}, extra
    return conv


def _change(a):
    args = {"op": a.get("op"), "target": a.get("targetId"), **_pick(a, "name", "reason")}
    if a.get("toId"):
        args["to"] = a["toId"]
    return {k: v for k, v in args.items() if v is not None}, ""


_CONV = {
    "get_task_tree": _tree("task"), "list_projects": _tree("project"),
    "get_next_actions": lambda a: ({}, ""), "get_weekly_review": lambda a: ({"range": "week"}, ""),
    "get_current_timer": lambda a: ({}, ""),
    "list_time_sessions": _from_to, "get_daily_time": _history("human"), "get_agent_time": _history("agent"),
    "list_activity_suggestions": lambda a: ({}, "v2 列的是还没有规则收的窗口块；待确认建议没有了。"),
    "get_match_history": lambda a: ({}, "匹配历史没有了；已有规则看 Get_Rules。"),
    "get_window_awaiting_target": lambda a: ({}, "「等 AI 认的窗口」没有了；用本工具看哪些窗口没规则收。"),
    "get_detector_rules": lambda a: ({}, ""), "get_report_status": lambda a: ({"kind": "report"}, ""),
    "get_detector_health": lambda a: (_pick(a, "deviceId", "limit"), ""),
    "propose_detector_rules": _rules, "apply_detector_rules": _rules,
    "suggest_window_target": lambda a: ({}, "v2 没有「认下这一个窗口」；写第一级规则把这类窗口收进块。"),
    "propose_ignore_rule": _ignore,
    "propose_activity_matches": lambda a: ({}, "v1 按建议 id 配任务；v2 按块名记：先 Get_Pending_Classify_Windows 取块名，再 assign。"),
    "propose_report": lambda a: ({"text": a["summary"][:8000]} if isinstance(a.get("summary"), str) and a["summary"] else {},
                                 "只换了总结；逐条归类请另用 CRUD_Windowsblock。"),
    "propose_project": _create("project", "zoneId"), "propose_task": _create("task", "projectId"),
    "propose_change": _change,
}


def migrate(name: str, args) -> dict:
    a = args if isinstance(args, dict) else {}
    try:
        arguments, extra = _CONV[name](a)
    except Exception:  # 参数形状不对：能换的换不了，照样给出新工具名
        arguments, extra = {}, "参数没法换算。"
    use = V1_USE[name]
    note = f"{name} 已停用，请改调 {use}（不再返回数据）。{extra}"
    return {"deprecated": True, "use": use, "call": {"name": use, "arguments": arguments}, "note": note}
