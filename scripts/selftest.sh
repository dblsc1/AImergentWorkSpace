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

# 17 ── 模板检查被占位符替换自毁（A1 类）：模板 *.sh 含连续 token 字面量，
#        replace_token 会把「查占位符」改成「查模块名」，正确的模块恰因替换成功而永远红
printf '17. 模板脚本是否会被占位符替换自毁\n'
tokm='{{'"MODULE_NAME"'}}'; tokf='{{'"FRAMEWORK_ROOT"'}}'
tpl=$repo/code/_template
if [ ! -d "$tpl" ]; then
  N "本仓无模块模板"
elif find "$tpl" -name '*.sh' -exec grep -lIF -e "$tokm" -e "$tokf" {} + 2>/dev/null | grep -q .; then
  F "模板 *.sh 含连续占位符字面量 —— 生成模块后该脚本自毁（改查模块名字面量）"
else
  P "模板脚本的 token 全部拆分书写，替换后检查仍指向占位符"
fi

# 18 ── 撒谎式成功（A2 类）：写「将来要 commit 的产物」前不验落点是否被 gitignore 吞
printf '18. 写产物的脚本是否验落点可跟踪\n'
if grep -q 'assert_trackable' "$S/lib/emit.sh" 2>/dev/null &&
   grep -q 'assert_trackable' "$S/review_complete.sh" 2>/dev/null; then
  P "review_complete 写报告前验 check-ignore；被吞即响亮失败而非打印✅"
else
  F "报告可以写进被 gitignore 吞掉的路径并照常报成功 —— 审核证据蒸发"
fi

# 19 ── 检查越过嵌套仓边界（A3 类）：根仓的 find 把模块仓文件当自己的判 → 误伤拒绝提交
printf '19. 检查是否尊重嵌套仓边界\n'
if grep -q 'show-toplevel' "$S/checks/_common/20-report-committed.sh" 2>/dev/null; then
  P "report 类检查跳过嵌套模块仓的文件（归模块自己的门禁管）"
else
  F "根仓检查会把嵌套模块仓的 report.json 判成未跟踪 —— 模块一存在根仓就永远提交不了"
fi

# 24 ── 角色用错层级时，报错说的是 gitignore，不是"你用错角色了"
printf '24. 审核角色与仓层级错配是否可诊断\n'
if [ -x "$S/review_complete.sh" ] && grep -q '模块级.*审核角色' "$S/review_complete.sh" 2>/dev/null; then
  P "根仓跑模块级审核角色时，直指「该用 consulter」而非只报 gitignore"
else
  F "角色层级错配只会报 gitignore 落点被吞 —— 人看不出真正的问题是角色用错了"
fi

# 25 ── 模块内相对路径在根仓跑：只说"找不到文件"，不说"你站错了仓"
printf '25. 站错仓时是否可诊断\n'
if [ -x "$S/dispatch.sh" ] && grep -q '模块内相对路径' "$S/dispatch.sh" 2>/dev/null; then
  P "任务单是模块相对路径而人在根仓时，直指该 cd 进哪个模块"
else
  F "只报「找不到任务单」—— 人会去找文件，真问题是站错了仓"
fi

# 26 ── 派单提示词里混进绝对路径（copycat 的 @/srv/… 病）
printf '26. 派单提示词是否只含相对路径\n'
if [ -x "$S/dispatch.sh" ] && grep -q 'realpath --relative-to' "$S/dispatch.sh" 2>/dev/null; then
  P "角色卡路径按当前仓根取相对，跨仓派单不会写死绝对路径"
else
  F "跨仓派单会把绝对路径写进提示词 —— 换台机器即废"
fi

# 27 ── 派活主干上的撒谎式成功：agent 没写权限，一个字没落盘，进程照退 0
printf '27. 派出去的活是否核实真落盘\n'
if [ -x "$S/run_agent.sh" ] &&
   grep -q 'permission-mode' "$S/run_agent.sh" 2>/dev/null &&
   grep -q 'landed' "$S/run_agent.sh" 2>/dev/null; then
  P "run_agent 授写权限 + 事后核工作树真变了（rc=0 且零改动 → 判未完成）"
