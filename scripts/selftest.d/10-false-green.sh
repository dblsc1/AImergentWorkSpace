# 断言 1–20 · 假绿灯 / 留痕落点 / 硬闸门是否存在
# 断言 70–72（2026-08-11 补，P1 审计）· 障签解除标志解析 + quotepath 同类 footgun
# 由 scripts/selftest.sh 按文件名顺序 source；共享 repo/S/P/F/N 与 pass/fail/na 计数。
# 本文件不可单独执行（没有那些变量），入口永远是 scripts/selftest.sh。

# 1 ── 空区间假绿灯：直接在 main 上工作时 diff 为空，一份都不验却退 0
printf '1. 空区间假绿灯\n'
if [ ! -x "$S/gates/check-report-schema.sh" ]; then
  F "无 check-report-schema.sh（留痕铁律没有机械执行者）"
elif head=$(git -C "$repo" rev-parse HEAD 2>/dev/null) &&
     (cd "$repo" && AIMERGENT_REPORT_BASE="$head" "$S/gates/check-report-schema.sh" >/dev/null 2>&1); then
  F "base==head 时仍退 0 —— 一份报告都没验却报绿"
else
  P "base==head 时响亮失败，不退化为绿灯"
fi

# 2 ── 直提 main 无提示：10 个 commit 全直提 main，全程零提示
printf '2. 直提 main 是否会被察觉\n'
if [ -n "$(find "$S/checks" -name '40-branch-discipline.sh' 2>/dev/null)" ] ||
   grep -qE 'abbrev-ref' "$S/gates/run-gates.sh" 2>/dev/null; then
  P "存在分支纪律检查"
else
  F "无任何机制检测'正在往 main 上直接提交'（铁律 1 无执行者）"
fi

# 3 ── 报告落点在仓外：9 条 sub_reports 全指向 /tmp，会话结束即蒸发
printf '3. sub_reports 落点是否被校验\n'
if [ -n "$(find "$S/checks" -name '30-subreport-in-repo.sh' 2>/dev/null)" ]; then
  P "有独立检查项校验 sub_reports 落点在仓内"
else
  F "无机制校验 sub_reports[].path 是否在仓内（仍靠自觉）"
fi

# 4 ── 新模块首跑必红：脚手不做初始 commit
printf '4. 脚手是否交付可直接跑门禁的状态\n'
if [ ! -f "$S/new_module.sh" ]; then
  N "本仓不是框架仓（无 new_module.sh）"
elif grep -qE 'commit -q -m' "$S/new_module.sh"; then
  P "new_module.sh 做初始 commit"
else
  F "new_module.sh 只 init 不 commit —— 新模块首跑门禁必红"
fi

# 5 ── 自核脚本成死代码：入仓不等于会跑
printf '5. reviewcode 脚本是否真的会被执行\n'
if [ -x "$repo/review/reviewcode/run_all.sh" ] ||
   [ -x "$repo/code/_template/review/reviewcode/run_all.sh" ] ||
   [ -x "$repo/module_template/review/reviewcode/run_all.sh" ]; then
  P "自核脚本有 run_all.sh 挂载点"
elif [ -n "$(git -C "$repo" ls-files 'review/reviewcode/*.sh' 2>/dev/null)" ]; then
  F "自核脚本已入仓但无 run_all.sh 挂载 —— 死代码，比没有更糟"
else
  F "模板未内置 run_all.sh 骨架 —— 每个模块会各自重新发明或干脆漏掉"
fi

# 6 ── hook 未装无提醒：.git/hooks 不随 clone 走
printf '6. hook 未安装是否会被发现\n'
if grep -qE 'hook 安装状态|missing_hooks' "$S/gates/run-gates.sh" 2>/dev/null; then
  P "门禁会检测 hook 安装状态"
else
  F "hook 未安装无任何提醒（换台机器 clone 下来就是零保护）"
fi

# 7 ── 完工无硬闸门：靠自觉的规矩，执行率取决于 agent 记不记得
printf '7. 完工检测是否为硬闸门\n'
if [ -x "$S/hooks/pre-commit" ] && grep -q mission_complete "$S/hooks/pre-commit" 2>/dev/null; then
  P "pre-commit 调用 mission_complete，检测不过拒绝提交"
else
  F "无提交层闸门 —— 完工检测可被直接跳过"
fi

