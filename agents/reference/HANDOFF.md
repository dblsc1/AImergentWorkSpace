> # ⚠️ 本文件是讨论草稿，不是契约，也不是规范。
> 它记录的是 2026-07 那轮架构讨论的思路与理由，**随时可能与已拍板的事实不一致**。
> **唯一权威是 `agents/CONSTITUTION.md`**（项目宪法）。两者冲突时一律以宪法为准，不需要犹豫。
> 读本文件的正确姿势：**当背景资料看**（"当时为什么这么想"），不要当判据引用。
> 已知本文件内部就有自相矛盾处（§3.1 与 §6 对防重键的写法对不上，已由宪法 D7 定死）。

> **定位**：本文件是**项目总目标**，指导性、随项目进度可改。它回答「要做成什么」，
> 框架规范（`agents/AGENTS.md` 铁律）回答「怎么做才算数」。两者冲突时**停下上报人类**，不要自行取舍。
>
> **和框架规范的分工**：本文讲的是**这个系统的技术形态**（分层、事件标准、集合设计、API、联动表）——
> 它是**项目级提示词**，任何在本项目干活的 agent 都该读。
> `agents/AGENTS.md` 的铁律讲的是**协作与交付的流程约束**（留痕、写边界、审核、合并）。
> 两者不重叠：一个管「这系统长什么样」，一个管「你怎么干活才算数」。

---

# HANDOFF 手册 — 综合时间管理系统（副屏系统）

> 本手册面向接手开发的编程 AI（arbiter / backend / reviewer 体系）。
> 内容为产品目标与全部已定架构决策的完整移交，含决策理由。
> 契约类章节（事件标准、集合、API、技术约定）为**规范性内容**，实现不得偏离；
> 其余为背景与理由，供判断时参考。

---

## 1. 项目愿景与产品形态

**一句话**：一个显示在电脑副屏上的个人综合时间管理系统，同时作为开放的 AI 教育课程组件——用户/学生可以自己生成标准 JSON 来驱动系统。

### 1.1 三级数据结构

```
分区 (zone)     如：生活区、工作区、学习区
 └─ 项目 (project)   如：生活-学英语
     └─ 任务 (task)   具体行动，如：记单词
```

### 1.2 视图与时间维度

| 视图 | 时间维度 | 内容 | 现状 |
|---|---|---|---|
| 贡献圆环 (ring) | 现在 | 当前任务占项目比例、项目占总计划比例、累计时长 | 前端已有（project-task-contribution-ring.html），暴露 `window.renderContributionRing(json)` 接口，中文键 |
| 项目表/控制台 (table) | 将来·规划 | 各分区的项目与任务、进度 | 前端已有（index.html，NEXUS 控制台，目前用 localStorage，需迁移到 API） |
| 甘特图 (gantt) | 将来·排程 | 计划起止、依赖、实际进度叠加 | 待开发 |
| 成长花园 (garden) | 过去 | 树按分类生长，回顾历史投入 | 已是可演示产品（见 §11），需重构接入事件标准 |

后续会有更多视图。**架构必须保证：新增视图 = 新增一条读路径 + 至多一张新投影，现有代码零改动。**

### 1.3 教育定位

事件 JSON 标准公开发布。课程学生写脚本生成合规 JSON 即可驱动自己的花园——入门课用文件导入（零网络知识），进阶课用 API 推送。"AI 提议 → 人类确认 → 成为事实"的交互本身也是课程内容。

---

## 2. 顶层架构原则（全部决策的根）

1. **单一事实来源，星型派生**。所有写操作进唯一写入口（nexus-core），事实落库后派生各视图投影。四个前端之间**没有任何通信路径**，它们只是同一份事实的四面镜子。拒绝视图间网状同步（4 视图=12 条同步路径的运维噩梦）。
2. **事实 / 意图 / 投影三分**：
   - 发生过的事 = **事件**（append-only，可回放）→ events 集合，走开放标准；
   - 打算做的事 = **计划状态**（会反复修改）→ planner 模块普通 CRUD，**不进开放标准**（Gantt 拖动日期是改状态，可顺带发 `plan.updated` 事件留痕，但 Gantt 读状态接口，不重放事件）；
   - 每个视图读自己的**投影**，不翻别人的库。
