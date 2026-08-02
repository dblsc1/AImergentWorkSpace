# ── 逃生口记账：记不上账就不许放行 ────────────────────────────────
# 分片，由 scripts/selftest.sh source；共享 repo/S/P/F/N 与 pass/fail/na。
#
# 立论（CFO 2026-08-03 的问题 + 实测答案）：
#   问：缺 lib/emit.sh 时逃生口记账会不会静默停掉？
#   答：会，而且比「静默」更糟。删掉 emit.sh 跑 mission_complete + MISSION_OVERRIDE：
#       退出码 0、ledger 一行没长，界面照样打印「已记入 logs/ledger.jsonl（入仓的证据）」。
#   成因三段，缺一不可：
#     ① `. lib/emit.sh 2>/dev/null || true` —— 缺件不中止
#     ② `emit_override … 2>/dev/null || true` —— 记账失败被吞
#     ③ 那句「已记入」是**无条件先打印的**，emit_override 在它之后才调
#   checks/16 的注释早写死了「账本不入仓 = 所有逃生口实际上是静默的」；
#   「记账机制自己可以静默消失」是同一个洞的另一半。

# 66 ── 行为断言：缺件 / 落点被吞时，逃生口必须**拒绝放行**
printf '66. 记不上账时逃生口会不会照样放行\n'
if [ ! -f "$S/mission_complete.sh" ] || [ ! -f "$S/lib/emit.sh" ]; then
  N "本仓没有 mission_complete.sh 或 lib/emit.sh，不适用"