# 8 ── 闭卷考试：agent 不知道自己会被怎么判
printf '8. 完工判据是否开卷（开工时可读）\n'
if [ -x "$S/mission_complete.sh" ] && "$S/mission_complete.sh" --list >/dev/null 2>&1; then
  P "mission_complete.sh --list 可输出全部判据"
else
  F "判据不可枚举 —— agent 只能猜自己会被怎么判"
fi

# 9 ── 检查项不可增删改查：改一条要动主脚本
printf '9. 检查项是否可增删改查\n'
n=$(find "$S/checks" -name '*.sh' 2>/dev/null | wc -l)
if [ "$n" -ge 1 ] && [ -d "$S/checks/_common" ] &&
   grep -q 'codeagent/\$role/checks' "$S/mission_complete.sh" 2>/dev/null; then
  P "$n 条检查独立成文件，且按 _common/<角色>/模块 三层级联"
elif [ "$n" -ge 1 ]; then
  F "检查项虽独立成文件，但不分角色 —— 所有角色被同一套判据卡住"
else
  F "检查项硬编码在主脚本里，改一条要动主脚本"
fi

# 10 ── 角色卡不硬性读取：忘写「先读角色卡」= 角色卡等于不存在
printf '10. 角色卡是否强制加载\n'
if [ -x "$S/dispatch.sh" ] && [ -x "$S/new_agent.sh" ] &&
   grep -q 'claude/agents' "$S/new_agent.sh" 2>/dev/null; then
  P "dispatch.sh 生成固定首行 + new_agent.sh 生成 .claude/agents/（含出生考卷）"
elif [ -x "$S/dispatch.sh" ]; then
  F "有 dispatch.sh 但不生成 .claude/agents/ —— 仍只靠提示词首行"
else
  F "派单提示词全靠手写，忘写=角色卡不生效且无人察觉"
fi

# 11 ── 文档路径腐烂：路径表点名的目录根本不存在
printf '11. 文档引用的路径是否被核验\n'
if [ -n "$(find "$S/checks" -name '60-doc-paths-exist.sh' 2>/dev/null)" ]; then
  P "有检查项核验文档中引用的仓内路径真实存在"
else
  F "文档可引用不存在的路径 —— 路径表点名而目录不存在正是无留痕的物理原因"
fi

# 12 ── 逃生口静默：硬闸门被绕过而无人知道
printf '12. 硬闸门的逃生口是否强制记账\n'
if [ -x "$S/mission_complete.sh" ] &&
   grep -q 'MISSION_OVERRIDE' "$S/mission_complete.sh" 2>/dev/null &&
   grep -q 'mission_override' "$S/mission_complete.sh" 2>/dev/null; then
  P "override 放行但强制写 diary，绕过在台账上可见"
else
  F "逃生口缺失或静默 —— 要么卡死无解，要么绕过无痕"
fi

# 13 ── 「续用 > 重开」只是句口号：执行率取决于 agent 记不记得
printf '13. 续用是否可机械执行\n'
if [ -x "$S/run_agent.sh" ] && grep -q 'resume' "$S/run_agent.sh" 2>/dev/null; then
  P "run_agent.sh 记录 session id 并支持 --resume，续用是动作不是态度"
else
  F "没有续用机制 —— 打回-修复循环只能靠 agent 自觉不重开"
fi

# 14 ── 重组把唯一合法合并通道改坏了却没人知道
printf '14. 合并通道路径是否完好\n'
mm=$S/merge-to-integration.sh
if [ ! -x "$mm" ]; then
  F "缺少 merge-to-integration.sh —— 没有唯一合法合并通道"
elif grep -qE '(^|[^A-Za-z0-9_])ci/gates/' "$mm" 2>/dev/null ||
     grep -q 'codeagent/reviewagent' "$mm" 2>/dev/null; then
  F "合并通道仍引用重组前的路径/角色 —— 实际跑不起来"
else
  P "merge-to-main.sh 引用的路径与当前结构一致"
fi

# 15 ── 未受控双写：同一仓同一分支两个写入者
printf '15. 写区互斥是否可核验\n'
if [ -x "$S/mission_start.sh" ] && [ -x "$S/checks/_common/05-write-lease.sh" ]; then
  P "路签：mission_start 发签、checks/05 验签，越区提交被拒"
else
  F "没有写区互斥机制 —— 两个 programmer 可以同时改同一片代码"
fi

