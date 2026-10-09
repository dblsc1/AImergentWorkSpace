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
- **失联同样惰性关闭（v2.18）**：会发心跳的运行不看遗忘超时，看 ``agent_liveness``（关在最后一次信号的时刻）。
- **关闭是一道原子边界（v2.4）**：``_close`` 先用一次条件更新打关闭标记（定死结束时刻与 outcome）
  并取回整份文档作快照，再按快照组装、ingest、删活状态；相位 / attend 的写入都带「未关闭」条件
  （``agent_phases.py``）。标记后崩溃留下的「已标记未删除」由下一次清理按快照补完。
"""

from __future__ import annotations

import unicodedata
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta

from ... import config
from ..events import service as events_service
from ..events.schemas import SPEC
from ..planner import service as planner_service
from ..planner.errors import InvalidInputError, NotFoundError
from . import repo
from .agent_liveness import AGENT_HEARTBEAT_SECONDS, mark_expired

SOURCE = "agent-hook"
EVENT_TYPE = "agent.run.completed"
#: v2.19 匿名上报（契约「调用方范围与匿名上报」）：匿名的 clientKey 存成这个前缀 + 原值，自成一个名字空间
ANON_KEY_PREFIX = "anon:"
#: 每个租户同时在跑的匿名运行上限。ponytail: 先数后插不是原子的，并发时可能多出几个；网关限速兜着，够用
MAX_ANONYMOUS_RUNS = 20


class TooManyAnonymousRunsError(Exception):
    """同时在跑的匿名运行到上限（main.py 映射成 429）。"""


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


def ts(raw: str) -> datetime:
    """存进 agent_runs 的时刻一律带偏移（startedAt 服务端盖、相位的 at 入口已校验）。"""
    return datetime.fromisoformat(raw)


def snapshot_data(snap: dict) -> tuple[dict, datetime]:
    """由关闭标记里定死的结束时刻与 outcome，把快照组装成事件 ``data``（v2.4 追加键在此）。

    所有关闭路径同一套裁剪：结束之后的相位与 reply 丢弃，attend 的 until 钳到结束时刻、
    起点在结束之后的丢弃。没有的键不出现——不报相位的运行写出的 data 与 v2.1 逐字节相同。"""
    marker = snap["closing"]
    ended = ts(marker["endedAt"])
    started = ts(snap["startedAt"])
    data = {
        "agent": snap["agent"],
        "tool": snap["tool"],
        "startAt": snap["startedAt"],
        # 秒级下限 1，同 timer.stop：起停同秒也是一次真实运行
        "durationSeconds": max(int((ended - started).total_seconds()), 1),
        "outcome": marker["outcome"],
    }
    if snap.get("model"):
        data["model"] = snap["model"]
    if marker.get("output") is not None:
        data["output"] = marker["output"]
    if snap.get("label"):
        data["label"] = snap["label"]
    for key in ("beatSource", "beatCount"):  # v2.18：谁发的心跳、发了几下（没有就不出现）
        if snap.get(key):
            data[key] = snap[key]
    if snap.get("unverified"):  # v2.19：匿名开的运行；没有就不出现
        data["unverified"] = True
    phases = [p for p in snap.get("phases") or [] if ts(p["at"]) <= ended]
    if phases:
        data["phases"] = phases
    interactions = []
    for item in snap.get("interactions") or []:
        if ts(item["at"]) > ended:
            continue
        if item["kind"] == "attend" and ts(item["until"]) > ended:
            item = {**item, "until": marker["endedAt"]}
        interactions.append(item)
    if interactions:
        data["interactions"] = interactions
    return data, ended


def _finish(snap: dict) -> tuple[dict, bool]:
    """按已标记的快照组装信封 → ingest → 删活状态。崩在半路时下一次 stop / 超时清理
    拿同一份快照重做，``agent:<runId>`` 防重兜底。返回 (落库的那条事件, 是否命中防重)。"""
    data, ended = snapshot_data(snap)
    subject = {"zone": snap["zoneId"], "project": snap["projectId"]}
    if snap.get("taskId"):
        subject["task"] = snap["taskId"]

    envelope = {
        "spec": SPEC,
        "id": f"evt_{uuid.uuid4().hex[:12]}",
        "dedupeKey": _dedupe_key(snap["runId"]),
        "type": EVENT_TYPE,
        "user": snap["user"],
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
    repo.delete_agent_run(snap["user"], snap["runId"])  # ingest 成功（含 duplicate）之后才删

    if result.duplicate:
        # 并发关闭的后到者：落库的是先到那条，回显它（它的 outcome 才是事实）
        return events_service.find_by_dedupe(snap["user"], SOURCE, envelope["dedupeKey"]), True
    return envelope, False


def _close(run: dict, outcome: str, output: str | None, ended: datetime) -> tuple[dict, bool]:
    """唯一的关闭路径（stop / 超时共用，v2.4 原子边界）：先标记取快照，再按快照落账。"""
    marker = {"outcome": outcome, "endedAt": ended.isoformat()}
    if output is not None:
        marker["output"] = output
    snap = repo.mark_agent_run_closing(run["user"], run["runId"], marker)
    if snap is None:
        # 别人先标记了（并发 stop / 超时）：按**它的**标记收尾；已删 = 已落账，回原事件
        snap = repo.get_agent_run(run["user"], run["runId"])
        if snap is None:
            stored = events_service.find_by_dedupe(run["user"], SOURCE, _dedupe_key(run["runId"]))
            if stored is None:
                raise NotFoundError(f"代理运行不存在：{run['runId']!r}")
            return stored, True
    return _finish(snap)


def _expire(user: str, now: datetime) -> None:
    """惰性关闭该租户下超时的运行：outcome=timeout，时长封顶为超时上限。
    顺手补完「已标记未删除」的运行（标记后崩溃 / ingest 失败留下的，v2.4）。"""
    cap = timedelta(hours=config.settings.agent_run_timeout_hours)  # 调用时读，测试可替换 settings
    for run in repo.list_agent_runs(user):
        if "closing" in run:
            _finish(run)
            continue
        started = ts(run["startedAt"])
        if run.get("heartbeat"):  # v2.18：会发心跳的运行不看遗忘超时，只看失联与 7 天安全上限
            if (snap := mark_expired(run, now)) is not None:
                _finish(snap)
        elif now - started > cap:
            _close(run, "timeout", None, started + cap)


def clean(value: str | None) -> str | None:
    """去掉控制字符（契约「隐私」：服务端只做长度上限与去控制字符）；去完为空当没给。"""
    if value is None:
        return None
    cleaned = "".join(ch for ch in value if unicodedata.category(ch) != "Cc")
    return cleaned or None


def _started(run: dict) -> dict:
    return {"runId": run["runId"], "startedAt": run["startedAt"], "heartbeatSeconds": AGENT_HEARTBEAT_SECONDS}


def start(
    task_id: str | None,
    agent: str,
    tool: str,
    model: str | None,
    user: str,
    *,
    resolve_chain: Callable[..., tuple[dict, str, str]],
    now: Callable[[], datetime],
    phase: str | None = None,
    label: str | None = None,
    match: str | None = None,
    client_key: str | None = None,
    project_id: str | None = None,
    heartbeat: bool = False,
    beat_source: str | None = None,
    unverified: bool = False,
) -> tuple[dict, bool]:
    """开一个代理运行。**不碰 timer_state，不关任何在跑的运行。**

    返回 ``(响应, 是否新开)``：带 ``clientKey`` 且同租户同 key 的运行还在跑时回原来那个（v2.4）。
    ``unverified``（v2.19）= 这个请求什么凭据都没带：不采用它给的任务 / 项目 / match，clientKey 另起名字空间。"""
    right_now = now()
    _expire(user, right_now)  # 超时的同 key 运行先收掉，下面才开得出新的
    if unverified:
        # 任务 / 项目存不存在会从状态码漏出去；match 会把人的窗口认到一个没验证的调用方头上
        task_id = project_id = match = None
        client_key = ANON_KEY_PREFIX + client_key if client_key else None
    elif client_key and client_key.startswith(ANON_KEY_PREFIX):
        raise InvalidInputError(f"clientKey 不能以 {ANON_KEY_PREFIX!r} 开头（保留给匿名上报）")
    if client_key:
        # 先认原运行，再校验只对新建有意义的字段：原任务后来被删了，重试照样回原运行
        existing = repo.find_agent_run_by_client_key(user, client_key)
        if existing is not None and bool(existing.get("unverified")) == unverified:  # 两类运行互不认领
            # v2.13 会话改名：这次给了且不同的 label / match 换掉存着的，其余不动
            names = {k: v for k, v in (("label", clean(label)), ("match", clean(match))) if v and v != existing.get(k)}
            if names:
                repo.relabel_agent_run(user, existing["runId"], names)
            repo.touch_agent_run(user, existing["runId"], right_now.isoformat(),  # v2.18
                                 declare=heartbeat, beat_source=beat_source)
            return _started(existing), False
    if task_id is not None:
        _task, chain_project, zone_id = resolve_chain(task_id, action="拒绝开始代理运行")
        if project_id not in (None, chain_project):  # v2.13：自相矛盾的不收，不替它挑一个
            raise InvalidInputError(f"projectId {project_id!r} 不是任务 {task_id!r} 所在的项目")
        project_id = chain_project
    elif project_id is not None:
        # v2.13 只挂项目：subject 与收件箱运行同形（无 task），项目是真的
        project = planner_service.get_project(project_id)
        if project is None or not project.get("zoneId"):
            raise NotFoundError(f"项目不存在：{project_id!r}")
        zone_id = project["zoneId"]
    else:
        # 都不挂 → 收件箱（同人的「先记下来再理清」）。只用 well-known id，不代建收件箱：
        # 事件 subject 只存 opaque id，收件箱哪天被种子建出来，名字自然 join 得上。
        zone_id, project_id = planner_service.INBOX_ZONE_ID, planner_service.INBOX_PROJECT_ID

    if unverified and repo.count_unverified_agent_runs(user) >= MAX_ANONYMOUS_RUNS:
        raise TooManyAnonymousRunsError(f"同时在跑的匿名代理运行已到上限（{MAX_ANONYMOUS_RUNS} 个），稍后再试")

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
        "lastSeenAt": right_now.isoformat(),  # v2.18：start / phase / heartbeat 都更新它
        **({"heartbeat": True} if heartbeat else {}),  # 声明会发心跳：活性规则只管这样的运行
        **({"beatSource": beat_source} if beat_source else {}),
    }
    # v2.4 选填键：没给就不写，老读方看到的文档与 v2.1 一样
    label, match = clean(label), clean(match)
    if label:
        run["label"] = label
    if match:
        run["match"] = match
    if phase:
        run["phases"] = [{"at": run["startedAt"], "phase": phase}]
    if client_key:
        run["clientKey"] = client_key
    if unverified:
        run["unverified"] = True
    for _ in range(3):
        if repo.add_agent_run(run):
            if unverified:
                _enforce_anonymous_cap(user, run["runId"])
            return _started(run), True
        existing = repo.find_agent_run_by_client_key(user, client_key)
        if existing is not None and bool(existing.get("unverified")) == unverified:
            return _started(existing), False
        # 撞键后那条又刚被关掉：再插一次
    raise RuntimeError(f"clientKey 争用未决：{client_key!r}")


def _enforce_anonymous_cap(user: str, run_id: str) -> None:
    """先插后数：并发的匿名 start 都看到 19 个时前面的 count 挡不住，这里插完再数，超限就撤回自己的那条。
    每个幸存者都在自己插入之后数过，最后一个数的看得到全部幸存者，所以幸存者不会超过上限；
    代价是边界上并发的两个可能都被撤回（429 重试即可），不会多放。名额就是活文档：stop / 超时 / 撤回都是删文档，不会泄漏。"""
    try:
        over = repo.count_unverified_agent_runs(user) > MAX_ANONYMOUS_RUNS
    except BaseException:
        repo.delete_agent_run(user, run_id)
        raise
    if over:
        repo.delete_agent_run(user, run_id)
        raise TooManyAnonymousRunsError(f"同时在跑的匿名代理运行已到上限（{MAX_ANONYMOUS_RUNS} 个），稍后再试")


def require_unverified(user: str, run_id: str) -> None:
    """v2.19：匿名调用方只能动匿名开的运行。不是的（在跑的看文档，已结束的看那条事实）与不存在的
    回同一个 404、同一句话——匿名分不出「没有」与「不是你的」。"""
    run = repo.get_agent_run(user, run_id)
    if run is None:
        stored = events_service.find_by_dedupe(user, SOURCE, _dedupe_key(run_id))
        run = (stored or {}).get("data") or {}
    if run.get("unverified") is not True:
        raise NotFoundError(f"代理运行不存在：{run_id!r}")


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


def current_phase(run: dict) -> str | None:
    """按 at 排最后的那条相位（phases 存的时候已按 (at, 到达先后) 排好）。"""
    phases = run.get("phases") or []
    return phases[-1]["phase"] if phases else None


def list_open(user: str, *, now: Callable[[], datetime]) -> list[dict]:
    """在跑的运行原样文档 + ``elapsedSeconds``（服务端此刻 − startedAt，钳到 ≥0）。
    读之前先收超时——契约明文允许的「读时写」，只给 ``views/current`` 与 ``views/agent-time``（v2.3）。"""
    right_now = now()
    _expire(user, right_now)
    runs = [run for run in repo.list_agent_runs(user) if "closing" not in run]
    for run in runs:
        elapsed = (right_now - datetime.fromisoformat(run["startedAt"])).total_seconds()
        run["elapsedSeconds"] = max(int(elapsed), 0)
    return runs


def list_running(user: str, *, now: Callable[[], datetime]) -> list[dict]:
    """``views/current`` 的 ``agents[]``（v2.4 增 phase/label）。"""
    return [
        {**{k: run.get(k) for k in ("runId", "taskId", "agent", "tool", "model", "startedAt", "label")},
         "phase": current_phase(run), "elapsedSeconds": run["elapsedSeconds"],  # v2.21：同 views/lanes 的口径
         "unverified": bool(run.get("unverified"))}  # unverified 只给 views 过滤用，不回出
        for run in list_open(user, now=now)
    ]
