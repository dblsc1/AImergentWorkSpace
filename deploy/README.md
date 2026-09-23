# honeycomb（组装层）

这个目录**不含任何业务代码**。它只是 compose + nginx，把 `../modules/`
里各自独立可用的模块和 `../contracts/` 里的契约占位实现拼成一个能跑的站点。
要改行为，去对应的模块或契约目录改；这里只负责"怎么把它们接在一起"。

## 起停

```bash
cp .env.example .env
# 编辑 .env，把 HONEYCOMB_PASSWORD 填成一个真口令 —— 必须先做这一步，
# 没有默认口令，auth 服务缺它会直接拒绝启动。
docker compose up -d
```

打开 `http://127.0.0.1:8800/`（默认只绑回环，局域网/公网都探测不到）。

```bash
docker compose down       # 停，数据留着
docker compose down -v    # 停 + 删数据（mongo 的 named volume 一起没了）
```

## 现状

- `nexus-core`、`auth`（auth.gate.v1 占位实现）、`mongo`、`web`（nginx）四个服务。
  `web` 另外只读挂载两个前端目录：`/hive/`（任务蜂巢，`/` 跳这里）与 `/ring/`（计时台）。
- 登录门接在 `/api/`、`/hive/`、`/ring/` 前面：未登录一律跳 `/login/`，
  登录页来自 `contracts/auth.gate.v1/stub/web/`。
- `web` 的配置是 envsubst 模板 `nginx/templates/default.conf.template`，共用
  `../modules/nginx-docker/` 里的门片段、顶栏注入片段与静态资源。顶栏由网关注入
  两个前端，页签按已装的前端生成。
- 换认证服务、换登录页、加自己的路由：用自己的 `.env` 与 override 文件，不改本目录，
  见 `../contracts/gateway.v1/contract.md`。`test/` 里是 CI 用来验这份契约的 override
  与一条示例路由。
- `./install.sh add hive ring` 从各模块的 `module.yaml` 生成行为相同的一份到
  `deploy/generated/`；CI 把手写与生成的两份都真起一遍、登录、逐个资源请求一遍。

## 安全边界

只绑 `127.0.0.1` 是因为这套东西**没有 TLS**。要放到局域网或公网，
请自己在前面加一层 TLS（哪怕自签证书），别直接把 80/8800 暴露出去——
明文 HTTP 下口令和会话 cookie 都是裸奔的。
