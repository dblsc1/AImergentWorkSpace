# hive · 对外接口契约

> 本文件是 hive 对外行为的**唯一事实**。先改这里、再改代码，顺序不可颠倒。
> 破坏性变更必须先评估消费方并留变更记录，禁止悄悄删除既有承诺。

## 契约索引声明（provides / consumes）

```yaml
provides:
  - id: hive.honeycomb-view.v1
    summary: >
      蜂巢视图页面。纯静态页，无构建工具、无 CDN 依赖。分区是圆心发散的角度扇区，
      项目是扇区内的六边形格子；鼠标悬停放大、点开展开为卡片、中心格承载计时。
      静态路由由组装层决定（默认 /），入口文件 code/frontend/index.html。
      页面里的请求与跳转（/api/core/...、去计时台 ring/）都从网关注入的
      `window.HONEYCOMB_BASE` 拼，整站挂子路径时跟着走（contracts/gateway.v1 第七节）；
      没注入按 `/`。
  - id: hive.zone-order.v1
    summary: >
      分区排序的本机记忆，键 `nexus.hex.zoneOrder.v1`（localStorage，JSON 数组，
      元素是 zoneId）。**仅本机偏好，不是事实**——后端不知道它的存在，清掉只会
      回到默认顺序。别的模块要读可以读，但不得依赖它存在。
  - id: hive.tree-cache.v1
    summary: >
      `views/tree` 的本机缓存，键 `table-tree-cache-v1`（localStorage）。**纯离线兜底**：
      拉不到后端时先把上一次的树画出来，同时挂离线状态条。清掉只会让离线时没东西可画。
      键名是 `table-` 前缀不是 `hive-`——模块改名时按「不改运行时标识符」的裁决留下的，
      改它会让已有用户的缓存失效，别顺手统一。

consumes:
  - id: contracts.design-tokens.v1
    contract: ../../../contracts/design-tokens-v1.md
    purpose: >
      颜色一律 `var(--…)`，零裸 hex；唯一豁免是 tokens.css 里哨兵注释包着的兜底块。
      **分区色阶 `--zone-1..--zone-12` 目前还不在那份契约里**，hex.css 有一块临时
      兜底定义（同样被哨兵注释包着，整块可删）。hex-data.js 只产出变量名、不产出
      色值，所以色阶进契约并落到 tokens.css 之后，删掉那个兜底块即可，JS 一行都不用改。

  - id: contracts.timer-ring-visual.v1
    contract: ../../../contracts/timer-ring-visual-v1.md
    purpose: >
      **跨模块视觉规范，不是 API。** 中心格的圆环是这份规范的第二个实现（第一个是
      ring）。两处对不上就是全站不一致，规范说「实现与规范不一致时，改的是实现」。
      改中心格圆环之前先读那份规范。

  - id: nexus-core.views.tree.v1
    contract: ../../nexus-core/module_docs/contract.md
    purpose: >
      蜂巢的主数据源：分区 / 项目 / 任务三级结构与 id、name、progress、flags。
      **progress / progressSource 直接渲染，不在前端重算。**
      项目级颜色由前端按 zoneId 从 zone.color 派生——契约故意不提供 project.color。
      flags 只读不解释，本模块不对 flags 的值做任何 if 分支。

  - id: nexus-core.views.current.v1
    contract: ../../nexus-core/module_docs/contract.md
    purpose: 中心格的计时状态：是否在跑、跑的是哪个项目/任务、会话开始时刻。

  - id: nexus-core.focus.v1
    contract: ../../nexus-core/module_docs/contract.md
    purpose: >
      没在计时时中心格的「此刻的焦点」与项目格的描边（2026-10-09，nexus-core v2.16；数据在已经在拉的
      views.current.v1 响应里）。见下「此刻的焦点」节。

  - id: nexus-core.views.gantt.v1
    contract: ../../nexus-core/module_docs/contract.md
    purpose: >
      项目卡双轨（计划期已走比例 + 有事实天数比例）。这是既有只读投影，
      本模块只是多读一条已经存在的端点，不要求后端加任何东西。
      「今天」用服务端返回的 today，不用本地时钟——客户端时区/时钟不准会让红线飘。

  - id: nexus-core.views.next-actions.v1
    contract: ../../nexus-core/module_docs/contract.md
    purpose: 六边形卡片里的「下一步行动」。前端只负责忠实渲染，不自己排序、不自己筛。

  - id: nexus-core.views.review.v1
    contract: ../../nexus-core/module_docs/contract.md
    purpose: 回顾面板的数据源，只读。

  - id: nexus-core.events.read.v1
    contract: ../../nexus-core/module_docs/contract.md
    purpose: >
      GET /api/core/events，只读展示 `session.completed` 事实（计时档案、最近完成、热度）。
      事实里只有 opaque id（subject.zone/project/task），名字由本模块现查 views/tree
      得到——后端故意不 join 名字。**查不到（已删除）就展示 id 并标注「已删除」，不崩**：
      项目和任务两类 id 都可能被删，按同一套规则兜底，不要只补任务那一种情况。

  - id: nexus-core.planner.crud.v1
    contract: ../../nexus-core/module_docs/contract.md
    purpose: >
      分区 / 项目 / 任务的增删改，统一入口 `/api/core/planner/{zones|projects|tasks}`。
      **`views/tree`、`views/current` 不在 planner 命名空间下**，路径不同，
      别在"统一改前缀"的时候把两组路径揉到一起。
      PATCH 只带真正改动的字段——后端是 strict 模型，多带一个字段就是 422。
      写成功后由调用方重新 fetchTree 刷新，本模块不做本地乐观更新。

  - id: nexus-core.timer.v1
    contract: ../../nexus-core/module_docs/contract.md
    purpose: >
      中心格的三个动作：完成 = POST /api/core/timer/stop，取消不记录 =
      POST /api/core/timer/cancel，继续 = POST /api/core/timer/start {taskId}。
      与 ring 的「停止并记录」/「取消，不记录」是同一批接口，不另开请求面。
      开始计时前若已有任务在跑，无条件先发一次 stop 再 start，**两步串行不并行**。
      **本模块绝不写 events**——事实账本只追加，由后端在 stop 之后自己投。

  - id: nexus-core.views.export.v1
    contract: ../../nexus-core/module_docs/contract.md
    purpose: >
      顶栏「导出数据」：GET /api/core/export，原样展示 + 下载 JSON，不塑形。
      （早就在用，2026-09-28 补登记。）

  - id: nexus-core.restore.v1
    contract: ../../nexus-core/module_docs/contract.md
    purpose: >
      顶栏「导入数据」（2026-09-28）：选一份导出的 JSON，原样发给
      POST /api/core/restore。两步：先 dryRun=true 报「将导入多少」、零写入；人点「确认导入」
      才带 dry-run 回的 checksum 发 dryRun=false。**只进空实例**：服务端 409 时如实告诉人
      「这里已经有数据、不会合并」，不在前端做任何合并或清库。
      导入成功后整页刷新（蜂巢、档案、中心格一次全部重拉）。

  - id: nexus-core.activity.proposals.v1
    contract: ../../nexus-core/module_docs/contract.md
    purpose: >
      蜂巢右上角「AI 待批准」浮层（2026-10-22，见下「待批准浮层」节）：读 `GET activity/proposals?status=pending&limit=100`
      画「新增项目 / 新增任务」卡片；卡片上的「批准」「不要」走 `POST activity/proposals/{id}/approve|reject`
      （只有人能批；项目提议还没批准时批它的任务回 409，如实提示「先批准它的项目」）。

  - id: nexus-core.activity.reports.v1
    contract: ../../nexus-core/module_docs/contract.md
    purpose: >
      同一浮层：读 `GET activity/reports?status=pending&items=true` 画「新增任务 / 改挂 / 当噪声」卡片；
      条目批准 / 不要走 `POST …/reports/{id}/items/{itemId}/approve|reject`，整份「全部批准」走 `…/reports/{id}/approve`。
      只读与批准 / 不要，不拆条、不改去向（那些留在「待批准」页）。

  - id: nexus-core.activity.auto.v1
    contract: ../../nexus-core/module_docs/contract.md
    purpose: >
      「AI 决策」面板的「分类历史」栏（2026-10-23，见下「AI 决策面板」节）：读 `GET activity/auto` 与 `?date=昨天`（v2.37；今天 + 昨天自动记下的段，每条
      `{eventId, startAt, durationSeconds, app, title, taskId, projectId, reassigned, source, ai}`，v2.15 起带 `ai`），只读。

  - id: nexus-core.sessions.reassign.v1
    contract: ../../nexus-core/module_docs/contract.md
    purpose: >
      同一栏把历史卡拖到蜂巢里改归属：`POST sessions/{eventId}/reassign {taskId}` 或 `{projectId}`（与助理页「自动记录」同一写法）。
      只有人能改（设备令牌 403）；失败如实显示后端 detail。新建任务另走 planner.crud.v1 的 `POST planner/tasks`（同长按 / 加任务现成的 `createTask`）。

  - id: nexus-core.activity.secretary.v1
    contract: ../../nexus-core/module_docs/contract.md
    purpose: >
      三级分类决策栏「AI 问题」段（2026-10-24，见「三级分类决策栏」节）：读 `GET activity/questions?status=pending` 与 `GET activity/secretary-reports`（缺省今天）；
      写 `POST activity/questions/{id}/answer {option?, text?}`、`POST activity/questions/{id}/dismiss`（只许人）。
      另用 `GET activity/proposals` 的 `sessionChanges[]`（批准 / 拒绝同其它提议，批准可带 `edit: {taskId | projectId}`）。端点不存在（404）= 对应内容不显示。

  - id: nexus-core.activity.tiers.v1
    contract: ../../nexus-core/module_docs/contract.md
    purpose: >
      「AI 决策」面板的三级分类决策栏（2026-10-24，见下「三级分类决策栏」节）。启动时读一次 `GET activity/settings`：`tiers` 为 true 才换成一栏；
      false、缺键或读取失败一律保持两栏旧面板。读 `GET activity/blocks?status=all&limit=500`（今天）与 `GET activity/blocks/records?date=&blockKey=`（拆分时间条）；
      写 `POST activity/blocks/assign|split|generic|ignore|reset`（除 generic 外带 `ifSeq`，409 = 重读，块仍待处理则用新 seq 自动重试一次；只许人）。
      另用 proposals 里 `windowRule` / `blockRule` / `blockAction` / `question` 四种新 `kind`，以及 `POST activity/proposals/{id}/approve` 的可选 `edit`（批准时更正）。
```

