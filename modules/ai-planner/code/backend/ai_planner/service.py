"""宿主 AI 规划服务 —— 对前端出的流式端点（F-AI-1 / F-AI-5）。

装配：受控工具层（安全边界）+ codex 驱动（可想不可执行）+ 动作路由（唯一执行者）。
不进 compose 容器：codex 在宿主，够不到容器（F-AI-1）。绑定地址来自 config（非 0.0.0.0）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Iterator

from pydantic import BaseModel

from .action_router import route_actions
from .controlled_tools import ControlledToolLayer
from .driver_base import PlannerDriver
from .streaming import StreamEvent


class PlanReq(BaseModel):
    """`POST /api/planner/plan` 请求体。

    **必须落在模块顶层，不能挪进 `create_app()` 内部**（曾经的坑，见下）。
    """

    goal: str = "帮我理清收件箱并排下一步"


def run_planning(
    layer: ControlledToolLayer, driver: PlannerDriver, goal: str
) -> Iterator[StreamEvent]:
    """一次规划的编排：读日程 → 驱动想 → 受控层执行/提议 → done。

    纯生成器，不依赖 web 框架，便于单测。service 层把它包成 SSE。

    **提议按请求隔离**（module_docs/contract.md「done 事件语义」）：`layer` 是
    `bootstrap.build_app()` 里进程启动时构造的单例，跨请求复用；`layer.proposals`
    是它的实例属性，若 `done` 事件直接读它，会把「进程开机以来的全部提议」回给
    每一次请求（早前会话已删掉的对象也会被带出来，前端点确认即 404）。修法两层：
      1) 请求开始即清空 `layer.proposals`——避免它随进程生命周期无界增长（兜底，
         不是隔离正确性的唯一保障，见下）；
      2) `done.data.proposals` 来自**本次** `route_actions` 实际产出的 `proposal`
         事件（外置收集器 `collected_proposals`，函数局部变量），不读共享的
         `layer.proposals`——即便以后这个端点被并发请求撞上，某次请求的 `done`
         汇总也只由它自己触发产出的提议构成，不依赖对共享列表时序的假设。
    刻意不选「layer 每请求新建」：那会牵连白名单/actor 注入那套装配逻辑，
    代价比这里大，且与本次问题无关。
    """
    # 请求开始先清空共享列表（兜底：避免单例 layer.proposals 无界增长）。
    layer.proposals.clear()
    collected_proposals: list[dict[str, Any]] = []

    # 1) 先读全量日程（读端无风险，喂给驱动）
    try:
        schedule = layer.read_schedule()
    except Exception as exc:  # nexus 不可达等
        yield StreamEvent(
            type="degraded",
            message=f"AI 规划暂不可用：读取日程失败（{exc}）",
        )
        yield StreamEvent(type="done")
        return

    # 2) 驱动产事件流；action_plan 到达时路由动作
    degraded = False
    for evt in driver.plan(schedule, goal):
        if evt.type == "action_plan":
            actions = evt.data.get("actions", [])
            yield evt
            for routed in route_actions(layer, actions):
                if routed.type == "proposal":
                    collected_proposals.append(routed.data)
                yield routed
        else:
            if evt.type == "degraded":
                degraded = True
            yield evt

    # 3) 收尾：把本次（且只有本次）提议汇总一次（前端逐条确认用）
    if not degraded:
        yield StreamEvent(
            type="done",
            message="规划完成",
            data={"proposals": collected_proposals},
        )
    else:
        yield StreamEvent(type="done", message="AI 规划降级结束，待办区规则层不受影响")


def create_app(layer: ControlledToolLayer, driver: PlannerDriver):  # pragma: no cover
    """FastAPI 应用工厂。SSE 端点 POST /api/planner/plan。

    标注 no cover：需真实 nexus + codex 才有意义，逻辑核心在 run_planning（已单测）。

    **踩过的坑**（真起服务时才炸，pragma: no cover 让它躺了一整轮没被发现）：
    `PlanReq` 曾定义在本函数**内部**（局部类）。本模块头部 `from __future__ import
    annotations` 把所有类型注解变成字符串；FastAPI 用 `typing.get_type_hints()`
    解析 `req: PlanReq` 时只看函数 `__globals__`，看不到局部作用域，于是解析
    `PlanReq` 时 `NameError`——FastAPI 吞掉这个失败后，把 `req` 误判成一个**查询
    参数**而不是请求体，导致真调用永远 422（`loc: query.req`）。单测从没发现，
    因为 `pragma: no cover` 且没有真实 HTTP 调用路径。修法：`PlanReq` 挪到模块
    顶层（见上方）——同一坑形，别再把 FastAPI 路由用的 pydantic model 定义在
    工厂函数内部。
    """
    from fastapi import FastAPI
    from fastapi.responses import StreamingResponse

    app = FastAPI(title="ai-planner")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/api/planner/plan")
    def plan(req: PlanReq) -> StreamingResponse:
        def gen():
            for evt in run_planning(layer, driver, req.goal):
                yield evt.to_sse()

        return StreamingResponse(gen(), media_type="text/event-stream")

    return app


def load_guide(guide_path: str) -> str:
    """加载 F-GUIDE 说明书作为 codex system prompt。缺失即 die（契约先行，别猜）。"""
    p = Path(guide_path)
    if not p.exists():
        raise FileNotFoundError(
            f"F-GUIDE 说明书不存在：{guide_path}（system prompt 唯一事实，缺了不启动）"
        )
    return p.read_text(encoding="utf-8")
