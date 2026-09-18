"""``actor``/``lastWriter`` 字段的写入辅助（契约 v1.5「写者字段」节，
PRD F-ACTOR-1，**仅字段**）。

拆成独立文件的理由同 `deps.py`/`errors.py`——这是三类对象建/改都要过一遍的
横切逻辑，拆出来 `service.py` 的六处调用点（三建三改）只需各加一行，不必
在 `service.py` 里重复"翻译 actor→lastWriter"的逻辑。

**两个字段名故意不同**：``actor`` 是请求体里"这次写操作声明由谁发起"的
入参，``lastWriter`` 是落库/响应里"这个对象上次是谁写的"的持久状态——一个
是动作、一个是状态。

**本文件只做字段落库，不做来源校验**（来源区分留 F-ACTOR-1 的波2 版本，
依赖尚未创建的 `ai-planner` 模块受控层才有实施对象）：任何调用方现在都能在
请求体里自称 ``actor:"human"``，本文件原样信任、原样记录——这是已知的、
暂时的信任洞，契约「写者字段」节的「本版明确不做什么」子节已经写清楚，
不是这里的疏忽。
"""

from __future__ import annotations

from .errors import InvalidInputError

VALID_ACTORS = ("human", "ai")

#: 缺省 actor（PRD F-ACTOR-1：「缺省 human」）。
DEFAULT_ACTOR = "human"


def normalize_actor(actor: str | None) -> str:
    """建对象用：缺省 human；非法值直接拒（校验指名道姓，同其余入参纪律）。"""
    value = actor if actor is not None else DEFAULT_ACTOR
    if value not in VALID_ACTORS:
        raise InvalidInputError(f"actor 非法：{value!r}，合法取值 {'/'.join(VALID_ACTORS)}")
    return value


def apply_actor_update(updates: dict) -> dict:
    """改对象用：``updates`` 里的 ``actor`` 键（若有）翻译成 ``lastWriter``。

    未传 ``actor``（PATCH 只改别的字段）时不动 ``lastWriter``——与其余可选
    字段的 ``exclude_unset`` 语义一致，PATCH 是局部更新，不传就是不改。
    """
    if "actor" not in updates:
        return updates
    updates = dict(updates)
    updates["lastWriter"] = normalize_actor(updates.pop("actor"))
    return updates


def with_actor(fields: dict, actor: str | None) -> dict:
    """PATCH 交给 ``service`` 的字段：``actor=None`` 时**不带这个键**。

    ``service.apply_actor_update`` 按"键在不在"决定动不动 ``lastWriter``
    （PATCH 局部更新语义）——塞一个 ``actor: None`` 进去会把"不改"悄悄变成
    "改成非法值"。原是 ``unified_router.py`` 的私有 ``_with_actor``，v1.7
    JSON 导入（``import_apply.py``）的更新路径需要同一份逻辑，拆到这里给
    两个调用点共用而不是各写一份（同一形状只认一个真身）。
    """
    updates = {key: value for key, value in fields.items() if key != "actor"}
    if actor is not None:
        updates["actor"] = actor
    return updates
