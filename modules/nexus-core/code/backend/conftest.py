"""pytest 引导：sys.path + 测试环境 + 库清理。它是引导文件，不是子边界的一部分。

**测试环境注入**：``app.config`` 在 import 时就解析 env 并在缺失时立即失败
（这是被测行为本身，不能松）。所以测试进程必须在 import ``app`` 前
备好 ``NEXUS_MONGO_URI`` / ``NEXUS_DB_NAME``。这里用 ``setdefault``：
外部（如 CI）显式给了就用外部的，没给就指向本机 Mongo
（任务单「存储环境」节），库名用**独立测试库** ``nexus_core_test``——
绝不碰生产库 ``nexus_core``。

这不是业务代码读 env（那仍只在 ``app/config.py``），是测试夹具在**造**环境。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

os.environ.setdefault("NEXUS_MONGO_URI", "mongodb://127.0.0.1:27017")
os.environ.setdefault("NEXUS_DB_NAME", "nexus_core_test")
# UTC，不是猜出来的默认值：它只是「测试夹具选了哪个合法时区」，与
# app/config.py 的「NEXUS_TZ 无默认值、缺失即 die」不矛盾（生产配置不许弱默认值
# 是一回事，这里是测试环境显式选定的固定值，是另一回事）。选 UTC 是为了让全部
# 既有断言（写死 +00:00 偏移、按 UTC 日期比较）在归日改按 NEXUS_TZ 后继续零
# 回归；真正验证「非 UTC 时区下跨日界」的用例（契约 v0.9 的要害断言）走
# tests/test_timezone_and_rebuild.py，那边直接构造 ZoneInfo/替换的 settings，
# 不依赖这个全局默认值。
os.environ.setdefault("NEXUS_TZ", "UTC")

# ── 硬拒绝：库名不像测试库就不跑（2026-08-01 事故）──────────────
# 上面那行 `setdefault` 是**弱防护**：它只在「你什么都没传」时生效，
# 而真正危险的场景恰恰是「你传了、但传错了」——
# 传 NEXUS_DB_NAME=nexus_core 进来，setdefault 什么也不做，
# 下面的 clean_db（autouse）就照着**真库**逐个集合 delete_many，
# 62 个测试清 62 遍，然后报「62 passed」。
#
# 实证：本项目的用户计时历史就是这么没的（曾出过事故：测试意外连上生产库，
# 62 个测试各清一遍，然后报「62 passed」）。
# 事实是 append-only 设计，没有软删除、没有回收站——**丢了就是丢了**。
#
# 所以这里必须是 die，不是默认值。道理和其他地方一致：
# 「关键路径缺文件必须 die；唯一不许的是静默跳过后报成功」——
# 这里是「静默清库后报测试通过」，同一形状。
_db_name = os.environ["NEXUS_DB_NAME"]
if not _db_name.endswith("_test"):
    raise SystemExit(
        f"\n❌ 拒绝跑测试：NEXUS_DB_NAME={_db_name!r} 不是测试库。\n"
        f"   测试套件的 clean_db 是 autouse，每个测试前会 delete_many 清空全部集合"
        f"（events/timer_state/proj_current/proj_daily_stats/zones/projects/tasks/"
        f"name_registry/counters）。\n"
        f"   指向真库 = 数据全没，且事实是 append-only、无备份、不可恢复。\n"
        f"   要跑测试：别传 NEXUS_DB_NAME（默认 nexus_core_test），"
        f"或显式传一个以 _test 结尾的库名。\n"
    )

import pytest  # noqa: E402

_COLLECTIONS = (
    "events", "timer_state", "proj_current", "proj_daily_stats",
    "zones", "projects", "tasks", "name_registry", "counters",
    # v1.6 审计流水（F-ACTOR-2）。**新增集合必须同批加进这张表**：
    # 漏了它，审计记录会跨测试累积，"这条流水是本用例写的"就再也不成立，
    # 而症状是别的用例莫名其妙地多几条——最难查的那种。
    "planner_audit",
)


@pytest.fixture(autouse=True)
def clean_db():
    """每个测试从空库开始——测试之间不许互相喂状态。"""
    from app.repo import get_db  # noqa: PLC0415 —— 必须晚于上面的 env 注入

    db = get_db()
    for name in _COLLECTIONS:
        db[name].delete_many({})
    yield


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient  # noqa: PLC0415

    from app.main import app  # noqa: PLC0415

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def seeded():
    """planner 最小种子。返回 {任务名: 任务文档}——J10 后任务 id 由系统生成，
    测试从这里拿真实 id，不再写死 t_45 之类的编号。"""
    import seed_planner  # noqa: PLC0415

    return seed_planner.seed()
