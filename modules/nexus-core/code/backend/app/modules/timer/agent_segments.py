"""代理「只算以后的时间」分段（契约 v2.48，``POST /agents/{runId}/place`` 的 ``timeFrom:"now"``）。

在跑的运行文档上有 ``cuts``：每次「只算以后」落一条 ``{at, projectId, taskId?}`` = 那个去向**算到 at 为止**。
结束时 ``segments_of`` 把运行切成一段一个去向（最后一段 = 结束时的去向）进事实 ``data.segments``，
每段自带干活秒数与按日切分，投影按段各写各的目标；整条运行的 ``durationSeconds`` / ``days`` 等不变。
纯函数，不碰库。ponytail: 每段干活秒数各自取整，一次切分最多差 1 秒；要精确得把 cut 对齐到整秒。
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from .agent_working import working_days


def segments_of(snap: dict, started: datetime, ended: datetime, tz: ZoneInfo) -> list[dict]:
    """没有切分、或切出来只有一段有长度 → ``[]``（按整条运行结算，subject 已是最终去向）。"""
    cuts = sorted(snap.get("cuts") or [], key=lambda c: datetime.fromisoformat(c["at"]))
    out, since = [], started
    last = {"projectId": snap["projectId"], "taskId": snap.get("taskId")}
    for cut in [*cuts, None]:
        until = ended if cut is None else min(datetime.fromisoformat(cut["at"]), ended)
        if until > since:
            target = last if cut is None else cut
            working, days = working_days(snap.get("phases"), since, until, tz)
            out.append({"projectId": target["projectId"], **({"taskId": target["taskId"]} if target.get("taskId") else {}),
                        "startAt": since.isoformat(), "endAt": until.isoformat(),
                        "durationSeconds": int((until - since).total_seconds()), "workingSeconds": working, "days": days})
            since = until
    return out if len(out) > 1 else []
