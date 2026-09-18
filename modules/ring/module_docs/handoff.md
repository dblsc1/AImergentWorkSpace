# ring · handoff（冷启动接手便条）

> **面向未来接手**：新 AI 只读这一份就能上手——不是历史流水。做完一个任务，检查这里要不要更新。

## 一句话

**贡献圆环视图 + 计时控制台**：纯静态页，读 nexus-core 的
`views/current`/`views/tree`/`views/gantt`，写 `timer/start|stop|cancel|backfill`。
对外暴露渲染入口 `window.renderContributionRing(json)`，
静态路由 `/ring/`。零后端逻辑、零构建工具、零 CDN 依赖（D18）。

> ⚠️ 本节以下三小节（怎么跑/接口/避坑）截至 2026-08-19 只做了**最小校正**
> （补自动化测试的存在、补 backfill 端点），**没有整篇重写**——本文件对
> "当前只有贡献圆环一个只读视图"的旧描述在更早的 cockpit-v1/倒计时改造
> 时就已经过期，那次没有同步更新这份 handoff。代码侧的最新
> 结构、window.* 导出全表、四个 JS 文件的分工，唯一事实源是
> `code/frontend/handoff.md`（每改必核，是新鲜的）。
> 谁下一次动这个模块，建议顺手把本节按 `code/frontend/handoff.md` 的口径
> 整篇重写一次，而不是继续这样一条一条打补丁。

## 怎么跑 / 怎么测

生产：由 nginx 挂 `/ring/`（见 `../nginx-docker`），需先登录。
本地单看页面：任意静态服务器指向 `code/frontend/`；
此时 fetch 会失败，页面显示"连接失败"——**这是预期**，走的是错误分支，不是坏了。

**有自动化测试**（真浏览器 Playwright，不经 nginx、不写库、不需要口令）：
`bash code/frontend/tests/run.sh`。详见 `code/frontend/handoff.md`「怎么跑/怎么测」
——本条目此前写的"没有自动化测试"是过期信息，倒计时/仪表圆环/补登三轮任务单
都补了对应套件，只是这份 handoff 没跟上代码侧的进度。

## 接口

规范性定义见 `module_docs/contract.md`（唯一事实）。
消费：`../nexus-core/module_docs/contract.md` 的 `CurrentOut`/`TreeOut`/`GanttOut`/
`TimerOut`/`TimerStopOut`/`TimerCancelOut`/`TimerBackfillIn`/`TimerBackfillOut`。
`CurrentOut` 实际只用 `running` / `project.{name,totalSeconds,shareOfPlan}` /
`task.{name,totalSeconds,shareOfProject}`；`zone` 不用，`sessionStartAt` 用于
表芯秒跳自增。字段级最新明细见 `module_docs/contract.md` 的 `consumes` 声明。

- 2026-09-12：「暂停 / 继续」只用既有 `timer/stop|start`；暂停记忆是跨模块共享的本机键
  `nexus.timer.paused.v1`（形状见 `contracts/timer-ring-visual-v1.md`，table 蜂巢中心格读写同一个键）。

## 避坑 / 冻结点 / 技术债

1. **`renderContributionRing` 内部渲染逻辑的旧冻结已解除**（2026-08-08，
   F-RING-1）：旧的双弧+渐变+引线画法整体重做成仪表圆环，"逐字节抄上游原件"
   的约束不再适用，上游参考原件本身仍未被动过。详情见
   `code/frontend/handoff.md`「与旧版的关键差异」。
2. **空闲态不能直接喂 null。** `running:false` 时字段为 `null`，
   直传会让渲染层判空抛错。必须先分流到空闲态分支。
3. **占比已经是 0–100，别再除或乘 100。** 契约明确不是 0–1 小数。
4. **fetch 必须是同源相对路径**，不能写死 host:port。
5. 轮询 7 秒（建议 5–10 秒的中间值，无特殊理由）。
6. **`timer/backfill`（2026-08-19）与 `timer_state` 完全独立**：不判断、
   不读当前是否在跑，入口空闲态/运行态都可点，前端不许自己加"先停表"的
   伪约束——这是契约明写的行为，不是本模块自己的选择。详情、时区偏移拼接
   的坑、三条显示规则的落地位置，全在 `code/frontend/handoff.md`「补登
   入口」一节，不在此重复。

## 契约缺口台账（补一条清一条）

| 日期 | 缺口 | 现状 |
|---|---|---|
| 2026-09-08 | 「计时台改名」新开了一个消费面（`PATCH /api/core/planner/tasks/{id}`），当时只落在实现里，**没进 `module_docs/contract.md`**。 | **已清**：`nexus-core.planner.crud.v1` 补进 consumes，写明 body 只发 `{name}`（后端 `TaskUpdate` 是 `_Strict`，多带一个字段就是 422），以及"前端能写"的来源是 2026-08-01 人类裁决、用的是已注册入口、绝不写 `events`。 |
| 2026-09-08 | `contracts/timer-ring-visual-v1.md` 白纸黑字写着「`code/ring` 是它的**第一个实现者**」，可这个仓里一直一个字都没提它。漏登的后果不是报错，是**下一个改圆环的人不知道自己受它约束**（规范说「实现与规范不一致时，改的是实现」），而 table 的蜂巢中心格是照着它做的第二个实现——两处对不上就是全站不一致。 | **已清**：作为 `contracts.timer-ring-visual.v1` 补进 consumes，并写明"改 `ring.css` 的 `.chrono` 那一族之前先读那份规范"。 |