else
  F "派出去的 agent 可能一个字没落盘却报成功 —— 撒谎式成功在派活主干上"
fi

# 28 ── landed 被脚本自己的 diary 写入骗成恒真（检查永远给同一个答案）
printf '28. landed 是否排除脚本自产物\n'
if [ -x "$S/run_agent.sh" ] && grep -q "grep -vE '(^|\[ ?\])logs/'" "$S/run_agent.sh" 2>/dev/null; then
  P "落盘快照排除 logs/，不会被自己的 diary 写入骗过"
else
  F "landed 会把脚本自己的 diary 写入算成「活落盘了」—— 恒真"
fi

# 29 ── die-on-first 只告诉你第一个问题，来回三四趟才修完
printf '29. 起飞/着陆是否为可读检查单\n'
if [ -f "$S/lib/checklist.sh" ] &&
   grep -q 'checklist.sh' "$S/mission_start.sh" 2>/dev/null &&
   grep -q 'checklist.sh' "$S/mission_complete.sh" 2>/dev/null; then
  P "起飞单与着陆单共用同一套渲染，一次列全、每项带「怎么办」"
else
  F "还是 die-on-first —— 只报第一个问题，人要来回跑好几趟"
fi

# 30 ── 改名后旧留痕树还在，看起来是活的，人打开它以为"没更新"
printf '30. 孤儿留痕树是否会被发现\n'
if [ -n "$(find "$S/checks" -name '12-standing-docs.sh' 2>/dev/null)" ] &&
   grep -q '孤儿留痕树' "$S/checks/_common/12-standing-docs.sh" 2>/dev/null; then
  P "迁移留下的旧 docs 树会被点名（两棵并存时人会打开旧那棵）"
else
  F "改名后旧留痕树静默留着 —— 看起来是活的，实际早已停更"
fi

# 31 ── 18 个脚本平铺，"我现在该跑哪个"没有答案
printf '31. 是否有单一入口与常驻检查单\n'
if [ -x "$S/aim" ] && grep -q '两张检查单' "$S/new_agent.sh" 2>/dev/null; then
  P "scripts/aim 给出生命周期入口；两张检查单与地图写进 agent 的 system prompt（常驻）"
else
  F "没有入口，或检查单只在派单提示词里出现一次（会滚走）"
fi

# 32 ── 模块的提交闸门从来没装上过，而且缺了不报错
printf '32. 模块是否真的装到完整闸门\n'
if grep -q 'mission_complete.sh mission_start.sh' "$S/install-gates.sh" 2>/dev/null &&
   grep -q '拒绝提交' "$S/hooks/pre-commit" 2>/dev/null; then
  P "install-gates 装齐流程脚本+checks+lib；pre-commit 缺件即拒绝，不静默放行"
else
  F "模块可能只装了 gates/ —— 着陆检查一次都不会跑，且缺件时静默放行"
fi

# 33 ── 「改了这个要看哪些文档」只在着陆算一次
printf '33. 文档影响面是否五个时点都能算\n'
if [ -x "$S/doc_impact.sh" ] && [ -f "$S/lib/docmap.sh" ] &&
   grep -q 'doc_impact' "$S/arbiter-push.sh" 2>/dev/null &&
   grep -q 'dm_impact' "$S/mission_start.sh" 2>/dev/null; then
  P "算法抽成 lib/docmap.sh，起飞/随时/着陆/推送各调一次，同一个答案来源"
else
  F "影响面只在着陆算 —— 起飞不知道、干活中查不到、推送不再算"
fi

# 34 ── 模块副本会随框架演进静默过期
printf '34. 模块门禁新鲜度是否被核\n'
if [ -n "$(find "$S/checks" -name '13-gates-fresh.sh' 2>/dev/null)" ]; then
  P "模块门禁副本与框架逐文件比对，过期或缺件即红"
else
  F "模块门禁停在装它那天的版本，在跑、在绿，但跑的是旧判据"
fi

