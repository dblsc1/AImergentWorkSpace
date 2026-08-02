# 断言 63–64 · 公共件缺失时的两层防线（S1，2026-08-02 P0）
# 由 scripts/selftest.sh 按文件名顺序 source；共享 repo/S/P/F/N 与 pass/fail/na 计数。
# 本文件不可单独执行（没有那些变量），入口永远是 scripts/selftest.sh。

# 63 ── 层①：公共件缺失时，**每一条**用它的 check 都必须响亮死
#      2026-08-02 P0 实测：删掉 scripts/lib/paths.sh，十五条 _common 里十二条
#      静默 exit 0，着陆检查单照打 ✅ —— 界面和真验过一模一样。
#      三段病根之一：check 只 `set -uo pipefail`（没有 -e），`. <公共件>` 失败不中止，
#      接着 staged_paths 未定义 → mapfile 读到空 → 数组空 → 走到 exit 0。
#
#      **这条断言刻意逐条跑、不抽样**：抽一条过了不能证明其余十七条也过，
#      而「只修撞见的那一个」正是铁律 23 点名的病（判例库：修类不修实例）。
printf '63. 公共件缺失时每一条 check 是否都响亮死（层①）\n'
if [ ! -d "$S/checks" ] || [ ! -f "$S/lib/paths.sh" ]; then
  N "本仓没有 checks/ 或 lib/paths.sh"
else
  _sb63=$(mktemp -d); git -C "$_sb63" init -q
  cp -r "$S" "$_sb63/scripts"
  rm -f "$_sb63/scripts/lib/paths.sh"          # ← 唯一的扰动
  mkdir -p "$_sb63/agents/x/docs/worklog"
  printf 'x\n' > "$_sb63/agents/x/docs/worklog/w.md"
  git -C "$_sb63" add -A >/dev/null 2>&1
  _silent63=""; _n63=0
  while IFS= read -r _c; do
    grep -q 'paths\.sh' "$_c" || continue     # 不用它的 check 不在本条断言范围内
    _n63=$((_n63+1))
    _out63=$( cd "$_sb63" && AIMERGENT_ROLE=x bash "$_c" 2>&1 )
    _rc63=$( cd "$_sb63" && AIMERGENT_ROLE=x bash "$_c" >/dev/null 2>&1; echo $? )
    # **光看退出码不够**：反向验证抓到的——单独撤掉 05-write-lease 的守卫，
    # 它照样非 0，因为沙箱里它本来就会因「角色 x 没有路签」而红。
    # 那是**红对了但理由是错的**：真出事时人会去申领路签，而实际病因是判据瞎了。
    # 所以这里要求看见守卫**自己那句话**（「拒绝以」是守卫独有的措辞，
    # bash 自己的 `No such file or directory` 不会带），才算这条守卫真的在。
    if [ "$_rc63" -eq 0 ]; then
      _silent63="$_silent63 $(basename "$_c")(静默退0)"
    else
      case "$_out63" in *拒绝以*) ;; *) _silent63="$_silent63 $(basename "$_c")(红了但不是守卫拦的)" ;; esac
    fi
  done < <(find "$_sb63/scripts/checks" -name '*.sh' -type f | LC_ALL=C sort)
  rm -rf "$_sb63"
  if [ "$_n63" -eq 0 ]; then
    F "一条用 paths.sh 的 check 都没找到 —— 这条断言在验一个不存在的机制"
  elif [ -n "$_silent63" ]; then
    F "删掉 lib/paths.sh 后仍静默退 0 的 check（$_n63 条里）：$_silent63"
  else
    P "$_n63 条用 lib/paths.sh 的 check 逐条验过：全部由守卫本身拦下（不是碰巧因别的理由红）"
  fi
fi

# 64 ── 层②：主脚本不许把「自己炸了却退 0」判成 pass
#      病根第三段，也是最要害的一段：`if out=$("$c" 2>&1); then cl_ok` ——
#      **成功分支把 $out 整个丢弃**，解释器的报错一个字都不显示。
#      即使今天十八处 source 全加了守卫，明天新写的一条照样可能
#      「函数没定义 → 数组空 → exit 0」，而主脚本仍会判它 pass。
#      **两层必须各自有效**，所以这里单独验主脚本，不依赖层①。
printf '64. 主脚本会不会吞掉「check 自己炸了却退 0」（层②）\n'
if [ ! -f "$S/mission_complete.sh" ]; then
  F "没有 mission_complete.sh"
