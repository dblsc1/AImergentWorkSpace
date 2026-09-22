"""提议按请求隔离（紧急小修，2026-08-12）。

`bootstrap.build_app()` 里 `ControlledToolLayer` 是进程启动时构造一次的单例，
跨请求复用（见 `ai_planner/bootstrap.py::build_app`）。实测复现：连续两次
`run_planning`（同一个 layer 实例，正如真实进程里发生的那样），第二次 `done`
事件的 `proposals` 里带出了第一次规划产生的提议（`target_id` 指向早前会话
已被删掉的对象，前端点确认会 404）。

本测试固定住「第二次规划的 done.proposals 不包含第一次规划的提议」这条断言，
并在 docstring 里留下**反向验证记录**（把隔离改回去，本测试必须变红）——
把 `ai_planner/service.py::run_planning` 里 `data={"proposals": collected_proposals}`
改回 `data={"proposals": [p.to_ui() for p in layer.proposals]}`（且删掉开头的
`layer.proposals.clear()`）后重跑：
`test_second_planning_does_not_leak_first_planning_proposals` FAILED
（`AssertionError: assert 'prop_gen1' not in {...}` 变成 `AssertionError`，
第二次 done.proposals 长度从 1 变成 2，包含了第一次的 prop_gen1）。
"""
from __future__ import annotations

from ai_planner.codex_driver import StubDriver
from ai_planner.controlled_tools import ControlledToolLayer
from ai_planner.service import run_planning


def _propose_delete_action(target_id: str, reason: str) -> dict:
    return {
        "tool": "propose_delete",
        "args": {"type": "tasks", "id": target_id, "reason": reason},
    }


def test_second_planning_does_not_leak_first_planning_proposals(
    layer: ControlledToolLayer,
):
    """同一个 layer 实例（模拟 bootstrap 单例跨请求复用）连续跑两次规划：
    第二次的 done.proposals 只能有第二次自己产出的提议，不能含第一次的。
    """
    # 第一次规划：产出一条指向 t_gen1 的删除提议
    driver1 = StubDriver([_propose_delete_action("t_gen1", "第一次规划的提议")])
    events1 = list(run_planning(layer, driver1, "第一次目标"))
    done1 = events1[-1]
    assert done1.type == "done"
    ids1 = {p["target_id"] for p in done1.data["proposals"]}
    assert ids1 == {"t_gen1"}

    # 第二次规划：只读日程，不产生任何动作（模拟「只读一下日程，别改」的请求）
    driver2 = StubDriver([])
    events2 = list(run_planning(layer, driver2, "只读一下日程，不要做任何修改"))
    done2 = events2[-1]
    assert done2.type == "done"

    # 核心断言：第二次的 done.proposals 必须是空的，绝不能带出第一次的 t_gen1
    ids2 = {p["target_id"] for p in done2.data["proposals"]}
    assert "t_gen1" not in ids2, (
        f"第二次规划的 done.proposals 泄漏了第一次的提议：{done2.data['proposals']!r}"
    )
    assert ids2 == set()


def test_third_planning_only_sees_its_own_proposal(layer: ControlledToolLayer):
    """三次规划各产一条不同提议，第三次的汇总里只能有第三次那一条。"""
    for i, target in enumerate(["t_a", "t_b", "t_c"], start=1):
        driver = StubDriver([_propose_delete_action(target, f"第{i}次")])
        events = list(run_planning(layer, driver, f"目标{i}"))
        done = events[-1]
        ids = {p["target_id"] for p in done.data["proposals"]}
        assert ids == {target}, f"第 {i} 次规划的 done.proposals 应只含 {target!r}，实际 {ids!r}"
