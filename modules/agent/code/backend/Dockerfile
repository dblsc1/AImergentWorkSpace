# agent —— agent.chat.v1 的缺省实现（opencode 适配器）
#
# opencode 钉死版本（npm 包 opencode-ai，官方发布渠道之一）。升级 = 改这一行 + 跑 tests/test_integration.py：
# 那里坐实的事件名、权限名、配置键都可能随版本变。
ARG OPENCODE_VERSION=1.18.33

FROM node:22-slim AS opencode
ARG OPENCODE_VERSION
# npm 按架构装对应的平台包（opencode-linux-x64 / -arm64），postinstall 把二进制放到 bin/ 下
RUN npm install -g --no-audit --no-fund "opencode-ai@${OPENCODE_VERSION}" \
 && cp "$(readlink -f "$(npm root -g)/opencode-ai/bin/opencode.exe")" /opencode \
 && /opencode --version

FROM python:3.12-slim
COPY --from=opencode /opencode /usr/local/bin/opencode

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY app/ ./app/

# 不用 root 跑：模型能碰到的进程越少权限越好（opencode 的执行类工具已全部拒绝，这是第二道）。
RUN useradd --create-home --uid 10001 agent && mkdir -p /data && chown agent:agent /data
USER agent
ENV AGENT_DATA_DIR=/data
VOLUME /data
EXPOSE 8030

# 0.0.0.0 只在 honeycomb-agent-net 内网里（compose 不映射宿主端口）。
# opencode 子进程另外只听 127.0.0.1，容器外连不上（agent.chat.v1 第六节第 5 条）。
CMD ["python", "-m", "uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8030"]
