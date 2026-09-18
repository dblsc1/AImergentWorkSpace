# ring · 对外接口契约

> 本文件是 ring 对外行为的**唯一事实**。改本文件先改这里、再改代码；破坏性变更必须先评估消费方并留变更记录。禁止悄悄删除既有承诺。

## 契约索引声明（provides / consumes）

```yaml
provides:
  - id: ring.contribution-ring.v1
    summary: 贡献圆环页面 + 计时控制台。渲染入口 window.renderContributionRing(json)，静态路由 /ring/
consumes:
  - id: contracts.timer-ring-visual.v1
    contract: ../../../contracts/timer-ring-visual-v1.md
    purpose: >
      **跨模块视觉规范，不是 API。** 那份契约白纸黑字写着「`modules/ring` 是它的
      第一个实现者」，可这个仓里一直一个字都没提它（2026-09-08 审计补登）。
      漏登的后果不是报错，是**下一个改圆环的人不知道自己受它约束**：
      规范说「实现与本规范不一致时，改的是实现，不是规范」，
      而 table 的蜂巢中心格是照着它做的第二个实现 —— 两处对不上就是全站不一致。
      改 ring.css 的 .chrono 那一族之前，先读那份规范。
  - id: nexus-core.timer.v1
    contract: ../nexus-core/module_docs/contract.md
    purpose: >
      计时控制：POST /api/core/timer/start {taskId}、POST /api/core/timer/stop、
      POST /api/core/timer/cancel（F-RING-2「取消，不记录」按钮，需先经确认
      弹层——取消一段进行中的计时，从不产生任何事实，与 stop 语义互斥）。
      任务列表从 views/tree 取（只要 id 与 name，不自己维护副本）。
      **不直接写事实**——stop 之后由后端组装 session.completed 投进事件入口，
      前端不碰 POST /api/core/events；cancel 同样不碰。
      **2026-08-19 新增**：POST /api/core/timer/backfill {taskId, startAt,
      durationSeconds}（补登「完成了但没计时」的历史段）——严格照
      nexus-core `TimerBackfillIn`/`TimerBackfillOut` 发字段，不发明、不宽松
      处理。与当前是否在跑完全独立（不读、不判断 timer_state），入口在空闲态
      与运行态都可点。`startAt` 前端必须拼出带浏览器本地时区偏移的 ISO 字符串
      （契约：裸时间一律 400）。响应体 `duplicate:true` 时前端显示「这段已经
      补过了」（绝不与「已记录」混淆）；成功时原样回显服务端 `date`（不自己
      算时区）；4xx 原样显示 `detail`。
  - id: nexus-core.planner.crud.v1
    contract: ../nexus-core/module_docs/contract.md
    purpose: >
      计时台改名（2026-09-08）：PATCH /api/core/planner/tasks/{id}，body 只带
      `{name}`。人类要求「长按改名先不做，点击进入计时台改名就行。计时台那里
      是真得改」——table 的蜂巢长按会建一条名字是「日期 时间」占位串的任务，
      要能在这里改成人话。落点 code/frontend/ring-rename.js。
      ⚠️ body 只发 name：后端 TaskUpdate 是 _Strict（extra=forbid），
      多带一个字段就是 422。
      前端写 planner 是 2026-08-01 的人类裁决（推翻「四前端只读」），
      用的是已注册入口，不新增请求面，
      **绝不写 events**（事实账本只追加）。
  - id: nexus-core.views.tree.v1
    contract: ../nexus-core/module_docs/contract.md
    purpose: >
      任务选择器（两级：项目→任务）的数据源。用 zones/projects/tasks 的
      id 与 name；另用任务节点的 dependsOn/done 驱动 F-RING-6 前置未完成
      提示（读既有字段，无新增请求）——两字段任一缺失时前端防御式判「不显示
      提示」，不报错（nexus-core 灰度上线该字段期间的兼容行为）。
  - id: nexus-core.views.current.v1
    contract: ../nexus-core/module_docs/contract.md
    purpose: >
      计时状态的数据源：是否在跑、跑的是哪个项目/任务、会话开始时刻，驱动
      仪表圆环空闲态/运行态切换与表芯秒跳本地自增。实际用到 CurrentOut
      字段：running、project.id、project.name、task.id、task.name、
      task.shareOfProject、sessionStartAt；running=false 时上述子字段按契约
      为 null，前端走空闲态分支。zone 字段本视图未使用。
      **F-RING-1 起 project.totalSeconds/task.totalSeconds/shareOfPlan 不再是
      环分段的主数据源**（那两个字段是终身累计口径，撑不起设计要的"今天"
      范围）——分段改用下面 `views.gantt.v1` 的今日切片；仅当 `views.gantt.v1`
      不可达时，才退回用 `task.shareOfProject` 画终身累计两段式兜底展示。
  - id: nexus-core.views.gantt.v1
    contract: ../nexus-core/module_docs/contract.md
    purpose: >
      "今天"范围的任务级逐日事实数据源（F-RING-1，2026-08-08 人类裁决：不为
      本用途新开端点，直接复用甘特既有读端）——仪表圆环分段（今日三档：当前
      任务/次高任务/其余任务合计）与空闲态表芯"今天 · N 分"的唯一数据来源。
      实际用到字段（按 `code/frontend/ring-instrument.js` 的
      `computeTodaySnapshot()` 逐一核对，不是笼统写"用了这个接口"）：
      `GanttOut.today`（服务端归日字符串，"今天"判据只认它做**字符串相等
      比较**，不用客户端 `new Date()` 拼日期——时区归日错一小时就是错一天）、
      `projects[].id`、`projects[].tasks[].id`、`projects[].tasks[].name`
      （图例展示任务名）、`projects[].tasks[].actual[].date`、
      `projects[].tasks[].actual[].seconds`。
      **不使用** `projects[].tasks[].done` 与 `projects[].tasks[].dependsOn`
      ——F-RING-6 前置提示读的是 `views.tree.v1` 的同名字段，不是这里；两条
      视图都带这两个字段是巧合的重叠，ring 只认 tree 那份，避免"同一个判断
      两个数据源各读一次、迟早漂移"。也不使用 `plan` 任务层（甘特前端的职责，
      该前端不在本仓）、不使用 `projects[].actual`（项目层已按**全部任务**求和，
      ring 需要任务级明细自己按 `date` 过滤后再求和，两者口径不同不能互相
      替代）。`views/gantt` 不可达、或响应缺 `today`/`projects`、或某个项目
      缺 `tasks` 数组：静默退回上面 `views.current.v1` 的终身累计两段式
      兜底展示，不报错、不影响 `views.current.v1` 那一半的正常渲染。
```