## 两条本模块自己定的语义

**暂停是纯前端的。** 后端没有「暂停」这个概念。暂停 = 一次 `timer/stop`（这一段照常
入账）+ 本机记住「停的是哪个任务」，键 `nexus.timer.paused.v1`，形状见
`contracts/timer-ring-visual-v1.md`。蜂巢页与 ring 同源、**共用这一个键**，
两边都能恢复同一个暂停态。取消则不产生任何事实。

配套的第二个本机键是 `nexus.timer.carry.v1`（`{taskId, carriedSeconds}`）：
从暂停「继续」之后，计时中显示的时长 = 这个累计值 + 本段已跑。它只是显示用的
累计，**不是事实**——清掉只会让显示从本段重新起算，账本一个字节都不受影响。
换任务或结束暂停态时由前端自己清零。

**分区色不是主题色。** 用户显式设过色的分区原样用 `zone.color`（那是用户设定，
契约已豁免它留在 API 内）；没设过的按分区序号取 `var(--zone-N)`。
本模块只决定第几号色，不决定第几号色长什么样——在 JS 里现算 `hsl()`/`oklch()`
同样算运行时生成色值，绕过对比度校验，禁止。

## 长按项目格：记进项目的「未分类」，不再建任务（2026-10-08，v0.4）

