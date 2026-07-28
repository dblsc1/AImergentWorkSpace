#!/usr/bin/env bash
# 派活 —— CFO / arbiter 只需决定「叫谁 + 给哪张任务单」，提示词由本脚本生成。
#
#   scripts/dispatch.sh <角色> <任务单路径>
#
# 解决的问题：角色卡不在代码目录父链上，harness 不会自动加载。
# 靠人手写「第一行：先读你的角色卡」= 忘写就等于角色卡不存在，且无人察觉。
# 本脚本把那一行变成机器生成的，并附上角色卡的 git 短哈希，供事后核对版本。
set -uo pipefail
. "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/lib/emit.sh" 2>/dev/null || true

root=$(git rev-parse --show-toplevel 2>/dev/null) || { echo "❌ 不在 Git 仓内" >&2; exit 2; }
cd "$root"
role=${1:-}; card_task=${2:-}
[ -n "$role" ] && [ -n "$card_task" ] || {
  echo "用法: scripts/dispatch.sh <角色> <任务单路径>" >&2
  echo "  角色: arbiter | backend | frontend | reviewagent | consulter" >&2
  exit 2
}

# 角色卡路径解析（env 注入优先，绝不硬编码绝对路径）：
#   ① AIMERGENT_FRAMEWORK_ROOT   ② 模块脚手时写下的 .aimergent-framework   ③ 本仓自己
framework=${AIMERGENT_FRAMEWORK_ROOT:-}
if [ -z "$framework" ] && [ -f "$root/.aimergent-framework" ]; then
  framework=$(cd "$root" && cd "$(cat .aimergent-framework)" && pwd -P)
fi
[ -n "$framework" ] || framework=$root
# 角色卡两处：模块级四角色在 agents/roles/；
# 项目级 CFO 与 consulter 各自成目录（agents/cfo/、agents/consulter/），二者**平级**。
card="$framework/agents/roles/$role.md"
[ -f "$card" ] || card="$framework/agents/$role/AGENTS.md"
[ -f "$card" ] || {
  echo "❌ 找不到角色卡: $role" >&2
  echo "   模块角色: arbiter | programmer | programmer_reviewer | module_reviewer" >&2
  echo "   项目角色: cfo | consulter（二者平级，可设 AIMERGENT_FRAMEWORK_ROOT）" >&2
  exit 2; }
if [ ! -f "$card_task" ]; then
  echo "❌ 找不到任务单: $card_task" >&2
  # 反复撞到的同一类错：模块相对路径在框架根跑。
  # 只说"找不到"会让人去找文件；真问题是**站错了仓**。
  case "$card_task" in
    codeagent/*|module_docs/*|code/*|review/*)
      if [ ! -d codeagent ] && [ -d code ]; then
        echo "   这是**模块内相对路径**，而你现在在框架根（$root）。" >&2
        echo "   派模块级角色（arbiter / programmer / programmer_reviewer / module_reviewer）" >&2
        echo "   必须先进模块仓再派：" >&2
        for m in code/*/; do
          m=${m%/}; [ -d "$m/codeagent" ] || continue
          [ -f "$m/$card_task" ] && echo "     cd $m && ../../scripts/dispatch.sh $role $card_task   ← 任务单在这个模块里" >&2
        done
        echo "   （根仓的角色只有 CFO arbiter 与 consulter）" >&2
      fi ;;
  esac
  exit 2
fi

# 角色卡在框架根、任务单在模块仓时，$card 不在 $root 之下，前缀剥离不生效，
# 会把**绝对路径**写进提示词 —— 那正是 copycat 的 `@/srv/aimergent/0/…` 病：换台机器就废。
# 一律算相对当前仓根的路径。
card_rel=$(realpath --relative-to="$root" "$card" 2>/dev/null || printf '%s' "${card#"$root"/}")
card_hash=$(git -C "$framework" log -1 --format=%h -- "${card#"$framework"/}" 2>/dev/null || echo unknown)

cat <<EOF
先读你的角色卡 \`$card_rel\`（版本 $card_hash），你是 $role，执行任务单 \`$card_task\`。

════════ 开工前必做（顺序不可颠倒） ════════

1. 读角色卡 \`$card_rel\` 与任务单 \`$card_task\`。
2. 跑开工考试并把结果贴回给派活方：
       scripts/exam.sh $role
   按提示作答后 \`scripts/exam.sh $role --submit <答案文件>\`。
   **不通过不要开始干活** —— 派活方会重新派单。
3. 下面这张表是你完工时会被逐条核验的判据（开卷，现在就读）：

$(scripts/mission_complete.sh --list 2>/dev/null | sed 's/^/   /')

════════ 任务单原文 ════════

$(cat "$card_task")

════════ 交付前 ════════

- 跑 \`scripts/mission_complete.sh\`，全绿才提交。
- commit 消息必须带 trailer：\`Agent-Attribution: $role@<模块>+<任务id>\`
- 报告与 worklog 必须写在仓内 canonical 路径并 commit；写在仓外 = 按未产出计。
EOF
