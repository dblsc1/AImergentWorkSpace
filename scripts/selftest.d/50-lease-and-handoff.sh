# 断言 52–55 · 模块 handoff / 路签 TTL / 拒发记账
# 由 scripts/selftest.sh 按文件名顺序 source；共享 repo/S/P/F/N 与 pass/fail/na 计数。
# 本文件不可单独执行（没有那些变量），入口永远是 scripts/selftest.sh。

# 52 ── 模块级 handoff（arbiter 侧）有没有机械检查
#      A-2 事故：规范写「handoff 每改必核」，而 checks/14 只扫 code/ 那份、
#      从不看 module_docs/。六个模块的 arbiter 级交接文档全是空模板、一次没红过。
printf '52. 模块级 handoff 是否有机械检查\n'
_c17="$S/checks/_common/17-module-handoff.sh"
if [ ! -f "$_c17" ]; then
  F "没有 checks/_common/17-module-handoff.sh —— module_docs/handoff.md 无人把关（A-2 事故本体）"
else
  _sbx=$(mktemp -d)
  mkdir -p "$_sbx/codeagent" "$_sbx/module_docs" "$_sbx/scripts/lib" "$_sbx/scripts/checks/_common"
  git -C "$_sbx" init -q
  cp "$S/lib/paths.sh" "$_sbx/scripts/lib/" 2>/dev/null
  cp "$_c17" "$_sbx/scripts/checks/_common/"
  chmod +x "$_sbx/scripts/checks/_common/17-module-handoff.sh"
  # 喂一份**未填实的模板**，必须变红
  cp "$repo/code/_template/module_docs/handoff.md" "$_sbx/module_docs/handoff.md" 2>/dev/null
  _rc_tpl=0; ( cd "$_sbx" && ./scripts/checks/_common/17-module-handoff.sh >/dev/null 2>&1 ) || _rc_tpl=$?
  # 换成填实的，必须变绿
  printf '# m · handoff\n\n## 一句话\n真内容\n\n## 怎么跑 / 怎么测\n真命令\n\n## 接口\n真接口\n\n## 避坑 / 冻结点 / 技术债\n真坑\n' \
    > "$_sbx/module_docs/handoff.md"
  _rc_real=0; ( cd "$_sbx" && ./scripts/checks/_common/17-module-handoff.sh >/dev/null 2>&1 ) || _rc_real=$?
  rm -rf "$_sbx"
  if [ "$_rc_tpl" -ne 0 ] && [ "$_rc_real" -eq 0 ]; then
    P "空模板必红（exit=$_rc_tpl）、填实必绿（exit=$_rc_real）—— 判据认内容不认行数"
  else
    F "17-module-handoff 判据失灵：空模板 exit=$_rc_tpl（应非0）、填实 exit=$_rc_real（应0）"
  fi
fi

# 53 ── 写区路签会不会过期（2026-08-02 事故：同一天三次卡死子代理）
#      派活方派完活走人，签永久有效，被卡方沉默重试。三次事后 worklog 都写
#      「以后记得还签」然后又犯 —— 靠记性无效，必须靠 TTL。
#      这里实弹打三发：① 过期签必须被回收 ② **无 meta 的签必须不被回收**
#      （未知 ≠ 过期；静默回收判不出年龄的签 = 把互斥悄悄关掉）③ 回收必须打印回收了谁。
printf '53. 写区路签是否会过期回收\n'
_lease_lib="$S/lib/lease.sh"
if [ ! -f "$_lease_lib" ]; then
  F "没有 scripts/lib/lease.sh —— 路签无 TTL，忘了还签就永久卡住（2026-08-02 事故本体）"
