"""只读聚合：把 events/planner/projector 三个子边界的既有只读函数拼成一份全量导出。

**红线**：本文件不 import 任何子边界的 ``repo.py``，只调用对方 ``service.py``
的公开函数（跨子边界纪律，rules.md §7.3 红线 2）。projector 例外走
``handlers/*.py`` 的公开读函数——这是 rules.md §7.4 钉死的指定读路径，
``views/queries.py`` 已经这么用，本文件同构复用，不新开口子。

因此本文件**不直连 mongo**，任何一条聚合数据的口径改变都应该发生在被聚合的
那个子边界里，不在这里重新实现或过滤。

导出与写路径零关联：不碰 ``timer_state``，不调用任何 create/update/delete，
纯粹把已有只读函数的结果拼一个 dict。事件**原样**返回（含 ``id``/``type``/
``time``/``subject``/``data`` 等全部字段，`_id` 已在 ``events/repo.py`` 层剔除）
——用户要看原始事实，这里不做任何塑形、过滤或 join（与 ``views/queries.py``
特意精简字段、按占比/百分数塑形的读端调性不同，是有意的：那些是给 UI 用的
展示形状，这个是给用户自己的数据副本）。
"""

from __future__ import annotations

from datetime import datetime, timezone

from ...config import LOCAL_USER
from ..events import service as events_service
from ..planner import service as planner_service
from ..projector.handlers import current as current_projection
from ..projector.handlers import daily_stats as daily_stats_projection


def export_all() -> dict:
    """``GET /api/core/export`` 的聚合体（contract.md「只读导出」）。

    ``zones``/``projects``/``tasks`` 用 planner 的原始列表函数（非 tree/gantt
    塑形后的形状）——导出是给用户自己的数据副本，不是某个 UI 组件的专用视图，
    没有理由丢字段或按项目分组。
    """
    return {
        "zones": planner_service.list_zones(),
        "projects": planner_service.list_projects(),
        "tasks": planner_service.list_tasks(),
        "events": events_service.iter_all_events(),
        "projections": {
            "proj_current": current_projection.read_current(LOCAL_USER),
            "proj_daily_stats": daily_stats_projection.read_daily_stats(LOCAL_USER),
        },
        "exportedAt": datetime.now(timezone.utc).isoformat(),
    }
