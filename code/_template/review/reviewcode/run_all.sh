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
if grep -rIl -F -e '{{MODULE_NAME}}' -e '{{FRAMEWORK_ROOT}}' . 2>/dev/null | grep -qv '^./.git'; then
  bad "仍有未替换的占位符"; else ok "占位符已清零"; fi
# 2. 无作者机器绝对路径
if git grep -nI -E '/(srv|home|Users)/[a-z]' -- . ':!*reviewcode*' 2>/dev/null | grep -q .; then
  bad "出现硬编码绝对路径（换台机器即废）"; else ok "无硬编码绝对路径"; fi
# 3. 全类型行数（补 gate 的后缀盲区）
over=$(git ls-files -z | xargs -0 -I{} sh -c '[ -f "{}" ] && [ "$(wc -l < "{}")" -gt 500 ] && echo "{}"' 2>/dev/null || true)
[ -z "$over" ] && ok "无超 500 行文件" || bad "超 500 行: $(tr '\n' ' ' <<<"$over")"
# 4. 语义占位符未清零
if git grep -nI -E '（迁移时填写|TODO：填|<一句话' -- module_docs/ 2>/dev/null | grep -q .; then
  bad "module_docs/ 仍有语义占位符未填"; else ok "module_docs/ 已填实"; fi

# ── 模块特有检查写在下面 ──

exit $fail
