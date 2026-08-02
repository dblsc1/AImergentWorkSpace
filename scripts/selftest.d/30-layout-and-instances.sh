# 断言 38–37 · J3 布局 / 实例模型 / 停线旗 / 化石计数
# 由 scripts/selftest.sh 按文件名顺序 source；共享 repo/S/P/F/N 与 pass/fail/na 计数。
# 本文件不可单独执行（没有那些变量），入口永远是 scripts/selftest.sh。

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