else
  _sbx=$(mktemp -d); git -C "$_sbx" init -q
  _out=$(
    cd "$_sbx" || exit 9
    # shellcheck disable=SC1090
    . "$_lease_lib" || exit 9
    _d=$(lease_dir_path); mkdir -p "$_d"
    # ① 过期签：granted_at 拨回 3 小时前，TTL 2 小时
    echo 'some/area/' > "$_d/walked_away.lease"
    printf 'granted_at=%s\nttl=7200\ntask=某任务单\n' "$(( $(date -u +%s) - 10800 ))" > "$_d/walked_away.meta"
    # ② 无 meta 的签（上一版留下的）：必须**留着**
    echo 'other/area/' > "$_d/no_meta.lease"
    # ③ 未过期签：必须留着
    echo 'fresh/area/' > "$_d/fresh.lease"
    printf 'granted_at=%s\nttl=7200\ntask=新任务\n' "$(date -u +%s)" > "$_d/fresh.meta"
    lease_reap 2>&1 >/dev/null
    printf '|剩余:'
    for f in "$_d"/*.lease; do printf '%s ' "$(basename "$f" .lease)"; done
  )
  rm -rf "$_sbx"
  _left=${_out#*|剩余:}
  _msg=${_out%%|剩余:*}
  _ok=1
  case "$_left" in *walked_away*) _ok=0; _why="过期签没被回收" ;; esac
  case "$_left" in *no_meta*) ;; *) _ok=0; _why="无 meta 的签被回收了——未知不等于过期，静默回收会让双写发生" ;; esac
  case "$_left" in *fresh*) ;; *) _ok=0; _why="未过期的签被误回收" ;; esac
  case "$_msg" in *walked_away*) ;; *) _ok=0; _why="回收了但没打印回收了谁——静默回收比忘了还签更危险" ;; esac
  if [ "$_ok" -ne 1 ]; then
    F "路签 TTL 判据失灵：$_why（剩余：$_left）"
  else
    # ④ **库写了还得真接上**。原先这里写的是 `grep -q lease_reap mission_start.sh`——
    #    那只证明字符串在文件里，注释掉、写成 `: lease_reap` 都照样绿。
    #    本项目本轮的主线教训就是「判据只看形状不看后果」，这里必须实弹：
    #    真跑一次 mission_start.sh，看过期签是不是真的没了。
    #    （reap 在脚本最前面，`--list` 这条最轻的路径也会触发，不需要任务单/角色卡。）
    _sbx2=$(mktemp -d); git -C "$_sbx2" init -q
    mkdir -p "$_sbx2/scripts"; cp -r "$S/lib" "$S/mission_start.sh" "$_sbx2/scripts/" 2>/dev/null
    _gd="$_sbx2/.git/aimergent-leases"; mkdir -p "$_gd"
    echo 'some/area/' > "$_gd/walked_away.lease"
    printf 'granted_at=%s\nttl=7200\ntask=某任务单\n' "$(( $(date -u +%s) - 10800 ))" > "$_gd/walked_away.meta"
    ( cd "$_sbx2" && bash scripts/mission_start.sh --list >/dev/null 2>&1 )
    _still=0; [ -f "$_gd/walked_away.lease" ] && _still=1
    rm -rf "$_sbx2"
    if [ "$_still" -eq 1 ]; then
      F "lease.sh 的 TTL 判据本身没问题，但 mission_start.sh 跑完过期签还在 —— 库写了没接上，等于没有"
    else
      P "过期签被回收且打印了持有者；无 meta 与未过期的签都保留；mission_start 实跑确认真接上了"
    fi
  fi
fi

# ── 路签沙箱：54/55/57 共用 ────────────────────────────────────────
# 数事件条数。**不要写 `grep -c … || echo 0`** —— grep 无命中时自己已经印了 0，
# 再 echo 一个 0 就变成两行 "0\n0"，喂给 [ ] 是语法错（本轮实测撞到）。
_cnt() { local n; n=$(grep -c -- "$1" "$2" 2>/dev/null) || n=0; printf '%s' "${n:-0}"; }

# 造一个只在「写区重叠」这一项上会失败的 mission_start 沙箱，其余七项全过。
# 这样断言变红时，红的原因唯一 —— 不是被别的检查项顺带拖红的。
# 用法：_mk_lease_sandbox <沙箱目录> <持有者> <持有区>
_mk_lease_sandbox() {
  local sbx=$1 holder=$2 held=$3
  rm -rf "$sbx"; mkdir -p "$sbx/scripts/lib" "$sbx/agents/consulter" "$sbx/docs"
  git -C "$sbx" init -q
  git -C "$sbx" symbolic-ref HEAD refs/heads/feat/sandbox      # 6 分支纪律：不在 main 上
  cp "$S"/lib/*.sh "$sbx/scripts/lib/" 2>/dev/null
  cp "$S/mission_start.sh" "$sbx/scripts/"
  printf '#!/usr/bin/env bash\nexit 0\n' > "$sbx/scripts/dispatch.sh"   # 发签成功后被 exec，桩掉
  chmod +x "$sbx/scripts/mission_start.sh" "$sbx/scripts/dispatch.sh"
  printf '# consulter\n可写：docs/\n' > "$sbx/agents/consulter/AGENTS.md"   # 4 角色卡，无占位符
  local h; h=$(git -C "$sbx" rev-parse --path-format=absolute --git-path hooks)
  mkdir -p "$h"; local g
  for g in pre-commit pre-push commit-msg; do printf '#!/bin/sh\nexit 0\n' > "$h/$g"; chmod +x "$h/$g"; done
  printf '## 目标\nx\n## 验收标准\nx\n## 可触碰目录\nx\n## 自检门\nx\n' > "$sbx/task.md"   # 1 四小节
  # 真做一次提交：没有 commit 的仓里「写区干净」是白捡的（未跟踪文件不算脏），
  # 那样 55 项的判据 A 就成了恒真 —— 必须让干净/脏两种状态都是真实可造的。
  git -C "$sbx" config user.email t@example.com
  git -C "$sbx" config user.name selftest
  git -C "$sbx" add -A >/dev/null 2>&1
  git -C "$sbx" commit -q --no-verify -m 'selftest sandbox base' >/dev/null 2>&1
  local d="$sbx/.git/aimergent-leases"; mkdir -p "$d"
  printf '%s\n' "$held" > "$d/$holder.lease"
  printf 'granted_at=%s\nttl=7200\ntask=持有者的任务单\n' "$(date -u +%s)" > "$d/$holder.meta"
}

# 54 ── 路签拒发不记账（F4，2026-08-02 consulter 报、CFO 机械复核属实）
#      账上 18 条发签、0 条拒发 —— 于是「今天卡了三次」查无实据，
#      任何优化路签的方案都没有基线可比。两侧都验：拒发必记，正常发签必不误记。
printf '54. 路签拒发是否记账\n'
if [ ! -f "$S/mission_start.sh" ]; then
  N "本仓没有 mission_start.sh，不适用"
else
  _sbx54=$(mktemp -d)
  _mk_lease_sandbox "$_sbx54" arbiter 'scripts/'
  _rc54=0
  ( cd "$_sbx54" && bash scripts/mission_start.sh consulter task.md scripts/lib/ ) \
    >"$_sbx54/out.txt" 2>&1 || _rc54=$?
  _denied=$(_cnt '"event":"lease_denied"' "$_sbx54/logs/diary.jsonl")
  # 另一侧：不重叠的申领必须正常发签，且**不能**误记一条拒发
  rm -f "$_sbx54/logs/diary.jsonl"
  _rc54b=0
  ( cd "$_sbx54" && bash scripts/mission_start.sh consulter task.md docs/ ) >/dev/null 2>&1 || _rc54b=$?
  _denied_ok=$(_cnt '"event":"lease_denied"' "$_sbx54/logs/diary.jsonl")
  _granted=$(_cnt '"event":"lease_grant"' "$_sbx54/logs/diary.jsonl")
  rm -rf "$_sbx54"
  if [ "$_rc54" -eq 0 ]; then
    F "写区重叠竟然发签成功了（rc=0）—— 沙箱没造对或重叠判据失效"
  elif [ "$_denied" -lt 1 ]; then
    F "拒发没有落 lease_denied 事件 —— 拒发次数不可数，路签优化没有基线可比（F4）"
  elif [ "$_denied_ok" -ne 0 ]; then
    F "不重叠的正常发签也记了 lease_denied（$_denied_ok 条）—— 恒真事件等于没有事件"
  elif [ "$_granted" -lt 1 ] || [ "$_rc54b" -ne 0 ]; then
    F "不重叠的申领没能正常发签（rc=$_rc54b, lease_grant=$_granted）"
  else
    P "拒发落 lease_denied（rc=$_rc54）、正常发签只落 lease_grant 不误记拒发"
  fi
fi

# 55 ── 拒发时给不给「能不能强收」的判据（层②，CFO 2026-08-02 裁决的落点）
#      三件事一起验，缺一件这条机制就退化：
#      ① 干净时要说「可强收」并给可粘贴命令 ② 脏时要说「不建议」并指出哪条不满足
#      ③ **两种情况下签都必须还在** —— 自动强收会让双写安静地发生（lib/lease.sh 红线）
printf '55. 路签拒发是否给出强收判据（且绝不自动强收）\n'
if [ ! -f "$S/mission_start.sh" ] || [ ! -f "$S/lib/lease.sh" ]; then
  N "本仓没有 mission_start.sh / lib/lease.sh，不适用"
else
  _sbx55=$(mktemp -d)
  # ① 持有者写区干净 → 应判「可强收」
  _mk_lease_sandbox "$_sbx55" arbiter 'scripts/'
  ( cd "$_sbx55" && bash scripts/mission_start.sh consulter task.md scripts/lib/ ) \
    >"$_sbx55/clean.txt" 2>&1
  _left_clean=1; [ -f "$_sbx55/.git/aimergent-leases/arbiter.lease" ] || _left_clean=0
  _c=$(cat "$_sbx55/clean.txt")            # 先取走：下一次造沙箱会 rm -rf 掉它
  # ② 持有者写区有未提交改动 → 应判「不建议强收」
  _mk_lease_sandbox "$_sbx55" arbiter 'scripts/'
  printf '\n# 持有者还在改\n' >> "$_sbx55/scripts/dispatch.sh"
  ( cd "$_sbx55" && bash scripts/mission_start.sh consulter task.md scripts/lib/ ) \
    >"$_sbx55/dirty.txt" 2>&1
  _left_dirty=1; [ -f "$_sbx55/.git/aimergent-leases/arbiter.lease" ] || _left_dirty=0
  _d=$(cat "$_sbx55/dirty.txt")
  rm -rf "$_sbx55"
  if ! grep -q '强收判据' <<<"$_c"; then
    F "拒发只说「重叠」，不给强收判据 —— 被卡方仍然无从判断该等还是该收（层②未落地）"
  elif ! grep -q '结论：可强收' <<<"$_c" || ! grep -q -- '--release arbiter' <<<"$_c"; then
    F "持有者写区干净时没判「可强收」或没给可粘贴命令（判据摆了却要人自己推）"
  elif ! grep -q '结论：不建议强收' <<<"$_d" || ! grep -q '未提交改动' <<<"$_d"; then
    F "持有者写区有未提交改动时仍建议强收 —— 判据恒真，等于没有判据"
  elif [ "$_left_clean" -ne 1 ] || [ "$_left_dirty" -ne 1 ]; then
    F "拒发时把别人的签自动收了（clean=$_left_clean dirty=$_left_dirty）—— 静默回收会让双写安静地发生"
  else
    P "干净判可强收+给可粘贴命令、脏判不建议并指出原因、两种情况下签都还在（没自动强收）"
  fi
fi


# 68 ── 写区粒度棘轮跨不跨得了轮次（F2 的原始场景就是跨轮次的）
#      原判据拿**当前持有的签文件**当基线，`[ -f … ] || return 1`——
#      **签一还基线就没了**，而还签是 mission_complete 的常规要求。
#      于是它对自己要防的那个形状全瞎：CFO 的签五天 4 片涨到 8 片，
#      **每一次膨胀都在不同轮次**。2026-08-03 实测 consulter 亲身撞上：
#      上一轮 `scripts/checks/ … agents/consulter/` → 本轮 `agents/ scripts/ …`，
#      第 9 项照打 ✅「写区没有比上一次放宽」。
#
#      ⚠️ fixture 刻意用 **cfo** 这个角色：这条判据管的是派活方自己，
#      **判据不许只对别人生效**（棘轮的历史数据全是 cfo 的签）。
printf '68. 写区放宽棘轮能不能跨还签比对（fixture 用 cfo 自己）\n'
if [ ! -f "$S/mission_start.sh" ] || [ ! -f "$S/lib/lease.sh" ]; then
  N "本仓没有 mission_start.sh 或 lib/lease.sh，不适用"
else
  _sb68=$(mktemp -d); git -C "$_sb68" init -q -b feat/probe
  git -C "$_sb68" config user.email t@e.c >/dev/null 2>&1
  cp -r "$S" "$_sb68/scripts"
  mkdir -p "$_sb68/agents/cfo/docs/worklog" "$_sb68/logs"
  # 角色卡（第 4 项要它存在且没有未替换占位符）
  printf '# cfo\n写边界：agents/cfo/\n' > "$_sb68/agents/cfo/AGENTS.md"
  # 任务单四小节（第 1 项）
  _tc68="$_sb68/agents/cfo/docs/worklog/2026-08-03-任务单-探针.md"   # ref-fixture
  printf '# 探针任务单\n## 目标\n验棘轮\n## 验收标准\n第9项能跨还签\n## 可触碰目录\nagents/cfo/\n## 自检门\nselftest\n' > "$_tc68"
  # 三个 hook（第 5 项）——只要可执行即可，内容不重要
  _hd68=$(git -C "$_sb68" rev-parse --path-format=absolute --git-path hooks)
  mkdir -p "$_hd68"; for _h in pre-push commit-msg pre-commit; do
    printf '#!/bin/sh\nexit 0\n' > "$_hd68/$_h"; chmod +x "$_hd68/$_h"; done
  git -C "$_sb68" add -A >/dev/null 2>&1

  _ms68() { ( cd "$_sb68" && bash scripts/mission_start.sh "$@" ) 2>&1; }
  _item9() { sed -n 's/^│ *\(⏭\|✅\|❌\) *9 *//p' <<<"$1"; }

  _o68_first=$(_ms68 cfo "${_tc68#"$_sb68"/}" agents/cfo/)      # ①首次发签：本机零签史
  _ms68 --release cfo >/dev/null                                 # ②还签（常规要求）
  _o68_wide=$(_ms68 cfo "${_tc68#"$_sb68"/}" agents/)            # ③跨轮次放宽

  # 反向：把基线改回「只认当前签文件」，同一串操作必须变成 ✅（证明验的是这次修复）
  _ms68 --release cfo >/dev/null
  python3 - "$_sb68/scripts/lib/lease.sh" <<'PY' 2>/dev/null
import sys
p=sys.argv[1]; s=open(p,encoding="utf-8").read()
a='''  if [ -f "$d/$role.lease" ]; then
    mapfile -t old < "$d/$role.lease"
  else
    mapfile -t old < <(lease_last_grant "$role")   # 跨轮次基线
  fi'''
b='''  [ -f "$d/$role.lease" ] || return 1
  mapfile -t old < "$d/$role.lease"'''
open(p,"w",encoding="utf-8").write(s.replace(a,b,1))
PY
  _ms68 cfo "${_tc68#"$_sb68"/}" agents/cfo/ >/dev/null
  _ms68 --release cfo >/dev/null
  _o68_old=$(_ms68 cfo "${_tc68#"$_sb68"/}" agents/)             # 旧基线：应恒 ✅
  rm -rf "$_sb68"

  _v68=""
  # ① 零签史时不许打 ✅ ——「没放宽」和「没有基线」不是一回事
  case "$(_item9 "$_o68_first")" in *无基线可比*) ;; *) _v68="$_v68 [零签史却给了结论]" ;; esac
  # ② 跨还签必须报出放宽，且点名到具体的前缀
  case "$_o68_wide" in *"写区比上一次放宽"*) ;; *) _v68="$_v68 [跨还签的放宽没报出来]" ;; esac
  case "$_o68_wide" in *"放宽：agents/cfo/ → agents/"*) ;; *) _v68="$_v68 [没点名是哪一片放宽]" ;; esac
  # ③ 必须把签史摆出来（光说「有点宽」没有信息量，是立本条时的原话）
  case "$_o68_wide" in *"签史"*) ;; *) _v68="$_v68 [报了放宽却不列签史]" ;; esac
  # ④ 反向：旧基线下同一串操作恒 ✅ —— 证明这条断言验的是修复本身，不是旁因
  case "$(_item9 "$_o68_old")" in *"没有比上一次放宽"*) ;; *) _v68="$_v68 [旧基线下也红：本断言不是在验这次修复]" ;; esac
  if [ -z "$_v68" ]; then
    P "cfo 发窄签→还签→发宽签：跨轮次报出「放宽：agents/cfo/ → agents/」并列签史；零签史时如实说无基线；换回旧基线同一串操作恒绿"
  else
    F "棘轮仍然失忆：$_v68"
  fi
fi
