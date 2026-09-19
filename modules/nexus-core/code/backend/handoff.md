# code/backend · 一页纸（切片 1「金链路」落地后）

> 每次改 `code/backend/` 都要同批核这页（门禁 14）。谎话比空白更糟——写不准就删行。

## 是什么

nexus-core 的 FastAPI 后端。切片 1 已是**真实现**（mock/fixtures 已退役）：

```
timer.stop → session.completed → POST /events（校验+防重+落库）
           → projector DISPATCH → proj_current → GET /views/current
```

## 怎么跑 / 怎么测

```bash
cd code/backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
NEXUS_MONGO_URI=mongodb://127.0.0.1:27017 NEXUS_DB_NAME=nexus_core NEXUS_TZ=Asia/Shanghai \
  .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
NEXUS_MONGO_URI=... NEXUS_DB_NAME=... NEXUS_TZ=... .venv/bin/python seed_planner.py   # 一次性种子
NEXUS_MONGO_URI=mongodb://127.0.0.1:27017/ .venv/bin/python -m pytest -q   # 单测（conftest 自带 nexus_core_test + NEXUS_TZ=UTC，别传 NEXUS_DB_NAME）

# 投影重建（新增/改了归日口径后必跑；契约「投影重建」v0.9）：
NEXUS_MONGO_URI=... NEXUS_DB_NAME=nexus_core NEXUS_TZ=... \
  .venv/bin/python -m app.modules.projector.rebuild [--only proj_current|proj_daily_stats]
```

三个变量（`NEXUS_MONGO_URI`/`NEXUS_DB_NAME`/`NEXUS_TZ`）**均无默认值，缺失或非法立即失败**。
存储：容器 **`honeycomb-mongo`**（2026-08-10 起改名，见「冻结点」），
绑 127.0.0.1:27017，生产库名 `nexus_core`。
`NEXUS_TZ` 是 2026-08-02（本轮）新增的必填项，IANA 时区名（如 `Asia/Shanghai`）——见下方
「日界与时区」一节，**别猜、别配成 UTC 图省事**：猜错时区会把用户的工作静默记到错误的日子。

## 结构（＝将来的分拆线）

