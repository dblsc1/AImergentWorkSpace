# nexus-core · 对外接口契约

> 本文件是 nexus-core 对外行为的**唯一事实**。改动前先想清楚为什么要改，
> **先改这里、再改代码**，顺序不可颠倒。
>
> **本版范围（v2.0）**：**按租户分数据**。同一套 nexus-core 可以同时装多份彼此看不见的
> 数据（多个孩子、多个家庭成员）。租户只从网关转来的 `X-Nexus-Tenant` 头来（网关用认证
> 服务 verify 的结果覆盖客户端自带的同名头，见 `contracts/gateway.v1`）；缺头＝单人默认
> 租户 `u_local`，**单人部署的行为与数据和 v1.x 完全一样**。多用户部署打开
> `NEXUS_TENANT_STRICT=1`：缺头即 401，绝不静默落到 `u_local`。见「按租户分数据」节。
> 主版本号升到 2 是因为唯一约束从「全局 id」变成「租户内 id」；HTTP 接口一个字段都没减。
>
> v1.9：`GET /api/core/export` 产出的快照此前没有任何端点能吃
> 回去（喂给 import 会被三层拒绝，而那三条拒绝各守一件实事，一条都不该放宽）。
> 新增 `nexus-core.restore.v1`（`POST /api/core/restore`，见「快照恢复」节）：
> 吃 export 的**逐字节**输出，**只对空实例开放**（非空 409「恢复通道，不是合并
> 通道」）；id 原样保留、`events` 原样落台账、`projections` 不落库而是落完台账
> 现场重建；两段式同 import（dry-run + checksum）；整次恢复算一次高风险写，经
> `guard.run_write` 设防并留一条审计。import 的任何行为不变，属追加式变更。
>
> v1.8：人类需求「计时器和网页前端都加个补登功能，
> 用于完成了但没计时的情况」——**契约已实现并通过验证**（实现 commit
> `69da9d6`，12 条单测全绿；验证 commit `f308bdf`，4 条整合测试全绿，结论
> approved）。新增 `timer.v1` 的 `POST /api/core/timer/backfill`（见下「补登」节）：表单粒度＝
> 任务+日期+开始时刻+时长，必须挂具体任务；入口 `ring`+`hive`（`gantt` 保持
> 只读不加写入面）。信封 `data` 与 `stop()` 完全同形，**零投影改动**是本版
> 的规范性约束——若实现时发现必须改投影，说明设计有错，应停下重新设计，
> 不许绕过这条约束硬做。
> `source` 固定 `manual-backfill`（区分「表测出来的」与「人回忆的」两种证据
> 强度）；`dedupeKey` 为内容派生键并**必须归一化为 UTC**，否则同一时刻的两种
> ISO 写法会算出两个键、防重失效。补登不碰 `timer_state`、允许墙钟重叠。
>
> v1.7：把「导出 JSON → 编辑器里改 → 导回去直接
> 生效」这个外部编辑通道接完整。新增 `nexus-core.planner.import.v1`
> （`POST /api/core/import`，见「JSON 一键导入编辑」节），吃**与 export 同
> 形状**的 JSON：**默认 dry-run**（零写入，只回 diff 计划 + 计划的
> checksum）；真正写入必须带上 dry-run 那份计划的 checksum，服务端拿同一份
> payload 对**当前**库重新算一遍、checksum 不一致就拒——防止拿一份过时的
> 计划应用（库在这期间可能已经变了）；`events`/`projections` 出现在请求体
> 里（哪怕是 `null`）直接 **400** 并点名，不许静默忽略；删除需要显式
> `allowDelete:true`，缺省不删。**这是 v1.6「低风险直接做 / 高风险先提议后
> 确认」两段式设防的同一个形状**——import 走人的路径（`actor=human`），复用
> `guard.run_write` 逐条执行，AI 凭据自报 human 一样会被判成伪装而 403，
> 没有为这个新端点另开一套更宽松的口子。**该版红线不变**：import 只碰
> planner（zones/projects/tasks），`events` 台账一个字节都不能被它写——这是
> 判据本身而不是靠自觉，见该节实现细节。
>
> v1.6 及之前的历史范围（切片 1「金链路」写入侧、J10 标识三分、切片 2
> planner 写侧 CRUD、统一 CRUD 入口、档案读端、日界与时区、投影重建、甘特
> 读端（项目层 + v1.1 新增任务层）、健康检查暴露库名、任务级排期与依赖、
> 只读全量导出端点、任务 PATCH 补 `plannedWeight`、v1.5 的收件箱 `p_inbox`
> 禁删 / 下一步行动读端 / 每周回顾读端 / 写者字段 `actor`→`lastWriter`、
> v1.6 的 actor 来源区分与高风险二次设防（F-ACTOR-1 波2 / F-API-3）+
> planner 审计流水（F-ACTOR-2））均已落地，不再赘述，历史见 `contract-changelog.md`。
> `proposals`、`/views/garden` **仍不在本版承诺内**，等对应切片再进契约。
>
> **事件信封不在本文件定义**——它是跨模块的开放标准，唯一事实是仓根
> `contracts/yq-event-v1.md`。本文件只说本模块**怎么收、怎么存、怎么派生**。

## 契约索引声明（provides / consumes）

```yaml
provides:
  - id: nexus-core.views.current.v1
    summary: 当前正在计时的项目/任务及其占比与累计时长（贡献圆环读端）
    status: 已实现，已验证
  - id: nexus-core.views.tree.v1
    summary: 分区→项目→任务的一次性全量树（项目表/控制台读端）；v1.2 起任务节点补
      plan/dependsOn（只读附带，与 planner.crud.v1 的 TaskOut 同形状），补齐 v1.1
      漏掉的这一个读端
    status: 已实现，已验证
  - id: nexus-core.events.ingest.v1
    summary: 事件唯一写入口（校验信封 → 防重 → 落库 → 派生投影）
    status: 已实现，已验证
  - id: nexus-core.events.read.v1
    summary: 档案读端 GET /api/core/events（v0.6）——按 type/from/to 过滤、按 time 倒序、
      分页（默认 100、上限 1000），事件信封原样，不 join 名字。端点 v0.6 起就在，
      本条是索引补登（hive 早已登记消费它，安装器按索引解依赖时找不到提供方）
    status: 已实现，已验证
  - id: nexus-core.timer.v1
    summary: 计时开始/结束；stop 时组装 session.completed 投进事件入口；v1.8 起加
      POST /api/core/timer/backfill（补登「完成了但没计时」的历史段），信封 data 形状
      与 stop 完全同形（零投影改动），source 固定 manual-backfill 与实时计时区分证据强度，
      dedupeKey 内容派生并归一化为 UTC；不碰 timer_state，允许与在跑计时的墙钟重叠
    status: start/stop 已实现，已验证；backfill 已实现（v1.8，
      commit `69da9d6`），已验证（commit `f308bdf`）
  - id: nexus-core.planner.crud.v1
    summary: zones/projects/tasks CRUD（统一入口 /api/core/planner/{type}）；删除拒绝级联返 409；
      id 永不复用；v1.1 起任务对象增 plan/dependsOn（排期与前置依赖，纯计划态不进事件）；
      v1.4 起任务 PATCH 支持 plannedWeight（与 ProjectUpdate 对称）；v1.5 起三类对象的
      建/改均可带 actor（human/ai，缺省 human），落库为 lastWriter 字段；v1.5 起
      well-known `p_inbox` 项目禁删（409，见「收件箱」节，F-INBOX-1）；**v1.6 起
      actor 由请求来源判定**（凭据头 X-Nexus-Client-Token，见「actor 来源区分与高风险
      二次设防」节），且**高风险写（DELETE / 改 projectId、zoneId / 改 plan）带
      actor=ai 一律 403**（F-API-3 二次设防）
    status: 已实现，已验证
  - id: nexus-core.planner.audit.v1
    summary: planner 写操作的 append-only 审计流水（独立集合 planner_audit，**不进 events**）
      + 只读端点 GET /api/core/planner/audit；记录 seq/at/actor/source/op/objectType/
      objectId/outcome/changes，兼任「AI 批量写崩在半路，查它做到第几步」的追溯手段
    status: 已实现（v1.6，F-ACTOR-2），待验证
  - id: nexus-core.views.gantt.v1
    summary: 计划（plan）与事实（proj_daily_stats）叠加的甘特读端；v1.1 起项目层之下嵌套
      任务层，任务自带 plan/dependsOn/按天 actual（此前只有项目层，本条目缺失于索引，本版补记）
    status: 已实现，已验证
  - id: nexus-core.views.export.v1
    summary: 只读全量导出（zones/projects/tasks/events 原样 + 两张投影 + exportedAt）；
      纯聚合既有只读函数，不新增数据、不碰任何写路径
    status: 已实现，已验证
  - id: nexus-core.views.next-actions.v1
    summary: GTD 待办区规则读端（F-TODO-1）——可做/等待分类、过期琥珀标、按 zone 分组、
      按 plannedWeight 降序 + 今天到期/过期置顶排序；含依赖图环检测防御（历史脏数据
      撞环时降级为「可做」+ 警示标，绝不死循环）；只读实时算，不物化投影
    status: 已实现，已验证
  - id: nexus-core.views.review.v1
    summary: GTD 每周回顾聚合读端（F-REVIEW-1）——本周计划vs事实、过期项目、久未动任务、
      inbox 待清空计数；全部现有 events/planner/投影重新聚合，零新模型
    status: 已实现，已验证
  - id: nexus-core.planner.import.v1
    summary: JSON 一键导入编辑（POST /api/core/import），吃与 export 同形状的 JSON；
      默认 dry-run 零写入只回 diff 计划 + checksum，apply 须带上该 checksum 且服务端
      对当前库重算比对不上就拒（防过时计划）；events/projections 出现在请求体里
      即 400 点名；删除需显式 allowDelete，缺省不删；actor 固定走人的路径，复用
      guard.run_write 逐条执行，AI 凭据自报 human 同样 403（与 planner.crud.v1 的
      高风险二次设防同一条防线）
    status: 已实现（v1.7），待验证
  - id: nexus-core.restore.v1
    summary: 快照恢复（POST /api/core/restore），吃 GET /api/core/export 的逐字节输出；
      只对空实例开放（非空 409）；id 原样保留、events 原样落台账（全系统唯一绕开 ingest
      直写台账的路径）、projections 不落库而是现场重建、「名字 → 号」登记表从 key 反推；
      两段式 dry-run + checksum；整次恢复算一次高风险写，经 guard.run_write 设防留痕
    status: 已实现（v1.9），待验证
  - id: nexus-core.tenancy.v1
    summary: 按租户分数据（v2.0）——租户取自网关转来的 X-Nexus-Tenant（格式
      ^[A-Za-z0-9_.:-]{1,64}$，不合规 400），缺头为 u_local（单人部署零变化）；
      NEXUS_TENANT_STRICT=1 时缺头 401；全部读写按租户隔离，事件 user 服务端盖章；
      唯一约束为 (user, id)
    status: 已实现（v2.0），待验证
consumes:
  - id: yq-event/v1
    contract: ../../contracts/yq-event.v1/contract.md
    purpose: 事件信封的唯一事实。本模块是它的第一个实现者，不得偏离
```

## 对外 API

