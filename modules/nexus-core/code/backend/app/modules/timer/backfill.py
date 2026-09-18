"""补登：``POST /api/core/timer/backfill``（契约「补登（规范性 · v1.8）」节，唯一事实源）。

拆成独立文件是本模块 contract.md「内部子边界」的纪律（单文件不超过 300 行，比单文件
500 行的上限更严）——``service.py`` 已经在 start/stop/cancel 上够满，backfill 的信封组装/
拒绝规则/归一化整块搬出来，``service.backfill()`` 只留一个薄委托（同
``planner/service.py`` 委托 ``planvalidate.py::validate_plan`` 的既有拆分手法）。

**完全不碰 ``timer_state``**：不 stop 当前计时、不被当前计时阻塞——补登说的是「过去
某段时间我干了活」，与「我现在正在干活」是两件独立的事（契约「与活状态计时的关系」）。
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from ... import timeutil
from ...config import settings
from ..events import service as events_service
from ..events.schemas import SPEC
from ..planner.errors import InvalidInputError

#: 补登信封的 source（契约「补登」节）——与实时计时的 ``timer-backend`` 结构上
#: 不可能撞 dedupeKey，两条独立的防重轨道；也是「表测的」与「回忆填的」两种
#: 证据强度的判别位。
BACKFILL_SOURCE = "manual-backfill"

#: 契约「拒绝规则」：单段会话不可能超过一天，主要拦单位填错（分钟当秒存）。
_MAX_BACKFILL_DURATION_SECONDS = 86400


def _parse_start_at(raw: str) -> datetime:
    """契约「拒绝规则」：不带时区偏移一律 400，**不许猜** ``NEXUS_TZ``。

    猜会在跨时区/夏令时场景静默错日，且错得看不出来——同 config.py 里
    ``NEXUS_TZ`` 无默认值的理由：数据一旦按错误的日界落库，之后每次统计都
    继承这个错。无法解析同样 400。
    """
    if not isinstance(raw, str) or not raw:
        raise InvalidInputError("startAt 缺失或不是字符串")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise InvalidInputError(f"startAt 不是合法 ISO8601：{raw!r}") from exc
    if parsed.tzinfo is None:
        raise InvalidInputError(
            f"startAt 缺少时区偏移：{raw!r}——不允许按 NEXUS_TZ 猜测，"
            f"请带上精确的时区偏移（如 '+08:00' 或 'Z'）"
        )
    return parsed


def backfill(
    task_id: str,
    start_at_raw: str,
    duration_seconds: int,
    user: str,
    *,
    resolve_chain: Callable[..., tuple[dict, str, str]],
    now: Callable[[], datetime],
) -> dict:
    """补登：给「完成了但没计时」的工作补一条真实 ``session.completed``。

    ``resolve_chain``/``now`` 由调用方（``service.py``）注入——两者是
    ``start()``/``stop()`` 已有的归属链硬取与服务端时钟，backfill 与它们
    共用同一套判据（契约要求「与 start() 同一套」），不重新实现一遍。
    """
    # 1. 归属链——与 start() 同一套判据、同一套报错文案（契约「服务端组装的信封」表）。
    _task, project_id, zone_id = resolve_chain(task_id, action="拒绝补登")

    # 2. durationSeconds：正整数、≤86400（契约「拒绝规则」表）。
    if (
        not isinstance(duration_seconds, int)
        or isinstance(duration_seconds, bool)
        or duration_seconds <= 0
    ):
        raise InvalidInputError(f"durationSeconds 必须是正整数，实际 {duration_seconds!r}")
    if duration_seconds > _MAX_BACKFILL_DURATION_SECONDS:
        raise InvalidInputError(
            f"durationSeconds 超过单段会话上限 {_MAX_BACKFILL_DURATION_SECONDS} 秒"
            f"（一天），实际 {duration_seconds}——多半是单位填错（分钟/秒混淆）"
        )

    # 3. startAt：必须带时区、可解析。
    started = _parse_start_at(start_at_raw)

    # 4. 不能补登未来。
    ended = started + timedelta(seconds=duration_seconds)
    right_now = now()
    if ended > right_now:
        raise InvalidInputError(
            f"补登的时间段延伸到未来（startAt+durationSeconds={ended.isoformat()} "
            f"晚于当前时刻 {right_now.isoformat()}）——补登只能记录已经发生的事"
        )

    # 5. dedupeKey：内容派生的确定性键，startAt 必须先归一化到 UTC 再拼——
    #    「+08:00」与等价的「Z」写法字符串不同、指的是同一时刻，不归一化会让
    #    同一件事算出两个不同的键，防重直接失效（契约要害条款）。
    normalized = started.astimezone(timezone.utc).isoformat()
    dedupe_key = f"backfill:{task_id}:{normalized}:{duration_seconds}"

    envelope = {
        "spec": SPEC,
        "id": f"evt_{uuid.uuid4().hex[:12]}",  # 不是防重键，每次组装可不同
        "dedupeKey": dedupe_key,
        "type": "session.completed",
        "user": user,
        "source": BACKFILL_SOURCE,
        "time": ended.isoformat(),  # 会话结束时刻，与 stop() 的 time 语义对齐
        "subject": {"zone": zone_id, "project": project_id, "task": task_id},
        "data": {"durationSeconds": duration_seconds, "startAt": start_at_raw},
        "flags": [],
    }

    result = events_service.ingest(envelope)
    if result.rejected:
        # 自己组的信封被自己的校验拒了 = 实现 bug，响亮失败，不吞
        raise RuntimeError(f"backfill 组装的信封未过事件校验：{result.rejected[0].reason}")

    duplicate = result.duplicate > 0
    if duplicate:
        # duplicate:true 时 event 回显的是**原来那条**，不是刚组装的这条
        # （它的 id 从未落库；contract-schemas.md 明文要求回显原条）。
        stored = events_service.find_by_dedupe(user, BACKFILL_SOURCE, dedupe_key)
        event_out = {"id": stored["id"], "dedupeKey": stored["dedupeKey"], "type": stored["type"]}
    else:
        event_out = {
            "id": envelope["id"],
            "dedupeKey": envelope["dedupeKey"],
            "type": envelope["type"],
        }

    return {
        "recorded": True,  # accepted 或 duplicate 都算「有一条事件对应这次请求」
        "duplicate": duplicate,
        "date": timeutil.local_date(started, settings.tz),  # 归日取 startAt，同 daily_stats.py 口径
        "event": event_out,
    }
