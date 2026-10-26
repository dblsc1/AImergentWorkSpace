# mcp · 对外接口契约

> 本模块实现 `contracts/mcp.tools.v2`（v0.5：15 个工具，读 9 个、写 6 个；v2.1 / v0.4.2 追加读工具 `Get_Today_Todo`、`Get_Tasks_Match_History`（旧名 `Get_Today`、`Get_Match_History` 仍可调，不在 `tools/list`），分别读 nexus-core v2.40 的 `activity/gaps`、`activity/blocks/history`；v2.2 / v0.4.3 追加读工具 `Get_Blocks_Match_History`，读 v2.41 的 `activity/windows/history`，共 18 个，读 12 个；`tools/list` 只列这一套）；传输、租户、`Origin`、协议与屏幕文字不可信沿用
> `contracts/mcp.tools.v1`。v1 的 23 个工具名仍可调，只回迁移说明、不调下游。以下括号里是 v1 的历史（给追溯用）：
> `contracts/mcp.tools.v1`（给 AI 代理用的工具，MCP Streamable HTTP；v1.2 起 10 个只读 + 1 个只写草稿的 propose_；v1.3 再加 1 个只写建议的 propose_activity_matches；v1.7 再加 1 个只读的 get_match_history，共 13 个；v1.9 再加 get_window_awaiting_target 与 suggest_window_target，共 15 个；v1.17 再加 apply_detector_rules，共 19 个；v1.18 再加 propose_project / propose_task，共 21 个；v1.24 再加 propose_change / propose_ignore_rule，共 23 个；没有范围头的调用方 22 个）。行为的唯一事实在
> 那份契约里，本文件只登记依赖、说明实现选择。改行为先改那份契约。

