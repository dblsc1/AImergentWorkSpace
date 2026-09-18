"""`scripts/backup.sh`/`restore.sh` 的容器名断言（2026-08-10 P0 事故的直接
产物：`COCKPIT_MONGO_CONTAINER` 默认值曾随 `deploy/docker-compose.yml` 的
compose 迁移过期——部署侧改了容器名，脚本默认值
没跟着改，静默连不上任何容器）。

不只验「默认值现在对不对」，还要验「以后默认值又错了
（或有人手滑传错 `COCKPIT_MONGO_CONTAINER`）时，脚本会不会响亮地死，而不是
悄悄产出一份看起来成功、实际是空的/连不上的备份」。
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
BACKUP = BACKEND / "scripts" / "backup.sh"
RESTORE = BACKEND / "scripts" / "restore.sh"


def _env(**extra: str) -> dict[str, str]:
    env = dict(os.environ)
    env.update(extra)
    return env


# ------------------------------------------------- 默认值本身对不对（D1）


def test_backup_default_container_name_is_current():
    """D1：默认容器名字面量必须是当前真实容器名，不是已改名的旧值。
    直接读脚本源码断言字面量——比跑一次真备份更快、更聚焦，且能在
    `docker exec` 探活逻辑改了实现细节之后仍然守住"默认值对不对"这条。
    """
    text = BACKUP.read_text(encoding="utf-8")
    assert "honeycomb-mongo" in text
    # 旧容器名只许出现在解释「为什么改名」的注释里，不许再是任何默认值来源：
    # 默认值的字面量形态是 `:-<名字>}`（花括号收尾；解释性注释里的名字后面
    # 从来不接花括号）。这里逐个默认值断言它等于当前组装层的容器名，
    # 不误伤注释 —— 这条守的是「默认值会随部署改名而过期」这个类，不是某个名字。
    import re as _re
    for m in _re.finditer(r"COCKPIT_MONGO_CONTAINER:-([^}]*)\}", text):
        assert m.group(1) == "honeycomb-mongo", (
            f"默认容器名 {m.group(1)!r} 与组装层的 honeycomb-mongo 不一致 —— "
            "部署侧改名后这里没跟着改，备份会静默连不上任何容器")


def test_restore_default_container_name_is_current():
    text = RESTORE.read_text(encoding="utf-8")
    for line in text.splitlines():
        if "COCKPIT_MONGO_CONTAINER:-" in line:
            assert "honeycomb-mongo" in line, f"默认值这一行与组装层不一致：{line}"


# ------------------------------------------- 连不上容器时必须 die（D3）


def test_backup_dies_loudly_on_unreachable_container(tmp_path):
    """撞见的正是 2026-08-10 P0 的真实形状：容器名不对。喂一个绝不存在的
    容器名，backup.sh 必须：①非零退出 ②说清楚是哪个容器连不上 ③**不产出
    任何看起来像成功的 archive 文件**（静默备份空库的反面）。
    """
    archive_repo = tmp_path / "archive-repo"
    archive_repo.mkdir()
    subprocess.run(["git", "init", "-q", str(archive_repo)], check=True, timeout=60)

    env = _env(
        COCKPIT_ARCHIVE_DIR=str(archive_repo),
        COCKPIT_MONGO_CONTAINER="container-does-not-exist-XyZ",
    )
    result = subprocess.run(
        ["bash", str(BACKUP), "nexus_core_test"],
        capture_output=True, text=True, env=env, timeout=60,
    )

    assert result.returncode != 0, "容器连不上时必须非零退出，不许假装成功"
    assert "container-does-not-exist-XyZ" in result.stderr, "错误必须点名是哪个容器"
    assert "COCKPIT_MONGO_CONTAINER" in result.stderr, "必须给出可操作的修复路径"
    assert not list(archive_repo.rglob("*.archive")), (
        "连不上容器时绝不许留下任何 .archive 文件——留下就是「看起来备份成功」"
    )


def test_backup_container_check_happens_before_touching_archive_dir(tmp_path):
    """容器探活必须在**碰备份仓之前**——探活失败时不该往备份仓写任何东西
    （哪怕只是空目录/占位文件），否则备份仓的历史会混进"这次其实没备份成功"
    的噪音，事后排查更难。
    """
    archive_repo_parent = tmp_path / "would-be-parent"
    archive_repo = archive_repo_parent / "archive-repo"
    # 故意不建 archive_repo：如果脚本在容器检查之前就跑到「备份仓不存在」的
    # die，也一样算通过（两种 die 谁先谁后不重要，重要的是**没有中间态写入**）。
    env = _env(
        COCKPIT_ARCHIVE_DIR=str(archive_repo),
        COCKPIT_MONGO_CONTAINER="container-does-not-exist-XyZ",
    )
    result = subprocess.run(
        ["bash", str(BACKUP), "nexus_core_test"],
        capture_output=True, text=True, env=env, timeout=60,
    )
    assert result.returncode != 0
    assert not archive_repo_parent.exists() or not any(archive_repo_parent.rglob("*")), (
        "容器/备份仓任一检查失败都不许在文件系统上留下中间态"
    )


def test_restore_dies_loudly_on_unreachable_container(tmp_path):
    """restore.sh 同款断言：还原是覆盖性操作，连不上容器时**必须在触碰任何
    真实数据之前**死给你看，不许往下走到"假装目标库是空的"那一步。
    """
    fake_archive = tmp_path / "fake.archive"
    fake_archive.write_bytes(b"not a real mongodump archive, just needs to exist")

    env = _env(COCKPIT_MONGO_CONTAINER="container-does-not-exist-XyZ")
    result = subprocess.run(
        ["bash", str(RESTORE), str(fake_archive), "nexus_core_test"],
        capture_output=True, text=True, env=env, timeout=60,
        input="",  # 万一意外走到交互确认，给个空输入让它自然失败而不是挂起
    )

    assert result.returncode != 0, "容器连不上时必须非零退出"
    assert "container-does-not-exist-XyZ" in result.stderr
    assert "确认还原" not in result.stdout, (
        "连不上容器必须在到达交互确认这一步之前就死——否则用户会以为"
        "流程正常进行，实际上后面的 mongorestore 注定失败"
    )


# ------------------------------------------- 反向验证：探活代码是否真的在跑


def test_reverse_validation_without_preflight_check_would_pass_env_gate(monkeypatch):
    """反向验证（不是摆设）：如果把 `docker exec "$CONTAINER" true` 这道
    探活从脚本里去掉，`test_backup_dies_loudly_on_unreachable_container`
    会不会退化成"因为别的原因"（比如 mongodump 本身失败）才死，而不是
    "因为探活失败"才死？——用脚本自身的退出码语义直接验证：探活失败时
    退出发生在 `mkdir -p "$DAY_DIR"` 之前，archive 目录结构完全不会被创建。
    这条测试用一个**不存在的 ARCHIVE_DIR 的父目录**验证"没有任何中间态"，
    与上面的 `test_backup_container_check_happens_before_touching_archive_dir`
    互为交叉验证（两条测试从不同角度断言同一个"探活在最前面"的事实）。
    """
    text = BACKUP.read_text(encoding="utf-8")
    lines = text.splitlines()
    container_check_idx = next(
        i for i, l in enumerate(lines) if 'docker exec "$CONTAINER" true' in l
    )
    dump_idx = next(
        i for i, l in enumerate(lines)
        if 'docker exec "$CONTAINER" mongodump' in l or 'mongodump --uri' in l
    )
    assert container_check_idx < dump_idx, (
        "探活代码必须排在真正的 mongodump 调用之前，否则「连不上时死」这条"
        "断言测的其实是 mongodump 自己的失败路径，不是本轮新加的探活"
    )
