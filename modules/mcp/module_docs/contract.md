# mcp · 对外接口契约

> 本模块实现 `contracts/mcp.tools.v1`（给 AI 代理用的工具，MCP Streamable HTTP；v1.2 起 10 个只读 + 1 个只写草稿的 propose_；v1.3 再加 1 个只写建议的 propose_activity_matches；v1.7 再加 1 个只读的 get_match_history，共 13 个；v1.9 再加 get_window_awaiting_target 与 suggest_window_target，共 15 个）。行为的唯一事实在
> 那份契约里，本文件只登记依赖、说明实现选择。改行为先改那份契约。

```yaml
provides:
  - id: mcp.tools.v1
    contract: ../../../contracts/mcp.tools.v1/contract.md
    summary: 17 个工具（12 个只读（v1.13 加 get_report_status）+ propose_detector_rules 只写草稿 + propose_activity_matches 只写待确认的建议 + v1.13 propose_report 只写待人批准的报告 + v1.9 认窗口的 get_window_awaiting_target / suggest_window_target），挂在 <站点前缀>api/mcp/（经网关、过门）；对内 http://mcp:8020/api/mcp/
consumes:
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
  （`initialize`、`ping`、`tools/list`、`tools/call`）。`code/server/tools.py`：15 个工具、入参校验、cursor、路径。
- **纯标准库，没有用官方 MCP Python SDK。** SDK 能做无状态 Streamable HTTP，但要带进 starlette / pydantic /
  anyio / httpx 一串依赖，Origin 与租户这两道 HTTP 层的门还得另写中间件（SDK 自带的 DNS 重绑定防护比的是 `Host`，
  契约明确不拿 `Host` 比）；用到的协议面只有四个方法，手写更小、每一步都看得见。
- 上限：请求体 256 KiB、批量 16 条、同时处理 8 个请求（再多等 10 秒后 503）、nexus-core 单次响应 8 MiB。
- 无状态、不缓存：每次工具调用现取 `views/tree` 算路径，不存在跨租户缓存。
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

## 测试

`cd modules/mcp/code/server && python -m pytest -q tests`（CI「mcp 测试」）：真起服务 + 假 nexus-core，
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

v1.14（2026-10-09，nexus-core v2.22「忽略并记住」）：`get_detector_rules` 多读一个 `GET /api/core/activity/ignores`，输出追加 `ignored`（`{id, app, titleContains, since, ignoredRecords, ignoredSeconds}`，`app` / `titleContains` 过 `_screen`；老后端 404 = `[]`）。工具数、写端点不变。`get_current_timer` 的 `agents[]`、`get_agent_time` 的 `open[]` 不列藏起来的代理是 nexus-core 的 views 做的，MCP 原样带出。 两处各追加 `hiddenCount`（数字，藏起来的在跑代理个数，不带名字；老后端没有 = 0），让模型知道列表不全。
