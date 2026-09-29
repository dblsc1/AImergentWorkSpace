# mcp · 对外接口契约

> 本模块实现 `contracts/mcp.tools.v1`（给 AI 代理用的只读工具，MCP Streamable HTTP）。行为的唯一事实在
> 那份契约里，本文件只登记依赖、说明实现选择。改行为先改那份契约。

```yaml
provides:
  - id: mcp.tools.v1
    contract: ../../../contracts/mcp.tools.v1/contract.md
    summary: 9 个只读工具，挂在 <站点前缀>api/mcp/（经网关、过门）；对内 http://mcp:8020/api/mcp/
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
  - id: nexus-core.views.agent-time.v1
    contract: ../../nexus-core/module_docs/contract.md
    purpose: get_agent_time
  - id: nexus-core.activity.suggestions.v1
    contract: ../../nexus-core/module_docs/contract.md
    purpose: list_activity_suggestions（只调 GET）
  - id: nexus-core.tenancy.v1
    contract: ../../nexus-core/module_docs/contract.md
    purpose: 租户头格式与严格模式
```

对外入口由网关提供（`contracts/gateway.v1` 第八节）；这里不把 `gateway.v1` 写进 consumes——它的提供方
`nginx-docker` 不是可单独安装的模块，写进来安装器会想把它当服务装。

## 实现

- `code/server/mcp_server.py`：HTTP 层（Origin → 租户 → 协议版本头 → 64 KiB 上限）与 JSON-RPC
  （`initialize`、`ping`、`tools/list`、`tools/call`）。`code/server/tools.py`：9 个工具、入参校验、cursor、路径。
- **纯标准库，没有用官方 MCP Python SDK。** SDK 能做无状态 Streamable HTTP，但要带进 starlette / pydantic /
  anyio / httpx 一串依赖，Origin 与租户这两道 HTTP 层的门还得另写中间件（SDK 自带的 DNS 重绑定防护比的是 `Host`，
  契约明确不拿 `Host` 比）；用到的协议面只有四个方法，手写更小、每一步都看得见。
- 上限：请求体 64 KiB、批量 16 条、同时处理 8 个请求（再多等 10 秒后 503）、nexus-core 单次响应 8 MiB。
- 无状态、不缓存：每次工具调用现取 `views/tree` 算路径，不存在跨租户缓存。
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
405、只调白名单 GET、下游 4xx 透传 / 5xx 与连不上 502、建议不出 `deviceId`、`tools/list` 全部只读。
经网关的整条链（设备令牌、两个账号互不可见、cookie、令牌开不了 `/api/agent/`）在 `deploy/test/mcp.sh`（CI「多账号」）。