```yaml
provides:
  - id: mcp.tools.v2
    contract: ../../../contracts/mcp.tools.v2/contract.md
    summary: v0.5 工具集：读 9 个、写 6 个，help / note / permissions，address，写工具默认提议、direct_apply 三条件；v1 名字只回迁移说明
  - id: mcp.tools.v1
    contract: ../../../contracts/mcp.tools.v1/contract.md
    summary: 23 个工具（没有范围头的调用方 22 个；v1.24 加 propose_change / propose_ignore_rule：提议改名 / 搬移 / 删除、提议「以后都忽略」，人批准才生效；v1.18 加 propose_project / propose_task：提议新项目 / 项目下的新任务，人在「待批准」页批准才建，list_projects 输出追加 zones / proposals；v1.17 加 apply_detector_rules：明确的 write 范围、账号开关 aiDirectRules 开着才直接写生效的规则；13 个只读（v1.13 加 get_report_status；v1.16 加 get_detector_health：检测程序状态 / 最近事故 / 固定排查提示，开关开着时带不可信的启动日志）+ propose_detector_rules 只写草稿 + propose_activity_matches 只写待确认的建议 + v1.13 propose_report 只写待人批准的报告 + v1.9 认窗口的 get_window_awaiting_target / suggest_window_target），挂在 <站点前缀>api/mcp/（经网关、过门）；对内 http://mcp:8020/api/mcp/
consumes:
  - id: nexus-core.views.history.v1
    contract: ../../nexus-core/module_docs/contract.md
    purpose: v2 Get_History
  - id: nexus-core.views.arrangements.v1
    contract: ../../nexus-core/module_docs/contract.md
    purpose: v2 Get_Arrangements
  - id: nexus-core.activity.tiers.v1
    contract: ../../nexus-core/module_docs/contract.md
    purpose: v2 Get_Pending_Classify_Windows、Get_Rules、三个窗口分类写工具、Get_Proposals 的直接施加日志、activity/settings 只读
  - id: nexus-core.activity.secretary.v1
    contract: ../../nexus-core/module_docs/contract.md
    purpose: v2 Submit_Report、Submit_Questions_To_Human、CRUD_Honeycomb 的 retime；Get_Proposals 读报告与问题
  - id: nexus-core.activity.proposals.v1
    contract: ../../nexus-core/module_docs/contract.md
    purpose: v1.24 propose_change（POST activity/proposals/changes）、propose_ignore_rule（POST …/ignores）；v1.18 propose_project（POST activity/proposals/projects）、propose_task（POST …/tasks）；list_projects 读 GET activity/proposals（老后端 404 = 空）；批准 / 不要 MCP 永远不调
  - id: nexus-core.views.tree.v1
    contract: ../../nexus-core/module_docs/contract.md
    purpose: get_task_tree、list_projects；所有工具的显示路径
  - id: nexus-core.views.current.v1
    contract: ../../nexus-core/module_docs/contract.md
    purpose: get_current_timer
  - id: nexus-core.events.read.v1
    contract: ../../nexus-core/module_docs/contract.md
    purpose: list_time_sessions（只读 type=session.completed）
  - id: nexus-core.views.gantt.v1
    contract: ../../nexus-core/module_docs/contract.md
    purpose: get_daily_time
  - id: nexus-core.activity.pending-days.v1
    contract: ../../nexus-core/module_docs/contract.md
    purpose: v1.15 get_daily_time 追加 pending（GET activity/suggestions/pending-days，只读，只有数字）
  - id: nexus-core.views.review.v1
    contract: ../../nexus-core/module_docs/contract.md
    purpose: get_weekly_review
  - id: nexus-core.views.next-actions.v1
    contract: ../../nexus-core/module_docs/contract.md
    purpose: get_next_actions
  - id: detector.rules.v1
    contract: ../../../contracts/detector.rules.v1/contract.md
    purpose: get_detector_rules（GET rules / drafts/current）；propose_detector_rules（POST drafts，唯一的写）
  - id: nexus-core.views.agent-time.v1
    contract: ../../nexus-core/module_docs/contract.md
    purpose: get_agent_time
  - id: nexus-core.activity.suggestions.v1
    contract: ../../nexus-core/module_docs/contract.md
    purpose: list_activity_suggestions（只调 GET）；propose_activity_matches（POST matches；不调 confirm / dismiss / unmatch）；get_match_history（GET history，v1.7）
  - id: nexus-core.activity.ignores.v1
    contract: ../../nexus-core/module_docs/contract.md
    purpose: v1.14 get_detector_rules 追加 ignored（GET activity/ignores，只读）
  - id: nexus-core.detector.incidents.v1
    contract: ../../nexus-core/module_docs/contract.md
    purpose: v1.16 get_detector_health（GET detector/devices、GET detector/incidents?deviceId=&limit=，只读；只看调用方自己的设备）
  - id: nexus-core.activity.reports.v1
    contract: ../../nexus-core/module_docs/contract.md
    purpose: v1.13 propose_report（POST activity/reports）；get_report_status（GET activity/reports?status=all&limit=1&items=true）；不调 approve / reject（只有人能）
  - id: nexus-core.tenancy.v1
    contract: ../../nexus-core/module_docs/contract.md
    purpose: 租户头格式与严格模式
```

对外入口由网关提供（`contracts/gateway.v1` 第八节）；这里不把 `gateway.v1` 写进 consumes——它的提供方
`nginx-docker` 不是可单独安装的模块，写进来安装器会想把它当服务装。

## 实现

- `code/server/mcp_server.py`：HTTP 层（Origin → 租户 → 协议版本头 → 256 KiB 上限（v1.2 前 64 KiB））与 JSON-RPC
  （`initialize`、`ping`、`tools/list`、`tools/call`）。v2 起的拆分：`tools.py`（下游请求、入参检查、address 解析、`permissions`、
  15 个工具的实现与分发）、`specs.py`（工具声明：名字、说明、inputSchema、help 示例，以及 `Get_Started` 的说明文档）、
  `v1_migration.py`（23 个 v1 名字的迁移说明）。
- **纯标准库，没有用官方 MCP Python SDK。** SDK 能做无状态 Streamable HTTP，但要带进 starlette / pydantic /
  anyio / httpx 一串依赖，Origin 与租户这两道 HTTP 层的门还得另写中间件（SDK 自带的 DNS 重绑定防护比的是 `Host`，
  契约明确不拿 `Host` 比）；用到的协议面只有四个方法，手写更小、每一步都看得见。
