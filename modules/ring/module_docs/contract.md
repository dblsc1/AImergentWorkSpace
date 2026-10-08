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
      计时页「N 条待确认 → AI助理」小链接（2026-09-30，`code/frontend/ring-suggest-link.js`、`#suggest-link`）：
      GET /api/core/activity/suggestions?status=pending&limit=1，只用 `total`。0 条、404（后端早于 v2.2）、出错，
      或网关注入的 `window.HONEYCOMB_NAV` 里没有 `<前缀>assistant/` 页签（没装「AI助理」）→ 链接不出现。
      打开页面与标签页重新可见时各拉一次，不轮询。**只读**：确认 / 忽略都在「AI助理」页
      （`modules/assistant`，那里的契约写全了字段）。2026-09-28 至 09-30 这里是完整的「待确认」面板，已整体搬走。
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
      回顾与分析（2026-09-30 已建，`modules/assistant`；聊天与待确认面板已搬过去）。泳道的历史某天视图将来可从「AI助理」链过来，本版不要求。
  - id: nexus-core.focus.v1
    contract: ../nexus-core/module_docs/contract.md
    purpose: >
      没在计时时圆环中心的「此刻的焦点」（2026-10-09，nexus-core v2.16；数据在已经在拉的 `views.current.v1` 响应里）。
      字与走秒出自共享件 `HoneycombFocus`（`modules/nginx-docker` 契约「此刻的焦点」节）；画法见变更记录 2026-10-09 条。
      一键开始计时走已有的 `timer.v1` start 与 `POST /api/core/planner/projects/{id}/unclassified`（v2.9）。
  - id: nexus-core.activity.auto.v1
    contract: ../nexus-core/module_docs/contract.md
    purpose: >
      自动跟踪在计时页上的两样东西（2026-10-08，nexus-core v2.14；数据都在已经在拉的 `views.lanes.v1` 响应里，不多一个轮询）。
      ① `human.auto` 非 null 且 `human.running` 为 null：人那张卡的卡头多一粒胶囊「自动 · 项目 / 任务」（只到项目时
      「自动 · 项目」）+ 从 `since` 起走秒的钟（`MM:SS` / `H:MM:SS`，一秒一跳）；`--plan` 色虚线边，与「计时中」（实线）
      和「在电脑前」分得开；轨道上**不画**计时段（它不是手动计时）。② `human.needsChoice` 非 null 且没在计时：泳道
      最上面（人那张卡之前）一张卡「你在 <程序 · 标题>，记到哪？」（`code/frontend/ring-choice.js`）：项目下拉
      （`views.tree.v1`，不列 `status: "done"` 的项目）→ 任务下拉（缺省「未分类」，不列已完成的任务）、勾选项
      「以后这个窗口都这样记」（缺省勾）、按钮「确定」（没选项目时禁用）与「这次不选」。
      「确定」= `POST /api/core/activity/choice {key, taskId | projectId, remember}`；「这次不选」=
      `POST /api/core/activity/choice/dismiss {key}`。成功即收卡，并发 `honeycomb:timer-changed`（顶栏芯片上的小点跟着收）；
      例外先留一句说明、人点「知道了」才收：响应 `pseudonymized: true`（标题是代号，没法记住）、勾了记住但
      `remembered: false`（规则没写成）、404 且是窗口已不在在场记录里。其他失败（含选的任务 / 项目刚被删的 404）
      卡留着、显示「没记上：detail」。**卡是粘的**：出现后留到人答 / 说这次不选——之后的轮询换了别的窗口或不再报，
      卡不换、表单与焦点不丢；手动开始计时（`running` 非 null）就收。人一直不答 = 什么都不发生（那段活动照常进
      「待确认」）。程序名、标题、项目名、任务名只当文本渲染。
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
| 2026-10-09 | 仓主：蜂巢页和计时页还是没有实时显示人此刻的焦点窗口 / 任务 | 认 nexus-core v2.16 在 `views.current.v1` 上追加的 `focus`（本页已经在 7 秒轮询这条读端，不多一个请求；新增 consumes `nexus-core.focus.v1`）。**取代**「没在计时时圆环中心只写『当前没有进行中的计时』」：没有手动计时、共享件 `HoneycombFocus.describe()`（`modules/nginx-docker` 契约「此刻的焦点」节，页面直接引 `../__cockpit/focus.js`）非 null 时，中心换成**此刻的焦点**（`#chrono-center[data-focus]`）：第一行 `#focus-lead`「正在：项目 / 任务」（认不出时「正在用」，自动跟踪时「自动 · 项目 / 任务」），第二行 `#focus-window`「程序 · 标题」（一行截断，`title` 给全文），`#focus-elapsed` 从 `since` 起每秒走的钟，`#focus-hint` 小字写出处（规则 / 你选的 / AI 认的 / 来自会话 / 按以往）+「未计时」。圆环轨道（`.chrono.is-focus .track`）换成 `--fact` 色**虚线**并缓慢呼吸——比手动计时的实心贡献弧轻，一眼分得开；`prefers-reduced-motion` 下不动。认得出项目时按钮是 `#focus-start-btn`「开始计时」：一下就对那个任务起**真的手动计时**（只到项目时先 `POST /api/core/planner/projects/{id}/unclassified` 取「未分类」桶），并把项目 / 任务下拉预选好；认不出时仍是原来的 `#start-big-btn`（计选中的任务）。倒计时页签下两个按钮都藏。`focus.state` 为 `afk` → 第一行「离开」、整块压暗、不给焦点按钮。没有新鲜心跳 / 老后端 / 共享件没加载到 → 与本条之前完全一样（「今天 · N 分」+「当前没有进行中的计时」）。手动计时在跑时中心不变。项目 / 任务由服务端认，本页不自己认；全部 textContent。泳道里人那张卡那行字同版改由同一个共享件出（见 `modules/nginx-docker` 契约）。 |
| 2026-10-08 | 仓主：规则认不出就让 AI 出来写规则；认错了人要一键撤 | nexus-core v2.15「让 AI 认窗口」在计时页上的三样（都画在人那张卡上，不另起卡）：① `human.aiThinking` → 一行小字「AI 正在认这个窗口…」+ 轻微的呼吸（`prefers-reduced-motion` 下不动），这期间不出「你在 X，记到哪？」；② `human.auto.source` 为 `"ai"` → 胶囊写「自动 · 项目 / 任务（AI 认的）」，后面一个「不对」；③ 点「不对」= `POST api/core/activity/choice/reject {key}`（`ring-choice.js` 的 `reject`），成功后 `ring-lanes.js` 立刻把那张「你在 X，记到哪？」摆到最上面让人自己选，失败就重拉、按服务端此刻的说法画。画法在共享的 `lanes.js`（`opts.onAutoWrong`），本模块只接动作。测试：`tests/test_ring_choice.py` 追加四条 |
| 2026-10-08 | 仓主：留一个开关——允许 / 不允许 AI 管理进行中的任务；规则认不出时提醒人选项目 / 任务（自动跟踪第一步） | 认 nexus-core v2.14 在 `views.lanes.v1` 的 `human` 上追加的 `auto` / `needsChoice`（新增 consumes `nexus-core.activity.auto.v1`，写全了行为）：没在计时时人那张卡上出「自动 · 项目 / 任务」胶囊 + 走秒的钟（共享件 `lanes.js` 画）；规则认不出的窗口停留够久时泳道最上面出一张「你在 X，记到哪？」（新文件 `ring-choice.js`，经 `lanes.js` 的 `opts.lead` 摆在人那张卡之前，随换位动效浮上来），选项目 →（可选）任务，缺省勾「以后这个窗口都这样记」，「确定」/「这次不选」。**手动计时永远优先**：在计时时两样都不出现，圆环、开始 / 暂停 / 停止的行为一概不变。开关关着（缺省）时服务端不给这两个键，页面与此前相同。只增 |
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
| 2026-09-30 | 仓主定新页「AI助理」 | 「问问助手」聊天（`ring-chat.js`、`#chat-panel`）与「待确认」面板（`ring-suggestions.js`、`#suggest-panel`）连同样式、测试原样搬到 `modules/assistant`（行为与承诺由那边的契约接着兑现）；本页**不再 consume `agent.chat.v1`**（装 ring 不再带上聊天后端，装 assistant 才带）；`activity.suggestions.v1` 改为只读个数的小链接「N 条待确认 → AI助理」（0 条、404、没装 AI助理页时不出现） |
| 2026-10-03 | 仓主：泳道改卡片式、按活跃排、只展开前 5；人那条线要看得出「我在」 | `views.lanes.v1` 的画法追加如下，**取代** `nexus-core.views.lanes.v1` 条里与之冲突的三处说法（其余承诺不变：配色、闪烁、轮询、404 隐藏、textContent、读端上限与「还有更多」）。① **卡片**：每条泳道一张卡（卡头：名字、当前相位胶囊、摘要；卡身：该线的时间条），所有卡共用最上面一条时间轴、左右对齐。人那张卡**钉在最前**、不算进 5 张。② **排序与折叠**（取代「按响应顺序」）：代理按共享件 `lanes.js` 的纯函数 `sortByActivity` 排——在跑且当前相位是 `waiting_input`/`waiting_permission` 的浮到最前（它们在等人），其余按**视窗内不空闲（working / waiting_* / error）的秒数**倒序，同分按最近一次相位转入倒序；前 **5** 张展开，其余收进原生 `<details>`「还有 N 个」（仍是窗口里的**全部**泳道，只是收着；轮询重画保持开合与焦点；折叠区因只剩 ≤ 5 张而消失时，焦点交给面板标题，开合记着、再出现时照旧）。在等你的卡另加黄色左边与底色。③ **代理卡头写活跃分钟**（「活跃 N 分 · 最近 HH:MM」/「HH:MM 结束」，不是今天的时刻带日期）：这是**代理**运行的、排序依据的数字，取代「面板里不出现任何秒数合计」对代理的那一半；**人**的时间仍不出现任何合计，人的数字仍只在圆环上。④ **连线**（取代「从人那条线落到该代理线的竖线」）：卡片之间分开放，`reply` 画成该代理自己时间条上的一根竖线、`attend` 画成该条上的半透明带子，悬停写「我回了话 · HH:MM」/「我在看 · HH:MM–HH:MM」；「现在」在每张卡的条上各一根。⑤ **人此刻的状态**（取代「在场细带」）：人那张卡的卡头放状态胶囊——各设备里 `to` 落在 `[now − 90 秒, now + 60 秒]`（容设备时钟快一点，再往后的不算）的 `human.presence` 段中有一段 `afk:false` → 「在电脑前」，只有 `afk:true` → 「离开」，都没有 → 「不在线」（灰）；摘要写前台「程序 · 标题」（服务端已脱敏，原样当文本、CSS 截断），在计时（`human.running`）时改写「计时中 · HH:MM 起」并另加「计时中」胶囊，优先于前台程序（共享件纯函数 `humanStatus`）。在场不再是细带：人那条时间条分上下两半，上半是计时记下的段（实心），下半是在场（同色系浅填 = 在电脑前，与上半各占一行、不扣掉计时的时段；离开画斜线），图例加「在电脑前」「离开」。窄屏卡片纵排、条随宽度缩放、不出横向滚动。代码：共享件 `lanes.js`（`render` 加 `opts.cards`/`opts.top`，新增导出 `sortByActivity`/`activeSeconds`/`humanStatus`）、`lanes.css` 末段；`ring-lanes.js` 传 `cards: true, top: 5` |
| 2026-10-08 | 仓主：正在干活的代理没排在最前，被折进「还有 N 个」 | **取代** 2026-10-03 条②里的排序与折叠说法（其余不变）。`sortByActivity` 改按**档位**排：① 在跑且当前相位 `waiting_input`/`waiting_permission` → ② 在跑且 `working` → ③ 在跑且 `error` → ④ 在跑且 `idle` → ⑤ 已结束；同档按视窗内不空闲秒数倒序，同分按最近一次相位转入倒序（已结束的高活跃运行不再压过正在干活的）。仍展开前 **5** 张、其余收进「还有 N 个」，但档 ①② 的卡**永不折叠**：多于 5 张时全部展开，折叠从它们之后才开始。纯前端排法，无接口变化；导出不变 |
| 2026-10-08 | 仓主：长按项目不再建「日期 时间」任务，时间记进项目的「未分类」 | 认 nexus-core v2.9 的 `views.current.v1` `task.kind`：计的是项目的「未分类」时间桶（`kind: "unclassified"`）时，圆环中心、倒计时层、桌面通知、暂停记忆的 `taskName` **只写项目名**（不写「未分类」）；「改任务名」按钮藏掉、点圆环里的名字也不开编辑层（桶是系统任务，后端 409）。图例里它作为项目内的一档仍叫「未分类」（来自 `views.gantt.v1` 任务层）。**取代** 2026-09-08 那条里「蜂巢长按会建一条名字是『日期 时间』占位串的任务，要能在这里改成人话」的前提——新的长按不再建这种任务；旧的占位任务还在，照旧能改名。本页不提供「把未分类的时间归到某个任务」（后续 PR） |
| 2026-10-08 | 仓主：泳道卡片换位要有动效——往上走的略放大、排队上去，其余飘下来 | `views.lanes.v1` 的画法**追加**（排序、折叠、配色、轮询等既有承诺不变）：卡片按 `runId` 认。同一块泳道**第二次起**的重画里位置变了的卡从旧位置滑到新位置——**往上走的**略放大（约 1.025）、带抬起的阴影、压在别的卡上面，ease-out 360 毫秒，几张一起上时自上而下各晚 50 毫秒（排队）；**往下让的**只平移，440 毫秒、稍软；**新出现的**淡入 + 上浮；进出收着的折叠区（旧位置或新位置看不见）只淡入、不飞；消失的直接没有。**相位变了**的卡（如 在干活 → 等你回话）相位胶囊弹一下、卡边亮一圈，各一次。只动 `transform` / `opacity`，先读完位置再一次写完；不抢焦点、不改折叠区开合；**第一次画不动**、页面不可见时不动；`prefers-reduced-motion: reduce` 时不平移不放大（直接到位），只留淡入与卡边那一圈的透明度。实现：共享件 `lanes.js` 的 `motion()`（Web Animations，动画 id `hcl-move`；重画仍整棵换掉，只按 `runId` 记旧位置）、`lanes.css` 末段；卡上新增 `data-phase`（当前相位 / `ended`）。本版不做：消失的卡淡出、「现在」那一端另加呼吸（在跑末段原有的闪烁不变） |
| 2026-10-08 | 仓主：结束超过 3 小时的运行不要再显示，只看还开着的和刚结束的 | **取代** `nexus-core.views.lanes.v1` 条与 2026-09-30 / 10-03 条里「画窗口里的**全部**泳道」对已结束运行的那一半（其余不变）：计时页的泳道卡片只画**在跑的**运行（一律画，超时挂着的也画）和 `endAt` 距响应的 `now` **不到 3 小时**的已结束运行；更早结束的不画，**与选的窗口无关**（「最近 3 小时」「今天」都一样）。面板标题的红绿灯、「还有 N 个」、读屏摘要只数画出来的；筛完一个不剩时照旧写「这段时间没有代理在跑。」。实现：共享件 `lanes.js` 新增导出的纯函数 `recentRuns(agents, now)`（常量 `ENDED_KEEP_MS` = 3 小时），`render` 在 `opts.cards` 时排序前先筛；顶栏预览（列表式，本来就只看最近 1 小时）不受影响 |