仓主 2026-10-08：「不能再快捷新建一个只是日期时间的任务，而是直接把时间记录到项目总的『项目未分类』时间。」
**取代** 2026-09-08 的「长按某个项目自动创建一个子任务（标题就弄个日期+时间任务）开始计时」。

- 长按项目格 = `POST /api/core/planner/projects/{id}/unclassified`（nexus-core v2.9，取或建该项目的「未分类」时间桶，
  回 `{taskId}`）→ 已在计时先 `timer/stop` → `timer/start {taskId}`。**不再 `POST planner/tasks`。**
  长按待办卡片照旧（那条任务已存在，直接计时）。
- 计时中中心格**只显示项目名**（`views/current` 的 `task.kind === "unclassified"`）；暂停记忆
  `nexus.timer.paused.v1` 的 `taskName` 这时也存项目名。
- 这一段结束后，在展开卡与分区规划面板的「最近完成」里各占一行：「临时任务 YYYY-MM-DD HH:MM · N 分钟」
  （时间 = 这一段的开始，浏览器本地时区，同该列表其余时刻）。数据来自 `GET /api/core/events?type=session.completed&limit=200`
  里 `subject.task` 等于某项目 `unclassifiedTaskId` 的那些；它不是任务——没有「撤销」、不计入「近 7 天完成」与热度。
  计时档案里同一段写成「项目 / 临时任务 YYYY-MM-DD HH:MM」。