- 上限：请求体 256 KiB、批量 16 条、同时处理 8 个请求（再多等 10 秒后 503）、nexus-core 单次响应 8 MiB。
- 无状态；唯一的缓存是 `activity/settings`（给 `permissions` 用，按租户分开、至多 30 秒、写工具收到 `403` 就清）。其余每次工具调用现取，不存在跨租户缓存。
- v2（`mcp.tools.v2`）：
  - 写端点（`POST`）只在调用方范围是 `write` 时带 `X-Nexus-Scope: write`，读端点从不带；`direct_apply` 的判定全在 nexus-core，MCP 只把 `403 reason` 翻成一句话。
  - 所有 id 入参也收 address：`tools.Tree.resolve` 按当前 `views/tree` 解析（id 优先，再按 address 整串不分大小写匹配），对不上或有歧义 → `400` 列至多 5 个候选。
  - 批量写工具（`ops` / `items` / `questions`）逐条处理、逐条给结果；`Submit_Questions_To_Human` 一条一个 `POST`，所以可以部分成功。
  - 「今天」和缺省的 `date`：用 `NEXUS_TZ`（镜像里有时区库时）或进程本地时区算；nexus-core 才是归日的权威，所以缺省日期尽量不传、交给下游。
  - 契约没写死、实现里定下的几处：`Get_Pending_Classify_Windows` 的 `titles` 取下游给的 `titles`，没有就取自动块名「程序 · 标题」里的标题；
    `CRUD_Windowsblock` 的 `split` 要求各段首尾相接，发给下游时把首段开头、末段结尾放开成 `null`（覆盖全天）；
    重名提醒里建议的新名字是「原名 + 数字」（从 2 起第一个同级没占用的）。
- 调用方范围（`mcp.tools.v1` v1.12）：`mcp_server.caller_scope` 读网关转来的 `X-Nexus-Scope` / `X-Nexus-Anonymous`——
  `report` / 匿名 / 不认识的取值整个端点 403；`read` 时 `tools/list` 不列 `tools.WRITES`、`tools.call` 拒绝它们。
- 日志只记请求行、状态码、工具名与结果状态，不记请求头（`Authorization`/`Cookie` 本来也被网关清掉了）、不记工具结果。

## 配置

| 变量 | 缺省 | 作用 |
|---|---|---|
| `MCP_BIND` | `0.0.0.0:8020` | 监听地址（只在 compose 内部网里，不映射宿主端口） |
| `NEXUS_CORE_URL` | `http://nexus-core:8000` | nexus-core 地址 |
| `NEXUS_TENANT_STRICT` | `0` | 与 nexus-core 同一个开关（compose 传同一个值）；`1` = 缺租户头 401。看不懂的取值拒绝启动 |
| `MCP_ALLOWED_ORIGINS` | 空 | 逗号分隔；带 `Origin` 的请求须与其中一项完全相等，否则 403 |
| `NEXUS_TZ` | 空 | 可选。给了就用它算「今天」（缺省日期、`Get_Started` 的 `today`）；没给或镜像里没有时区库 = 进程本地时区 |

## 测试

