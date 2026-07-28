#!/usr/bin/env bash
# 生成一个角色实例：复制角色卡 → 填实可读可写范围 → 写出 .claude/agents/<角色>.md
#
#   scripts/new_agent.sh <角色> [模块路径]
#     角色: arbiter | programmer | programmer_reviewer | module_reviewer
#     模块路径省略时 = 当前仓根
#
# 范围可用环境变量覆盖（不给就用下面的角色默认值）：
#   AIMERGENT_WRITABLE / AIMERGENT_READONLY / AIMERGENT_FORBIDDEN
#
# 为什么要生成 .claude/agents/：角色卡不在代码目录的父链上，harness 不会自动加载。
# 靠派单提示词首行手写「先读角色卡」= 忘写就等于角色卡不存在，且无人察觉。
# .claude/agents/<角色>.md 是该子代理的 system prompt —— 它读不到才是异常。
#
# 考题直接写进 system prompt：agent 出生后的第一条输出必须是答卷。
# 「创建时考试」在 harness 上做不到（agent 还不存在时没人能答题），
# 「出生第一句话就是答卷」做得到，而且更靠前。
set -euo pipefail
die() { printf '❌ %s\n' "$*" >&2; exit 1; }

root=$(git rev-parse --show-toplevel 2>/dev/null) || die "不在 Git 仓内"
role=${1:-}; mod=${2:-$root}
[ -n "$role" ] || die "用法: scripts/new_agent.sh <角色> [模块路径]"
[ -d "$mod" ] || die "模块路径不存在: $mod"
mod=$(cd -- "$mod" && pwd -P)

framework=${AIMERGENT_FRAMEWORK_ROOT:-}
if [ -z "$framework" ] && [ -f "$mod/.aimergent-framework" ]; then
  framework=$(cd "$mod" && cd "$(cat .aimergent-framework)" && pwd -P)
fi
[ -n "$framework" ] || framework=$root
card="$framework/agents/roles/$role.md"
[ -f "$card" ] || die "找不到角色卡: $card（可设 AIMERGENT_FRAMEWORK_ROOT）"

# ── 角色默认边界（可被 env 覆盖）───────────────────────────────
case "$role" in
  arbiter)
    w_def='module_docs/（reviewlog.md 除外）、各角色 docs/ 的任务单、自己 docs/'
    r_def='全模块'
    f_def='code/、review/、项目级规范' ;;
  programmer)
    w_def='code/、codeagent/programmer/docs/'
    r_def='module_docs/contract.md、module_docs/rules.md'
    f_def='review/、module_docs/（只读不写）、其他模块' ;;
  programmer_reviewer)
    w_def='review/reviewcode/、review/reviewreport/、codeagent/programmer_reviewer/docs/'
    r_def='code/ 全部、module_docs/'
    f_def='修改任何业务代码、其他模块' ;;
  module_reviewer)
    w_def='review/reviewreport/、codeagent/module_reviewer/docs/'
    r_def='全模块（含 arbiter 的任务单、报告与 worklog）'
    f_def='code/、module_docs/、替 arbiter 做技术判断' ;;
  *) die "未知角色: $role（arbiter|programmer|programmer_reviewer|module_reviewer）" ;;
esac
writable=${AIMERGENT_WRITABLE:-$w_def}
readonly_=${AIMERGENT_READONLY:-$r_def}
forbidden=${AIMERGENT_FORBIDDEN:-$f_def}

# ── ① 落地填实后的角色卡 ─────────────────────────────────────
dest_dir="$mod/codeagent/$role"
mkdir -p "$dest_dir/docs/worklog" "$dest_dir/docs/WorkIterationDiary"
touch "$dest_dir/docs/WorkIterationDiary/.gitkeep"
sed -e "s|{{WRITABLE}}|$writable|g" \
    -e "s|{{READONLY}}|$readonly_|g" \
    -e "s|{{FORBIDDEN}}|$forbidden|g" \
    "$card" > "$dest_dir/AGENTS.md"
printf '@AGENTS.md\n' > "$dest_dir/CLAUDE.md"
grep -q '{{' "$dest_dir/AGENTS.md" && die "角色卡仍有未替换占位符: $dest_dir/AGENTS.md"

# ── ② 写 .claude/agents/<角色>.md：system prompt + 出生即答卷 ──
mod_name=$(basename "$mod")
fw_rel=$(realpath --relative-to="$mod" "$framework")
mkdir -p "$mod/.claude/agents"
cat > "$mod/.claude/agents/$role.md" <<AGENT
---
name: $role
description: $mod_name 模块的 $role。派活时用 subagent_type=$role。
---
# 你的角色卡（正文内联，不用 @import —— 该语法在 agent 定义里是否展开未验证）

$(cat "$dest_dir/AGENTS.md")

# 模块规范

模块级规范在 \`AGENTS.md\`（模块根），含技术栈、自检门、流程与**已知坑**，开工先读。

# 你的第一条输出（不可跳过）

在做任何事之前，先回答下面五题，把答卷作为你的第一条输出。
答不上来就说"不知道"，不要猜——答错会被退回重派，猜对却没读规范才是真问题。

1. 你的 canonical report.json 写到哪个路径？
2. 你可以写哪些目录？明确禁写哪些？
3. 提交前必须跑哪条命令做完工检测？
4. 同一任务打回上限几次？超限升级给谁？
5. 报告写在仓外（/tmp、会话目录）算什么？

判卷：\`$fw_rel/scripts/exam.sh $role --submit <答案文件>\`
不通过 → 派活方重新派单，不要硬着头皮开始干活。

# 你的边界（越界即打回）

- 可写：$writable
- 只读：$readonly_
- 禁碰：$forbidden

# 完工判据（开卷，现在就知道最后会被怎么判）

跑 \`$fw_rel/scripts/mission_complete.sh --list\` 看全部判据；
提交前跑 \`$fw_rel/scripts/mission_complete.sh\`，全绿才提交。
AGENT

printf '✅ 角色 %s 已就绪\n' "$role"
printf '   角色卡  : %s\n' "codeagent/$role/AGENTS.md（边界已填实）"
printf '   子代理  : .claude/agents/%s.md（system prompt，含出生答卷）\n' "$role"
printf '   可写    : %s\n' "$writable"
printf '   下一步  : scripts/dispatch.sh %s <任务单>\n' "$role"