3. **低耦合独立项目**：每个可部署单元自己一个仓库/容器/数据库（或独立集合组），彼此不 import 代码、不直接读对方的表。唯一共享物：账户服务发的 token + 公开的 JSON 事件标准。
4. **AI 只有提议权**：AI 永远不直接写事实与计划（例外：查询与计时器控制可直接执行）。AI 的判断物化为数据上的属性（flags），而非运行时绕过规则。
5. **物理收敛、逻辑分离**：单人运维，部署单元收敛为 3 个后端；但代码模块边界严格，将来拆微服务 = 搬目录。

---

## 3. 开放事件标准 yq-event/v1（规范性）

### 3.1 信封格式

```json
{
  "spec": "yq-event/v1",
  "id": "evt_a1b2c3",
  "type": "session.completed",
  "user": "u_123",
  "source": "timer-backend",
  "time": "2026-07-23T10:00:00+08:00",
  "subject": { "tier2": "学习", "tier3": "英语", "project": "p_1", "task": "t_45" },
  "data": { "durationSeconds": 1800, "startAt": "2026-07-23T09:30:00+08:00" },
  "flags": ["no_growth"],
  "ai": { "generated": true, "confidence": 0.87, "confirmed": true }
}
```

- 必填：spec, id, type, user, source, time, subject
- 可选：data（按 type 定型）、flags（字符串数组，默认空）、ai
- `id` + `source` 构成防重键（dedupe）：同一事件重复提交只生效一次

### 3.2 设计原理（参考 CloudEvents）

- **信封永远不变，载荷按 type 分型**。扩展 = 新增 type，不改信封。
- **消费者只处理自己认识的 type，不认识的必须静默忽略**（Postel 法则）。garden 只订阅成长相关 type，圆环后端只看 `session.*`，互不干扰。
- 信封字段只增不改不删。真正不兼容时发 v2，新旧并行。
- 学生入门只需信封 + 最简单的 `growth.granted` 类型。

### 3.3 首批事件类型

| type | data 载荷 | 说明 |
|---|---|---|
| session.completed | durationSeconds, startAt | 一段计时结束（系统心跳） |
| task.created | kind | 任务创建留痕 |
| task.done | — | 任务完成 |
| growth.granted | amount, note | 通用成长点（学生/外部来源的最简类型） |
| diary.extracted | 结构化分类、备注 | garden 现有日记链路泛化 |
| plan.updated | 变更摘要 | 计划修改留痕（审计用） |

### 3.4 两种接入门

1. **文件导入**（入门）：网页上传 .json 文件 → `POST /events/import`
2. **API 推送**（进阶）：`POST /events`，带账户 token

自有后端（timer 等）与外部用户走**同一入口**，接口文档天然被验证。

---

## 4. 部署视图

```
                    nginx（统一网关，见 §12 上游写法）
                             │
    ┌────────────┬───────────┼──────────────────────┐
    ▼            ▼           ▼                      ▼
account-service  nexus-core  ai-gateway        静态文件目录
（已有，独立平台） （系统心脏）  （AI 综合管理入口）  /ring /table /gantt /garden
登录·发 JWT
```

- **nexus-core**（FastAPI + Mongo）：事件存储、计划状态、投影。逻辑上三个模块，物理上一个应用。
- **ai-gateway**（FastAPI）单独部署的三个硬理由：持有 LLM 密钥；可能挂/慢（LLM 超时不得拖累计时器）；"只写提议"的铁律靠物理隔离保证不被绕过。自身仅持久化 `conversations`，对系统事实零持久化，可随时重启。
- 四个前端全是纯静态页，零后端逻辑。
- nginx 路由（定死，前端写死地址）：
  - `/api/auth/*` → account-service
  - `/api/core/*` → nexus-core
  - `/api/ai/*` → ai-gateway
  - `/ring/ /table/ /gantt/ /garden/` → 静态

---

## 5. nexus-core 内部结构（规范性）

### 5.1 目录