- 桶这个任务本身不出现在任何任务列表里（`views/tree` 不列它）。
- 旧版长按建出来的「YYYY-MM-DD HH:MM[ 备注]」任务**不迁移**，仍是普通任务，中心格对这种名字的简写规则保留。
- 本版不做：把一段临时任务归到具体任务（后续 PR）。
- **归到具体任务之后（2026-10-08，nexus-core v2.11）**：在「待批准」页的「待分类」里归走的段，事件条目多一个
  `currentSubject`（改挂后的归属，形状同 `subject`）。本页一律按 `currentSubject || subject` 认段：归走的段不再是
  「临时任务」：「最近完成」里那一行留着，名字换成「任务名 · N 分钟」、挂在任务所在的项目下（任务已不在任务树里的不列），
  计时档案里写成「项目 / 任务名」；放回桶的段照旧是「临时任务」。归入的操作不在本页做。

## 此刻的焦点（2026-10-09，v0.4）

仓主 2026-10-09：「蜂巢页和计时页还是没有实时显示人类当前焦点所在窗口或者任务。」nexus-core v2.16 在本页已经在
5 秒轮询的 `views.current.v1` 上追加了 `focus`（不多一个请求）。字与走秒出自共享件 `window.HoneycombFocus`
（`modules/nginx-docker` 契约「此刻的焦点」节，网关随顶栏注入；项目 / 任务由服务端认，本页不自己认）。
**取代**「空闲态中心格只写『空闲』」：

- **中心格**：没有手动计时、本机也没有暂停着、`describe()` 非 null 时，沿用计时中的版式——圆心读数是从 `since`
  起每秒走的钟（只有变了的那几位翻牌）；圆环下（原来写「空闲」的地方）两行：第一行 `.hex-center-focus` 写项目 / 任务
  （认不出时写窗口「程序 · 标题」；一行、放不下省略号，`title` 给全文），第二行写「正在」（自动跟踪时「自动」、
  离开时「离开」）+ 出处（「正在 · 按以往」；出处有规则 / 你选的 / AI 认的 / 来自会话 / 按以往，认不出只写「正在」）。
  静置档格子小：有第一行时第二行只在悬停档出现。中心格加 `is-focus`（离开时再加 `is-afk`，整格压暗）：
  圆环轨道换成 `--fact` 色虚线并缓慢呼吸，不画进度弧、不走秒针——与手动计时分得开。不出现完成 / 暂停 / 取消那三块按钮；
  点中心格照旧进计时台（在那里一键开始计时）。`aria-label` 写「正在：…（未计时），点击进入计时台」。
- **项目格**：`focus.projectId` 非 null（且在电脑前、没有手动计时）时，那个项目的格子加 `is-live-focus`：
  一圈 `--fact` 色的描边缓慢呼吸，让人一眼看到自己在哪。格子不在页面上（分区收起 / 项目已归档）→ 什么都不做、不报错。
  同一时刻至多一格。
- `prefers-reduced-motion`：轨道与描边都不动（静态虚线 / 静态描边）。
- 手动计时在跑、暂停中：中心格与本条之前完全一样，也不描项目格（手动计时在每一处都优先）。没有新鲜心跳 / 老后端 /
  共享件没加载到 → 照旧「空闲」。
- 走秒：有焦点时中心格每秒重画一次（此前只在计时中才每秒重画）。项目名 / 任务名 / 程序名 / 标题只当文本渲染。

**2026-10-09 追加（nexus-core v2.17「串行的注意力时间线」）**：中心格仍然只写**当前这一个**窗口 / 目标、钟数的是这一次
待了多久（人的注意力是串行的）。`.hex-center-focus` 的 `title`（悬停的全文）末尾多一句共享件给的
「（近 2 小时在这上面 N 分）」（`describe().dwell`，同一个窗口累计；老后端 / 离开时没有）。格子上的字不变，不多一个请求。

## 待批准浮层（2026-10-22，v0.4）

> 2026-10-23 起由下节「AI 决策面板」取代：脚本 / 样式改名 `hex-decide.*`，折叠键改 `hive.decide.collapsed`，批准 / 拒绝改成滑动 + 5 秒撤销；
> 本节的数据来源、卡片种类、点卡片指给人看的行为照旧有效。