| 子边界 | 文件 | 职责 |
|---|---|---|
| events | router/schemas/service/repo | 事实唯一写入口：校验→盖 recordedAt→防重→落库→触发 projector。**v0.6 新增只读档案端** `GET /api/core/events`（同一 router 的 GET，`service.list_events`）：按 `type`/`from`/`to`/`limit`/`offset` 过滤，`time` 倒序，不 join 名字；`ingest`/DISPATCH 表零改动 |
| timer | router/service/repo/**backfill** | 活状态；stop 组信封走 events 入口（不许直写 events）。补登见下方专节 |
| planner | schemas/service/repo/**unified_router**/**deps**/**errors**/**planvalidate**/**actor**/**inbox** | **完整 CRUD**：三类对象 id/key 全系统生成；删除拒级联 409 带剩余数；改名/换归属只重算自己的 key、不动 id、不级联子对象；错误映射（400/404/409）在 `app/main.py`；种子 `seed_planner.py` 全走 create_*，不许手编 id/key。**v0.6 起 `unified_router.py`（统一入口 `/api/core/planner/{type}`，`GET/POST/PATCH/DELETE`，`{type}` ∈ `zones\|projects\|tasks`）是唯一写路径**——原来分离的十二条旧端点（`planner/router.py`）**已删除**（v0.6 产品决定「旧端口清掉」，v0.5 时期的新旧并存是过渡不是设计）。统一入口仍是**只做路由转换**：已知 type 各有静态路由，调的是 `service.py` 的函数与 `schemas.py` 里的类型化模型；未知 type 由文件末尾的 `/{type}` 兜底路由处理（404，点名收到值+合法取值）；**兜底路由必须注册在最后**，放前面会把 `/planner/zones` 等合法路径也吞掉（Starlette 按注册顺序做首个匹配命中）。**v1.1 新增**：任务 `plan`/`dependsOn`（见下方「任务级排期与依赖」一节）；`deps.py`（存在性/自指/成环 DFS 校验）与 `errors.py`（四个域异常类真身，供 `service.py`/`deps.py` 共用而不互相 import 成环）。**v1.5 新增三个文件**（同样是把 `service.py` 拉回 300 行预算的拆分，不是新子边界）：`planvalidate.py`（`plan` 格式/区间校验，从 `service.py` 原地搬出，逐字节不变）、`actor.py`（`actor`→`lastWriter` 的建/改两路翻译，供 `service.py` 六处写入口各调一行）、`inbox.py`（well-known `p_inbox`/`z_inbox` 固定 id 单例，`ensure()`/`is_protected_project()`）。**v1.7 新增三个文件**（JSON 一键导入编辑，见下方专节）：`import_diff.py`/`import_apply.py`/`import_router.py`。 |
| projector | registry/repo/handlers/current+daily_stats/**rebuild** | DISPATCH 表唯一联动真相；handler 幂等、只写自己的投影集合。`handlers/daily_stats.py`（写 `proj_daily_stats`，甘特「事实」图层的数据源，`date` 取 `data.startAt` 不是 `time`——跨零点的一段计时归到开始那天，理由见该文件顶部注释；**归日按 `NEXUS_TZ`**，2026-08-02 新增，见下节）；`repo.py` 一份文件管两张集合（`proj_current` + `proj_daily_stats`），另加 `clear_current`/`clear_daily_stats` 两个**仅供 rebuild 调用**的清空函数——红线是「只有 repo.py 能碰 mongo」，不是「一集合一文件」。**新增 `rebuild.py`**（契约「投影重建」v0.9）：`python -m app.modules.projector.rebuild [--only <投影名>]`，只读 `events`（走 `events/service.py::iter_all_events`，不直接 import `events/repo.py`）、只写投影，先清后放、天然幂等。**v1.1（O1）/v1.5：本子边界零改动**——任务级「某天多少分钟」与 GTD 待办/回顾两条新读端都直接读现有 `proj_daily_stats`，见 views 一行。 |
| views | router/queries/schemas/**next_actions**/**review** | 纯读端；数据来自 timer.service + planner.service + projector 的 read_current/read_daily_stats；**三类对象响应都带 `key`**（v0.4）——库里对象必须经系统路径创建（旧手编数据缺 key 会 KeyError，重跑种子即修）。**不读 events 集合**（红线，见下）——档案读端因此不能放在这里，归 events 子边界自己。`GET /views/gantt`（v0.8）：project 级 plan（读 planner）+ actual（读 `proj_daily_stats`，同一天跨 task 已在 queries.py 里求和）叠加；`today` **改用 `NEXUS_TZ` 下的日期**（2026-08-02，契约「日界与时区」v0.9）——与 `daily_stats.py` 的归日共用同一个函数 `app/timeutil.py::local_date`，不许各自算一遍。**v1.1**：每个 project 之下再挂 `tasks[]`（O1）——同一份 `proj_daily_stats` 行**额外**按 `(projectId, taskId)` 分组一次（原有按 `projectId` 求和的项目层逻辑一字不改），`taskId` 为 `None` 的行只计入项目层、不进任何任务的 `actual`。**v1.5 新增两个文件**：`next_actions.py`（`GET /views/next-actions`，F-TODO-1，含图级环检测防历史脏数据死循环，见下方「GTD 待办区/回顾读端」一节）、`review.py`（`GET /views/review`，F-REVIEW-1，四块纯聚合）——都只调 `planner.service` 与 `projector.handlers.daily_stats`，零新读路径、零新投影。 |

