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
      前端写 planner 是 2026-08-01 的产品决定（推翻「四前端只读」），
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
      "今天"范围的任务级逐日事实数据源（F-RING-1，2026-08-08 产品决定：不为
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
  - id: nexus-core.activity.suggestions.v1
    contract: ../nexus-core/module_docs/contract.md
    purpose: >
      计时页「待确认」面板（2026-09-28，`code/frontend/ring-suggestions.js`）：
      GET /api/core/activity/suggestions?status=pending&limit=200 列出桌面检测程序上传的活动段，
      每条可改任务后 POST {id}/confirm {taskId}，或 POST {id}/dismiss；「全部确认」只发
      `suggestion.taskId` 非空且 `confidence ≥ 阈值`（缺省 80%）的条目。用到字段：
      `items[].{id,startAt,endAt,durationSeconds,app,title,suggestion.taskId,suggestion.confidence}`。
      **建议不是事实**：确认之后才调 `window.fetchAndRender` 刷新圆环；确认不是计时，
      **不发** `honeycomb:timer-changed`。GET 404（后端早于 v2.2）→ 面板整块不出现。
      失败原样显示 `detail`，条目留在列表里。app/title 来自别的机器，只当文本渲染。
      任务下拉与路径显示读 `views.tree.v1`（zones/projects/tasks 的 id、name、zoneId）。
  - id: nexus-core.views.lanes.v1
    contract: ../nexus-core/module_docs/contract.md
    purpose: >
      （v0.3，**契约先行，前端待建**）计时页的**主视图之一**：圆环下方整宽的「泳道」区，**默认展开**，画窗口里的
      **全部**泳道（读端上限 200 个运行，`truncated` 时在区尾写一句「还有更多」）。人一条线在最上，下面每个代理运行
      一条线（名字用 `label`，没有用 `agent`），横轴是时间（缺省「最近 3 小时」实时窗口，可切「今天」；实时窗口
      读 `?date=<today>`——跨零点时读 `?from=<昨天>&to=<今天>`——本地按 `now` 裁出最近 3 小时）。人那条线：`human.sessions` 画实心块（按 `mode` 深浅），
      `running` 画到 `now`，`human.presence` 在人那条线下缘画一条细带（离开画斜线，悬停显示程序名 + 标题）。
      代理线按 `phases` 推出的段着色：`working` 绿、`waiting_input`/`waiting_permission` 黄、`idle` 灰、`error` 红；
      第一个转入点之前按 `working` 画；**只有在跑运行的最后一段**做闪烁（绿慢闪、黄快闪），
      `prefers-reduced-motion` 时不闪；`overdue` 的运行末段画虚线。连线：`reply` 画一根从人那条线落到该代理线的
      实线竖线，`attend` 画同色半透明的竖向带子（`at`→`until`）。约 15 秒轮询，页面不可见时停；404（后端早于 v2.4）
      → 面板整块不出现。`app`/`title`/`label`/`detail` 来自别的机器，只当文本渲染（textContent）。
      **画的是标记，不是时长**：面板里不出现任何秒数合计，人的数字仍只在圆环上。另读 `views.current.v1` 的
      `agents[].phase` 给顶栏 / 面板标题画当前红绿灯（`null` 按 `working`）。
      选在 ring 而不是新开模块：ring 本来就是「此刻」的页面、已在轮询 current，不必为一张图再加路由与安装项。
      颜色取 `tokens.css` 的语义色（缺的在实现 PR 里按 `contracts/design-tokens-v1.md` 先加 token，不在 JS 里现算），
      与顶栏芯片悬停的精简预览（`modules/nginx-docker` 契约「泳道预览」节）同一套配色。
      页面分工（设计意图，仓主 2026-09-30 定）：**计时页（ring）= 现在**——在跑的计时、全部泳道；
      **任务 / 项目页（hive）= 未来**——计划；**新页「AI助理」= 回顾与分析**——聊天、待确认的活动建议、检测程序设置、
      回顾与分析（该页待建；聊天与待确认面板现住 ring，迁过去另起 PR）。泳道的历史某天视图将来可从「AI助理」链过来，本版不要求。
  - id: agent.chat.v1
    contract: ../../../contracts/agent.chat.v1/contract.md
    purpose: >
      （v0.3 AI 桥，`code/frontend/ring-chat.js`、HTML 末尾 `#chat-panel`、`ring.css` 末段）计时页的
      「问问助手」面板，与「待确认」面板上下叠放：上面是待确认的活动建议（不变），下面是和助手聊天
      （会话下拉 + 新对话 + 删除、消息列表、输入框、发送 / 停止）。只调 `<前缀>api/agent/`，**从不调任何
      代理运行时（opencode）自己的接口**。用到：`GET health`（非 200 → 聊天整块不出现；
      `configured:false` → 显示「去 .env 填 AGENT_API_KEY」）、会话的列 / 建 / 读 / 删、
      `POST messages` 读 SSE（`start`/`delta`/`tool`/`done`/`error`，不认识的事件忽略；
      `tool` 只显示「正在查：…」）、`cancel`（「停止」按钮）。POST 一律 `Content-Type: application/json`。
      失败原样显示 `detail`。回答正文**当纯文本渲染**（不插 HTML）——里面可能转述别的机器上来的窗口标题。
      助手在 v0.3 什么都不写；它提到的活动建议仍由人在上半块点确认。