`cd modules/mcp/code/server && python -m pytest -q tests`（CI「mcp 测试」）。v2：`tests/test_mcp_v2.py`（假 nexus-core 与客户端在 `tests/fake_nexus.py`）覆盖
契约 `mcp.tools.v2` 第六节全部：三种范围的 `tools/list`（写 18 / 读 12；没有范围头 20 = 18 + 自动跟踪的两个 v1 工具）、15 个工具的 `help`（不取数据）与 happy path、每次返回的 `note`（≤ 100 字）与 `permissions`
（含缓存与 `403` 清缓存）、参数错误带 `example` 与固定 `hint`、`422` → `400`、address 解析 / 歧义 / 不存在、`X-Nexus-Scope` 只给写端点且只在 `write` 范围、
`direct_apply` 的几种 `403`、批量写部分成功、`create` 不收 `direct_apply`、`Submit_*` 没有 `direct_apply`、`tiersOff`、23 个 v1 名字逐个迁移且不调下游、
两个租户互不可见、只调登记的端点。传输层（协议、`Origin`、租户、体积、批量、范围头）在 `tests/test_mcp.py`。以下是 v1 时代的覆盖清单（历史）：真起服务 + 假 nexus-core，
覆盖每个工具的形状、分页与 cursor 绑定、时间偏移 400、日期区间、租户 401/400/单人/两租户隔离、Origin、413、
405、只调白名单 GET、下游 4xx 透传 / 5xx 与连不上 502、建议不出 `deviceId`、`tools/list` 除 `propose_` 外全部只读、
`propose_detector_rules` 只发一个 POST 草稿且不带 `Authorization`、逐条错误原样带回、
`propose_activity_matches` 只发一个 POST matches（`suggestionId` → `id`；v1.4 的 `newTask` 原样下传，MCP 不建任务）、
`list_activity_suggestions` 带出 `newTask`（含 `projectPath`）；v1.6 的 `collection` / `projectId` 原样下传（只贴标签的条目不用 `taskId` / `confidence`），
`list_activity_suggestions` 带出 `collection`、`suggestedProjectId`、`suggestedProjectPath`。
经网关的整条链（设备令牌、两个账号互不可见、cookie、令牌开不了 `/api/agent/`）在 `deploy/test/mcp.sh`（CI「多账号」）。

v1.5（2026-10-08）：`_Paths` 从 `views/tree` 的 `project.unclassifiedTaskId` 认出项目的「未分类」时间桶（nexus-core v2.9），路径「分区 / 项目 / 未分类」；`list_time_sessions` / `get_daily_time` 的条目加 `unclassified`。没有新的下游请求。

v1.7（2026-10-08）：`get_match_history` 包 `GET /api/core/activity/suggestions/history`（nexus-core v2.12）——只这一个下游请求，
不另读 `views/tree`（路径用下游回的名字拼）；白名单取字段、标题截到 80 个字。测试：形状与裁剪、`limit` 原样下传、租户下传、
坏入参 400、算进「只调白名单 GET」与 `tools/list` 的 13 个。

v1.8（2026-10-08）：分类规则可以只到项目（`detector.rules.v1` v1.1，nexus-core v2.14）。`get_detector_rules` 的每条规则多一个
`projectId`（到任务的为 `null`），只到项目的 `path` 是「分区 / 项目」；`propose_detector_rules` 的入参 schema 不再要求 `taskId`
必填、多一个 `projectId`（逐条校验仍在 nexus-core，原样下传）。工具仍是 13 个，下游请求不变。测试：读出的形状、schema、原样下传。

v1.9（2026-10-08）：让 AI 认规则认不出的窗口（nexus-core v2.15）。`get_window_awaiting_target` 包 `POST /api/core/activity/ai/claim`
（空对象；白名单取 `key / app / title / claimedAt / answerBy`），`suggest_window_target` 包 `POST /api/core/activity/ai/suggest`
（只下传 `key / taskId / projectId / confidence / reason / none` 六个键；inputSchema 没有能指定窗口的键）。两个都不带
`Authorization`、带租户头；注解都不是只读，前者 `idempotentHint: true`。`_types` 多认一种 `number`。工具共 15 个。
测试：下游请求与请求体一字不差、租户下传、没有窗口为 `null`、409 原样带回（「什么都没写」）、坏入参 400 且不调下游、
`tools/list` 的 15 个与注解。经网关的整条链在 `deploy/test/mcp.sh`（没有窗口 → `null`、没在等的 key → 409 且规则版本不变、
令牌直连三个端点 403）。

v1.10（2026-10-09）：此刻的焦点走 MCP（nexus-core v2.16）。`get_current_timer` 的输出追加 `focus` / `auto` / `needsChoice`，
都取自它本来就读的 `GET /api/core/views/current`——**不多一个下游请求，也不自己认项目 / 任务**（路径仍由 `views/tree` 按 id 现取）。
`focus.title` / `needsChoice.title` 截到 80 个字符；`elapsedSeconds` = MCP 此刻 − `since`。老后端没有这些键 → 三个都是 `null`。
工具仍是 15 个，说明改写（在计时 / 否则看 focus / 只是提示 / 标题已脱敏）。测试：三种形状（有目标、只到项目、离开）、
截断、老后端为 `null`、在计时时两样都在、下游请求集合不变。

