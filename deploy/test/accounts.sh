#!/usr/bin/env bash
# 占位认证的账号+密码登录（auth.gate v1.1）+ nexus-core 按租户分数据，从门外验。
# 前提：.env 里 HONEYCOMB_PASSWORD 留空、NEXUS_TENANT_STRICT=1，并已用
#   docker compose run --rm -T auth python /app/auth_stub.py adduser alice|bob
# 建好两个账号（密码都是 $1），站点已起好。
#
# 用法：deploy/test/accounts.sh <密码> [基址，缺省 http://127.0.0.1:8800]
set -euo pipefail
PW=$1
BASE=${2:-http://127.0.0.1:8800}
A=$(mktemp); B=$(mktemp); trap 'rm -f "$A" "$B"' EXIT
C=(curl -s --noproxy '*')
fail=0
check() {  # 名字 实际 期望
  if [ "$2" = "$3" ]; then echo "ok    $1"; else echo "FAIL  $1: 得到 [$2]，期望 [$3]"; fail=1; fi
}
login() {  # jar 账号 密码 → 状态码
  "${C[@]}" -c "$1" -o /dev/null -w '%{http_code}' -X POST -H 'Content-Type: application/json' \
    -d "{\"username\":\"$2\",\"password\":\"$3\"}" "$BASE/api/auth/login"
}

check "登录页知道开了账号登录" \
  "$("${C[@]}" "$BASE/api/auth/health")" '{"status":"ok","accounts":true,"sharedPassword":false}'
check "alice 登录" "$(login "$A" alice "$PW")" 204
check "bob 登录" "$(login "$B" bob "$PW")" 204
check "密码错 401" "$(login /dev/null alice wrong-password)" 401

check "/me 形状（auth.gate v1.1）" \
  "$("${C[@]}" -b "$A" "$BASE/api/auth/me" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["ok"], d["user"]["name"], d["user"]["id"].startswith("u_"))')" \
  "True alice True"
check "nexus-core 开着严格模式" \
  "$("${C[@]}" -b "$A" "$BASE/api/core/health" | python3 -c 'import json,sys; print(json.load(sys.stdin)["tenantGuard"])')" \
  "strict"

"${C[@]}" -b "$A" -o /dev/null -X POST -H 'Content-Type: application/json' \
  -d '{"name":"alice-only-zone"}' "$BASE/api/core/planner/zones"
check "alice 看得到自己建的分区" \
  "$("${C[@]}" -b "$A" "$BASE/api/core/planner/zones" | grep -c alice-only-zone)" 1
check "bob 看不到 alice 的分区" \
  "$("${C[@]}" -b "$B" "$BASE/api/core/planner/zones" | grep -c alice-only-zone || true)" 0

[ "$fail" = 0 ] && echo "✅ 多账号全部通过" || { echo "❌ 有失败"; exit 1; }
