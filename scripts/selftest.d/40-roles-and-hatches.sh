# 断言 49–51 · 模型分档 / 角色 export / 逃生口记账
# 由 scripts/selftest.sh 按文件名顺序 source；共享 repo/S/P/F/N 与 pass/fail/na 计数。
# 本文件不可单独执行（没有那些变量），入口永远是 scripts/selftest.sh。

# 49 ── 模型分档规矩没有机械执行点（CFO 实证：两次把 programmer 跑上了 Opus）
printf '49. 模型分档是否有机械执行点\n'
if [ -x "$S/run_agent.sh" ] &&
   grep -q 'AIMERGENT_AGENT_MODEL' "$S/run_agent.sh" 2>/dev/null &&
   grep -qE -- '--model' "$S/run_agent.sh" 2>/dev/null &&
   grep -qE 'AIMERGENT_AGENT_MODEL:-sonnet' "$S/run_agent.sh" 2>/dev/null; then
  P "run_agent.sh 有 AIMERGENT_AGENT_MODEL 入口且默认中档（sonnet），不是继承"
else
  F "模型分档无执行点 —— 只能靠派活方每次记得（blockers/2026-07-30-model-tier-not-enforced.md 已实证两次跑上 Opus）"
fi

# 50 ── mission_complete.sh 推断出角色后不 export，05 写区路签核验静默放行
#      （障签 2026-07-31：只靠"源码里有 export 字样"骗不过铁律 23，必须真的拦一次越区写入）
#
#      2026-08-02 扩测（F3）：本条原来只喂 codeagent/programmer/docs/worklog/x.md ——
#      J3 五条 canonical 路径里**唯一还能命中旧正则的那条**。于是这条断言长期恒绿，
#      而现实中的 J3 布局全部推成 unknown，谁都没被它拦下来过。
#      现在**三种布局逐个端到端跑**：旧布局 / J3 模块级 / J3 项目级。
#      （纯推断函数的十条逐条喂在 #56；这里验的是「推断→export→05 真拦住」整条链。）
printf '50. 角色自动推断时写区路签是否依然生效（三种布局逐个端到端）\n'
_req50=("$S/mission_complete.sh" "$S/checks/_common/05-write-lease.sh" \
        "$S/lib/paths.sh" "$S/lib/checklist.sh")
_missing50=0
for _f in "${_req50[@]}"; do [ -f "$_f" ] || _missing50=1; done
if [ "$_missing50" -eq 1 ]; then
  N "本仓没有 mission_complete.sh / 05-write-lease.sh 全套，不适用"
