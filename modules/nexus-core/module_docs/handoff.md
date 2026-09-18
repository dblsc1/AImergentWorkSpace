# nexus-core · handoff（冷启动接手便条）

> **面向未来接手**：新 AI 只读这一份就能上手——不是历史流水（那是 diary/worklog）。

## 一句话

**系统心脏**：唯一的事件写入口 + 计划状态（planner）+ 投影（projector）+ 四前端的读端（views）。
对外提供 `/api/core/*` 全部端点。不依赖任何业务模块——**它是被依赖的那个**，
只消费仓根的事件信封标准 `contracts/yq-event-v1.md`。

技术栈：Python + FastAPI + MongoDB（`repo.py` 是唯一能 import mongo 的层）。

## 怎么跑 / 怎么测

```bash
cd code/backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt   # 首次

# 起服务（env 缺失立即 die，没有弱默认值）
NEXUS_BIND=127.0.0.1:8000 NEXUS_MONGO_URI=mongodb://127.0.0.1:27017/ \
NEXUS_DB_NAME=nexus_core .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000

# 测试（需要真 Mongo，不是 mock）
# ⚠️ **别传 NEXUS_DB_NAME** —— 传真库名会被 conftest 硬拒绝（见「已知踩过的坑」）
NEXUS_MONGO_URI=mongodb://127.0.0.1:27017/ .venv/bin/python -m pytest -q

# 种子数据（幂等，按名字判重）
NEXUS_MONGO_URI=... NEXUS_DB_NAME=... .venv/bin/python seed_planner.py
```

Mongo 跑在 docker 容器 `honeycomb-mongo`，绑 `127.0.0.1:27017`
（**不是 27017**——那个端口上有本机其他系统的实例，别搞混）。

自检门：`code/backend/tests/`（pytest 套件；契约断言全部脚本化，多处含反向验证——
故意让实现退回错误行为，确认对应断言真的会红）。

## 接口

规范性定义在 `module_docs/contract.md`。
对外使用说明另有 `module_docs/CRUD-调用指南.md`（单条建/改/删，面向外部消费方，
非规范性）与 `module_docs/JSON导入导出指南.md`（导出→改→dry-run→apply 整批
同步，含可直接复制的 curl 示例，v1.7 新增）。

| 子模块 | 干什么 |
|---|---|
| `events/` | 唯一事件写入口：信封校验 → 防重 → 落库 → 触发 DISPATCH |
| `timer/` | 计时 start/stop；stop 组装 `session.completed` **投进事件入口**，不绕过 |
| `planner/` | zones/projects/tasks 的 CRUD 与只读面。**v1.6 新增两条子文件**（仍在 `planner/` 内，同 `deps.py` 拆分先例）：`guard.py`（actor 来源判定 + 高风险二次设防 + 审计留痕，**九条写路由的唯一执行通道**）、`audit.py`（审计流水的构造/摘要/脱敏与读端）。**v1.7 新增三条子文件**（JSON 一键导入编辑，同一拆分纪律）：`import_diff.py`（纯函数：payload 对当前库算成一份 diff 计划 + checksum，可写字段白名单反射自 `schemas.py` 的 `*Create`/`*Update`）、`import_apply.py`（把计划逐条经 `guard.run_write` 落库，创建→更新→删除子先于父的顺序）、`import_router.py`（`POST /api/core/import`，两段式 + `events`/`projections` 拒收）；`actor.py` 的 `with_actor` 从 `unified_router.py` 的私有 `_with_actor` 提升为公开函数，两个更新路径共用 |
| `projector/` | 事件 → 投影。`registry.py` 的 DISPATCH 表是**唯一联动真相** |
| `views/` | 四前端的读端，**纯只读**。v1.5 新增两条子文件（不是新子边界，仍在 `views/` 内，同 `planner/deps.py` 拆分先例）：`next_actions.py`（F-TODO-1，含图级环检测防历史脏数据死循环）、`review.py`（F-REVIEW-1，四块纯聚合） |
| `export/`（v1.3 新增） | 只读全量导出：跨 events/planner/projector 聚合既有 service 函数，自己零数据、零 mongo import |


## 架构：两本册子 + 一个状态机

