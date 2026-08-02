#!/usr/bin/env bash
# 判据：仓内已存在的 canonical report.json 必须被 Git 跟踪且工作区干净。
[ "${1:-}" = --describe ] && { echo "20 report：已存在的 canonical report.json 必须已跟踪且工作区干净"; exit 0; }
set -uo pipefail
. "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../lib" && pwd -P)/paths.sh" || { printf '❌ %s：载入 paths.sh 失败 —— 拒绝以「什么都没验」的姿态退 0\n' "${BASH_SOURCE[0]}" >&2; exit 2; }
fail=0
while IFS= read -r -d '' f; do
  # 边界：嵌套模块仓的文件归模块自己的门禁管，不归本仓（A3 跨仓误伤）
  own=$(git -C "$(dirname "$f")" rev-parse --show-toplevel 2>/dev/null)
  [ "$own" = "$(git rev-parse --show-toplevel)" ] || continue
  [ -n "$f" ] || continue
  if ! git ls-files --error-unmatch "$f" >/dev/null 2>&1; then
    echo "report 未被 Git 跟踪：$f（未跟踪 = 未产出）" >&2; fail=1; continue
  fi
  if [ -n "$(git diff --name-only -- "$f")" ]; then
    echo "report 工作区脏（有未暂存改动）：$f" >&2; fail=1
  fi
done < <(find . -path ./.git -prune -o -name report.json -print0 2>/dev/null | sed -z 's|^\./||')
exit $fail
