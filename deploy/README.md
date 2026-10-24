# honeycomb（组装层）

这个目录**不含任何业务代码**。它只是 compose + nginx，把 `../modules/`
里各自独立可用的模块和 `../contracts/` 里的契约占位实现拼成一个能跑的站点。
要改行为，去对应的模块或契约目录改；这里只负责"怎么把它们接在一起"。

## 起停

```bash
cp .env.example .env
# 编辑 .env，把 HONEYCOMB_PASSWORD 填成一个真口令 —— 必须先做这一步，
# 没有默认口令，auth 服务缺它会直接拒绝启动。
# （多个账号各用各的数据：见根目录 README「多个账号」，不用填这一项。）
docker compose up -d
```

打开 `http://127.0.0.1:8800/`（默认只绑回环，局域网/公网都探测不到）。

```bash
docker compose down       # 停，数据留着
docker compose down -v    # 停 + 删数据（mongo 与账号文件的 named volume 一起没了）
```

## 现状

- `nexus-core`、`auth`（auth.gate.v1 占位实现：共享口令 / 账号+密码，账号文件在
  named volume `honeycomb_auth_data`）、`mongo`、`web`（nginx）四个服务。
  `web` 另外只读挂载三个前端目录：`/hive/`（任务蜂巢，`/` 跳这里）、`/ring/`（计时台）与 `/assistant/`（待批准）；`/auto/`（自动记录：检测设置、分类规则）是 assistant 目录的第二个入口（`auto.html`），同一个目录挂两次。
- v0.3 起另有 `mcp`（只读 MCP 工具，`contracts/mcp.tools.v1`）与 `agent`（聊天后端，`contracts/agent.chat.v1`，
  「待批准」页的「AI 对话」；聊天记录在 named volume `honeycomb_agent_data`）。
  **v0.4 起 `agent` 是可选服务，缺省不起**（compose profile `agent`；主路径是用户自己的 AI CLI 接 MCP）：
  `.env` 设两行 `COMPOSE_PROFILES=agent` 和 `AGENT_UPSTREAM=agent:8030`（再填 `AGENT_API_KEY`）才构建、启动它并让网关转发。
  `AGENT_UPSTREAM` 缺省为空：网关不转发、不查 DNS（免得内网 DNS 恰好有个叫 `agent` 的主机收到请求），`/api/agent/*` 过门后回 502、页面不显示聊天入口，不要模型密钥。
  **源码安装升级**：以前起过 `agent` 的，容器不会自己消失（compose 不管不在 profile 里的服务）：`docker compose --profile agent rm -sf agent` 清掉；填过密钥又想继续用的，自己加上面两行（源码安装没有安装脚本替你补）。
  两者与网关同在内网 `honeycomb-agent-net`；`agent` 不在 `honeycomb-net` 上，读数据只能经 MCP。
  `test/agent.yml` + `test/agent.sh` 是 CI「agent 测试」：真 opencode、假模型、假 MCP，不要密钥（job 里设了 `COMPOSE_PROFILES=agent`）。
  `smoke.sh` 缺省按「聊天关」核对（`/api/agent/health` 回 502）；`HONEYCOMB_SMOKE_AGENT=1` 按「聊天开」核对。
- 登录门接在 `/api/`、`/hive/`、`/ring/`、`/assistant/`、`/auto/` 前面：未登录一律跳 `/login/`，
  登录页来自 `contracts/auth.gate.v1/stub/web/`。
- `web` 的配置是 envsubst 模板 `nginx/templates/default.conf.template`，共用
  `../modules/nginx-docker/` 里的门片段、顶栏注入片段与静态资源。顶栏由网关注入
  三个前端，页签按已装的前端生成（任务、计时、待批准、自动记录）。
- 换认证服务、换登录页、加自己的路由：用自己的 `.env` 与 override 文件，不改本目录，
  见 `../contracts/gateway.v1/contract.md`。`test/` 里是 CI 用来验这份契约的 override
  与一条示例路由；`test/accounts.sh` 是多账号的端到端验证（CI「多账号」）。
- `./install.sh add hive ring assistant` 从各模块的 `module.yaml` 生成行为相同的一份到
  `deploy/generated/`；CI 把手写与生成的两份都真起一遍、登录、逐个资源请求一遍。

## 安全边界

只绑 `127.0.0.1` 是因为这套东西**没有 TLS**。要放到局域网或公网，
请自己在前面加一层 TLS（哪怕自签证书），别直接把 80/8800 暴露出去——
明文 HTTP 下口令和会话 cookie 都是裸奔的。