红线：mongo import 只在 `repo.py`（共享连接工厂 `app/repo.py`）；跨子边界只调对方
`service.py`（例外两处：views 读投影走 `projector/handlers/current.py` 的
`read_current`、`projector/handlers/daily_stats.py` 的 `read_daily_stats`，
这是 rules.md §7.4 钉死的公开读路径）；registry 的 DISPATCH
表任何修改单独成 commit。当前路由面：`session.completed → (handlers/current,
handlers/daily_stats)`——一个事件类型现在挂两个 handler，各写各的投影集合
（`proj_current` / `proj_daily_stats`），互不干扰、都不发新事件。

## 冻结点 / 避坑

- **`code/backend/scripts/backup.sh`/`restore.sh` 默认容器名必须跟着 compose
  走**（曾出过 P0：部署侧改了容器名、脚本默认值没
  跟着改，静默连不上）——两脚本拿到容器名后立刻 `docker exec "$CONTAINER" true`
  探活，连不上即 die。
- **v1.5：`p_inbox`/`z_inbox` 固定 id 系统单例**（`planner/inbox.py`），`delete_project`
  恒 409（判据 `is_protected_project`，与「拒绝级联」分开判，不合并 `if`）。
  `views/next_actions.py` 的图级环检测防的是写时校验落地前的历史脏数据，非常规路径。
- **v1.6：九条写路由的唯一通道是 `planner/guard.py::run_write`**（来源判定 → 高风险设防 → 执行 → 留审计）。**新增写端点必须接上它**，否则那条路径没有二次设防——`tests/test_actor_source_guard.py` 有 AST 断言盯着，漏接直接红（v1.5 那个「actor 只落字段不查来源」的信任洞本轮已关闭，别再照旧注释理解）。三条别踩：① `_with_actor()` 未携凭据时**整个 `actor` 键都不放进 fields**（塞 `actor: None` 会把"不改 lastWriter"变成"改成非法值"）；② `GET /planner/audit` 必须注册在 `{type}` 兜底之前；③ 审计集合 `planner_audit` **只许 insert/find**（AST 断言守着），"修一条记错的审计"＝违反 append-only。
- **新增集合必须同批进清库表**：`conftest._COLLECTIONS` 有断言守着（`test_every_collection_is_registered_for_cleanup`），但这不是仓里唯一一份同形状的集合名登记表——加集合时也要记得手动检查并同步其余登记表，否则漏掉的那份会让旧数据在测试间残留，进而在完全无关的地方（如 `uniq_seq` 撞重号）直接崩。
- **v1.6 的已知缺口（登记在案，不是遗漏）**：未携带凭据的调用方仍按 `human` 放行，因为四个前端都还没带凭据、注入点在 `nginx-docker` 网关（跨模块）。严格模式 `NEXUS_ACTOR_STRICT=1` 已实现且有测试，**默认关**；网关注入落地前打开它 = 前端的搬移/改期/删除全被 403 打死（配置层已让这种组合启动即 die）。
- **防重键是 `(user, source, dedupeKey)` 唯一索引**（`events/repo.py`），`id` 上没有索引——B2 要求同 id 两条都落，别"顺手"加唯一。
- **事件 `subject` 只存三个 opaque id `{zone, project, task}`**（J10）；`tier2`/`tier3` 是已废旧模型，出现即拒（B5b）——**显式拒绝，不是静默忽略**，别"宽容"回去。
- **标识三分**（zones/projects/tasks）：唯一约束只压 `id`；`key` 四段（分区号-项目号-名字号-同名序号）来自 `name_registry` 登记表 + `counters` 自增，**不建索引、绝不做拼音音译**（塔/她 塌陷）；`name` 中文原样。timer 在 start 时快照三个 id 进 `timer_state`，stop 不再查 planner。
- **`recordedAt` 盖章在 `events/service.py`**，客户端值一律覆盖；`time` 用客户端的。两字段永不合并。
- **投影幂等**靠 `proj_current.appliedKeys` 的原子守卫（`projector/repo.py`），不靠调用方自觉；DuplicateKeyError = 已应用过，是正常分支。
- **timer.stop 先 ingest 后清 timer_state**：顺序反过来会丢事件；重试靠稳定 dedupeKey 防重。
- 测试库默认 `nexus_core_test`（conftest 注入）。
  **⚠️ 但这句话在 2026-08-01 之前是假的**：那时用的是 `setdefault`，
  只在「你什么都没传」时生效；显式传 `NEXUS_DB_NAME=nexus_core` 就会
  **照着真库清**——`clean_db` 是 autouse，每个测试前 `delete_many` 清空
  全部八个集合，62 个测试清 62 遍。用户的计时历史就是这么丢的
  现在 conftest 会**硬拒绝**非 `_test` 结尾的库名。**别去绕它。**
  跑测试就别传 `NEXUS_DB_NAME`。