| 方法 | 路径 | 入参 | 出参 | 状态 |
|---|---|---|---|---|
| GET | `/api/core/health` | 无 | `{"status":"ok","db":"<库名>","actorGuard":"strict"\|"lenient"}` | ✅ 已实现（v1.0 加 `db`，v1.6 加 `actorGuard`）|
| POST | `/api/core/events` | 单条或数组，`yq-event/v1` 信封 | `IngestOut` | ✅ 已实现 |
| POST | `/api/core/timer/start` | `{taskId}` | `TimerOut` | ✅ 已实现 |
| POST | `/api/core/timer/stop` | 无 | `TimerStopOut` | ✅ 已实现 |
| POST | `/api/core/timer/backfill` | `TimerBackfillIn`（见下「补登」节） | `TimerBackfillOut` | ✅ 已实现（v1.8，`69da9d6`，已验证 `f308bdf`） |
| GET | `/api/core/events` | `?type&from&to&limit&offset` | `{total, items[]}` | ✅ 已实现（v0.6 档案读端）|
| GET | `/api/core/views/current` | 无 | `CurrentOut` | ✅ 已实现（含 `key` 字段） |
| GET | `/api/core/views/tree` | `?includeEphemeral=false` | `TreeOut` | ✅ 已实现（含 `key` 字段） |
| GET | `/api/core/export` | 无 | `ExportOut`（见下「只读全量导出」节） | ✅ 已实现（v1.3） |
| GET | `/api/core/views/next-actions` | 无 | `NextActionsOut`（见下「下一步行动读端」节） | ✅ 已实现（v1.5） |
| GET | `/api/core/views/review` | 无 | `ReviewOut`（见下「每周回顾读端」节） | ✅ 已实现（v1.5） |
| GET | `/api/core/planner/audit` | `?limit&objectId&actor&outcome` | `AuditOut`（见下「planner 审计流水」节） | ✅ 已实现（v1.6） |
| POST | `/api/core/import` | `ImportRequest`（见下「JSON 一键导入编辑」节） | `ImportResultOut` | ✅ 已实现（v1.7） |
| POST | `/api/core/restore` | 请求体 = `GET /export` 原样；`?dryRun&checksum`（见下「快照恢复」节） | `RestoreResultOut` | ✅ 已实现（v1.9） |
| ~~GET~~ | ~~`/api/core/zones`~~ | 无 | `[ZoneOut]` | **v0.6 已删除**，改走 `/api/core/planner/{type}` |
| ~~POST~~ | ~~`/api/core/zones`~~ | `{name, color?, order?}` | `ZoneOut` | **v0.6 已删除**，改走 `/api/core/planner/{type}` |
| ~~PATCH~~ | ~~`/api/core/zones/{id}`~~ | `{name?, color?, order?}` | `ZoneOut` | **v0.6 已删除**，改走 `/api/core/planner/{type}` |
| ~~DELETE~~ | ~~`/api/core/zones/{id}`~~ | 无 | `204` / `409` | **v0.6 已删除**，改走 `/api/core/planner/{type}` |
| ~~GET~~ | ~~`/api/core/projects`~~ | `?zoneId=` | `[ProjectOut]` | **v0.6 已删除**，改走 `/api/core/planner/{type}` |
| ~~POST~~ | ~~`/api/core/projects`~~ | `{zoneId, name, plannedWeight?, plan?}` | `ProjectOut` | **v0.6 已删除**，改走 `/api/core/planner/{type}` |
| ~~PATCH~~ | ~~`/api/core/projects/{id}`~~ | `{name?, zoneId?, status?, plannedWeight?, plan?}` | `ProjectOut` | **v0.6 已删除**，改走 `/api/core/planner/{type}` |
| ~~DELETE~~ | ~~`/api/core/projects/{id}`~~ | 无 | `204` / `409` | **v0.6 已删除**，改走 `/api/core/planner/{type}` |
| ~~GET~~ | ~~`/api/core/tasks`~~ | `?projectId=` | `[TaskOut]` | **v0.6 已删除**，改走 `/api/core/planner/{type}` |
| ~~POST~~ | ~~`/api/core/tasks`~~ | `{projectId, name, kind?, flags?, plannedWeight?}` | `TaskOut` | **v0.6 已删除**，改走 `/api/core/planner/{type}` |
| ~~PATCH~~ | ~~`/api/core/tasks/{id}`~~ | `{name?, projectId?, done?, kind?, flags?, plannedWeight?}` | `TaskOut` | **v0.6 已删除**，改走 `/api/core/planner/{type}`。**v1.4 补 `plannedWeight?`**——`ProjectUpdate` 一直有，任务侧当年切片漏加，见「校验」节 |
| ~~DELETE~~ | ~~`/api/core/tasks/{id}`~~ | 无 | `204` / `409` | **v0.6 已删除**，改走 `/api/core/planner/{type}` |

响应对应当前租户（v2.0，见「按租户分数据」）；单人部署即 `user="u_local"`（HANDOFF §6 兼容锚点）。
**省认证，不省字段**——租户只换注入来源（网关头）；v1.x 老文档无 `user` 字段的读时兼容与回填见「按租户分数据」。


**各出参的详细定义（字段、语义、边界）见 `module_docs/contract-schemas.md`。**
它是本契约的一部分，不是附录——上表是索引，那里是展开。

## 健康检查暴露库名（规范性 · v1.0，v1.6 加 `actorGuard`，v2.0 加 `tenantGuard`）

`GET /api/core/health` 返回
`{"status":"ok","db":"<当前库名>","actorGuard":"strict"|"lenient","tenantGuard":"strict"|"lenient"}`。

`tenantGuard`（v2.0）：`strict` 表示缺 `X-Nexus-Tenant` 的请求一律 401；`lenient`（默认）
表示缺头按单人默认租户 `u_local` 处理。多用户部署忘了开严格模式，从这里一眼看出。
健康检查本身**不看租户**：它是容器健康检查，直连、不经网关。

`actorGuard` 是**本进程的 actor 设防姿态**（v1.6）：`strict` 表示高风险写必须
携带有效的人路径凭据，`lenient` 表示未携带任何凭据的调用方仍按 `human` 放行
（默认，见「actor 来源区分与高风险二次设防」节）。**加它的理由与 `db` 完全同款**：
姿态是进程级配置，跨进程只能靠字段暴露；不暴露就只能靠"我记得我配过"，
而"我记得"在本项目已经翻过两次车。**新增字段不是破坏性变更**——消费方按
必需字段判，不做整体相等（只断言必需字段是否存在且合法，不做整体相等——
整体相等会让任何新增字段都变成假红）。

### 为什么加这个字段

**让「我打的是不是生产库」变成机器可判的事实，而不是靠人记得。**

本项目今天两次栽在「默认值指向生产」上：
1. 单测 `conftest.py` 的库名默认值 → 传了真库名，62 个测试清 62 遍，
   **用户计时历史全丢**
2. E2E `conftest.py` 的网关地址默认值 → 测试把任务挂到用户真实项目下、遗留孤儿分区

第一个的护栏是「库名不以 `_test` 结尾就 die」——**有效，但只在进程内可判**。
E2E 是跨进程打 HTTP 的，它**看不见对端的库名**，所以同一道护栏套不上。
加这个字段就套得上了：**两处用同一个判据形状**，不再各写各的。

### 安全性

`/api/core/*` 全部在 `auth_request` 之后，未登录拿不到。
库名对已登录用户不算敏感（他本来就能读写全部数据）。

## 日界与时区（规范性 · v0.9）

**「一天」按 `NEXUS_TZ` 指定的时区切，不是 UTC。**

| 用途 | 取值 |
|---|---|
| `proj_daily_stats.date` 的归日 | `data.startAt` 转到 `NEXUS_TZ` 后取日期 |
| `views/gantt` 的 `today` | `NEXUS_TZ` 下的今天 |

### 为什么必须显式配、不能默认 UTC

v0.8 实现用了 UTC，**内部自洽但与用户的墙钟不一致**：
UTC+8 的用户在本地 8 月 2 日 01:24 干活，会被记到 **8 月 1 日**，
`today` 也说 8 月 1 日。

对一个「记录今天学了多久」的产品，**凌晨 0–8 点的工作全被算进昨天**是实质性错误。
而且这类错**最难发现**——图不会看着崩（红线和柱子都用同一个错基准，彼此自洽），
只有用户对着自己的记忆纳闷时才会暴露。

`NEXUS_TZ` **必填、无默认值**（IANA 名，如 `Asia/Shanghai`）。
不给默认值是有意的：猜错时区会静默地把工作记到错误的日子，
而**数据一旦按错误的日界落库，之后每次统计都继承这个错**。
与本模块其他关键配置一致——缺了就 die，不猜。

### 存储侧不变

**事件本身仍存带时区的绝对时刻**（`startAt` / `time` 都是 ISO8601 带偏移）。
时区只影响**投影的归日**与 `today`，不改事实本身。
所以改 `NEXUS_TZ` 后**重放即可得到新日界下的正确投影**，历史事实一个字节不动。

## 投影重建（规范性 · v0.9）

**投影是事实的派生物，必须能从事实完全重建。**

```bash
.venv/bin/python -m app.modules.projector.rebuild [--only <投影名>]
```

### 什么时候必须重建

1. **新增投影时**——它错过了此前所有历史事实。
   v0.8 加 `proj_daily_stats` 时就撞上：库里 25 条事实，投影 0 条。
2. **改了归日/聚合口径时**（如本版改时区）。
3. 怀疑投影与事实不一致时。

### 硬约束

- **只读事实、只写投影。** 重建**绝不能**修改 `events` 集合——
  事实是 append-only 的唯一真相，重建是从它推导，不是反过来。
- **先清目标投影再重放**，否则会在已有计数上重复累加。
- **幂等**：连跑两次结果必须相同。
- **不发新事件**（handler 约束在重放路径同样成立）。

## 甘特读端（规范性 · v0.8，v1.1 补任务层）

`GET /api/core/views/gantt?from=&to=` —— **计划与事实两个图层**的叠加数据源。

```jsonc
{
  "projects": [
    { "id": "p_1", "key": "1-2", "name": "示例项目三",
      "plan":   { "start": "2026-08-01", "end": "2026-08-10" },   // null = 未排期
      "actual": [                                                  // 按天聚合，可为空数组
        { "date": "2026-08-01", "seconds": 12600 },
        { "date": "2026-08-02", "seconds": 3600  }
      ],
      "tasks": [                                     // v1.1 新增：任务层（O1，见下节）
        { "id": "t_1", "key": "1-2-3-1", "name": "背单词", "done": false,
          "plan": { "start": "2026-08-01", "end": "2026-08-05" },   // null = 未排期
          "dependsOn": ["t_0"],                        // 前置任务 id 列表，纯表达不排程
          "actual": [                                   // 按天聚合，可为空数组
            { "date": "2026-08-01", "seconds": 3600 }
          ] }
      ] }
  ],
  "today": "2026-08-02"        // 服务端的今天，前端画红线用
}
```

**任务层与项目层的 `actual` 不是同一份数字的两种切法之外的东西**：项目层 `actual`
仍是**该项目当天全部时长**（含没挂具体任务的事件，contract B5），任务层 `actual`
是**该任务当天的时长**——两者来自同一张 `proj_daily_stats`，项目层按 `(date)` 求和、
任务层按 `(date, taskId)` 分组，互相不冲突、不需要对账（任务层求和 ≤ 项目层同日数字，
差额就是「有项目无具体任务」的那部分事件）。

### 双图层语义（本节要害）

前端把它画成**两个可独立开关的图层**，叠在同一行上：

| 图层 | 数据源 | 能不能改 |
|---|---|---|
| **计划** | `plan.start` / `plan.end` | **可拖拽改期** → `PATCH /api/core/planner/projects/{id}` |
| **事实** | `actual[]` 按天聚合 | **锁死，绝不可编辑** |

**事实为什么锁死**：它来自 append-only 的事实流水账。拖动它 = 改历史。
要修正只能**追加一条修正事件**，不能就地改。
UI 上这个区别必须一眼可见——不能让人以为拖了事实条就改了记录。

**红线 = `today`**。它把时间轴分成两半，而这个划分是有语义的：
**左边已发生（计划与事实可对照），右边未发生（只有计划）**。
`today` 由**服务端给**，不用前端的本地时钟——客户端时区/时钟不准会让红线飘，
而"今天"是判断"该干的干了没"的基准，不能各人一个答案。

### 为什么新增一张投影而不是给 project 加字段

「某天干了多久」现在**根本没有数据**：`proj_current` 只存累计总量，答不了时间维度。

**加投影不加字段**：`registry.py` 的 DISPATCH 表加一个
handler，写自己的 `proj_daily_stats` 集合。`current.handle` 不动、`views` 其余不动、
`planner` 不动——**现有代码零改动**。

存成 project 上的字段则有两个真相源（流水账 vs 字段），补录或投影失败就永久分叉。

### O1：任务级「某天多少分钟」为什么扩现有投影，不新开一张（v1.1）

PRD F-API-4 要「任务 × 日 → 分钟」。**答案是：这份数据从 v0.8 起就已经在
`proj_daily_stats` 里了**——它的唯一约束是 `(user, date, projectId, taskId)`，
`daily_stats.py` 的 handler 从落地那天起就按这四元组累加，`taskId` 从来不是
装饰性字段。v0.8 只是**读端**（`views/queries.py::get_gantt`）把它按 `projectId`
再汇总掉了，任务粒度被读丢了，不是没被算过。

**因此 v1.1 不新增 `proj_task_daily` 投影，只改读端**：`get_gantt` 在按
`projectId` 求项目层 `actual` 的同时，**再按 `(projectId, taskId)` 分组**一次，
挂到对应任务的 `tasks[].actual` 下。`registry.py` 的 DISPATCH 表、
`daily_stats.py` 的 handler、`proj_daily_stats` 的形状与索引**全部零改动**。

拒绝新开投影的理由：

1. **重复存储同一份事实**——`taskId` 已经在文档里，新投影会把它原样再抄一遍，
   多一个集合、多一条 DISPATCH 记录、多一处「万一两边算法漂移」的风险，
   换不来任何新数据。
2. **不需要重建**：因为没有新投影、没有改 DISPATCH、没有改归日口径，
   **生产库 `proj_daily_stats` 里已有的历史数据从写入那天起就带着正确的任务粒度**，
   本版不需要跑 `projector.rebuild`（对比 v0.9 加时区、v0.8 加这张投影本身时都
   必须重建——那两次是数据本身要变或投影是新的，这次数据一个字节都不用动）。