```
nexus-core/
  app/
    main.py                 # 只做组装 create_app()，~50 行
    core/                   # 纯技术设施：config.py db.py auth.py errors.py
    common/
      envelope.py           # yq-event/v1 的 pydantic 模型（跨边界数据必经校验）
      ids.py  timeutil.py
    modules/
      events/    router.py service.py repo.py
      timer/     router.py service.py
      planner/   router.py service.py repo.py schemas.py
      proposals/ router.py service.py
      views/     router.py queries.py      # 纯只读，不写任何东西
      projector/
        registry.py         # DISPATCH 显式注册表（唯一联动真相）
        handlers/  current.py daily_stats.py trees.py   # 一个投影一个文件
        rebuild.py          # 全量重放
    jobs/scheduler.py       # APScheduler：夜间 rebuild 等
  tests/                    # 按模块对应
```

### 5.2 每模块三层角色

- **router.py（前台）**：收请求、验 token、转参。禁止业务逻辑。
- **service.py（厨房）**：全部业务判断。跨模块协作**只准调用对方 service.py 的公开函数**。
- **repo.py（仓库）**：全模块唯一允许 import mongo 客户端、碰数据库的文件。

单文件不超过 300 行。行数增长只发生在"多一个模块目录 / 多一个 handler 文件"。

### 5.3 联动分发：显式 DISPATCH 表（不用装饰器自注册）

```python
# projector/registry.py —— 全系统唯一联动真相，任何修改单独成 commit
from .handlers import current, daily_stats, trees

DISPATCH = {
    "session.completed": [current.update, daily_stats.update, trees.grow],
    "task.done":         [daily_stats.update, trees.grow],
    "task.created":      [current.touch_if_running],
    "growth.granted":    [trees.grow],
    "plan.updated":      [],   # 甘特直读 projects.plan，无需投影
}
```

选显式表而非装饰器自注册的理由：多 AI agent 协作 + reviewer 审查的工作流下，摆在明面上的中心表最易审、git diff 最清晰。

### 5.4 投影更新与容错

- 事件写入成功后，**同请求内**按 DISPATCH 同步更新投影（当前规模不需要消息队列）。
- 所有 handler **幂等**（靠事件 dedupeKey），只写自己的投影集合，**禁止发新事件**（防连锁反应）。
- **夜间全量重算**（rebuild.py + 定时任务）兜底：投影脏了、成长规则改了，重放事件流即自愈。事件溯源送的保险，必须保持可用。
- 一次请求的完整链路示例（POST /timer/stop）：
  timer/router → timer/service（读 timer_state、组装 session.completed）→ events/service.append（dedupe、落库）→ projector.run → 按表调 current/daily_stats/trees 三个 handler（各自先检查 flags）→ 返回 200。

### 5.5 flags 机制（规范性）

- flags = 贴在数据（任务、事件）上的字符串数组，如 `["no_growth"]`。
- **贴什么贴纸 = 数据入库时决定（人或 AI 皆可）；每张贴纸导致什么行为 = handler 代码焊死（改行为必须改代码、过 review）。**
- handler 首行检查：`if "no_growth" in evt.flags: return`。
- 收益：重放结果与当时一致（flags 随事件永久落库）；可审计（查事件即知为何没长树，AI 判断依据在 proposals.reason）；可反悔（追加修正事件摘 flag 或补 growth.granted，历史不改写）。
- 典型应用：临时任务 `task.kind="ephemeral"` + `flags:["no_growth"]` → 不长树、views/tree 默认过滤（前端留"显示临时任务"开关）。
- v2 才考虑的事（**现在不做**）：规则本身可配置化（规则版本化存库、重放按当时版本执行）。

---

## 6. MongoDB 集合设计（nexus-core 库，规范性）

