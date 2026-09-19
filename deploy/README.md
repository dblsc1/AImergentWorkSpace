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

- `api`（nexus-core 模块）、`auth`（auth.gate.v1 占位实现）、`mongo`、`web`（nginx）
  四个服务，登录门已经接好：未登录访问 `/api/`（进而 `/hive/`、`/ring/` 上线后）
  一律跳 `/login/`。
- 前端模块的 location 在 nginx 配置里先留成注释。`ring` 已经进了 `modules/`，
  接线（取消注释 + `module.yaml`）还没做；`hive` 随后落地。
- `/login/` 的页面本体（`auth.login-page.v1`）也还没落地，路由先占住。

## 安全边界

只绑 `127.0.0.1` 是因为这套东西**没有 TLS**。要放到局域网或公网，
请自己在前面加一层 TLS（哪怕自签证书），别直接把 80/8800 暴露出去——
明文 HTTP 下口令和会话 cookie 都是裸奔的。