3. 契约「投影重建」的 `--only` 口径仍然覆盖它——`proj_daily_stats` 已经是
   `rebuild.py` 的 `_TARGETS` 之一，任务层数据是它的一部分，天然纳入，不用新增映射。

### `proj_daily_stats` 形状

```jsonc
{ "user": "u_local", "date": "2026-08-01",
  "projectId": "p_1", "taskId": "t_1", "seconds": 3600 }
```

按 `(user, date, projectId, taskId)` 唯一，handler 幂等累加。
**同一条事件重复派发不许重复计数**——防重已在 events 层做过，
但 handler 自身也必须幂等。

## 补登（规范性 · v1.8，backfill）

`POST /api/core/timer/backfill` —— 给「完成了但没计时」的工作补一条真实记录。
2026-08-19：ring 与 table 各加一个入口（gantt 保持只读，不加写入面），
表单粒度＝**任务 + 日期 + 开始时刻 + 时长**（人类已定口径，不是「日期＋时长」也不是
「起止两端」），且**必须挂具体任务**，不允许只挂项目。

### 请求 / 响应形状

```jsonc
// 请求 TimerBackfillIn
{
  "taskId": "t_a1b2c3",
  "startAt": "2026-08-18T14:30:00+08:00",
  "durationSeconds": 5400
}

// 响应 TimerBackfillOut
{
  "recorded": true,
  "duplicate": false,
  "date": "2026-08-18",
  "event": { "id": "evt_...", "dedupeKey": "backfill:...", "type": "session.completed" }
}
```

`date` 是**服务端算好的归日结果**回显给界面（同「日界与时区」节的 `NEXUS_TZ` 口径），
让用户看见「这段记到了 8-18」，而不是让前端再算一遍时区——两处算时区 = 迟早不一致。
`duplicate: true` 表示防重命中，界面要说「这段已经补过了」，**不能显示成「已记录」**，
那是骗用户——已经落库的是**上一次**那条，这一次什么都没发生。

### 服务端组装的信封（规范性）

| 字段 | 值 | 为什么 |
|---|---|---|
| `source` | **`"manual-backfill"`**，不是 `"timer-backend"` | ① 唯一约束是 `(user, source, dedupeKey)`，换 `source` 后补登键与实时计时键**结构上不可能撞**，两条独立的防重轨道；② 档案必须能分辨「表测出来的」和「人回忆的」——这是两种证据强度，混成一个 `source` 就永远分不开了。以后如果要对补登数据打折信任度、单独统计、或提示用户"这段是回忆填的"，全靠这个字段撑住，不靠翻 `dedupeKey` 前缀猜 |
| `dedupeKey` | `backfill:<taskId>:<startAt 归一化为 UTC ISO>:<durationSeconds>` | **内容派生的确定性键**，不用服务端发号、不用前端存状态。同一任务 + 同一开始时刻 + 同一时长重复提交 = 只落一条——双击、断网重试、用户以为没成功又点一次，全部幂等。**归一化必做**：`2026-08-18T14:30:00+08:00` 与 `2026-08-18T06:30:00Z` 指的是同一时刻，字符串却不同；不归一化就是"同一件事"算出两个不同的键，防重直接失效，用户能把同一段工作补两遍 |
| `time` | `startAt + durationSeconds`（会话结束时刻） | 与 `stop()` 的 `"time": now` 语义对齐——对 `session.completed` 而言 `time` 就是"这段会话结束的那一刻"，补登只是结束时刻不是当下，语义没变 |
| `subject` | zone/project/task **在服务端从 planner 硬取全链**，与 `start()` 同一套 | 归属断链（`taskId` 指向的任务已被删除、或其 `projectId`/`zoneId` 悬空）**当场响亮失败**（见下「拒绝规则」404），不留给投影去静默跳过——静默跳过会让这条事件在统计里凭空消失且无迹可查 |
| `data` | `{"durationSeconds": n, "startAt": "..."}` | **与 `stop()` 完全同形**，这是「零投影改动」的前提——见下节 |
| `flags` | `[]` | 不新增 flag。`source` 已经是判别位，再加一个 flag 表达同一件事是重复的契约面，两处都要维护就是两份真相 |

### 拒绝规则（规范性，全部响亮报错，不许静默吞）

| 情形 | 状态码 | 理由 |
|---|---|---|
| `taskId` 不存在 / 归属链断裂（`projectId`/`zoneId` 悬空） | 404 | 与 `timer/start` 同一判据，同一套报错文案，前端不用为补登另写一套错误处理 |
| `durationSeconds` 非正整数 | 400 | |
| `durationSeconds > 86400` | 400 | 单段会话不可能超过一天。这条主要拦**单位填错**（把 90 分钟填成 90 但后端当秒来存，或反过来把秒当分钟填），不拦也能落库，但会污染统计且极难发现——数字看着"合理地大"，不会报错，只会让某天的图表异常却查不出原因 |
| `startAt` 不带时区偏移 | 400 | **不许猜。** 缺偏移就悄悄套用 `NEXUS_TZ` 会在跨时区/夏令时场景静默错日，且错得看不出来——同「日界与时区」节 `NEXUS_TZ` 无默认值的理由：数据一旦按错误的日界落库，之后每次统计都继承这个错 |
| `startAt` 无法解析 | 400 | |
| `startAt + durationSeconds > now` | 400 | 不能补登未来——补登说的是"过去发生过的事"，未来没有事实可补 |
| 过旧的 `startAt` | **不拦** | 补三个月前的活是正当需求，没有理由设时间窗口上限 |

### 与活状态计时的关系（规范性）

**补登完全不碰 `timer_state`**：不 stop 当前计时、不被当前计时阻塞、正在计时时也允许补登。
补登说的是「过去某段时间我干了活」，与「我现在正在干活」是两件独立的事——
让它们互相干扰会制造一个很烦的伪约束（想补登得先停表），而这个约束换不来任何正确性。

**允许墙钟重叠，不做先后判定。** 补登天然是事后行为，无法也不该要求它与历史记录（含正在
进行的计时）不重叠。（这与"多计时器前台/后台的先后判定"是两个议题，不在本版范围内。）

### 零投影改动（规范性约束）

**本端点不修改任何投影 handler，`registry.py` 的 DISPATCH 表零改动。**

- `proj_daily_stats` 按 `(user, date, projectId, taskId)` 归日，`date` 取的是
  `data.startAt` 经 `NEXUS_TZ` 归日（见「甘特读端」节）——只要补登组出的 `data`
  形状是 `{durationSeconds, startAt}`，与 `stop()` 完全同形，投影 handler 分不出
  这条事件是实时计时结束时组装的还是补登组装的，自动落对日子。
- `proj_current` 是全时段累计、**无日期维度**（`repo.apply_session` 只 `$inc`，不看
  时间），补登是真事实，计入累计天然正确。

**这不是"顺便没改"，是设计约束**：`source` 承担了区分"实时"与"补登"两种证据强度的
职责（见上「服务端组装的信封」），投影层完全不需要知道这个区分——它只关心
`(date, projectId, taskId)` 和秒数，这正是"投影是事实的派生物、只依赖事实形状"这条
不变量在起作用。

**若实现时发现必须改 `daily_stats.py` 或 `current.py` 才能让补登生效，说明本设计有错，
立刻停下重新设计，不要绕过这条约束硬做**——那意味着 `data` 形状对不上、或归属链解析
和 `start()` 走了不同路径，两者都是需要重新设计而不是"顺手改一下投影"的信号。

## 错误响应形状（规范性 · v0.7）

**所有 4xx 响应统一为 `{"detail": "<人话>"}`。**

```jsonc
{"detail": "任务不存在：'t_不存在'（planner 里查无此 id）"}
{"detail": "项目 的 plannedWeight 不能为负：-5.0"}
{"detail": "分区 'z_7f21a4' 下还有 2 个项目——不做级联删除，先清空再删"}
```

**为什么要写进契约**（ring 的 programmer 2026-08-01 指出）：

`ring` 与 `hive` 都**直接把 `detail` 原样显示给用户**——这是我们有意定的纪律
（后端是权威，前端不重复实现校验、不用自己的措辞包装）。
但在 v0.7 之前，这个形状只是 `main.py` 里 exception handler 的**观察到的行为**，
契约一个字都没承诺。

后果是：**exception handler 的形状一改，两个前端同时静默失败，
而且没有任何契约违反能被抓住**——报错框会变成空白或 `undefined`，
测试也照样绿（它们断言的是状态码）。**没写进契约的依赖，就是没人守的依赖。**

三条约束：

1. **`detail` 是给人看的**：必须点名具体字段/取值/剩余数量，不许是
   `"validation failed"` 这类通用文案（那会逼前端自己再写一套校验）。
2. **形状对所有 4xx 一致**，包括 400/401/404/409/422。
3. 改这个形状 = **破坏性变更**，须走 CR 并通知全部消费方
   （现为 `modules/ring`、`modules/hive`）。

## 档案读端（规范性 · v0.6）

`GET /api/core/events` —— 把已落库的**事实**读出来，用户的「x年x月x日
x时x分–x时x分完成了 xx 任务」就是从这里来的。

| 参数 | 必填 | 说明 |
|---|---|---|
| `type` | ❌ | 事件类型过滤，如 `session.completed` |
| `from` / `to` | ❌ | ISO8601 日期或日期时间，按 `time` 过滤 |
| `limit` | ❌ | 默认 100，上限 1000 |
| `offset` | ❌ | 默认 0 |

```jsonc
{ "total": 42, "items": [ /* 事件信封原样，剔除 _id */ ] }
```

**三条规范性约束**：

1. **只读。** 不改变事实的产生方式，不碰 `ingest`，不碰 DISPATCH 表。
   人类要求「events 不准动」指的是**写入侧**；加一条只读查询不违反它。
2. **按 `time` 倒序**（最近的在前）。空结果返 `200` + `total:0`，**不是 404**。
3. **不在后端 join 任务名。** 事实里只存 opaque id 是有意的（§标识三分）——
   名字会改，存了名字历史就自相矛盾。消费方拿 id 去 planner 查**当前**名字。

## 只读全量导出（规范性 · v1.3）

`GET /api/core/export` —— 一次性把用户在本模块的**全部**数据拼成一份 JSON，
给用户「导出我自己的数据」这个诉求用（消费方：`hive` 前端「导出数据」按钮）。

```jsonc
{
  "zones":    [ /* Zone 原始文档，剔除 _id，与 planner.crud.v1 的 ZoneOut 同形状 */ ],
  "projects": [ /* Project 原始文档，同上 */ ],
  "tasks":    [ /* Task 原始文档，同上 */ ],
  "events":   [ /* 事件信封原样，剔除 _id —— 与档案读端 items[] 同一份数据、
                   同一个剔除口径，唯一区别是这里不分页、不过滤、不排序 */ ],
  "projections": {
    "proj_current":     { /* proj_current 原始文档；从未计时过则为 null */ },
    "proj_daily_stats": [ /* proj_daily_stats 原始文档数组，剔除 _id/user/appliedKeys */ ]
  },
  "exportedAt": "2026-08-08T12:00:00+00:00"   // 服务端生成时刻，ISO8601 带时区（UTC）
}
```

**四条规范性约束**：

1. **纯只读聚合，不新增数据、不新开投影。** 四类实体 + 两张投影全部复用既有
   只读函数（`planner.list_zones/list_projects/list_tasks`、
   `events.iter_all_events`、`projector.handlers.{current,daily_stats}.read_*`）——
   与本节相邻的「档案读端」「甘特读端」同一条纪律：口径只住在被聚合的那个
   子边界里，本端点不重新实现、不重新过滤。
2. **`events` 原样返回，不分页、不按 `type`/`from`/`to` 过滤。** 与
   `GET /api/core/events`（档案读端，上一节）复用同一份 `iter_all_events`——
   那个函数本就是给 `projector.rebuild` 全量重放用的，**不受档案读端 1000 条
   上限约束**，导出必须看到全部历史事实，用有上限的 `list_events` 会悄悄截断。
3. **`zones`/`projects`/`tasks` 是 planner 的原始列表，不是 `views.tree.v1`
   塑形后的树。** 导出是给用户的数据副本，不是某个 UI 组件的专用视图——没有
   理由像 `views/queries.py` 那样丢字段（如 `color`/`plannedWeight`）或按
   `zone→project→task` 嵌套重排。
4. **`exportedAt` 由服务端生成。** 消费方（`hive` 前端）下载文件时若要用
   日期命名，须用这个字段，**不得用浏览器本地时钟**——同「甘特读端」的
   `today` 那条理由：客户端时区/时钟不准，服务端时刻才是唯一权威。

## JSON 一键导入编辑（规范性 · v1.7）

