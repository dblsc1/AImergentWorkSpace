# nexus-core / backend —— 全栈 compose 用镜像
#
# 取舍（任务交付项 1）：**依赖进镜像，源码走 bind mount**。
#   · requirements.txt 在构建期 pip install 到系统 site-packages（可复现的依赖层，
#     改依赖才需 rebuild）。
#   · app/ 也 COPY 进镜像，使镜像**自带一份可运行副本**——脱离宿主源码也能跑，
#     这对将来生产切换/迁移是稳的一侧。
#   · 但 deploy/docker-compose.yml 会把宿主 code/backend 只读挂到 /app 覆盖它，
#     保「编辑即时生效」的开发工作流（与当前 nohup 直跑源码的习惯一致）。
#     改了源码 `docker restart honeycomb-nexus-core` 即可，不必 rebuild。
#
# 配置全部来自 env（app/config.py 是唯一读 env 处；缺 NEXUS_MONGO_URI/NEXUS_DB_NAME/
# NEXUS_TZ 会在进程启动那一刻 die，不带坏配置继续跑）。
FROM python:3.12-slim

WORKDIR /app

# 先装依赖（单独一层，命中缓存）。--no-cache-dir 让镜像小一点。
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# 镜像自带副本（bind mount 未挂载时的兜底）。
COPY app/ ./app/

EXPOSE 8000

# 实际监听由 CLI 钉死 0.0.0.0:8000（容器内全网卡，暴露面由 compose 的端口映射控制，
# 本服务在 compose 里根本不映射宿主端口，只在 bridge 网内被 nginx 以 nexus:8000 访问）。
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
