# 断言 72 · 门禁「修在根上但没部署到各副本」是否会被主动扫出来（铁律 23）
# 由 scripts/selftest.sh 按文件名顺序 source；共享 repo/S/P/F/N 与 pass/fail/na 计数。
# 本文件不可单独执行（没有那些变量），入口永远是 scripts/selftest.sh。
#
# 病根实证（2026-08-19）：框架根 mission_start.sh 修过「写区逗号假绿」（root commit
# 5d9e3d9，selftest 断言 #71），但那次只修在框架根——7 个模块仓的门禁脚本是**拷贝**，
# 6 个没跟着更新。checks/_common/13-gates-fresh.sh 只在**模块自己跑门禁时**生效，
# 而这 6 个模块长期靠 MISSION_OVERRIDE 跳过着陆检查，13 号判据形同虚设、没人看见
# 那条红。scripts/gates-drift-scan.sh 是从框架根主动扫全部 code/* 的补丁，
# 本断言验证它真的会拦（不是又一个「写了但没生效」的机制）。

printf '72. 门禁漂移主动扫描：框架根改了但模块副本没跟上时，会不会被扫出来点名\n'
if [ ! -f "$S/gates-drift-scan.sh" ]; then
  F "没有 scripts/gates-drift-scan.sh —— 铁律 23 要求的『扫同类』断言缺失"
else
  _sb72=$(mktemp -d); git -C "$_sb72" init -q
  mkdir -p "$_sb72/scripts/gates" "$_sb72/scripts/checks/_common" "$_sb72/scripts/lib" "$_sb72/scripts/hooks"
  cp "$S/gates-drift-scan.sh" "$_sb72/scripts/gates-drift-scan.sh"
  printf '#!/usr/bin/env bash\necho gate-v1\n' > "$_sb72/scripts/gates/run-gates.sh"
  printf '#!/usr/bin/env bash\necho lib-v1\n'  > "$_sb72/scripts/lib/paths.sh"
  printf '#!/usr/bin/env bash\necho mc-v1\n'   > "$_sb72/scripts/mission_complete.sh"
  printf '#!/usr/bin/env bash\necho ms-v1\n'   > "$_sb72/scripts/mission_start.sh"

  # 两个「已装门禁」的模块仓：modA（同步）、modB（同步）
  for _m in modA modB; do
    mkdir -p "$_sb72/code/$_m/scripts/gates" "$_sb72/code/$_m/scripts/lib"
    git -C "$_sb72/code/$_m" init -q
    cp "$_sb72/scripts/gates/run-gates.sh"    "$_sb72/code/$_m/scripts/gates/run-gates.sh"
    cp "$_sb72/scripts/lib/paths.sh"          "$_sb72/code/$_m/scripts/lib/paths.sh"
    cp "$_sb72/scripts/mission_start.sh"      "$_sb72/code/$_m/scripts/mission_start.sh"
    cp "$_sb72/scripts/mission_complete.sh"   "$_sb72/code/$_m/scripts/mission_complete.sh"
  done
  # 一个没装门禁的目录（没有 mission_start.sh）：不该被当成模块扫
  mkdir -p "$_sb72/code/notamodule"

  # ── 段①：基线全同步，期望绿 ──────────────────────────────
  _out72a=$(bash "$_sb72/scripts/gates-drift-scan.sh" "$_sb72/code" 2>&1); _rc72a=$?

  # ── 段②：篡改 modB 的 mission_start.sh 一个字节，期望红且点名 modB + 文件名 ──
  printf '#!/usr/bin/env bash\necho ms-v1  # tampered\n' > "$_sb72/code/modB/scripts/mission_start.sh"
  _out72b=$(bash "$_sb72/scripts/gates-drift-scan.sh" "$_sb72/code" 2>&1); _rc72b=$?

  # ── 段③：恢复，期望重新变绿（证明不是常红的坏断言）──────────
  cp "$_sb72/scripts/mission_start.sh" "$_sb72/code/modB/scripts/mission_start.sh"
  _out72c=$(bash "$_sb72/scripts/gates-drift-scan.sh" "$_sb72/code" 2>&1); _rc72c=$?

  rm -rf "$_sb72"

  _bad72=""
  [ "$_rc72a" -eq 0 ] || _bad72="$_bad72 段①基线未绿(rc=$_rc72a)"
  case "$_out72a" in *modA*|*modB*) _bad72="$_bad72 段①基线误报了本不该漂移的模块" ;; esac
  [ "$_rc72b" -ne 0 ] || _bad72="$_bad72 段②篡改后仍退0(没拦住)"
  case "$_out72b" in
    *modB*mission_start.sh*) ;;
    *) _bad72="$_bad72 段②没点名modB+mission_start.sh" ;;
  esac
  case "$_out72b" in *modA*) _bad72="$_bad72 段②误伤了没改动的modA" ;; esac
  [ "$_rc72c" -eq 0 ] || _bad72="$_bad72 段③恢复后未转绿(rc=$_rc72c)"

  if [ -n "$_bad72" ]; then
    F "gates-drift-scan.sh 红绿验证未通过：$_bad72"
  else
    P "基线绿、篡改单个模块单个文件后红且点名「模块+文件」、恢复后重新转绿 —— 三段都验过"
  fi
fi
