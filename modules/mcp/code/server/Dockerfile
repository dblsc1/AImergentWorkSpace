# HoneyComb MCP 服务（contracts/mcp.tools.v1）。纯标准库，没有 requirements。
# 源码版（deploy/docker-compose.yml）与发布版（ghcr.io/dblsc1/honeycomb-mcp）都用这一份。
FROM python:3.12-slim
WORKDIR /app
COPY mcp_server.py tools.py ./
# 非 root 跑：它只读、只连 nexus-core，用不着任何权限
USER nobody
EXPOSE 8020
ENV PYTHONUNBUFFERED=1
HEALTHCHECK --interval=10s --timeout=5s --retries=5 --start-period=5s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8020/healthz').status==200 else 1)"
CMD ["python", "mcp_server.py"]