# 16 ── 新功能没测试，旧功能只能靠肉眼守
printf '16. 缺测试是否会被审核看见\n'
if [ -x "$S/review_start.sh" ] && grep -q '没有配套测试' "$S/review_start.sh" 2>/dev/null; then
  P "起审时自动提示缺测试，reviewer 必须在报告写出补/不补的结论（不硬拦）"
else
  F "新增功能没测试时无人提醒 —— 旧功能迟早只能靠肉眼守"
fi

# 17 ── 中文留痕撞上 C 转义：三个 check 静默假阴性，门禁照样报绿
printf '17. 门禁对非 ASCII 路径是否正确\n'
if [ -f "$S/lib/paths.sh" ] &&
   ! grep -rl 'diff --cached --name-only' "$S/checks" 2>/dev/null | grep -q .; then
  P "checks 一律经 lib/paths.sh 取 NUL 分隔路径，中文留痕不会被静默跳过"
else
  F "仍有 check 直接用 --name-only —— 非 ASCII 路径会被 C 转义，该拦的静默漏过"
fi

# 18 ── 改名/搬家留下悬空引用，只在运行时炸（本轮六次重组，每次都留）
printf '18. 引用完整性是否被穷举核验\n'
if [ -x "$S/gates/check-references.sh" ] &&
   grep -q 'check-references' "$S/gates/run-gates.sh" 2>/dev/null; then
  P "check-references 穷举扫全仓引用，且已挂进 run-gates（不挂就会腐烂）"
elif [ -x "$S/gates/check-references.sh" ]; then
  F "有 check-references 但没挂进 run-gates —— 不会自动跑的检查等于没有"
else
  F "引用完整性只有手写抽样断言 —— 下一次重构照样留悬空引用"
fi

# 19 ── 铁律 11「改动即同步文档」一直没有执行者
printf '19. 文档同步是否有机械执行者\n'
if [ -n "$(find "$S/checks" -name '11-doc-sync.sh' 2>/dev/null)" ]; then
  P "长期文档↔代码区域治理关系（doc-map.tsv + 模块约定），且每次体检长期文档本身"
else
  F "铁律 11 无执行者 —— 文档滞后只能靠人发现"
fi

# 20 ── 门禁红了照样能推上去（我自己干过一次）
printf '20. 推送前是否强制跑门禁\n'
if [ -x "$S/arbiter-push.sh" ] && grep -q 'run-gates.sh' "$S/arbiter-push.sh" 2>/dev/null; then
  P "arbiter-push 推之前跑 run-gates，不绿不推（逃生口记账）"
else
  F "推送不校验门禁 —— 门禁红了照样能推上去"
fi

# ── 70–72：15-stale-blocker.sh 建仓至今没点过火（P1，2026-08-11 审计实证）────
# 病根：`_file=${mark##*@}` 切完，项目实际写法「`` `文字` @ `文件` ``」的反引号与
# 前导空格原样留在 `$_file` 里，`[ -f "$_file" ]` 恒假 —— 障已解也永远判不出来。
# 现场证据：blockers/2026-08-02-e2e-defaults-to-prod.md 改前用改前脚本仍 exit 0。
# 正反两侧都要断言：只加「解除后必须红」会把「本来就该绿」的路也测瞎（判例库教训）。

# 70 ── 反引号 `<文字>@<文件>` 格式：未解除必须绿，已解除必须红
printf '70. 障签解除标志（反引号 `` `文字`@`文件` `` 格式）解析是否生效\n'
if [ ! -f "$S/checks/_common/15-stale-blocker.sh" ]; then
  N "本仓没有 15-stale-blocker.sh"
else
  _sb70=$(mktemp -d); git -C "$_sb70" init -q
  mkdir -p "$_sb70/blockers" "$_sb70/t70"
  printf '解除标志：`marker_70` @ `t70/target.txt`\n' > "$_sb70/blockers/x.md"
  printf 'no marker here\n' > "$_sb70/t70/target.txt"
  git -C "$_sb70" add -A >/dev/null 2>&1
  ( cd "$_sb70" && bash "$S/checks/_common/15-stale-blocker.sh" ) >"$_sb70/out_unmet" 2>&1
  _rc70u=$?
  printf 'has marker_70 now\n' > "$_sb70/t70/target.txt"
  ( cd "$_sb70" && bash "$S/checks/_common/15-stale-blocker.sh" ) >"$_sb70/out_met" 2>&1
  _rc70m=$?
  _v70=""
  [ "$_rc70u" = 0 ] || _v70="$_v70 [未解除时应绿却 rc=$_rc70u：$(cat "$_sb70/out_unmet")]"
  [ "$_rc70m" != 0 ] || _v70="$_v70 [已解除仍绿 rc=0 —— 反引号 @ 格式的解除标志判据失效，就是 P1 实证的那个坑]"
  rm -rf "$_sb70"
  if [ -z "$_v70" ]; then
    P "反引号 @ 格式：未解除保持绿、已解除变红，两侧都对"
  else
    F "$_v70"
  fi
