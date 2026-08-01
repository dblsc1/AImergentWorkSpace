#!/usr/bin/env bash
# 模块自核脚本总入口。run-gates.sh 会自动执行本文件（若可执行）。
# 铁律 19：审核可稀疏，但免掉的每一轮必须以确定性脚本顶上，脚本必须入仓且真的会跑。
# 没有本文件，入仓的自核脚本就是死代码——比没有检查更糟。
set -uo pipefail
cd "$(git rev-parse --show-toplevel)"
fail=0
ok(){ printf '  ✅ %s\n' "$*"; }; bad(){ printf '  ❌ %s\n' "$*"; fail=1; }

echo "── reviewcode: 通用四检查 ──"
# 1. 占位符清零
# 占位符字面量必须拆开写：new_module 的 replace_token 会替换全仓的连续 token；
# 本脚本若含连续字面量，生成后它会变成「查模块名」——正确的模块恰因替换成功而永远红（A1 事故）。
tok_m='{{'"MODULE_NAME"'}}'
tok_f='{{'"FRAMEWORK_ROOT"'}}'
if grep -rIl -F -e "$tok_m" -e "$tok_f" . 2>/dev/null | grep -qv '^./.git'; then
  bad "仍有未替换的占位符"; else ok "占位符已清零"; fi
# 2. 无作者机器绝对路径
# 注释行豁免（与 scripts/gates/run-gates.sh 同款，2026-08-01 补齐第二份副本）：
# 判据防的是「**用到的**绝对路径换台机器就废」——注释/文档行里**讲**这个坑的路径
# 没有可执行伤害，逼人改文案 = 判据不许人讨论问题本身。只扫非注释行。
#
# ⚠️ 这条规则在两个文件里各有一份实现（本文件 + scripts/gates/run-gates.sh），
# 且两处报错文案一模一样、从输出分不出是哪份在拦。
# **改一处必须同步另一处**（2026-07-30 障签 abspath-second-copy 的成因：
# 只修了 run-gates.sh 那份，模块这份没动，于是整体仍红、四模块推送被迫走逃生口）。
# 先落变量再判，不用 `producer | grep -q`——那在 pipefail 下有 SIGPIPE 竞态。
abspath_hits=$(git grep -nI -E '/(srv|home|Users)/[a-z]' -- . \
                 ':!*reviewcode*' ':!*/worklog/*' ':!review/reviewreport/*' 2>/dev/null \
                 | grep -vE '^[^:]+:[0-9]+:[[:space:]]*(#|//)' || true)
if [ -n "$abspath_hits" ]; then
  bad "出现硬编码绝对路径（换台机器即废）"; else ok "无硬编码绝对路径"; fi
# 3. 全类型行数（补 gate 的后缀盲区）
over=$(git ls-files -z | xargs -0 -I{} sh -c '[ -f "{}" ] && [ "$(wc -l < "{}")" -gt 500 ] && echo "{}"' 2>/dev/null || true)
[ -z "$over" ] && ok "无超 500 行文件" || bad "超 500 行: $(tr '\n' ' ' <<<"$over")"
# 4. 语义占位符未清零（新脚手模块必红——这是待办清单，不是故障）
todo=$(git grep -lI -E '（迁移时填写|TODO：填|<一句话' -- module_docs/ 2>/dev/null || true)
if [ -n "$todo" ]; then
  bad "module_docs/ 还没填实（新模块的第一件事）：$(tr '\n' ' ' <<<"$todo")"
else ok "module_docs/ 已填实"; fi

# ── 模块特有检查写在下面 ──

exit $fail