# 35 ── 监督分工（谁审谁）曾散写在三处角色卡里，改一处漏两处
printf '35. 监督分工是否单一事实源\n'
if [ "$repo/scripts" = "$S" ] && [ -d "$repo/agents" ]; then
  _sup=$repo/agents/protocol/supervision.md
  _refs=0
  for _card in agents/cfo/AGENTS.md agents/consulter/AGENTS.md agents/consulter/审查提示词.md \
               agents/reference/工作空间全景.md; do
    grep -q 'agents/protocol/supervision.md' "$repo/$_card" 2>/dev/null && _refs=$((_refs+1))
  done
  if [ -f "$_sup" ] && grep -q '监督矩阵' "$_sup" && [ "$_refs" -eq 4 ]; then
    P "supervision.md 是唯一事实源，四份角色/地形文档都指回它（$_refs/4）"
  else
    F "监督矩阵缺失或文档未指回（$_refs/4）—— 分工又要开始各写各的了（实证：全景文档并行写入旧表述）"
  fi
else
  N "非框架仓布局，无项目级角色卡"
fi

# 38 ── J3 布局：留痕迁代码旁后，模板/协议/门禁三层必须同时认新落点
printf '38. J3 新留痕布局是否三层一致\n'
if [ "$repo/scripts" = "$S" ] && [ -d "$repo/agents" ]; then
  _t=$repo/code/_template
  if [ -d "$_t/module_docs/worklog" ] && [ -f "$_t/code/backend/handoff.md" ] &&
     [ -d "$_t/codeagent/programmer" ] && [ ! -d "$_t/codeagent/programmer/docs" ] &&
     grep -q 'module_docs/report.json' "$repo/agents/protocol/report-schema.md" &&
     grep -q 'module_docs/report.json' "$S/gates/check-report-schema.sh" &&
     grep -q 'code/\*/report.json' "$S/gates/check-report-schema.sh"; then
    P "模板骨架、report-schema、check-report-schema 三层都认 J3 落点"
  else
    F "某一层还在旧布局——agent 会按读到的那层写错地方"
  fi
else
  N "非框架仓布局"
fi

# 39 ── 一页纸「每改必核」：检查项存在、可执行、满足 --describe 契约
printf '39. handoff 每改必核检查是否在位\n'
_c14=$S/checks/_common/14-handoff-fresh.sh
if [ -x "$_c14" ] && "$_c14" --describe >/dev/null 2>&1; then
  P "checks/14 在位且满足 --describe（mission_complete 自动发现）"
else
  F "J3 的「每改必核」没有机器落点——一页纸会静默过期"
fi

# 40 ── 实例模型：new_instance 存在且 session 复用有落点
printf '40. 编号实例与 session 复用是否有落点\n'
if [ -x "$S/new_instance.sh" ] && grep -q 'session' "$S/new_instance.sh" &&
   grep -qF '[new-instance]=new_instance.sh' "$S/aim"; then
  P "new_instance.sh 生成 agent.md+session+comm.jsonl，aim 有入口"
else
  F "实例模型只在文档里——arbiter 开不出编号实例，续用不重开落不了地"
fi

# 41 ── 生成的子代理 prompt 不随源再生就会漂（实证：曾漂到旧版卡整整一轮）
printf '41. consulter 子代理 prompt 是否与源同步\n'
_gen=$repo/.claude/agents/consulter.md
if [ -f "$repo/agents/consulter/AGENTS.md" ]; then
  if [ -f "$_gen" ] && {
       printf -- '---\nname: consulter\ndescription: workspace v5 的架构顾问兼框架维护者 —— 与 CFO 平级，掌管框架仓的 git。\n---\n'
       cat "$repo/agents/consulter/AGENTS.md"
       printf -- '\n---\n\n'
       cat "$repo/agents/consulter/审查提示词.md"
     } | cmp -s - "$_gen"; then
    P ".claude/agents/consulter.md 与角色卡+审查提示词逐字节一致"
  else
    F "生成物与源漂移（或缺失）——克隆下来的 consulter 会带着旧卡出生；重跑拼接再提交"
  fi
else
  N "非框架仓布局"
fi

# 42 ── 单文件申领不到写区路签（CFO 实报：.gitignore→.gitignore/ 永远匹配不上）
printf '42. 单文件写区路签是否可用\n'
if grep -q '\${l%/}' "$S/checks/_common/05-write-lease.sh" 2>/dev/null &&
   grep -q 'if \[ -f "\$p" \]' "$S/mission_start.sh" 2>/dev/null; then
  P "05 接受精确相等（含旧尾斜杠签），norm() 对已存在文件不加斜杠"
