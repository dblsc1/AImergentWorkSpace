"""``session.completed`` → ``proj_daily_stats``（甘特图「事实」图层的数据源）。

三条铁则（与 ``handlers/current.py`` 相同）：

- **幂等**：同一事件投两次，投影结果相同——靠 ``repo.apply_daily_stat`` 的
  ``dedupeKey`` 幂等守卫，不靠调用方自觉、也不靠 events 层的防重（那层挡的是
  「同一条事件别落库两次」，这里挡的是「同一条事件别在这个 handler 里被算两次」，
  两层各管各的，缺一不可）。
- **只写自己的投影集合**（``proj_daily_stats``），别的集合一个都不碰。
- **禁止发新事件**：本文件不 import ``events`` 子边界的任何东西。

对外公开（同 rules.md §7.4 的窄接口约定，与 ``handlers/current.py`` 同构）：
``handle(envelope)`` / ``read_daily_stats(user, date_from=None, date_to=None)``。
``read_daily_stats`` 是 views/gantt 的指定读路径——views 无 repo，不自己碰投影集合。

## 归日按 ``NEXUS_TZ``，不是 UTC（契约「日界与时区」v0.9）

``data.startAt`` 定了「哪一刻」，``NEXUS_TZ`` 定了「那一刻落在哪一天」——
两者不是一回事。UTC+8 的用户在本地 8 月 2 日 01:30 干活，``startAt`` 的 UTC 数值
是 8 月 1 日 17:30，若直接取 UTC 日期会把这段工作记到 8 月 1 日，
与用户的墙钟不符（这类错最难发现：图不会看着崩）。归日与
``views/gantt`` 的 ``today`` 共用 ``timeutil.local_date``，不许各自换算一遍。

## 为什么 ``date`` 从 ``data.startAt`` 取，不是 ``time`` 也不是 ``recordedAt``

三个候选时间戳答的是三个不同问题：

- ``time``（事件的「发生时刻」）—— 对 ``session.completed`` 而言是**会话结束**那一刻
  （见 ``timer/service.py::stop``：``"time": now.isoformat()``）。用它归日，
  一段跨零点的计时（比如 23:40–00:20）会被记到**结束那天**，但用户体感里
  这段工作是「昨晚」做的，不是「今天凌晨」做的——多数记录习惯（包括常见的
  时间管理工具）把跨零点的一段归到**开始**那天。
- ``recordedAt``（服务端收到并盖章的时刻）——这是基础设施时间戳，不是业务时间戳。
  补交事件（B7：``time`` 可以是过去）用它归日会把「昨天做的事」记到「今天补录」的日子上，
  语义上完全错误。
- ``data.startAt``（这段计时**开始**的时刻，``timer/service.py::stop`` 组装信封时
  写入 ``data["startAt"] = state["startAt"]``）——**这段工作实际发生在哪天**的最准确回答。
  跨零点的一段计时归到开始那天，这是本 handler 的既定选择：用户点「开始」时锚定的
  是「我现在要开始干活」，那一刻所在的日期才是这段工作的「主场」。

**外部事件也可能没有 ``data.startAt``**（yq-event/v1 是开放标准，`source` 不一定是
本模块的 timer）——此时静默跳过，不猜测、不拿 ``time`` 顶替（那会引入上面说的偏差）。
"""

from __future__ import annotations

from datetime import datetime

from ....config import settings
from ....timeutil import local_date
from .. import repo


def handle(envelope: dict) -> None:
    """吃一条已落库的 ``session.completed``，累计到 ``proj_daily_stats``。

    ``proj_daily_stats`` 以 ``projectId`` 为主键的一部分（见契约的形状），
    与 ``current.handle`` 不同——那边 project/task 各自独立累计，project 缺失
    也不影响 task 那份统计；这里「按项目分组的一天」是最小可用单元，缺了
    ``projectId`` 这行数据没有归属，写不出有意义的一条，静默跳过。
    ``taskId`` 仍是可选的（B5：无具体任务的事件，如通用成长点，可省）。
    """
    subject = envelope.get("subject") or {}
    project_id = subject.get("project")
    task_id = subject.get("task")
    if not project_id:
        return

    data = envelope.get("data") or {}
    seconds = data.get("durationSeconds")
    if not isinstance(seconds, (int, float)) or isinstance(seconds, bool) or seconds <= 0:
        return  # 没有正时长就没有可累计的东西；坏载荷不炸投影（事件本身已落库）

    start_at = data.get("startAt")
    if not isinstance(start_at, str) or not start_at:
        return  # 外部 source 可能没有这个字段（开放标准）；不用 time 猜测顶替

    try:
        moment = datetime.fromisoformat(start_at)
    except ValueError:
        return  # 格式不合法：坏载荷不炸投影，事件本身已落库、可事后排查
    if moment.tzinfo is None:
        return  # 契约要求 startAt 带偏移；缺偏移视同坏载荷，同上不炸投影
    date = local_date(moment, settings.tz)

    repo.apply_daily_stat(
        user=envelope["user"],
        dedupe_key=envelope["dedupeKey"],
        date=date,
        project_id=project_id,
        task_id=task_id,
        seconds=int(seconds),
    )


def read_daily_stats(
    user: str, date_from: str | None = None, date_to: str | None = None
) -> list[dict]:
    """views 的指定读路径（rules.md §7.4 公开接口）。返回按天聚合的原始文档
    （``{date, projectId, taskId, seconds}``）；按项目再汇总是 ``views/queries.py`` 的活。
    """
    return repo.read_daily_stats(user, date_from=date_from, date_to=date_to)
