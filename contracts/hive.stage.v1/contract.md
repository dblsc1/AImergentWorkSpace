# hive.stage.v1 —— 蜂巢新布局：左边代理，右边人

> **状态**：v1，仓主 2026-11-07 批准。中文名「蜂巢新布局」。批准后只增不改不删；不兼容的改动发 `hive.stage.v2`。
>
> - 提供方：`modules/hive`（页面）；`modules/nexus-core` v2.44（「三」「四」的端点）。
> - 依赖：`onboarding.v1`（显示名、未分类、补记）、`desktop.pet.v1`「三」（宠物包）、`agent.lane.v1`、nexus-core v2.16 `focus`、v2.17 `attend`。
>
> **仓主原话（2026-11-07，节选）**：「计时页面需要合到蜂巢页面里面……直接显示当前页面在对应项目边上，根本不用选」
> 「左边未分类的泳道改为对应的AI形象。在左上角踌躇，拖动pet到对应项目就能创建分类规则，不再显示其他的详细信息在蜂巢，点开再显示」
> 「蜂巢中心固定驻留聚焦agent的计时器，右上角固定驻留一个人的时钟计时器，形成左边agent，右边人的格局」
> 「3个dspet 一个龙娘围着蜂巢干活。再多就不显示」「类似于去掉选择，用位置代替列表。所以拖动是个重要功能」。

```yaml
provides:
  - id: hive.stage.v1
    summary: >
      蜂巢页成为主页面，计时页并入。中心：聚焦代理的计时环；右上角：人的时钟，点一下立刻计时，计时中拖到格子 = 记到该项目，停下还没记的变白条、拖白条到格子 = 记到该项目；气泡飞到人所在项目格子；
      格子边至多 4 只干活的宠物；左上角：没有去向的代理宠物，拖进格子 = POST agents/{runId}/place（可记成代理去向规则）；
      左下角：未分类窗口时间的桶，拖窗口块进格子 = blocks/assign。点格子出完成记录：左人（计时、对话），右代理，留 token 位置。
```

## 一、布局（规范性）

设计宽度 1280px；1024–1279 宽按比例缩小同一布局；< 1024（含手机）本版不支持，以后做列表。

| 位置 | 放什么 | 数据 |
|---|---|---|
| 中间 | 蜂巢：每个项目一个格子，按分区分组。**格子上只有项目名** | 任务树 |
| 蜂巢正中心 | 聚焦代理的计时环（代理名 + 本次已用时间） | 「二」1 |
| 右上角 | 人的时钟：今天已记时长 + 当前项目名；一个气泡停在人所在项目的格子旁；时钟下方是上下缓慢浮动的白条 | 「二」2、「三·1」 |
| 格子边 | 干活的宠物，至多 4 只 | 「二」3 |
| 左上角 | 没有去向的代理宠物，慢慢踱步 | 「二」4 |
| 左下角 | 「未分类」桶：`未分类 · 今天 X 小时 Y 分`（不足 1 分钟写 `未分类 · 今天 0 分`） | 「二」5 |

- 计时页（`/timer`）并入蜂巢页；旧地址 `302` 到 `/hive/`（临时跳转，保留以后改）。
- 决策栏照旧在页面上（只剩 AI 提议，见 `onboarding.v1`「八」）。

## 二、各部分的规则（规范性）

1. **中心计时环**：取 v2.17 `attend` 里人此刻在看的那条运行（`attend` 最后一条的 `until` ≥ 此刻 − 15 秒算此刻在看）；人没在看任何代理 → 今天最后看过的那条；今天没看过 → 空环。「今天」= NEXUS_TZ 本地日。
   人换着看别的代理时，环换成那条运行，过渡 ≤ 600ms。
2. **右上角时钟**：今天人的记录时长合计（手动计时 + 自动入账），读 `GET views/lanes`（今天）的 `human.sessions` 与 `human.running`，裁到今天后求和。v2.16 `focus` 认出项目时，气泡停在该项目格子旁；
   项目变了，气泡沿弧线飞到新格子（约 600ms）；`focus.state: "afk"` 或认不出时，气泡回到时钟上。
   **手动计时**（离开电脑的事用它）：
   - 点时钟 = 立刻开始计时（`POST timer/start-unplaced`，「三·1」），不用选项目；再点 = 停（既有 `POST timer/stop`）。
   - 计时中把时钟（或它的气泡）拖到某个格子 = 这次计时记到该项目（`POST timer/place`）。已在计时时又拖到另一格 = 改记到新格子，不新开一段。
   - 停下时还没记到项目的，变成一条白条，在右上角时钟下方上下缓慢浮动；拖白条到格子 = 记到该项目（`POST timer/unplaced/{id}/place`）。多条白条按时间排，至多显示 5 条，多的显示 `+N`。
   - 放到空白处 = 回原位，什么都不改。
