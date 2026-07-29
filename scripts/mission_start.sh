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
. "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/lib/checklist.sh"
die() { printf '❌ %s\n' "$*" >&2; exit 1; }

root=$(git rev-parse --show-toplevel 2>/dev/null) || die "不在 Git 仓内"
cd "$root"
# 停线旗：旗在 = 不发新签（存量工作可收尾，新活不开；判据见 agents/protocol/supervision.md）
if [ -f logs/STOPLINE ] && [ "${1:-}" != --release ] && [ "${1:-}" != --list ] && [ "${1:-}" != --checklist ]; then
  printf '🛑 停线中，不发新写区路签。原因：\n' >&2; sed 's/^/   /' logs/STOPLINE >&2; exit 1
fi
lease_dir=$(git rev-parse --path-format=absolute --git-common-dir)/aimergent-leases
mkdir -p "$lease_dir"

# 目录 → 带尾斜杠的前缀；**已存在的普通文件 → 原样**（不加斜杠）。
# 病根（CFO 2026-07-29 实报）：一律加斜杠让 .gitignore 变成 .gitignore/，
# checks/05 前缀匹配永远不中 → 根级单文件无法合法进入任何提交；
# 与白名单 .gitignore 叠加 = 静默丢数据路径（白名单行加不上 → 新目录被吞）。
norm() { local p=${1#./}; p=${p%/}; if [ -f "$p" ] && [ ! -d "$p" ]; then printf '%s' "$p"; else printf '%s/' "$p"; fi }

if [ "${1:-}" = --checklist ]; then
  cat <<'LIST'

┌─ 起飞前检查单（scripts/mission_start.sh）
│
│  1  任务单四小节齐全      目标 / 可判定的验收标准 / 可触碰目录 / 自检门
│                          缺任一项 → 补齐再派；含糊的任务单会污染它派出去的所有产出
│  2  写区不与他人重叠      路径前缀互含即算重叠（父/子文件夹都算）
│                          重叠 → 等对方还签，或把写区切细到不相交
│  3  预期文档变更已声明    --docs update:<路径> create:<路径>
│                          着陆时机器逐条核对；不声明 = 着陆时只能靠反查兜底
│  4  角色卡存在且边界填实  scripts/new_agent.sh <角色> 生成，{{WRITABLE}} 已替换
│  5  门禁已安装            pre-commit / pre-push / commit-msg 三个 hook 齐
│                          缺 → scripts/install-gates.sh .
│  6  不在 main 上          派活前先开 feat/ 分支
│  7  无遗留路签            上一轮的签没还会挡住本轮；--release <角色> 还签
│
└─ 全过才发签、才出派单提示词

LIST
  exit 0
fi

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

cl_header "起飞前检查单 · $role" "任务单 $task"

# 1 任务单四小节
miss=()
grep -q '目标' "$task" || miss+=("目标")
grep -q '验收' "$task" || miss+=("验收标准")
grep -qE '触碰|可写|边界' "$task" || miss+=("可触碰目录")
grep -qE '自检门|自检' "$task" || miss+=("自检门")
if [ ${#miss[@]} -eq 0 ]; then cl_ok 1 "任务单四小节齐全"
else cl_bad 1 "任务单四小节" "缺 ${miss[*]}" "补进 $task —— 含糊的任务单会污染它派出去的所有产出"; fi

# 2 路签重叠（父/子文件夹都算）
declare -a want=()
for p in "$@"; do want+=("$(norm "$p")"); done
clash=""
shopt -s nullglob
for f in "$lease_dir"/*.lease; do
  holder=$(basename "$f" .lease); [ "$holder" = "$role" ] && continue
  while IFS= read -r held; do
    [ -n "$held" ] || continue
    for w in "${want[@]}"; do
      case "$w"  in "$held"*) clash="$w ⊂ $held（$holder 持有）" ;; esac
      case "$held" in "$w"*)  clash="$w ⊃ $held（$holder 持有）" ;; esac
    done
  done < "$f"
done
shopt -u nullglob
if [ -z "$clash" ]; then cl_ok 2 "写区不与他人重叠：${want[*]}"
else cl_bad 2 "写区重叠" "$clash" "等对方 --release 还签，或把写区切细到不相交"; fi

# 3 预期文档变更
if [ "${#docs[@]}" -gt 0 ]; then
  cl_ok 3 "预期文档变更已声明（${#docs[@]} 条，着陆时逐条核）"
  while IFS=$'\t' read -r _a _p; do cl_table_row "  $_a" "$_p"; done < <(printf '%s\n' "${docs[@]}")
else
  cl_skip 3 "未声明预期文档变更" "着陆时只能靠「谁提到了我」反查兜底；建议 --docs update:<路径>"
fi

# 4 角色卡存在且边界已填实
# 两处布局：模块级在 codeagent/<角色>/；项目级（cfo / consulter）在 agents/<角色>/。
# 只认前者会让项目级角色永远过不了这一项 —— 判据必须匹配角色所在层级。
_card="codeagent/$role/AGENTS.md"
[ -f "$_card" ] || _card="agents/$role/AGENTS.md"
if [ ! -f "$_card" ]; then
  cl_bad 4 "角色卡" "codeagent/$role/AGENTS.md 与 agents/$role/AGENTS.md 都不存在" "scripts/new_agent.sh $role"
elif grep -q '{{' "$_card" 2>/dev/null; then
  cl_bad 4 "角色卡边界" "$_card 仍有未替换占位符" "重跑 scripts/new_agent.sh $role"
else
  cl_ok 4 "角色卡就位且写边界已填实"
fi

# 5 门禁已安装
_hooks=$(git rev-parse --path-format=absolute --git-path hooks)
_missing=""
for _h in pre-commit pre-push commit-msg; do [ -x "$_hooks/$_h" ] || _missing="$_missing $_h"; done
if [ -z "$_missing" ]; then cl_ok 5 "门禁已装（pre-commit / pre-push / commit-msg）"
else cl_bad 5 "门禁未装" "缺$_missing" "scripts/install-gates.sh ."; fi

# 6 分支
_br=$(git rev-parse --abbrev-ref HEAD 2>/dev/null)
case "$_br" in
  main|master) cl_bad 6 "分支纪律" "当前在 $_br 上" "git checkout -b feat/<主题>" ;;
  *) cl_ok 6 "在 $_br 上（非 main）" ;;
esac

# 7 遗留路签
_stale=$(ls "$lease_dir"/*.lease 2>/dev/null | xargs -r -n1 basename 2>/dev/null | sed 's/\.lease$//' | grep -vx "$role" | tr '\n' ' ')
if [ -z "$_stale" ]; then cl_ok 7 "无他人遗留路签"
else cl_skip 7 "他人持签中：$_stale" "不冲突即可并发；确认已完工的用 --release <角色> 还签"; fi

# 起飞预告：这片写区可能牵动哪些长期文档 —— 现在知道，好过着陆时被拦
if [ -f "$root/scripts/lib/docmap.sh" ]; then
  . "$root/scripts/lib/docmap.sh"
  _pre=$(dm_impact "${want[@]}" 2>/dev/null | cut -f1 | sort -u)
  if [ -n "$_pre" ]; then
    cl_note "这片写区可能牵动的长期文档（着陆时会逐条核）："
    while read -r _d; do [ -n "$_d" ] && cl_table_row "  " "$_d"; done <<<"$_pre"
    cl_note "干活中随时可查：scripts/doc_impact.sh"
    printf '│\n'
  fi
fi

cl_footer "起飞检查通过，发签并出派单提示词" "起飞检查未通过，不发签" || exit 1

printf '%s\n' "${want[@]}" > "$lease_dir/$role.lease"
if [ "${#docs[@]}" -gt 0 ]; then printf '%s\n' "${docs[@]}" > "$lease_dir/$role.docs"
else rm -f "$lease_dir/$role.docs"; fi
emit_event lease_grant "$role: ${want[*]}"

# ── ③ 出提示词 ────────────────────────────────────────────
printf '✅ 路签已发：%s → %s\n\n' "$role" "${want[*]}" >&2
exec "$root/scripts/dispatch.sh" "$role" "$task"
