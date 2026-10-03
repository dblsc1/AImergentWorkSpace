#!/usr/bin/env bash
# gateway.v1 契约的门外验证。前提：站点按 deploy/test/override.yml 起好，
# 且 .env 里 HONEYCOMB_EXTRA_ROUTES_DIR=./test/extra。
#
# 用法：deploy/test/gateway_contract.sh <口令> [基址，缺省 http://127.0.0.1:8800]
set -euo pipefail
PW=$1
BASE=${2:-http://127.0.0.1:8800}
JAR=$(mktemp); trap 'rm -f "$JAR"' EXIT
C=(curl -s --noproxy '*')
fail=0
check() {  # 名字 实际 期望
  if [ "$2" = "$3" ]; then echo "ok    $1"; else echo "FAIL  $1: 得到 [$2]，期望 [$3]"; fail=1; fi
}

# 额外路由受同一道门保护：未登录 302 到登录页
check "额外路由未登录跳登录页" \
  "$("${C[@]}" -o /dev/null -w '%{http_code} %{redirect_url}' "$BASE/__echo_tenant")" \
  "302 $BASE/login/"

"${C[@]}" -c "$JAR" -o /dev/null -X POST -H 'Content-Type: application/json' \
  -d "{\"password\":\"$PW\"}" "$BASE/api/auth/login"

# 客户端自带的租户头到不了后端（占位件不给租户，后端应看到空）
check "客户端伪造的 X-Nexus-Tenant 被网关覆盖" \
  "$("${C[@]}" -b "$JAR" -H 'X-Nexus-Tenant: evil' "$BASE/__echo_tenant")" \
  "tenant=[] base=[/]"

# 认证服务确实是 AUTH_UPSTREAM 指的那个：缺省的 auth 被 override 关掉了，登录态照样有效
check "缺省 auth 已关、AUTH_UPSTREAM 指 auth2，仍能过门" \
  "$("${C[@]}" -b "$JAR" -o /dev/null -w '%{http_code}' "$BASE/api/core/health")" \
  "200"
check "登录接口也走 AUTH_UPSTREAM" \
  "$("${C[@]}" -o /dev/null -w '%{http_code}' -X POST -H 'Content-Type: application/json' -d "{\"password\":\"$PW\"}" "$BASE/api/auth/login")" \
  "204"

# 第八节 AI 桥：/api/agent/ 过门；转给 AGENT_UPSTREAM 时去掉 Cookie 与 Authorization、保留路径与查询串；
# mcp 被 profiles 关掉，网关照常起，/api/mcp/ 回 502
check "聊天后端路由未登录跳登录页" \
  "$("${C[@]}" -o /dev/null -w '%{http_code} %{redirect_url}' "$BASE/api/agent/x")" \
  "302 $BASE/login/"
check "聊天后端收不到 Cookie / Authorization、收不到伪造的租户" \
  "$("${C[@]}" -b "$JAR" -H 'Authorization: Basic Zm9vOmJhcg==' -H 'X-Nexus-Tenant: evil' "$BASE/api/agent/x?q=1")" \
  "path=[/api/agent/x?q=1] cookie=[] auth=[] tenant=[]"
check "mcp 关掉时网关照常起，/api/mcp/ 回 502" \
  "$("${C[@]}" -b "$JAR" -o /dev/null -w '%{http_code}' -X POST -H 'Content-Type: application/json' -d '{}' "$BASE/api/mcp/")" \
  "502"

[ "$fail" = 0 ] && echo "✅ gateway.v1 契约全部通过" || { echo "❌ 有失败"; exit 1; }