else
  _sbx=$(mktemp -d)
  # _build_role_export_sandbox <mission_complete 路径> <留痕路径> <该留痕应推出的角色>
  _build_role_export_sandbox() {
    local _mc=$1 _trace=$2 _role=$3
    rm -rf "$_sbx"; mkdir -p "$_sbx"
    git -C "$_sbx" init -q
    git -C "$_sbx" config user.email t@example.com
    git -C "$_sbx" config user.name selftest
    mkdir -p "$_sbx/scripts/lib" "$_sbx/scripts/checks/_common"
    cp "$S/lib/emit.sh" "$_sbx/scripts/lib/" 2>/dev/null || true
    cp "$S/lib/checklist.sh" "$S/lib/paths.sh" "$_sbx/scripts/lib/"
    cp "$S/checks/_common/05-write-lease.sh" "$_sbx/scripts/checks/_common/"
    cp "$_mc" "$_sbx/scripts/mission_complete.sh"
    chmod +x "$_sbx/scripts/mission_complete.sh" "$_sbx/scripts/checks/_common/"*.sh
    # 该角色只申领了一块不相干的目录 —— 越权目标不在其中
    local _lease_dir; _lease_dir=$(git -C "$_sbx" rev-parse --path-format=absolute --git-common-dir)/aimergent-leases
    mkdir -p "$_lease_dir"; printf 'nothing-real/\n' > "$_lease_dir/$_role.lease"
    # 只靠"暂存路径"触发角色自动推断，**不手动传 AIMERGENT_ROLE**
    mkdir -p "$_sbx/$(dirname "$_trace")" "$_sbx/secrets"
    echo hi > "$_sbx/$_trace"
    echo leak > "$_sbx/secrets/out-of-lease.txt"      # 越出写区路签的文件
    git -C "$_sbx" add -A
  }
  _bad50=""
  for _c50 in "codeagent/programmer/docs/worklog/x.md|programmer|旧布局" `# ref-fixture` \
              "module_docs/worklog/x.md|arbiter|J3模块级" `# ref-fixture` \
              "agents/consulter/docs/worklog/x.md|consulter|J3项目级" `# ref-fixture`; do
    _t50=${_c50%%|*}; _r50=${_c50#*|}; _lab50=${_r50#*|}; _r50=${_r50%%|*}
    _build_role_export_sandbox "$S/mission_complete.sh" "$_t50" "$_r50"
    _rc=0
    _out50=$(cd "$_sbx" && env -u AIMERGENT_ROLE bash scripts/mission_complete.sh 2>&1) || _rc=$?
    if [ "$_rc" -eq 0 ] || ! grep -q '越出写区路签' <<<"$_out50"; then
      _bad50="$_bad50 [$_lab50 $_t50→$_r50 exit=$_rc]"
    fi
  done
  rm -rf "$_sbx"
  if [ -n "$_bad50" ]; then
    F "越区写入未被拦：$_bad50 —— 角色没推出来（F3）或推出来没 export 给子检查（障签 2026-07-31）"
  else
    P "旧布局 / J3 模块级 / J3 项目级三种留痕路径，都能自动推出角色并真的拦住越区写入"
  fi
fi

# 51 ── 逃生口是不是每一个都真的记账（A-1 事故：账本不入仓 = 逃生口全静默）
#      两段验：① 静态扫全类（防新增逃生口时漏接）② 真点火（防「源码里有那个词」骗过去）
printf '51. 逃生口是否全部接进问责账本\n'
if [ ! -f "$S/lib/emit.sh" ]; then
  N "本仓没有 lib/emit.sh，不适用"
else
  # ① 类扫描：凡出现逃生口 env 的脚本，必须也调 emit_override
  _hatch_bad=""
  while IFS= read -r _h; do
    [ -n "$_h" ] || continue
    while IFS= read -r _f; do
      [ -n "$_f" ] || continue
      case "$_f" in */lib/emit.sh|*/selftest.sh) continue ;; esac   # 定义处与本测试自身不算
      grep -q 'emit_override' "$_f" 2>/dev/null || _hatch_bad="$_hatch_bad $(basename "$_f"):$_h"
    done < <(grep -rlE "$_h" --include='*.sh' "$S" 2>/dev/null)
  done < <(grep -rhoE 'AIMERGENT_[A-Z_]*(OVERRIDE|SKIP[A-Z_]*|UNREVIEWED|ALLOW_[A-Z_]*)' \
             --include='*.sh' "$S" 2>/dev/null | sort -u)

  # ② 真点火：造一个隔离沙箱仓，实调 emit_override，断言 ledger 真落一行且不被 gitignore 吞
  _lsbx=$(mktemp -d)
  git -C "$_lsbx" init -q 2>/dev/null
  mkdir -p "$_lsbx/scripts/lib" "$_lsbx/logs"
  cp "$S/lib/emit.sh" "$_lsbx/scripts/lib/" 2>/dev/null
  _fire=$( cd "$_lsbx" && bash -c '
      . scripts/lib/emit.sh 2>/dev/null
      emit_override TESTHATCH "selftest 点火" 2>/dev/null
      [ -s logs/ledger.jsonl ] && grep -q TESTHATCH logs/ledger.jsonl && echo FIRED
  ' 2>/dev/null )
  # 顺带验真仓的落点没被 .gitignore 吞（吞了 = 写了也不入仓 = 白写）
  _swallowed=""
  git -C "$repo" check-ignore -q -- logs/ledger.jsonl 2>/dev/null && _swallowed=1
  rm -rf "$_lsbx"

  if [ -n "$_hatch_bad" ]; then
    F "有逃生口没接问责账本:$_hatch_bad —— 漏一个，那个逃生口就是静默的（铁律23 修到类）"
  elif [ "$_fire" != FIRED ]; then
    F "emit_override 没能真写出 ledger 条目 —— 函数在但不生效，比没有更糟"
  elif [ -n "$_swallowed" ]; then
    F "logs/ledger.jsonl 被 .gitignore 吞 —— 记了也不入仓，按铁律18 等于没记（正是 A-1 事故本体）"
  else
    P "四类逃生口全部接 emit_override；真点火写出 ledger 条目；落点未被 .gitignore 吞"
  fi
fi