仓主 2026-10-22：「做一个蜂巢视图的侧边栏，把这些待批准的用图形化卡片栏放在右上角浮动，点击可以直接展开对应的项目和高亮对应任务或虚化显示 AI 建议的新任务 / 项目，直观展示 AI 的增删改查。」
（原）`hex-pending.js` 在蜂巢上方右上角浮一个面板（没有待批准的就整块不出现；可折叠，折叠态记在 localStorage `hive.pending.collapsed`；每 60 秒、蜂巢重拉后、每次批准后重读）：

- 一张卡 = 一件待批准的事，按动词分色：**新增项目**、**新增任务**（来自 proposals，或报告里的 `newTask` 条，同一任务只画一张）、
  **改挂**（报告 `assign` 条：目标任务 / 项目 + 窗口数与时长）、**当噪声**（报告 `dismiss` 条）。AI 给的文字一律只当文本。
  2026-10-22 起 proposals 响应多 `changes`（**改名**、**移动**、**删除**现有项目 / 任务）与 `ignores`（**以后都忽略**）两个键（老服务端缺键当空），各画成卡；点 change 卡展开目标所在项目并高亮目标，删除另加划掉的淡红，任务移动另点亮目的项目格；忽略卡点了只选中。
- 点卡片：目标是现有任务 → 展开它的项目并给那条任务脉冲描边；新增任务 → 展开项目并在列表顶放一条半透明虚线的「AI 建议」；
  新增项目 → 在它的分区里插一格半透明虚线的幽灵格并展开，列出将建的任务。幽灵格 / 幽灵条目只活到收起或重拉为止，不参与长按计时。
  其余分区的变灰沿用展开时现成的聚焦效果。
- 卡片上「批准」「不要」，每份报告一个「全部批准」；成功后重拉蜂巢（真的新项目 / 任务就出现了）。底部「去待批准页」链到 `/assistant/`。

## AI 决策面板（2026-10-23，v0.4）

仓主 2026-10-23 批了交互草稿：右上角浮层改成「AI 决策」，取代上节的待批准浮层。`hex-decide.js` / `hex-decide.css`（取代 `hex-pending.*`）。
折叠成一个角标（默认折叠，状态记在 localStorage `hive.decide.collapsed`，仅本机偏好）；展开两栏「分类历史」「待批准」，视口 ≤560px 时变成两个页签。
没有待批准也没有历史就整块不出现。每 60 秒、蜂巢重拉后重读；**拖拽 / 滑动中、有待发的批准、有新任务输入框时不重读**（结束后补一次）。

- **待批准栏**：来源同上节（proposals 含 projects / tasks / changes / ignores + 待办报告的条目明细）。每张卡：第 1 行窗口名（项目 / 任务 / 变更类卡用对象名）大字；
  第 2 行「↓ 项目 - 任务」（只到项目：「↓ 项目 - 未分类」；新任务「↓ 项目 - ＋ 名字（新任务）」；新项目「↓ 新建项目（分区：X）」；忽略「↓ 以后都忽略这个窗口」；
  改名「↓ 改名为 X」；移动「↓ 移到 X」；删除「↓ 删除」）；第 3 行 AI 理由；合并过的另有「N 段合并 · M 分钟」。
  **合并**：报告条里 种类 + 去向 + 标准化窗口标题 都相同的合成一张（标准化 = assistant `suggestions.js` 的 `normTitle` = nexus-core `history.norm_title` 同一算法）；
  批准 / 拒绝对每条底层项依次 POST 各自的端点，有失败的条留在卡上并显示后端 detail。
  **底色**：目标项目所在格子的颜色与深浅（格子的 `--zone-raw` / `--zone-mix`，不另造色）；忽略 / 噪声灰色，删除深红白字。
- **手势**：卡片横向拖——左滑过卡宽 30% = 批准（露出绿底「批准」），右滑 = 拒绝（红底「拒绝」），不够就弹回；松手后卡片飞出、高度收起，下面的上移。
  键盘：卡片可聚焦，A = 批准、R = 拒绝；另有小号「批准」「不要」按钮。点（不是拖）卡片照旧在蜂巢里指给人看（上节）。
