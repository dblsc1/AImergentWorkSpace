#!/usr/bin/env bash
# 门禁有效性自测 —— 把真实踩过的坑做成断言，检查本仓的治理工具能否拦住它们。
#
#   scripts/selftest.sh [目标仓路径]     默认当前 git 顶层
#
# 输出 PASS(有防御) / FAIL(无防御) / N/A(不适用本仓类型)。
# 设计原则：静态能力探针 + 一次真实空区间试跑；不写入目标仓、不产生 commit。
#
# 「我的规范拦得住我自己犯过的每一个错」—— 这份脚本就是那句话的证据。
# 它必须进 CI 每次跑，否则半年后这些闸门会在无人察觉中失效（V1 假绿门禁就是这么来的）。
set -uo pipefail
. "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/lib/emit.sh" 2>/dev/null || printf '⏭  已跳过事件上报（缺 scripts/lib/emit.sh；只影响控制台可见性，不影响本次结果）\n' >&2

repo=${1:-$(git rev-parse --show-toplevel 2>/dev/null || true)}
[ -n "$repo" ] && [ -e "$repo/.git" ] || { echo "❌ 不是 git 仓: ${repo:-<空>}" >&2; exit 2; }
repo=$(cd -- "$repo" && pwd -P)

pass=0; fail=0; na=0
P() { printf '  ✅ PASS  %s\n' "$*"; pass=$((pass+1)); }
F() { printf '  ❌ FAIL  %s\n' "$*"; fail=$((fail+1)); }
N() { printf '  ⏭  N/A   %s\n' "$*"; na=$((na+1)); }

# 兼容 V3（ci/）与 V4（scripts/）两种布局
S=$repo/scripts; [ -d "$S" ] || S=$repo/ci

printf '\n══ 门禁有效性自测 · %s ══\n\n' "$repo"

# ── 断言分片（C1：单文件 ≤500 行）───────────────────────────────────
# 断言主体在 scripts/selftest.d/，按文件名顺序 source；**入口仍然只有这一个脚本**。
# 分片共享上面的 repo/S/P/F/N 与 pass/fail/na，所以只能 source，不能单独执行。
#
# ⚠️ 这里刻意不写 `[ -d "$D" ] && for …` —— 那正是铁律 23 点名的高频形状：
#    分片目录被 gitignore 吞掉 / 没随 clone 下来，循环跑零次，脚本照样退 0，
#    界面上和「61 条全绿」一模一样。缺分片必须响亮死掉。
D="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/selftest.d"
[ -d "$D" ] || { printf '❌ 缺 %s —— 断言主体全在里面，缺了会「零断言退 0」\n' "$D" >&2; exit 2; }
_parts=(); while IFS= read -r _p; do _parts+=("$_p"); done < <(LC_ALL=C ls "$D"/*.sh 2>/dev/null)
[ "${#_parts[@]}" -gt 0 ] || { printf '❌ %s 里一个断言分片都没有\n' "$D" >&2; exit 2; }
for _p in "${_parts[@]}"; do . "$_p"; done
# 跑完一条断言都没有 = 分片被吞或全体哑火。**不许静默退 0**。
# 这里不写死条数：写死的 N 是化石（断言 36 就是为这个存在的）。
[ $((pass + fail + na)) -gt 0 ] || { printf '❌ 断言分片一条都没跑（selftest.d 被吞了？）\n' >&2; exit 2; }


printf '\n── 小结: PASS %d · FAIL %d · N/A %d ──\n\n' "$pass" "$fail" "$na"
[ "$fail" -eq 0 ]
