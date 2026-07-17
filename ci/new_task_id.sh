#!/usr/bin/env bash
# 生成任务 id：<module>-<slug>-<YYMMDD-HHMMSS>[-rnd]。arbiter 接单时调，贯穿 diary/jsonl/commit/report。
# 用法: new_task_id.sh <module> <slug>
set -euo pipefail
[ $# -eq 2 ] || { echo "用法: $0 <module> <slug>" >&2; exit 1; }
mod=$1; slug=$(printf '%s' "$2" | tr ' /' '--' | tr -cd 'A-Za-z0-9_-'); slug=${slug:-task}
printf '%s-%s-%s-%s\n' "$mod" "$slug" "$(date +%y%m%d-%H%M%S)" "$(printf '%03d' $((RANDOM%1000)))"