`POST /api/core/import` —— 「导出 JSON → 编辑器里改 → 导回去直接生效」的
外部编辑通道（消费方：`hive` 前端，与「只读全量导出」节的导出按钮成对）。

```jsonc
// 请求体：与 export 的 zones/projects/tasks 同形状（原始文档），外加四个控制字段
{
  "zones":    [ /* 元素不带 id（或 id:null）即新建；带已存在的 id 即改 */ ],
  "projects": [ /* 同上 */ ],
  "tasks":    [ /* 同上 */ ],
  "exportedAt": "2026-08-08T12:00:00+00:00",  // 可选，原样接受但忽略
  "dryRun": true,        // 可选，缺省 true——不传就是只看计划，零写入
  "allowDelete": false,  // 可选，缺省 false——缺省不删
  "checksum": "…"        // dryRun:false 时必填：dry-run 那份计划的 checksum
}
```

```jsonc
// 响应：dry-run 与 apply 共用同一个形状，靠 dryRun 判断该看哪一半
{
  "dryRun": true,
  "checksum": "3f9c2b7a1e8d…",   // 本次计划的 sha256，见下「校验和算法」
  "allowDelete": false,
  "plan": {                       // dry-run 非 null；apply 时恒为 null
    "zones":    [ {"op":"create","id":null,"fields":{"name":"新分区"}},
                  {"op":"update","id":"z_x","fields":{"name":"改名"}},
                  {"op":"delete","id":"z_y","fields":{}} ],
    "projects": [ /* 同构 */ ],
    "tasks":    [ /* 同构 */ ]
  },
  "applied": null,                // apply 时非 null（同构，create 的 id 已回填真实值）；dry-run 时恒为 null
  "summary": {"create": 1, "update": 1, "delete": 1},
  "skippedDeletes": {"zones": [], "projects": ["p_老项目"], "tasks": []}
     // 只在 dry-run 且 allowDelete:false 时有内容：库里有、payload 没提到、
     // 但因为没开 allowDelete 而不会被删的 id，仅供参考，不参与 checksum
}
```

### 两段式：dry-run 与 apply（本节要害）

1. **默认 `dryRun:true`**：只读库、算一份 diff 计划，**一个字节都不写**。
2. **`dryRun:false` 时必须带上前一次 dry-run 返回的 `checksum`**：服务端拿
   **同一份** `zones`/`projects`/`tasks`/`allowDelete` 对**当前**库重新跑
   一遍同一套算法，重新算一遍 checksum，与请求体里的比对：
   - **一致**：按算出来的计划真正执行（见「执行顺序」）。
   - **不一致**：**409**，不执行任何一步——库在 dry-run 之后被改动过
     （或者 payload/`allowDelete` 变了），旧计划已经不代表现在该做什么，
     拒绝执行防止把过时的判断套在新状态上。
3. **无状态**：checksum 判据是纯函数（对同一份输入、同一份当前库状态，
   任何时候算出的 checksum 逐字节相同），服务端**不缓存**上一次算出的计划
   ——"过时"就是"对当前库重新算一遍，结果变了"，不需要额外的会话状态。

### 校验和算法（规范性）

对 `{"allowDelete": <bool>, "zones": [...], "projects": [...], "tasks": [...]}`
（`zones`/`projects`/`tasks` 是**算出来的计划**，元素形如
`{"op","id","fields"}`，不是请求体原样）做 `json.dumps(sort_keys=True,
ensure_ascii=False, separators=(",",":"))` 之后取 `sha256` 十六进制摘要。
`allowDelete` 并进被哈希的内容里——同一份 payload 换个 `allowDelete` 会得到
不同的计划（删除条目有无不同），理应得到不同的 checksum。

### 建 / 改 / 删的判据（规范性）

| 情形 | 判据 |
|---|---|
| **建** | 元素里没有 `id` 字段，或 `id` 为 `null`/空串 |
| **改** | 元素的 `id` 与库中某个现存对象一致，且至少一个可写字段的值与当前不同 |
| **删候选** | 库中存在、但 payload 的对应数组里**没有一个元素带这个 id** |
| **无变化** | 元素的 `id` 命中现存对象，但可写字段全部与当前一致 → 不进计划 |

可写字段与统一 CRUD 入口的 `*Create`/`*Update` 请求模型**逐字段同步**（`id`
/`key`/`lastWriter`/`progress`/`progressSource`/`doneAt` 等系统计算字段不参
与比较，改了也不生效——同「标识三分」节，`key` 永远重算、不是可写字段）。

### 拒绝规则（规范性）

| 情形 | 响应 |
|---|---|
| `id` 命中不了任何现存对象 | **400**，点名这个 id——新建对象请去掉 `id`，不要自己编一个 |
| 同一类型数组里 `id` 重复出现 | **400**，点名重复的 id |
| 新建项目缺 `zoneId` / 引用不存在的分区 | **400**，点名 `zoneId` |
| 新建任务缺 `projectId` / 引用不存在的项目 | **400**，点名 `projectId` |
| 改 `zoneId`/`projectId` 引用不存在的目标 | **400**，点名目标 id |
| `events`/`projections` 出现在请求体里（哪怕值是 `null`） | **400**，点名出现的字段名，**不许静默忽略** |
| `dryRun:false` 但缺 `checksum` | **400**，提示先 dry-run |
| `checksum` 与重算结果不一致 | **409**，提示重新 dry-run |
| apply 途中撞上既有业务校验/级联保护（400/404/409） | 原状态码，**已执行的动作不回滚**（见下「执行顺序」） |
| 带 AI 凭据却自报 `actor:"human"`（本端点固定走人的路径） | **403**，与 v1.6「actor 来源区分」同一条判据，见下 |

### 已知限制：同一批不支持"父子两级都新建"

新建项目的 `zoneId`、新建任务的 `projectId` 必须指向**已经存在于库中**的
对象——新建的父对象在 payload 里没有真实 id（这正是"新建"的判据本身），
本端点不做占位符解析去把同一批里的新父对象与新子对象串起来。要新建带子
对象的父对象，请**先单独导入建好父对象**，拿到真实 id 后再在下一次导入的
JSON 里建子对象。这是有意的范围收窄，不是遗漏——占位符 id 解析是完全不同
量级的复杂度，且真实需求里"编辑器里改数据"很少会同批新建两代。

### 执行顺序与失败语义（规范性）

apply 按**创建（zones→projects→tasks）→ 更新（同顺序）→ 删除（tasks→
projects→zones，子先于父）**的顺序逐条执行，每一条都经统一 CRUD 入口同一
个执行通道 `guard.run_write`（判来源 → 高风险设防 → 执行 → 留审计）。

**apply 不是事务**：Mongo 没有跨对象事务，**遇到第一个失败就中止，之前已
成功的动作不回滚**——与既有 `guard.run_write` 的哲学一致（AI 批量写崩在半
路，靠 `GET /api/core/planner/audit` 按 `seq` 查"做到第几步"），import 批
量写崩在半路同样靠它，不需要另建一套"事务日志"。删除顺序"子先于父"是为
了让"整棵子树一起从 payload 里消失"这种最常见的删除场景一次成功；如果
payload 只删了父、忘了一起删子，既有的**级联保护（409）在 apply 真执行到
那一步时照常触发**，不因为走了 import 这条路而失效。

### actor：固定走人的路径（规范性）

本端点的每一条建/改/删都固定传 `body_actor="human"` 给 `guard.run_write`
——契约「actor 来源区分与高风险二次设防」节的判据据此原样生效，不是另开
一套：

- 未携凭据（今天的前端）或携带人路径凭据 → 有效 actor 判成 `human`，正常
  执行，改出来的对象 `lastWriter` 一律变成 `"human"`。
- **携带 AI 凭据** → `resolve_actor` 判定为「带 AI 凭据却自报 human」，
  即"伪装"，**403**，库里一个字节都没写——AI 不能靠打这个端点绕开 v1.6 的
  高风险二次设防，import 与统一 CRUD 入口共用同一条防线。
- 严格模式（`NEXUS_ACTOR_STRICT=1`）下，`unverified` 来源的高风险动作
  （删除、改 `zoneId`/`projectId`/`plan`）同样按既有判据 **403**。

`dryRun:true` 时**不判定 actor/来源，只算 diff**——判定挪到真正写入的那一
刻，与"零写入"的承诺一致；任何人都能先看一眼计划，不暴露自己是否持有凭据。

### 红线：events/projections 一个字节都碰不到（规范性）

本端点只读/写 `zones`/`projects`/`tasks` 三个集合，`import_diff.py` 只调
`planner/repo.py` 的既有只读函数，`import_apply.py` 只经 `guard.run_write`
调 `planner/service.py` 的既有建/改/删函数——与统一 CRUD 入口是**同一条
写路径**，不是另开一条。`events` 是 append-only 事实台账，
本端点没有任何代码路径写它；请求体里出现 `events`/`projections` 字段直接
400 拒绝，**不接受静默忽略**——静默忽略会让用户以为改了其实没改，比报错
坏得多（规格原文）。

## 按租户分数据（规范性 · v2.0）

一个租户 = 一份彼此看不见的数据（zones / projects / tasks / 事件台账 / 两张投影 /
计时状态 / 审计流水 / 「名字 → 号」登记表）。

### 租户从哪来（规范性）

| 请求里的 `X-Nexus-Tenant` | 结果 |
|---|---|
| 没有，或空串 | 单人默认租户 `u_local`；严格模式（`NEXUS_TENANT_STRICT=1`）下 **401** |
| 匹配 `^[A-Za-z0-9_.:-]{1,64}$` | 就是它 |
| 不匹配 | **400**，点名取值。不清洗、不截断——截断后的 id 可能正好是别人的 |

- **信任边界**：nexus-core 只在内网、只经网关访问；网关把认证服务 verify 给出的租户
  设到这个头上，并**覆盖客户端自带的同名头**（`contracts/gateway.v1` 第五节）。
  nexus-core 自己不做认证，它信的是网关。
- `/api/core/health` 不看租户（见「健康检查暴露库名」）。

### 隔离（规范性）

- **读**：每个读端只返回当前租户的数据。另一个租户的对象，按 id 改 / 删 → **404**
  （与「id 不存在」同形状，不暴露它在别的租户里存在）；拿来当父对象或计时 → 400 / 404。
- **写**：新文档一律盖上当前租户。事件的 `user` **由服务端按租户盖章**（同 `recordedAt`
  的 B6 纪律）：信封里的 `user` 客户端照填（信封校验不放宽），落库的是租户——否则一个
  租户能往另一个租户的台账里写事实。快照恢复同理：恢复进哪个租户，事实就属于哪个租户。
- **响应形状不变**：planner 对象、审计记录读出来都不带 `user` 字段（租户是存储层的事）。
- **唯一约束**：zones / projects / tasks 为 `(user, id)`，登记表为 `(user, name)`，
  审计流水为 `(user, seq)`（v2.0.1）——**审计序号按租户各自递增**，全局序号会让一个
  租户从 `seq` 的跳号里看出别的租户写了多少次。`u_local` 的序号接着 v1.x 往下走。
  同一份快照可以恢复进两个租户；每个租户各有自己的收件箱 `p_inbox`。
  旧的全局唯一索引由 app **启动时**删掉换新（只动索引、不动数据，幂等）——迁移是
  手动跑的，不能指望它。
- **「名字 → 号」按租户**：一个租户用过哪些名字，不会从另一个租户拿到的号里看出来。
- **投影重建**仍然清全体租户、重放全体租户的事实（结果按租户各自归位）。

### 单人部署与老数据（规范性）

- 不带头 = `u_local`，与 v1.x 行为一致。
- v1.x 的老 planner 文档没有 `user` 字段：`u_local` **同时认**这批文档，不跑迁移也照常
  可见；别的租户看不到它们。`migrations/004_backfill_tenant.py` 给它们补上 `u_local`。

### 多用户部署必须打开严格模式

认证服务哪天出 bug 漏带了租户头，不开严格模式的后果是：所有人的请求悄悄落进同一份
`u_local` 数据，**而且不报错**。开了之后是 401，响亮失败。

## 快照恢复（规范性 · v1.9）

`POST /api/core/restore` —— `GET /api/core/export` 的逆操作：把一份导出快照原样
搬进**空实例**。给「换机器 / 重装之后把自己的数据搬回来」用；此前唯一的路是
`mongodump`，要求使用者知道后端是 Mongo、知道卷名、有 docker 权限。

**为什么不是 import**：import 是**编辑通道**，拿快照喂它会被三层拒绝（带 `events`
→ 400；不认外来 id → 400；不许父子同批新建 → 400）。三条拒绝各守一件实事——台账
不许经编辑通道伪造、id 不许自己编、引用不许悬空——一条都不放宽。restore 是另一条
通道，import 的任何行为不变。