理解这个模块只要抓住一件事——**系统里有两本册子，语义相反**：

| | 名册（planner） | 流水账（events） |
|---|---|---|
| 记什么 | **有哪些东西**：分区/项目/任务 | **发生过什么**：每段计时是一笔事实 |
| 能改吗 | 能改能删 | **只增不改不删** |
| 重复提交 | 建两次就是两个对象 | 防重键命中 → 只生效一次，返 200 **不是错** |
| 触发投影 | 否 | **是**（唯一触发路径） |
| 谁是真相 | 「现在长什么样」 | **「发生过什么」——它才是最终真相** |

`timer` 是横跨两者的**状态机**：
`start` 写 `timer_state`（一个当前状态，像名册）；
`stop` **组装 `session.completed` 去调 `events.ingest`**（走流水账），
成功后才清 `timer_state`。**绝不绕过 ingest 直写 events 集合**。

```
写入口（前端只能调这些，不许自己发明第四条路）
  /api/core/planner/{type}    名册增删改   ← v0.6 起唯一写路径
                              v1.6 起每条写都过 guard：判来源 → 高风险设防 → 留审计
  /api/core/timer/start|stop  计时状态机
  /api/core/events            事实追加（前端暂不开放）

读出口
  /api/core/views/current     圆环：当前在跑什么 + 占比
  /api/core/views/tree        项目表：全量树
  /api/core/views/gantt       甘特：计划×事实双图层，v0.8 项目层，v1.1 补任务层（此前漏记，本次补）
  /api/core/events            档案：x时x分–x时x分做了什么（v0.6 新增）
  /api/core/export            只读全量导出：zones/projects/tasks 原始文档 + events 原样全量
                              + 两张投影 + exportedAt（v1.3 新增，消费方 table「导出数据」按钮）
  /api/core/planner/audit     审计流水：谁在什么时候动了名册（v1.6 新增，**不含 events**，
                              seq 降序；AI 批量写崩在半路靠它查做到第几步）
  /api/core/views/next-actions GTD 待办区：可做/等待分类 + 环防御（v1.5 新增，F-TODO-1）
  /api/core/views/review      GTD 每周回顾：四块聚合，零新集合（v1.5 新增，F-REVIEW-1）
  /api/core/import            JSON 一键导入编辑（v1.7 新增）：默认 dry-run 零写入只回
                              diff 计划 + checksum，apply 须带上该 checksum 且服务端
                              对当前库重算比对不上就拒；events/projections 出现即 400；
                              删除需 allowDelete；actor 固定走人的路径，逐条经
                              guard.run_write，与统一入口共用同一条设防
```

### 累计时间为什么不是字段

「每任务/每项目花了多久」**不存在任务文档上**，而是 `proj_current` 投影里的
累加值——`current.handle` 每吃一条 `session.completed` 就累加一次。

存成字段就有**两个真相源**：流水账 vs 文档上的数字。补录一条历史、
或某次投影更新失败，两边永久分叉。而流水账不可改，**它才是真相**，
任何数字都必须能从它重算（`rebuild.py` 全量重放的位置就在这）。

**要按天/周统计 → 加投影，不加字段**：`registry.py` 的 DISPATCH 表加一行 +
写个 handler 写自己的集合，**现有代码零改动**。改 DISPATCH 必须单独一个 commit。

### 扩展性实况（不是文档自述，是核实过的）

| 要做的事 | 成本 |
|---|---|
| 给现有对象加字段 | 改 `schemas.py`+`service.py` 默认值+契约。**Mongo 不用迁移，但老文档没有新字段** |
| 加新实体类型 | `repo.py` 的增删改查已参数化，三类共用一套；加组 schema + 统一入口注册即可 |
| 加新事件类型 / 新投影 | DISPATCH 加一行 + 新 handler。**设计得最好的一块** |
| 改事件信封 | 只增不改不删；`extra="allow"` 保留未知字段 |

**全模块 1859 行，最大文件 261 行。** 分层是真的：`pymongo` 全仓只在 `repo.py`，
router 零业务逻辑，跨模块只调对方 `service.py`。

