# V4.1 角色重构与结构收敛

## 做了什么

按人类第二轮意见：

1. **规范正文进 `agents/`**，根上只留 `README.md` + 两个一行指针（`AGENTS.md` 给 Codex、
   `CLAUDE.md` 给 Claude Code）。**指针必须留在根**——那是两个 harness 的自动发现点，
   移走等于开工不加载规范。顺带修掉「根缺 CLAUDE.md」。
2. **`agents/review/` 拆解**：检测脚本归 `scripts/reviewcode/`，审核详报归 reviewer 自己的 docs
   （留痕分散原则）。这个目录本来就不该整体塞进 `agents/`，是我搬错。
3. **协议移出 `roles/`**：`report-schema.md` 与 `orchestration.md` 不是角色，进 `agents/protocol/`。
4. **四角色定案**：`arbiter` / `programmer` / `programmer_reviewer` / `module_reviewer`。
   backend + frontend 合并为通用 `programmer`；写边界在角色卡里**留空占位**，
   由 `scripts/new_agent.sh` 生成实例时填实，支持按任务分区收窄。
5. **`module_reviewer` 补上「编排者产出无人审」的洞**，但职责严格限定在规范面：
   文档规范 / 有无越权 / 大任务报告是否合规。**不评技术方案**——越过规范面本身就是越权。
6. **`.env.example`** 列全 18 个 `AIMERGENT_*` 变量，此前零文档。
7. **模块 `AGENTS.md` 成为模块级提示词事实源**，内含「已知坑」一节——
   踩坑直接写这里，不另开文档（另开的结果是没人读）。
8. `install-ci.sh` → `install-gates.sh`（V4 已无 `ci/` 目录，名字是残留）。

## 两个 harness 约束（不是选择，是事实）

- **根 `AGENTS.md`/`CLAUDE.md` 不能移走**，否则两个 harness 都不自动加载规范。
- **三层嵌套（CFO→arbiter→programmer）未验证**：子代理默认不带派活工具，
  arbiter 若本身是 CFO 的子代理，多半开不了自己的子代理。
  **本版按两层扁平设计**：arbiter 各开独立 session，在自己 session 内派 programmer。

## 「创建即考试」为什么做不到，以及替代品

Claude Code 的子代理由模型在运行时开，不经 shell，脚本拦不住这个动作；
而且 agent 不存在时没人能答题。**替代品：把考题写进 `.claude/agents/<角色>.md`
（该子代理的 system prompt），要求出生第一条输出就是答卷。**
比"创建时考试"更靠前，且派活方一眼看得到。判卷仍走 `scripts/exam.sh`，不过则重派。

## shell 派活：嵌套限制的解法（补记）

CLI 实测 `claude -p --agent <角色>` 起的是**全新进程**，整个 session 以该角色身份跑，
**不走子代理那条路，因此不受嵌套层数限制**。arbiter 即使自己是子代理也能派 programmer。
落成 `scripts/run_agent.sh`。

附带一个意外收获：`-r/--resume <session_id>` 让「续用 > 重开」**第一次成为机械动作**——
session id 自动记在 `logs/sessions/`，打回时 `--resume` 即续用。
此前这条规范执行率取决于 agent 记不记得（实测重开一次可占整轮子代理消耗三成）。

代价如实记：独立进程不共享父 session 的上下文与缓存，结果靠 stdout 解析。

## 两个 harness 事实（此前我用了但没验证）

- `.claude/agents/*.md` 按**工作目录/项目根**发现（项目级 `.claude/agents/` 或用户级 `~/.claude/agents/`）。
  生成在 `<模块>/.claude/agents/` 只在 Claude 以该模块为工作目录时生效 —— shell 派活必须先 cd 进模块。
- **agent 定义里 `@path` import 是否展开，我没验证**。已改为把角色卡正文**直接内联**进
  `.claude/agents/<角色>.md`，不依赖该语法。宁可文件长一点，不赌未验证的行为。

## reviewer_opinion（补记）

人类要求：**任何 agent 的 mission_complete 都要有 reviewer_opinion**。落成 `checks/70`。

实现时撞到一个真冲突,记下来:**「每份报告都要有审核意见」与既有铁律「审核门在 merge 不在 commit」直接打架**——
若提交时就要求 approved,programmer 永远无法先提交形成 candidate,审核根本无从开始。

解法是把它拆成两格:
- **提交时**:字段必须存在且格式正确,`verdict` 允许 `pending`。拦的是「根本没人审、也没打算让人审」。
- **合并时**:`verdict` 必须 `approved`。这才是审核门。

