"""AI 代理运行（契约 v2.1「AI 代理运行」节，唯一事实源）。

**人是一条泳道，AI 代理是很多条泳道。** 放在 timer 子边界里，因为它和人的计时是
同一类东西（活状态 → stop 时组装一条事实投进事件入口），但两条泳道**互不知道对方**：
本文件不读不写 ``timer_state``，``service.start/stop`` 也不碰 ``agent_runs``。

拆成独立文件同 ``backfill.py``（300 行纪律）；``service.py`` 只留薄委托，把
``_resolve_task_chain``（与 timer/start 同一套归属链判据）和服务端时钟 ``_now`` 注入进来。

关键取舍：

- **可并发**：没有「start 自动关上一个」——那是人的泳道的规则（人同一时刻只做一件事），
  搬到代理身上就是把三个并行的代理硬塞成串行记账。
- **先 ingest 后删活状态**（同 ``service.stop``）：删失败后重试命中防重，不丢不重。
- **dedupeKey = agent:<runId>，runId 在 start 时定死**：重复 stop、并发 stop、超时关闭
  三条路径撞同一个键，全系统只可能有一条 ``agent.run.completed``。
- **重复 stop 回原来那条（duplicate:true），不报 409**：同 timer stop 的 S8——hook 在网络
  抖动时一定会重试，409 会让它以为没停成再试一遍。
- **遗忘超时惰性关闭**：没有调度器。事件的 ``time``/``durationSeconds`` 按「开始 + 上限」
  算，不按被发现的时刻，所以关得晚不影响任何数字。
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime, timedelta

from ... import config
from ..events import service as events_service
from ..events.schemas import SPEC
from ..planner import service as planner_service
from ..planner.errors import NotFoundError
from . import repo

SOURCE = "agent-hook"
EVENT_TYPE = "agent.run.completed"


def _dedupe_key(run_id: str) -> str:
    return f"agent:{run_id}"


def _out(run_id: str, event: dict, duplicate: bool) -> dict:
    data = event.get("data") or {}
    return {
        "runId": run_id,
        "duplicate": duplicate,
        "outcome": data["outcome"],
        "durationSeconds": data["durationSeconds"],
        "event": {"id": event["id"], "dedupeKey": event["dedupeKey"], "type": event["type"]},
    }


def _close(run: dict, outcome: str, output: str | None, ended: datetime) -> tuple[dict, bool]:
    """组装信封 → ingest → 删活状态。返回 (落库的那条事件, 是否命中防重)。"""
    started = datetime.fromisoformat(run["startedAt"])
    data = {
        "agent": run["agent"],
        "tool": run["tool"],
        "startAt": run["startedAt"],
        # 秒级下限 1，同 timer.stop：起停同秒也是一次真实运行
        "durationSeconds": max(int((ended - started).total_seconds()), 1),
        "outcome": outcome,
    }
    if run.get("model"):
        data["model"] = run["model"]
    if output is not None:
        data["output"] = output
    subject = {"zone": run["zoneId"], "project": run["projectId"]}
    if run.get("taskId"):
        subject["task"] = run["taskId"]

    envelope = {
        "spec": SPEC,
        "id": f"evt_{uuid.uuid4().hex[:12]}",
        "dedupeKey": _dedupe_key(run["runId"]),
        "type": EVENT_TYPE,
        "user": run["user"],
        "source": SOURCE,
        "time": ended.isoformat(),
        "subject": subject,
        "data": data,
        "flags": [],
    }
    result = events_service.ingest(envelope)
    if result.rejected:
        # 自己组的信封被自己的校验拒了 = 实现 bug，响亮失败，不吞
        raise RuntimeError(f"agents 组装的信封未过事件校验：{result.rejected[0].reason}")
    repo.delete_agent_run(run["user"], run["runId"])  # ingest 成功（含 duplicate）之后才删

    if result.duplicate:
        # 并发 stop 的后到者：落库的是先到那条，回显它（它的 outcome 才是事实）
        return events_service.find_by_dedupe(run["user"], SOURCE, envelope["dedupeKey"]), True
    return envelope, False


def _expire(user: str, now: datetime) -> None:
    """惰性关闭该租户下超时的运行：outcome=timeout，时长封顶为超时上限。"""
    cap = timedelta(hours=config.settings.agent_run_timeout_hours)  # 调用时读，测试可替换 settings
    for run in repo.list_agent_runs(user):
        started = datetime.fromisoformat(run["startedAt"])
        if now - started > cap:
            _close(run, "timeout", None, started + cap)


def start(
    task_id: str | None,
    agent: str,
    tool: str,
    model: str | None,
    user: str,
    *,
    resolve_chain: Callable[..., tuple[dict, str, str]],
    now: Callable[[], datetime],
) -> dict:
    """开一个代理运行。**不碰 timer_state，不关任何在跑的运行。**"""
    if task_id is None:
        # 不挂任务 → 收件箱（同人的「先记下来再理清」）。只用 well-known id，不代建收件箱：
        # 事件 subject 只存 opaque id，收件箱哪天被种子建出来，名字自然 join 得上。
        zone_id, project_id = planner_service.INBOX_ZONE_ID, planner_service.INBOX_PROJECT_ID
    else:
        _task, project_id, zone_id = resolve_chain(task_id, action="拒绝开始代理运行")

    right_now = now()
    _expire(user, right_now)
    run = {
        "user": user,
        "runId": f"run_{uuid.uuid4().hex[:12]}",
        "taskId": task_id,
        "projectId": project_id,
        "zoneId": zone_id,
        "agent": agent,
        "tool": tool,
        "model": model,
        "startedAt": right_now.isoformat(),
    }
    repo.add_agent_run(run)
    return {"runId": run["runId"], "startedAt": run["startedAt"]}


def stop(
    run_id: str, outcome: str, output: str | None, user: str, *, now: Callable[[], datetime],
) -> dict:
    """结束一个运行，写**一条** ``agent.run.completed``。已结束的运行回原事件（duplicate:true）。"""
    right_now = now()
    _expire(user, right_now)  # 先收超时：超时的运行此刻已是 timeout 事实，下面按「已结束」回显
    run = repo.get_agent_run(user, run_id)
    if run is None:
        stored = events_service.find_by_dedupe(user, SOURCE, _dedupe_key(run_id))
        if stored is None:
            raise NotFoundError(f"代理运行不存在：{run_id!r}")
        return _out(run_id, stored, True)
    event, duplicate = _close(run, outcome, output, right_now)
    return _out(run_id, event, duplicate)


def list_running(user: str, *, now: Callable[[], datetime]) -> list[dict]:
    """``views/current`` 的 ``agents[]``。读之前先收超时（契约明文允许的唯一「读时写」）。"""
    _expire(user, now())
    return [
        {k: run.get(k) for k in ("runId", "taskId", "agent", "tool", "model", "startedAt")}
        for run in repo.list_agent_runs(user)
    ]