3. **干活的宠物**：此刻在跑、挂了任务或项目的运行，每条一只，停在它项目格子的边上，播 `working` / `waiting` / `idle` 动作（`desktop.pet.v1`「一」的 mood）。
   - 动作由运行的 `phase` 定：`working` → `working`，`waiting_input` / `waiting_permission` → `waiting`，`idle`、`error`、失联 → `idle`。
   - 至多 4 只，按最近活动时间（`views/lanes` 的 `agents[].lastSeenAt`，为 `null` 取 `startAt`）从新到旧取，并列按 `runId` 升序；其余在蜂巢边显示一个 `+N`。
   - 用哪个宠物包：按运行的 `tool` 查「宠物包对照」（`desktop.pet.v1`「三」）；查不到用 `*` 对应的包（缺省 `deepseek`）。
   - 只显示宠物与名牌（`label` 或 `agent`）；明细在点开格子后看。
4. **左上角：没有去向的代理**：此刻在跑、`taskId` 与 `projectId` 都没有的运行，每条一只，在左上角区域慢慢随机走动；最多 4 只，多的显示 `+N`。
   走动由可注入的随机源与时钟驱动；位置始终在左上角区域内，每步位移 ≤ 速度 × 时间。
   拖一只进格子 = `POST agents/{runId}/place {projectId, remember: true}`（「三」）；成功后宠物飞到该格子边，提示「已记下：以后 <agent> 都算 <项目名>」，3 秒后消失。
   放到空白处 = 回原位，什么都不改。
5. **左下角桶**：今天（NEXUS_TZ 本地日）「未分类」窗口时间合计。点桶展开抽屉：`legible` 的块按秒数排，可拖；看不懂的按「浏览器（其他网站）」「终端（未认出）」「其他」合成灰色行，不能拖。
   拖块进格子 = `POST activity/blocks/assign {date, blockKey, ifSeq, projectId, remember: true}`；成功后块从抽屉消失、桶上时长减少，提示「已记下：以后 <displayName> 都算 <项目名>」。
6. **拖动**：原生 Pointer Events，鼠标、触控板都能拖。拖动中目标格子高亮；失败（`409` / `422` / 网络）时被拖物回原位并提示原因。

## 三、代理去向（规范性，nexus-core v2.44）

`POST /api/core/agents/{runId}/place`（只许人；带 Bearer 的设备令牌 `403`）

```jsonc
{ "taskId": "t_…" | "projectId": "p_…",   // 恰好一个
  "remember": true }                        // 缺省 true：同时写（或替换）一条代理去向规则
// 200 → { "runId": "run_…", "taskId": null, "projectId": "p_…", "rule": { "agent": "garden", "projectId": "p_…" } | null }
```

- 整条运行（从开始算）改挂到这个目标：追加事件 `agent.run.placed`（`data: {runId, taskId?, projectId?, at}`），投影按最后一条算（重建后一致）；读投影（`views/lanes`、`views/agent-time`、按天统计）按新目标重算这一条。已结束的运行也可以 place。运行不存在 `404`；目标不存在 `404`。给 `taskId` 时响应里的 `projectId` = 任务所在项目。
- **代理去向规则** `agent_place_rules {user, agent, taskId?, projectId?, createdAt}`，`(user, agent)` 唯一，至多 500 条（超了 `422`，什么都不改；替换已有的 `agent` 不算超）。规则的 `agent` 为空串 `422`。
  以后 `agents/start` **没给** `taskId` / `projectId` 时，服务端按 `agent` 完全相同查这张表，查到就挂上。给了的照旧以给的为准。规则的目标后来被删：当没有规则处理，运行不挂目标，不报错。
- `GET /api/core/agents/place-rules`（人与 MCP；设备令牌 `403`）→ `{items: [{agent, taskId, projectId, createdAt, broken}]}`，目标已删的 `broken: true`；`DELETE /api/core/agents/place-rules/{agent}`（只许人）删一条，不存在 `404`，已挂上的运行不动。`{agent}` 按 URL 百分号编码，服务端解码后精确比较。
- 进导出 / 恢复。

