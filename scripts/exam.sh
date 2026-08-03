#!/usr/bin/env bash
# 开工考试 —— 只考「通用流程」，题库固定在框架级，不含任何项目/任务特有内容。
# 任务特定的要求由 arbiter 开单时写进任务单，由 scripts/checks/ 机器核验，不进题库。
#
#   scripts/exam.sh <角色>                    出题（把这段发给 subagent）
#   scripts/exam.sh <角色> --submit <答案文件>  判卷
#
# 不通过的处置：退回 arbiter 重新派单，并附带错在哪。走既有打回循环，不铸令牌。
set -uo pipefail
. "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/lib/emit.sh" 2>/dev/null || printf '⏭  已跳过事件上报（缺 scripts/lib/emit.sh；只影响控制台可见性，不影响本次结果）\n' >&2

root=$(git rev-parse --show-toplevel 2>/dev/null) || { echo "❌ 不在 Git 仓内" >&2; exit 2; }
cd "$root"
role=${1:-}
[ -n "$role" ] || { echo "用法: scripts/exam.sh <角色> [--submit <答案文件>]" >&2; exit 2; }
diary=logs/diary.jsonl

case "$role" in
  programmer)          writable="code/" ;;
  programmer_reviewer) writable="review/" ;;
  module_reviewer)     writable="review/reviewreport/" ;;
  arbiter)             writable="module_docs/" ;;
  *)                   writable="" ;;
esac

questions() {
cat <<EOF
开工考试 · 角色 = $role · 共 5 题（只考通用流程，不考业务）

Q1  你的 canonical report.json 写到哪个路径？（写完整相对路径）
Q2  你可以写哪些目录？明确禁写哪些？
Q3  提交前必须跑哪条命令做完工检测？
Q4  同一任务打回上限几次？超限升级给谁？
Q5  报告写在仓外（/tmp、会话目录）算什么？

作答方式：把答案写成一个文件，每行 "Q<n>: <答案>"，然后
  scripts/exam.sh $role --submit <答案文件>
EOF
}


# 从 report-schema.md 的 canonical path 表里读某个角色的落点。
# 表里每行末尾有 `<!-- role:<slug> -->` 机器锚点，人看的表和机器读的是**同一行**。
# 不做兜底猜测：查不到就返回空，由调用方响亮报错 —— 猜一个路径出来正是本函数要根治的病。
_canonical_path_for() {   # _canonical_path_for <角色> → 仓内相对路径（查不到则空）
  local role=$1 root schema line fw
  root=$(git rev-parse --show-toplevel 2>/dev/null) || return 0
  schema="$root/agents/protocol/report-schema.md"
  # 模块是**独立 Git 仓**，仓内没有 agents/protocol/ —— 必须回退到框架根。
  # ⚠️ v1 这里调了一个**全仓从未定义过**的 `_framework_root`，于是路径塌成
  #    `/agents/protocol/report-schema.md`，**每个模块里的考试都必错**。
  #    而我的断言只在框架根跑（第一个分支就命中），恰好是能工作的那一半 ——
  #    「判据只覆盖了粗心的那一半」，我在写完这条教训的下一轮就自己犯了。
  if [ ! -f "$schema" ]; then
    . "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/lib/paths.sh" 2>/dev/null || true
    if command -v framework_root >/dev/null 2>&1; then
      fw=$(framework_root 2>/dev/null) && [ -n "$fw" ] && schema="$fw/agents/protocol/report-schema.md"
    fi
  fi
  [ -f "$schema" ] || { printf '__SCHEMA_NOT_FOUND__'; return 0; }
  line=$(grep -F "<!-- role:$role -->" "$schema" | head -1) || return 0
  [ -n "$line" ] || return 0
  # 取第二列，剥掉反引号与说明（第一个反引号包起来的就是路径）
  printf '%s' "$line" | sed -n 's/.*|[^|]*|[[:space:]]*`\([^`]*\)`.*/\1/p'
}

