# cockpit 全栈 · docker compose 部署

把原「手动 nohup」的四服务栈（mongo / nexus-core / auth / nginx）收敛成一套
`docker compose` 全栈：一条命令起停、开机自启、对外单端口 **10000**。

- compose 文件：`deploy/docker-compose.yml`
- env 事实源：`deploy/.env.example`（占位，入仓）→ 拷成 `deploy/.env`（真值，**不进仓**）
- 镜像：nexus / auth 各一个 `Dockerfile`（在各模块 `code/backend/`），nginx 直接用 `nginx:alpine`
- 内部走 bridge 网 `cockpit-net`，服务名互通（nginx 上游＝`http://nexus:8000` / `http://auth:8010`）
- **唯一对外端口**：nginx 映射 `192.168.31.181:10000 -> 容器 80`

## 一次性准备

```bash
cp deploy/.env.example deploy/.env      # 填真值：AUTH_PASSWORD/AUTH_SECRET、库名、时区、宿主端口
```

`deploy/.env` 关键项：

| 变量 | 生产值 | 说明 |
|---|---|---|
| `MONGO_VOLUME_NAME` | `cockpit-mongo-data` | 复用现手动栈的 mongo 卷（**external**，`down -v` 也删不掉）|
| `NEXUS_DB_NAME` | `nexus_core` | 生产库；验证时务必换 `nexus_core_*_test` 之类，别写生产库 |
| `NEXUS_TZ` | `Asia/Shanghai` | 日界时区，缺失/错值会把工作静默记到错误的日子 |
| `AUTH_PASSWORD` / `AUTH_SECRET` | 与现手动栈 `code/auth/code/backend/.env` 一致 | 保持一致，切换后会话/口令连续 |
| `NGINX_HOST_BIND` | `192.168.31.181:10000` | 宿主监听 IP:端口，IP 由路由器固定 |

mongo 卷是 `external`，生产卷 `cockpit-mongo-data` 已存在无需建。
若用独立测试卷，先建：`docker volume create <卷名>`。

## 起 / 停 / 看日志

```bash
# 起（后台）
docker compose -f deploy/docker-compose.yml up -d

# 首次或改了依赖/Dockerfile 时先构建
docker compose -f deploy/docker-compose.yml build

# 停（保留数据卷；external 卷本就不会被删）
docker compose -f deploy/docker-compose.yml down

# 看某个服务日志
docker compose -f deploy/docker-compose.yml logs -f nginx     # 或 nexus / auth / mongo

# 看四容器健康
docker ps --filter name=cockpit-c- --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
```

改了 nexus/auth 的 **源码**（`app/` 下）无需 rebuild：源码是只读 bind mount，
`docker restart cockpit-c-nexus`（或 `cockpit-c-auth`）即生效。
改了 **依赖**（requirements.txt）或 Dockerfile 才需 `build`。

## 开机自启

两层保证：

1. **docker 守护进程开机自启**（已确认本机 `systemctl is-enabled docker` = `enabled`）。
   若换机器显示 `disabled`，一次性执行（需 sudo）：
   ```bash
   sudo systemctl enable docker
   ```
2. **各服务 `restart: unless-stopped`**：docker 守护进程启动时会把上次在运行、
   且不是被显式 `stop` 的容器重新拉起。二者配合 = 机器重启后整栈自动回来。

> ⚠️ 本次在**沙箱 docker 守护进程**上实测：`docker restart <服务>` 的**主动**恢复
> 正常（见下「验证」），但该守护进程**不处理 restart 策略的崩溃/开机自动拉起**
> （`docker kill` 后容器停在 exited、`RestartCount=0`）。`restart: unless-stopped`
> 配置本身正确，真实主机的 dockerd 会照常在崩溃/开机时自动拉起。若要在**任意**
> dockerd 上都拿到强保证，加下面的 systemd unit。

### （可选，更强保证）systemd unit

不依赖 dockerd 自身的 restart 策略，用 systemd 在开机时显式 `compose up`：

```ini
# /etc/systemd/system/cockpit-stack.service   —— 需 sudo 放置
[Unit]
Description=cockpit full stack (docker compose)
Requires=docker.service
After=docker.service network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/srv/aimergent/sample-workspace-v5-time-management-test
ExecStart=/usr/bin/docker compose -f deploy/docker-compose.yml up -d
ExecStop=/usr/bin/docker compose -f deploy/docker-compose.yml down
[Install]
WantedBy=multi-user.target
```

一次性启用（需 sudo）：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now cockpit-stack.service
```

## 与旧「手动 nohup」栈的切换（交给有 sudo / 决策权的人）

新栈用**不同的容器名/卷/端口**构建验证，与旧栈完全并存，验证期间绝不动旧栈。
旧栈现状：nginx 容器 `cockpit-nginx-local`（`--network host`，8081）、
nexus uvicorn `127.0.0.1:8000`、auth uvicorn `127.0.0.1:8010`、mongo 容器
`cockpit-mongo`（`127.0.0.1:27117`，卷 `cockpit-mongo-data`）。

切到新栈（**需人工决策 + 部分 sudo**，按顺序）：

1. 确认新栈在 10000 上验证通过（本 README「验证」一节）。
2. 把 `deploy/.env` 的 `NEXUS_DB_NAME` 改回生产库 `nexus_core`、
   `MONGO_VOLUME_NAME` 改回 `cockpit-mongo-data`。
3. **停旧栈**（释放对生产 mongo 卷的独占锁 —— 两个 mongod 不能同时开在同一 dbPath）：
   ```bash
   docker rm -f cockpit-nginx-local cockpit-mongo     # 旧 nginx + 旧 mongo 容器
   # 旧 nexus / auth 是宿主 nohup 进程，按你的启动方式停（kill 对应 uvicorn pid）
   ```
4. 起新栈复用生产卷：`docker compose -f deploy/docker-compose.yml up -d`。
5. 复验 10000 全链路。

> 生产 mongo 卷 `cockpit-mongo-data` 是 `external`：`docker compose down` 甚至
> `down -v` 都不会删它，数据安全。但**新旧 mongo 不能同时挂它**（独占锁），
> 所以第 3 步必须先停旧 mongo 才能让新 mongo 接管生产卷。

## 验证（对着 10000，不碰 8081）

```bash
set -a; . deploy/.env; set +a
B=http://192.168.31.181:10000; CJ=$(mktemp)
curl -s -o /dev/null -w '%{http_code}\n' $B/healthz                 # 200
curl -s -o /dev/null -w '%{http_code}\n' $B/table/                  # 302 -> /login/（未登录）
curl -s -o /dev/null -c "$CJ" -H 'Content-Type: application/json' \
  -d "{\"password\":\"$AUTH_PASSWORD\"}" $B/api/auth/login          # 204 + Set-Cookie
for p in /table/ /ring/ /gantt/ /api/core/export; do
  curl -s -o /dev/null -w "$p %{http_code}\n" -b "$CJ" $B$p          # 全 200
done
curl -s -b "$CJ" $B/api/core/health                                  # {"db":"..."} 确认库名
rm -f "$CJ"
```

自恢复与数据保留：

```bash
docker restart cockpit-c-nexus       # 主动重启，~6s 回 healthy，链路自恢复
docker compose -f deploy/docker-compose.yml down
docker compose -f deploy/docker-compose.yml up -d   # 卷复用，mongo 数据仍在
```