⚠️ **合并那格目前只写在规范里,没接进 `merge-to-main.sh`**（397 行,我没吃透,不敢盲改）。
在接进去之前那一格靠人守,**不要当成已有机械保证**——这正是本轮反复强调的那类"写了规范却没有执行者"。

## merge-to-main.sh 复核（补记，含一条我说错的更正）

**更正**：我上一轮说「合并时 approved 没有执行者」是**错的**。
`merge-to-main.sh:228-298` 早就在强制——它要求 reviewer 那份 report.json
`.status == "approved"` 且 `review_target` 与 candidate 精确绑定，不通过直接 die。
合并门一直有执行者，只是通过 reviewer 的报告 status，不是通过 `reviewer_opinion` 字段。

**但读完发现更严重的问题：它在 V4 上是坏的。** 重组时三处路径没跟着改：
`ci/gates/check-report-schema.sh`、`ci/gates/run-{gates,tests}.sh`、`codeagent/reviewagent/`。
**唯一合法的合并通道在 V4 上根本跑不起来**，而 selftest 没有任何断言覆盖它。

已修，并补 selftest 第 14 条专门盯这件事。教训与上次同源：
**字符串替换式重组，静态 grep 看不出「引用的东西已经不存在」——必须有断言守着。**

## 闸门适配多角色：级联

人类问「mission_complete 对每个角色不尽相同怎么办」。
方案是**把级联原则用到闸门上**，与规范同一套：

```
scripts/checks/_common/       所有角色
scripts/checks/<角色>/         角色专属
codeagent/<角色>/checks/       本模块追加
```

三层依次全跑，**下层只能加严**。增删改查一条检查 = 加/删/改一个文件，主脚本永不用动。
角色解析：`AIMERGENT_ROLE` → 从暂存 worklog 路径推断 → 只跑 `_common`。

**没有采纳「scripts 改名 script_samples + 每个 agent 各带一份闸门」**，理由：
这些脚本是真被执行和安装的，不是样例；而每个 agent 各带一份通用检查会立刻产生
「同一个检查被实现 N 遍、逐渐漂移」——那正是模块 reviewcode 已经踩过的坑。
模块级扩展点保留了（`codeagent/<角色>/checks/`），但只用来**追加**，不复制通用项。

## V4.1 第二批：路签 / 先审后推 / 控制台（补记）

人类全批准八条，另加控制面板。要点与理由：

**① 路签（火车那套）**：`mission_start.sh` 发签、`checks/_common/05` 验签、
`mission_complete` 通过后还签。重叠判据是**路径前缀互含**（父/子文件夹都算）。
「无法绕过」落在验签——不跑发签就没有签，一提交即被 pre-commit 拦死。
**发签自愿，验签强制。** 因此 `run_agent` 不必串行，写区不重合即可并发。

**② 先审后推（人类提出，我改主意）**：原设计要求先 push 再审，多余。
本地 commit 已是不可变、有 SHA 的真实对象，绑它足够；reviewer 与 arbiter 同机同仓，本地 SHA 直接可验。
先审后推两个实打实的好处：**被打回的活永远不上远端**（time_management 那两个含 Critical
的中间态就是先推后审留下的）、**返修不需要 force-push**。
代价：review 时没有 CI 结果 —— 本地 gates/tests 先顶，CI 作为推之后的第二道网。

**③ 合并目标改 dev**：`merge-to-main.sh` → `merge-to-integration.sh`，
目标 `$AIMERGENT_INTEGRATION_BRANCH`（默认 dev），**以 main 为目标直接拒绝**。
dev→main 是人的动作，agent 不得代劳。

**④ 控制台**：`control-panel/` 零依赖 Python 标准库服务 + SSE。
所有脚本 source `scripts/lib/emit.sh`，激活/退出自动上报——
**「每激活一个 sh 面板就看得到」是自动的，不需要各脚本各写一遍上报代码**。

**⑤ 两个新闸门**：`90-dependency-drift`（依赖是最容易悄悄劣化又最难事后看出的东西）、
`83-test-required`（新功能必须带测试 —— 这是「把 token 花在新功能上」的前提：
旧功能被回归套件锁住，module_reviewer 才敢不去肉眼看老代码）。
配套把 `review/regression/`（永久回归）与 `review/reviewcode/`（本轮一次性核验）分家。

自测扩到 16 条，全绿。
