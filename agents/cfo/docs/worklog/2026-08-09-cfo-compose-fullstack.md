# 2026-08-09 · 四服务栈 docker-compose 全栈化（对外 10000）

## 目标
把「手动 nohup」的四服务栈（mongo/nexus-core/auth/nginx）收敛成一套 `docker compose`
全栈：一条命令起停、开机自启、对外单端口 10000。构建验证期间不动正在运行的旧栈。

## 交付（根仓 deploy/）
- `deploy/docker-compose.yml`：四服务 + bridge 网 `cockpit-net`，服务名互通。
  - 显式 `name: cockpit`（不用目录名 deploy，避免卷被前缀成 deploy_xxx 与本机 ecs 栈的
    `deploy_mongo_data` 撞车）。
  - mongo 卷 `external: true` 复用 named volume，`down -v` 也删不掉（生产数据不会被误删）。
  - nginx 上游改指服务名 `http://nexus:8000` / `http://auth:8010`；bridge 模式经 env 驱动
    nginx-docker 模块的 `cockpit.conf.template`（NGINX_BIND=0.0.0.0 / PORT=80 /
    RESOLVER=127.0.0.11 / LISTEN_LOOPBACK 置空），**nginx 业务配置零改动**。
  - 唯一对外端口：`ports: 192.168.31.181:10000:80`。
  - 四服务 `restart: unless-stopped`；depends_on 走 service_healthy 链（nginx 起时服务名可解析）。
- `deploy/.env.example`（占位入仓）+ `deploy/.env`（真值 gitignore，密钥经 env_file 注入 auth）。
- `deploy/README.md`：起停/日志/开机自启（含 systemd unit + 一次性 sudo 清单）/旧栈切换步骤。
- Dockerfile 在各模块仓：`programmer@nexus-core+compose-dockerfile`（1907e99）、
  `programmer@auth+compose-dockerfile`（91c156e）。
- 根 `.gitignore` 白名单登记 `!/deploy/`（白名单会静默吞未登记的新根级目录）。

## 验证（对着 10000，全程不碰旧栈 8081 / cockpit-mongo）
用独立测试卷 `cockpit-validate-mongo-data` + 测试库 `nexus_core_compose_test`，绝不碰生产数据：
- 四容器全 healthy；旧栈 cockpit-nginx-local(8081) / cockpit-mongo 全程存活未动。
- 10000 全链路：`/healthz` 200；未登录 `/table/` 302→`/login/`；`POST /api/auth/login`
  204+Set-Cookie；带 cookie `/table/ /ring/ /gantt/ /api/core/export` 全 200；
  `/api/core/health` 返回 `db=nexus_core_compose_test`（证明打的是测试库）。
- 自恢复：`docker restart cockpit-c-nexus` ~6s 回 healthy，export 复 200。
- 数据保留：测试库插标记 → `compose down`（无 -v）→ `up` → 标记仍在（卷复用证明）；
  生产卷 cockpit-mongo-data 全程未动。

## 已知项 / 交 CFO 与用户决策
1. **本沙箱 dockerd 不执行 restart 策略的崩溃/开机自动拉起**（`docker kill` 后停 exited、
   RestartCount=0）。`restart: unless-stopped` 配置正确，真实主机的 dockerd 会照常自动拉起；
   要在任意 dockerd 上拿强保证，用 README 里的 systemd unit（需 sudo）。
2. **切旧栈→新栈需人工 + sudo**：停旧 mongo 才能让新 mongo 接管生产卷（两个 mongod 不能同挂
   一个 dbPath）。步骤见 README「切换」节。
3. `systemctl enable docker` 本机已 enabled；systemd unit 的 enable 需 sudo，列在 README。
