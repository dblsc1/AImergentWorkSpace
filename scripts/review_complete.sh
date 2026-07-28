#!/usr/bin/env bash
# 收审 —— reviewer 调用：规范性检查 + 生成 review_target + 回报 json。
#
#   scripts/review_complete.sh <reviewer角色> <base> <head> <approved|rejected> [结论]
set -uo pipefail
. "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/lib/emit.sh" 2>/dev/null || true
die() { printf '❌ %s\n' "$*" >&2; exit 1; }
root=$(git rev-parse --show-toplevel) || die "不在 Git 仓内"; cd "$root"

r=${1:?用法: review_complete.sh <角色> <base> <head> <verdict> [结论]}
base=$(git rev-parse --verify "${2:?}^{commit}") || die "base 不可解析"
head=$(git rev-parse --verify "${3:?}^{commit}") || die "head 不可解析"
verdict=${4:?verdict 必须是 approved 或 rejected}
summary=${5:-}
case "$verdict" in approved|rejected) ;; *) die "verdict 只能是 approved|rejected" ;; esac
case "$r" in
  programmer_reviewer|module_reviewer) out="codeagent/$r/docs/report.json" ;;
  consulter) out="agents/cfo/consulter/docs/findings/report.json" ;;   # 根仓 L0 独立审核
  *) die "不是审核角色: $r（programmer_reviewer|module_reviewer|consulter）" ;;
esac

# 角色与仓层级要对得上：模块级审核角色只在模块仓内有落点。
# 在框架根跑它们，落点 codeagent/… 会被根仓白名单 .gitignore 吞掉，
# 而"被 gitignore 吞了"这个报错**没告诉人真正的问题是角色用错了层级**。
case "$r" in
  programmer_reviewer|module_reviewer)
    if [ ! -d codeagent ] && git check-ignore -q -- "$out" 2>/dev/null; then
      die "$r 是**模块级**审核角色，只在模块仓内运行（落点 $out 在本仓不被跟踪）。
   根仓（L0）的独立审核角色是 **consulter**：
     scripts/review_complete.sh consulter <base> <head> <verdict> \"结论\"
   分工：consulter 审 CFO / arbiter / programmer 的活；CFO 审 consulter 改的框架。"
    fi ;;
esac
# 写前验落点（A2 类修复）：被 gitignore 吞 = 撒谎式成功，宁可现在响亮失败
assert_trackable "$out" || exit 1
git merge-base --is-ancestor "$base" "$head" || die "base 不是 head 的祖先"

# 规范性检查：代码化优先。approved 却一个检测脚本都没写 = 违反铁律 17
scripts_n=$(git ls-files 'review/reviewcode/*.sh' | grep -cv run_all.sh || true)
if [ "$verdict" = approved ] && [ "${scripts_n:-0}" -eq 0 ]; then
  echo "⚠️  approved 但 review/reviewcode/ 一个检测脚本都没有。" >&2
  echo "    铁律 17：可机械核验的一律脚本化；纯肉眼审必须在报告写明为何不能代码化。" >&2
fi

files=$(git diff --name-only --no-renames "$base..$head" | jq -R . | jq -s -c .)
mkdir -p "$(dirname "$out")"
jq -n --arg role "$r" --arg v "$verdict" --arg s "$summary" \
      --arg b "$base" --arg h "$head" --arg br "$(git rev-parse --abbrev-ref HEAD)" \
      --argjson f "$files" --arg out "$out" '{
  schema_version:2, module:"'"$(basename "$root")"'", role:$role,
  task:"审核 \($b[0:7])..\($h[0:7])", tier:"normal",
  status:(if $v=="approved" then "approved" else "rejected" end),
  summary:(if $s=="" then "审核结论 \($v)" else $s end),
  git:{protocol:"embedded-self-v2", branch:$br, base:$b, head:"SELF",
       diff_mode:"contains", changed_files:[$out]},
  contract:{touched:false, which:[], consumes:[]},
  cross_module_impact:[], escalation:null,
  reviewer_opinion:{reviewer:$role, verdict:$v, path:"review/reviewreport/", round:1},
  review_target:{branch:$br, base:$b, head:$h, diff_mode:"exact", changed_files:$f}
}' > "$out"
printf '✅ 审核报告已生成: %s（%s）\n' "$out" "$verdict"
printf '   下一步: 提交它 → arbiter 收报告 → approved 则 arbiter-push\n'
emit_event review_complete "$r $verdict"
