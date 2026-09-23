"""计时活状态；stop 时组装 ``session.completed`` 投进事件入口。

关键取舍（都对着契约条款，不是偏好）：

- **stop 走 ``events/service.ingest``**——绕过校验与防重直写
  ``events`` 集合，重放时会炸在一个查不出来源的地方。
- **``dedupeKey = timer:<sessionId>`` 且 sessionId 在 start 时定死**：
  同一段计时无论 stop 重试多少次，防重键都稳定 → 只产生一条事件（S7）。
- **先 ingest 后清 timer_state**：若清态失败后重试，重放的 ingest 命中防重
  （duplicate），不会重复落库——顺序反过来会丢事件。
- **subject 的三个 opaque id（zone/project/task）在 start 时快照进 timer_state**
  （J10 标识三分）：stop 时不再查 planner；事件只存 id，改名/搬移不污染历史。
  归属链（task→project→zone）在 start 时**统一硬取**，断链当场响亮失败——
  上一轮返修的教训：同一行里 `.get()` 与 `[...]` 混用 = 自己都没把握，
  「契约没说」的地方要么问，要么在最早的时点炸清楚，不许糊过去。
- **未在计时时 stop → 平静返回 running:false**（S8），不是错误。
- **``cancel`` 与 ``stop`` 是两种语义，不是同一个的变体**：
  ``stop`` = 「这段时间发生过」，``cancel`` = 「这段时间不算数」。
  cancel **从一开始就不产生事实**——不组装信封、不调 ``events_service.ingest``、
  不碰投影。**绝不许用「先 stop 再删事实」实现**：事实是 append-only，
  那样会在事实流里留下一条、又删一条，而中间态可能已被投影读到。
  （实证 2026-08-03：一个 agent 测顶栏时留了个计时没停，跑了 6h54m；
  当时契约只有 start/stop，用户要停表的**唯一出路是往自己档案里写一条
  6h54m 的假事实**。误触、点错任务、忘了停表走开都是常态，
  每一次都以一条假记录收场是产品缺口，不是用户操作问题。）
- **未在计时时 cancel → 响亮报错**（与 stop 的 S8 刻意不同）：
  stop 的语义是「确保没在计时」，重复调用天然幂等；
  cancel 的语义是「把我正指着的那段丢掉」，没有可丢的东西时静默 200
  会让界面显示「已取消」而其实什么都没发生——那是在骗用户。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from ...tenant import current as current_tenant
from ..events import service as events_service
from ..events.schemas import SPEC
from ..planner import service as planner_service
from . import backfill as backfill_impl
from . import repo

SOURCE = "timer-backend"
#: 补登信封的 source（契约「补登」节）——``backfill.py`` 里的真身，这里重导出
#: 是为了给外部（如测试）一个稳定的 ``service.BACKFILL_SOURCE`` 引用点。
BACKFILL_SOURCE = backfill_impl.BACKFILL_SOURCE


class UnknownTaskError(LookupError):
    """start 指了一个 planner 里不存在的任务。main.py 映射成 404。"""


class NoRunningTimerError(LookupError):
    """cancel 时没有正在计时的 session。main.py 映射成 409。

    **为什么是错误、而 stop 的同一情形不是**：见模块 docstring。
    一句话——stop 是幂等的「确保停了」，cancel 是「丢掉我正指着的那一段」，
    没有可丢的就必须说出来，不能静默成功。
    """


def _now() -> datetime:
    return datetime.now(timezone.utc)


def get_running_state(user: str | None = None) -> dict | None:
    """views 的指定读路径（跨子边界只准调 service 公开函数）。"""
    user = user or current_tenant()  # v2.0：缺省取请求的租户
    return repo.get_running(user)


def _resolve_task_chain(task_id: str, *, action: str) -> tuple[dict, str, str]:
    """归属链一次取全、逐级验在（task→project→zone），断链当场响亮失败。

    ``start()`` 与 ``backfill()`` 共用同一套判据（契约「补登」节要求二者
    「与 start() 同一套」）——错的实现是各写一遍、两条路径悄悄分叉。
    ``action`` 只用来把报错文案写清是哪个操作拒绝了（"拒绝开始计时" /
    "拒绝补登"），判据本身完全一致。
    """
    task = planner_service.get_task(task_id)
    if task is None:
        raise UnknownTaskError(f"任务不存在：{task_id!r}（planner 里查无此 id）")

    project_id = task.get("projectId")
    project = planner_service.get_project(project_id) if project_id else None
    if project is None:
        raise UnknownTaskError(
            f"任务 {task_id!r} 的归属项目 {project_id!r} 不存在——数据链断裂，{action}"
        )
    zone_id = project.get("zoneId")
    if not zone_id:
        raise UnknownTaskError(
            f"项目 {project_id!r} 没有归属分区（zoneId 缺失）——数据链断裂，{action}"
        )
    return task, project_id, zone_id


def start(task_id: str, user: str | None = None) -> dict:
    """开始计时。**自动关闭上一个未结束的 session**（先 stop 再 start）——
    用户点「开始」时意图明确，让他先手动停上一个是无谓摩擦（contract.md）。"""
    user = user or current_tenant()  # v2.0：缺省取请求的租户
    _task, project_id, zone_id = _resolve_task_chain(task_id, action="拒绝开始计时")

    stop(user)  # 无活状态时是 no-op；有则正常走事件入口收尾

    start_at = _now().isoformat()
    repo.set_running(
        {
            "user": user,
            "sessionId": f"sess_{uuid.uuid4().hex[:12]}",
            "taskId": task_id,
            "projectId": project_id,
            "zoneId": zone_id,
            "startAt": start_at,
        }
    )
    return {"running": True, "taskId": task_id, "startAt": start_at}


def stop(user: str | None = None) -> dict:
    """结束计时：组装 ``session.completed`` → 事件入口 → 清活状态。"""
    user = user or current_tenant()  # v2.0：缺省取请求的租户
    state = repo.get_running(user)
    if state is None:
        return {"running": False, "event": None}  # S8：幂等，不报错

    now = _now()
    started = datetime.fromisoformat(state["startAt"])
    duration = max(int((now - started).total_seconds()), 1)  # 秒级下限 1：起停同秒也算一段真实工作

    envelope = {
        "spec": SPEC,
        "id": f"evt_{uuid.uuid4().hex[:12]}",  # 每次组装可不同——它不是防重键
        "dedupeKey": f"timer:{state['sessionId']}",  # 防重键必须稳定（契约 §3）
        "type": "session.completed",
        "user": user,
        "source": SOURCE,
        "time": now.isoformat(),  # 事情发生的时间 = 会话结束时刻
        "subject": {
            "zone": state["zoneId"],
            "project": state["projectId"],
            "task": state["taskId"],
        },
        "data": {"durationSeconds": duration, "startAt": state["startAt"]},
        "flags": [],
    }

    result = events_service.ingest(envelope)
    if result.rejected:
        # 自己组的信封被自己的校验拒了 = 实现 bug，响亮失败，不吞
        raise RuntimeError(f"timer 组装的信封未过事件校验：{result.rejected[0].reason}")

    repo.clear_running(user)  # ingest 成功（accepted 或 duplicate）之后才清
    return {
        "running": False,
        "event": {
            "id": envelope["id"],
            "dedupeKey": envelope["dedupeKey"],
            "type": envelope["type"],
        },
    }


def backfill(
    task_id: str,
    start_at_raw: str,
    duration_seconds: int,
    user: str | None = None,
) -> dict:
    """补登：给「完成了但没计时」的工作补一条真实 ``session.completed``
    （契约「补登（规范性 · v1.8，backfill）」节，唯一事实源）。

    实现在 ``backfill.py``（本模块 contract.md「内部子边界」的 300 行纪律逼出的
    拆分，同 planner 的 planvalidate.py 一个道理）；这里只是薄委托，把 start()
    已有的归属链硬取 ``_resolve_task_chain`` 与服务端时钟 ``_now`` 注入进去——
    backfill 与 start()「同一套判据」，不重新实现一遍（契约要害条款）。
    """
    user = user or current_tenant()  # v2.0：缺省取请求的租户
    return backfill_impl.backfill(
        task_id, start_at_raw, duration_seconds, user,
        resolve_chain=_resolve_task_chain, now=_now,
    )


def cancel(user: str | None = None) -> dict:
    """取消计时：**丢弃活状态，一条事实都不产生**。

    本函数刻意**不 import、不调用** ``events_service``，也不碰 projector。
    这不是省事，是语义：cancel 说的是「这段时间不算数」，
    而「不算数」的正确实现是**从一开始就不写**，不是「写了再删」。

    ⚠️ 下一个改这里的人：如果你打算让它复用 ``stop()``——**别**。
    那会产生一条 ``session.completed`` 再想办法删掉它，而
    ``events`` 是 append-only（无软删除、无回收站），投影可能在两步之间
    读到那条中间态。``tests/test_timer_cancel.py`` 里有一条**反向验证**
    专门盯着这件事：把 cancel 换成内部调 stop，T1 立刻变红。

    返回被丢弃那段的摘要（``discardedSeconds`` 只是给界面回显用的**派生值**，
    没有任何地方存它）——让用户看见「丢掉的是 6h54m」，而不是无声消失。
    """
    user = user or current_tenant()  # v2.0：缺省取请求的租户
    state = repo.get_running(user)
    if state is None:
        raise NoRunningTimerError(
            "当前没有正在计时的会话，没有可取消的东西。"
            "（这不是 500：请求本身合法，只是与当前状态冲突。"
            "若你想要的是「确保没在计时」，那是 POST /timer/stop 的幂等语义。）"
        )

    discarded = max(
        int((_now() - datetime.fromisoformat(state["startAt"])).total_seconds()), 0
    )

    repo.clear_running(user)  # 全部副作用就这一行：删活状态。事实流一个字节都不动。
    return {
        "running": False,
        "cancelled": {
            "taskId": state["taskId"],
            "startAt": state["startAt"],
            "discardedSeconds": discarded,
        },
    }
