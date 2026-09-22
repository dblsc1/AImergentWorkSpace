#!/usr/bin/env bash
# 组装层冒烟：对一个已经起好的站点，从门外走一遍。
#
#   未登录：/ 跳 /hive/，/hive/ 与 /api/ 跳 /login/，登录页本身能打开，
#           顶栏计时芯片回降级 JSON（不跳、不泄露）
#   登录后：API 通；/hive/、/ring/ 两个页面，以及页面里引用的**每一个**资源都是 200
#
# 用法：deploy/smoke.sh <口令> [基址，缺省 http://127.0.0.1:8800]
# 本机 HTTP 跑的话 .env 里要 AUTH_COOKIE_SECURE=false，否则 curl 不回传 cookie。
# CI 用它核对手写与生成的两份组装（见 .github/workflows/ci.yml 的 compose-smoke）。
set -uo pipefail

pw=${1:?用法: deploy/smoke.sh <口令> [基址]}
base=${2:-http://127.0.0.1:8800}
root=$(cd "$(dirname "$0")/.." && pwd)
jar=$(mktemp)
trap 'rm -f "$jar"' EXIT
fail=0

check() {  # 期望 实际 说明
  if [ "$1" = "$2" ]; then echo "ok    $3"; else echo "FAIL  $3：期望 $1，实际 ${2:-<空>}"; fail=1; fi
}
code() { curl -s -o /dev/null -w '%{http_code}' "$@"; }
location() { curl -s -o /dev/null -D - "$@" | tr -d '\r' | awk 'tolower($1)=="location:"{print $2}'; }

# web 容器没有 healthcheck 时 `compose up --wait` 只等到「在跑」，nginx 可能还没监听。
for _ in $(seq 30); do curl -fs "$base/healthz" >/dev/null && break; sleep 1; done

check /hive/  "$(location "$base/")"                "/ 跳主界面"
check /login/ "$(location "$base/hive/")"           "未登录 /hive/ 跳登录页"
check /login/ "$(location "$base/api/core/health")" "未登录 /api/ 跳登录页"
check 200     "$(code "$base/login/")"              "登录页不设门"
check '{"degraded":true,"running":false}' "$(curl -s "$base/__cockpit/current")" "顶栏芯片未登录回降级 JSON"

check 204 "$(code -c "$jar" -H 'Content-Type: application/json' \
  -d "{\"password\":\"$pw\"}" "$base/api/auth/login")" "登录"
check 200 "$(code -b "$jar" "$base/api/core/health")" "登录后 API"

page() {  # 前缀 页面文件：页面 + 它引用的每个资源都得 200
  check 200 "$(code -b "$jar" "$base$1")" "$1"
  for a in $(grep -oE '(src|href)="[^":#]+"' "$root/$2" | sed -E 's/^[a-z]+="//; s/"$//'); do
    case $a in /*) url=$base$a ;; *) url=$base$1$a ;; esac
    check 200 "$(code -b "$jar" "$url")" "  $url"
  done
}
page /hive/ modules/hive/code/frontend/index.html
page /ring/ modules/ring/code/frontend/project-task-contribution-ring.html

[ "$fail" = 0 ] && echo "✅ 全部通过" || echo "❌ 有失败项"
exit "$fail"