- **CRUD 三条硬语义**（v0.4）：删除拒级联（409 带剩余数，先清子再删父）；id 任何情况不可变不复用（repo 层有断言）；校验指名道姓（400 点名字段/id，404 与 400 分流）。
- **可选入参的服务端默认**（契约未定、worklog 已报备）：zone.color `#999999`、zone.order=现有数、plannedWeight `100.0`。契约补齐后改 `planner/service.py` 一处即可。
- **v1.4：`TaskUpdate` 补 `plannedWeight`**（`TaskCreate` 一直有，`TaskUpdate` 漏加过，
  PATCH 权重曾被 `extra="forbid"` 判成 422），复用既有 `_check_weight`，非新校验。
- **统一入口 `unified_router.py` 的路由注册顺序不能改**：`/zones` `/projects` `/tasks` 的静态路由必须在文件里排在 `/{type}` 兜底路由之前，否则兜底会把合法 type 也吞成「未知 type」。以后要加第四类对象，同样得把它的静态路由插在兜底之前。
- **v0.6：十二条 planner 旧端点（`/api/core/{zones,projects,tasks}`）已删除**，`planner/router.py` 整个文件随之删除（main.py 不再 include 它）。`/api/core/planner/{type}` 是唯一写路径。**`modules/hive` 前端仍在直接调旧端点，删除后它会暂时坏**——这是产品决定 2026-08-01 明确接受的代价，前端迁移不在本目录职责内、另行处理，不要为了不破坏它而恢复旧端点。
- **档案读端 `GET /api/core/events` 放在 `events/` 子边界，不放 `views/`**：`views/` 的红线是「不读 events 集合」，档案读端恰恰要读 events，所以只能长在 events 自己的 router/service/repo 里；与 `POST /events`（ingest）共用 `/events` 前缀但函数完全独立。`from`/`to` 的比较在 `service.py` 里解析成 `datetime` 后再比——`time` 字段带任意时区偏移，直接对 ISO8601 字符串做字典序比较在跨时区时会算错顺序。
- **甘特「计划」写侧复用既有 PATCH，不开专用端点**：`PATCH /api/core/planner/projects/{id}` 的 `plan` 字段真做校验（`planner/planvalidate.py::validate_plan`，v1.5 从 `service.py` 搬出，逐字节不变）——`start`/`end` 必须 `YYYY-MM-DD`、`end` 不得早于 `start`，违反 400 点名字段；`plan:null` 合法。create/update 共用同一份校验函数。
- **`proj_daily_stats` 唯一约束是 `(user,date,projectId,taskId)`**，不是 `user` 单字段（那是 `proj_current` 的约束）——`taskId` 可以是 `None`（外部事件可以没有具体任务），`None` 参与唯一索引没问题。幂等实现与 `proj_current` 同构：`appliedKeys` 数组 + 原子 `update_one` + `DuplicateKeyError` 兜底。
- **日界按 `NEXUS_TZ` 切，不是 UTC**（2026-08-02，契约「日界与时区」v0.9）：`NEXUS_TZ` 必填无默认值；
  `app/timeutil.py::local_date(moment, tz)` 是**唯一**「时刻→本地日期」换算函数，`daily_stats.py`
  的归日、`views/queries.py` 的 `today`、`timer/service.py::backfill` 的响应 `date` 都调它，不许
  各自写一遍。**事件本身不动**：改 `NEXUS_TZ` 后重放即得新日界，历史事实一个字节不用改。要害断言：
  `tests/test_timezone_and_rebuild.py::test_t2_cross_midnight_boundary_uses_nexus_tz_not_utc`。
  **`proj_current` 无日期维度，不需要动**。