```
events            # 事实，只增不改不删
  { _id, user, type, source, time,
    subject:{tier2,tier3,project,task}, data:{...},
    flags:[], ai:{generated,confidence,confirmed} }
  索引: (user,time) (user,type,time)  唯一:(user,source,dedupeKey)

timer_state       # 活状态：正在计时的 session，每用户至多一条
  { user, taskId, projectId, startAt }
  # stop = 删本条 + 写一条 session.completed 事件

zones             { _id, user, name, color, order }

projects          { _id, user, zoneId, name, tier2, tier3,
                    status: active|done|archived,
                    plan:{ start, end, deps:[projectId], milestones:[{name,date}] },
                    actual:{ start, end },     # 由事件推得，重算可刷新
                    plannedWeight }

tasks             { _id, projectId, name, done, doneAt,
                    plannedWeight, kind: normal|ephemeral, flags:[],
                    plan:{ start, end } }      # Gantt 细到任务时用

proposals         # AI 与人的交接台（待确认队列，泛化 garden 现有"确认成长"）
  { _id, user, kind: event|plan_change|insight,
    payload:{...},            # kind=event 时即完整事件草稿
    confidence, reason,       # AI 判断依据，展示给用户
    status: pending|accepted|rejected, createdAt, decidedAt }

proj_current      { user, running, zone, project:{id,name,totalSeconds,shareOfPlan},
                    task:{id,name,totalSeconds,shareOfProject}, sessionStartAt }
proj_daily_stats  { user, date, projectId, zoneId, seconds, tasksDone }
proj_trees        { user, tier2, tier3, stage(0-10), xp, updatedAt }
```

占比语义：`plannedWeight` 存计划值；`shareOfPlan / shareOfProject` 默认按事件聚合的**实际值**（圆环随真实工作增长）；控制台可展示计划 vs 实际偏差。

分类映射：**分区 ≈ Tier2，项目 ≈ Tier3（一个项目一棵树）**。项目归档后树保留为成熟树。

**认证后置的兼容锚点**：账号功能后置期间，一切文档照常带 `user` 字段，写死常量 `"u_local"`。将来接入 JWT 仅替换注入来源，零数据迁移。**今天省认证，不省字段。**

---

## 7. API 设计（规范性）

### 7.1 nexus-core（/api/core/*）

```
# events 模块
POST /events                    # 单条/批量，过 envelope 校验，dedupe，落库，触发 DISPATCH
POST /events/import             # 文件导入（教育入门门）
GET  /events?type&from&to       # 审计/回放查询

# timer 模块
POST /timer/start {taskId}      # 自动关闭上一个未结束 session
POST /timer/stop

# planner 模块
GET/POST/PATCH/DELETE  /zones  /projects  /tasks
PUT  /projects/{id}/plan        # Gantt 拖拽保存（可顺带发 plan.updated 留痕）

# proposals 模块
GET  /proposals?status=pending
POST /proposals/{id}/accept | /reject

# views 模块（四前端读端）
GET /views/current              # 圆环轮询（5–10s）
GET /views/tree                 # 项目表：zones→projects→tasks 一次给全，默认过滤 ephemeral
GET /views/gantt                # plan + actual 叠加 + daily_stats 实际进度
GET /views/garden               # proj_trees；回放模式直接查 events

GET /health
GET /debug/dispatch             # 打印 DISPATCH 注册表（可读性）
```

`/views/current` 响应模型：

```python
class CurrentOut(BaseModel):
    running: bool
    zone: ZoneRef | None            # {id, name}
    project: ProjectStat | None     # {id, name, totalSeconds, shareOfPlan}
    task: TaskStat | None           # {id, name, totalSeconds, shareOfProject}
    sessionStartAt: datetime | None
```

圆环前端适配器（现有页面不改内部逻辑）：

```js
const r = await fetch(API + "/views/current").then(x => x.json());
renderContributionRing({
  "项目": r.project.name, "当前任务": r.task.name,
  "项目占总计划": r.project.shareOfPlan,
  "当前任务占项目": r.task.shareOfProject,
  "项目累计时长秒": r.project.totalSeconds,
  "任务累计时长秒": r.task.totalSeconds
});
```

### 7.2 ai-gateway（/api/ai/*）

```
POST /ai/chat        # AI 综合管理入口，SSE 流式；LLM + 工具调用，带用户 JWT 转发到 core
POST /ai/classify    # 自由文本 → tier2/tier3/项目（DeepSeek 提取器的泛化）
```

**工具清单（首批 8 个）**：start_timer(taskId)、stop_timer()、create_task(project,name,kind,flags)、complete_task(taskId)、update_plan(projectId,start,end)→产出 plan_change 提议、query_stats(range,groupBy)、propose_event(type,subject,data)→永远走 proposals、list_pending_proposals()。

**权限边界**：查询与计时器直接执行（"开始背单词"本就是用户口头指令）；对事实与计划的改动一律落 proposals 等确认。