```jsonc
// 请求体：GET /api/core/export 的输出，逐字节原样，一个键都不用加减
{ "zones": [...], "projects": [...], "tasks": [...], "events": [...],
  "projections": {...}, "exportedAt": "..." }
// 控制参数走查询串——请求体要与 export 逐字节一致，`curl --data-binary @export.json` 直接喂：
//   ?dryRun=true                          缺省 true，零写入
//   ?dryRun=false&checksum=<dry-run 返回的 checksum>
```

```jsonc
// 响应：dry-run 与 apply 同形状
{
  "dryRun": false,
  "checksum": "3f9c2b7a1e8d…",
  "summary": {"zones": 12, "projects": 28, "tasks": 48, "events": 83},
  "rebuilt": {"proj_current": 83, "proj_daily_stats": 83}   // {投影名: 重放的事件数}；dry-run 时为 null
}
```

### 搬什么、怎么搬（规范性）

| 快照里的 | 怎么处理 |
|---|---|
| `zones`/`projects`/`tasks` | **id 原样保留**，文档原样落库——不走 `create_*`，那条路会重发 id、重算 key |
| `events` | **原样落台账**，`recordedAt` 不重盖（盖章时刻是原实例收到它的时刻，重盖等于改写历史）；防重照旧走唯一索引 |
| `projections` | **读进来但不落库**；台账落完后按「投影重建」节从全部事实重放。投影是派生物，直接导入等于允许投影与台账对不上 |
| `exportedAt` | 接受、忽略 |
| 「名字 → 号」登记表（export 不带） | 从各对象的 `key` 反推补登记，发号器抬到出现过的最大号——否则新实例从 1 重新发号，新建对象与恢复进来的对象撞 key |

`events` 这一行是**全系统唯一允许绕开 `ingest` 直写台账的地方**：它搬的是**同一份
台账**，不是编辑——与 import 拒收 `events` 守的是同一条纪律的两面。信封校验不放宽，
每条过同一个 `Envelope` 模型，只是改成整批先过、有一条不合格就整批 400。

### 只对空实例开放（规范性）

`zones`/`projects`/`tasks`/`events` 任一非空 → **409**，`detail` 列出各类条数并写明
「这是恢复通道，不是合并通道」。dry-run 与 apply 同样判——dry-run 就该告诉你这份
快照进不去。`planner_audit`/`name_registry`/`counters`/`timer_state` 不参与判定。

**本版不提供覆盖已有数据的模式。**「清空再恢复」要删台账，而台账是 append-only
的事实来源；这个口子开不开、怎么开，另行决定。

### 两段式（规范性）

与 import 同一套纪律：默认 dry-run 零写入；apply 必须带 dry-run 返回的 checksum。

- **checksum 算法**：对 `{"zones","projects","tasks","events"}` 四个数组（快照原样，
  不含 `projections`/`exportedAt`）做 `json.dumps(sort_keys=True, ensure_ascii=False,
  separators=(",",":"))` 之后取 `sha256` 十六进制摘要。
- **与 import 的区别**：计划只取决于快照本身，所以不把当前库并进哈希；「dry-run
  之后库变了」由 apply 时重判「库必须为空」承担。
- apply 缺 `checksum` → 400；与这份快照算出的不一致 → 409（apply 的不是 dry-run
  过的那一份）。

### 拒绝规则（规范性）

全部校验在**任何写入之前**跑完——恢复写到一半才发现断链，留下的是一个既不空、
也不完整的实例。

| 情形 | 响应 |
|---|---|
| 缺 `zones`/`projects`/`tasks`/`events` 任一数组，或出现未知顶层键 | **422**——文件被截断或手改过，按「少了就是空」恢复会静默丢数据 |
| 对象缺 `id`，或 `id` 不是非空字符串 | **400**，点名 `类型[下标]` |
| 同类型 `id` 重复 | **400**，点名 id |
| 项目的 `zoneId`、任务的 `projectId`、任务 `dependsOn` 指向快照里不存在的对象 | **400**，点名两端 id |
| 事件信封不合法，或快照内 `(user, source, dedupeKey)` 重复 | **400**，点名 `events[下标]` 与原因（最多列 5 条） |
| 目标实例非空 | **409**，见上 |
| apply 缺 checksum / checksum 不符 | **400** / **409** |
| 有效 actor 为 `ai`，或严格模式下未携人路径凭据 | **403**，见下 |

事件的 `subject` **不做**闭包校验：历史事件本就可以指向已删除的对象（「标识三分」节）。

### 设防与留痕（规范性）

整次恢复经 `guard.run_write` 执行，算**一次高风险写**（`op:"restore"`，它一次能写满
全库）——与「actor 来源区分与高风险二次设防」节同一条防线，不另开口子：有效 actor
为 `ai` → 403；严格模式下 source 不是 `human` → 403；拒绝发生在任何写入之前。
`planner_audit` 记一条 `op:"restore"`、`objectType:"snapshot"`、`changes` 为三类对象
的条数，applied/denied/failed 照记。dry-run 不判来源（同 import）。

### 执行顺序与失败语义（规范性）

zones → projects → tasks（按 id upsert）→ 补登记表 → events（逐条经防重写入）→
重建全部投影。**不是事务**：中途失败不回滚，实例留在非空状态，再恢复会 409——此时
换一个空库重来。`timer_state` 不在快照里，恢复后没有在跑的计时。

## 收件箱（规范性 · v1.5，F-INBOX-1）

GTD「捕捉」的落点：一个 well-known 的「未分类」zone + 一个 well-known 的
`p_inbox` 项目，**固定 id**（`z_inbox` / `p_inbox`），不走三类对象平时的
uuid 生成——它们是系统单例，不是用户建的对象，固定 id 才能让「这是不是
收件箱」这个判断在任何库上都成立（不用猜名字、不用查配置）。

```jsonc
// GET /api/core/planner/projects 里能看到它，形状与其他项目完全一样
{ "id": "p_inbox", "key": "<未分类的号>-<收件箱的号>", "zoneId": "z_inbox",
  "name": "收件箱", "status": "active", "plannedWeight": 0.0, "plan": null }
```

### 幂等种子

由 `code/backend/seed_planner.py` 的 `service.ensure_inbox()` 创建：库里已有
`p_inbox`/`z_inbox` 就直接返回现有的，不重建、不重复——**对生产库幂等**，
反复跑没有副作用。**不在 app 启动路径里自动建**（同其余种子数据的纪律，
`views/`、`planner/` 等模块本身不改变数据），显式跑一次种子脚本才生效。

### 禁删（F-API-4）

`DELETE /api/core/planner/projects/p_inbox` → **409**，与「删除语义」节的
`HasChildrenError` 走同一个异常类、同一个状态码映射，但**触发条件不是
"还有子对象"，是"这是系统捕捉落点"**——复用同一形状（"这个删除请求合法，
但当前状态不允许"），不是新开一种错误。

```jsonc
{"detail": "项目 'p_inbox' 是系统收件箱（捕捉落点），禁止删除"}
```

任务仍可以正常从 `p_inbox` 搬走（`PATCH .../tasks/{id}` 改 `projectId`，
J10 已有的能力，不新增写路径）——禁删保护的是**容器本身**，不限制容器
里的任务流动，这正是 F-INBOX-3「理清」要用到的既有能力。

## 下一步行动读端（规范性 · v1.5，F-TODO-1）

`GET /api/core/views/next-actions` —— GTD「下一步行动」的规则层实现，
**离线确定、不依赖 LLM**（PRD G3）：全部输入是 planner 现有字段
（`done`/`dependsOn`/`plan`/`plannedWeight`/`zoneId`），没有新集合、没有新投影。

```jsonc
{
  "today": "2026-08-10",
  "zones": [
    {
      "id": "z_1", "key": "Z01", "name": "示例分区一",
      "actionable": [
        { "id": "t_1", "key": "Z01-P01-T01-1", "name": "背单词",
          "projectId": "p_1", "projectName": "示例项目三",
          "plannedWeight": 100.0,
          "plan": { "start": "2026-08-01", "end": "2026-08-10" },  // null=未排期
          "overdue": false, "dueToday": true,
          "dependsOn": ["t_0"],
          "cycleWarning": false,
          "blockedBy": []                 // 可做列表恒为空数组
        }
      ],
      "waiting": [
        { "id": "t_2", "key": "Z01-P01-T01-2", "name": "写作文",
          "projectId": "p_1", "projectName": "示例项目三",
          "plannedWeight": 80.0, "plan": null,
          "overdue": false, "dueToday": false,
          "dependsOn": ["t_1"], "cycleWarning": false,
          "blockedBy": [ { "id": "t_1", "key": "Z01-P01-T01-1", "name": "背单词" } ]
        }
      ]
    }
  ]
}
```

### 分类（PRD 术语映射）

| 分类 | 判据 |
|---|---|
| **可做**（`actionable`） | `done=false` 且 `dependsOn` 全部已 `done`（含 `dependsOn=[]` 的情形，也含**无 `plan` 的任务**——GTD 里没有 due 的 next action 一样是可做，不因缺排期而降级） |
| **等待**（`waiting`） | `done=false` 且至少一个直接前置未 `done`；`blockedBy` 点名**直接**未完成前置（不做多级传递展示——A 等 B、B 等 C，A 的 `blockedBy` 只列 B，不列 C，前端要看更深链路走 `views/tree` 或 `views/gantt` 的 `dependsOn`） |

`done=true` 的任务不出现在本读端（已完成的不是"下一步"）。

### 过期与今天到期

- `overdue`：`plan.end < today`（服务端归日，见「日界与时区」节，`NEXUS_TZ`，
  **不接受客户端 `new Date` 拼出来的"今天"**）且任务未完成。**前端拿它渲染
  琥珀色警示**，本端点只给布尔值，不做视觉决定。
- `dueToday`：`plan.end == today`。与 `overdue` 互斥，两者任一为真都参与「置顶」
  排序（见下）。
- 无 `plan` 的任务两个字段恒为 `false`（没有 `plan.end` 就没有"过期"可言）。

### 排序（每个 zone 的 `actionable`/`waiting` 各自独立排序）

`(0 if overdue 或 dueToday else 1, -plannedWeight, key)` 升序——即：
**今天到期/过期的任务永远排最前**；同为紧急或同为不紧急时，**`plannedWeight`
降序**；权重相同时按 `key`（稳定序，不随请求抖动）。`zones` 数组本身按
`zone.order` 升序（与 `views/tree` 的 zone 顺序同口径）。

### 依赖环防御（硬约束 · A8d，grill 技术底线）

写入时的成环校验（`planner/deps.py::validate_depends_on`）只保证**"这次写入
之后"**图无环，**不保证历史数据**——成环校验是切片 2 中途才加的（v1.1），
更早写入的任务、或任何绕过 API 直接写库的数据都可能带环。本读端**必须**
在遍历依赖图前做**图级环检测**（标准三色 DFS，`O(点数+边数)`，保证终止），
外加一道硬深度上限兜底（防御性护栏，理论上不会触发，触发即视为数据异常
而不是继续递归）。

**处置**：任何被判定"位于某个环上"的未完成任务，**无论其直接
`dependsOn` 是否全部 `done`，一律降级放进 `actionable`**（不是崩溃、不是
500、不是挂起），并把 `cycleWarning` 置 `true`；`blockedBy` 恒为 `[]`
（环上的"前置"关系本身已不可信，不展示可能误导的阻塞信息）。

pytest 覆盖：构造一个绕过写入校验的环（直接操作 repo/Mongo，模拟历史脏
数据），断言请求在有限时间内返回且相关任务 `cycleWarning=true`；反向验证
——临时移除环检测短路，同一 fixture 必须挂起或报错（证明检测确实在生效，
不是摆设）。

## 每周回顾读端（规范性 · v1.5，F-REVIEW-1）

`GET /api/core/views/review` —— GTD「回顾」的聚合视图。**全部是现有
events/planner/投影的重新聚合，零新集合、零新投影**（PRD F-REVIEW-1 明文）。

```jsonc
{
  "today": "2026-08-10",
  "weekStart": "2026-08-10",   // 本周周一，服务端归日
  "weekEnd": "2026-08-16",     // 本周周日
  "planVsActual": [
    { "projectId": "p_1", "key": "Z01-P01", "name": "示例项目三",
      "plan": { "start": "2026-08-01", "end": "2026-08-31" },   // null=未排期
      "scheduledThisWeek": true,        // plan 区间与本周有交集
      "actualSecondsThisWeek": 5400 }   // 本周内该项目的计时秒数（跨任务求和）
  ],
  "overdueProjects": [
    { "id": "p_2", "key": "Z01-P02", "name": "示例项目二",
      "plan": { "start": "2026-07-01", "end": "2026-08-01" }, "status": "active" }
  ],
  "staleTasks": [
    { "id": "t_5", "key": "Z01-P01-T01-5", "name": "示例任务二",
      "projectId": "p_1",
      "lastActiveDate": "2026-07-20" }   // 最近一次有计时的日期；从未计时过为 null
  ],
  "inboxPendingCount": 3
}
```

