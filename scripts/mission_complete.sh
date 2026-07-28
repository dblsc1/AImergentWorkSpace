#!/usr/bin/env bash
# 完工检测 —— 硬闸门（挂 pre-commit）。任一条检查失败即拒绝提交。
#
#   scripts/mission_complete.sh              跑适用于当前角色的全部检查
#   scripts/mission_complete.sh --list       打印判据（开工时读，开卷考试）
#   AIMERGENT_ROLE=programmer scripts/mission_complete.sh   显式指定角色
#
# ── 闸门怎么适配不同角色：级联，和规范同一套原则 ──────────────
#
#   ① scripts/checks/_common/        所有角色都跑
#   ② scripts/checks/<角色>/          该角色专属
#   ③ codeagent/<角色>/checks/        本模块给该角色追加的（模块自己维护）
#
# 三层**依次全跑，下层只能加严**（只能新增检查，不能删掉上层的）——
# 这与「模块只能加严项目规范」是同一条原则，闸门不该有例外。
# 增删改查一条检查 = 加/删/改对应层里的一个文件，主脚本永远不用动。
#
# 角色解析顺序：AIMERGENT_ROLE → 从暂存的 worklog 路径推断 → 只跑 _common。
#
# 逃生口：AIMERGENT_MISSION_OVERRIDE="<理由>" 放行，但强制记入 logs/diary.jsonl，绝不静默。
set -uo pipefail
. "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/lib/emit.sh" 2>/dev/null || true

root=$(git rev-parse --show-toplevel 2>/dev/null) || { echo "❌ 不在 Git 仓内" >&2; exit 2; }
cd "$root"
diary=logs/diary.jsonl

# ── 解析角色 ─────────────────────────────────────────────────
role=${AIMERGENT_ROLE:-}
if [ -z "$role" ]; then
  role=$(git diff --cached --name-only --diff-filter=ACMR 2>/dev/null |
         sed -nE 's|^codeagent/([^/]+)/docs/.*|\1|p' | head -1)
fi
[ -n "$role" ] || role=unknown

collect() {
  local d c
  for d in "scripts/checks/_common" "scripts/checks/$role" "codeagent/$role/checks"; do
    [ -d "$d" ] || continue
    for c in "$d"/*.sh; do [ -x "$c" ] && printf '%s\n' "$c"; done
  done
}

if [ "${1:-}" = --list ]; then
  echo "完工前会被逐条核验的项（角色 = $role；开工时就该读到）："
  echo
  printf '  【通用 · 所有角色】\n'
  for c in scripts/checks/_common/*.sh; do [ -x "$c" ] && printf '    · %s\n' "$("$c" --describe)"; done
  if [ -d "scripts/checks/$role" ]; then
    printf '  【%s 专属】\n' "$role"
    for c in "scripts/checks/$role"/*.sh; do [ -x "$c" ] && printf '    · %s\n' "$("$c" --describe)"; done
  fi
  if [ -d "codeagent/$role/checks" ]; then
    printf '  【本模块追加】\n'
    for c in "codeagent/$role/checks"/*.sh; do [ -x "$c" ] && printf '    · %s\n' "$("$c" --describe)"; done
  fi
  echo
  echo "  角色未识别时只跑【通用】；显式指定用 AIMERGENT_ROLE=<角色>。"
  echo "  逃生口：AIMERGENT_MISSION_OVERRIDE=\"<理由>\"（放行但强制记账）"
  exit 0
fi

pass=0; fail=0; failed_names=()
printf '── 完工检测（角色 = %s）──\n' "$role"
while IFS= read -r c; do
  [ -n "$c" ] || continue
  name=$(basename "$c" .sh)
  layer=$(dirname "$c"); layer=${layer##*/}
  if out=$("$c" 2>&1); then
    printf '  ✅ %-26s [%s]\n' "$name" "$layer"
    pass=$((pass+1))
  else
    printf '  ❌ %-26s [%s]\n' "$name" "$layer"
    sed 's/^/       /' <<<"$out"
    fail=$((fail+1)); failed_names+=("$name")
  fi
done < <(collect)

[ "$((pass+fail))" -gt 0 ] || { echo "⚠️  没有可执行的检查项（scripts/checks/ 是空的？）" >&2; exit 2; }

log_event() {
  mkdir -p "$(dirname "$diary")"
  ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  printf '{"ts":"%s","event":"%s","role":"%s","branch":"%s","pass":%d,"fail":%d,"failed":"%s","reason":%s}\n' \
    "$ts" "$1" "$role" "$(git rev-parse --abbrev-ref HEAD)" "$pass" "$fail" \
    "${failed_names[*]:-}" "${2:-null}" >> "$diary"
}

refresh_console() {
  [ -x scripts/reindex.sh ] && scripts/reindex.sh > logs/INDEX.md 2>/dev/null || true
  [ -x scripts/console.sh ] && scripts/console.sh > logs/console.json 2>/dev/null || true
}

if [ "$fail" -eq 0 ]; then
  printf '🟢 完工检测通过（%d 项）\n' "$pass"
  log_event mission_complete
  refresh_console
  exit 0
fi

if [ -n "${AIMERGENT_MISSION_OVERRIDE:-}" ]; then
  echo "⚠️  逃生口放行：$AIMERGENT_MISSION_OVERRIDE"
  echo "    已强制记入 $diary —— 这次绕过在台账上是可见的。"
  log_event mission_override "\"$AIMERGENT_MISSION_OVERRIDE\""
  refresh_console
  exit 0
fi

printf '🔴 完工检测未通过（%d 项）——拒绝提交\n' "$fail" >&2
echo "   确需绕过：AIMERGENT_MISSION_OVERRIDE=\"<理由>\" git commit ...（放行但记账）" >&2
log_event mission_blocked
exit 1
