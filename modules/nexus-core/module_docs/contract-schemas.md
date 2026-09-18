# nexus-core · 响应体详解

> 从 `contract.md` 拆出（2026-08-02，主文件第二次触到单文件 500 行上限）。
>
> **这里是规范的一部分，不是附录。** 拆分只是按主题分文件，
> `contract.md` 的「对外 API」表是索引，本文件是那张表里每个出参的详细定义。
>
> **拆分原则**：按**主题**切，切口在语义边界上，不复制任何一条规则。
> 今天四次栽在「同一条规则两份副本」上——所以这里绝不重述 `contract.md` 的条款，
> 只展开它点名的响应体。要找规则去主文件，这里只有形状。

### `CurrentOut` — `GET /api/core/views/current`

```jsonc
{
  "running": true,                 // 必填 bool。false 时下面四个可为 null
  "zone":    { "id": "z_7f21a4", "key": "Z01", "name": "示例分区二" },   // null=无
  "project": { "id": "p_3c98de", "key": "Z01-P01", "name": "示例项目三",
               "totalSeconds": 50400,
               "shareOfPlan": 47.5 },                    // 见下「占比语义」
  "task":    { "id": "t_a1b2c3", "key": "Z01-P01-T01-1", "name": "示例任务三",
               "totalSeconds": 5400,
               "shareOfProject": 32.1 },
  "sessionStartAt": "2026-07-28T09:30:00+08:00"          // ISO8601 带时区，null=未在计时
}
```

#### 占比语义（**规范性，本版最重要的一条**）

`shareOfPlan` / `shareOfProject` 是 **0–100 的百分数**，不是 0–1 的小数。
允许一位小数。

**为什么必须在契约里钉死**：第一个消费方 `ring` 的入参校验是
「必须是 0 到 100 的数字」，取值范围不合就直接抛「数据格式错误」。
0.475 与 47.5 都是"合法的浮点数"，机器分不出谁对——**这种歧义不会在类型检查里暴露，
只会在联调时表现为一个查半天的运行时错误**。契约的价值就在这种地方。

口径：默认是**按事件聚合的实际值**（圆环随真实工作增长），不是 `plannedWeight` 计划值。
计划 vs 实际的偏差由 table / gantt 展示，不在本端。

空闲态（`running:false`）时四个字段全为 `null`，**不是 0** ——
0 会被圆环画成一个真实存在但为零的弧，`null` 才表示"没有当前任务"。

### `TreeOut` — `GET /api/core/views/tree`

```jsonc
{
  "zones": [
    { "id": "z_1", "key": "Z01", "name": "示例分区一", "color": "#ff9d45", "order": 0 }
  ],
  "projects": [
    { "id": "p_1", "key": "Z01-P01", "zoneId": "z_1", "name": "示例项目四",
      "status": "active",                      // active|done|archived
      "progress": 72,                          // 0–100 整数，见下
      "progressSource": "computed",            // computed|manual
      "deadline": "2026-08-04",                // = plan.end，日期或 null
      "tasks": [
        { "id": "t_1", "key": "Z01-P01-T01-1", "name": "晨跑 3 公里", "done": true,
          "kind": "normal", "flags": [],
          "plan": { "start": "2026-08-01", "end": "2026-08-05" },  // v1.2 新增，可为 null
          "dependsOn": ["t_0"] }                                    // v1.2 新增，默认 []
      ] }
  ]
}
```

- **`progress`**：0–100 整数。`progressSource=computed` 时由后端按「已完成任务数 / 任务总数」
  算出；`manual` 表示人工覆盖值，后端原样返回不重算（HANDOFF §11.1：
  progress 从手动值改为计算值，**保留手动覆盖**）。前端不自行计算，避免两处口径漂移。
- **`deadline`**：`projects.plan.end` 的投影，`YYYY-MM-DD`。无计划则 `null`。
- **`includeEphemeral`**：默认 `false`，即**默认过滤 `kind="ephemeral"` 的临时任务**
  （HANDOFF §5.5）。前端可传 `true` 显示（对应"显示临时任务"开关）。
- **`flags`**：随任务返回，前端只读不解释。**贴纸决定数据性质、代码决定系统行为**，
  前端两者都不碰。
- **`plan`/`dependsOn`（v1.2）**：与 `planner.crud.v1` 的 `TaskOut.plan`/`dependsOn`
  同形状，**只读附带**——本端不做任何校验或改写，写路径仍只有
  `PATCH /api/core/planner/tasks/{id}`。**为什么现在才补**：v1.1 给 `TaskOut`
  与 `views.gantt.v1` 都加了这两个字段，却漏了 `views.tree.v1`——三个读端里
  两个消费方（table 用 tree 渲染项目表、ring 用 tree 读前置状态）实际读的正是
  这一条，字段缺失在契约层面看不出来（`TreeOut` 的 `_Strict` 只拒绝多余字段，
  不检查"是不是该加的都加了"），直到 table 的 feature-detect 控件在联调时
  发现永远读不到 `dependsOn` 键。**教训**：加字段时要过一遍**全部**读取同一
  实体的端点，不能改完一个就当作那批字段"已经加完了"。