四块聚合，**逐块点明数据来源**（都是既有只读函数，不重新实现口径）：

| 块 | 内容 | 数据来源 |
|---|---|---|
| `planVsActual` | 全部项目：计划区间是否与本周有交集 + 本周实际计时秒数 | `planner.list_projects` + `proj_daily_stats`（按本周日期范围过滤，同「甘特读端」`from`/`to` 口径） |
| `overdueProjects` | `status="active"` 且 `plan.end < today` 的项目 | `planner.list_projects` |
| `staleTasks` | 未完成 + 最近 `STALE_TASK_DAYS`（默认 14，**经验阈值非契约硬约束**，同 PRD O1 的开放口径，用户试用后可调）天内该任务无任何计时（含从未计时过） | `planner.list_tasks` + `proj_daily_stats` 全量按 `(taskId, date)` 求最近一次 |
| `inboxPendingCount` | `projectId == "p_inbox"` 的任务总数（不区分 `done`——"待清空"问的是"还有多少留在捕捉篮子里没搬走"，不是"有没有做完"） | `planner.list_tasks` |

**`weekStart`/`weekEnd` 用 ISO 周（周一起）**，基于服务端 `NEXUS_TZ` 归日的
`today` 推算，不接受客户端本地周定义（同「甘特读端」`today` 那条理由）。

## 写者字段 actor/lastWriter（规范性 · v1.5 字段，v1.6 起来源由服务端判定）

统一 CRUD 写入口（POST/PATCH）新增可选入参 `actor`，取值 `"human"` |
`"ai"`，**缺省 `"human"`**；服务端把它落库为对象上的 `lastWriter` 字段
（三类对象的响应体都新增这个字段，见「planner CRUD」节的响应形状）。

> **v1.6 起请求体里的 `actor` 不再是最终裁决者**：携带凭据的调用方由服务端
> 判定来源并**强制覆盖** `lastWriter`，请求体只在"未携带任何凭据"时仍被采信
> （向后兼容既有前端）。完整规则见下一节，本节只定义字段本身的形状与语义。

```jsonc
// POST /api/core/planner/tasks 请求体（actor 可选）
{ "projectId": "p_1", "name": "背单词", "actor": "ai" }
// 响应新增字段
{ "id": "t_1", "key": "...", ..., "lastWriter": "ai" }
```

- `actor` 非法值（不是 `human`/`ai`）→ **400**，点名取值（同其余入参校验纪律）。
- **PATCH 不传 `actor`** → `lastWriter` 保持原值不变（与其余可选字段的
  `exclude_unset` 语义一致——PATCH 是局部更新，不传就是不改）。
- **旧文档缺 `lastWriter`**：读取侧默认 `"human"`（`Field` 默认值兜底），
  同时配一条迁移回填（`migrations/003_backfill_last_writer.py`），两条纪律
  同`handoff.md`「加字段时两条纪律缺一不可」一致。

### v1.5 的信任洞与 v1.6 的补法（历史，不要删）

v1.5 明文承认过一个**已知的、暂时的信任洞**：任何调用方都能在请求体里写
`actor:"human"` 冒充人工操作，服务端原样信任。那不是疏忽，是**依赖顺序使然**
——来源区分只有在 `ai-planner` 的受控工具层存在之后才有实施对象。

波2 之后 `ai-planner` 已落地（受控层写命令签名无 `actor` 形参、硬注入
`actor="ai"`、`propose_*` 不持 nexus 客户端），威胁模型有了实施对象，
**v1.6 因此补上下面两节**。留着这段历史是为了让后来者知道：那个洞是被
**计划过、登记过、然后关掉**的，不是"忘了"。

## actor 来源区分与高风险二次设防（规范性 · v1.6，F-ACTOR-1 波2 / F-API-3）

> **本节是安全边界，不是校验规则。** 它假设调用方**恶意且绕过了受控工具层**
> ——受控层的白名单是第一道，本节是第二道。两道都在，才叫设防；
> 只有第一道，叫单点。

### 一、来源判定：凭据头，不是请求体

写请求可携带**来源凭据头**：

```
X-Nexus-Client-Token: <token>
```

服务端把它归成三类**来源（source）**，与请求体里的 `actor` 是两码事：

| source | 判据 | 含义 |
|---|---|---|
| `ai` | 头存在且等于 `NEXUS_AI_CLIENT_TOKEN` | 调用方是 AI 受控工具层 |
| `human` | 头存在且等于 `NEXUS_HUMAN_CLIENT_TOKEN` | 调用方是人的前端路径（经网关注入） |
| `unverified` | **头不存在，或头存在但为空串/纯空白** | 未表明身份的调用方（今天的前端就在这一类） |

**空串/纯空白视同头不存在，不是"带了个错凭据"**（`planner/guard.py::resolve_source`，
测试 `test_empty_header_counts_as_absent` 押着）。理由：网关用
`proxy_set_header X-Nexus-Client-Token "$var"` 转发时，`$var` 未设的情况下发的
正是**空串**，不是缺这个 header——把空串判成"带了个错凭据"会在网关配置疏漏时把
整个前端的写路径 403 打死，而那是运维事故（配置漏配），不是有人试探凭据。
这条判据与上一段"头存在但不匹配 → 403 不降级" **不矛盾**：不匹配指的是**非空**
但比不上任何已配置凭据的字符串（真的伪造/轮换错了）；空串是"压根没传值"的
另一种表现形式，两者是不同的输入类，判罚也不同——严格区分才不会顺手把运维事故
升级成安全事件。此前只在 `resolve_source` 的实现与测试里体现，契约正文没写，
属规范空白（2026-08-11 复核发现，见 contract-changelog.md）。**头存在但两个
都不匹配 → 403，不降级成 `unverified`。** 理由是 fail-closed：
一个"带了凭据但不对"的请求，最可能的情形是凭据轮换错了或有人在试探；
把它静默当成"没带凭据的普通人"，等于**用一个更宽松的身份接住了一次失败的鉴权**。

两个 token 的比较用**恒定时间比较**（`hmac.compare_digest`），不用 `==`。

### 二、有效 actor：来源压过自报

| source | 请求体 `actor` | 落库 `lastWriter` | 说明 |
|---|---|---|---|
| `ai` | 不传 / `"ai"` | **`ai`**（强制） | 受控层无须自报，服务端也不信它自报 |
| `ai` | `"human"` | — | **403**：带 AI 凭据却自称人，这是伪装，不是笔误 |
| `human` | 不传 | **`human`**（强制） | |
| `human` | `"ai"` | `ai` | 允许：**自降权限永远可信**（前端代 AI 提议落库时用） |
| `unverified` | 不传 | v1.5 语义不变（POST 落 `human`；PATCH 不动） | 向后兼容，见下 |
| `unverified` | `"ai"` / `"human"` | 按自报 | 向后兼容 |

判据一句话：**"我是 human" 是提权声明，必须有凭据；"我是 ai" 是降权声明，
谁说都信。** 这条不对称不是妥协，是威胁模型本身的形状——冒充 AI 拿不到任何东西。

**为什么 `unverified` 仍按 v1.5 语义放行**：今天人的前端（`hive`/`ring`/`gantt`）
一个都没有携带凭据，凭据注入点在 `nginx-docker` 网关（跨模块）。把 `unverified`
一刀切成不可信，等于在网关改造落地前把整个前端写路径打死。**这是有意的、
已登记的剩余缺口**，见本节末尾「剩余缺口」，并由严格模式提供开关。

### 三、高风险写：带 `actor=ai` 一律 403（F-API-3）

**高风险的定义（本表是唯一事实，`ai-planner-guide-v1.md` §3.3 与之对齐）**：

| 操作 | 是否高风险 | 理由 |
|---|---|---|
| `DELETE` 任意 type | **是** | 不可逆 |
| `PATCH` 含 `projectId`（任务搬移） | **是** | 改归属，GTD「理清」的实质动作 |
| `PATCH` 含 `zoneId`（项目搬移） | **是** | 同上 |
| `PATCH` 含 `plan`（含 `plan:null` 清空） | **是** | 改计划期＝改用户对时间的承诺 |
| `POST /api/core/restore` 的 apply（v1.9） | **是** | 一次写满全库，含事实台账 |
| `POST`（建对象） | 否 | 可撤销，PRD F-AI-4 低风险类 |
| `PATCH` 只含 `name`/`plannedWeight`/`order`/`color`/`done`/`kind`/`flags`/`status`/`dependsOn` | 否 | 同上 |

判据看的是 **PATCH 请求体里实际出现的键**（`exclude_unset`），不是最终值有没有变。
传了 `plan` 又原样传回旧值，仍算高风险——**"你有没有伸手"比"伸手后有没有变"更好判、
更难绕**，而且后者需要先读旧值，读与写之间就有竞态。

规则：

1. **有效 actor 为 `ai` 的高风险写 → `403`**，`detail` 点名操作与原因。
   这条与 source 无关：受控层带凭据发的、别人自报 `actor:"ai"` 发的，一样拒。
2. **严格模式（`NEXUS_ACTOR_STRICT=1`）下，高风险写还要求 source 必须是 `human`**
   ——即必须持有人路径凭据。`unverified` 的高风险写 → `403`。
3. 低风险写不受本节限制（AI 建任务、改标题、调权重照常，PRD F-AI-4）。

**这不是"AI 写了再靠令牌拦"**：拒绝发生在**任何库写入之前**，服务端不曾落过一个字节。

### 四、剩余缺口（登记在案，不许当成已解决）

| 缺口 | 现状 | 解除条件 |
|---|---|---|
| `unverified` 仍按 `human` 待遇 | 严格模式默认**关** | `nginx-docker` 网关为人路径注入 `X-Nexus-Client-Token`，之后本模块把 `NEXUS_ACTOR_STRICT` 置 1 |
| 人路径 token 的保管 | 由部署方注入 env | **绝不得落进受控工具层 / codex 可及的文件系统**（硬约束）；AI 侧只应拿到 `NEXUS_AI_CLIENT_TOKEN` |
| 离线写路径不产生审计 | 种子脚本 / 迁移脚本直接经 `repo` 写库，不过统一入口 | 属"有本机文件系统与 Mongo 权限"的威胁面，超出 actor 模型；如需覆盖须另开任务 |

**为什么不用现有的登录会话（cookie）区分**：`ai-planner` 今天用的正是**同一把口令**
登录同一个 auth 门（见 `code/ai-planner` 的 `nexus_client._ensure_auth`）。
会话 cookie 只证明"过了门"，不证明"你是谁"——用它区分人与 AI，等于用同一把钥匙
的两次转动去分辨两个人。凭据必须是**两把物理上分开的钥匙**，这就是新增头的理由。

## planner 审计流水（规范性 · v1.6，F-ACTOR-2）

### 红线先说：审计**不是**事件

`planner_audit` 是**独立集合**，与 `events` 零交集：不进事件信封、不进投影、
不进重建链路、不被 `GET /api/core/events` 或 `GET /api/core/export` 返回。

**为什么必须分开**：`events` 是**跨模块开放标准**（学生按它写脚本），
`spec` 字段只增不改不删；审计是**本模块的运维追溯**，字段会随需求长。
把运维记录塞进公开事实流，等于让"我们内部想多记一列"变成"所有学生的脚本要改"。
反过来，让审计迁就事件信封的不变性，则第一次要加字段时就会有人去改信封。
**混在一起会同时毁掉两边**，分开是唯一解。

### 记录形状

每条记录（`AuditOut.items[]`）：

```jsonc
{
  "auditId":    "aud_9f31c2a7b4d0",   // 本条记录的 opaque id，永不复用
  "seq":        42,                    // 单调递增序号，**追溯顺序看它，不看时间戳**
  "at":         "2026-08-10T12:00:00+00:00",  // 服务端盖章的 UTC 时刻
  "actor":      "ai",                  // 有效 actor（服务端判定，不是自报）
  "source":     "ai",                  // ai | human | unverified（凭据类别）
  "op":         "update",              // create | update | delete | restore（v1.9）
  "objectType": "tasks",               // zones | projects | tasks | snapshot（v1.9，restore 专用）
  "objectId":   "t_a1b2c3",            // 目标 id；create 被拒时为 null（还没有 id）
  "highRisk":   true,                  // 本次操作是否落在高风险表内
  "outcome":    "denied",              // applied | denied | failed
  "changes":    { "projectId": "p_9" },// 变更摘要（见下）
  "reason":     "高风险操作…"           // denied/failed 的原因；applied 时为 null
}
```

**三种 outcome 都记，缺一不可**：

