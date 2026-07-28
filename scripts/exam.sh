#!/usr/bin/env bash
# 开工考试 —— 只考「通用流程」，题库固定在框架级，不含任何项目/任务特有内容。
# 任务特定的要求由 arbiter 开单时写进任务单，由 scripts/checks/ 机器核验，不进题库。
#
#   scripts/exam.sh <角色>                    出题（把这段发给 subagent）
#   scripts/exam.sh <角色> --submit <答案文件>  判卷
#
# 不通过的处置：退回 arbiter 重新派单，并附带错在哪。走既有打回循环，不铸令牌。
set -uo pipefail
. "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/lib/emit.sh" 2>/dev/null || true

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

grade() {
  local ans=$1 wrong=0
  local -a msgs=()
  norm() { tr 'A-Z' 'a-z' < "$ans" | tr -d ' '; }
  local body; body=$(norm)

  # Q1 报告落点
  if [[ "$body" != *"codeagent/$role/docs/report.json"* && "$body" != *"docs/report.json"* ]]; then
    msgs+=("Q1 报告落点：未答出 codeagent/$role/docs/report.json")
    wrong=$((wrong+1))
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
