# 编排机制 · agent 树怎么高效跑（CFO/arbiter 必读）

> CFO 和各模块 arbiter 编排子代理时遵循。目标:多层嵌套、在原 session 内、全程留痕、少冷启动。

## 核心机制（基于 harness 现实）
1. **子代理=冷启动全新上下文**:只拿派单提示词,不继承父对话。首行必须"先读你的角色卡 <path>"。
2. **续用 > 重开**:用当前 harness 的续用/追加任务能力联系已开子代理，让它保留自己的上下文。**打回-修复循环续用同一个实现 agent，不要每轮重开**。具体动作见 `agents/roles/arbiter.md` 附录 harness 映射表（Claude Code = `SendMessage(agentId)`）——**"续用"是个动作，不是个态度**；照抄那张表，别自己发挥。
3. **让干活的 agent 活着**:一个子模块的 backend 从实现→被打回→修复,应是**同一个 agent 全程**,不是每轮新开。
4. **在原 session 内**:子代理后台跑、完成通知父;整棵树在一个 session,结果回流父。
5. **三层嵌套(CFO→arbiter→backend)= 未验证**:先用小任务验能不能、几层可靠,再全自动。不行就退化为"CFO 开扁平 per-module executor(两层)"。
6. **模型分档（2026-07-30 用户裁定）**:reviewer 与 arbiter 的**常规轮次（无打回）一律用中档模型（Claude=sonnet），不用高档（opus）**。升档只在:打回后的复审、跨模块/架构级裁决分析。programmer 照 arbiter 卡的执行档轴选。省下的不是钱是延迟——常规审查用高档是浪费,不是审慎。

## 留痕（每层都留，缺一层=断链）
- 每个 agent 出 `report.json`(三部分,见 report-schema.md)+ worklog。
- arbiter 的 `sub_reports` 卷子代理报告(摘要+路径);CFO 的报告卷各模块 arbiter。
- **落点必须在仓内**：不属固定角色的临时执行者，报告由派活方收编进 `<派活方>/docs/subreports/` 并随任务 commit。`sub_reports[].path` 写仓外绝对路径（`/tmp/...`、会话目录）= 该子报告按未产出计。**能被下一个人 `git show` 出来的才叫留痕。**
- 用户只看 CFO 顶层报告 + 全链路 git 留痕即可回溯到底。

## 与 CI/CD 的关系（两层，别混）
- **session 内编排**=开发期:CFO/arbiter 派 agent 干活、迭代、在 feat 分支提交。
- **CI/CD**=push 后自动门禁（框架根 `scripts/`：gates + tests），PR 到 main 时运行。
- 组合:session 内编排把活干完并形成 feature candidate → push 触发 CI/CD → 独立 approved 且 gates/tests 全绿后才 squash 合并 main。**编排主动组织工作，CI/CD 被动验证交付。**
