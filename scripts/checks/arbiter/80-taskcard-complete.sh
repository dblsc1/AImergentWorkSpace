#!/usr/bin/env bash
# 判据：本次新增/修改的任务单必须写全四个必填小节。
# 任务单是一对多的——一份含糊的任务单污染它派出去的所有产出，所以它比一份烂代码杠杆大。
[ "${1:-}" = --describe ] && { echo "80 任务单完整：新增/改动的任务单（文件名含「任务单」）必须含 目标/验收标准/可触碰目录/自检门 四节"; exit 0; }
set -uo pipefail
. "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../lib" && pwd -P)/paths.sh" || { printf '❌ %s：载入 paths.sh 失败 —— 拒绝以「什么都没验」的姿态退 0\n' "${BASH_SOURCE[0]}" >&2; exit 2; }
fail=0
while IFS= read -r -d '' f; do
  [ -f "$f" ] || continue
  # 只认文件名带「任务单”的（CFO 2026-07-29 实报：worklog 与任务单同目录时,
  # 回顾性 worklog 被迫硬套任务单四小节结构——按目录判是误伤，按文件名判才对）
  case "$f" in *任务单*.md) ;; *) continue ;; esac
  case "$f" in */TEMPLATE-*) continue ;; esac
  miss=()
  grep -q '目标' "$f"       || miss+=("目标")
  grep -q '验收' "$f"       || miss+=("验收标准")
  grep -qE '触碰|可写|边界' "$f" || miss+=("可触碰目录")
  grep -qE '自检门|自检' "$f"    || miss+=("自检门")
  [ ${#miss[@]} -eq 0 ] || { echo "$f 任务单缺小节: ${miss[*]}" >&2; fail=1; }
done < <(staged_paths ACMR)
exit $fail