**教训（v1.4）：加字段要同一批过完 `XCreate`/`XUpdate` 两个模型，不能只顾一个。**
`plannedWeight` 当年切片 2 建 `TaskCreate` 时加了，`TaskUpdate` 却漏了——`_Strict`
的 `extra="forbid"` 让 PATCH 权重直接 422，且不报"缺字段"，报的是"多余字段"，
排查时容易看错方向。核对方法：新加字段时把该对象的 Create/Update/Out 三个模型
并排读一遍，同一个字段在三处的取舍（必填/可选/不出现）要能说出理由，不能"抄了
一半就当抄完了"——同 v1.2 那次「加任务级字段要过一遍全部读端」是同一类教训。

## 迁移与备份

**加字段时两条纪律缺一不可**：

1. **读老数据一律 `.get(字段, 默认值)`，不许硬取。**
   实证：`views/queries.py` 硬取 `project["key"]` 对切片1旧数据炸 500。
2. **写一个迁移回填。** `migrations/` 下按 `NNN_描述.py` 命名（NNN 是三位序号），导出 `DESCRIPTION` 与 `up(db)`。

只做 1 不做 2：默认值散落各处，迟早漏一处。
只做 2 不做 1：迁移跑之前照样炸，而且从备份还原回来的老数据又是旧形状。

```bash
.venv/bin/python migrations/migrate.py --dry-run   # 看要跑什么
.venv/bin/python migrations/migrate.py             # 真跑（幂等，已应用的不重跑）
```

迁移文件**只增不改**——别人的库已按旧内容跑过，改了就永久分叉。要修写下一个。
每个迁移**必须幂等**（用 `$exists: False` 之类条件）。

**备份**：

```bash
COCKPIT_ARCHIVE_DIR=<备份仓路径> bash code/backend/scripts/backup.sh              # dump → 备份仓 → commit → push
bash code/backend/scripts/restore.sh <archive> <目标库名>
```

备份落在**独立的 `cockpit-archived` 仓**（工作树外，不进业务仓 —— 备份进业务仓会让工作树每次备份后都永远是脏的），
按 `YYYY/M/D/` 分目录，带 `.meta.json` 记指纹与各集合条数。

**还原是覆盖性操作**：两个参数都必填、**目标库名没有默认值**、
会先打印要覆盖掉什么、要你照抄一遍库名确认、还原后自动对数。
理由见下方「已知踩过的坑」第一条。

**备份没演练过不算备份**：改 schema 或改备份机制后，
至少往一个 `*_test` 库还原一次并确认对数。2026-08-01 首次演练当场撞到
`mongorestore` 的 `nsFrom`/`nsTo` 星号数必须相等——**没演练就发现不了**。

## 避坑 / 冻结点 / 技术债

**冻结点（改之前先想清楚，这些是有代价换来的）**

1. **只有 `repo.py` 能 import mongo。** router 不放业务逻辑，跨子模块只调对方
   `service.py` 的公开函数。唯一例外：`projector` 没有 `service.py`，
   公开面是 `registry.dispatch()` + `handlers/current.py` 的 `handle()/read_current()`
   （rules.md §7.4 钉死，是设计决定不是漏网）。
2. **DISPATCH 表任何修改单独成 commit**，说明影响面。
3. **防重按 `dedupeKey` 不是 `id`。** 按 id 判重 = 防重完全失效，重试会重复落库。
4. **`recordedAt` 服务端盖章**，客户端填了也覆盖。
5. **占比是 0–100 不是 0–1。** ring 的校验期望 0–100；0.475 与 47.5 都是
   "合法浮点数"，机器分不出——这种歧义只会在联调时炸。
6. **空闲态返 `null` 不是 0。** 0 会被圆环画成一个真实存在但为零的弧。
7. **`id`/`key`/`name` 三分**（人类裁决 J10）：`id` 永不变且永不复用、事件只存它；
   `key` 搬移时重算、**无唯一约束**；`name` 随时可改。
   `key` 不做拼音音译（`塔`/`她` 会撞成同一个串）。
8. **timer.start 时把 task→project→zone 三级 id 快照进 `timer_state`**，
   stop 只读快照不再查 planner——否则 start 与 stop 之间数据变了会算错。
