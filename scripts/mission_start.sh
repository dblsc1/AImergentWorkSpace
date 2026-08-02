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
. "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/lib/checklist.sh" || { printf '❌ %s：载入 checklist.sh 失败 —— 拒绝以「什么都没验」的姿态退 0\n' "${BASH_SOURCE[0]}" >&2; exit 2; }
. "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/lib/lease.sh" || { printf '❌ %s：载入 lease.sh 失败 —— 拒绝以「什么都没验」的姿态退 0\n' "${BASH_SOURCE[0]}" >&2; exit 2; }
die() { printf '❌ %s\n' "$*" >&2; exit 1; }

root=$(git rev-parse --show-toplevel 2>/dev/null) || die "不在 Git 仓内"
cd "$root"

# **开工第一件事就是回收过期签**（2026-08-02 起）。实证：派活方派完活走人，
# 签一直挂着，被卡的一方沉默地重试。一天内同一个错犯了三次，每次 worklog 都写
# 「以后记得还签」——靠记性无效，只能靠过期。
# 排除自己：本轮要发给谁，谁的旧签本来就会被覆盖。
lease_reap "${1:-}" >/dev/null || true
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
│  8  障签                  blockers/ 便条：写区压着未决裁决 → 拒发签
│                          （便条带「波及：<前缀>」才硬拦；其余仅列出提示）
│  9  写区没比上次放宽      **只警告不拦**：放宽合法，但要在任务单里写清为什么
│                          05 拦下越区提交后「顺手放宽自己的签」是棘轮，没有反向齿
│
└─ 全过才发签、才出派单提示词

LIST
  exit 0
fi

if [ "${1:-}" = --list ]; then
  echo "当前持有的写区路签："
  shopt -s nullglob
  for f in "$lease_dir"/*.lease; do
    _h=$(basename "$f" .lease); _a=$(lease_age "$_h")
    if [ "$_a" -lt 0 ] 2>/dev/null; then _ages='年龄未知(无 meta)'
    else _ages="$(( _a / 60 ))分钟"; fi
    printf '  %-22s %-12s %s\n' "$_h" "$_ages" "$(tr '\n' ' ' < "$f")"
  done
  shopt -u nullglob
  exit 0
fi

if [ "${1:-}" = --release ]; then
  r=${2:?用法: --release <角色>}
  if lease_release "$r"; then echo "✅ 已还签：$r"; else echo "ℹ️  $r 本来就没有签"; fi
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
clash=""; clash_holder=""
shopt -s nullglob
for f in "$lease_dir"/*.lease; do
  holder=$(basename "$f" .lease); [ "$holder" = "$role" ] && continue
  while IFS= read -r held; do
    [ -n "$held" ] || continue
    for w in "${want[@]}"; do
      case "$w"  in "$held"*) clash="$w ⊂ $held"; clash_holder=$holder ;; esac
      case "$held" in "$w"*)  clash="$w ⊃ $held"; clash_holder=$holder ;; esac
    done
  done < "$f"
done
shopt -u nullglob
if [ -z "$clash" ]; then cl_ok 2 "写区不与他人重叠：${want[*]}"
else
  # **拒发不许只说「重叠」**：被卡方看到的必须是谁、多久、什么任务、三条出路。
  # 原版只印一句「写区重叠」，被卡的子代理无从判断该等还是该上报，于是沉默重试。
  cl_bad 2 "写区重叠" \
    "$clash ← 持有者 $(lease_holder_detail "$clash_holder")" \
    "① 让持有者跑 scripts/mission_start.sh --release $clash_holder；② 把写区切细到不相交（多数重叠是粒度太粗，不是真冲突）；③ 签超过 TTL（${LEASE_TTL_SECONDS}s）会在下次 mission_start 时自动回收"
  # 层②（2026-08-02 CFO 裁决）：**被挡住的这一刻**才是释放的关键路径。
  # 把「能不能强收」的判据连同可粘贴的命令直接印给被卡方 —— 但不自动收
  # （lib/lease.sh 的红线：静默回收会让双写安静地发生）。
  _retry=$(printf 'scripts/mission_start.sh %q %q' "$role" "$task")
  for _w in "${want[@]}"; do _retry="$_retry $(printf '%q' "$_w")"; done
  while IFS= read -r _l; do cl_note "$_l"; done < <(lease_takeover_advice "$clash_holder" "$_retry")
fi

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
# 列出他人持签时**带上年龄**：一个挂了 90 分钟的签和一个刚发 2 分钟的签，
# 处置完全不同，只印名字看不出区别。
_stale=""
shopt -s nullglob
for f in "$lease_dir"/*.lease; do
  _h=$(basename "$f" .lease); [ "$_h" = "$role" ] && continue
  _stale="${_stale:+$_stale；}$(lease_holder_detail "$_h")"
done
shopt -u nullglob
if [ -z "$_stale" ]; then cl_ok 7 "无他人遗留路签"
else cl_skip 7 "他人持签中：$_stale" "不冲突即可并发；确认已完工的用 --release <角色> 还签；超 TTL（${LEASE_TTL_SECONDS}s）下次 mission_start 自动回收"; fi

# 8 障签（blockers/ 便条）：派活别派进未决裁决压着的写区（2026-07-30 用户点破：
# 便条防的是执行者停等，那发签时刻就该看它，不是等着陆才撞）。
# 便条带「波及：<前缀>」且与申领写区重叠 → 硬拒；其余在场便条 → 列出提示。
_blk_hard=""; _blk_open=""
while IFS= read -r -d '' _b; do
  [ -f "$_b" ] || continue
  _blk_open="$_blk_open ${_b#blockers/}"
  while IFS= read -r _scope; do
    _scope=$(sed 's/^波及：//; s/^[[:space:]]*//; s/[[:space:]]*$//' <<<"$_scope"); [ -n "$_scope" ] || continue
    _scope_n=$(norm "$_scope")
    for w in "${want[@]}"; do
      case "$w" in "$_scope_n"*) _blk_hard="$w ⊂ 波及($_scope_n) ← ${_b}" ;; esac
      case "$_scope_n" in "$w"*) _blk_hard="$w ⊃ 波及($_scope_n) ← ${_b}" ;; esac
    done
  done < <(grep '^波及：' "$_b" 2>/dev/null)