else
  F "根级单文件无法合法进入任何提交——与白名单 .gitignore 叠加成静默丢数据路径"
fi

# 43 ── 判据仓型不匹配（第 3/4 例：12、60 在模块仓跑根仓判据恒红）
printf '43. 检查项是否仓型感知\n'
if grep -q 'repo_is_module' "$S/lib/paths.sh" 2>/dev/null &&
   grep -q 'repo_is_module' "$S/checks/_common/12-standing-docs.sh" 2>/dev/null &&
   grep -q 'resolves_here_or_framework' "$S/checks/_common/60-doc-paths-exist.sh" 2>/dev/null; then
  P "仓型/框架根解析是 lib/paths.sh 公共件，12/60 都在用"
else
  F "按框架仓写的判据装进模块仓恒红——恒定答案类，模块提交会被无关判据卡死"
fi

# 44 ── cfo_reviewer 子代理机制（J6）：卡存在、矩阵与 CFO 卡都指它、模块本地豁免可用
printf '44. CFO 例行审查子代理机制是否落地\n'
if [ "$repo/scripts" = "$S" ] && [ -d "$repo/agents" ]; then
  if [ -f "$repo/agents/cfo/reviewer/agent.md" ] &&
     grep -q 'agents/cfo/reviewer/agent.md' "$repo/agents/protocol/supervision.md" &&
     grep -q 'agents/cfo/reviewer/agent.md' "$repo/agents/cfo/AGENTS.md" &&
     grep -q '\.local\.txt' "$S/checks/_common/60-doc-paths-exist.sh"; then
    P "J6 卡在位、矩阵与 CFO 卡指回；模块本地豁免名单（.local）可登记"
  else
    F "J6 机制缺件——CFO 开不出规范面审查子代理，或模块前向引用仍无处登记"
  fi
else
  N "非框架仓布局"
fi

# 45 ── 停线旗：阻塞级问题必须能一键冻结提交/发签/推送（三个入口一个都不能少）
printf '45. 停线机制是否三入口齐备\n'
if grep -q 'STOPLINE' "$S/hooks/pre-commit" 2>/dev/null &&
   grep -q 'STOPLINE' "$S/mission_start.sh" 2>/dev/null &&
   grep -q 'STOPLINE' "$S/arbiter-push.sh" 2>/dev/null &&
   grep -q '^/logs/STOPLINE$' "$repo/.gitignore" 2>/dev/null; then
  P "logs/STOPLINE 旗被 pre-commit/mission_start/arbiter-push 三入口认，且本地不入仓"
else
  F "停线缺入口——阻塞级架构问题发现了也停不下来，只能眼看它继续产出"
fi

# 46 ── 被测对象必须是仓里那份（两任同日各踩一次的形状）
printf '46. 门禁新鲜度是否核「入仓」与「活钩子」\n'
if grep -q 'ls-files --error-unmatch "\$rel"' "$S/checks/_common/13-gates-fresh.sh" 2>/dev/null &&
   grep -q '活钩子' "$S/checks/_common/13-gates-fresh.sh" 2>/dev/null &&
   grep -q "'\*worklog/\*.md'" "$S/checks/_common/90-dependency-drift.sh" 2>/dev/null; then
  P "13 拒未入仓副本、活钩子自比（框架 checkout 不豁免）；90 的说明取值域随 J3 迁移"
else
  F "工作区副本能骗过新鲜度 / 活钩子停在装机那天 / 新落点的说明不可见——三个都是「被测对象错位」"
fi

# 47 ── 「先 rebase 再装」曾只活在散文里，作者本人一轮后原样再犯
printf '47. 装门禁前是否机械核源仓新鲜度\n'
if grep -q 'AIMERGENT_INSTALL_STALE_OK' "$S/install-gates.sh" 2>/dev/null &&
   grep -q 'rev-list --count "HEAD..\$_up"' "$S/install-gates.sh" 2>/dev/null; then
  P "源仓落后上游即拒装（记账逃生口可放行）——教训从散文变成断言"