| outcome | 何时 | 为什么必须记 |
|---|---|---|
| `applied` | 库已改 | 追溯"改了什么" |
| `denied` | 被本契约「高风险二次设防」拒绝（403） | **被拒的尝试是安全信号**，只记成功等于把攻击痕迹丢掉 |
| `failed` | 过了设防但被业务校验拒（400/404/409） | 批量写崩在半路时，它标出"第几步崩的、为什么" |

**`seq` 为什么必须有**：AI 批量写在同一秒内可以发十几条，时间戳分不出先后；
"做到第几步"要的是**全序**，不是近似。`seq` 由 `counters` 集合原子自增
（与 `name_registry` 发号同一套机制），进程重启、并发写都不重号。

**`changes` 是摘要不是快照**：`create` 记入参键值、`update` 记 PATCH 里实际出现的键值、
`delete` 记 `{}`。长字符串截断到 200 字符、数组截到 20 项并标注 `"…"`。
**绝不记凭据头**——审计流水本身不得成为密钥泄漏面。

### append-only 是实现级保证，不只是承诺

- 写路径**只有** `insert_one`；本模块不提供、也不实现审计记录的改与删。
- 没有任何 HTTP 路径能改写审计（`GET /api/core/planner/audit` 是唯一暴露面，只读）。
- 该不变量由测试机械核验（`tests/test_planner_audit.py` 的 AST 断言：
  审计集合上只允许 `insert_one`/`find`/`create_index`/`count_documents`）——
  **口头承诺 append-only 不算数，能重现拦住违规写法的断言才算**。

### 读端 `GET /api/core/planner/audit`

| 参数 | 必填 | 说明 |
|---|---|---|
| `limit` | ❌ | 默认 50，上限 500 |
| `objectId` | ❌ | 只看某个对象的流水 |
| `actor` | ❌ | `human` / `ai` |
| `outcome` | ❌ | `applied` / `denied` / `failed` |

响应：`{"total": <匹配总数>, "items": [<记录>…]}`，**`seq` 降序**（最新在前）。
非法 `limit`（≤0 或 >500）与非法枚举值 → **400**，点名取值。

`audit` 是 `/api/core/planner/` 下的**保留只读伪 type**：`POST`/`PATCH`/`DELETE`
`/planner/audit` 走「未知 type」兜底返 **404**（它不是 zones/projects/tasks）。

## 统一 CRUD 入口（规范性 · v0.5，v0.6 起为唯一写路径）

> **v0.6 起这是 planner 的唯一写路径。** 十二条旧端点（`/api/core/{zones,projects,tasks}`）
> **已删除**。产品决定 2026-08-01：「旧端口清掉」「都要从统一入口走」。
>
> v0.5 时曾新旧并存（怕删了 `modules/hive` 立刻坏），那是**过渡状态不是设计**——
> 同一件事两个入口，迟早有人只改一边。前端迁移另行处理，
> 迁移期间 table 会暂时坏，这是**有意接受的代价**，不是意外。

### 端点

| 方法 | 路径 | 语义 |
|---|---|---|
| GET | `/api/core/planner/{type}` | 列表。查询参数与对应旧端点一致（如 `?zoneId=`、`?projectId=`）|
| POST | `/api/core/planner/{type}` | 新建。请求体与对应旧端点一致 |
| PATCH | `/api/core/planner/{type}/{id}` | 局部更新。请求体与对应旧端点一致。**v1.6：改 `projectId`/`zoneId`/`plan` 属高风险，`actor=ai` → `403`** |
| DELETE | `/api/core/planner/{type}/{id}` | 删除。`204` / `409`（有子对象）/ **`403`（v1.6：`actor=ai` 一律拒）**|

`{type}` ∈ `zones` | `projects` | `tasks`。**未知 type 返 404**，
消息必须点名收到的是什么、合法取值有哪些——不许静默当成某个默认类型。
（`GET /api/core/planner/audit` 是 v1.6 新增的**保留只读伪 type**，
见「planner 审计流水」节；它只有 GET，其余方法照常落进未知 type 的 404。）

### 为什么统一入口而**不是**统一校验（本节最重要的一条）

路由归一是安全的；**校验绝不能归一**。

现行契约明文保证「错误消息点名具体字段」：

```
"项目 的 plannedWeight 不能为负：-5.0"
"任务 kind 非法：'不合法的kind'，合法取值 normal/ephemeral"
```

`modules/hive` 的实现直接依赖这条（原样展示后端消息、不自己包装措辞）。
**通用 CRUD 层天然滑向通用报错**（"validation failed"），一旦那样，
这条契约保证就没了，前端只能自己再写一套校验——正是当初决定不要的重复。

因此实现必须是：**薄路由层按 `{type}` 分派到各自类型化的 pydantic schema
与 service 函数**，而不是一个吃 dict 的动态处理器。
本节新增的是**入口**，不是新的校验路径；同一份校验被两个入口共用。

### 与旧端点的关系（规范性）

- **同一份 service 函数**。新入口不得复制业务逻辑，只做路由转换。
  两条路径产生不同行为 = 缺陷，不是特性。
- 响应形状、状态码、错误消息**逐字节相同**。
- 旧端点无弃用计划。要弃用需另提 CR 并给消费方迁移期。

### 为什么不做成独立服务（产品决定 2026-08-01，记下来免得重新讨论）

planner 的数据被 `views` 每 5–10 秒 join 一次：`views/current` 要同时拿
计时状态（timer）+ 任务/项目/分区名字（planner）+ 累计秒数（projector）。
拆成独立服务后这个 join 跨网络，且库要么共享（违反「模块只经契约依赖」的原则）、
要么分库（事件里的 id 指向另一个库，views 没法 join）。

**该独立部署的是 `ai-gateway`**（LLM 密钥隔离、可能挂/慢不得拖累计时器），
不是 planner。planner 被划为将来的拆分线，那是认证接入、
有真实多用户压力之后的事，现在拆买不到任何东西却先付网络与一致性的账。

## planner CRUD（规范性 · 切片 2）

### 三类对象的响应形状

三类对象一律返回 **`id` / `key` / `name` 三件套**（见下一节），外加各自的业务字段：

```jsonc
// ZoneOut
{ "id": "z_…", "key": "Z01", "name": "示例分区一", "color": "#ff9d45", "order": 0,
  "lastWriter": "human" }                                    // v1.5 新增，缺省 "human"

// ProjectOut
{ "id": "p_…", "key": "Z01-P01", "zoneId": "z_…", "name": "示例项目一",
  "status": "active", "plannedWeight": 100,
  "plan": { "start": "2026-08-01", "end": "2026-08-31" },    // plan 可为 null
  "lastWriter": "human" }                                    // v1.5 新增

// TaskOut
{ "id": "t_…", "key": "Z01-P01-T01-1", "projectId": "p_…", "name": "示例任务一",
  "done": false, "doneAt": null, "kind": "normal", "flags": [], "plannedWeight": 50,
  "plan": { "start": "2026-08-01", "end": "2026-08-05" },    // v1.1 新增，可为 null
  "dependsOn": ["t_…"],                                      // v1.1 新增，默认 []
  "lastWriter": "human" }                                    // v1.5 新增
```

`lastWriter` 的写入口与语义见「写者字段 actor/lastWriter」节——请求体侧的
入参叫 `actor`，落库/响应侧的字段叫 `lastWriter`，两个名字故意不同（一个是
"这次写操作声明由谁发起"的动作，一个是"这个对象上次是谁写的"的持久状态）。

### 删除语义：**拒绝级联，返回 409**

| 删什么 | 还有子对象 / 被依赖时 | 无子对象 / 无依赖时 |
|---|---|---|
| 分区 | **409** + 说明还有几个项目 | 204 |
| 项目 | **409** + 说明还有几个任务；`p_inbox` **恒 409**（系统收件箱，v1.5，见「收件箱」节） | 204（`p_inbox` 除外） |
| 任务 | **409** + 说明有哪些任务依赖它（v1.1，见「排期与依赖」节） | 204 |

**v1.6 起 DELETE 多一道前置判据**：有效 actor 为 `ai` 的删除请求**在任何存在性
检查之前**就返 `403`（见「actor 来源区分与高风险二次设防」节）。顺序是有意的
——先拒来源、再谈对象在不在，否则 404/409 的差异会变成一个**探测器**，
让无权删除的调用方靠状态码枚举出哪些 id 存在。

**不做级联删除。** 理由：分区下可能挂着几十个项目、每个项目挂着任务，
一次误操作抹掉一棵树是不可逆的；让用户先清空子对象，是把不可逆动作拆成可见的几步。
**任务的 409 是同一形状的另一种触发**：删掉的不是「父」，而是「被别人指着的前置」——
别的任务 `dependsOn` 里还写着它的 id，删掉它会让那些依赖悬空，同样先拒绝再要求解依赖。

**已产生过事件的对象删掉之后，历史事件不受影响** —— 事件存的是 opaque id，
id 不复用（见下节），所以历史永远指向一个"曾经存在过的东西"，不会张冠李戴。

### 改名与换归属（对应标识三分）

- `PATCH` 改 `name` → **只动 `name`**，`id` 不动，`key` 重算
- `PATCH` 改 `projectId`（任务换项目）→ **只动 `projectId`**，`id` 不动，`key` 重算
- **`id` 在任何情况下都不可变**，也**不许被复用**——删掉一个对象后，
  新建的对象必须拿新 id，否则历史事件会指到一个不相干的新对象上

### 校验

| 情形 | 响应 |
|---|---|
| `zoneId` / `projectId` 指向不存在的对象 | **400**，说明哪个 id 不存在 |
| `name` 为空或全空白 | **400** |
| `plannedWeight` 为负 | **400** |
| `kind` 不是 `normal` / `ephemeral` | **400** |
| 目标 id 不存在（PATCH/DELETE） | **404** |
| `X-Nexus-Client-Token` 存在但不匹配任何已配置凭据（v1.6） | **403**，不降级成"未携带" |
| 携带 AI 凭据却自报 `actor:"human"`（v1.6） | **403**，点名这是伪装 |
| 有效 actor 为 `ai` 的高风险写（v1.6） | **403**，点名操作与原因 |
| 严格模式下 `unverified` 来源的高风险写（v1.6） | **403**，点名缺人路径凭据 |

**禁止静默回退**：任何一处校验不过都要给出**指名道姓**的错误，
不许"用个默认值继续"——弱默认值会让配置错误在下游表现成别的症状。

### 排期与依赖（规范性 · v1.1，任务级）

任务对象新增两个 planner 台账字段——**都是计划态，不进事件标准**（下节「与事件的关系」
同样适用）：

- `plan: {start, end} | null`——任务自己的排期，格式与校验**复用项目 `plan` 那一套**
  （`YYYY-MM-DD`，`end` 不得早于 `start`，`null` 是合法的清空操作），走同一个校验函数，
  不是照抄一份。
- `dependsOn: [taskId]`——前置任务 id 列表，**默认 `[]`**。这是**纯表达，不是排程**：
  系统不会因为 A `dependsOn` B 就阻止用户同时进行 A、也不会自动调整任何日期。
  它回答的问题是「A 应该排在 B 之后」，展示与提醒是消费方（甘特箭头、计时页前置提示）的事。

校验（新增于既有 400/404 校验表之外，逐条指名道姓）：

| 情形 | 响应 |
|---|---|
| `plan.end` < `plan.start` | **400**，点名 `plan.start`/`plan.end` |
| `dependsOn` 含不存在的任务 id | **400**，点名哪个 id |
| `dependsOn` 含自身 | **400** |
| `dependsOn` 成环 | **400**，给出完整环路径（如 `t_a → t_b → t_c → t_a`） |
| 删除被依赖的任务 | **409**，列出依赖它的任务（见上节「删除语义」） |

**成环检测覆盖全任务图，不只看这一次写入涉及的几个节点**：写入时把候选的新
`dependsOn` 边代入当前任务的位置，从这个任务出发做 DFS；只要能绕回自己就是环。
因为**每次写入都会先过这道校验**，图在任何时刻都是无环的，所以新环只可能通过
"这一次改动的边"产生——从被改动的节点出发做 DFS 就足够，不需要每次扫全图找任意环。

**跨项目依赖允许**：`dependsOn` 不检查两端是否同属一个项目——甘特允许跨项目箭头
（PRD F-GANTT-4），依赖表达的是时间先后，不是归属关系。

**排期冲突与越界排期均不校验、不阻止**（用户裁决，PRD F-API-3）：
任务计划期可以超出所属项目的计划期，项目本身可以没有计划期；两个有依赖关系的任务
`plan` 时间段也可以互相重叠或倒序。**这不是遗漏**——自动排程是完全不同量级的功能，
契约明确不做；冲突提示（琥珀色警示）是纯展示层的事，后端不参与判断、不阻止写入。

### 与事件的关系

planner 是**计划状态**，走普通 CRUD，**不进开放事件标准**（事实与计划三分）。

