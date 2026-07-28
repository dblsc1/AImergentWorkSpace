#!/usr/bin/env bash
# 完工检测 —— 硬闸门（挂 pre-commit）。任一条检查失败即拒绝提交。
#
#   scripts/mission_complete.sh          跑全部检查
#   scripts/mission_complete.sh --list   打印全部检查项（开工时读，开卷考试）
#
# 增删改查检查项 = 加/删/改 scripts/checks/ 下的文件，本脚本不用动。
# 每个检查脚本约定：--describe 打印判据；无参运行时 0=通过、非 0=失败并在 stderr 说明。
#
# 逃生口：AIMERGENT_MISSION_OVERRIDE="<理由>" 放行，但强制记入 logs/diary.jsonl，绝不静默。
set -uo pipefail

root=$(git rev-parse --show-toplevel 2>/dev/null) || { echo "❌ 不在 Git 仓内" >&2; exit 2; }
cd "$root"
checks_dir=scripts/checks
diary=logs/diary.jsonl

if [ "${1:-}" = --list ]; then
  echo "完工前会被逐条核验的项（本次任务的判据，开工时就该读到）："
  echo
  for c in "$checks_dir"/*.sh; do
    [ -x "$c" ] || continue
    printf '  · %s\n' "$("$c" --describe)"
  done
  echo
  echo "逃生口：AIMERGENT_MISSION_OVERRIDE=\"<理由>\"（放行但强制记账）"
  exit 0
fi

[ -d "$checks_dir" ] || { echo "❌ 缺少 $checks_dir" >&2; exit 2; }

pass=0; fail=0; failed_names=()
echo "── 完工检测 ──"
for c in "$checks_dir"/*.sh; do
  [ -x "$c" ] || continue
  name=$(basename "$c" .sh)
  if out=$("$c" 2>&1); then
    printf '  ✅ %s\n' "$name"
    pass=$((pass+1))
  else
    printf '  ❌ %s\n' "$name"
    sed 's/^/       /' <<<"$out"
    fail=$((fail+1)); failed_names+=("$name")
  fi
done

log_event() {
  mkdir -p "$(dirname "$diary")"
  ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  printf '{"ts":"%s","event":"%s","branch":"%s","pass":%d,"fail":%d,"failed":"%s","reason":%s}\n' \
    "$ts" "$1" "$(git rev-parse --abbrev-ref HEAD)" "$pass" "$fail" \
    "${failed_names[*]:-}" "${2:-null}" >> "$diary"
}

refresh_console() {
  [ -x scripts/reindex.sh ] && scripts/reindex.sh > logs/INDEX.md 2>/dev/null || true
  [ -x scripts/console.sh ] && scripts/console.sh > logs/console.json 2>/dev/null || true
}

if [ "$fail" -eq 0 ]; then
  echo "🟢 完工检测通过（$pass 项）"
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

echo "🔴 完工检测未通过（$fail 项）——拒绝提交" >&2
echo "   确需绕过：AIMERGENT_MISSION_OVERRIDE=\"<理由>\" git commit ...（放行但记账）" >&2
log_event mission_blocked
exit 1
