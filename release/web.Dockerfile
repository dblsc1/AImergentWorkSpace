# HoneyComb 网关镜像（发布版）：nginx + 网关模板 + 顶栏 + 登录页 + hive / ring 两个前端。
#
# 源码版（deploy/docker-compose.yml）是只读挂载这些目录、不 build 的；发布版把同一批
# 文件烤进镜像，下载的人不用 clone 仓库。两边用的是**同一份文件**，不是复制出来的另一份。
# 构建上下文是仓库根：docker build -f release/web.Dockerfile .
FROM nginx:1.27-alpine

COPY deploy/nginx/templates/default.conf.template /etc/nginx/templates/default.conf.template
COPY modules/nginx-docker/nginx /etc/nginx/honeycomb
COPY modules/nginx-docker/static /usr/share/nginx/html/__cockpit
COPY contracts/auth.gate.v1/stub/web /usr/share/nginx/html/login
COPY modules/hive/code/frontend /usr/share/nginx/html/hive
COPY modules/ring/code/frontend /usr/share/nginx/html/ring
# 单测与测试工具不进镜像
RUN find /usr/share/nginx/html \( -name '*.test.js' -o -name '*.md' \) -delete \
 && rm -rf /usr/share/nginx/html/ring/tests /usr/share/nginx/html/hive/run_tests.sh \
 && mkdir -p /etc/nginx/templates/extra

# 与 deploy/docker-compose.yml 的 web 同一组缺省值（contracts/gateway.v1）
ENV AUTH_UPSTREAM=auth:8010 \
    HONEYCOMB_BASE_PATH=/ \
    NGINX_ENVSUBST_FILTER=^(AUTH_UPSTREAM|HONEYCOMB_)

HEALTHCHECK --interval=10s --timeout=5s --retries=5 --start-period=10s \
  CMD wget -q -O - http://127.0.0.1/healthz || exit 1
