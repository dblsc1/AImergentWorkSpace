# 断言 17′–35 · 字面量自毁 / 嵌套仓边界 / 诊断质量 / 监督分工
# 由 scripts/selftest.sh 按文件名顺序 source；共享 repo/S/P/F/N 与 pass/fail/na 计数。
# 本文件不可单独执行（没有那些变量），入口永远是 scripts/selftest.sh。

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

