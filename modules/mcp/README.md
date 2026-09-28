# mcp · 给 AI 代理用的只读工具

HoneyComb 的 MCP 服务器（契约 `contracts/mcp.tools.v1`）：把任务树、人的时间、代理时间、在跑的计时、
待确认的活动建议读给 AI 代理。**8 个工具全部只读**，什么都不写。

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
