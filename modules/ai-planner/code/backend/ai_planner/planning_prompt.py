"""动作清单 prompt 构造 —— codex/deepseek 两个驱动共用同一份指令与输出格式约束。

两个驱动喂给底层模型的「你能做什么、该怎么输出」这份说明必须逐字一致 —— 否则
「同一份 F-GUIDE，两种模型表现不一样」就没法归因到模型本身，而可能只是 prompt 措辞
差异。唯一允许变化的输入是 `guide`（F-GUIDE 全文）、`schedule`（日程）、`goal`（用户目标）。
"""
from __future__ import annotations

import json
from typing import Any


def build_action_prompt(guide: str, schedule: dict[str, Any], goal: str) -> str:
    schedule_json = json.dumps(schedule, ensure_ascii=False, indent=2)
    return (
        f"{guide}\n\n"
        "=== 运行约束（本次调用）===\n"
        "你运行在只读沙盒里，**不执行任何操作**。你唯一的产出是一份 JSON 动作清单，"
        "由宿主的受控工具层去执行。只能使用说明书 §3 白名单里的命令名作为 tool。\n"
        "低风险命令（create_*/set_*）宿主会直接执行；高风险命令必须用 propose_* 形式，"
        "宿主只会把它们变成提议交用户确认。\n\n"
        f"=== 用户目标 ===\n{goal}\n\n"
        f"=== 全量日程（JSON）===\n{schedule_json}\n\n"
        "=== 输出 ===\n"
        '严格输出形如 {"actions":[{"tool":"<白名单命令>","args_json":"<参数的JSON字符串>"}]} '
        '的 JSON —— 注意 args_json 是把该命令参数**编码成一段字符串**，'
        '例如 {"tool":"create_task","args_json":"{\\"projectId\\":\\"p_inbox\\",\\"name\\":\\"读书\\"}"}。'
        "不要输出别的。至少给出你认为最有价值的若干条动作。"
    )
