#!/usr/bin/env bash
# 用 shell 起一个独立 Claude 进程执行角色任务 —— 绕开「子代理不能再开子代理」。
#
#   scripts/run_agent.sh <角色> <任务单路径> [模块路径]
#   scripts/run_agent.sh <角色> <任务单路径> [模块路径] --resume
#
# 原理：`claude -p --agent <角色>` 起的是一个**全新进程**，整个 session 以该角色身份跑，
# 不走子代理那条路，因此不受嵌套层数限制。arbiter 即使自己是子代理，也能用它派 programmer。
#
# --resume：按记录的 session id 续用同一个进程的会话（铁律「续用 > 重开」终于可机械执行）。
# 打回-修复循环必须用它——重开等于把上下文全丢了重读一遍。
set -uo pipefail
die() { printf '❌ %s\n' "$*" >&2; exit 1; }

command -v claude >/dev/null || die "找不到 claude CLI"
root=$(git rev-parse --show-toplevel 2>/dev/null) || die "不在 Git 仓内"
role=${1:-}; task=${2:-}; mod=${3:-$root}
[ -n "$role" ] && [ -n "$task" ] || die "用法: run_agent.sh <角色> <任务单> [模块路径] [--resume]"
case "${3:-}" in --resume) mod=$root ;; esac
resume=0; for a in "$@"; do [ "$a" = --resume ] && resume=1; done
[ -d "$mod" ] || die "模块路径不存在: $mod"
mod=$(cd -- "$mod" && pwd -P)
[ -f "$mod/$task" ] || [ -f "$task" ] || die "找不到任务单: $task"

# agent 定义按工作目录发现，所以必须在模块内起
[ -f "$mod/.claude/agents/$role.md" ] ||
  die "模块内没有该角色定义: $mod/.claude/agents/$role.md（先跑 scripts/new_agent.sh $role）"

sess_dir="$root/logs/sessions"; mkdir -p "$sess_dir"
sess_file="$sess_dir/$(basename "$mod").$role.session"

prompt=$("$root/scripts/dispatch.sh" "$role" "$task" 2>/dev/null) ||
  prompt="先读 codeagent/$role/AGENTS.md，你是 $role，执行任务单 $task。"

cd "$mod"
args=(-p --agent "$role" --output-format json)
if [ "$resume" -eq 1 ]; then
  [ -f "$sess_file" ] || die "没有可续用的 session 记录: $sess_file"
  args+=(--resume "$(cat "$sess_file")")
  printf '↻ 续用 session %s\n' "$(cat "$sess_file")" >&2
else
  printf '▶ 新开 %s（模块 %s）\n' "$role" "$(basename "$mod")" >&2
fi

out=$(claude "${args[@]}" "$prompt")
rc=$?

sid=$(jq -r '.session_id // empty' <<<"$out" 2>/dev/null || true)
[ -n "$sid" ] && printf '%s' "$sid" > "$sess_file"

# 记进 diary：新开还是续用，是「重开率」这个指标的原料
ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
printf '{"ts":"%s","event":"run_agent","role":"%s","module":"%s","resumed":%s,"rc":%d}\n' \
  "$ts" "$role" "$(basename "$mod")" "$([ "$resume" -eq 1 ] && echo true || echo false)" "$rc" \
  >> "$root/logs/diary.jsonl"

jq -r '.result // .' <<<"$out" 2>/dev/null || printf '%s\n' "$out"
[ -n "$sid" ] && printf '\n─ session: %s（打回时用 --resume 续用，别重开）\n' "$sid" >&2
exit $rc
