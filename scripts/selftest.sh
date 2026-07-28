#!/usr/bin/env bash
# 门禁有效性自测 —— 把真实踩过的坑做成断言，检查本仓的治理工具能否拦住它们。
#
#   scripts/selftest.sh [目标仓路径]     默认当前 git 顶层
#
# 输出 PASS(有防御) / FAIL(无防御) / N/A(不适用本仓类型)。
# 设计原则：静态能力探针 + 一次真实空区间试跑；不写入目标仓、不产生 commit。
#
# 「我的规范拦得住我自己犯过的每一个错」—— 这份脚本就是那句话的证据。
# 它必须进 CI 每次跑，否则半年后这些闸门会在无人察觉中失效（V1 假绿门禁就是这么来的）。
set -uo pipefail
. "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/lib/emit.sh" 2>/dev/null || true

repo=${1:-$(git rev-parse --show-toplevel 2>/dev/null || true)}
[ -n "$repo" ] && [ -e "$repo/.git" ] || { echo "❌ 不是 git 仓: ${repo:-<空>}" >&2; exit 2; }
repo=$(cd -- "$repo" && pwd -P)

pass=0; fail=0; na=0
P() { printf '  ✅ PASS  %s\n' "$*"; pass=$((pass+1)); }
F() { printf '  ❌ FAIL  %s\n' "$*"; fail=$((fail+1)); }
N() { printf '  ⏭  N/A   %s\n' "$*"; na=$((na+1)); }

# 兼容 V3（ci/）与 V4（scripts/）两种布局
S=$repo/scripts; [ -d "$S" ] || S=$repo/ci

printf '\n══ 门禁有效性自测 · %s ══\n\n' "$repo"

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

printf '\n── 小结: PASS %d · FAIL %d · N/A %d ──\n\n' "$pass" "$fail" "$na"
[ "$fail" -eq 0 ]
