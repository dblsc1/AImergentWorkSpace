"""`create_app` 的 FastAPI 装配 —— 这条线路此前完全没被单测碰过。

**踩过的坑**：`service.py` 顶部 `from __future__ import annotations` 把注解变字符串；
`PlanReq` 曾定义在 `create_app()` **内部**（局部类），FastAPI 用
`typing.get_type_hints()` 解析 `req: PlanReq` 时看不到局部作用域，`NameError` 被
FastAPI 吞掉后把 `req` 误判成查询参数——**真调用永远 422**，但因为
`create_app` 标了 `pragma: no cover` 且没有任何测试真正经 HTTP 打过这条路，
52 全绿的基线完全没发现。本文件把这条线路钉住：用 `TestClient` 真发一次
HTTP POST（不 mock 到函数调用层），断言请求体被当 body 解析、不是 422。
"""
from __future__ import annotations

from typing import Any, Iterator

from fastapi.testclient import TestClient

from ai_planner.codex_driver import CodexDriver
from ai_planner.controlled_tools import ControlledToolLayer
from ai_planner.service import create_app
from ai_planner.streaming import StreamEvent


class _StubDriver(CodexDriver):
    """固定产出一条 action_plan（空动作），不真调 codex。"""

    def plan(self, schedule: dict[str, Any], goal: str) -> Iterator[StreamEvent]:
        yield StreamEvent(type="thinking", message=f"goal={goal}")
        yield StreamEvent(type="action_plan", message="清单", data={"actions": []})


def _app(spy_client: Any) -> Any:
    layer = ControlledToolLayer(spy_client)
    return create_app(layer, _StubDriver())


def test_plan_endpoint_accepts_json_body_not_query(spy: Any) -> None:
    """回归钉子：body 里的 goal 必须被当 request body 解析，不是 422 查询参数。"""
    app = _app(spy)
    client = TestClient(app)

    resp = client.post("/api/planner/plan", json={"goal": "冒烟测试目标"})

    assert resp.status_code == 200, resp.text
    body = resp.text
    assert "goal=冒烟测试目标" in body
    assert '"type": "done"' in body or '"type":"done"' in body


def test_plan_endpoint_uses_default_goal_when_body_omitted(spy: Any) -> None:
    """不传 body 时用默认 goal，同样不该 422（默认值来自 PlanReq 模块级定义）。"""
    app = _app(spy)
    client = TestClient(app)

    resp = client.post("/api/planner/plan", json={})

    assert resp.status_code == 200, resp.text
    assert "帮我理清收件箱并排下一步" in resp.text


def test_health_endpoint(spy: Any) -> None:
    app = _app(spy)
    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