else
  _sb64=$(mktemp -d); git -C "$_sb64" init -q
  git -C "$_sb64" config user.email t@e.c >/dev/null 2>&1
  cp -r "$S" "$_sb64/scripts"
  # 只留两条 check：一条正常绿，一条**退 0 但吐解释器级错误**。
  # 层①在这个沙箱里是完好的 —— 所以这条断言测的纯粹是主脚本的判断。
  find "$_sb64/scripts/checks" -name '*.sh' -type f -delete
  mkdir -p "$_sb64/scripts/checks/_common"
  printf '#!/usr/bin/env bash\n[ "${1:-}" = --describe ] && { echo "fine 正常绿"; exit 0; }\nexit 0\n' \
    > "$_sb64/scripts/checks/_common/01-fine.sh"
  printf '#!/usr/bin/env bash\n[ "${1:-}" = --describe ] && { echo "boom 退0但炸了"; exit 0; }\nprintf "%%s: line 7: staged_paths: command not found\\n" "boom.sh" >&2\nexit 0\n' \
    > "$_sb64/scripts/checks/_common/02-boom.sh"
  chmod +x "$_sb64/scripts/checks/_common"/*.sh
  mkdir -p "$_sb64/agents/x/docs/worklog"; printf 'x\n' > "$_sb64/agents/x/docs/worklog/w.md"
  git -C "$_sb64" add -A >/dev/null 2>&1
  _o64=$( cd "$_sb64" && AIMERGENT_ROLE=x bash scripts/mission_complete.sh 2>&1 )
  # 拆掉层② —— 把守卫函数改成恒假，证明这条断言不是恒绿的
  sed -i 's/^interpreter_blew_up() {$/interpreter_blew_up() { return 1; _unused() {/' \
    "$_sb64/scripts/mission_complete.sh" 2>/dev/null
  _o64b=$( cd "$_sb64" && AIMERGENT_ROLE=x bash scripts/mission_complete.sh 2>&1 )
  rm -rf "$_sb64"
  _v64=""
  case "$_o64" in *"❌"*boom*) ;; *) _v64="$_v64 [退0+解释器错误被判成了 pass]" ;; esac
  case "$_o64" in *"✅"*fine*) ;; *) _v64="$_v64 [正常的 check 被误伤成红]" ;; esac
  case "$_o64b" in *"❌"*boom*) _v64="$_v64 [拆掉守卫后仍然红——这条断言不是在验守卫]" ;; esac
  if [ -z "$_v64" ]; then
    P "退 0 但吐解释器级错误的 check 被判失败；正常 check 零误伤；拆掉守卫后确实变绿（证明验的是守卫本身）"
  else
    F "主脚本仍会吞掉自相矛盾的成功：$_v64"
  fi
fi

# 65 ── 结构断言：挡住**下一个**新写的脚本再引入同一形状
#      行为断言（63/64）只能验今天在仓里的这些；新写一条 check 时，
#      作者会照着旁边那条抄 —— 所以「旁边那条长什么样」必须是对的。
printf '65. scripts/ 里 source 必需公共件时是否一律带失败守卫\n'
_naked=""
while IFS= read -r _f; do
  case "$_f" in "$S"/lib/*) continue ;; esac
  while IFS= read -r _ln; do
    case "$_ln" in
      *emit.sh*) continue ;;        # emit.sh 是声明过的可选件（自带 `|| true`）
      *"||"*)    continue ;;        # 已有守卫
    esac
    _naked="$_naked $(basename "$_f")"
    # ⚠️ 原来这里还接了 `| grep '\.sh"'` —— 于是 `. "$_lease_lib"` 这种
    #    **变量形式的 source 全部溜过去**，断言自己开了个洞。反向验证逼出来的。
  done < <(grep -hE '^[[:space:]]*\.[[:space:]]+"' "$_f")
done < <(find "$S" -name '*.sh' -type f 2>/dev/null | LC_ALL=C sort)
if [ -n "$_naked" ]; then
  F "source 公共件却没有失败守卫（缺件时会静默继续，然后以「什么都没验」的姿态退 0）：$_naked"
else
  P "scripts/ 下所有必需公共件的 source 点都带行尾守卫；emit.sh 作为可选件显式豁免"
fi
