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
. "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/lib/checklist.sh" || { printf '❌ %s：载入 checklist.sh 失败 —— 拒绝以「什么都没验」的姿态退 0\n' "${BASH_SOURCE[0]}" >&2; exit 2; }

root=$(git rev-parse --show-toplevel 2>/dev/null) || { echo "❌ 不在 Git 仓内" >&2; exit 2; }
cd "$root"
diary=logs/diary.jsonl

# ── 解析角色 ─────────────────────────────────────────────────
role=${AIMERGENT_ROLE:-}
_role_why=""
if [ -z "$role" ]; then
  # F3（2026-08-02）：原来这里是一条只认旧布局的正则
  # `s|^codeagent/([^/]+)/docs/.*|\1|p`，J3 之后的五条 canonical 留痕路径实测**全部**
  # 推成空。判据已搬进 lib/paths.sh 的 role_from_trace_path / role_from_staged
  # （两层优先级 + 同层歧义返回空，理由见那里）；selftest #56 逐条喂五条路径。
  . "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/lib/paths.sh" || { printf '❌ %s：载入 paths.sh 失败 —— 拒绝以「什么都没验」的姿态退 0\n' "${BASH_SOURCE[0]}" >&2; exit 2; }
  role=$(git diff --cached -z --name-only --diff-filter=ACMR 2>/dev/null |
         tr '\0' '\n' | role_from_staged) || role=""
  [ -n "$role" ] || _role_why="暂存路径里推断不出角色（或同层出现了两个不同角色）"
fi
if [ -z "$role" ]; then
  role=unknown
  # **说清楚为什么是 unknown**。原来这里静默变成 unknown，然后 checks/05 报
  # 「角色 unknown 没有写区路签」—— 被拦住的人会以为是路签的问题，
  # 甚至真的去给 unknown 申领一块签。错误信息把人引向错的地方，判例库记过三次。
  printf '⚠️  角色未识别：%s\n' "$_role_why" >&2
  printf '    只会跑【通用】检查；写区路签那一项会因为找不到 unknown.lease 而失败。\n' >&2
  printf '    正解：AIMERGENT_ROLE=<角色> git commit …（角色名＝申领路签时用的那个）\n' >&2
fi
export AIMERGENT_ROLE="$role"   # 子检查脚本（05-write-lease.sh 等）靠环境变量读角色，
                                 # 不 export 时子进程拿到空值，写区路签核验静默放行（障签 2026-07-31）。

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

cl_header "着陆检查单 · $role" "分支 $(git rev-parse --abbrev-ref HEAD 2>/dev/null) · $(git diff --cached --name-only | wc -l) 个文件待提交"

# 起飞时声明了什么 —— 对照表放在最前面，人一眼能看出「说要改的，改了没」
lease_dir=$(git rev-parse --path-format=absolute --git-common-dir)/aimergent-leases
if [ -n "$role" ] && [ -f "$lease_dir/$role.docs" ]; then
  cl_note "起飞时声明的文档变更（本单第 11 项逐条核）："
  while IFS=$'\t' read -r _a _p; do
    [ -n "$_p" ] || continue
    if git diff --cached --name-only -z | tr '\0' '\n' | grep -qxF -- "$_p"; then
      cl_table_row "  ✓ $_a" "$_p"
    else
      cl_table_row "  ✗ $_a" "$_p   ← 说要改，没在本次提交里"
    fi
  done < "$lease_dir/$role.docs"
  printf '│\n'
fi
if [ -f "$lease_dir/${role}.lease" ]; then
  cl_note "持有写区：$(tr '\n' ' ' < "$lease_dir/$role.lease")"
  # 把签的年龄摆在每次提交前的必经之路上（2026-08-02 事故的第三层）。
  #
  # ⚠️ **这里只提醒，不自动还签**，与上游 X_structure 的做法**有意不同**：
  #    在 X 那边 mission_complete.sh 是一个独立的「我完工了」仪式；
  #    在本仓它就是 pre-commit 钩子的主体（scripts/hooks/pre-commit 第 23 行），
  #    **每次 commit 都会跑**。一个任务多次提交是常态，在这里还签
  #    等于每提交一次就把互斥关掉一次 —— 那不是修复，是把闸门拆了。
  #    「完工即自动还」在本仓需要另找落点，已挂给 consulter（见 worklog）。
  #    本轮真正封死事故的是 TTL 自动回收（lib/lease.sh + mission_start.sh）。
  if [ -f "$root/scripts/lib/lease.sh" ]; then
    . "$root/scripts/lib/lease.sh" || { printf '❌ %s：载入 lease.sh 失败 —— 拒绝以「什么都没验」的姿态退 0\n' "${BASH_SOURCE[0]}" >&2; exit 2; }
    _lage=$(lease_age "$role")
    if [ "$_lage" -lt 0 ] 2>/dev/null; then
      cl_note "  签龄未知（无 meta，上一版留下的）—— 完工后手动还：scripts/mission_start.sh --release $role"
    elif [ "$_lage" -gt $(( LEASE_TTL_SECONDS / 2 )) ]; then
      cl_note "  ⏰ 已持有 $(( _lage / 60 )) 分钟（TTL $(( LEASE_TTL_SECONDS / 60 )) 分钟）—— 若已完工请立刻还签，别人正在等：scripts/mission_start.sh --release $role"
    else
      cl_note "  签龄 $(( _lage / 60 )) 分钟；完工后还签：scripts/mission_start.sh --release $role"
    fi
  fi
  printf '│\n'
