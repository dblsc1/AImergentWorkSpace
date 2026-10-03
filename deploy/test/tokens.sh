#!/usr/bin/env bash
# 设备令牌（auth.gate v1.2），从门外验：令牌能过网关调 /api/core/*，同一个令牌开不了
# 页面；吊销（HTTP 与命令行）后旧令牌进不来。
# 前提：同 accounts.sh（alice、bob 两个账号，密码都是 $1，站点已起好），另外 .env 里
# 设了 AUTH_SECRET（没有固定密钥 auth 不发令牌）。在 deploy/ 下跑（命令行那段要用 compose）。
# accounts.sh 先跑过的话还会顺带验令牌带着 alice 的租户（看得到 alice-only-zone）。
#
# 用法：deploy/test/tokens.sh <密码> [基址，缺省 http://127.0.0.1:8800]
set -euo pipefail
PW=$1
BASE=${2:-http://127.0.0.1:8800}
A=$(mktemp); trap 'rm -f "$A"' EXIT
C=(curl -s --noproxy '*')
fail=0
check() {  # 名字 实际 期望
  if [ "$2" = "$3" ]; then echo "ok    $1"; else echo "FAIL  $1: 得到 [$2]，期望 [$3]"; fail=1; fi
}
code() {  # 令牌 路径 [curl 选项] → 状态码（不跟随跳转：被拒 = 302 去登录页）
  "${C[@]}" "${@:3}" -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $1" "$BASE$2"
}

"${C[@]}" -c "$A" -o /dev/null -X POST -H 'Content-Type: application/json' \
  -d "{\"username\":\"alice\",\"password\":\"$PW\"}" "$BASE/api/auth/login"
TOK=$("${C[@]}" -b "$A" -X POST -H 'Content-Type: application/json' -d '{"label":"ci"}' \
  "$BASE/api/auth/tokens" | python3 -c 'import json,sys; print(json.load(sys.stdin)["token"])')

check "没令牌调接口被拒" "$("${C[@]}" -o /dev/null -w '%{http_code}' "$BASE/api/core/views/tree")" 302
check "令牌调 /api/core/views/tree" "$(code "$TOK" /api/core/views/tree)" 200
check "同一个令牌开不了页面 /hive/" "$(code "$TOK" /hive/)" 302
# nginx 按解码、规范化后的路径匹配 location（这条落到 /hive/），而 X-Original-URI 是原样
# 的 /api/core/...：verify 必须自己识破，否则令牌借接口的外壳打开页面。
check "绕路 /api/core/../../hive/ 也不行" "$(code "$TOK" /api/core/%2e%2e/%2e%2e/hive/ --path-as-is)" 302
if "${C[@]}" -b "$A" "$BASE/api/core/planner/zones" | grep -q alice-only-zone; then
  check "令牌带着 alice 的租户" \
    "$("${C[@]}" -H "Authorization: Bearer $TOK" "$BASE/api/core/planner/zones" | grep -c alice-only-zone)" 1
fi

# detector.settings.v1：设备令牌只能读检测程序设置，改 / 删必须是人的会话。服务端靠「请求带没带
# Authorization: Bearer」区分——这条要求网关对 /api/core/ 原样转发 Authorization，从门外验一遍。
S='/api/core/detector/settings?deviceId=dev_ci'
BODY='{"schemaVersion":1,"privacy":{"titles":"drop"}}'
check "令牌读检测程序设置" "$(code "$TOK" "$S")" 200
check "令牌改检测程序设置被拒" \
  "$(code "$TOK" "$S" -X PUT -H 'Content-Type: application/json' -d "$BODY")" 403
check "令牌删检测程序设置被拒" "$(code "$TOK" "$S" -X DELETE)" 403
check "网页会话改检测程序设置" "$("${C[@]}" -b "$A" -o /dev/null -w '%{http_code}' -X PUT \
  -H 'Content-Type: application/json' -d "$BODY" "$BASE$S")" 200
check "网页会话删检测程序设置" "$("${C[@]}" -b "$A" -o /dev/null -w '%{http_code}' -X DELETE "$BASE$S")" 204

# detector.rules.v1：同一条规矩管分类规则——令牌能读，改规则、建 / 应用草稿都 403。
R='/api/core/detector/rules'
check "令牌读分类规则" "$(code "$TOK" "$R")" 200
check "令牌改分类规则被拒" \
  "$(code "$TOK" "$R" -X PUT -H 'If-Match: "0"' -H 'Content-Type: application/json' -d '{"rules":[]}')" 403
check "令牌直连建规则草稿被拒" \
  "$(code "$TOK" "$R/drafts" -X POST -H 'Content-Type: application/json' -d '{"rules":[],"summary":"x"}')" 403
check "网页会话改分类规则" "$("${C[@]}" -b "$A" -o /dev/null -w '%{http_code}' -X PUT -H 'If-Match: "0"' \
  -H 'Content-Type: application/json' -d '{"rules":[]}' "$BASE$R")" 200

"${C[@]}" -b "$A" -o /dev/null -X POST -H 'Content-Type: application/json' "$BASE/api/auth/tokens/revoke"
check "网页吊销后旧令牌被拒" "$(code "$TOK" /api/core/views/tree)" 302

BOB=$(docker compose exec -T auth python /app/auth_stub.py token bob 2>/dev/null)
check "命令行发的令牌能用" "$(code "$BOB" /api/core/views/tree)" 200
docker compose exec -T auth python /app/auth_stub.py revoke bob >/dev/null
sleep 3  # 后台每 2 秒重读令牌文件
check "命令行吊销后被拒" "$(code "$BOB" /api/core/views/tree)" 302

[ "$fail" = 0 ] && echo "✅ 设备令牌全部通过" || { echo "❌ 有失败"; exit 1; }