else
  F "先 rebase 再装只是一句 worklog——已实证散文管不住一轮之后的自己"
fi

# 48 ── 过期便条（「等裁决」解了没销）曾一日三例——该自堵，不该巡逻
printf '48. 障签机制是否在位（过期便条自堵）\n'
_c15=$S/checks/_common/15-stale-blocker.sh
if [ -x "$_c15" ] && "$_c15" --describe >/dev/null 2>&1 &&
   grep -q '解除标志' "$_c15" && [ -d "$repo/blockers" ] &&
   grep -q 'blockers/' "$repo/agents/protocol/report-schema.md" 2>/dev/null &&
   grep -q '波及' "$S/mission_start.sh" 2>/dev/null; then
  P "blockers/ 便条：着陆逼销障（checks/15）+ 发签拦压线写区（起飞单第 8 项）"
else
  F "「等裁决」只活在报告散文里，或发签时刻不看便条——programmer 会被派进未决裁决压着的写区"
fi

# 36 ── 「N 条铁律 / N 条断言」的 N 是化石：写下来的那天就开始漂
#        （实证：同一仓里同时存在 12、22、23 三个数字，实际 35 条）
printf '36. 文档是否硬编码了会漂移的条目计数\n'
if [ -d "$repo/agents" ]; then
  _drift=$(grep -rn '[0-9]\+ 条\(通用\)\?铁律\|[0-9]\+ 条闸门\|[0-9]\+ 条断言' \
    --include='*.md' --include='*.sh' "$repo/agents" "$repo/scripts" "$repo/README.md" 2>/dev/null |
    grep -v '/worklog/' | grep -v '/findings/' | grep -v '/subreports/' | grep -v 'selftest.sh')
  if [ -z "$_drift" ]; then
    P "长期文档不写死条目数，指向「全集/运行输出」"
  else
    F "硬编码计数会随条目增删静默过期：$(printf '%s' "$_drift" | head -3 | tr '\n' ' ')"
  fi
else
  N "非框架仓布局"
fi

# 37 ── 起飞单第 4 项只认模块级角色卡，项目级角色永远过不了
printf '37. 角色卡判据是否匹配角色层级\n'
if grep -q 'agents/\$role/AGENTS.md' "$S/mission_start.sh" 2>/dev/null; then
  P "起飞单认两处布局：模块级 codeagent/ 与项目级 agents/"
else
  F "只认模块级布局 —— cfo / consulter 永远卡在第 4 项"
fi

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
    . "$_lease_lib"
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

# 56 ── 角色自动推断是否覆盖 J3 之后的全部 canonical 留痕路径（F3）
#      **这一条是为了不把 #50 的坑重挖一遍而存在的**：#50 的沙箱只喂
#      codeagent/programmer/docs/worklog/x.md —— 五条里唯一还能命中旧正则的那条，
#      于是断言恒绿而现实全是 unknown。这里逐条喂，缺一条就判不合格。
printf '56. 角色推断是否覆盖 J3 canonical 留痕路径（逐条喂）\n'
if [ ! -f "$S/lib/paths.sh" ] || ! grep -q 'role_from_trace_path' "$S/lib/paths.sh" 2>/dev/null; then
  F "lib/paths.sh 里没有 role_from_trace_path —— 角色推断没有公共件，各处各写一套（F3）"
