#!/usr/bin/env bash
# diary 事件追加（机器 append-only，谁都不改历史）。
# 用法: log_event.sh <diary.jsonl 路径> '<json对象，不含ts>'
#   例: log_event.sh codeagent/arbiter/docs/WorkIterationDiary/<id>/arbiter.jsonl \
#        '{"task_id":"…","event":"verdict","by":"reviewagent","verdict":"rejected","round":1,"issues":3}'
set -euo pipefail
[ $# -eq 2 ] || { echo "用法: $0 <diary.jsonl> '<json>'" >&2; exit 1; }
f="$1"; json="$2"
mkdir -p "$(dirname "$f")"
ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
# 在首个 { 后注入 ts
printf '%s\n' "${json/\{/\{\"ts\":\"$ts\",}" >> "$f"