- **撤销**：滑动 / 按键后**不立刻发**，底部提示「已批准：窗口名　撤销」5 秒；撤销 = 卡片原位恢复；5 秒到了才发 POST。可同时有多条待发，各自计时。
  `pagehide` 时把待发的立刻用 `fetch(keepalive)` 发出。
- **分类历史栏**：`GET activity/auto`（今天）+ `GET activity/auto?date=昨天`（nexus-core v2.37；老服务端忽略 `date`，按 `id` 去重），合并后新的在前、最多 60 条。
  卡片：窗口标题（缺则程序名）大字、「↓ 项目 - 任务」（`t_unc_*` 桶 = 未分类）、「规则 / AI / 已改」徽标 + 「HH:MM · N 分钟」，底色同上。
- **拖历史卡进蜂巢**：指针拖，影子跟手；悬在项目格上自动展开它，悬在任务行上高亮；放在任务行 ⇒ `reassign {taskId}`，放在格子本身 ⇒ `reassign {projectId}`，
  放在别处 = 取消。拖拽期间面板滑出右边、半透明且不挡指针（够得着下面的格子），放下 / 取消后复位；每个展开的格子多一行「＋ 新建任务」，放上去变成行内输入框（预填窗口标题，≤64 字），Enter 经 `createTask` 建任务再改挂到它，Esc 取消。
  成功后提示「已改归属 → …」、卡片换目标与颜色、目标任务行闪一下；失败提示「没改成：detail」。改归属只有人能做，**不是计时**（不发 `honeycomb:timer-changed`）。

补充（2026-10-23 复审）：「全部批准」在那份报告有条目处于撤销窗口时禁用（提示「等撤销倒计时结束」）；历史先读今天、按响应的 `date`（v2.37）推昨天，不按浏览器时区；
输入法选字的回车不提交；新建任务输入框失焦即取消（请求在路上除外）；任务建好但改归属失败时如实说「任务已建好」，重试 = 把卡拖到那条新任务上；
拖拽可被 Esc / 窗口失焦 / 标签页转后台取消；触摸要长按 ~300ms 才开始拖；标签页转后台时把待发的一条接一条发出（新项目提议在前），pagehide 时并行 keepalive；
取不到提议时留着上一份卡片。

保留的保证：未知 `changes.op` 不画也不抛；409 的 detail 如实显示；换展开 / 收起 / Esc 清掉高亮与幽灵；一个重试计时器；文字一律 textContent。

## 三级分类决策栏（2026-10-24，v0.4）

仓主 2026-10-24 批了草稿。`hex-tiers.js` / `hex-tiers.css`（接在 `hex-decide.*` 后面）。启动时读一次 `GET activity/settings`：**`tiers` 不是 `true`（含读取失败）→ 上节两栏面板一个字节不变**；
`true` → 同一个右上角浮层（同一个角标、同一个折叠键 `hive.decide.collapsed`）换成**一栏**，滑动 / 撤销计时 / 拖到格子 / 提示条都复用上节的。
按卡片类型分段，顺序固定，没有内容的段不显示，每段标题带计数（角标 = 前四段之和）：

1. **AI 问题**：`GET activity/questions?status=pending` 的待回答问题（取代 proposals 里的 `kind: "question"`；端点 404 / 读取失败 = 不显示）。
   卡片：小标签「AI 提问」、标题、说明、时间段（有 `from` / `to` 时）；每个选项一个按钮，带目标的选项在标签下写出确切去向「↓ 项目 - 任务」和要记的时间段；`allowText` 时有文字输入与「提交」；「撤下」。
   点选项 / 提交 = `POST questions/{id}/answer {option}` / `{text}`：成功 → 卡片移除并提示（`answer.recorded` 为真 =「已记到 X」，否则「已回答」）；失败 → 如实提示后端 detail，卡片留着。
   「撤下」走上节同一个 5 秒撤销窗口，到点才发 `POST questions/{id}/dismiss`。标题、说明、选项文字都来自 AI，一律 `textContent`。
   今天的秘书报告（`GET activity/secretary-reports`）有的话，作为一张折叠卡放在本段最上面：默认只露前三行，「展开」/「收起」；不计数，只有它时本段也显示。
