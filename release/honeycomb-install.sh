#!/usr/bin/env sh
# HoneyComb 一键安装（Linux / macOS）。只需要 Docker，不需要源码、不需要 Python。
#
#   curl -fsSL https://github.com/dblsc1/AImergentWorkSpace/releases/download/__TAG__/honeycomb-install.sh | sh
#
# 做的事：
#   1. 检查 docker 与 docker compose；
#   2. 在 ./honeycomb（或 $HONEYCOMB_DIR）放下本版的 docker-compose.yml；
#   3. 第一次装：生成 .env，随机登录口令与会话密钥（口令打印一次，也存在 .env 里）；
#      再次运行：保留 .env 与数据，只换成本版镜像——也就是升级；
#   4. 拉镜像、起服务、等它健康，告诉你打开哪个地址。
#
# 环境变量（都可不设）：
#   HONEYCOMB_DIR     安装目录，缺省 ./honeycomb
#   HONEYCOMB_BIND    监听地址，缺省 127.0.0.1:8800（只本机能访问，这是故意的）
set -eu

# 整个脚本包在 main 里、最后一行才调用：`curl | sh` 半路断网时，没下载完的脚本
# 连 main 都没定义完，什么也不会执行，不会留下半装的状态。
main() {

  TAG=__TAG__
  REPO=https://github.com/dblsc1/AImergentWorkSpace
  DIR=${HONEYCOMB_DIR:-./honeycomb}
  BIND=${HONEYCOMB_BIND:-127.0.0.1:8800}

  say() { printf '%s\n' "$*"; }
  die() { printf '❌ %s\n' "$*" >&2; exit 1; }

  command -v docker >/dev/null 2>&1 || die "没找到 docker。先装 Docker：https://docs.docker.com/get-docker/"
  docker compose version >/dev/null 2>&1 || die "没找到 docker compose（v2）。装新版 Docker Desktop 或 docker-compose-plugin。"
  docker info >/dev/null 2>&1 || die "docker 在，但连不上（没启动？当前用户没权限？试试 sudo，或把自己加进 docker 组）。"

  mkdir -p "$DIR/extra"
  cd "$DIR"

  # 取本版的 compose。HONEYCOMB_ASSETS 指向本地目录时从那里拷（CI 用，发布前验证）。
  if [ -n "${HONEYCOMB_ASSETS:-}" ]; then
    cp "$HONEYCOMB_ASSETS/docker-compose.yml" docker-compose.yml
  elif command -v curl >/dev/null 2>&1; then
    curl -fsSL "$REPO/releases/download/$TAG/docker-compose.yml" -o docker-compose.yml
  else
    wget -qO docker-compose.yml "$REPO/releases/download/$TAG/docker-compose.yml"
  fi

  rand() {  # 32 位十六进制，不依赖 openssl
    od -An -N16 -tx1 /dev/urandom | tr -d ' \n'
  }

  fresh=0
  if [ ! -f .env ]; then
    fresh=1
    pw=$(rand)
    umask 077
    cat > .env <<EOF
# HoneyComb 配置。改完 docker compose up -d 生效。
# 登录口令（共享口令：一个口令、一份数据）。要多人各用各的，见 README「多个账号」。
HONEYCOMB_PASSWORD=$pw
# 签登录会话用，别外传；换掉它 = 所有人重新登录。
AUTH_SECRET=$(rand)
# 只本机能访问。要放到局域网改成 0.0.0.0:8800，但先在前面加 TLS（见 README）。
HONEYCOMB_BIND=$BIND
HONEYCOMB_TZ=Asia/Shanghai
EOF
  fi

  say "拉镜像（第一次要几分钟）……"
  # compose 的 -q 压不住逐层进度（走 stderr），整段收进日志，出错才打印
  if [ -z "${HONEYCOMB_NO_PULL:-}" ]; then
    docker compose pull -q >.pull.log 2>&1 || { cat .pull.log; die "拉镜像失败，检查网络。"; }
  fi
  docker compose up -d --wait --wait-timeout 300 >.up.log 2>&1 || {
    cat .up.log; docker compose ps
    die "没起来。看日志：cd $DIR && docker compose logs"
  }

  # 升级时以 .env 里的为准（可能改过端口）
  BIND=$(sed -n 's/^HONEYCOMB_BIND=//p' .env | tail -1); BIND=${BIND:-127.0.0.1:8800}
  port=${BIND##*:}
  say ""
  say "✅ HoneyComb $TAG 已经跑起来了：http://127.0.0.1:$port/"
  if [ "$fresh" = 1 ]; then
    say "   登录口令：$pw"
    say "   （也存在 $DIR/.env 里。演示数据、多个账号、升级与卸载见 README。）"
  else
    say "   沿用原来的 .env 和数据（这次是升级）。"
  fi
  say "   停：cd $DIR && docker compose down"
}

main "$@"