**定时任务**（APScheduler）：每晚读当天事件写 `insight` 提议（今日复盘）；每晨对比 plan vs actual，将延期项目写 `plan_change` 提议。AI 综合管理 = 被动问答 + 每日主动递条子。

### 7.3 实时性

副屏轮询 5–10 秒为默认方案。如需即时：加极简 SSE 端点 `/api/core/stream`，推送内容只有"数据变了"一声铃，各前端收到后各自重拉自己的接口——视图之间依然互不知晓。

---

## 8. 技术建议（本系统的架构约定）

> **定性**：这九条是**本系统的技术约定**，不是框架铁律。
> 它们描述「这个系统应该长什么样」，由 arbiter 在开单时转成任务单的**验收标准**，
> 由 reviewer 转成 `review/reviewcode/` 里的**检测脚本**——那才是它们真正生效的方式。
> 随项目进度可以改；改了要在本节留一行原因，别让下一个人重新发明。
>
> 与 `agents/AGENTS.md` 铁律的区别：**铁律管流程（怎么干活才算数），本节管技术（系统长什么样）**。
> 本节与铁律冲突时以铁律为准（例：本节第八条写「单文件不超过 300 行」，
> 比铁律 9 的 500 行更严 —— 下层加严是允许的，按 300 执行）。

```
一、只有 repo.py 允许 import mongo 客户端；router 禁止出现业务逻辑
二、模块间只准调用对方 service.py 的公开函数
三、events 集合只增不改不删；修正历史 = 追加修正事件
四、所有 handler 必须幂等，只写自己的投影集合，禁止发新事件
五、DISPATCH 注册表是唯一联动真相，任何修改单独成 commit 说明影响面
六、AI（ai-gateway）对事实与计划只有提议权；直接执行权仅限查询与计时器
七、flags 决定数据性质，代码决定系统行为，二者的修改走不同评审
八、跨边界数据必须过 envelope 校验；单文件不超过 300 行
九、每个 PR 必须附带或更新对应模块测试；金链路测试永远保绿
```

**金链路测试**（架构完整性的哨兵，贯穿五个模块；应落进 `review/regression/`，每次全跑）：
建任务(kind=ephemeral, flags=[no_growth]) → timer start/stop → 断言：proj_daily_stats 有记录、proj_trees 无变化、views/tree 默认不返回该任务、proj_current 曾正确更新。

---

## 9. 开发流程（vibecoding 编制）

### 9.1 顺序：契约先行 → mock 解耦 → 垂直切片

1. **契约四件套**（人类唯一深度介入点）：`contracts/yq-event-v1.md`、`contracts/api.openapi.yaml`、`contracts/collections.md`、`CONSTITUTION.md`。**不是前端定义数据，而是契约定义一切，前端只是第一个消费者。**
2. **mock server**：~200 行 FastAPI 按 openapi.yaml 返回假数据。四前端对 mock 开发、确定功能，后端并行不受阻。
3. **垂直切片**（每片可演示，人类介入压缩为"看 demo、点验收"）：

```
切片1  events + timer + views/current          → 圆环真转（金链路诞生）
切片2  planner CRUD + views/tree               → 项目表活了（index.html 弃 localStorage 改 API）
切片3  plan 字段 + views/gantt                 → 甘特活了
切片4  projector(trees) + garden 事件适配        → 花园由事实驱动
切片5  ai-gateway (chat/classify/proposals)     → AI 入口上线
切片6  夜间 rebuild + SSE + nginx + 账号接入收尾
```

### 9.2 arbiter 编制

模块 arbiter 六个：events、timer、planner、views、projector、ai-gateway；另设**总 arbiter 持有四份契约**——契约改动必须总 arbiter 批准并广播全体（多 agent 协作最易腐坏处，制度钉死）。reviewer 验收底线 = 金链路测试绿 + 第 8 节技术约定对照。

### 9.3 人类介入点（仅三处）

定契约；每切片末验收 demo；铁律冲突时终裁。

---

## 10. 关键决策速查（含理由，防止重新发明）