## 对外 API

本模块无 API，只有静态渲染入口 `window.renderContributionRing(json)`（见上）。

## 入口与路由

- nginx 公开前缀：`/ring/`（静态路由不经 nexus-core）
- 纯静态页面，无内部服务名/端口

## 依赖的外部契约

| 依赖 | 契约位置 | 用途 |
|---|---|---|
| nexus-core views | `../nexus-core/module_docs/contract.md`（`CurrentOut` / `TreeOut` / `GanttOut`） | 计时状态、选择器、"今天"数据源，见上 consumes |
| nexus-core timer | `../nexus-core/module_docs/contract.md`（`TimerOut` / `TimerStopOut` / `TimerCancelOut` / `TimerBackfillIn` / `TimerBackfillOut`） | 计时控制写入面，见上 consumes |

## 数据与存储

无。纯静态页面，不持久化任何数据，不拥有 Git 外的 data root。

## 配置与密钥

无。不读取任何环境变量、不持有任何密钥；后端地址是写死的同源相对路径
`/api/core/...`（见 consumes），由 nginx 路由决定实际转发目标，不是本模块的配置项。

## 变更记录

| 日期 | CR | 变更 |
|---|---|---|
| 2026-07-31 | 无（首次填实，非破坏性变更） | 契约从模板占位填实：provides（渲染入口 + 静态路由）、consumes（nexus-core views/current，字段级列明）；对应代码见 `code/frontend/` 提交 `7b34679` |
| 2026-08-01 | 人类裁决：四前端做成完整页面，可写但走统一入口 | v0.2：ring 由纯只读改为**可控制计时**。新增 consumes `timer.v1`（start/stop）与 `views.tree.v1`（任务选择器数据源）。**仍不直接写事实**——events 不向前端开放，计时由 timer 代劳 |
| 2026-08-08 | 无（**代码先行的追平**，非新变更——两项能力已在 `code/frontend/` 落地并有测试覆盖，本文件此前没跟上，不是先批后做） | v0.3：`timer.v1` 的 purpose 补上 `POST /api/core/timer/cancel`（F-RING-2 取消按钮，对应代码 commit `27c506d` 之前的 `696112d`）；新增 consumes `nexus-core.views.gantt.v1`（F-RING-1"今天"数据源，人类裁决不新开端点、复用甘特既有读端，字段级列明 `today`/`projects[].tasks[].id`/`actual[].{date,seconds}`，对应代码 commit `27c506d`）；`views.current.v1` 的 purpose 同步注明 `totalSeconds`/`shareOfPlan` 已让位给 `views.gantt.v1`、仅作不可达兜底；「依赖的外部契约」表拆成 views/timer 两行、覆盖 `GanttOut`/`TimerCancelOut`。本轮同时要求：module_docs/contract.md 的 `consumes` 缺口是本次唯一改动面，**不动代码**（本轮无对应代码 commit） |
| 2026-08-19 | 派单：ring/table 各加补登入口 | v0.4：`timer.v1` 的 purpose 补上 `POST /api/core/timer/backfill`（补登「完成了但没计时」的历史段，nexus-core v1.8，与 `timer_state` 完全独立，空闲态/运行态均可点）；「依赖的外部契约」表 timer 行补 `TimerBackfillIn`/`TimerBackfillOut`。对应代码：`code/frontend/ring-backfill.js`（新文件）、`project-task-contribution-ring.html`/`ring.css`/`ring-controls.js`（导出 `window.postCore`）改动，见本次 commit |