else
  # 逐条喂：<路径>|<期望角色>|<仓型>（m=模块仓 / f=框架仓；仓型影响 code/ 的判读）
  _cases56=(
    "agents/consulter/docs/worklog/x.md|consulter|f"          # ref-fixture
    "agents/cfo/docs/worklog/x.md|cfo|f"                      # ref-fixture
    "module_docs/worklog/x.md|arbiter|m"                      # ref-fixture
    "code/backend/worklog/x.md|programmer|m"                  # ref-fixture
    "code/backend/orders/report.json|programmer|m"            # ref-fixture
    "review/reviewreport/report.json|programmer_reviewer|m"   # ref-fixture
    "review/reviewcode/tests/t.sh|programmer_reviewer|m"      # ref-fixture
    "codeagent/programmer/docs/worklog/x.md|programmer|m"     # ref-fixture
    "codeagent/programmer/01/docs/comm.jsonl|programmer|m"    # ref-fixture
    "codeagent/module_reviewer/docs/report.json|module_reviewer|m"  # ref-fixture
  )
  _sbx56=$(mktemp -d); mkdir -p "$_sbx56/m/codeagent" "$_sbx56/m/module_docs" "$_sbx56/f"
  _miss56=""
  for _case in "${_cases56[@]}"; do
    _p56=${_case%%|*}; _rest56=${_case#*|}; _want56=${_rest56%%|*}; _kind56=${_rest56#*|}
    _got56=$( cd "$_sbx56/$_kind56" && . "$S/lib/paths.sh" && \
              printf '%s\n' "$_p56" | role_from_staged )
    [ "$_got56" = "$_want56" ] || _miss56="$_miss56
       $_p56 → 期望 $_want56，实得「${_got56:-<空>}」"
  done
  # 反面两条：框架根的 code/ 装的是模块仓不是代码侧；同层两个角色必须判歧义
  _fwc=$( cd "$_sbx56/f" && . "$S/lib/paths.sh" && printf 'code/gantt/x.md\n' | role_from_staged )   # ref-fixture
  [ -z "$_fwc" ] || _miss56="$_miss56
       框架仓的 code/gantt/x.md 被推成「$_fwc」—— 框架根的 code/ 装的是模块仓"   # ref-fixture
  _amb56=$( cd "$_sbx56/f" && . "$S/lib/paths.sh" && \
            printf 'agents/cfo/docs/a.md\nagents/consulter/docs/b.md\n' | role_from_staged )   # ref-fixture
  [ -z "$_amb56" ] || _miss56="$_miss56
       同层出现 cfo 与 consulter 两个角色却给了「$_amb56」—— 歧义必须返回空，不许挑第一个"
  rm -rf "$_sbx56"
  if [ -n "$_miss56" ]; then
    F "角色推断没覆盖全部 canonical 路径（F3）：$_miss56"
  else
    P "十条 canonical 留痕路径逐条命中；框架仓 code/ 不误判；同层歧义返回空不静默挑第一个"
  fi
fi

# 57 ── ref-fixture 行级标记：既要能豁免测试输入，又**不许把这道门变哑**
#      成因：F3 的断言必须把 J3 canonical 路径写成字面量喂进角色推断，
#      而 check-references 把它们全判成悬空引用。逐条登记进 doc-path-exempt.txt
#      只修实例——下一个写断言的人照样撞，且登记表会长成一张混着
#      「真的还没建」与「永远不会建」的名单，再也分不开。
#      行级标记修的是类；但**任何豁免机制都必须验它没顺手把门关掉**。
printf '57. ref-fixture 标记豁免测试输入，但不许把引用完整性变哑\n'
if [ ! -x "$S/gates/check-references.sh" ]; then
  N "本仓没有 gates/check-references.sh，不适用"
else
  _sbx57=$(mktemp -d); git -C "$_sbx57" init -q
  mkdir -p "$_sbx57/scripts/gates"
  cp "$S/gates/check-references.sh" "$_sbx57/scripts/gates/"
  chmod +x "$_sbx57/scripts/gates/check-references.sh"
  : > "$_sbx57/scripts/gates/doc-path-exempt.txt"
  # 一行带标记（应放行）、一行不带（应照红）。两条路径都真不存在。
  {
    printf '#!/usr/bin/env bash\n'
    printf 'fixture="scripts/never-exists-fixture.sh"   # ref-fixture\n'
    printf 'realref="scripts/never-exists-dangling.sh"\n'
  } > "$_sbx57/scripts/probe.sh"
  git -C "$_sbx57" add -A >/dev/null 2>&1
  _o57=$( cd "$_sbx57" && bash scripts/gates/check-references.sh 2>&1 )
  rm -rf "$_sbx57"
  if grep -q 'never-exists-fixture' <<<"$_o57"; then
    F "带 ref-fixture 标记的测试输入仍被判成悬空引用 —— 类没修掉，下一个写断言的人还得逐条登记"
  elif ! grep -q 'never-exists-dangling' <<<"$_o57"; then
    F "没带标记的真悬空引用也没被抓 —— 豁免机制把整道门变哑了（比误报危险得多）"
  else
    P "带标记的测试输入被放行、同一文件里没带标记的真悬空引用照样红（豁免是行级的，不是整文件）"
  fi
fi

# 58 ── 写区粒度棘轮：放宽必须被看见，但**不许硬拦**（F2）
#      五天 diary 的形状：05 拦下越区提交 → 持有者放宽自己的签 → 覆盖面越来越大，
#      每次膨胀前 90 秒内都紧跟一条 failed:write-lease。棘轮没有反向齿。
#      三件一起验：① 放宽要检出并说清哪一条 ② 不放宽不许误报
#      ③ **必须只警告不拦**（判断题硬拦会逼人用逃生口，逃生口用滥闸门全废）
#      ④ 警告必须带签史，且**永远含第一条** —— 只看最近几次看不出单调性
printf '58. 写区放宽是否被看见（且只警告不硬拦、带签史）\n'
if [ ! -f "$S/mission_start.sh" ] || ! grep -q 'lease_widening' "$S/lib/lease.sh" 2>/dev/null; then
  F "lib/lease.sh 没有 lease_widening —— 写区棘轮无人看见（F2）"
else
  _sbx58=$(mktemp -d)
  _mk_lease_sandbox "$_sbx58" someone_else 'unrelated-area/'
  _ld58="$_sbx58/.git/aimergent-leases"
  # 自己的旧签：docs/a/ —— 本轮申领 docs/ 是把它放宽
  printf 'docs/a/\n' > "$_ld58/consulter.lease"
  printf 'granted_at=%s\nttl=7200\ntask=上一轮\n' "$(date -u +%s)" > "$_ld58/consulter.meta"
  # 造六条签史，验「中间略、但第一条永远在」
  mkdir -p "$_sbx58/logs"
  for _i in 1 2 3 4 5 6; do
    printf '{"ts":"2026-07-0%sT01:02:03Z","event":"lease_grant","note":"consulter: docs/a/","rc":0}\n' "$_i"
  done > "$_sbx58/logs/diary.jsonl"
  _rc58=0
  ( cd "$_sbx58" && bash scripts/mission_start.sh consulter task.md docs/ ) \
    >"$_sbx58/wide.txt" 2>&1 || _rc58=$?
  _w58=$(cat "$_sbx58/wide.txt")
  # 收窄/不变的一侧：申领 docs/a/b/（落在旧签里）不该报放宽
  printf 'docs/a/\n' > "$_ld58/consulter.lease"
  printf 'granted_at=%s\nttl=7200\ntask=上一轮\n' "$(date -u +%s)" > "$_ld58/consulter.meta"
  ( cd "$_sbx58" && bash scripts/mission_start.sh consulter task.md docs/a/b/ ) \
    >"$_sbx58/narrow.txt" 2>&1
  _n58=$(cat "$_sbx58/narrow.txt")
  rm -rf "$_sbx58"
  if ! grep -q '放宽：docs/a/ → docs/' <<<"$_w58"; then
    F "写区从 docs/a/ 放宽到 docs/ 没被检出 —— 棘轮转了没人看见（F2）"
  elif [ "$_rc58" -ne 0 ]; then
    F "放宽被**硬拦**了（rc=$_rc58）—— 判断题不做硬闸门，硬拦会逼人改用逃生口"
  elif ! grep -q '签史' <<<"$_w58" || ! grep -q '6 次发签' <<<"$_w58"; then
    F "警告没带签史 —— 只说「你的签有点宽」没有信息量，要说「从几片涨到几片、从未收窄」"
  elif ! grep -q '2026-07-01' <<<"$_w58"; then
    F "签史略掉了第一条 —— 棘轮比的是起点和现在，只看最近几次看不出单调性"
  elif grep -q '放宽：' <<<"$_n58"; then
    F "申领的写区落在旧签里（收窄）却报了放宽 —— 误报会训练人忽略这条警告"
  else
    P "放宽被检出并指名道姓、只警告不拦（rc=$_rc58）、签史含第一条、收窄不误报"
  fi
fi

printf '\n── 小结: PASS %d · FAIL %d · N/A %d ──\n\n' "$pass" "$fail" "$na"
[ "$fail" -eq 0 ]
