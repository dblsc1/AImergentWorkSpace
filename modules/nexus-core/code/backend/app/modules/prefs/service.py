"""泳道偏好（契约 v2.22）：藏起来 / 置顶（按代理身份）、手动排位（按运行）。

- **身份 = (agent, 归一化后的 label)**，不是 runId：同名代理重启后仍然藏着 / 置顶着。label 归一化 = 折叠空白 + casefold；
  没有 label 的运行身份只有 agent。藏起来只影响**显示**，一秒时间都不改。
- **手动排位按运行**（runId → slot，slot = 在「未置顶的在跑运行」里的位置）：只在这条运行还在跑时有效，
  它一结束就不再占位（读时忽略，下一次写顺手清掉），掉到已结束那一组。
- 偏好是显示用的活状态，不进台账 / 投影 / 导出 / 快照恢复。
- 只许人：router 的依赖先挡掉带 ``Authorization: Bearer`` 的（设备令牌 / read 令牌 → 403）；匿名与 report 令牌被范围中间件拦在外面。
"""

from __future__ import annotations

from datetime import datetime, timezone

from ...tenant import current as current_tenant
from ..planner.errors import NotFoundError, UnprocessableError
from ..timer import service as timer_service
from . import repo

MAX_PREFS = 200  #: 至多记这么多个代理身份（藏起来 / 置顶着的）
MAX_ORDER = 50  #: 至多这么多条运行被手动排过位
MAX_AGENT, MAX_LABEL = 128, 200
_CAS_RETRIES = 20


def ident(agent: str | None, label: str | None, unverified: bool = False) -> str:
    """代理身份的键（归一化）：同一个 agent + 同一个 label（折叠空白、不分大小写）+ 同一类（已验证 / 未验证）= 同一个代理。
    agent / label 是开运行的人自报的，**不能证明身份**：匿名（未验证）的运行单独一类，藏起来的偏好互不串——
    藏起一个匿名的名字不会藏掉同名的已验证会话，反过来也一样。偏好只管显示，不参与任何授权或记账。"""
    return (" ".join((agent or "").split()).casefold() + "\n" + " ".join((label or "").split()).casefold()
            + ("\n未验证" if unverified else ""))


def _now() -> datetime:
    now = datetime.now(timezone.utc)
    return now.replace(microsecond=now.microsecond // 1000 * 1000)  # mongo 只存到毫秒：存前取整，读回来才相等


def _out(doc: dict | None) -> dict:
    doc = doc or {}
    return {"agents": [{"agent": a["agent"], "label": a["label"], "unverified": a.get("unverified", False),
                        "hidden": a["hidden"], "pinned": a["pinned"],
                        "pinnedAt": a["pinnedAt"].isoformat() if a.get("pinnedAt") else None}
                       for a in doc.get("agents", [])],
            "order": [{"runId": o["runId"], "slot": o["slot"]} for o in doc.get("order", [])]}


def load(user: str) -> dict:
    """``views/lanes`` / ``views/current`` 读的原始文档（没有 = 空）。"""
    return repo.get(user) or {"agents": [], "order": []}


def hidden_keys(user: str) -> set[str]:
    return {a["key"] for a in load(user)["agents"] if a["hidden"]}


def _live_ids(user: str) -> set[str]:
    # 未验证（匿名）的运行不能手动排位：它们永远排在已验证的后面
    return {r["runId"] for r in timer_service.list_lane_runs(user)[1] if r["endTs"] is None and not r.get("unverified")}


def _write(user: str, change) -> dict:
    """读 → ``change(agents, order)`` 原地改 → 带版本写；撞了重来。藏 / 置顶都没了的身份顺手删掉。"""
    for _ in range(_CAS_RETRIES):
        old = repo.get(user)
        agents = [dict(a) for a in (old or {}).get("agents", [])]
        order = [dict(o) for o in (old or {}).get("order", [])]
        change(agents, order)
        agents = [a for a in agents if a["hidden"] or a["pinned"]]
        if len(agents) > MAX_PREFS:
            raise UnprocessableError(f"藏起来 / 置顶的代理最多 {MAX_PREFS} 个")
        if len(order) > MAX_ORDER:
            raise UnprocessableError(f"手动排位的运行最多 {MAX_ORDER} 条")
        if repo.cas(user, old, agents, order):
            return _out({"agents": agents, "order": order})
    raise RuntimeError("泳道偏好写入争用未决")


def get() -> dict:
    return _out(load(current_tenant()))


def set_agent(agent: str, label: str, hidden: bool | None, pinned: bool | None, unverified: bool = False) -> dict:
    """幂等：给哪个标志就设成哪个值，没给的不动。未验证的运行只能藏、不能置顶。"""
    if unverified and pinned:
        raise UnprocessableError("未验证（匿名）的运行不能置顶")
    key = ident(agent, label, unverified)

    def change(agents: list[dict], _order: list[dict]) -> None:
        hit = next((a for a in agents if a["key"] == key), None)
        if hit is None:
            hit = {"key": key, "agent": " ".join(agent.split()), "label": " ".join(label.split()), "unverified": unverified,
                   "hidden": False, "pinned": False, "pinnedAt": None}
            agents.append(hit)
        if hidden is not None:
            hit["hidden"] = hidden
        if pinned is not None and pinned != hit["pinned"]:
            hit["pinned"], hit["pinnedAt"] = pinned, _now() if pinned else None

    return _write(current_tenant(), change)


def set_order(run_id: str, index: int) -> dict:
    """把这条在跑的运行放到「未置顶的在跑运行」的第 index 位（0 起）。已结束 / 不存在 → 404。"""
    user = current_tenant()
    live = _live_ids(user)
    if run_id not in live:
        raise NotFoundError(f"运行不在跑，不能手动排位：{run_id[:80]!r}")

    def change(_agents: list[dict], order: list[dict]) -> None:
        order[:] = [o for o in order if o["runId"] in live and o["runId"] != run_id]
        order.append({"runId": run_id, "slot": index})

    return _write(user, change)


def clear_order(run_id: str | None) -> dict:
    """``run_id`` 给了 = 只清这一条；没给 = 全清。没有这条也算成功（幂等）。"""

    def change(_agents: list[dict], order: list[dict]) -> None:
        order[:] = [o for o in order if run_id is not None and o["runId"] != run_id]

    return _write(current_tenant(), change)