fi

# 71 ── 不受支持的解除标志格式必须响亮失败，不能退化成「永远 met=0」的第二种静默假绿
printf '71. 解除标志格式不受支持时是否会响亮失败（而不是永远判不出已解除）\n'
if [ ! -f "$S/checks/_common/15-stale-blocker.sh" ]; then
  N "本仓没有 15-stale-blocker.sh"
else
  _sb71=$(mktemp -d); git -C "$_sb71" init -q
  mkdir -p "$_sb71/blockers"
  # 既不是「仓内路径」也不是「文字@文件」——一句夹着反引号的自然语言描述
  # （历史上 2026-08-02-nexus-core 便条真实用过这种写法，[ -e ] 对它恒假）
  printf '解除标志：`curl` 的输出里出现 `"db"`\n' > "$_sb71/blockers/weird.md"
  git -C "$_sb71" add -A >/dev/null 2>&1
  ( cd "$_sb71" && bash "$S/checks/_common/15-stale-blocker.sh" ) >"$_sb71/out" 2>&1
  _rc71=$?
  _out71=$(cat "$_sb71/out")
  rm -rf "$_sb71"
  if [ "$_rc71" != 0 ] && grep -q '格式不受支持' <<<"$_out71"; then
    P "格式不受支持的解除标志被响亮拦下（不会退化成永远判不出已解除的静默假绿）"
  else
    F "格式不受支持的解除标志被放过了 rc=$_rc71 —— 这种便条永远销不掉，等于把便条钉死却没人知道"
  fi
fi

# 72 ── quotepath 同类 footgun：review_complete.sh 写进报告的 changed_files
# 必须躲开 core.quotePath 的 C 转义，否则含非 ASCII 文件名的合法审核报告会被
# check-report-schema.sh（那边已经加了 quotePath=false）判定「不完全一致」而拒绝——
# 两边各转义一次就对不上，这是同一个坑的另一种发作方式（false-red 而非 false-green，
# 但病根同源：拿 git 输出的路径去做机械比对，没管 core.quotePath）。
printf '72. 审核报告的 changed_files 是否躲开 quotepath 的 C 转义\n'
if [ ! -f "$S/review_complete.sh" ] || [ ! -f "$S/gates/check-report-schema.sh" ]; then
  N "本仓没有 review_complete.sh 或 check-report-schema.sh"
else
  _sb72=$(mktemp -d); git -C "$_sb72" init -q -b main
  git -C "$_sb72" config user.email t@e.c; git -C "$_sb72" config user.name t
  mkdir -p "$_sb72/code" "$_sb72/agents/x/docs/worklog"
  printf 'x\n' > "$_sb72/agents/x/docs/worklog/w.md"
  git -C "$_sb72" add -A >/dev/null 2>&1; git -C "$_sb72" commit -q -m base
  _base72=$(git -C "$_sb72" rev-parse HEAD)
  printf 'y' > "$_sb72/code/中文文件名.txt"
  git -C "$_sb72" add -A >/dev/null 2>&1; git -C "$_sb72" commit -q -m feat
  _head72=$(git -C "$_sb72" rev-parse HEAD)
  cp -r "$S" "$_sb72/scripts"
  ( cd "$_sb72" && bash scripts/review_complete.sh consulter "$_base72" "$_head72" approved x ) >/dev/null 2>&1
  git -C "$_sb72" add -A >/dev/null 2>&1; git -C "$_sb72" commit -q -m report
  ( cd "$_sb72" && AIMERGENT_REPORT_BASE="$_base72" bash scripts/gates/check-report-schema.sh ) \
    >"$_sb72/schema_out" 2>&1
  _rc72=$?
  _out72=$(cat "$_sb72/schema_out")
  rm -rf "$_sb72"
  if [ "$_rc72" = 0 ]; then
    P "含中文文件名的合法审核报告通过 check-report-schema.sh（changed_files 未被 C 转义污染）"
  else
    F "含中文文件名的合法审核报告被 check-report-schema.sh 拒了：$_out72"
  fi
fi