- **投影重建 `rebuild.py`**（同上，契约「投影重建」v0.9）：`--only` 的「投影名→(handler,clear函数)」
  映射是 rebuild.py 自己的一张小表，**只读** `registry.DISPATCH` 判断某条事件该不该喂给某个
  handler，不修改 DISPATCH 本身。幂等靠「先清后放」天然保证，不是额外加的去重逻辑。
- **`GET /api/core/health` 暴露当前库名**（2026-08-02，契约「健康检查暴露库名」v1.0）：
  返回 `{"status":"ok","db":"<settings.db_name>"}`。让 E2E 能跨进程机械核验「打的是
  不是生产库」——单测护栏只在进程内可判，E2E 打 HTTP 看不见对端库名，两处用同一判据形状。
- **陈旧 UTC 断言已修复**（2026-08-02，期望值现算不写死字面量，多时区检查脚本
  防复发）。⚠️ 极端时区下 `test_gantt.py` 四条同形状假红**仍未修**，留给下一轮。

## 只读全量导出 `GET /api/core/export`（2026-08-09 新增，契约 v1.3）

新增子边界 `export/`（`router.py`+`service.py`），聚合六路既有只读函数（`planner.list_*`、
`events.iter_all_events`——**无 1000 条上限**、`projector.handlers.{current,daily_stats}.read_*`）。
**零直连 mongo、零新投影**。不挂进 `views/`——那里的红线是「不读 events」。测试见 `tests/test_export.py`。

## JSON 一键导入编辑 `POST /api/core/import`（2026-08-12 新增，契约 v1.7）

三个新文件留在 `planner/` 内（不新开子边界，重的是复用 `guard`/`audit`/
`service`/`repo`）：`import_diff.py`（纯函数 `build_plan`：payload 对当前
库算成 create/update/delete 计划 + checksum；可写字段白名单**反射自**
`schemas.py` 的 `*Create`/`*Update`，不另写清单）、`import_apply.py`
（按创建→更新→删除子先于父的顺序逐条经 `guard.run_write` 落库，
`body_actor` **固定 `"human"`**——AI 凭据自报 human 照样被判伪装 403，
更新动作复用提升为公开的 `actor.with_actor()`）、`import_router.py`
（两段式，`events`/`projections` 出现即 400）。

**checksum 是无状态纯函数**：`{"allowDelete","zones","projects","tasks"}`
（算出来的计划）排序后 `sha256`。dry-run/apply 调同一个 `build_plan`，
"过时"＝"重算结果变了"——**别加服务端缓存去记上一次的计划**。apply 不是
事务，遇到第一个失败（含既有 409 级联保护）就中止不回滚。**已知限制**：
同一批不支持"父子两级都新建"（新建的 `zoneId`/`projectId` 必须指向已存在
于库中的对象）。零新增集合，清库表不用改。测试见 `tests/test_import.py`
（19 条）。

## 技术债（接手者注意）

- `POST /events/import`（文件导入门）未实现：contract v0.2 API 表未承诺它，等进契约再做。
- `proj_trees` 投影：仍是后续切片。
- ~~内部审核脚本套件当时暴露的多处脚本级问题（mock 口径、测试引导缺 `NEXUS_TZ`、
  健康检查判据过期、契约形状加载器只读单文件）~~
  **均已核实修复**（2026-08-08 复核全绿，含两组反向验证 selftest）——这几条曾经在
  本文件占了很长篇幅，现在全部过期，按本文件开头的纪律删掉，只留这一行销账记录。

