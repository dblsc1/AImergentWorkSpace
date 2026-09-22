"""codex 驱动冒烟 —— 真调一次 codex：读日程 → 产动作清单，经受控层路由。

**不接触真 nexus**（无需口令）：用 conftest 里的 SpyNexusClient 当 mock 后端，
证明的是「codex 真能被驱动、读懂日程、产出白名单动作；受控层能执行低风险 + 提议高风险」。
真 nexus 集成留待部署环境（需 AI_PLANNER_NEXUS_PASSWORD）。

跑法：
    AI_PLANNER_NEXUS_BASE=http://127.0.0.1:8000 \
    python3 smoke/codex_smoke.py

退出码 0 = 冒烟通过（拿到 >=1 低风险执行 + >=1 高风险提议，或至少拿到结构化动作）。
codex 接不通时打印降级信息并以码 2 退出（可诊断，不算测试失败的崩溃）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND))
sys.path.insert(0, str(_BACKEND / "tests"))

from ai_planner.codex_driver import CodexExecDriver  # noqa: E402
from ai_planner.config import Config  # noqa: E402
from ai_planner.controlled_tools import ControlledToolLayer  # noqa: E402
from ai_planner.service import run_planning  # noqa: E402
from conftest import SpyNexusClient  # noqa: E402


def main() -> int:
    schedule = json.loads(
        (Path(__file__).parent / "fixtures" / "schedule.json").read_text("utf-8")
    )

    spy = SpyNexusClient()
    # 让 read_schedule 返回冒烟固定日程
    spy.get_export = lambda: schedule  # type: ignore[assignment]
    layer = ControlledToolLayer(spy)

    guide_path = _BACKEND.parents[3] / "contracts" / "ai-planner-guide-v1.md"
    guide = guide_path.read_text("utf-8") if guide_path.exists() else "你是 GTD 规划助理。"

    cfg = Config(
        bind_host="127.0.0.1", bind_port=8700,
        nexus_base="http://127.0.0.1:8000",
        nexus_cookie=None,
        codex_bin="codex", codex_model=None, codex_timeout_s=180,
        guide_path=str(guide_path),
    )
    driver = CodexExecDriver(cfg, guide_text=guide)

    goal = "收件箱里有两条几乎重复的『看GTD的书』；『考证复习』已过期。帮我理清：把有用的想法建成正式任务，重复的提议删除，过期项目下的任务提议改期。"

    executed = 0
    proposals = 0
    degraded = False
    got_actions = False
    print("=== codex 冒烟开始（真调 codex，读固定日程）===")
    for evt in run_planning(layer, driver, goal):
        if evt.type == "thinking":
            print(f"  [想] {evt.message[:120]}")
        elif evt.type == "action_plan":
            got_actions = True
            print(f"  [清单] {evt.message}: {json.dumps(evt.data.get('actions'), ensure_ascii=False)[:400]}")
        elif evt.type == "executed":
            executed += 1
            print(f"  [执行] {evt.message}")
        elif evt.type == "proposal":
            proposals += 1
            print(f"  [提议] {evt.message}")
        elif evt.type == "rejected":
            print(f"  [拒绝] {evt.message}")
        elif evt.type == "degraded":
            degraded = True
            print(f"  [降级] {evt.message}")
        elif evt.type == "done":
            print(f"  [完成] {evt.message}")

    print(f"=== 汇总：executed={executed} proposals={proposals} "
          f"got_actions={got_actions} degraded={degraded} ===")
    # actor 注入实证
    for _t, body in spy.creates:
        assert body.get("actor") == "ai", "冒烟中出现非 ai 写！"
    if degraded:
        print("codex 接不通 → 降级（可诊断，非崩溃）")
        return 2
    if got_actions and (executed + proposals) >= 1:
        print("冒烟通过：codex 驱动打通，受控层执行/提议正常，actor=ai")
        return 0
    print("codex 有响应但未产出可路由动作")
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
