"""单条事实删除工具 · **事件 id 不唯一时的行为**（任务单 2026-08-03 的旁支）。

⚠️ **事件的 ``id`` 字段不唯一。** 防重唯一索引建在 ``(user, source, dedupeKey)`` 上
（见 ``events/repo.py``），契约 B2 明确要求「同 ``id`` 不同 ``dedupeKey`` 两条都要落库」。

所以 ``{"id": <给定 id>}`` 可能匹配到多条。**「一条命令删掉了两条事实」是这个工具
最容易犯、也最难挽回的错**——本文件把「拒绝执行、要求消歧」这个行为钉死。

四道闸与 T5 在 `test_delete_event.py`，公共种子在 `_delete_event_common.py`
（拆开是因为模块规矩单文件 ≤ 300 行）。
"""

from __future__ import annotations

import sys
from pathlib import Path

from app.config import settings
from app.repo import get_db

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import delete_event as deleter  # noqa: E402

from _delete_event_common import (  # noqa: E402
    TARGET_ID,
    TARGET_SECONDS,
    ids,
    make_event,
    run,
    seeded_events,  # noqa: F401 —— pytest 夹具靠 import 进来生效
)


def _add_same_id_neighbor() -> None:
    """再插一条**同 id、不同 dedupeKey** 的事实（契约 B2 允许的合法状态）。"""
    get_db()["events"].insert_one(
        make_event(
            event_id=TARGET_ID, dedupe="timer:sess_other",
            seconds=999, time_="2026-08-03T05:00:00+08:00",
        )
    )


def test_ambiguous_id_is_refused(seeded_events):
    """匹配到多条时**拒绝执行**并列出候选，不许「取第一条」。

    宁可让人多打一个参数，也不替他猜哪条才是他要的。
    """
    _add_same_id_neighbor()
    before = ids()

    result = run(settings.db_name, TARGET_ID)

    assert result.returncode != 0
    assert "匹配到 2 条事实，拒绝执行" in result.stderr
    assert "--dedupe-key" in result.stderr, "要告诉人怎么消歧"
    assert ids() == before, "拒绝执行时一条都不许动"


def test_ambiguous_id_lists_candidates_with_distinguishing_info(seeded_events):
    """候选列表要给得出**能用来分辨**的信息，否则「你自己选」等于没说。"""
    _add_same_id_neighbor()

    result = run(settings.db_name, TARGET_ID)

    assert "timer:sess_bogus" in result.stderr
    assert "timer:sess_other" in result.stderr
    assert str(TARGET_SECONDS) in result.stderr, "时长是人最容易认出「哪条是那条」的字段"


def test_dedupe_key_disambiguates_to_exactly_one(seeded_events):
    """给了 ``--dedupe-key`` 就能精确定位到一条，且**删的是 _id，不误伤同 id 的邻居**。

    这条用例的真正目的是证明删除**没有**拿 ``id`` 当条件——
    拿 id 当删除条件的实现会在这里把两条一起删掉。
    """
    db = get_db()
    _add_same_id_neighbor()

    doc = deleter.locate(db, TARGET_ID, "timer:sess_bogus")
    assert doc["data"]["durationSeconds"] == TARGET_SECONDS

    deleter.delete_one(db, doc)

    remaining = list(db["events"].find({"id": TARGET_ID}, {"_id": 0}))
    assert len(remaining) == 1, "同 id 的邻居必须还在"
    assert remaining[0]["dedupeKey"] == "timer:sess_other"


def test_dedupe_key_that_matches_nothing_still_fails_loudly(seeded_events):
    """``--dedupe-key`` 给错 → 仍是「查无此事实」的响亮失败，不是静默成功。"""
    _add_same_id_neighbor()
    before = ids()

    result = run(settings.db_name, TARGET_ID, "--dedupe-key", "timer:sess_nope")

    assert result.returncode != 0
    assert "库里没有" in result.stderr
    assert ids() == before


def test_locate_raises_ambiguous(seeded_events):
    """核心函数层面：多条匹配抛 ``AmbiguousEventId``，不返回第一条。"""
    import pytest

    _add_same_id_neighbor()
    with pytest.raises(deleter.AmbiguousEventId):
        deleter.locate(get_db(), TARGET_ID)
