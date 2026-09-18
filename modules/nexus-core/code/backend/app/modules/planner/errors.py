"""planner 的域异常——`main.py` 按类型映射成 400/404/409（contract.md「校验」）。

拆成独立文件是为了让 `service.py`（业务逻辑）与 `deps.py`（依赖图校验）
都能引用同一套异常类型而不互相 import 成环：`service.py` 调 `deps.py` 的
校验函数，`deps.py` 抛的异常要能被 `main.py` 现有的 `InvalidInputError → 400`
handler 接住，两边就必须认同一个类，不能各定义一份形似的。
"""

from __future__ import annotations


class NotFoundError(LookupError):
    """PATCH/DELETE 的目标 id 不存在 → 404。"""


class InvalidInputError(ValueError):
    """入参引用/取值非法 → 400。消息必须指名道姓。"""


class UnknownProjectError(InvalidInputError):
    """create/move 指了一个不存在的项目（历史名，保持兼容）。"""


class HasChildrenError(RuntimeError):
    """删除时还有子对象，或被别的任务依赖 → 409。消息说明还剩几个/是谁。"""


class ForbiddenError(RuntimeError):
    """actor 设防拒绝 → 403（契约 v1.6「actor 来源区分与高风险二次设防」）。

    **只在 main.py 挂一个 handler**（挂在本基类上，Starlette 按 MRO 查找），
    三个子类因此共用同一条 403 映射——安全拒绝的对外形状必须一致，
    否则调用方能靠状态码差异反推出"我是哪一种不合法"，那本身就是信息泄漏。
    """


class UnknownClientTokenError(ForbiddenError):
    """`X-Nexus-Client-Token` 存在但不匹配任何已配置凭据 → 403。

    **不降级成"未携带"**：一次失败的凭据校验被静默当成匿名放行，
    等于用更宽松的身份接住了它。fail-closed 是这里唯一正确的方向。
    """


class ActorForgeryError(ForbiddenError):
    """带 AI 凭据却自报 ``actor:"human"`` → 403。这是伪装，不是笔误。"""


class HighRiskDeniedError(ForbiddenError):
    """高风险写被二次设防拒绝 → 403（F-API-3）。库里一个字节都没写。"""


class StalePlanError(RuntimeError):
    """JSON 导入 apply 的 checksum 与当前库重新算出的不一致 → 409（契约 v1.7）。

    库在 dry-run 之后被改动过（或 payload/`allowDelete` 变了），拒绝执行以
    防止拿一份过时的计划写库。**409 而不是 400**：请求体本身合法，冲突的是
    **当前状态**——与 `HasChildrenError`「拒绝级联」是同一种形状（请求合法，
    但与此刻的库状态对不上）。
    """
