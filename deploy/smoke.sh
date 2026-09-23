#!/usr/bin/env bash
# 组装层冒烟：对一个已经起好的站点，从门外走一遍。
#
#   未登录：/ 跳 /hive/，/hive/ 与 /api/ 跳 /login/，登录页本身能打开，
#           顶栏计时芯片回降级 JSON（不跳、不泄露）
#   登录后：API 通；/hive/、/ring/ 两个页面，以及页面里引用的**每一个**资源都是 200
#
# 用法：deploy/smoke.sh <口令> [基址，缺省 http://127.0.0.1:8800]
# 整站挂子路径时先 export HONEYCOMB_BASE_PATH=/Cockpit/（与 .env 一致），基址不带前缀。
# 默认 .env（cookie 带 Secure）即可：curl 与浏览器一样，在 127.0.0.1 上回传 Secure cookie。
# CI 用它核对手写与生成的两份组装（见 .github/workflows/ci.yml 的 compose-smoke）。
set -uo pipefail

pw=${1:?用法: deploy/smoke.sh <口令> [基址]}
base=${2:-http://127.0.0.1:8800}
bp=${HONEYCOMB_BASE_PATH:-/}
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

check "${bp}hive/"  "$(location "$base$bp")"                "$bp 跳主界面"
check "${bp}login/" "$(location "$base${bp}hive/")"           "未登录 hive/ 跳登录页"
check "${bp}login/" "$(location "$base${bp}api/core/health")" "未登录 api/ 跳登录页"
check 200     "$(code "$base${bp}login/")"              "登录页不设门"
check '{"degraded":true,"running":false}' "$(curl -s "$base${bp}__cockpit/current")" "顶栏芯片未登录回降级 JSON"

check 204 "$(code -c "$jar" -H 'Content-Type: application/json' \
  -d "{\"password\":\"$pw\"}" "$base${bp}api/auth/login")" "登录"
check 200 "$(code -b "$jar" "$base${bp}api/core/health")" "登录后 API"
# 看响应头，不看 cookie 罐：curl 存罐时会把 Path 末尾的 / 去掉
check 1 "$(curl -s -D - -o /dev/null -H 'Content-Type: application/json' -d "{\"password\":\"$pw\"}" \
  "$base${bp}api/auth/login" | grep -ci "^set-cookie:.*; Path=$bp;")" "cookie 的 Path 跟着前缀"

page() {  # 前缀 页面文件：页面 + 它引用的每个资源都得 200
  check 200 "$(code -b "$jar" "$base$1")" "$1"
  for a in $(grep -oE '(src|href)="[^":#]+"' "$root/$2" | sed -E 's/^[a-z]+="//; s/"$//'); do
    case $a in /*) url=$base$a ;; *) url=$base$1$a ;; esac
    check 200 "$(code -b "$jar" "$url")" "  $url"
  done
}
page "${bp}hive/" modules/hive/code/frontend/index.html
page "${bp}ring/" modules/ring/code/frontend/project-task-contribution-ring.html
# 网关注入给页面的前缀、页签与顶栏资源都在前缀下
hive=$(curl -s -b "$jar" "$base${bp}hive/")
check 1 "$(grep -c "window.HONEYCOMB_BASE=\"$bp\"" <<<"$hive")" "页面拿到站点前缀"
for a in $(grep -oE '(src|href)="[^"]*__cockpit/[^"]*"' <<<"$hive" | sed -E 's/^[a-z]+="//; s/"$//'); do
  check 200 "$(code -b "$jar" "$base$a")" "  注入的 $a"
done

[ "$fail" = 0 ] && echo "✅ 全部通过" || echo "❌ 有失败项"
exit "$fail"
