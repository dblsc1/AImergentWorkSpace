"""孤儿事实清理工具（2026-08-03 · P1–P7 + 反向验证断言）。

**全部用例只跑测试库。** conftest.py 那条「库名不以 `_test` 结尾就 SystemExit」
的硬护栏是 2026-08-01 清空真库事故之后立的——本文件不绕它、不改它，
反而处处用 ``settings.db_name``（已被那条护栏保证是测试库）当目标。

覆盖：
- P1 默认 dry-run，一条都不删
- P2 --apply 必须重打库名，不符即中止且不删
- P3 备份失败即中止且不删
- P4 判据（含**反向验证**：改用 ``_id`` → 断言必须变红）
- P5 删完投影与剩余事实一致
- P6 对照输出里能看到条数与保留项
- P7 库名不在白名单直接拒
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.config import settings
from app.repo import get_db

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import prune_orphan_events as prune  # noqa: E402

BACKEND = Path(__file__).resolve().parent.parent
SCRIPT = BACKEND / "scripts" / "prune-orphan-events.sh"

#: 3 条真实 + 5 条孤儿（规格指定的种子规模）
REAL_COUNT = 3
ORPHAN_COUNT = 5


def _event(*, dedupe: str, project: str, task: str | None, seconds: int, time_: str) -> dict:
    subject: dict = {"zone": "z_seed", "project": project}
    if task is not None:
        subject["task"] = task
    return {
        "spec": "yq-event/v1",
        "id": f"evt_{dedupe}",
        "dedupeKey": dedupe,
        "type": "session.completed",
        "user": "u_local",
        "source": "test",
        "time": time_,
        "subject": subject,
        "data": {"durationSeconds": seconds, "startAt": time_},
        "flags": [],
    }


@pytest.fixture()
def seeded_orphans():
    """3 条真实（实体健在）+ 5 条孤儿（实体已被 teardown 删掉）。

    返回 (real_dedupes, orphan_dedupes)。孤儿指向的 p_/t_ id **从不入库**——
    这就是 E2E 夹具「造完自己清」之后留下的形状。
    """
    db = get_db()
    db["projects"].insert_one({"id": "p_real01", "zoneId": "z_seed", "name": "真实项目"})
    db["tasks"].insert_one({"id": "t_real01", "projectId": "p_real01", "name": "真实任务"})

    real, orphan = [], []
    for i in range(REAL_COUNT):
        dedupe = f"real-{i}"
        real.append(dedupe)
        db["events"].insert_one(
            _event(
                dedupe=dedupe, project="p_real01", task="t_real01",
                seconds=1800, time_=f"2026-07-0{i + 1}T10:00:00+08:00",
            )
        )
    for i in range(ORPHAN_COUNT):
        dedupe = f"orphan-{i}"
        orphan.append(dedupe)
        db["events"].insert_one(
            _event(
                dedupe=dedupe, project=f"p_gone{i}", task=f"t_gone{i}",
                seconds=1, time_=f"2026-08-01T16:4{i}:00+08:00",
            )
        )
    return real, orphan


def _dedupes(collection_filter: dict | None = None) -> set[str]:
    return {
        doc["dedupeKey"]
        for doc in get_db()["events"].find(collection_filter or {}, {"dedupeKey": 1, "_id": 0})
    }


def _assert_p4_verdict(plan, real: list[str], orphan: list[str]) -> None:
    """P4 的判据断言，抽成函数是为了让**反向验证**能对同一组断言下 pytest.raises。

    正向调用它必须过；把身份字段改成 ``_id`` 后调用它必须抛 AssertionError。
    """
    assert len(plan.orphans) == ORPHAN_COUNT
    assert len(plan.kept) == REAL_COUNT
    assert {e["dedupeKey"] for e in plan.orphans} == set(orphan)
    assert {e["dedupeKey"] for e in plan.kept} == set(real)


# ───────────────────────────────────────────────── P4 判据 + 反向验证


def test_p4_criterion_uses_id_field(seeded_orphans):
    """P4：判据按 ``id`` 比对，恰好认出 5 条孤儿、3 条真实。"""
    real, orphan = seeded_orphans
    plan = prune.collect(get_db(), settings.db_name)
    _assert_p4_verdict(plan, real, orphan)


def test_p4_reverse_using_underscore_id_turns_red(seeded_orphans, monkeypatch):
    """**反向验证**：把判据字段换成 ``_id``，断言必须变红。

    这正是规格里记的那次误判——用 ``_id`` 比对时，实体的身份集合取不到任何
    ``p_``/``t_`` 前缀 id，于是**每一条都被判成孤儿**（当时显示「50 条全是孤儿」）。
    如果这个用例没变红，说明 P4 的断言根本没在验判据，是摆设。
    """
    real, orphan = seeded_orphans
    monkeypatch.setattr(prune, "IDENTITY_FIELD", "_id")

    plan = prune.collect(get_db(), settings.db_name)

    # 先把「错在哪」钉死：全部 8 条都被误判成孤儿，一条真实的都没保住
    assert len(plan.orphans) == REAL_COUNT + ORPHAN_COUNT
    assert plan.kept == []

    # 再证明 P4 的那组断言确实会因此失败（红，不是绿）
    with pytest.raises(AssertionError):
        _assert_p4_verdict(plan, real, orphan)


def test_p4_taskless_real_event_is_kept(seeded_orphans):
    """契约里 ``subject.task`` 选填：没带 task 的事实**没有悬空引用**，不是孤儿。

    按规格字面「task 不在 tasks 的 id 集合」会把这种合法事实一起删掉——
    那才是真的篡改历史。这条用例把收紧后的判据钉住（见 prune 模块 docstring）。
    """
    get_db()["events"].insert_one(
        _event(
            dedupe="real-no-task", project="p_real01", task=None,
            seconds=600, time_="2026-07-09T09:00:00+08:00",
        )
    )
    plan = prune.collect(get_db(), settings.db_name)

    assert "real-no-task" in {e["dedupeKey"] for e in plan.kept}
    assert len(plan.orphans) == ORPHAN_COUNT


# ───────────────────────────────────────────────── P1 dry-run


def test_p1_dry_run_deletes_nothing(seeded_orphans):
    """P1：默认 dry-run，一条都不删。"""
    real, orphan = seeded_orphans
    before = _dedupes()

    result = subprocess.run(
        ["bash", str(SCRIPT), settings.db_name],
        capture_output=True, text=True, env=_env(), timeout=120,
    )

    assert result.returncode == 0, result.stderr
    assert _dedupes() == before
    assert get_db()["events"].count_documents({}) == REAL_COUNT + ORPHAN_COUNT
    assert "DRY-RUN" in result.stdout
    # P6：条数对照 + 保留项可见
    assert f"判为孤儿（将删除）    : {ORPHAN_COUNT}" in result.stdout
    assert f"判为真实（保留）      : {REAL_COUNT}" in result.stdout
    assert "真实项目 / 真实任务" in result.stdout


# ───────────────────────────────────────────────── P7 库名白名单


@pytest.mark.parametrize("bad_db", ["nexus_cores", "nexus-core", "admin", "nexus_core_v2"])
def test_p7_rejects_db_outside_allowlist(bad_db):
    """P7：库名不在白名单直接拒（打错一个字母就停住）。"""
    result = subprocess.run(
        ["bash", str(SCRIPT), bad_db],
        capture_output=True, text=True, env=_env(), timeout=60,
    )
    assert result.returncode != 0
    assert "不在允许名单内" in result.stderr


def test_p7_requires_db_name():
    """库名必填，没有默认值（同 restore.sh）。"""
    result = subprocess.run(
        ["bash", str(SCRIPT)], capture_output=True, text=True, env=_env(), timeout=60,
    )
    assert result.returncode != 0
    assert "库名必填" in result.stderr


def test_requires_explicit_tz():
    """NEXUS_TZ 缺失即拒：重建投影按它归日，猜错会把历史静默记到错误的日子。"""
    env = _env()
    env.pop("NEXUS_TZ", None)
    result = subprocess.run(
        ["bash", str(SCRIPT), settings.db_name],
        capture_output=True, text=True, env=env, timeout=60,
    )
    assert result.returncode != 0
    assert "NEXUS_TZ 未设置" in result.stderr


# ───────────────────────────────────────────────── P2 重打库名确认


def test_p2_wrong_confirmation_aborts_without_deleting(seeded_orphans):
    """P2：确认时打错库名 → 中止，且一条都没删。"""
    before = _dedupes()
    env = _env()
    env["COCKPIT_PRUNE_YES"] = "nexus_core"  # 与目标库不符

    result = subprocess.run(
        ["bash", str(SCRIPT), settings.db_name, "--apply"],
        capture_output=True, text=True, env=env, timeout=120,
    )

    assert result.returncode != 0
    assert "已中止，什么都没动" in result.stderr
    assert _dedupes() == before


# ───────────────────────────────────────────────── P3 备份失败即中止


def test_p3_aborts_when_backup_fails(seeded_orphans, tmp_path):
    """P3：备份失败 → 中止且一条都没删。

    「删除前唯一的保险」必须真的存在。这里把 COCKPIT_ARCHIVE_DIR 指到一个
    **不是 git 仓**的目录，backup.sh 会 die，清理必须跟着停。
    """
    before = _dedupes()
    env = _env()
    env["COCKPIT_PRUNE_YES"] = settings.db_name
    env["COCKPIT_ARCHIVE_DIR"] = str(tmp_path / "not-a-git-repo")
    (tmp_path / "not-a-git-repo").mkdir()

    result = subprocess.run(
        ["bash", str(SCRIPT), settings.db_name, "--apply"],
        capture_output=True, text=True, env=env, timeout=120,
    )

    assert result.returncode != 0
    assert "备份失败" in result.stderr
    assert "一条都没删" in result.stderr
    assert _dedupes() == before


# ───────────────────────────────────────────────── P5 真删 + 投影重建


def test_p5_apply_deletes_only_orphans_and_rebuilds(seeded_orphans, tmp_path):
    """①恰好删 5 条 ②3 条真实一条不少 ③投影重建后与剩余事实一致。

    走**完整的 .sh**（含真备份），不是直接调 Python——闸门本身也是交付物。
    """
    real, orphan = seeded_orphans
    db = get_db()

    # 先污染投影：让它带着 8 条事实的累计值，好证明重建确实把孤儿的贡献清掉了
    from app.modules.projector.rebuild import rebuild

    rebuild()
    dirty = sum(row["seconds"] for row in db["proj_daily_stats"].find({}, {"_id": 0}))
    assert dirty == REAL_COUNT * 1800 + ORPHAN_COUNT * 1, "前置：投影应先带着孤儿的贡献"

    archive = tmp_path / "archive-repo"
    archive.mkdir()
    subprocess.run(["git", "init", "-q", str(archive)], check=True, timeout=60)

    env = _env()
    env["COCKPIT_PRUNE_YES"] = settings.db_name
    env["COCKPIT_ARCHIVE_DIR"] = str(archive)

    result = subprocess.run(
        ["bash", str(SCRIPT), settings.db_name, "--apply"],
        capture_output=True, text=True, env=env, timeout=300,
    )
    assert result.returncode == 0, result.stdout + result.stderr

    # ① 恰好删 5 条
    assert f"已删除 {ORPHAN_COUNT} 条" in result.stdout
    # ② 3 条真实的一条不少
    assert _dedupes() == set(real)
    assert db["events"].count_documents({"type": "session.completed"}) == REAL_COUNT
    # ③ 投影与剩余事实一致
    after = sum(row["seconds"] for row in db["proj_daily_stats"].find({}, {"_id": 0}))
    assert after == REAL_COUNT * 1800
    assert "投影已重建" in result.stdout

    # 备份真的落地了（P3 不是走过场）
    assert list(archive.rglob("*.archive")), "--apply 应留下一份可还原的备份"


def test_p5_rebuild_matches_fresh_rebuild(seeded_orphans, tmp_path):
    """③ 的更强形式：清理后的投影 == 从剩余事实重新跑一遍 rebuild 的结果。"""
    from app.modules.projector.rebuild import rebuild

    db = get_db()
    plan = prune.collect(db, settings.db_name)
    prune.delete_orphans(db, plan)
    prune.rebuild_projections()
    after_prune = sorted(
        (r["date"], r["projectId"], r["seconds"])
        for r in db["proj_daily_stats"].find({}, {"_id": 0})
    )

    rebuild()  # 幂等：再跑一次应完全一致
    fresh = sorted(
        (r["date"], r["projectId"], r["seconds"])
        for r in db["proj_daily_stats"].find({}, {"_id": 0})
    )
    assert after_prune == fresh
    assert all(pid == "p_real01" for _, pid, _ in fresh), "投影里不该再有已删项目的贡献"


def _env() -> dict[str, str]:
    """子进程环境：显式带上测试库名与时区，绝不继承出一个指向真库的 env。"""
    env = dict(os.environ)
    env["NEXUS_DB_NAME"] = settings.db_name
    env["NEXUS_MONGO_URI"] = settings.mongo_uri
    env["NEXUS_TZ"] = "Asia/Shanghai"
    env.pop("COCKPIT_PRUNE_YES", None)
    return env