#### 与其他两条读端的对照（v1.2 新增，防止再漏第三处）

| 读端 | 任务节点带 `plan`/`dependsOn` | 用途 |
|---|---|---|
| `views.current.v1` | 不带（只有 `id`/`key`/`name`/累计量，见 `CurrentOut`） | 当前计时状态，不需要排期信息 |
| `views.tree.v1` | **带**（v1.2 补） | table 渲染、ring 读前置状态 |
| `views.gantt.v1` | 带（v1.1，任务层） | 甘特画任务条与依赖箭头 |
| `planner.crud.v1` | 带（v1.1，`TaskOut`） | 排期/依赖的**唯一写入口**读写两侧 |

`views.current.v1` 不带是**有意的**（占比/累计量场景用不到排期），不是又漏了一处——
以后再加任务级字段，先查这张表决定该不该进 `current`，不要机械地"四端点同步"。

#### 不进本契约的字段（**有意的，不是遗漏**）

| 前端现在有 | 为什么不进 API |
|---|---|
| 项目级 `color` | 表现层。**由前端按 `zoneId` 从 zone.color 派生**——同一分区下的项目视觉归属一致，这是设计意图；让后端逐项目存颜色反而会让它漂移 |
| 项目 `icon` | 纯装饰，无业务语义。品牌与视觉外置，不进业务数据 |

`zones[].color` 保留在 API 内，因为它在 HANDOFF §6 的 `zones` 集合里就是持久化字段
（分区配色是用户设定，不是主题）。

### `IngestOut` — `POST /api/core/events`

请求体：单条信封对象，或信封对象数组（批量）。信封定义见 `contracts/yq-event-v1.md`，
**本文件不复述字段**——复述就会两处漂移。

```jsonc
{
  "accepted":  2,        // 本次真正落库的条数
  "duplicate": 1,        // 因防重键命中而被忽略的条数（不是错误）
  "rejected":  [         // 校验不过的，逐条给原因；其余照常落库
    { "index": 3, "reason": "缺少必填字段 dedupeKey" }
  ]
}
```

**四条规范性语义**：

1. **防重按 `dedupeKey`，不是 `id`。** 唯一约束 `(user, source, dedupeKey)`。
   同一件事重试提交时 `id` 可以不同、`dedupeKey` 必须相同。
   **按 `id` 判重 = 防重失效**，重试会重复落库、重复长树。
2. **重复不是错误。** 命中防重键的条目计入 `duplicate` 并返回 **200**，不是 4xx——
   客户端重试是正常行为，把它判成错误会逼客户端去猜"到底成没成"。
3. **`recordedAt` 由服务端盖章。** 客户端填了也**覆盖**。`time` 用客户端给的（可以是过去）。
4. **部分失败不整批回滚。** 校验不过的进 `rejected` 数组，合法的照常落库。
   批量导入里一条坏数据不该让另外 99 条白跑。

**落库成功后，同请求内**按 `projector/registry.py` 的 DISPATCH 表同步更新投影，
再返回 200。handler 幂等、只写自己的投影集合、**禁止发新事件**。

### `TimerOut` / `TimerStopOut` — `POST /api/core/timer/start|stop`

```jsonc
// start 请求 {"taskId":"t_45"}   响应：
{ "running": true, "taskId": "t_45", "startAt": "2026-07-30T09:30:00+08:00" }

// stop 无请求体，响应：
{ "running": false, "event": { "id": "evt_...", "dedupeKey": "timer:sess_...",
                               "type": "session.completed" } }
```

- **start 自动关闭上一个未结束的 session**（先 stop 再 start），
  不返回错误——用户点"开始"时的意图是明确的，让他先去手动停上一个是无谓摩擦。
- **stop = 删 `timer_state` 本条 + 组装一条 `session.completed` 投进事件入口**。
  **不许绕过 `POST /events` 的校验与防重直接写 `events` 集合**——
  绕过一次，那条事件就没过信封校验，重放时会炸在一个查不出来源的地方。
- **stop 时没有在跑的计时** → 返回 200 且 `running:false`，**不报错**（幂等）。
- `dedupeKey` 由 timer 用 session 标识生成，**必须稳定**：同一段计时重复 stop 只产生一条事件。

### `TimerBackfillIn` / `TimerBackfillOut` — `POST /api/core/timer/backfill`（v1.8）

规范性条款（信封字段表、拒绝规则、与 `timer_state` 的关系、零投影改动）见 `contract.md`
「补登」节，那里是唯一事实。这里只给形状，不重述规则。