2. **待分类窗口块**：`GET activity/blocks` 里 `status: "pending"` 的块，以及 `generic` 且 `parts` 为空的块（带「通用窗口」徽标）。大字块名；第二行「↓ 项目 - 任务」按目标项目着色，没有目标「↓ ？」灰色；
   一行小字 =「AI：说明 · 时长 · N 段」。建议目标 / 说明来自同一块名、同一天的 `blockAction` 提议（`action: "assign"` 给目标，别的 action 只贡献 `reason`）；「改去向」选出来的优先。
   按钮：拆分、标为通用窗口（通用窗口上是「取消通用窗口」）、改去向（原生下拉，项目分组、含「项目 - 未分类」），有目标时另有「批准」。
   **左滑 = 批准当前去向**：立刻 `blocks/assign {date, blockKey, ifSeq, taskId | projectId, remember: true}`（通用窗口 `remember: false`）；没有目标 = 弹回并提示「先选去向」。
   **右滑 = 忽略**：`blocks/ignore {…, forever: false}`。两者都在底部提示 5 秒「撤销」，撤销 = `blocks/reset`（带响应里的新 `seq`）。注意 reset 不删 `remember` 写下的第二级规则。
   409（`ifSeq` 冲突）= 重读块列表；该块仍待处理就用新 `seq` 自动重试一次，否则卡片按现状重画并提示「这一块刚被改过，已刷新」。同一块上一步没回来时不发第二个；撤销按「现在」的块 reset，失败提示「撤销失败」。块列表读取失败时隐藏块与历史两段，面板顶给一行「读取失败，稍后重试」。
   **拆分**：卡片原地展开，`GET blocks/records` 画时间条（每条记录一根竖条，宽度按时长），白色把手可拖（也可方向键），「再加一个拆分点」最多到 20 段，每段一个去向下拉，
   「确认拆分」→ `blocks/split {parts}`，`fromTs` / `toTs` 是 UTC 毫秒（首段 `fromTs` 与末段 `toTs` 填块里实际的首尾记录时刻，不用 null），拆分点落进无记录的空隙时吸附到记录边界，每段都要有记录、都要选去向；取消 = 收起。**标为通用窗口**：`blocks/generic {blockKey, generic}`，提示里可撤销。
3. **待批准规则**：proposals 里 `windowRule`（徽标「第一级 · 收纳」，`rule {app, title, block}`）与 `blockRule`（「第二级 · 去向」，`blockKey` + `taskId | projectId`）；`op: "delete"` 画大红。
   「批准」「忽略」= 既有 `proposals/{id}/approve|reject`，走上节的 5 秒撤销（左滑 / 右滑同）；「更正」= 立即 `approve` 带 `edit`：`blockRule` 为 `{taskId | projectId}`，`windowRule` 为 `{rule: {block}}`。
   proposals 响应里各键下带 `kind` 的数组一并摊平，按 `kind` 分；没有就不显示。
4. **待批准日程改动**：上节「待批准」栏的卡片原样（新项目 / 新任务 / 改名 / 移动 / 删除 / 忽略规则 / 报告条目），加 proposals 里 `sessionChanges[]`：
   大字原归属「项目 - 任务」，第二行「↓ 新归属」，小字「开始时刻 · 时长 · AI：说明」。批准 / 不要同上节；多一个「更正」= 立即 `approve` 带 `edit: {taskId | projectId}`。
5. **自动化分类历史**：今天 `status: "assigned"` 且 `via` 为 `rule` / `ai-direct` 的块，新的在前，先 5 条、「展开全部」。「撤销」= `blocks/reset`；卡片可拖到蜂巢里的任务行 / 格子改挂 = `blocks/assign {…, remember: false}`（拖拽 / 新建任务沿用上节）。

三级分类开着时不再读 `GET activity/auto`（历史来自窗口块）。颜色沿用：项目深浅色；灰 = 忽略 / 无目标；大红 = 删除类。

## 页面分工（设计意图，v0.3）

页面分工（设计意图，仓主 2026-09-30 定）：**计时页（ring）= 现在**——在跑的计时、全部泳道；
**任务 / 项目页（hive）= 未来**——计划；**新页「待批准」= 回顾与分析**——聊天、待确认的活动建议、检测程序设置、
回顾与分析（该页待建；聊天与待确认面板现住 ring，迁过去另起 PR）。泳道的历史某天视图将来可从「待批准」链过来，本版不要求。
本页是「未来」：泳道（`nexus-core.views.lanes.v1`）**不在本页画**，本页只经共享顶栏的计时芯片悬停看到精简预览
（`modules/nginx-docker` 契约「泳道预览」节），本模块零改动。
