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
. "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/lib/emit.sh" 2>/dev/null || true
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

# ── ① 写权限：不给权限的 agent 一个字都落不了盘，而进程照样退 0 ──────
# 实证（CFO 报的 B1）：派出去的 agent 原话「写权限未授予，本次留痕为零」，
# 而 diary 记的是 rc:0 —— **撒谎式成功躺在派活主干上，所有派出去的活都会这样"完成"**。
#
# 路径级的写边界不由 harness 管，由**路签 + checks/_common/05 在提交层**管
# （harness 的权限模型没有"只许写这几个目录"这一档）。
# 所以这里只负责把"能不能写"打开，"能写哪儿"仍归路签 —— 两者是同一件事的两半，缺一不可。
perm=${AIMERGENT_AGENT_PERMISSION_MODE:-acceptEdits}
# ── ①.5 模型档：默认中档（sonnet），不是"继承派活方的档" ──────────────
# 实证（blockers/2026-07-30-model-tier-not-enforced.md）：orchestration.md 第 6 条
# 规定"reviewer/arbiter 常规轮次一律中档，升档只在打回复审/架构级裁决"，但这里
# 一直没有任何传模型的入口，规矩只能靠派活方每次记得——CFO 因此两次把 programmer
# 跑在了 Opus 上（用户两次指出"usage limit 走得飞快"）。默认取中档而非留空
#（留空由 claude CLI 自行决定，往往等于继承高档），让"省"成为默认、
# "升档"成为需要显式写 AIMERGENT_AGENT_MODEL=opus 的动作——方向与此前正好相反。
model=${AIMERGENT_AGENT_MODEL:-sonnet}
args=(-p --agent "$role" --output-format json --permission-mode "$perm" --model "$model")
lease_file=$(git rev-parse --path-format=absolute --git-common-dir)/aimergent-leases/$role.lease
if [ -f "$lease_file" ]; then
  printf '   写区路签：%s\n' "$(tr '\n' ' ' < "$lease_file")" >&2
else
  printf '   ⚠️  %s 没有写区路签 —— 它写出来的东西提交时会被 checks/05 拦下。\n' "$role" >&2
  printf '      先跑：scripts/mission_start.sh %s <任务单> <写区...>\n' "$role" >&2
fi

# 派活前的仓状态快照，用于事后核"活是不是真落了盘"。
# **必须排除脚本自己的产物**：run_agent / emit.sh 会往 logs/ 追加事件，
# 只写一行 diary 就会让 git status 变化 —— landed 于是恒真，
# 又是一个"检查永远给同一个答案"（CFO 实证：agent 一个字没改，landed 判 true）。
_snap() { { git status --porcelain 2>/dev/null | grep -vE '(^|[ ?])logs/'
             git rev-parse HEAD 2>/dev/null; } | sort; }
_before=$(_snap)
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

# ── ② 退出码 0 不等于干完了：核产出真落盘 ────────────────────────
# 这是铁律 12「不接受口头已完成」的机械化：进程说成功，去看工作树认不认。
_after=$(_snap)
landed=1
[ "$_before" = "$_after" ] && landed=0
if [ "$rc" -eq 0 ] && [ "$landed" -eq 0 ]; then
  rc=3
  cat >&2 <<HINT
❌ agent 退出码 0，但**工作树一个字节都没变** —— 按铁律 12 视为未完成。
   最常见原因：写权限没授予（agent 会说「一个字都落不了盘」但进程照退 0）。
   当前权限档：$perm
   若 agent 说的是「不能执行命令」而不是「不能写文件」，多半是 Bash 仍被门控，换：
     AIMERGENT_AGENT_PERMISSION_MODE=bypassPermissions scripts/run_agent.sh ...
   （可选档：acceptEdits / auto / bypassPermissions / dontAsk。
     范围仍由路签 + checks/05 在提交层兜底，harness 没有"只许写这几个目录"这一档。）
   若这次确实只需只读产出（例如纯审阅、只出结论不改文件）：
     AIMERGENT_ALLOW_NO_OUTPUT=1 scripts/run_agent.sh ...（放行但记账）
   当前模型档：$model（升档打回复审/架构级裁决用 AIMERGENT_AGENT_MODEL=opus）
HINT
  [ -n "${AIMERGENT_ALLOW_NO_OUTPUT:-}" ] && { rc=0; echo "⚠️  已按只读任务放行（记账）" >&2; }
fi

# 记进 diary：新开还是续用，是「重开率」这个指标的原料；
# model 与 perm 同理记下——不记就永远算不出"高档占比"这个数字（blockers/2026-07-30-model-tier-not-enforced.md）。
ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
printf '{"ts":"%s","event":"run_agent","role":"%s","module":"%s","resumed":%s,"landed":%s,"perm":"%s","model":"%s","rc":%d}\n' \
  "$ts" "$role" "$(basename "$mod")" "$([ "$resume" -eq 1 ] && echo true || echo false)" \
  "$([ "$landed" -eq 1 ] && echo true || echo false)" "$perm" "$model" "$rc" \
  >> "$root/logs/diary.jsonl"

jq -r '.result // .' <<<"$out" 2>/dev/null || printf '%s\n' "$out"
[ -n "$sid" ] && printf '\n─ session: %s（打回时用 --resume 续用，别重开）\n' "$sid" >&2
exit $rc
