#!/usr/bin/env bash
# 判据：不得直接在 main/master 上提交（铁律 1；main 只经合并门更新）。
[ "${1:-}" = --describe ] && { echo "40 分支纪律：不得在 main/master 上直接提交，须走 feat/ fix/ chore/ 分支"; exit 0; }
set -uo pipefail
b=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")
case "$b" in
  main|master)
    echo "当前在 $b 上提交。铁律 1：main 只经 scripts/merge-to-main.sh 更新" >&2
    echo "  正解：git checkout -b feat/<主题>" >&2
    exit 1 ;;
esac
exit 0
