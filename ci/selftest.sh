#!/usr/bin/env bash
# 门禁有效性自测：把六个真实事故做成六条断言，检查本仓的治理工具能否拦住它们。
# 用法: ci/selftest.sh [目标仓路径]   默认当前 git 顶层。
# 输出: 每条 PASS(有防御) / FAIL(无防御) / N/A(不适用本仓类型)
# 设计原则：只做静态能力探针 + 一次真实空区间试跑，不写入目标仓、不产生 commit。
set -uo pipefail

repo=${1:-$(git rev-parse --show-toplevel 2>/dev/null || true)}
[ -n "$repo" ] && [ -d "$repo/.git" ] || { echo "❌ 不是 git 仓: ${repo:-<空>}" >&2; exit 2; }
repo=$(cd -- "$repo" && pwd -P)

pass=0; fail=0; na=0
P() { printf '  ✅ PASS  %s\n' "$*"; pass=$((pass+1)); }
F() { printf '  ❌ FAIL  %s\n' "$*"; fail=$((fail+1)); }
N() { printf '  ⏭  N/A   %s\n' "$*"; na=$((na+1)); }

gates=$repo/ci/gates/run-gates.sh
schema=$repo/ci/gates/check-report-schema.sh

printf '\n══ 门禁有效性自测 · %s ══\n\n' "$repo"

# ── 断言 1：空区间假绿灯 ──────────────────────────────────────
# 事故：直接在 main 上工作时 merge-base == head，报告门禁 diff 为空、
#       一份都不验却退 0，制造"已核验"的假象。
printf '1. 空区间假绿灯\n'
if [ ! -x "$schema" ]; then
  F "无 check-report-schema.sh（留痕铁律没有机械执行者）"
elif head=$(git -C "$repo" rev-parse HEAD 2>/dev/null) &&
     (cd "$repo" && AIMERGENT_REPORT_BASE="$head" "$schema" >/dev/null 2>&1); then
  F "base==head 时仍退 0 —— 一份报告都没验却报绿"
else
  P "base==head 时响亮失败，不退化为绿灯"
fi

# ── 断言 2：直提 main 无提示 ──────────────────────────────────
# 事故：10 个 commit 全部直提 main，全程门禁绿、hook 绿、零提示。
printf '2. 直提 main 是否会被察觉\n'
if grep -qE 'abbrev-ref|当前分支|branch.*main' "$gates" 2>/dev/null ||
   [ -f "$repo/ci/hooks/pre-commit" ]; then
  P "存在分支纪律检查"
else
  F "无任何机制检测'正在往 main 上直接提交'（铁律 1 无执行者）"
fi

# ── 断言 3：报告落点在仓外 ────────────────────────────────────
# 事故：9 条 sub_reports[].path 全指向 /tmp，会话结束即蒸发，
#       按协议等于全部未产出，却无人察觉。
printf '3. sub_reports 落点是否被校验\n'
if grep -qE 'sub_reports' "$schema" 2>/dev/null; then
  P "报告门禁会校验 sub_reports 落点"
else
  F "无机制校验 sub_reports[].path 是否在仓内（仍靠自觉）"
fi

# ── 断言 4：新模块首跑必红 ────────────────────────────────────
# 事故：脚手不做初始 commit，新模块按指南首次跑门禁必吃一个语义对不上的红。
printf '4. 脚手是否交付可直接跑门禁的状态\n'
if [ ! -f "$repo/ci/new_module.sh" ]; then
  N "本仓不是框架仓（无 new_module.sh）"
elif grep -qE 'git -C .* commit|git commit' "$repo/ci/new_module.sh"; then
  P "new_module.sh 做初始 commit"
else
  F "new_module.sh 只 init 不 commit —— 新模块首跑门禁必红"
fi

# ── 断言 5：自核脚本成为死代码 ────────────────────────────────
# 事故：铁律 19 要求免审的自核脚本入仓，但入仓不等于会跑；
#       run-gates.sh 只执行 review/reviewcode/run_all.sh。
printf '5. reviewcode 脚本是否真的会被执行\n'
scripts=$(git -C "$repo" ls-files 'review/reviewcode/*.sh' 2>/dev/null | grep -v run_all.sh | wc -l)
if [ "$scripts" -eq 0 ]; then
  N "本仓 review/reviewcode/ 无自核脚本"
elif [ -x "$repo/review/reviewcode/run_all.sh" ]; then
  P "$scripts 个自核脚本有 run_all.sh 挂载点"
else
  F "$scripts 个自核脚本已入仓但无 run_all.sh —— 死代码，比没有更糟"
fi

# ── 断言 6：hook 未安装无提醒 ────────────────────────────────
# 事故：.git/hooks 不随 clone 走；不跑 install-ci.sh 就零保护，
#       且没有任何东西会告诉你处于零保护状态。
printf '6. hook 未安装是否会被发现\n'
hookdir=$(git -C "$repo" rev-parse --path-format=absolute --git-path hooks 2>/dev/null)
if grep -qE 'git-path hooks|\.git/hooks|hooks_dir' "$gates" 2>/dev/null; then
  P "门禁会检测 hook 安装状态"
elif [ -x "$hookdir/pre-push" ] && [ -x "$hookdir/commit-msg" ]; then
  F "hook 已装，但门禁不检测其存在 —— 谁删了都不会有人知道"
else
  F "hook 未安装，且无任何机制提醒（本仓当前处于零保护状态）"
fi

printf '\n── 小结: PASS %d · FAIL %d · N/A %d ──\n\n' "$pass" "$fail" "$na"
[ "$fail" -eq 0 ]