| 决策 | 结论 | 理由 |
|---|---|---|
| 后端语言 | Python + FastAPI + Mongo（motor 或与现有 garden 一致的驱动） | 与 garden1 现有栈统一 |
| 事件 vs 计划 | 事件 append-only 进开放标准；计划走 CRUD 状态 | Gantt 日期反复改，不是事实 |
| JSON 扩展策略 | 固定信封 + type 分型载荷 + 忽略未知 type | 简单与扩展兼得（CloudEvents 思路） |
| 联动机制 | 显式 DISPATCH 表（拒绝装饰器自注册） | AI 协作流程下可审性优先 |
| 消息队列 | 不用；同请求同步更新投影 + 夜间重放兜底 | 单人规模，幂等+重放已覆盖容错 |
| AI 例外请求（如临时任务不联动） | flags 物化到数据，handler 读 flags 跳过 | 可重放、可审计、可反悔；AI 不碰规则 |
| 部署粒度 | 3 后端（account/core/ai-gateway）+ 静态前端 | 单人运维上限；模块边界保留拆分期权 |
| 实时推送 | 默认轮询；可选 SSE"一声铃" | 副屏场景 5–10s 足够 |
| 认证 | 后置；全部数据带 user="u_local" 常量 | 零迁移接入 JWT |
| 占比 | plannedWeight 计划值 + 事件聚合实际值并存，默认显示实际 | 圆环随真实工作增长；偏差本身是核心信息 |

---

## 11. 现有资产与迁移说明

### 11.1 前端

- `project-task-contribution-ring.html`：完成度高。改造点：删 demoData、接 /views/current 轮询适配器、补"无任务运行"空闲态。
- `index.html`（NEXUS 控制台）：数据层从 localStorage 迁 API；localStorage 可降级为断网缓存。`progress` 从手动值改为计算值（完成任务比或实际/计划时长），保留手动覆盖。

### 11.2 garden1（/srv/ecs-services/garden1/，可重构）

现状：FastAPI 后端（注册/登录、JWT、日记提取——默认假提取器可配 DeepSeek、确认成长、花园状态、Pro 候补，Mongo 持久化）；主客户端为无构建工具的原生 JS Web 端（移动、虚拟摇杆、日记、分类规划、种树/成长、回放、账号）；树按 Tier2+Tier3 生长、每小分类 10 阶段、按日记防重复；日记正文限 2000 字、日志剔除原文；Godot 侧仅 FlowerPatch 原型，非主客户端。

**重构方向**：成长引擎从"只吃日记提取"改为"吃 yq-event 事件"。日记链路降为事件来源之一（diary.extracted）；判重从 diaryId 泛化为 dedupeKey；10 阶段、回放、防重复机制全部保留。"提取→确认成长"交互提升为全系统通用的 proposals 机制。garden 的隐私原则（不存原文、只存结构化分类/备注/日期）在全系统沿用。

已知待收尾（与本架构无关但勿丢）：nginx 因上游 copycat_api:8081 无法解析而反复重启致 /garden/ 502（修法见 §12）；mobile-demo.openapi.yaml 仍写"最多 3 条提取"与实际不符；布局 E2E 因环境无 Chrome 未重跑（建议换 Playwright 容器镜像）。

### 11.3 成长规则（garden 接入时定稿）

事件 → xp 的换算做成配置，起步建议：session 每满 30 分钟 = 1 xp；task.done = 3 xp；日记确认沿用现值。语义推荐"花园只反映发生过的事"：任务创建不长树，首条 session.completed / task.done 落地才冒芽（想"种下即见"则给 task.created 注册 plant_seed handler，一行注册的事）。

---

## 12. 运维备忘

nginx 上游一律用变量式写法，单服务下线不连坐：

```nginx
resolver 127.0.0.11 valid=10s;
location /xxx/ {
    set $upstream http://xxx_service:port;
    proxy_pass $upstream;
}
```

日常监控三件事：events 有没有落、DISPATCH 表对不对、夜间重算跑没跑。

---

## 13. 明确后置项（现在不做，防 scope creep）

- 账号/JWT 接入（锚点已埋：user="u_local"）
- 消息队列 / WebSocket
- 成长规则的用户可配置化与规则版本重放（v2）
- Godot 客户端（FlowerPatch 原型搁置，Web 端为主）
- 多用户/Pro 商业化功能

---

*本手册凝结自 2026-07 的架构讨论。若实现中发现与手册冲突且理由充分，先改契约文档、经总 arbiter 批准，再改代码——顺序不可颠倒。*
