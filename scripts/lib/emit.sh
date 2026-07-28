#!/usr/bin/env bash
# 脚本活动上报 —— 任何脚本 source 本文件，激活与退出自动写进 logs/diary.jsonl。
#
#   source "$(dirname "${BASH_SOURCE[0]}")/lib/emit.sh"
#
# 控制面板直接读 diary，所以「每激活一个 sh，面板就看得到」是自动的，不用各脚本各写一遍。
# 只加不改：diary 是 append-only 事件流水，谁都不许回头改历史。
AIMERGENT_EMIT_START=$(date +%s)
_emit_root=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
_emit_diary="$_emit_root/logs/diary.jsonl"
_emit_script=$(basename "${BASH_SOURCE[1]:-$0}")

emit_event() {  # emit_event <event> <note> [rc]
  local ev=${1:-note} note=${2:-} rc=${3:-0} ts
  ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  note=${note//\\/}          # 去反斜杠，避免破坏 JSON
  note=${note//\"/}           # 去引号
  note=${note//$'\n'/ }       # 单行
  mkdir -p "$(dirname "$_emit_diary")" 2>/dev/null || return 0
  printf '{"ts":"%s","event":"%s","script":"%s","actor":"%s","note":"%s","rc":%s,"branch":"%s"}\n' \
    "$ts" "$ev" "$_emit_script" "${AIMERGENT_ROLE:-${USER:-unknown}}" \
    "$note" "$rc" \
    "$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo -)" >> "$_emit_diary" 2>/dev/null || true
}

_emit_exit() {
  local rc=$?
  local dur=$(( $(date +%s) - AIMERGENT_EMIT_START ))
  emit_event script_end "耗时 ${dur}s" "$rc"
  return $rc
}
emit_event script_start "$*"
trap _emit_exit EXIT