grade() {
  local ans=$1 wrong=0
  local -a msgs=()
  norm() { tr 'A-Z' 'a-z' < "$ans" | tr -d ' '; }
  local body; body=$(norm)

  # Q1 报告落点
  # **判据从 agents/protocol/report-schema.md 读，不在这里硬编码。**
  # 病根（2026-08-03 实证）：这里原来写死 `codeagent/$role/docs/report.json`，
  # 那是 J3 之前的旧布局。**考试是每个 agent 干活前的第一课**，于是一夜之间
  # 两个 reviewer 照着它教的把 canonical report 放错了地方，推送门才拦下来。
  # 教错的指引比没有指引更糟 —— 没有指引人会去查，教错了人会照做。
  local canon; canon=$(_canonical_path_for "$role")
  if [ "$canon" = "__SCHEMA_NOT_FOUND__" ]; then
    # **找不到事实源必须响亮死**，不许拿一个空判据去判卷 ——
    # 那会把每个考生都判错，而错误信息还指向「补锚点」这个不相干的地方。
    msgs+=("Q1 无法判卷：找不到 agents/protocol/report-schema.md（本仓与框架根都没有）。这是判卷器坏了，不是你答错了 —— 请报告给项目 arbiter，别改答案去迎合它。")
    wrong=$((wrong+1))
  elif [ -z "$canon" ]; then
    msgs+=("Q1 报告落点：report-schema.md 里查不到角色 $role 的机器锚点 <!-- role:$role --> —— 先补那张表，别改这里")
    wrong=$((wrong+1))
  else
    # schema 表里写的可能是**模板形态**（如 `code/<子文件夹>/report.json`）。
    # 答出模板原文算对；答出**真实的具体路径**（`code/backend/report.json`）**也算对** ——
    # v1 只比模板字面量，于是答对具体路径反而判错，逼考生去背占位符。
    local want head tail ok=0
    want=$(tr 'A-Z' 'a-z' <<<"$canon" | tr -d ' ')
    [[ "$body" == *"$want"* ]] && ok=1
    if [ "$ok" -eq 0 ] && [[ "$want" == *"<"*">"* ]]; then
      head=${want%%<*}; tail=${want##*>}
      [ -n "$head" ] && [ -n "$tail" ] && [[ "$body" == *"$head"*"$tail"* ]] && ok=1
    fi
    if [ "$ok" -eq 0 ]; then
      msgs+=("Q1 报告落点：未答出 $canon（事实源：agents/protocol/report-schema.md）")
      wrong=$((wrong+1))
    fi
  fi
  # Q2 写边界：必须提到自己可写区；且非 reviewer 不得声称可写 review/
  if [ -n "$writable" ] && [[ "$body" != *"$(tr -d ' ' <<<"$writable")"* ]]; then
    msgs+=("Q2 写边界：未答出自己的可写区 $writable")
    wrong=$((wrong+1))
  elif [ "$role" = programmer ] && [[ "$body" == *"可写review/"* || "$body" == *"能写review/"* ]]; then
    msgs+=("Q2 写边界：声称可写 review/ —— 错，review/ 是 reviewagent 专属")
    wrong=$((wrong+1))
  fi
  # Q3 完工命令
  if [[ "$body" != *"mission_complete"* ]]; then
    msgs+=("Q3 完工命令：未答出 scripts/mission_complete.sh")
    wrong=$((wrong+1))
  fi
  # Q4 打回上限与升级路径
  if [[ "$body" != *"2"* ]] || { [[ "$body" != *"cfo"* ]] && [[ "$body" != *"升级"* ]]; }; then
    msgs+=("Q4 打回上限：未答出「上限 2 次，超限升级 CFO」")
    wrong=$((wrong+1))
  fi
  # Q5 仓外报告
  if [[ "$body" != *"未产出"* ]] && [[ "$body" != *"没写"* ]] && [[ "$body" != *"不算"* ]] && [[ "$body" != *"无效"* ]]; then
    msgs+=("Q5 仓外报告：未答出「按未产出处理」")
    wrong=$((wrong+1))
  fi

  local score=$((5-wrong))
  mkdir -p "$(dirname "$diary")"
  local ts; ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  printf '{"ts":"%s","event":"exam","role":"%s","score":%d,"pass":%s}\n' \
    "$ts" "$role" "$score" "$([ $wrong -eq 0 ] && echo true || echo false)" >> "$diary"

  if [ "$wrong" -eq 0 ]; then
    echo "✅ exam $role 5/5 —— 通过，可以接任务"
    return 0
  fi
  echo "❌ exam $role $score/5" >&2
  printf '   %s\n' "${msgs[@]}" >&2
  cat >&2 <<EOF

→ 退回派活方：本 subagent 不放行接任务。

【派单完整性自查表 —— 派活方先自查，再判是不是 agent 的问题】
  □ 是否用 scripts/dispatch.sh 生成提示词（而不是手写）？
  □ 首行是否指明了角色卡路径？
  □ 是否嵌入了 mission_complete.sh --list 的判据（开卷）？
  □ 是否用 scripts/new_agent.sh 生成过该角色（写边界已填实）？
  □ 任务单四小节是否齐全（目标/验收/可触碰目录/自检门）？
  □ 是否声明并申领了写区路签（mission_start.sh）？

  上面任一项没做 → **是派单漏了，不是 agent 没读**。补齐后重新派。
  全做了仍答错 → agent 确实没读，重新派并在任务单强调上列错项。
EOF
  return 1
}

if [ "${2:-}" = --submit ]; then
  [ -f "${3:-}" ] || { echo "❌ 答案文件不存在: ${3:-<空>}" >&2; exit 2; }
  grade "$3"
  exit $?
fi

questions
