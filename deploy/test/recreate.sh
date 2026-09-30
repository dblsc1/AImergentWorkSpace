#!/usr/bin/env bash
# 后端被单独重建（换了 IP）之后，网关**不重启**也得照常转发（2026-09-30 真机 bug：
# 升级时 `docker compose up -d` 只重建了 nexus-core，网关还打旧 IP，全站 502）。
#
# 在 compose 目录下跑：deploy/test/recreate.sh <口令> [基址，缺省 http://127.0.0.1:8800]
# 整站挂子路径时同 smoke.sh 先 export HONEYCOMB_BASE_PATH。逐个重建 nexus-core、auth，
# 以及组装里有的 mcp、agent（AI 桥），不碰 web。
set -uo pipefail

pw=${1:?用法: deploy/test/recreate.sh <口令> [基址]}
base=${2:-http://127.0.0.1:8800}
bp=${HONEYCOMB_BASE_PATH:-/}
jar=$(mktemp)
trap 'rm -f "$jar"' EXIT
fail=0

code() { curl -s -o /dev/null -w '%{http_code}' "$@"; }
login() { code -c "$jar" -H 'Content-Type: application/json' -d "{\"password\":\"$pw\"}" "$base${bp}api/auth/login"; }
ips() { docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}' "$1"; }
web_started() { docker inspect -f '{{.Id}} {{.State.StartedAt}}' "$(docker compose ps -q web)"; }
probe() {  # 经网关打这个服务的一条受保护路由
  case $1 in
    mcp) code -b "$jar" -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
           -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' "$base${bp}api/mcp/" ;;
    agent) code -b "$jar" "$base${bp}api/agent/health" ;;
    *) code -b "$jar" "$base${bp}api/core/health" ;;   # nexus-core；auth 走的是门子请求
  esac
}
web0=$(web_started)

svcs="nexus-core auth"
for s in mcp agent; do [ -n "$(docker compose ps -q "$s")" ] && svcs="$svcs $s"; done

for svc in $svcs; do
  before=$(ips "$(docker compose ps -q "$svc")")
  # 真机上重建后 IP 会变，但 Docker 也可能把刚放出来的 IP 原样分回来——那就什么也测不出。
  # 所以先停掉它，起占位容器（web 的一次性容器，与它同在各张网上，down 会清）直到旧 IP 全被占住，再重建。
  docker compose stop "$svc" >/dev/null 2>&1
  holders= held=
  for _ in 1 2 3 4 5 6; do
    h=$(docker compose run -d --rm --no-deps --entrypoint sleep web 300 2>/dev/null) || break
    holders="$holders $h" held="$held $(ips "$h")"
    missing=0; for x in $before; do case " $held " in *" $x "*) ;; *) missing=1 ;; esac; done
    [ "$missing" = 0 ] && break
  done
  docker compose up -d --force-recreate --no-deps --wait --wait-timeout 120 "$svc" >/dev/null 2>&1 \
    || { echo "FAIL  重建 $svc 没起来"; exit 1; }
  after=$(ips "$(docker compose ps -q "$svc")")
  [ -n "$holders" ] && docker rm -f $holders >/dev/null 2>&1
  same=; for x in $before; do case " $after " in *" $x "*) same="$same $x" ;; esac; done
  [ -z "$same" ] || { echo "FAIL  $svc 重建后 IP 没变（$same），测不出东西"; fail=1; continue; }
  # resolver valid=10s：最多等 30 秒。auth 重建后旧会话失效，每轮重新登录（顺带走 /api/auth/）
  got=
  for _ in $(seq 30); do
    if [ "$(login)" = 204 ]; then
      got=$(probe "$svc")
      [ "$got" = 200 ] && break
    fi
    sleep 1
  done
  if [ "$got" = 200 ]; then echo "ok    重建 $svc（$before→ $after）后不重启网关，经网关 200"
  else echo "FAIL  重建 $svc（$before→ $after）后经网关回 ${got:-登录失败}"; fail=1; fi
done
chip=$(curl -s -b "$jar" "$base${bp}__cockpit/current")
case $chip in *degraded*|'') echo "FAIL  顶栏芯片没走通 nexus-core：${chip:-<空>}"; fail=1 ;;
  *'"running"'*) echo "ok    顶栏芯片走通 nexus-core" ;; *) echo "FAIL  顶栏芯片：$chip"; fail=1 ;; esac
[ "$(web_started)" = "$web0" ] || { echo "FAIL  web 被重建或重启了，测试无效"; fail=1; }

[ "$fail" = 0 ] && echo "✅ 全部通过" || echo "❌ 有失败项"
exit "$fail"
