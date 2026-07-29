#!/usr/bin/env bash
# 在模块内开一个编号 agent 实例（arbiter 专用，裁决 J3/J4 布局）。
#
#   scripts/new_instance.sh <programmer|reviewer> [写区前缀...]
#   在模块仓根运行。生成 codeagent/<容器>/<编号>/{agent.md, session, docs/comm.jsonl}
#
# 机制：
#   · 编号自动递增（1 2 3 …），已有实例不覆盖。
#   · agent.md = 实例卡：来自框架角色卡基线 + 写区填实。
#   · session  = 一行 harness session id，机器读写；**同一块代码复用同一实例**：
#     调用方先读它，非空就续用（run_agent --resume <id>），空才新开并回写。
#   · docs/comm.jsonl = 该实例的个人沟通留痕（J4），只用 log_event.sh 追加。
set -euo pipefail
die() { printf '❌ %s\n' "$*" >&2; exit 1; }

container=${1:-}; shift || true
case "$container" in
  programmer|reviewer) ;;
  *) die "用法: $0 <programmer|reviewer> [写区前缀...]（arbiter 是长期单例，不走实例）" ;;
esac

root=$(git rev-parse --show-toplevel 2>/dev/null) || die "不在 Git 仓内"
cd "$root"
[ -d codeagent ] && [ -d module_docs ] || die "不是模块仓布局（缺 codeagent/ 或 module_docs/）"

# 角色卡基线：reviewer 容器实例化 programmer_reviewer 卡（J2：整合测试+规范审核）
role_card_name=$container
[ "$container" = reviewer ] && role_card_name=programmer_reviewer
framework=${AIMERGENT_FRAMEWORK_ROOT:-}
if [ -z "$framework" ] && [ -f .aimergent-framework ]; then
  framework=$(cd "$(cat .aimergent-framework)" 2>/dev/null && pwd -P) || true
fi
card=""
for c in "agents/roles/$role_card_name.md" "${framework:+$framework/agents/roles/$role_card_name.md}"; do
  [ -n "$c" ] && [ -f "$c" ] && { card=$c; break; }
done
[ -n "$card" ] || die "找不到角色卡基线 agents/roles/$role_card_name.md（本仓与框架根都没有）"

# 编号：现有最大号 + 1
n=1
while [ -e "codeagent/$container/$n" ]; do n=$((n+1)); done
inst="codeagent/$container/$n"
mkdir -p "$inst/docs"

# 写区：参数给了就填实；没给留待 mission_start 发签时约束
writable="${*:-（未预填——以 mission_start 发的写区路签为准）}"
{
  printf '# %s 实例 #%s · agent.md（实例卡）\n\n' "$container" "$n"
  printf '> 由 arbiter 用 scripts/new_instance.sh 生成。基线角色卡如下，写边界已按本实例填实。\n'
  printf '> 本实例负责的代码区：**%s**（同一块代码复用本实例，续用不重开）。\n\n' "$writable"
  printf -- '---\n\n'
  sed "s|{{WRITABLE}}|$writable|g; s|{{READONLY}}|模块内其余目录|g; s|{{FORBIDDEN}}|其他实例的写区、review/（programmer）或 code/（reviewer）|g" "$card"
} > "$inst/agent.md"

: > "$inst/session"          # 空 = 尚未开过；首次调用后回写 harness session id
: > "$inst/docs/comm.jsonl"  # J4 个人沟通留痕，append-only

# 回验：关键件必须真落地（不信上面每条命令的退出码）
for must in "$inst/agent.md" "$inst/session" "$inst/docs/comm.jsonl"; do
  [ -f "$must" ] || die "实例回验失败：$must 不存在"
done

printf '✅ 实例已建：%s\n' "$inst"
printf '   agent.md  : 实例卡（写区：%s）\n' "$writable"
printf '   session   : 空——首次 run_agent 后回写 id；之后一律 --resume 复用\n'
printf '   comm.jsonl: scripts/log_event.sh %s/docs/comm.jsonl '"'"'{"event":"..."}'"'"'\n' "$inst"
