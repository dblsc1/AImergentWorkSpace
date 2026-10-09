"""``GET /api/core/views/agent-time``（契约 v2.3「AI 代理时长读端」，唯一事实源）。

代理时长是另一个维度：只读 ``proj_agent_daily_stats``，**不碰人的任何投影**，响应里没有人的时长。
口径（契约规范性）：泳道秒数（并行运行各算各的）、归日同人（整段归 startAt 那天）、
``open[]`` 在跑运行不计入汇总。
"""

from __future__ import annotations

from datetime import datetime

from ...config import settings
from ...tenant import current as current_tenant
from ...timeutil import local_date
from ..prefs import service as prefs_service
from ..projector.handlers import agent_daily_stats as agent_projection
from ..timer import service as timer_service
from .queries import _today
from .schemas import AgentTimeOut


def _sum_by(rows: list[dict], key) -> dict:
    out: dict = {}
    for row in rows:
        acc = out.setdefault(key(row), [0, 0])
        acc[0] += row["seconds"]
        acc[1] += row["runs"]
    return out


def _attention_seconds(run: dict) -> int:
    """v2.17：这条运行上 attend 的秒数之和（人看它看了多久；attend 之间不重叠）。"""
    return int(sum(
        (datetime.fromisoformat(i["until"]) - datetime.fromisoformat(i["at"])).total_seconds()
        for i in run.get("interactions") or [] if i.get("kind") == "attend"))


def _desc(groups: dict) -> list:
    """seconds 降序；同秒数按键升序（None 排最前），保证输出稳定。"""
    return sorted(groups.items(), key=lambda kv: (-kv[1][0], repr(kv[0])))


def get_agent_time(date_from: str | None = None, date_to: str | None = None) -> AgentTimeOut:
    user = current_tenant()
    # 先收超时再读投影：被收掉的运行此刻已是 timeout 事实，落进下面的汇总而不是 open[]。
    # open[] 在读完投影之后再取一次：两次读之间刚结束的运行只会在汇总里，不会两边都算
    # （反过来最多暂时两边都不在，下次读就对了）。
    timer_service.list_open_agent_runs(user)
    rows = agent_projection.read_agent_daily_stats(user, date_from=date_from, date_to=date_to)
    hidden = prefs_service.hidden_keys(user)  # v2.22：藏起来的不列在 open[]（open[] 本来就不计入汇总）
    open_runs = [r for r in timer_service.list_open_agent_runs(user)
                 if prefs_service.ident(r.get("agent"), r.get("label"), bool(r.get("unverified"))) not in hidden]

    days = _sum_by(rows, lambda r: r["date"])
    agents = _sum_by(rows, lambda r: r["agent"])
    tasks = _sum_by(rows, lambda r: (r["projectId"], r.get("taskId")))

    def in_range(run: dict) -> bool:
        day = local_date(datetime.fromisoformat(run["startedAt"]), settings.tz)
        return (not date_from or day >= date_from) and (not date_to or day <= date_to)

    return AgentTimeOut(
        today=_today(),
        totalSeconds=sum(r["seconds"] for r in rows),
        runs=sum(r["runs"] for r in rows),
        days=[{"date": d, "seconds": s, "runs": n} for d, (s, n) in sorted(days.items())],
        agents=[{"agent": a, "seconds": s, "runs": n} for a, (s, n) in _desc(agents)],
        tasks=[
            {"projectId": p, "taskId": t, "seconds": s, "runs": n}
            for (p, t), (s, n) in _desc(tasks)
        ],
        open=[
            {**{k: run.get(k) for k in
                ("runId", "agent", "projectId", "taskId", "startedAt", "elapsedSeconds")},
             "attentionSeconds": _attention_seconds(run)}
            for run in open_runs
            if in_range(run)
        ],
    )