done < <(git ls-files -z 'blockers/*.md' 2>/dev/null)
if [ -n "$_blk_hard" ]; then
  cl_bad 8 "障签" "写区压着未决裁决：$_blk_hard" "先解障销便条，或把写区切出波及面——别让 programmer 停等一个没做的决定"
elif [ -n "$_blk_open" ]; then
  cl_skip 8 "在场便条：$_blk_open" "与本写区无关可并行；相关的自觉停一停"
else
  cl_ok 8 "无在等的裁决便条"
fi

# 9 写区粒度棘轮（F2，2026-08-02）：**只警告不拦**。
# 病根不是「谁忘了还签」，是 05 拦下越区提交后持有者的标准修法是放宽自己的签 ——
# 五天 diary 里每一次膨胀前 90 秒内都紧跟一条 failed:write-lease。
# 棘轮没有反向齿，终点是一个角色持有整仓，那时路签就成了派活方持有的全局互斥锁。
# 为什么不硬拦：「多宽算太宽」是判断题，硬拦会逼人用逃生口（判例库明文）。
# 所以只把事实摆到申领者眼前 —— 包括**签史**，光说「有点宽」没有信息量。
_wide=$(lease_widening "$role" "${want[@]}")
if [ -n "$_wide" ]; then
  cl_skip 9 "写区比上一次放宽了（只提醒，不拦）" \
    "放宽本身合法；但请在任务单里写清为什么——多数「必须放宽」其实是写区切得太粗"
  while IFS= read -r _l; do [ -n "$_l" ] && cl_note "$_l"; done <<<"$_wide"
  while IFS= read -r _l; do [ -n "$_l" ] && cl_note "$_l"; done < <(lease_grant_history "$role")
else
  cl_ok 9 "写区没有比上一次放宽"
fi

# 起飞预告：这片写区可能牵动哪些长期文档 —— 现在知道，好过着陆时被拦
if [ -f "$root/scripts/lib/docmap.sh" ]; then
  . "$root/scripts/lib/docmap.sh" || { printf '❌ %s：载入 docmap.sh 失败 —— 拒绝以「什么都没验」的姿态退 0\n' "${BASH_SOURCE[0]}" >&2; exit 2; }
  _pre=$(dm_impact "${want[@]}" 2>/dev/null | cut -f1 | sort -u)
  if [ -n "$_pre" ]; then
    cl_note "这片写区可能牵动的长期文档（着陆时会逐条核）："
    while read -r _d; do [ -n "$_d" ] && cl_table_row "  " "$_d"; done <<<"$_pre"
    cl_note "干活中随时可查：scripts/doc_impact.sh"
    printf '│\n'
  fi
fi

if ! cl_footer "起飞检查通过，发签并出派单提示词" "起飞检查未通过，不发签"; then
  # ── F4（2026-08-02）：**拒发也要记账** ──────────────────────────
  # 原来只有发签成功 emit lease_grant，拒发走 cl_bad 后直接 exit，全程零事件。
  # 后果不是少一行日志，是**问题规模不可数**：CFO 报「今天为此卡了三次」，
  # 而 logs/diary.jsonl 里 18 条发签、0 条拒发 —— 那句话查无实据。
  # 更要命的是任何「优化路签」的方案都验不出效果：没有基线就没有前后对比。
  # 铁律 19 的证据强度问题：先能数，再改。
  emit_event lease_denied \
    "$role: 申领 ${want[*]:-（未解析）} 被拒（${CL_FAILED_ITEMS[*]:-未记录}）" 1
  exit 1
fi

printf '%s\n' "${want[@]}" > "$lease_dir/$role.lease"
lease_write_meta "$role" "$task"    # 记发签时间/任务/TTL —— 没有它就判不了过期
if [ "${#docs[@]}" -gt 0 ]; then printf '%s\n' "${docs[@]}" > "$lease_dir/$role.docs"
else rm -f "$lease_dir/$role.docs"; fi
emit_event lease_grant "$role: ${want[*]}"

# ── ③ 出提示词 ────────────────────────────────────────────
printf '✅ 路签已发：%s → %s\n\n' "$role" "${want[*]}" >&2
exec "$root/scripts/dispatch.sh" "$role" "$task"
