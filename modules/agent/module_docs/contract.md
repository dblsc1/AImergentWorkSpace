# agent · 对外接口契约

> 本模块实现 `contracts/agent.chat.v1`（聊天后端：会话、SSE 流式回答、取消）的缺省实现——opencode 适配器。
> 行为的唯一事实在那份契约里，本文件只登记依赖、说明实现选择。改行为先改那份契约。

```yaml
provides:
  - id: agent.chat.v1
    contract: ../../../contracts/agent.chat.v1/contract.md
    summary: <站点前缀>api/agent/ 下的会话 CRUD、发消息（SSE）、取消；对内 http://agent:8030/api/agent/
consumes:
  - id: mcp.tools.v1
    contract: ../../../contracts/mcp.tools.v1/contract.md
    purpose: 代理读用户数据的唯一通路。对内地址 http://mcp:8020/api/mcp/，带会话所属租户的 X-Nexus-Tenant
```

对外入口由网关提供（`contracts/gateway.v1` 第八节）；同 `mcp`，不把 `gateway.v1` 写进 consumes。

## 实现

- `code/backend/app/main.py`：端点、请求体校验（415/413/422）、租户、限流（409/429）、SSE（`start`/`delta`/`tool`/
  `done`/`error`、15 秒 `: ping`、断连 = 取消）。
- `code/backend/app/runtime.py`：一个租户一个 `opencode serve`（127.0.0.1 随机端口、随机口令、从零拼的环境变量、
  HOME/XDG_* 指到租户目录），空闲收回、总数上限；opencode 事件 → 契约事件的翻译（`Translator`）。
- `code/backend/app/config.py`：读 env；拼 `OPENCODE_CONFIG_CONTENT`（`share: disabled`、`autoupdate: false`、
  `permission: {"*": "deny", "honeycomb_*": "allow"}`、自定义智能体 `honeycomb` 与系统提示、关掉自带智能体）。
- `code/backend/app/store.py`：会话与消息存成租户目录下的 JSON 文件。列会话、读历史不用拉起 opencode；
  opencode 自己的会话库只当模型上下文。
- 普通 RPC 30 秒超时（只有事件流不限读）；一轮总时限到了、取消或断连后，中止并经 `/session/status` 确认停了才放开 busy，确认不了就重启这个租户的运行时。
- opencode 钉死在 `Dockerfile` 的 `OPENCODE_VERSION`（npm 包 `opencode-ai`）。镜像里的进程不是 root。
- opencode 自己的日志文件接到 `/dev/null`：它会原样记上游报错（可能带密钥片段）。本服务的日志只记错误类别、
  状态码、关联 id。

数据卷 `honeycomb_agent_data`（`/data`）：`tenants/<sha256(租户) 前 32 位>/` 下是 `sessions/<id>.json`（会话元数据，列会话只读它）与 `sessions/<id>.jsonl`（消息，只追加，最多留最近 500 条）、
`oc/`（opencode 的 HOME 与会话库）、`work/`（opencode 的工作目录，永远是空的）。

## 配置

见 `contracts/agent.chat.v1` 第七节（`AGENT_API_KEY`、`AGENT_MODEL`、`AGENT_BASE_URL`、`AGENT_MAX_SESSIONS`、
`AGENT_MAX_RUNTIMES`、`AGENT_MAX_TURN_SECONDS`）。另有只给测试与换组装用的：`AGENT_MCP_URL`（缺省 `http://mcp:8020/api/mcp/`）、
`AGENT_DATA_DIR`（`/data`）、`AGENT_IDLE_SECONDS`（900）、`AGENT_OPENCODE_BIN`（`opencode`）。

## 测试

- `cd modules/agent/code/backend && python -m pytest -q tests`：`test_api.py` 用脚本化的假运行时测 HTTP 行为；
  `test_integration.py` 起真 opencode + 假模型 + 假 MCP（`tests/fakes/`），找不到 opencode 就跳过。
- `deploy/test/agent.sh`（CI「agent」）：在镜像里跑全部测试（真 opencode 必须在），假模型用内网服务名
  `http://fake-llm:9100/v1`，再按 compose 的环境变量验跑起来的服务本身。
