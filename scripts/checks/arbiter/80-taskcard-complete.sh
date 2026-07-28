#!/usr/bin/env bash
# 判据：本次新增/修改的任务单必须写全四个必填小节。
# 任务单是一对多的——一份含糊的任务单污染它派出去的所有产出，所以它比一份烂代码杠杆大。
[ "${1:-}" = --describe ] && { echo "80 任务单完整：新增/改动的任务单必须含 目标/验收标准/可触碰目录/自检门 四节"; exit 0; }
set -uo pipefail
fail=0
while IFS= read -r f; do
  [ -f "$f" ] || continue
  case "$f" in */TEMPLATE-*) continue ;; esac
  miss=()
  grep -q '目标' "$f"       || miss+=("目标")
  grep -q '验收' "$f"       || miss+=("验收标准")
  grep -qE '触碰|可写|边界' "$f" || miss+=("可触碰目录")
  grep -qE '自检门|自检' "$f"    || miss+=("自检门")
  [ ${#miss[@]} -eq 0 ] || { echo "$f 任务单缺小节: ${miss[*]}" >&2; fail=1; }
done < <(git diff --cached --name-only --diff-filter=ACMR | grep -E 'docs/worklog/.*\.md$' || true)
exit $fail
