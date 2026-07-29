#!/usr/bin/env bash
# 判据（裁决 J3「一页纸说明每改必核」）：
# 改了 code/<子文件夹>/ 下的代码，同一提交必须满足其一：
#   ① 该子文件夹的 handoff.md 也在本次暂存里（改到位）
#   ② 本次暂存的某个 report.json 的 docs_reviewed 对它表了态（no-change-needed + 理由）
# 子文件夹没有 handoff.md = 直接红（一页纸是必备件，不是可选件）。
# 仓型感知：本仓没有模块布局（无 codeagent/+module_docs/）时跳过；code/_template 不算。
[ "${1:-}" = --describe ] && { echo "14 handoff：改了 code/<子文件夹>/ 必须同批更新其 handoff.md 或在 report.json docs_reviewed 表态（J3 一页纸每改必核）"; exit 0; }
set -uo pipefail
. "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../lib" && pwd -P)/paths.sh"

[ -d codeagent ] && [ -d module_docs ] || exit 0   # 非模块仓（如框架仓）不适用

mapfile -t -d '' staged < <(staged_paths ACMR)
declare -A touched=() covered=()
for f in "${staged[@]}"; do
  case "$f" in
    code/_template/*) continue ;;
    code/*/*) ;;
    *) continue ;;
  esac
  top=${f#code/}; top=code/${top%%/*}
  case "$f" in
    "$top"/handoff.md) covered[$top]=1 ;;
    "$top"/worklog/*|"$top"/report.json) ;;   # 留痕文件自身不触发
    *) touched[$top]=1 ;;
  esac
done
[ "${#touched[@]}" -gt 0 ] || exit 0

# 收集本次暂存 report.json 里 docs_reviewed 表态过的路径
declared=""
for f in "${staged[@]}"; do
  [ "${f##*/}" = report.json ] || continue
  [ -f "$f" ] || continue
  declared+=$(command -v jq >/dev/null 2>&1 && jq -r '.docs_reviewed[]?.path // empty' "$f" 2>/dev/null || true)$'\n'
done

fail=0
for top in "${!touched[@]}"; do
  [ "${covered[$top]:-}" = 1 ] && continue
  if [ ! -f "$top/handoff.md" ]; then
    echo "$top/ 改了代码但没有 handoff.md（一页纸是必备件）——先建：cp 模板 code/*/handoff.md" >&2
    fail=1; continue
  fi
  if grep -qxF "$top/handoff.md" <<<"$declared"; then continue; fi
  echo "$top/ 改了代码，但 $top/handoff.md 既没同批更新、也没在任何暂存 report.json 的 docs_reviewed 里表态（J3 每改必核）" >&2
  fail=1
done
exit $fail
