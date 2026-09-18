"""`delete-event` 两个测试文件的公共种子与工具。

**为什么单独一个文件**：模块规矩是单文件 ≤ 300 行（`contract.md`「内部子边界」，
比单文件 500 行的上限更严）。删除工具的用例天然分成两组关注点——
「四道闸拦不拦得住」（`test_delete_event.py`）与「事件 id 不唯一怎么办」
（`test_delete_event_identity.py`）——合在一个文件里会超限。
共用的种子与子进程封装放这里，两边 import，不各写一份。

文件名以 `_` 开头：它不是测试文件，pytest 不会去收集它。
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from app.config import settings
from app.repo import get_db

BACKEND = Path(__file__).resolve().parent.parent
SCRIPT = BACKEND / "scripts" / "delete-event.sh"

#: 被删的那条（照实证：6h54m 的假事实）
TARGET_ID = "evt_2c5e32cc04d6"
TARGET_SECONDS = 24850
KEEP_COUNT = 3


def make_event(*, event_id: str, dedupe: str, seconds: int, time_: str) -> dict:
    return {
        "spec": "yq-event/v1",
        "id": event_id,
        "dedupeKey": dedupe,
        "type": "session.completed",
        "user": "u_local",
        "source": "test",
        "time": time_,
        "subject": {"zone": "z_seed", "project": "p_real01", "task": "t_real01"},
        "data": {"durationSeconds": seconds, "startAt": time_},
        "flags": [],
    }


@pytest.fixture()
def seeded_events():
    """1 条要删的假事实 + 3 条必须原封不动的真实事实。

    要删的那条**指向真实存在的实体**——这正是 prune-orphan-events 抓不到它、
    因而需要本工具的原因。
    """
    db = get_db()
    db["projects"].insert_one({"id": "p_real01", "zoneId": "z_seed", "name": "真实项目"})
    db["tasks"].insert_one({"id": "t_real01", "projectId": "p_real01", "name": "真实任务"})

    db["events"].insert_one(
        make_event(
            event_id=TARGET_ID, dedupe="timer:sess_bogus",
            seconds=TARGET_SECONDS, time_="2026-08-03T02:00:00+08:00",
        )
    )
    keep = []
    for i in range(KEEP_COUNT):
        dedupe = f"timer:sess_real{i}"
        keep.append(dedupe)
        db["events"].insert_one(
            make_event(
                event_id=f"evt_real{i:012d}", dedupe=dedupe,
                seconds=1800, time_=f"2026-07-0{i + 1}T10:00:00+08:00",
            )
        )
    return keep


def env() -> dict[str, str]:
    """子进程环境：显式带上测试库名与时区，绝不继承出一个指向真库的 env。"""
    result = dict(os.environ)
    result["NEXUS_DB_NAME"] = settings.db_name
    result["NEXUS_MONGO_URI"] = settings.mongo_uri
    result["NEXUS_TZ"] = "Asia/Shanghai"
    result.pop("COCKPIT_DELETE_YES", None)
    return result


def ids() -> list[str]:
    return sorted(doc["id"] for doc in get_db()["events"].find({}, {"id": 1, "_id": 0}))


def run(*args, env_=None, timeout=120):
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        capture_output=True, text=True, env=env_ or env(), timeout=timeout,
    )