## 迁移与备份（2026-08-01 新增）

```bash
# 迁移（幂等，已应用的不重跑）
.venv/bin/python migrations/migrate.py --dry-run
.venv/bin/python migrations/migrate.py

# 备份（dump → cockpit-archived 仓 → commit → push）
COCKPIT_ARCHIVE_DIR=<备份仓路径> bash scripts/backup.sh

# 还原（两参数必填，目标库名无默认值，会要你照抄库名确认）
bash scripts/restore.sh <archive文件> <目标库名>
```

**加字段时两条纪律缺一不可**：读老数据一律 `.get(字段, 默认值)` 不许硬取；
同时写一个迁移回填。只做一条都会漏——细节见 `../../module_docs/handoff.md`。

**迁移文件只增不改**（别人的库已按旧内容跑过，改了就永久分叉），**必须幂等**。

**备份没演练过不算备份**：改 schema 或改备份机制后，至少往一个 `*_test` 库
还原一次并对数。首次演练当场撞到 `mongorestore` 的 `nsFrom`/`nsTo`
星号数必须相等——不真跑就发现不了。

## 孤儿事实清理（2026-08-03 新增）

```bash
# 默认 dry-run：只打印会删什么，一条都不动
NEXUS_TZ=Asia/Shanghai bash scripts/prune-orphan-events.sh <库名>

# 真删：会重打库名确认 + 先自动备份（备份失败即中止）+ 删完自动重建投影
NEXUS_TZ=Asia/Shanghai COCKPIT_ARCHIVE_DIR=<备份仓路径> \
  bash scripts/prune-orphan-events.sh <库名> --apply
```

**「造完自己清」这个模式在 append-only 系统里会制造孤儿。** E2E 夹具的 teardown
删掉了它自己造的 project/task，可它注入的 `session.completed` 事实里那几个 id 还指着
它们 —— 前端 join 不到名字，档案页就显示成 `p_xxxxxx（项目已删除）`。
**写 E2E 夹具时要么别往真库写，要么连它造的事实一起清**，只清实体是最坏的那一半。

删 events **不违反 append-only**：append-only 保护的是真实发生过的事实不被改写，
不是保护我们自己制造的污染，清掉工具注入的产物是**恢复真相**（完整版见
`code/backend/scripts/prune_orphan_events.py` 头部，改工具前先读）。

三件**别拆**的东西：**判据只认 `id`，不是 `_id`**（J10 三段式，`_id` 是
Mongo ObjectId，拿它比会得到「全部都是孤儿」这种假结论，`test_prune_
orphan_events.py` 有反向验证）；**选谁删看 `id`，执行删除必须用 `_id`**
（事件 `id` 不唯一，契约 B2，拿它当删除条件会误伤旁边那条）；**`NEXUS_TZ`
必须显式给**（删完要重建投影，用错时区会把历史静默分桶到错误的日子）。

`subject.task` 在契约里**选填**：没带 task 的事实没有悬空引用，**不是孤儿**。
按「task 不在 tasks 的 id 集合」的字面判据会连它一起删，那才是真的篡改历史。

## 取消计时 `POST /api/core/timer/cancel`（2026-08-03 新增）

**`stop` 是「这段时间发生过」，`cancel` 是「这段时间不算数」。**

🚫 **绝不许把 cancel 实现成「先 stop 再删事实」**——这是本节唯一的要害：
`events` 是 append-only、无软删除、无回收站，`session.completed` 一进事件
入口 DISPATCH 表当场就派给两个 handler，删除发生在那之后已经晚了，那一瞬
读投影的东西会读到这条假事实。所以 `timer/service.py::cancel()` **不
import、不调用** `events_service`，全部副作用只有一行 `repo.clear_
running(user)`；`test_reverse_cancel_via_stop_turns_red` 反向验证：换成内部
调 stop，T1 必须变红。

