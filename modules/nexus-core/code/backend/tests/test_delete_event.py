"""单条事实删除工具 · **四道闸 + T5**（任务单 2026-08-03）。

「事件 id 不唯一怎么办」那组关注点在 `test_delete_event_identity.py`，
公共种子在 `_delete_event_common.py`（拆开是因为模块规矩单文件 ≤ 300 行）。

**全部用例只跑测试库。** conftest.py 那条「库名不以 `_test` 结尾就 SystemExit」
的硬护栏是 2026-08-01 清空真库事故之后立的——本文件不绕它、不改它，
反而处处用 ``settings.db_name``（已被那条护栏保证是测试库）当目标。

覆盖：
- T4 四道闸齐：①默认 dry-run ②--apply 要重打库名 ③删前先备份 ④删后重建投影
- T5 传不存在的 id：响亮失败（非 0 退出 + 说清原因），**不静默退 0**
- P7 库名白名单 / NEXUS_TZ 必填
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from app.config import settings
from app.repo import get_db

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import delete_event as deleter  # noqa: E402

from _delete_event_common import (  # noqa: E402
    KEEP_COUNT,
    SCRIPT,
    TARGET_ID,
    TARGET_SECONDS,
    env,
    ids,
    run,
    seeded_events,  # noqa: F401 —— pytest 夹具靠 import 进来生效
)


# ───────────────────────────────────────────── T5 不存在的 id


def test_t5_missing_id_fails_loudly(seeded_events):
    """T5：传一个不存在的 id → **响亮失败**（非 0 + 说清原因），不静默退 0。

    「什么都没找到但报成功」会让人以为删干净了，然后带着那条假事实继续过日子。
    """
    before = ids()

    result = run(settings.db_name, "evt_does_not_exist")

    assert result.returncode != 0, "查无此 id 必须非 0 退出——静默退 0 是这个工具最糟的失败方式"
    assert "库里没有" in result.stderr, result.stderr
    assert "evt_does_not_exist" in result.stderr, "报错要指名道姓说是哪个 id"
    assert ids() == before, "定位失败时一条都不许动"


def test_t5_missing_id_also_fails_under_apply(seeded_events, tmp_path):
    """--apply 下同样响亮失败，且**走不到备份/删除那步**。"""
    before = ids()
    bad_env = env()
    bad_env["COCKPIT_DELETE_YES"] = settings.db_name
    bad_env["COCKPIT_ARCHIVE_DIR"] = str(tmp_path / "never-used")

    result = run(settings.db_name, "evt_does_not_exist", "--apply", env_=bad_env)

    assert result.returncode != 0
    assert "定位失败，什么都没动" in result.stderr
    assert ids() == before


def test_locate_raises_on_missing(seeded_events):
    """核心函数层面的 T5：查无此 id 抛 ``EventNotFound``，不返回 None。"""
    with pytest.raises(deleter.EventNotFound):
        deleter.locate(get_db(), "evt_nope")


# ───────────────────────────────────────────── T4 闸一：默认 dry-run


def test_t4_gate1_dry_run_deletes_nothing(seeded_events):
    """T4①：默认 dry-run，一条都不删，且把**被删那条的完整内容**打出来。"""
    before = ids()

    result = run(settings.db_name, TARGET_ID)

    assert result.returncode == 0, result.stderr
    assert ids() == before, "dry-run 不许动数据"
    assert "DRY-RUN" in result.stdout
    # 前后条数预告
    assert f"events 集合条数：{KEEP_COUNT + 1} → {KEEP_COUNT}" in result.stdout
    # 被删那条的完整内容（逐字段核）
    assert f'"{TARGET_ID}"' in result.stdout
    assert str(TARGET_SECONDS) in result.stdout
    assert '"dedupeKey"' in result.stdout


# ───────────────────────────────────────────── T4 闸二：重打库名


def test_t4_gate2_wrong_confirmation_aborts_without_deleting(seeded_events):
    """T4②：确认时打错库名 → 中止，且一条都没删。"""
    before = ids()
    bad_env = env()
    bad_env["COCKPIT_DELETE_YES"] = "nexus_core"  # 与目标库不符

    result = run(settings.db_name, TARGET_ID, "--apply", env_=bad_env)

    assert result.returncode != 0
    assert "已中止，什么都没动" in result.stderr
    assert ids() == before


def test_t4_gate2_no_confirmation_in_noninteractive_aborts(seeded_events):
    """不给 COCKPIT_DELETE_YES 且非交互 → 说清「读不到确认」后中止，不是无理由退出。"""
    before = ids()

    result = subprocess.run(
        ["bash", str(SCRIPT), settings.db_name, TARGET_ID, "--apply"],
        capture_output=True, text=True, env=env(), timeout=120, stdin=subprocess.DEVNULL,
    )

    assert result.returncode != 0
    assert "读不到确认输入" in result.stderr
    assert ids() == before


# ───────────────────────────────────────────── T4 闸三：删前先备份


def test_t4_gate3_aborts_when_backup_fails(seeded_events, tmp_path):
    """T4③：备份失败 → 中止且一条都没删。

    「删除前唯一的保险」必须真的存在。这里把 COCKPIT_ARCHIVE_DIR 指到一个
    **不是 git 仓**的目录，backup.sh 会 die，删除必须跟着停。
    """
    before = ids()
    bad_env = env()
    bad_env["COCKPIT_DELETE_YES"] = settings.db_name
    bad_env["COCKPIT_ARCHIVE_DIR"] = str(tmp_path / "not-a-git-repo")
    (tmp_path / "not-a-git-repo").mkdir()

    result = run(settings.db_name, TARGET_ID, "--apply", env_=bad_env)

    assert result.returncode != 0
    assert "备份失败" in result.stderr
    assert "一条都没删" in result.stderr
    assert ids() == before


# ───────────────────────────────────────────── T4 闸四：真删 + 重建投影


def test_t4_gate4_apply_deletes_exactly_one_and_rebuilds(seeded_events, tmp_path):
    """T4④：恰好删 1 条、其余一条不少、投影重建后与剩余事实一致。

    走**完整的 .sh**（含真备份），不是直接调 Python——闸门本身也是交付物。
    """
    keep = seeded_events
    db = get_db()

    # 先污染投影：让它带着那条假事实的 24850 秒，好证明重建把它清掉了
    from app.modules.projector.rebuild import rebuild

    rebuild()
    dirty = sum(row["seconds"] for row in db["proj_daily_stats"].find({}, {"_id": 0}))
    assert dirty == KEEP_COUNT * 1800 + TARGET_SECONDS, "前置：投影应先带着假事实的贡献"

    archive = tmp_path / "archive-repo"
    archive.mkdir()
    subprocess.run(["git", "init", "-q", str(archive)], check=True, timeout=60)

    apply_env = env()
    apply_env["COCKPIT_DELETE_YES"] = settings.db_name
    apply_env["COCKPIT_ARCHIVE_DIR"] = str(archive)

    result = run(settings.db_name, TARGET_ID, "--apply", env_=apply_env, timeout=300)
    assert result.returncode == 0, result.stdout + result.stderr

    # 恰好删 1 条
    assert "已删除 1 条" in result.stdout
    assert f"events 条数：{KEEP_COUNT + 1} → {KEEP_COUNT}" in result.stdout
    # 其余一条不少
    assert TARGET_ID not in ids()
    assert {d["dedupeKey"] for d in db["events"].find({}, {"dedupeKey": 1, "_id": 0})} == set(keep)
    # 投影与剩余事实一致（假事实的 24850 秒没了）
    after = sum(row["seconds"] for row in db["proj_daily_stats"].find({}, {"_id": 0}))
    assert after == KEEP_COUNT * 1800
    assert "投影已重建" in result.stdout
    # 备份真的落地了（P3 不是走过场）
    assert list(archive.rglob("*.archive")), "--apply 应留下一份可还原的备份"


def test_rebuild_matches_fresh_rebuild(seeded_events):
    """更强形式：删除后的投影 == 从剩余事实重新跑一遍 rebuild 的结果。"""
    from app.modules.projector.rebuild import rebuild

    db = get_db()
    doc = deleter.locate(db, TARGET_ID)
    deleter.delete_one(db, doc)
    deleter.rebuild_projections()
    after_delete = sorted(
        (r["date"], r["projectId"], r["seconds"])
        for r in db["proj_daily_stats"].find({}, {"_id": 0})
    )

    rebuild()  # 幂等：再跑一次应完全一致
    fresh = sorted(
        (r["date"], r["projectId"], r["seconds"])
        for r in db["proj_daily_stats"].find({}, {"_id": 0})
    )
    assert after_delete == fresh
    assert sum(s for _, _, s in fresh) == KEEP_COUNT * 1800


# ───────────────────────────────────────────── P7 库名白名单 / TZ


@pytest.mark.parametrize("bad_db", ["nexus_cores", "nexus-core", "admin", "nexus_core_v2"])
def test_p7_rejects_db_outside_allowlist(bad_db):
    """P7：库名不在白名单直接拒（打错一个字母就停住）。"""
    result = run(bad_db, TARGET_ID, timeout=60)
    assert result.returncode != 0
    assert "不在允许名单内" in result.stderr


def test_requires_db_name():
    """库名必填，没有默认值（同 restore.sh / prune）。"""
    result = run(timeout=60)
    assert result.returncode != 0
    assert "库名必填" in result.stderr


def test_requires_event_id():
    """只给库名不给 id → 拒；并说清「不接受查询条件」这个有意的限制。"""
    result = run(settings.db_name, timeout=60)
    assert result.returncode != 0
    assert "缺事件 id" in result.stderr
    assert "不接受任何查询条件" in result.stderr


def test_requires_explicit_tz(seeded_events):
    """NEXUS_TZ 缺失即拒：重建投影按它归日，猜错会把历史静默记到错误的日子。"""
    no_tz = env()
    no_tz.pop("NEXUS_TZ", None)
    result = run(settings.db_name, TARGET_ID, env_=no_tz, timeout=60)
    assert result.returncode != 0
    assert "NEXUS_TZ 未设置" in result.stderr
