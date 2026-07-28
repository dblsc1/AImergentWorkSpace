#!/usr/bin/env bash
# 起审 —— arbiter 调用：把被审区间与报告原样转发给 reviewer，附上自己的留言。
#
#   scripts/review_start.sh <reviewer角色> <被审commit> [留言]
#
# 顺序说明（2026-07-28 改）：审核绑的是**本地 commit**，不需要先 push。
# 本地 commit 已是不可变、有 SHA 的真实对象，绑它足够；
# 先审后推的好处：被打回的活永远不上远端，返修不需要 force-push。
set -uo pipefail
. "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/lib/emit.sh" 2>/dev/null || true
die() { printf '❌ %s\n' "$*" >&2; exit 1; }
root=$(git rev-parse --show-toplevel) || die "不在 Git 仓内"; cd "$root"

reviewer=${1:?用法: review_start.sh <reviewer角色> <被审commit> [留言]}
head_sha=$(git rev-parse --verify "${2:?缺被审 commit}^{commit}" 2>/dev/null) || die "无法解析被审 commit: $2"
note=${3:-}
base_sha=$(git merge-base "$head_sha" "${AIMERGENT_INTEGRATION_BRANCH:-dev}" 2>/dev/null ||
           git merge-base "$head_sha" main 2>/dev/null) || die "无法确定被审区间起点"

case "$reviewer" in programmer_reviewer|module_reviewer) ;; *) die "不是审核角色: $reviewer" ;; esac

cat <<EOF
你是 $reviewer。被审区间已固定，**只审这个区间，不审当前工作区**。

review_target:
  branch : $(git rev-parse --abbrev-ref HEAD)
  base   : $base_sha
  head   : $head_sha
  diff_mode: exact

被审文件（$(git diff --name-only --no-renames "$base_sha..$head_sha" | wc -l) 个）：
$(git diff --name-only --no-renames "$base_sha..$head_sha" | sed 's/^/  /')

arbiter 留言：${note:-（无）}

要求：
1. **可机械核验的一律写脚本**，落 review/reviewcode/ 并挂进 run_all.sh（不挂＝死代码）。
2. 肉眼审必须在报告写出「为何不能代码化」的具体理由。
3. 审完跑 \`scripts/review_complete.sh $reviewer $base_sha $head_sha <approved|rejected>\`。
EOF
emit_event review_start "$reviewer $base_sha..$head_sha"