**没在计时时 cancel 返 409，而 stop 返平静 200 —— 这个不一致是有意的**：
stop 是幂等的「确保没在计时」；cancel 没有可丢的就必须说出来，静默 200
会让界面显示「已取消」而什么都没发生（冲突的是当前状态，同 `HasChildrenError
→ 409`，映射在 `app/main.py`）。

出参 `TimerCancelOut` **没有 `event` 字段**（`TimerStopOut` 有，可为 null），
让「取消不记账」在类型上就成立；`discardedSeconds` 是 `now - startAt`
现算的回显值，任何地方都没存。⚠️ 契约尚未登记本端点，见后续版本补记。

## 单条事实删除 `code/backend/scripts/delete-event.sh`（2026-08-03 新增）

```bash
# 默认 dry-run：打印那条事实的完整内容与前后条数，一条都不动
NEXUS_TZ=Asia/Shanghai bash scripts/delete-event.sh <库名> <事件id>

# 真删：重打库名确认 + 先自动备份（失败即中止）+ 删完自动重建投影
NEXUS_TZ=Asia/Shanghai COCKPIT_ARCHIVE_DIR=<备份仓路径> \
  bash scripts/delete-event.sh <库名> <事件id> --apply
```

**与 `prune-orphan-events.sh` 分工别混**：prune 删**一类**（判据写死），本
工具删**一条**（人给精确 id）——那类假事实指向的任务真实存在、不是孤儿，
prune 抓不到，这就是本工具存在的理由。

三件**别拆**的东西：**只接受精确 id，不接受任何查询条件**（批量删已由
prune 提供，按条件删等于绕过那道判据）；**id 匹配到多条时拒绝执行**（契约
B2 允许同 id 并存，要求 `--dedupe-key` 消歧，定位用 `id`、执行删除用 `_id`，
同 prune）；**库名必填、`NEXUS_TZ` 必填**（前者删除不可逆，后者删完要重建
投影，猜错时区会把历史静默分桶到错误的日子）。

（规格写的签名是 `delete-event.sh <事件id>`，实现多要一个库名——「`--apply`
要重打库名」这道闸没有库名参数就不成立，取闸门、不取字面签名。）

## 任务级排期与依赖（2026-08-08 新增，契约 v1.1）

`TaskOut` 增 `plan {start,end}|null`（复用 `Project.plan` 那份 `_validate_plan`）
与 `dependsOn: [taskId]`（默认 `[]`）。两者都是**计划态**，不进事件、不排程。

**三条校验在 `planner/deps.py::validate_depends_on`**：存在性（查 `repo.get_task`）、
自指（`task_id == dep_id` 单独判，消息比「成环」更精确）、成环（DFS，见下）。
`planner/errors.py` 装四个域异常类的真身——原定义在 `service.py`，`deps.py`
想复用又不想反向 import 成环，拆出去后两边都 `from .errors import ...`。

**成环检测只从改动的那个节点做 DFS，不扫全图**——前提是「每次写入都过这道校验」，图因此在任何时刻都无环，新环只能通过这次改的边产生；别为了"更保险"改成每次全图扫描，那不是更对，只是更慢。

**删除被依赖的任务 → 409**：`planner/repo.py::find_dependents(task_id)` 用
`{"dependsOn": task_id}` 查（Mongo 对数组字段用标量值查询天然是「包含」语义），
复用既有 `HasChildrenError`，报文列出全部依赖者的 name+id。**不建索引**——
线性扫描 `tasks` 集合，在个人任务管理场景的数据量下够用，索引收益不确定就不加。