```

## 对外 API

本模块无 API，只有静态渲染入口 `window.renderContributionRing(json)`（见上）。

## 入口与路由

- nginx 公开前缀：`<站点前缀>ring/`（缺省 `/ring/`；静态路由不经 nexus-core）。页面里的
  请求都从网关注入的 `window.HONEYCOMB_BASE` 拼（`contracts/gateway.v1` 第七节）
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
| 2026-08-01 | 产品决定：四前端做成完整页面，可写但走统一入口 | v0.2：ring 由纯只读改为**可控制计时**。新增 consumes `timer.v1`（start/stop）与 `views.tree.v1`（任务选择器数据源）。**仍不直接写事实**——events 不向前端开放，计时由 timer 代劳 |
| 2026-08-08 | 无（**代码先行的追平**，非新变更——两项能力已在 `code/frontend/` 落地并有测试覆盖，本文件此前没跟上，不是先批后做） | v0.3：`timer.v1` 的 purpose 补上 `POST /api/core/timer/cancel`（F-RING-2 取消按钮，对应代码 commit `27c506d` 之前的 `696112d`）；新增 consumes `nexus-core.views.gantt.v1`（F-RING-1"今天"数据源，产品决定不新开端点、复用甘特既有读端，字段级列明 `today`/`projects[].tasks[].id`/`actual[].{date,seconds}`，对应代码 commit `27c506d`）；`views.current.v1` 的 purpose 同步注明 `totalSeconds`/`shareOfPlan` 已让位给 `views.gantt.v1`、仅作不可达兜底；「依赖的外部契约」表拆成 views/timer 两行、覆盖 `GanttOut`/`TimerCancelOut`。本轮同时要求：module_docs/contract.md 的 `consumes` 缺口是本次唯一改动面，**不动代码**（本轮无对应代码 commit） |
| 2026-08-19 | 派单：ring/table 各加补登入口 | v0.4：`timer.v1` 的 purpose 补上 `POST /api/core/timer/backfill`（补登「完成了但没计时」的历史段，nexus-core v1.8，与 `timer_state` 完全独立，空闲态/运行态均可点）；「依赖的外部契约」表 timer 行补 `TimerBackfillIn`/`TimerBackfillOut`。对应代码：`code/frontend/ring-backfill.js`（新文件）、`project-task-contribution-ring.html`/`ring.css`/`ring-controls.js`（导出 `window.postCore`）改动，见本次 commit |
| 2026-09-23 | 下游需求 6：整站挂子路径 | 页面请求（`/api/core/...`）改为从网关注入的 `window.HONEYCOMB_BASE` 拼，缺省 `/` 时与之前逐字相同（`contracts/gateway.v1` 第七节） |
| 2026-09-26 | v0.2.1 实测 | 补登表单：没动过日期/时刻时，填时长自动把开始时刻推到「现在往前这么久」（之前默认开始=现在，只填时长必被拒）；开始+时长超过现在时本地先拦、用人话说明，不再把服务端带 ISO 时间戳的拒绝原文甩给人。计时写成功后发 `honeycomb:timer-changed`，顶栏芯片即时刷新（modules/nginx-docker 契约） |
| 2026-09-28 | v0.3 AI 桥（契约先行） | 新增 consumes `agent.chat.v1`：聊天面板与「待确认」面板合并，只经 `<前缀>api/agent/`；前端代码在实现 PR 里跟上 |
| 2026-09-28 | v0.3 AI 桥实现 | `agent.chat.v1` 的前端落地：`ring-chat.js`（新文件）、`#chat-panel`、`ring.css` 末段。与「待确认」上下叠放（不做页签）；POST 的 SSE 用 fetch + ReadableStream 解析；全部 textContent；Enter 发送、Shift+Enter 换行。装 ring 时安装器随之装上聊天后端模块 `agent`（它再拉上 `mcp`） |
| 2026-09-28 | v0.3 自动检测只是建议 | 新增 consumes `nexus-core.activity.suggestions.v1`：计时页「待确认」面板（`ring-suggestions.js`、HTML 末尾 `#suggest-panel`、`ring.css` 末段）。列出、改任务、确认、忽略、按把握阈值全部确认；端点 404 时整块隐藏；确认后刷新圆环、不发 `honeycomb:timer-changed` |
| 2026-09-30 | v0.3 人一条线、代理多条线（契约先行） | 新增 consumes `nexus-core.views.lanes.v1`（计时页「泳道」面板：配色、闪烁、连线、轮询、404 隐藏）与 `views.current.v1` 的 `agents[].phase`；前端代码在实现 PR 里跟上 |
| 2026-09-30 | 仓主定（PR #50） | 泳道从「可折叠面板」改为计时页默认展开的主视图、画全部泳道；配色走 tokens、与顶栏预览一致；写入页面分工设计意图（ring=现在 / hive=未来 / 待建「AI助理」=回顾与分析） |
| 2026-09-30 | v0.3 泳道前端实现 | `views.lanes.v1` 落地：`ring-lanes.js`（新文件）、HTML `#lanes-panel`（圆环卡下方）、`ring.css` 末段；画图用 nginx-docker 的共享件 `<前缀>__cockpit/lanes.js`（顶栏预览同一份）。缺省最近 3 小时、可切「今天」；标题红绿灯取同一份响应里在跑运行的当前相位（与 `views.current` 的 `agents[].phase` 同源，不另拉）。配色 token `--agent-work`/`--agent-wait` 按 design-tokens v1.2 追加，兜底块同步 |