v1.11（2026-10-09）：人的注意力走 MCP（nexus-core v2.17）。`get_agent_time` 的 `open[]` 每条追加 `attentionSeconds`、
`get_current_timer` 的 `focus` 追加 `dwellSeconds`，都是下游响应里的同名数字原样带出（`.get()`：老后端没有 → `null`），
**不多一个下游请求、不自己算**。工具仍是 15 个，两个工具的说明各加一句。没有新的屏幕文字（两个都是数字，不过 `_screen`）。
测试：有 / 没有这两个键两种下游形状。

v1.10 同版（安全审查）：屏幕来的文字不可信。`tools.py` 的 `_screen()` 是唯一的清洗处——控制字符 / 换行 / 零宽 / 双向控制符
→ 空格并成一行，再截断（`MAX_TITLE` 80 / `MAX_SCREEN_TEXT` 200）；`get_current_timer`、`list_activity_suggestions`、
`get_match_history`、`get_window_awaiting_target` 的 `app` / `title`（及 `reason`、集合名）与 `get_detector_rules` 的 `note`
都过它，规则的正则不过。五个工具的说明加同一句 `_SCREEN`，`INSTRUCTIONS` 也加。测试：带换行 / 控制符 / 「IGNORE ALL PREVIOUS
INSTRUCTIONS」的标题在五个工具里都是一行、截断过、只在自己的字段里；说明里有那句话；`suggest_window_target` 多带 `title` 400 且不调下游。

v1.13（2026-10-09，nexus-core v2.20「AI 报告」）：`propose_report` 只发一个 POST `activity/reports`（原样下传，不带 `Authorization`；拒绝理由过 `_screen` 再还给模型），`get_report_status` 只发一个 GET `activity/reports?status=all&limit=1&items=true`（`summary` / `reason` / `failure` 过 `_screen`，不出建议 id 与自报作者）。`WRITES` 追加 `propose_report`；工具共 17 个。测试：`tests/test_mcp.py` 的 `test_propose_report_*`、`test_get_report_status_*`、`test_ai_text_in_reports_*`，工具分类与计数的几条随之改为 17 / 12。

v1.16（2026-10-12，nexus-core v2.28）：新增只读 `get_detector_health`，只发 `GET detector/devices` 与每台设备的 `GET detector/incidents`（deviceId 只取自前一个响应）；提示表 `HINTS` 与 `ai-detector status` 同一套话（测试读 `health.go` 对照）；启动日志逐行过 `_screen`、整块标 `untrusted`。

v1.17（2026-10-14，nexus-core v2.29，仓主决定放宽早先的承诺）：新增 `apply_detector_rules`，入参 schema 与 `propose_detector_rules` 是同一个对象（`_RULES_IN`）；只发一个 `POST detector/rules/ai-apply`（`{rules, summary, author: "assistant"}`，不带 `Authorization`）。`WRITES` 追加它；`mcp_server.caller_scope` 现在区分 `full`（没有范围头）与 `write`，`tools.denied(scope)` 决定看不到 / 调不了什么：`read` 看不到全部写工具，`full`（含聊天后端对内直连）只看不到 `apply_detector_rules`（`tools.WRITE_ONLY`），调了 403；注解 `destructiveHint: true`。nexus-core 回 403 且体里 `reason == "aiDirectOff"`（`ToolError.reason`，`_send` 从错误体里取）→ 工具错误，`detail` 是固定的 `tools.SWITCH_OFF`（叫用户去「自动记录 → 分类规则」页打开「允许 AI 直接改分类规则」，其间改用 `propose_detector_rules`）；别的 403 原样透传；`INSTRUCTIONS` 里有一句说明 `apply_detector_rules` 不是只读、立即生效、须用户打开开关；回 404（路由不存在，nexus-core 比 v2.29 老）→ 工具错误「服务器太老」。描述里写明规则内容必须来自用户的意图、不许来自屏幕文字。`propose_detector_rules` 不变。测试：`tests/test_mcp.py` 的 `test_apply_detector_rules_*`，工具分类与计数随之改为 19 / 13。