**排期冲突与越界排期均不校验**（用户裁决，契约「排期与依赖」明文）：任务 `plan`
可以超出项目 `plan` 的范围、可以互相重叠或倒序、项目本身可以没有 `plan`——
自动排程是完全不同量级的功能，这里不做。**别看到甘特要画冲突箭头就手滑加一道
后端拦截**——冲突提示是展示层的事。

**O1：`/views/gantt` 补任务层，不是新投影**。`proj_daily_stats` 从 v0.8 起就按
`(user,date,projectId,taskId)` 存，`taskId` 从来不是装饰性字段——只是
`views/queries.py::get_gantt` 此前按 `projectId` 求和把它读丢了。v1.1
`get_gantt` 在原有分组之外**再按 `(projectId, taskId)` 分组一次**，挂进每个
project 的 `tasks[].actual`。**因此不需要跑 `projector.rebuild`**：没有新投影、
没有改 DISPATCH、没有改归日口径，生产库里的历史数据从写入那天起就有正确的
任务粒度。**下次真的需要新投影时**（比如要给任务粒度单独建索引、或聚合口径
与项目层分道），再照这个思路重新评估，不要因为「上次没开新投影」就默认这次也不开——
判断标准始终是「这份数据是不是已经在某处以够用的形状存在」。

`migrations/002_backfill_task_deps.py`：给缺 `dependsOn`/`plan` 的老任务文档
回填 `[]`/`null`。读取侧的默认值（`TaskOut.dependsOn` 的 `Field(default_factory
=list)`、`views/queries.py` 的 `.get(...) or []`）已经能兜底「不炸」，回填是
纪律 2（两条纪律缺一不可），**未对生产库 `nexus_core` 跑**——不紧急，留给
人类按需执行：`NEXUS_MONGO_URI=... NEXUS_DB_NAME=nexus_core NEXUS_TZ=...
.venv/bin/python migrations/migrate.py`。

**v1.2 补的集成缝**：这批字段当初漏了 `views/schemas.py::Task`（`GET /views/tree`
用，`ring`/`hive` 实际读任务列表主要经这条，不是 planner 分页列表）。**教训**：
加任务级字段要过一遍全部读取该实体的端点（tree/gantt/planner CRUD 三处），
改完一个不代表改完了；对照表见 `module_docs/contract-schemas.md`「与其他两条读端的对照」。

**旁支发现（2026-08-08 起持续复发，2026-08-19 第五次撞上）**：`m7_line_limit`
按 300 行硬拦本文件（未排除 `.md`），与项目级 D16「散文不算」不一致，`review/`
是 reviewer 写区改不了，只能精简本文件绕过；修法见 `_line_cap`，请排期修。

## 补登 `POST /api/core/timer/backfill`（2026-08-19 新增，契约 v1.8）

实现在 `timer/backfill.py`（300 行内部子边界纪律逼出的拆分，同 planner 的 `planvalidate.py`
一个道理），`service.py::backfill()` 只是薄委托，注入 `start()` 已有的 `_resolve_task_chain()`
（归属链硬取，两者同一套判据）与 `_now`。`source="manual-backfill"`；`dedupeKey` **必须先
`astimezone(UTC)` 归一化再拼**（`+08:00` 与等价 `Z` 撞同一条）；`data={durationSeconds,startAt}`
与 `stop()` 同形 → **零投影改动**；不碰 `timer_state`。`duplicate:true` 时 `event` 回显原条，
靠新增的 `events/service.py::find_by_dedupe` 取回。7 条拒绝规则复用既有
`UnknownTaskError`(404)/`InvalidInputError`(400)。测试见 `tests/test_timer_backfill.py`（12 条）。

## 运行方式（2026-08-09）
除 nohup 本机跑外，本模块现有 `code/backend/Dockerfile`，由框架根 `deploy/docker-compose.yml`
以 bridge 网服务名 `nexus:8000` 编排（源码只读 bind mount，编辑即时生效）。见 deploy/README.md。