else
  _sb66=$(mktemp -d); git -C "$_sb66" init -q
  git -C "$_sb66" config user.email t@e.c >/dev/null 2>&1
  cp -r "$S" "$_sb66/scripts"
  # 只留一条恒红的 check —— 目的是让流程走到逃生口分支，不是测 check 本身。
  find "$_sb66/scripts/checks" -name '*.sh' -type f -delete
  mkdir -p "$_sb66/scripts/checks/_common"
  printf '#!/usr/bin/env bash\n[ "${1:-}" = --describe ] && { echo "always-red 恒红"; exit 0; }\nexit 1\n' \
    > "$_sb66/scripts/checks/_common/01-always-red.sh"
  chmod +x "$_sb66/scripts/checks/_common"/*.sh
  mkdir -p "$_sb66/agents/x/docs/worklog" "$_sb66/logs"
  printf 'x\n' > "$_sb66/agents/x/docs/worklog/w.md"
  git -C "$_sb66" add -A >/dev/null 2>&1

  _led66="$_sb66/logs/ledger.jsonl"
  _lines66() { [ -f "$_led66" ] && wc -l < "$_led66" || echo 0; }
  # ⚠️ 退出码必须直取，不许走管道（判例库：验证者自己的 $? 也会撒谎）。
  _fire66() { ( cd "$_sb66" && AIMERGENT_ROLE=x AIMERGENT_MISSION_OVERRIDE="selftest 点火" \
                  bash scripts/mission_complete.sh ) > "$_sb66/out" 2>&1; printf '%s' "$?"; }

  _n0=$(_lines66); _rc_ok=$(_fire66);      _n1=$(_lines66); _out_ok=$(cat "$_sb66/out")
  # ── 拆掉记账能力之一：公共件没了
  mv "$_sb66/scripts/lib/emit.sh" "$_sb66/emit.sh.bak"
  _rc_nolib=$(_fire66);                    _n2=$(_lines66); _out_nolib=$(cat "$_sb66/out")
  mv "$_sb66/emit.sh.bak" "$_sb66/scripts/lib/emit.sh"
  # ── 拆掉记账能力之二：件在，但落点被 .gitignore 吞（A-1 事故本体）
  printf 'logs/\n' > "$_sb66/.gitignore"
  _rc_swallowed=$(_fire66);                _n3=$(_lines66); _out_sw=$(cat "$_sb66/out")
  rm -rf "$_sb66"

  _v66=""
  # 基线：记得上账时必须照常放行，且 ledger 真长一行（防这条断言靠「哪都红」蒙混）
  [ "$_rc_ok" = 0 ]              || _v66="$_v66 [记得上账却不放行 rc=$_rc_ok]"
  [ "$_n1" -gt "$_n0" ]          || _v66="$_v66 [放行了但 ledger 没长($_n0→$_n1)]"
  case "$_out_ok" in *已记入*) ;; *) _v66="$_v66 [真记上了却不告诉人]" ;; esac
  # 缺公共件：必须非 0，且**一个字都不许说「已记入」**
  [ "$_rc_nolib" != 0 ]          || _v66="$_v66 [缺 emit.sh 仍放行 rc=0]"
  [ "$_n2" = "$_n1" ]            || _v66="$_v66 [缺 emit.sh 却写出了账($_n1→$_n2)]"
  case "$_out_nolib" in *已记入*) _v66="$_v66 [缺 emit.sh 还打印「已记入」——撒谎式成功]" ;; esac
  # 落点被吞：件在、函数在，但写了不入仓 —— 这一发单独证明验的是「账真的落了」，
  # 不是「emit.sh 这个文件在不在」。两发同绿才叫两条路都堵上。
  [ "$_rc_swallowed" != 0 ]      || _v66="$_v66 [落点被 gitignore 吞仍放行 rc=0]"
  case "$_out_sw" in *已记入*) _v66="$_v66 [落点被吞还打印「已记入」]" ;; esac
  if [ -z "$_v66" ]; then
    P "记得上账→放行且 ledger 真长；缺 emit.sh→拒绝放行且不打印「已记入」；落点被 gitignore 吞→同样拒绝（rc: $_rc_ok / $_rc_nolib / $_rc_swallowed）"
  else
    F "逃生口记账不是事实题：$_v66"
  fi
fi

# 67 ── 结构断言（层③）：挡住**下一个**新写的逃生口
#      51 号按**名字**认逃生口（AIMERGENT_*_OVERRIDE|SKIP*|UNREVIEWED|ALLOW_*|OK），
#      而名字是白名单 —— INSTALL_STALE_OK 就是靠 `_OK` 结尾在盲区里躺了整轮
#      （它只写 diary，而 diary 按设计不入仓 = 按铁律 18 等于没记账）。
#      本条**不看名字**，只看两件可机械核验的事实：
#        · 会调 emit_override 的脚本，其 emit.sh 必须是**必需件**（守卫要能中止）
#        · 嘴上说「已记账」的脚本，必须真的调过 emit_override
printf '67. 会放行逃生口的脚本，记账能力是不是必需件（不按名字认）\n'
_v67=""
_hatch_files=""
while IFS= read -r _f; do
  case "$_f" in "$S"/lib/*|"$S"/selftest.d/*) continue ;; esac   # 定义处与本断言自身不算
  _says=0; _calls=0
  grep -q 'emit_override' "$_f" 2>/dev/null && _calls=1
  grep -qE '已记账|已记入' "$_f" 2>/dev/null && _says=1
  [ "$_calls" = 1 ] || [ "$_says" = 1 ] || continue
  _hatch_files="$_hatch_files $(basename "$_f")"
  # ① 嘴上说记了，就得真调
  [ "$_says" = 1 ] && [ "$_calls" = 0 ] && \
    _v67="$_v67 [$(basename "$_f") 打印「已记账」却从不调 emit_override]"
  # ② 记账能力必须是必需件：source 行要带**中止型**守卫（exit/die），`|| true` 不算
  _src=$(grep -hE '^[[:space:]]*\.[[:space:]].*emit\.sh' "$_f" 2>/dev/null || true)
  if [ -z "$_src" ]; then
    _v67="$_v67 [$(basename "$_f") 调 emit_override 却没 source emit.sh]"
  else
    case "$_src" in
      *"|| true"*) _v67="$_v67 [$(basename "$_f") 的 emit.sh 仍按可选件 || true 载入]" ;;
      *exit*|*die*) ;;
      *) _v67="$_v67 [$(basename "$_f") 的 emit.sh 守卫不会中止]" ;;
    esac
  fi
done < <(find "$S" -name '*.sh' -type f 2>/dev/null | LC_ALL=C sort)
if [ -z "$_hatch_files" ]; then
  N "本仓没有会放行逃生口的脚本"
elif [ -z "$_v67" ]; then
  P "会记账/声称记账的脚本逐个核过（$(printf '%s' "$_hatch_files" | wc -w) 个）：都真调 emit_override，且 emit.sh 按必需件载入"
else
  F "记账能力被当成可选件：$_v67"
fi