9. **任务的 `plan`/`dependsOn`（v1.1）是纯计划态，不进事件、不排程。** `dependsOn`
   成环检测只从「本次改动的那个节点」做 DFS，不必每次扫全图——前提是**每次写入都过
   这道校验**，图在任何时刻都无环，新环只能通过这一次改的边产生。删除被依赖的任务
   走 409（同「拒绝级联」语义，不是新的错误家族）。**排期冲突/越界不校验**（用户裁决），
   别看到「甘特要画冲突箭头」就手滑加一道后端拦截——那是展示层的事。
   **v1.2 修了 v1.1 留下的一处集成缝**：`views.tree.v1` 的任务节点当时没跟着补
   `plan`/`dependsOn`——只顾上了 `TaskOut` 与甘特任务层，而 `ring`/`table` 恰恰
   主要经 tree 读任务列表。**教训钉进 `contract-schemas.md`「与其他两条读端的对照」表**：
   下次任务级字段变更，先查那张表，别改完一个读端就当作那批字段"已经加完了"。

10. **`p_inbox`/`z_inbox` 是固定 id 的系统单例**（v1.5，F-INBOX-1），不走三类对象
    平常的 uuid 生成路径——`planner/inbox.py::ensure()` 幂等建、`is_protected_project()`
    判断"是不是它"。`delete_project` 对它恒 409，与「拒绝级联」复用同一个异常类
    但触发条件不同（"系统单例"而非"有子对象"），**别把两种 409 的判据混在一个
    `if` 里**——语义不同，只是外部表现（状态码+异常类）相同。
11. **~~`actor`/`lastWriter` 是 v1.5 的已知信任洞~~ → v1.6 已关闭**（波2-C）。
    现在的口径是：**来源看凭据头 `X-Nexus-Client-Token`，压过请求体自报**；
    高风险写（DELETE / 改 `projectId`、`zoneId`、`plan`）有效 actor 为 `ai` → **403**，
    拒在任何库写入与存在性检查之前。三条判断留在这里，别重新讨论：
    ① **"自称 ai"谁说都信，"自称 human"必须有凭据**——降权声明无条件采信，
      提权声明才需要出示凭据；冒充 AI 拿不到任何东西。
    ② **高风险看"请求体出现了哪个键"，不看"值有没有真的变"**——后者要先读旧值，
      读写之间有竞态，而且传了 `plan` 又传回旧值的调用确实伸手碰了计划期。
    ③ **403 必须拒在存在性检查之前**——否则 404/409 的差异就是个 id 探测器。
    **剩余缺口（登记在案）**：未携凭据的调用方仍按 `human` 放行，因为四个前端都还
    没带凭据、注入点在 `nginx-docker` 网关（跨模块，须排期）。严格模式
    `NEXUS_ACTOR_STRICT` 已实现且有测试，**默认关**，别在网关注入落地前打开。
13. **审计流水 `planner_audit` 与 `events` 是两本账，永远别合并**（v1.6，F-ACTOR-2）。
    `events` 是跨模块开放标准（信封只增不改不删），审计是本模块的
    运维追溯（字段会随需求长）。混在一起 = 内部想多记一列就得改所有人的脚本，
    或者审计被信封的不变性绑死。审计**只许 insert/find**（AST 断言守着），
    三种 outcome（`applied`/`denied`/`failed`）都记——只记成功等于把攻击痕迹
    和"批量写崩在第几步"一起丢掉，而后者正是这张表存在的理由。
12. **next-actions 的图级环检测是防御性代码，不是常规路径**：`planner/deps.py`
    的写时校验已经保证"正常写入之后"图无环，本读端的环检测防的是**写时校验
    加入之前**写入的历史数据（或任何绕过 API 直连 Mongo 写入的数据）。**别因为
    "写时已经校验过了"就删掉读端这道防御**——两者防的是不同时间点的风险。
