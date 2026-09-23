# 占位认证服务镜像（发布版）：contracts/auth.gate.v1 的参考实现，纯标准库。
# 构建上下文是仓库根：docker build -f release/auth.Dockerfile .
FROM python:3.12-slim

COPY contracts/auth.gate.v1/stub/auth_stub.py /app/auth_stub.py
# 账号文件（账号+密码登录）放这里，发布版 compose 挂 named volume
VOLUME /data
ENV AUTH_BIND=0.0.0.0:8010 \
    AUTH_USERS_FILE=/data/users.json
EXPOSE 8010
HEALTHCHECK --interval=10s --timeout=5s --retries=5 --start-period=10s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8010/api/auth/health').status==200 else 1)"
CMD ["python", "/app/auth_stub.py"]