本版 **不发 `plan.updated` 事件** —— 留到 gantt 那片再定，
因为那时才知道审计需要记什么粒度。**现在发一个形状没想清楚的事件，
比不发更糟**：事件只增不改不删，发错了要背着它走到 v2。

## 标识三分与 `key` 生成（规范性 · 产品决定 J10）

分区 / 项目 / 任务三类实体，各有**三样各管各的东西**：

| | 例 | 会不会变 | 约束 |
|---|---|---|---|
| `id` | `t_a1b2c3` | **永不变** | **唯一约束压这里** |
| `key` | `Z01-P01-T01-1` | 搬移/改名时重算 | **不做唯一约束** |
| `name` | `示例任务一` | 随时可改 | 用户输入什么就是什么（中文原样） |

**事件的 `subject` 只存 id**（见仓根 `contracts/yq-event-v1.md` 第 5 节）。
所以改名与搬移都不污染历史。

### `key` 怎么生成

```
分区   key = <分区号>                              例  Z01
项目   key = <分区号>-<项目号>                      例  Z01-P01
任务   key = <分区号>-<项目号>-<名字号>-<同名序号>    例  Z01-P01-T01-1
```

（**分区与项目的 key 格式在 v0.3 未定义**，programmer 当轮按升级条款停下上报、
没有猜一个格式实现——这个处置是对的。v0.4 补齐：**逐级前缀**，每级追加自己那一段。）

每段的号来自一张 **「名字 → 号」登记表**：见到新名字发新号，同名复用同一个号。

**不做拼音音译。** 音译会塌陷（`塔`/`她` 同为 `ta`，两个名字撞成一个标识），
且方案不止一种（换个库标识全变）。中英文走同一张表。

同名序号 = **同一项目下**同名的第几个，无其他含义。

### 建任务时

`id` 与 `key` **都由系统生成**，调用方只提供 `name` 与 `projectId`。

### 搬移与改名

| 操作 | 动什么 | 不动什么 |
|---|---|---|
| 改名 | `name` | `id`；`key` 重算 |
| 换归属 | `projectId` | `id`；`key` 重算 |

**`key` 里的路径是「出生地」不是「现住址」** —— 搬移后前缀不再表示当前归属，
当前归属永远以 `projectId` 字段为准。这不是 bug，是 `key` 的定义。

### 显示层

界面上的 `示例分区一/示例项目一/示例任务一#1` 是**渲染时按 id 查 name 拼的**，
**不入库、不进事件、不做检索键**。

## 入口与路由

- nginx 公开前缀：`/api/core/`（HANDOFF §4 已定死，前端写死地址）
- 内部服务名 / 端口：`nexus_core:8000`（部署时确认）
- 前端静态路由 `/ring/`、`/hive/` **不经本模块**，由 nginx 直接指向静态目录

## 内部子边界（**将来分拆的线，现在就按它切**）

人类硬约束：**nexus-core 之后必定会分拆，子边界＝将来的模块边界，
不按"现在方便"切。目标是分拆那天是「搬目录」，不是「重写」。**

```
app/modules/
  events/     router service repo     事实写入口、dedupe、落库
  timer/      router service          活状态；stop 时组装 session.completed
  planner/    router service repo     zones/projects/tasks 的 CRUD 状态
  proposals/  router service          AI 与人的交接台
  views/      router queries          纯只读，本契约两条读端住在这里
  projector/  registry handlers/      DISPATCH 显式表 + 各投影 handler
```

**跨子边界只准调用对方 `service.py` 的公开函数**（HANDOFF §8 第二条）。具体地：

- `views/` **只读投影集合**（`proj_current` / `proj_daily_stats` / `proj_trees`）与
  planner 的状态集合，**不读 `events`**、不调其他子模块的 `repo.py`。
- 只有 `repo.py` 允许 import mongo 客户端。`router.py` 里不许有业务判断。
- `projector/registry.py` 的 `DISPATCH` 表是**全系统唯一联动真相**，
  任何修改单独成 commit 说明影响面。

**这些不是风格偏好，是分拆期权的保证**：只要跨边界调用一直只走 `service.py`，
把 `modules/views/` 整个目录搬进独立仓时，要改的只有 import 路径与部署配置。
一旦有人为了"就这一次方便"直接从别人的 `repo.py` import，
那条边界就在分拆那天变成重写。

单文件不超过 300 行（HANDOFF §8 第八条，比单文件 500 行的上限更严，按 300 执行）。

## 依赖的外部契约

| 依赖 | 契约位置 | 用途 |
|---|---|---|
| `yq-event/v1` | `contracts/yq-event-v1.md` | 事件信封唯一事实。**本模块是它的第一个实现者**——实现与标准不一致时，改的是实现，不是标准 |

认证不在本模块：v2.0 起 `user` 是网关转来的租户（`X-Nexus-Tenant`，见「按租户分数据」），缺头为 `"u_local"`。

## 数据与存储

- 归本模块所有：`events` / `timer_state` / `zones` / `projects` / `tasks` / `proposals`
  / `proj_current` / `proj_daily_stats` / `proj_trees`（HANDOFF §6）
  / **`planner_audit`（v1.6，审计流水，append-only，见「planner 审计流水」节）**。
- **其他模块一律不得直连本模块的 Mongo**。要数据就加读路径，不要绕。
- `events` 集合**只增不改不删**；修正历史 = 追加修正事件。
- data root 由 env 指定，位于 Git 工作树之外。

## 配置与密钥

| 环境变量 | 必填 | 用途 | 安全约束 |
|---|---|---|---|
| `NEXUS_MONGO_URI` | 是 | Mongo 连接串 | 缺失立即失败，**禁止弱默认值** |
| `NEXUS_DB_NAME` | 是 | 库名 | 无默认 |
| `NEXUS_BIND` | 否 | 监听地址，默认 `127.0.0.1:8000` | 不得默认 `0.0.0.0` |
| `NEXUS_AI_CLIENT_TOKEN` | 否 | AI 受控工具层的来源凭据（v1.6） | 设了就必须 ≥16 字符；**只发给 `ai-planner`**；未设＝没有调用方会被判成 `ai` 来源 |
| `NEXUS_HUMAN_CLIENT_TOKEN` | 否 | 人路径（网关注入）的来源凭据（v1.6） | 设了就必须 ≥16 字符；**绝不得落进受控层 / codex 可及的文件系统**；不得与 AI 凭据相同 |
| `NEXUS_TENANT_STRICT` | 否 | 租户严格模式，默认 `0`（v2.0） | 取值只认 `0`/`1`/`true`/`false`；置 1 时缺 `X-Nexus-Tenant` 的请求一律 401。**多用户部署必开**，见「按租户分数据」节 |
| `NEXUS_ACTOR_STRICT` | 否 | 严格模式，默认 `0`（v1.6） | 取值只认 `0`/`1`/`true`/`false`（其余立即失败）；置 1 时 `NEXUS_HUMAN_CLIENT_TOKEN` 必填，否则启动失败——**开了严格模式却没有人路径凭据 = 把前端写路径全打死，这种配置必须炸在启动那一刻，不是炸在用户点删除那一刻** |

本模块**不持有任何 LLM 密钥**——那是 ai-gateway 的事，物理隔离是设计的一部分。
v1.6 新增的两个 token **不是**认证凭据（认证仍归网关的 `auth_request`），
它们只回答"这个请求是哪一类客户端发来的"，作用域仅限 actor 判定。

## 消费方（改本契约必须通知）

| 消费方 | 用哪条 | 位置 |
|---|---|---|
| `ring` 前端 | `views.current.v1`；`timer.v1` 的 `POST /api/core/timer/cancel`（取消计时按钮「取消，不记录」）；v1.2 起 `views.tree.v1` 任务节点的 `dependsOn`/`done`（F-RING-6 前置未完成提示，读既有 tree 接口，无新增请求）；`views.gantt.v1` 的 `today`/`projects[].id`/`projects[].tasks[].{id,name}`/`tasks[].actual[].{date,seconds}`（`modules/ring` 契约 commit `2cba2aa` 已登记己方消费，本行是反向索引追平）；**v1.8 新增已登记**：`timer.v1` 的 `POST /api/core/timer/backfill`（空闲态「计时方式」两个 tab 旁的「补登」入口，运行态也可点，任务下拉复用现有取任务列表路径，`modules/ring` 契约 commit `e6cfd9d` 已登记己方消费，本行是反向索引追平） | `modules/ring` |
| `hive` 前端 | `views.tree.v1`（v1.2 起任务节点带 `plan`/`dependsOn`，控件按这两个键 feature-detect 是否亮起）；`planner.crud.v1` 的 `TaskOut.plan`/`dependsOn`（F-TABLE-3 任务排期与前置任务编辑，写走既有 `PATCH /api/core/planner/tasks/{id}`）；`views.gantt.v1` 的 `projects[].plan`/`projects[].actual[].{date,seconds}`/`today`（`modules/hive` 契约 commit `603a44e` 已登记己方消费，本行是反向索引追平）；v1.3 起 `views.export.v1`（「导出数据」按钮，全量拉一次 + `exportedAt` 用于文件命名）；**v1.8 新增已登记**：`timer.v1` 的 `POST /api/core/timer/backfill`（任务行「补登」按钮，点开时任务字段预填该行任务且不可改，`modules/hive` 契约 commit `f11cdb3` 已登记己方消费，本行是反向索引追平） | `modules/hive` |
| `gantt` 前端 | `views.gantt.v1`；v1.1 起响应新增 `projects[].tasks[]`（任务层 plan/dependsOn/actual，F-GANTT-1..4） | 不在本仓 |
| `hive` / 新 todo 前端 | **v1.5 新增消费待登记**：`views.next-actions.v1`（F-TODO-2..5，待办区视图）、`views.review.v1`（F-REVIEW-2，每周回顾视图）、`planner.crud.v1` 的 `actor`/`lastWriter`（F-ACTOR-3，AI 写过的对象角标展示）、`p_inbox` 禁删（F-INBOX-1..4，收件箱/理清 UI） | `modules/hive`（或 GTD PRD O1 待定的新 `/todo/` 页） |
| `ai-planner`（波2 已落地） | `planner.crud.v1` 的统一写入口（受控工具层的白名单命令面，F-AI-2）+ `actor="ai"` 写入（F-ACTOR-1）+ `views.export.v1`（F-AI-3，读全量日程喂 LLM）；**明确不消费** `events`/`timer`（红线，AI 只碰 planner）。**v1.6 新增要求**：写请求应携带 `X-Nexus-Client-Token: $NEXUS_AI_CLIENT_TOKEN`（携带后 `actor` 由服务端强制判定，受控层再也不必、也不能自报）；**高风险写从此在服务端被 403 拒绝**，与受控层「只产提议」互为二次设防 | `code/ai-planner`；AI 侧行为约定见仓根 `contracts/ai-planner-guide-v1.md` |
| `nginx-docker` 网关（v1.6 新增待办，**尚未实施**） | 人路径的来源凭据注入：为 `/api/core/*` 的写请求注入 `X-Nexus-Client-Token: $NEXUS_HUMAN_CLIENT_TOKEN`。**未实施前 `NEXUS_ACTOR_STRICT` 必须保持 0**，否则前端的搬移/改期/删除全被 403 打死 | `code/nginx-docker`（跨模块，须排期，见「actor 来源区分与高风险二次设防」节「剩余缺口」） |

**`views.gantt.v1` 现有三个消费方，各读不同切片**：`gantt` 前端读全部（项目层
+ 任务层，画甘特图）；`ring`/`hive` 只读其中一部分字段（`today` 作为服务端
时钟基准、`projects[]` 的部分字段），**不需要任务层** `tasks[]`——三方共用
同一个响应，字段增减只影响真正用到那个字段的消费方，其余的按既有纪律
（改字段先走 CR）不受影响。

**v1.1/v1.2 变更对三方均为纯增量**：`TaskOut`、`TreeOut` 任务节点、
`GanttOut.projects[]` 都只加字段（`plan`/`dependsOn`/`tasks`），既有字段一个
不改、一个不删——旧客户端忽略新字段即可继续工作，零迁移期。

**v1.2 是修 v1.1 留下的集成缝**：v1.1 给 `TaskOut` 与 `views.gantt.v1` 都加了
`plan`/`dependsOn`，唯独漏了 `views.tree.v1`——而 `ring`/`hive` 实际读取任务
列表恰恰主要经过 tree（一次性全量树），不是 planner 的分页列表接口。v1.1 report
里「读既有任务列表接口」这句话不够精确，实际验收在联调时才发现 tree 没带字段。
补记于此，提醒以后加任务级字段要过一遍全部读取该实体的端点，不能改完一个
就当作那批字段"已经加完了"（同 `contract-schemas.md`「与其他两条读端的对照」）。

同步维护项目级依赖索引的反向索引。

## 变更记录

见 `module_docs/contract-changelog.md`（从本文件拆出，只记历史不含规范）。