14. **JSON 一键导入编辑的 checksum 判据是无状态纯函数，不是服务端会话**
    （v1.7）：dry-run 与 apply 调的是同一个 `import_diff.build_plan`，
    "过时"就是"对当前库重新算一遍，checksum 变了"——**别为了"更保险"加一个
    服务端缓存/Redis 去记"上一次算出的计划"**，那样反而引入了"缓存和真实
    状态谁更新"这个新问题，纯函数重算本身就是最简单也最正确的答案。
    apply **不是事务**：遇到第一个失败（哪怕是既有的级联保护 409）就中止，
    已成功的动作不回滚——与 `guard.run_write` 一贯的哲学一致，批量写崩在
    半路靠 `GET /api/core/planner/audit` 按 `seq` 查"做到第几步"。**同一批
    不支持"父子两级都新建"**（新建项目的 `zoneId`/新建任务的 `projectId`
    必须指向已存在于库中的对象）——这是有意的范围收窄，别看到"编辑器里
    改数据"就手滑加一套占位符 id 解析。

**技术债**

- `planner/repo.py` 每次写入都调 `create_index`，其余三个 `repo.py` 用
  `_indexes_ready` 缓存。不是 bug（幂等），但同仓内不一致，多余 DB 往返。
- ~~统一入口与十二条旧端点并存~~ → **v0.6 已删旧端点**，`/api/core/planner/{type}` 是唯一写路径。
- `logs/diary.jsonl` 不入仓（高频流水）；`logs/ledger.jsonl` **必须入仓**
  （记录关键操作绕过常规护栏时的问责账本）——别把 `logs/` 一刀切忽略，那会让这类记录变静默。

**已知踩过的坑**

- **测试套件会清空你指向的任何库。** `conftest.py` 的 `clean_db` 是 `autouse`，
  每个测试前 `delete_many` 全部八个集合。2026-08-01 我传了 `NEXUS_DB_NAME=nexus_core`
  跑测试，62 个测试清了 62 遍，**用户的计时历史全没**——事实 append-only、
  当时无备份、不可恢复。现在 conftest 硬拒绝非 `_test` 结尾的库名。
  **跑测试就别传 `NEXUS_DB_NAME`。**
  教训不是「别手滑」，是**默认值只防「没传」、不防「传错」**——危险操作要硬拒绝，不是给默认值。

- 曾因 `views/queries.py` 硬取 `project["key"]` 而对**切片1 时期的旧数据**炸 500
  （那批数据早于 key 生成逻辑）。改 schema 后要想清楚存量数据怎么办。
- `git commit | tail` 拿到的是 `tail` 的退出码（恒 0），**判成败只认 `git log`**。
- **新增 Mongo 集合必须同批检查仓里全部硬编码的集合名登记表**（E1 事故，2026-08-11）：
  `code/backend/conftest.py` 的 `_COLLECTIONS` 是清库表，但仓里不止这一份**同形状**的
  集合名列表——v1.6 新增 `planner_audit` 时只改了这一份，另一份漏改，清库清了
  `counters`（序号归零）却没清 `planner_audit`（旧记录还在），下次 seq 从 1 重发直接
  撞 `uniq_seq` 唯一索引，崩在审计段、丢的是三十多条契约断言，**症状离病因很远**。
  这个类值得有脚本盯：断言全部集合名登记表互相同步、且覆盖 `app/` 实际用到的全部集合名。
  判据必须是**静态的**——只看代码里写死了哪些集合名，不看「库里现在有哪些」
  （那会被历史遗留集合污染成假红）。**新增集合时它该红**，靠人巡查一定会漏。
- **通配地址检测曾经扫到"提到 `0.0.0.0` 的散文"，不是只扫真配置**（2026-08-11 实证）：
  文档里描述测试结果时提到 `0.0.0.0`、记录 Dockerfile 改动时提到 `0.0.0.0`，
  都被判成违规，与 Dockerfile 里真实的
  `--host 0.0.0.0`（容器内必须绑全网卡，暴露面由 compose 端口映射控制，这处是有意的）
  混在一条报错里——**误报的门禁比没门禁坏，会教下一个 agent 去改文档迎合扫描器**。
  正解有两条：扫描面**只认源码**（�文档散文不算，扩展名白名单要有唯一事实源），
  真正需要写死的那处走**显式豁免**（写明理由，且通过消息里要报出豁免了什么，
  **不是静默放行**）。
  下次再新增一个"配置里必须写死但契约不允许"的地址/口令类判据，照这个形状办：
  扫描面先收窄到源码，例外走显式豁免名单，不要靠"没扩展名"意外漏扫。