```jsonc
// TimerBackfillIn
{
  "taskId": "t_a1b2c3",
  "startAt": "2026-08-18T14:30:00+08:00",   // 必须带时区偏移，见 contract.md 拒绝规则
  "durationSeconds": 5400                    // 正整数，≤86400
}

// TimerBackfillOut
{
  "recorded": true,     // 是否有新事件落库；duplicate=true 时也是 true（幂等语义同 IngestOut）
  "duplicate": false,   // 命中防重键——前端据此显示「这段已经补过了」，不是「已记录」
  "date": "2026-08-18", // 服务端算好的归日结果（NEXUS_TZ），前端不再自己算
  "event": { "id": "evt_...", "dedupeKey": "backfill:...", "type": "session.completed" }
}
```

- `recorded`/`duplicate` 与 `IngestOut` 的 `accepted`/`duplicate` 是同一种语义在单条
  场景下的翻译：一条请求要么落一条新事件（`recorded:true, duplicate:false`），要么命中
  防重（`recorded:true, duplicate:true`，`event` 回显的是**原来那条**），要么被拒绝规则
  拦下（4xx，见 `contract.md`「补登」节「拒绝规则」表，走「错误响应形状」节的 `{"detail": ...}`）。
  **没有"部分成功"**——单条请求只对应一条事件，不像 `POST /api/core/events` 那样批量。
- `event` 字段与 `TimerStopOut.event` 同形状，前端可复用同一段渲染/展示逻辑。
- **`duplicate`（规范性）**：`true` 时，本次请求**没有新事件落库**——`event` 字段回显的
  是**上一次那条**，不是本次新建的。消费方必须把这层语义表达给用户：「这段已经补过
  了」，**绝不许显示成「已记录」**（那会让用户以为刚才那次点击生了效，实际上这次
  什么都没发生，落库的是上一次）。措辞与理由的唯一事实见 `contract.md`「补登」节
  「请求 / 响应形状」小节；只读本文件、不读那段散文的实现者，**这条注解就是防漏点**。

### `NextActionsOut` — `GET /api/core/views/next-actions`（v1.5，F-TODO-1）

字段语义、分类判据、环防御与排序规则见 `contract.md`「下一步行动读端」节
（规范性条款只住那一处）。这里只补一条读的细节：

- `actionable`/`waiting` 两个数组都用同一个 `NextActionTask` 形状，唯一区别是
  `waiting` 里的 `blockedBy` 非空——**不为 `actionable` 单独定义一份"缺
  `blockedBy`"的窄形状**，前端两个列表可以用同一个渲染组件。
- `zones` 只包含**至少有一条 `actionable`/`waiting` 的分区**；全空的分区不
  出现（不是"分区没有待办区就是错误"，只是没有可展示内容）。

### `ReviewOut` — `GET /api/core/views/review`（v1.5，F-REVIEW-1）

四块聚合的口径见 `contract.md`「每周回顾读端」节。补充两点实现细节：

- `planVsActual` 是**全部项目**，不只是"本周有计划"的项目——`scheduledThisWeek`
  是给前端筛选/高亮用的布尔标记，后端不做过滤，理由同 `views/gantt` 返回全部
  项目那条（新建但还没排期的项目也该在回顾里"看得见"，不是等排了期才出现）。
- `staleTasks.lastActiveDate` 是 `proj_daily_stats` 里该 `taskId` 的最大 `date`；
  从未出现过任何一行则为 `null`（不是"从很久以前"，是"压根没有过"，两者在
  UI 上应该有不同文案）。

### `AuditOut` — `GET /api/core/planner/audit`（v1.6，F-ACTOR-2）

记录形状、三种 `outcome` 的语义、append-only 的保证方式见 `contract.md`
「planner 审计流水」节（规范性条款只住那一处）。这里补三条读的细节：

- **排序看 `seq` 不看 `at`**：`at` 是 ISO 字符串、同毫秒可以有多条，
  按它排出来的"顺序"在批量写场景下是错的。`seq` 由 `counters` 集合原子自增，
  与 `name_registry` 的发号是同一套机制（同一个不变量：号只增、不重、不回收）。
- `objectId` 在 `op="create"` 且 `outcome!="applied"` 时是 `null`——
  那一刻对象还没有 id。前端按 `null` 分支渲染，别指望它总有值。
- `changes` 是**摘要**：长字符串截断（尾部 `…`）、长数组截断（末位 `"…"`）、
  键名里带 `token`/`secret`/`password` 的值替换成 `<已脱敏>`。
  想要完整快照请查对象本身，审计不承担"回滚数据源"的职责。

### planner 只读面（本版只读，不含 CRUD）

`views/current` 要返回 `zone.name` / `project.name` / `task.name`，
但**可变实体的显示名一律不进事件**，要求"读取时按 ID 现查"。
所以本版必须提供 planner 集合的**只读查询**，否则金链路只能永远返回空闲态。

- 供给：`zones` / `projects` / `tasks` 三个集合的按 ID 取名字。
- **本版不提供任何写侧 CRUD**（增删改留给切片 2）。
- 种子数据从哪来：本版允许一次性 seed 脚本落 `code/backend/`，
  **不得**在 `views/` 里硬编码名字——那会让前端看到的名字与库里的漂移。

