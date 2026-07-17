#!/usr/bin/env bash
# Claude Code PreToolUse(Bash) 二层门禁：拦裸 git push / gh 合并，审计留痕。
# 按命令位判断子命令（非子串），经 arbiter-push.sh / merge-to-main.sh 放行。
# 最佳努力层：pre-push 才是权威门；indirection（变量拼接/别名/其他解释器）可绕过本层（M3，已知残余）。
set -euo pipefail
payload=$(cat)

extract_cmd() {
  if command -v jq >/dev/null 2>&1; then
    printf '%s' "$payload" | jq -r '.tool_input.command // empty' 2>/dev/null && return 0
  fi
  if command -v python3 >/dev/null 2>&1; then
    printf '%s' "$payload" | python3 -c 'import json,sys
try: print(json.load(sys.stdin).get("tool_input",{}).get("command","") or "")
except Exception: pass' 2>/dev/null && return 0
  fi
  printf '%s' "$payload" | sed -n 's/.*"command"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -1
}
cmd=$(extract_cmd)

git_dir=$(git rev-parse --path-format=absolute --git-dir 2>/dev/null || true)
log=${AIMERGENT_PUSH_GUARD_LOG:-${git_dir:-/tmp}/push-guard-audit.log}
deny() { printf '%s deny: %s\n' "$(date -u +%FT%TZ 2>/dev/null || echo NA)" "$1" >>"$log" 2>/dev/null || true
         echo "❌ push-guard: 裸 push/合并被拦，请经 arbiter-push.sh / merge-to-main.sh。cmd=$1" >&2; exit 2; }

seg_blocked() { # $1=单命令段；按命令位判断
  local -a t; read -r -a t <<<"$1" || return 1
  local i=0
  while [ $i -lt ${#t[@]} ]; do case "${t[$i]}" in
    *=*|env|sudo|nice|nohup|time) i=$((i+1)) ;;   # 跳过 VAR=val 与常见前缀
    *) break ;;
  esac; done
  case "${t[$i]:-}" in
    git)
      local j=$((i+1)); while [ $j -lt ${#t[@]} ]; do case "${t[$j]}" in -*|*=*) j=$((j+1));; *) break;; esac; done
      [ "${t[$j]:-}" = push ] && return 0 ;;
    gh)
      { [ "${t[$((i+1))]:-}" = pr ] && [ "${t[$((i+2))]:-}" = merge ]; } && return 0
      if [ "${t[$((i+1))]:-}" = api ]; then case "$1" in *merge*) return 0;; esac; fi ;;
  esac
  return 1
}

segs=$(printf '%s' "$cmd" | sed -E 's/&&|\|\||;|\|/\n/g')
while IFS= read -r seg; do [ -n "$seg" ] && { seg_blocked "$seg" && deny "$cmd"; }; done <<EOF
$segs
EOF
exit 0
