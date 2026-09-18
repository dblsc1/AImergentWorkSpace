"""actor 来源判定与高风险二次设防（契约 v1.6，PRD F-ACTOR-1 波2 / F-API-3）。

**本文件是安全边界，不是校验规则。** 它假设调用方**恶意且绕过了 ai-planner 的
受控工具层**——受控层的白名单（AI 根本没有删除工具）是第一道，本文件是第二道。
两道都在才叫设防；只有第一道，叫单点。

三条判据，按顺序：

1. **来源看凭据头，不看请求体**（`X-Nexus-Client-Token`）。头存在但不匹配任何
   已配置凭据 → 403，**不降级成"未携带"**：把一次失败的凭据校验静默当成匿名
   放行，等于用更宽松的身份接住了它。
2. **来源压过自报**："我是 human" 是提权声明，必须有凭据；"我是 ai" 是降权
   声明，谁说都信。带 AI 凭据却自报 human → 403（伪装，不是笔误）。
3. **高风险写（DELETE / 改 projectId、zoneId、plan）有效 actor 为 ai → 403**，
   **拒在任何库写入与存在性检查之前**。先拒来源再谈对象在不在，否则
   404 与 409 的差异会变成一个 id 探测器。

每一次写（放行的、拒掉的、业务校验挂掉的）都留一条审计（见 `audit.py`）。
"""

from __future__ import annotations

import hmac
from collections.abc import Callable
from typing import Any

from ... import config
from . import audit
from .errors import ActorForgeryError, HighRiskDeniedError, UnknownClientTokenError

SOURCE_AI = "ai"
SOURCE_HUMAN = "human"
SOURCE_UNVERIFIED = "unverified"

ACTOR_AI = "ai"
ACTOR_HUMAN = "human"

#: PATCH 里出现这些键即为高风险（契约 v1.6 的高风险表）。
#: 判据看**请求体里实际出现的键**，不看值有没有变——"有没有伸手"比
#: "伸手后有没有变"更好判、更难绕；后者要先读旧值，读与写之间就有竞态。
HIGH_RISK_UPDATE_FIELDS = ("projectId", "zoneId", "plan")

_CREDENTIALED = (SOURCE_AI, SOURCE_HUMAN)


def resolve_source(headers: Any) -> str:
    """凭据头 → 来源类别。抛 `UnknownClientTokenError`（403）表示凭据不认识。

    头缺失**或为空串**都算 `unverified`：网关用 `proxy_set_header X: "$var"`
    在变量未设时发的正是空串，把它判成"带了个错凭据"会在网关配错时
    把整个前端打死，而那是运维事故不是攻击。
    """
    raw = headers.get(config.CLIENT_TOKEN_HEADER)
    token = (raw or "").strip()
    if not token:
        return SOURCE_UNVERIFIED
    settings = config.settings
    if settings.ai_client_token and hmac.compare_digest(token, settings.ai_client_token):
        return SOURCE_AI
    if settings.human_client_token and hmac.compare_digest(
        token, settings.human_client_token
    ):
        return SOURCE_HUMAN
    raise UnknownClientTokenError(
        f"{config.CLIENT_TOKEN_HEADER} 不匹配任何已配置的来源凭据——"
        f"拒绝而不是按匿名放行（契约 v1.6 fail-closed）"
    )


def resolve_actor(source: str, body_actor: str | None) -> str:
    """来源 + 自报 → 有效 actor。"""
    if source == SOURCE_AI:
        if body_actor == ACTOR_HUMAN:
            raise ActorForgeryError(
                "携带 AI 来源凭据的请求自报 actor=\"human\"——这是伪装，不是笔误。"
                "受控工具层无须、也不得自报 actor（契约 v1.6）"
            )
        return ACTOR_AI
    if body_actor is not None:
        return body_actor
    return ACTOR_HUMAN


