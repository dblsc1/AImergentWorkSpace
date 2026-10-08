# mcp · 给 AI 代理用的工具（只读 + 只写草稿 + 认窗口）

HoneyComb 的 MCP 服务器（契约 `contracts/mcp.tools.v1`）：把任务树、人的时间、代理时间、在跑的计时、
待确认的活动建议、以前的归类历史、活动分类规则读给 AI 代理。15 个工具：11 个只读（v1.7 加 `get_match_history`：
你以前把哪个窗口定到了哪个项目 / 任务，助理归类前先看它）；`propose_detector_rules`（v1.2）
只写**待人应用的规则草稿**——生效要人在 Cockpit「AI助理 → 规则」点「应用」（v1.8：规则的目标可以只到项目，
`projectId` 代替 `taskId`）；`propose_activity_matches`（v1.3）
只给待确认的活动**配任务建议**——入账要人在「AI助理 → 待确认建议」逐条点「是」；v1.4 起也能**提议新任务**
（`newTask`），同样人点「是」才建，MCP 自己从不建任务。

v1.9 的两个是给「允许 AI 管理进行中的任务」用的：`get_window_awaiting_target` 给出此刻规则认不出、等 AI 认的那**一个**窗口
（没有就是 `null`），`suggest_window_target` 回答它——这是唯一直接生效的写：服务端给那一个窗口写一条只认它的规则，
计时页显示「自动 · …（AI 认的）」，你点「不对」就撤。它只在那个窗口正等着回答的两分钟里收、每小时最多 12 次，
指定不了别的窗口。自带的助手会自己定时来取（见 `modules/agent`），你接的外部代理也可以调。

v1.10：`get_current_timer` 除了「在不在计时」，还带出 `focus`——你此刻在哪个窗口、待了多久、它多半属于哪个项目 / 任务
（与顶栏、计时页、蜂巢上显示的是服务端算好的同一份；只是提示，什么都没记；窗口标题已按你的隐私设置处理过）。
任何 MCP 客户端都能用它回答「我现在在做什么」。

默认组装（`deploy/docker-compose.yml`）和发布版都自带它，经网关挂在 `<站点前缀>api/mcp/`，要登录：

- 浏览器会话 cookie，或
- 设备令牌（`contracts/auth.gate.v1`）：命令行 `docker compose exec auth python /app/auth_stub.py token <账号>`，
  或登录后 `POST /api/auth/tokens`；请求头 `Authorization: Bearer <令牌>`。

接自己的 MCP 客户端（Streamable HTTP）：

```jsonc
{ "url": "http://127.0.0.1:8800/api/mcp/",
  "headers": { "Authorization": "Bearer hct1...." } }
```

命令行试一下：

```sh
curl -s -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"get_current_timer","arguments":{}}}' \
  http://127.0.0.1:8800/api/mcp/
```

浏览器页面直接调要把页面的 Origin 加进 `MCP_ALLOWED_ORIGINS`（缺省空 = 浏览器一律 403）。
实现、配置与测试见 `module_docs/contract.md`。
