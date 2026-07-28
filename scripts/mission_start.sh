#!/usr/bin/env bash
# 开工 —— 发路签、划写区、出提示词。
#
#   scripts/mission_start.sh <角色> <任务单> <写区前缀...> [--docs <update|create>:<文档路径> ...]
#   scripts/mission_start.sh --release <角色>        提前还签
#   scripts/mission_start.sh --list                  看当前所有签
#
# 路签机制（火车那套）：
#   · 发签在这里 —— 申请写区，与现有签**前缀重叠**就拒绝派活（父/子文件夹都算重叠）。
#   · 验签在 checks/_common/05-write-lease.sh —— 暂存的每个文件必须落在本角色的签内。
#   · 还签在 mission_complete.sh 通过之后。
#
# 「无法绕过」落在**验签**：你可以不跑本脚本，但那样你没有签，一提交就被 pre-commit 拦死。
# 发签是自愿的，验签是强制的。
set -uo pipefail
. "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/lib/emit.sh" 2>/dev/null || true
die() { printf '❌ %s\n' "$*" >&2; exit 1; }

root=$(git rev-parse --show-toplevel 2>/dev/null) || die "不在 Git 仓内"
cd "$root"
lease_dir=$(git rev-parse --path-format=absolute --git-common-dir)/aimergent-leases
mkdir -p "$lease_dir"

norm() { local p=${1#./}; p=${p%/}; printf '%s/' "$p"; }   # 统一成带尾斜杠的前缀

if [ "${1:-}" = --list ]; then
  echo "当前持有的写区路签："
  shopt -s nullglob
  for f in "$lease_dir"/*.lease; do
    printf '  %-22s %s\n' "$(basename "$f" .lease)" "$(tr '\n' ' ' < "$f")"
  done
  shopt -u nullglob
  exit 0
fi

if [ "${1:-}" = --release ]; then
  r=${2:?用法: --release <角色>}
  rm -f "$lease_dir/$r.lease" "$lease_dir/$r.docs" && echo "✅ 已还签：$r"
  emit_event lease_release "$r"
  exit 0
fi

role=${1:-}; task=${2:-}; shift 2 2>/dev/null || true
[ -n "$role" ] && [ -n "$task" ] || die "用法: mission_start.sh <角色> <任务单> <写区...> [--docs <update|create>:<路径> ...]"

# ── 预期文档变更：arbiter 派单时就声明，完工时机器逐条核对 ──
# 这一步把「铁律 11 靠自觉」变成「派单即承诺，交付即核对」。
declare -a docs=() prefixes=()
mode=write
for a in "$@"; do
  case "$a" in
    --docs) mode=docs; continue ;;
  esac
  case "$mode" in
    write) prefixes+=("$a") ;;
    docs)
      case "$a" in
        update:*|create:*) docs+=("${a%%:*}	${a#*:}") ;;
        *) die "--docs 项格式须为 update:<路径> 或 create:<路径>，收到: $a" ;;
      esac ;;
  esac
done
set -- "${prefixes[@]}"
[ -f "$task" ] || die "找不到任务单: $task"
[ $# -ge 1 ] || die "必须显式声明写区，例: code/<模块>/backend/orders/"

# ── ① 任务单开工前检查 ─────────────────────────────────────
miss=()
grep -q '目标' "$task" || miss+=("目标")
grep -q '验收' "$task" || miss+=("验收标准")
grep -qE '触碰|可写|边界' "$task" || miss+=("可触碰目录")
grep -qE '自检门|自检' "$task" || miss+=("自检门")
[ ${#miss[@]} -eq 0 ] || die "任务单缺小节 ${miss[*]} —— 一份含糊的任务单会污染它派出去的所有产出"

# ── ② 路签重叠检测（父/子文件夹都算重叠）──────────────────
declare -a want=()
for p in "$@"; do want+=("$(norm "$p")"); done

shopt -s nullglob
for f in "$lease_dir"/*.lease; do
  holder=$(basename "$f" .lease)
  [ "$holder" = "$role" ] && continue
  while IFS= read -r held; do
    [ -n "$held" ] || continue
    for w in "${want[@]}"; do
      case "$w" in "$held"*) die "写区与 $holder 的签重叠（被包含）：$w ⊂ $held" ;; esac
      case "$held" in "$w"*) die "写区与 $holder 的签重叠（包含对方）：$w ⊃ $held" ;; esac
    done
  done < "$f"
done
shopt -u nullglob

printf '%s\n' "${want[@]}" > "$lease_dir/$role.lease"
if [ "${#docs[@]}" -gt 0 ]; then
  printf '%s\n' "${docs[@]}" > "$lease_dir/$role.docs"
  printf '📄 预期文档变更（完工时机器逐条核对）：\n' >&2
  printf '   %s\n' "${docs[@]}" >&2
else
  rm -f "$lease_dir/$role.docs"
fi
emit_event lease_grant "$role: ${want[*]}"

# ── ③ 出提示词 ────────────────────────────────────────────
printf '✅ 路签已发：%s → %s\n\n' "$role" "${want[*]}" >&2
exec "$root/scripts/dispatch.sh" "$role" "$task"