def high_risk_reason(op: str, changes: dict) -> str | None:
    """高风险判据（契约 v1.6 唯一事实表）。返回原因文案，或 None＝不是高风险。"""
    if op == audit.OP_DELETE:
        return "DELETE（删除不可逆）"
    if op == audit.OP_UPDATE:
        hit = [field for field in HIGH_RISK_UPDATE_FIELDS if field in changes]
        if hit:
            return f"PATCH 改 {'/'.join(hit)}（搬移/改期）"
    return None


def denial_reason(actor: str, source: str, risk: str | None) -> str | None:
    """该不该拒？返回拒绝原因，或 None＝放行。"""
    if risk is None:
        return None  # 低风险写不受本节限制（PRD F-AI-4：建对象/改标题/调权重）
    if actor == ACTOR_AI:
        return (
            f"高风险操作 {risk} 不接受 actor=\"ai\"——AI 对搬移/改期/删除只有提议权，"
            f"执行必须走人的路径（契约 v1.6 F-API-3 二次设防）"
        )
    if config.settings.actor_strict and source not in (SOURCE_HUMAN,):
        return (
            f"严格模式下高风险操作 {risk} 要求携带人路径凭据"
            f"（{config.CLIENT_TOKEN_HEADER}），当前来源为 {source!r}"
        )
    return None


def _actor_argument(op: str, source: str, actor: str, body_actor: str | None) -> str | None:
    """交给 service 层的 actor 入参。

    - `create`：永远给有效 actor（未携凭据时它等于自报值或缺省 human，行为不变）。
    - `update`：**携凭据才强制覆盖**；未携凭据时原样交回自报值（可能是 None ＝
      不改 `lastWriter`），这是 v1.5 的 PATCH 局部更新语义，前端一个字都不用改。
    - `delete`：对象要没了，没有 `lastWriter` 可写。
    """
    if op == audit.OP_DELETE:
        return None
    if op == audit.OP_CREATE or source in _CREDENTIALED:
        return actor
    return body_actor


def run_write(
    request: Any,
    *,
    op: str,
    object_type: str,
    action: Callable[[str | None], Any],
    entity_id: str | None = None,
    body_actor: str | None = None,
    changes: dict | None = None,
) -> Any:
    """统一 CRUD 写入口的**唯一**执行通道：判来源 → 设防 → 执行 → 留审计。

    `action(actor_arg)` 由路由给出，里面只调 `service.py` 的公开函数；
    本函数不做任何业务判断（那仍归 service 层），只做安全判定与留痕。

    **九条写路由（3 类对象 × POST/PATCH/DELETE）必须全部经过这里**
    ——`tests/test_actor_source_guard.py::test_every_write_route_goes_through_guard`
    有一条 AST 断言盯着 `unified_router.py`，新增写端点忘了接就直接红。
    漏接一条的后果不是"少了条审计"，是**那条路径没有二次设防**。
    """
    changes = dict(changes or {})
    changes.pop("actor", None)  # actor 单列一列，不混进变更摘要

    source = resolve_source(request.headers)      # 403：凭据不认识
    actor = resolve_actor(source, body_actor)     # 403：伪装 human
    risk = high_risk_reason(op, changes)
    denial = denial_reason(actor, source, risk)

    def _audit(outcome: str, object_id: str | None, reason: str | None) -> None:
        audit.record(
            actor=actor, source=source, op=op, object_type=object_type,
            object_id=object_id, high_risk=risk is not None,
            outcome=outcome, changes=changes, reason=reason,
        )

    if denial is not None:
        # 库里一个字节都没写——先留痕再抛，被拒的尝试本身就是安全信号。
        _audit(audit.OUTCOME_DENIED, entity_id, denial)
        raise HighRiskDeniedError(denial)

    try:
        result = action(_actor_argument(op, source, actor, body_actor))
    except Exception as exc:  # noqa: BLE001 —— 记完原样抛，不吞、不改语义
        _audit(audit.OUTCOME_FAILED, entity_id, f"{type(exc).__name__}: {exc}")
        raise

    object_id = entity_id
    if object_id is None and isinstance(result, dict):
        object_id = result.get("id")  # create：id 由系统生成，执行后才有
    _audit(audit.OUTCOME_APPLIED, object_id, None)
    return result