fi

# ── 层②：主脚本不许吞掉「check 自己炸了却退 0」（S1，2026-08-02 P0）────────
# 病根第三段：原来这里是 `if out=$("$c" 2>&1); then cl_ok`，
# **成功分支把 $out 整个丢弃** —— 于是 `paths.sh: No such file or directory`
# 和 `staged_paths: command not found` 一个字都不显示，检查单照打 ✅。
# 实测：删掉 scripts/lib/paths.sh，十五条 _common 里十二条静默变绿。
#
# 为什么只给每条 check 加 `|| exit 2` 不够：那是修今天这十八处实例。
# 明天新写的一条照样可能「函数没定义 → 数组空 → exit 0」，
# 而**主脚本仍然会把它判成 pass**。两层各修各的病：
#   层① check 自己 source 失败要死  ·  层② 主脚本不许信一个自相矛盾的成功
#
# 判据钉在 **bash 自己的诊断前缀** `<脚本>: line N: …` 上，不是裸关键词 ——
# check 的正常业务输出里完全可能出现「找不到文件」这类字样，
# 而 `: line N:` 只有解释器级错误才会带。
interpreter_blew_up() {
  grep -qE ': line [0-9]+: .*(command not found|No such file or directory|unbound variable|syntax error|Permission denied|bad substitution)' <<<"${1:-}"
}

pass=0; fail=0; failed_names=()
n=0
while IFS= read -r c; do
  [ -n "$c" ] || continue
  n=$((n+1))
  name=$(basename "$c" .sh); name=${name#*-}
  layer=$(dirname "$c"); layer=${layer##*/}
  [ "$layer" = "_common" ] && layer=通用
  [ "$layer" = "checks" ] && layer=本模块
  if out=$("$c" 2>&1) && ! interpreter_blew_up "$out"; then
    cl_ok "$n" "$name  [$layer]"
    pass=$((pass+1))
  else
    if interpreter_blew_up "$out"; then
      # **退 0 但解释器报了错 = 这条 check 自己炸了却报成功。**
      # 这一层独立于每条 check 自己的守卫存在：即使今天十八处 source 全加了
      # `|| exit 2`，明天新写的一条照样可能「函数没定义 → 数组空 → exit 0」。
      # 主脚本不能只信退出码 —— 退出码正是被吞掉的那个东西。
      first="check 自己炸了却退 0（解释器级错误）：$(grep -m1 -E ': line [0-9]+: ' <<<"$out")"
      rest="→ 这条检查这次**什么都没验**。退 0 和真验过在界面上一模一样，所以这里按失败处理。"
    else
      first=$(head -1 <<<"$out"); rest=$(sed -n '2,3p' <<<"$out" | tr '\n' ' ')
    fi
    cl_bad "$n" "$name  [$layer]" "$first" "$rest"
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
  cl_footer "着陆检查通过，可以提交" "" >/dev/null
  printf '│\n└─ 🟢 着陆检查通过（%d 项），可以提交\n\n' "$pass"
  log_event mission_complete
  refresh_console
  exit 0
fi

if [ -n "${AIMERGENT_MISSION_OVERRIDE:-}" ]; then
  printf '│\n└─ ⚠️  逃生口放行：%s\n     已记入 logs/ledger.jsonl（入仓的证据）——这次绕过是可追的。\n\n' \
    "$AIMERGENT_MISSION_OVERRIDE"
  # 2026-08-01：原来只写 diary，而 diary 被 .gitignore 吞 = 证据不入仓 = 等于没记。
  # 改走 emit_override（双写 ledger + diary），log_event 保留给 console 用。
  emit_override MISSION_OVERRIDE "$AIMERGENT_MISSION_OVERRIDE" 2>/dev/null || true
  log_event mission_override "\"$AIMERGENT_MISSION_OVERRIDE\""
  refresh_console
  exit 0
fi

printf '│\n└─ 🔴 着陆检查未通过 —— 还差 %d 件，拒绝提交：\n' "$fail" >&2
printf '     · %s\n' "${failed_names[@]}" >&2
printf '\n   确需绕过：AIMERGENT_MISSION_OVERRIDE="<理由>" git commit ...（放行但记账）\n' >&2
log_event mission_blocked
exit 1