### 三·1 无项目计时与白条（规范性，nexus-core v2.44）

既有端点（nexus-core 契约「对外 API」）：`POST timer/start {taskId}`（必须带任务）、`POST timer/stop`、`POST timer/cancel`、`POST timer/backfill {taskId, startAt, durationSeconds}`、`POST planner/projects/{id}/unclassified → {taskId}`（项目的「未分类」桶，取或建）。
它们做不到「不带项目开始计时」「计时中改记到项目」「事后给一段计时补项目」，所以追加下列端点；**只许人**，设备令牌与 MCP `403`。

- `POST /api/core/timer/start-unplaced`（无体）→ `200 TimerOut`，`taskId: null`、`projectId: null`。已在计时 `409`。
- `POST /api/core/timer/place`
  ```jsonc
  { "taskId": "t_…" | "projectId": "p_…" }   // 恰好一个
  // 200 → TimerOut（目标已换成新的）
  ```
  把在跑的计时改记到该目标；`projectId` 记到该项目的「未分类」桶（取或建，同 `planner/projects/{id}/unclassified`）。在跑的计时已有目标时同样改记到新目标，不停、不新开一段。没在计时 `409`；目标不存在 `404`；两个都给或都不给 `422`。
- `POST timer/stop`（既有）：在跑的计时有目标 → 照旧写 `session.completed`；没有目标 → 不进台账，存一条白条 `timer_unplaced {user, id, startAt, endAt, createdAt}`。`TimerStopOut` 追加 `unplacedId`（有目标时为 `null`）。`timer/cancel`（既有）对无目标的计时同样是「不记录」。
- `GET /api/core/timer/unplaced` → `{items: [{id, startAt, endAt, durationSeconds}]}`，按 `startAt` 升序。
- `POST /api/core/timer/unplaced/{id}/place` `{taskId | projectId}`（恰好一个）：按白条的 `startAt` / `endAt` 写一条 `session.completed`（`source: "timer-backend"`，目标同 `timer/place`），删这条白条；`200 {recorded: true, taskId, projectId}`。白条不存在 `404`；目标不存在 `404`。允许与别的已记时间重叠（同 `timer/backfill`）。
- 白条进导出 / 恢复。

## 四、完成记录（规范性）

点格子弹出该项目的「完成记录」面板，可关。

- **左半边：人的时间**，两类，各一条时间线 + 合计，取今天（NEXUS_TZ 本地日），元素形状 `{from, to, kind}`：
  - 计时（`kind: "timer"`）：`GET views/lanes`（今天）`human.sessions` 里 `projectId` 命中该项目的段（`startAt` → `from`，`endAt` → `to`）；
  - 对话（`kind: "attend"`）：同一响应 `interactions` 里 `kind: "attend"`、`runId` 属于该项目下运行的条目（`at` → `from`，`until` → `to`）。
- **右半边：代理的时间**：该项目下每条运行一行：名字、`working` 秒数、当前状态；底部一格「token 花费」，本版显示「—」。
- 数据来自既有读端（`views/lanes`、`views/agent-time`）；本版不新增读端。

### 四·1 预留：代理用量（本版只定形状，不实现）

仓主 2026-11-07：「只留接口，包括 http 和 mcp 接口，先不做」。实现时按下面的形状，不另起名字：

- HTTP：`POST /api/core/agents/{runId}/usage {at, inputTokens, outputTokens, cacheTokens?, costMicros?, currency?}`（设备令牌 `report` 范围即可）。
- MCP：工具 `report_agent_usage`，参数同上加 `runId`。
- 事件：yq-event/v1 新增类型 `agent.usage`，`data` 同上（信封不变）。
- 读：「完成记录」右半边那一格读该项目下各运行的合计。

## 五、本版不做（有意的）

- < 1024 宽的屏幕（含手机；以后做列表）。
- 宠物之间的互动、宠物在格子间自由走动。
- 代理去向规则的前缀匹配（只认完全相同的 `agent`）。

## 六、变更记录

| 日期 | 版本 | 变更 |
|---|---|---|
| 2026-11-07 | v1 草案 | 首版，待仓主批准 |
| 2026-11-07 | v1 草案三 | 按 TDD 澄清（G-01–G-30）补齐边界；手动计时改为先计时后拖放 |
| 2026-11-07 | v1 | 仓主批准 |