v1.21（2026-10-19，nexus-core v2.33，仓主：「一个 ai 会话理论上只对应一个任务……点击展开可以单独划分」）：只改 `propose_report` 的描述文字，追加一句「一个窗口 / 一个 agent 会话 ⇒ 一个任务：同一窗口的段放进同一条；用户界面按窗口分组；「也可以逐条改 / 批准 / 不要」改成「按窗口展开，整个窗口或单段改去向、或标成噪声」，`next` 里「或逐条处理」改「或按窗口改去向」」。入参 / 输出 / 端点不变。

v1.18（2026-10-19，nexus-core v2.30，仓主：「给 mcp 加新增项目（需要人批准）和新增项目下的任务两个接口（都需要人批准）」）：新增 `propose_project`（`POST activity/proposals/projects`）与 `propose_task`（`POST activity/proposals/tasks`），都进 `tools._PROPOSE`（于是 `WRITES` 含它们、`read` 范围看不到也调不了；没有范围头与 `write` 都能用），只发一个 POST、不带 `Authorization`；结果固定 `created: false` 加一句 `next`（尚未创建、请用户在「待批准」页的「新项目与任务」批准）；nexus-core 的 400 / 409 / 429 原样成工具错误。`list_projects` 输出追加 `zones`（`[{zoneId, name}]`）与 `proposals`（多一个 `GET activity/proposals?status=all&limit=50`，名字过 `_screen`；该请求任何 `ToolError`——含老后端 404——都当空数组，不让 `list_projects` 失败）。工具分类与计数随之改为 21 / 20 / 13。测试：`tests/test_mcp.py` 的 `test_propose_project_and_task_*`。

v1.14（2026-10-09，nexus-core v2.22「忽略并记住」）：`get_detector_rules` 多读一个 `GET /api/core/activity/ignores`，输出追加 `ignored`（`{id, app, hasTitleFilter, since, ignoredRecords, ignoredSeconds}`，`app` 过 `_screen`；**不出 `titleContains`**——那段匹配文字可能就是被忽略窗口标题的一部分，只给登录的人；老后端 404 = `[]`）。工具数、写端点不变。`get_current_timer` 的 `agents[]`、`get_agent_time` 的 `open[]` 不列藏起来的代理是 nexus-core 的 views 做的，MCP 原样带出。 两处各追加 `hiddenCount`（数字，藏起来的在跑代理个数，不带名字；老后端没有 = 0），让模型知道列表不全。

v1.22（2026-10-19，nexus-core v2.34）：`list_activity_suggestions` 追加可选 `needsAI`（true 才下传 `?needsAI=true`；绑进游标）与每项 `needsAI`；`get_detector_rules` 的规则只在 `mode` 是 `ai` 时多一个 `mode` 键；`_RULES_IN` 的规则追加 `mode`；`suggest_window_target` 回包带 `needsAI` 时 `next` 改说「没写规则」。工具数不变。

v1.23（2026-10-22，nexus-core v2.35）：`list_activity_suggestions` 的 `status=confirmed` 行追加 `recordedTo`（`{projectId, projectName, taskId, taskName, via, eventId}`，当前去向、含事后改挂；`taskId` 为 null = 只记在项目的「未分类」；找不到为 null；名字过 `_screen`），pending / dismissed 行没有这个键；工具描述写明「已确认的时间没有丢」。工具数不变，不新增调用路径。

v1.24（2026-10-22，nexus-core v2.36，仓主批准）：新增 `propose_change` 与 `propose_ignore_rule`（各发一个 POST、都进 `tools._PROPOSE`）；`reason` 必填且非空白，缺则 `400` 不发请求。`list_projects.proposals` 追加 `kind: change` / `ignore` 条目，`titleContains` 不外传，只给 `hasTitleFilter`。工具分类与计数改为 23 / 22 / 13。测试：`tests/test_mcp.py` 的 `test_propose_change_*`。
