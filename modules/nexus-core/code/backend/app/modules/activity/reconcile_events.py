"""对账写台账的三种事件（契约 v2.38「四」）：补写 ``session.completed``、改目标 ``session.reassigned``、作废 ``session.voided``。
走 nexus-core 内部写事实的入口 ``events_service.ingest(internal=True)``（同补登 / 改挂），不走公开入口。

事件 id 由防重键派生（同键同 id）：先在块文档里记下意图、再写事件，崩在中间重来写出的是同一条，``runs`` 里记的 id 始终对得上。
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from ... import config
from ..events import service as events_service
from ..events.schemas import SPEC
from ..planner import unclassified
from ..planner.errors import InvalidInputError, NotFoundError
from ..timer import service as timer_service

SOURCE = "activity-block"
#: 取不到目标（任务 / 项目已删、断链）——这一条这轮写不成，不是对账的 bug
UNWRITABLE = (timer_service.UnknownTaskError, InvalidInputError, NotFoundError)
_AI_AUTO = {"generated": True, "auto": True, "confirmed": False}
_AI_BY = {"rule": _AI_AUTO, "ai-direct": _AI_AUTO, "human": {"generated": True, "confirmed": True},
          "ai": {"generated": True, "confirmed": True}}


def det_id(dedupe_key: str) -> str:
    return "evt_" + hashlib.sha256(dedupe_key.encode()).hexdigest()[:12]


def iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).astimezone(config.settings.tz).isoformat(timespec="milliseconds")


def to_dt(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, timezone.utc)


def _chain(run: dict) -> dict:
    if run.get("bucket"):  # 只给项目：落进该项目的「未分类」桶，没有就建（同确认建议）
        unclassified.find(run["bucket"]) or unclassified.create(run["bucket"], None)
    _task, project, zone = timer_service._resolve_task_chain(run["task"], action="拒绝对账")  # noqa: SLF001
    return {"zone": zone, "project": project, "task": run["task"]}


def _ingest(envelope: dict) -> bool:
    res = events_service.ingest(envelope, internal=True)
    if res.rejected:
        raise RuntimeError(f"{SOURCE} 组装的信封未过事件校验：{res.rejected[0].reason}")
    return res.accepted > 0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_completed(user: str, date: str, key: str, run: dict) -> bool:
    ai = {**_AI_BY.get(run["via"], _AI_BY["human"]), "block": {"date": date, "blockKey": key}}
    return _ingest({"spec": SPEC, "id": run["eventId"], "dedupeKey": run["dedupeKey"], "type": "session.completed",
                    "user": user, "source": SOURCE, "time": iso(run["endTs"]), "subject": _chain(run),
                    "data": {"startAt": iso(run["startTs"]), "durationSeconds": run["seconds"], "mode": "do"},
                    "flags": [], "ai": ai})


def write_reassign(user: str, run: dict, actor: str) -> None:
    stored = events_service.find_by_dedupe(user, SOURCE, run["dedupeKey"])
    if stored is None:
        return
    target = _chain(run)
    data = stored.get("data") or {}
    for _ in range(5):
        chain = events_service.reassignments_of(stored)
        last = chain[-1] if chain else None
        current = (last or stored)["subject"]
        if current.get("task") == target["task"]:
            return
        seq = last["data"]["seq"] if last else 0
        if _ingest({"spec": SPEC, "id": det_id(f"reassign:{stored['id']}:{seq + 1}"), "dedupeKey": f"reassign:{stored['id']}:{seq + 1}",
                    "type": events_service.REASSIGNED_TYPE, "user": user, "source": events_service.REASSIGN_SOURCE, "time": _now(),
                    "subject": target,
                    "data": {"sessionEventId": stored["id"], "seq": seq + 1, "fromTaskId": current.get("task"),
                             "fromProjectId": current.get("project"), "toTaskId": target["task"], "toProjectId": target["project"],
                             "actor": actor, "session": {"source": SOURCE, "dedupeKey": run["dedupeKey"], "startAt": data.get("startAt"),
                                                         "durationSeconds": data.get("durationSeconds")}},
                    "flags": []}):
            return


def write_void(user: str, void: dict, actor: str) -> bool:
    """``void`` = 块文档 ``voids`` 里的 ``{eventId, dedupeKey, reason}``。被作废的事件没写成（崩在写之前）就没有什么可作废。
    ``data.session`` 与 ``from*`` 是追加的抄录（同 ``session.reassigned``）：投影的 handler 不回头查台账，要知道当前归属在哪、减多少。"""
    stored = events_service.find_by_dedupe(user, SOURCE, void["dedupeKey"])
    if stored is None:
        return False
    chain = events_service.reassignments_of(stored)
    current = (chain[-1] if chain else stored)["subject"]
    data = stored.get("data") or {}
    key = f"void:{stored['id']}"
    return _ingest({"spec": SPEC, "id": det_id(key), "dedupeKey": key, "type": events_service.VOIDED_TYPE, "user": user,
                    "source": SOURCE, "time": _now(), "subject": stored["subject"],
                    "data": {"voidedEventId": stored["id"], "actor": actor, "reason": void["reason"],
                             "fromTaskId": current.get("task"), "fromProjectId": current.get("project"),
                             "session": {"source": SOURCE, "dedupeKey": void["dedupeKey"], "startAt": data.get("startAt"),
                                         "durationSeconds": data.get("durationSeconds")}},
                    "flags": []})
