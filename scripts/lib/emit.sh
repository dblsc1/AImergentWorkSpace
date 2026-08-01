#!/usr/bin/env bash
# 脚本活动上报 —— 任何脚本 source 本文件，激活与退出自动写进 logs/diary.jsonl。
#
#   source "$(dirname "${BASH_SOURCE[0]}")/lib/emit.sh"
#
# 控制面板直接读 diary，所以「每激活一个 sh，面板就看得到」是自动的，不用各脚本各写一遍。
# 只加不改：diary 是 append-only 事件流水，谁都不许回头改历史。
#
# ── 两个账本，别混（2026-08-01 A-1 事故后拆分）────────────────
#   logs/diary.jsonl   事件流水，高频，**不入仓**。每写一行就动 git status，
#                      入仓会把「活落盘了」类检查骗成恒真。它给控制台看，不是证据。
#   logs/ledger.jsonl  只记逃生口放行，低频，**必须入仓**。正常运行一行都不写。
#
# 事故实录：此前逃生口只写 diary，而 diary 被 .gitignore 吞。
# 于是「放行但记账」这句话是假的——按铁律18（未被 Git 跟踪的按未产出计），
# 当天 15 次逃生口使用等于没记账。**账本不入仓 = 所有逃生口实际上是静默的。**
AIMERGENT_EMIT_START=$(date +%s)
_emit_root=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
_emit_diary="$_emit_root/logs/diary.jsonl"
_emit_ledger="$_emit_root/logs/ledger.jsonl"
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

# 逃生口专用：**双写**。diary 给控制台看，ledger 是入仓的证据。
# 每一个逃生口 env 都必须调它——漏一个，那个逃生口就是静默的。
# selftest 扫这个类：凡 AIMERGENT_*_(OVERRIDE|SKIP*|UNREVIEWED|ALLOW_*) 出现处必须有 emit_override。
emit_override() {  # emit_override <逃生口名> <理由>
  local hatch=${1:-unknown} reason=${2:-} ts
  ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  mkdir -p "$(dirname "$_emit_ledger")" 2>/dev/null || return 0
  if command -v jq >/dev/null 2>&1; then
    jq -cn --arg ts "$ts" --arg hatch "$hatch" --arg reason "$reason" \
           --arg script "$_emit_script" --arg actor "${AIMERGENT_ROLE:-${USER:-unknown}}" \
           --arg branch "$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo -)" \
           --arg head "$(git rev-parse HEAD 2>/dev/null || echo -)" \
      '{ts:$ts,event:"override",hatch:$hatch,reason:$reason,script:$script,
        actor:$actor,branch:$branch,head:$head}' >> "$_emit_ledger" 2>/dev/null || true
  else
    # 无 jq 兜底：手工转义，宁可信息少也不能不写——不写就是静默。
    local r=${reason//\\/}; r=${r//\"/}; r=${r//$'\n'/ }
    printf '{"ts":"%s","event":"override","hatch":"%s","reason":"%s","script":"%s","actor":"%s"}\n' \
      "$ts" "$hatch" "$r" "$_emit_script" "${AIMERGENT_ROLE:-unknown}" >> "$_emit_ledger" 2>/dev/null || true
  fi
  emit_event override "$hatch: $reason"
}

_emit_exit() {
  local rc=$?
  local dur=$(( $(date +%s) - AIMERGENT_EMIT_START ))
  emit_event script_end "耗时 ${dur}s" "$rc"
  return $rc
}
emit_event script_start "$*"
trap _emit_exit EXIT

# 类规则：凡写「将来要被 commit 的产物」，写前必须验落点不被 .gitignore 吞。
# 被吞时脚本照常打印 ✅ = 撒谎式成功（A2 事故；与 install-gates 假装装好同类）。
assert_trackable() {  # assert_trackable <仓内相对路径> [仓根]
  local repo=${2:-.}
  git -C "$repo" check-ignore -q -- "$1" 2>/dev/null || return 0
  printf '❌ 落点被 .gitignore 吞掉: %s\n' "$1" >&2
  printf '   写进去也不会被跟踪 = 按铁律12「未产出」。修 .gitignore 白名单或换落点。\n' >&2
  return 1
}
