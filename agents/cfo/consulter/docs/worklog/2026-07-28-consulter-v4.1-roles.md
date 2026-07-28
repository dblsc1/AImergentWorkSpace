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
