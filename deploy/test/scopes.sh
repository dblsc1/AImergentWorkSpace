#!/usr/bin/env bash
# 令牌的权限范围与匿名上报（auth.gate v1.4、gateway.v1 第九节、nexus-core v2.19、mcp.tools v1.11），
# 从门外、经真网关验一遍：
#
#   report 令牌  能上报代理运行；读不到任何东西（GET 403）、用不了 MCP（403）、传不了事件（403）
#   read 令牌    能读、能上报；写不了（403）；MCP 只列只读工具、调会写的工具被拒
#   write 令牌   同以前的设备令牌
#   不带凭据     单人模式：只能上报，开出来的运行标 unverified，碰不到验证过的运行；多账号：一律被拒
#   伪造的范围头 / 匿名头 / 绕路的路径：都没用。单个吊销立即生效。匿名上报有限速、有同时在跑的上限。
#
# 前提：站点已起好，.env 里设了 AUTH_SECRET。单人模式用共享口令登录；开了账号时用 alice 登录（同 accounts.sh）。
# 整站挂子路径时先 export HONEYCOMB_BASE_PATH=/Cockpit/。
#
# 用法：deploy/test/scopes.sh <密码> [基址，缺省 http://127.0.0.1:8800]
set -euo pipefail
PW=$1
BASE=${2:-http://127.0.0.1:8800}${HONEYCOMB_BASE_PATH:-/}
BASE=${BASE%/}
J=$(mktemp); trap 'rm -f "$J"' EXIT
C=(curl -s --noproxy '*')
fail=0
check() {  # 名字 实际 期望
  if [ "$2" = "$3" ]; then echo "ok    $1"; else echo "FAIL  $1: 得到 [$2]，期望 [$3]"; fail=1; fi
}
py() { python3 -c "import json,sys; r=json.load(sys.stdin); $1"; }
code() { "${C[@]}" -o /dev/null -w '%{http_code}' "$@"; }   # 不跟随跳转：被门拒 = 302 去登录页
bearer() { echo "Authorization: Bearer $1"; }
JSON='Content-Type: application/json'
RUN='{"agent":"ci-agent","tool":"scopes.sh"}'
START=$BASE/api/core/agents/start
TREE=$BASE/api/core/views/tree
MCP=$BASE/api/mcp/
LIST='{"jsonrpc":"2.0","id":1,"method":"tools/list"}'

for _ in $(seq 30); do "${C[@]}" -f "$BASE/api/auth/health" >/dev/null && break; sleep 1; done
MULTI=$("${C[@]}" "$BASE/api/auth/health" | py 'print(int(r["accounts"]))')
if [ "$MULTI" = 1 ]; then LOGIN="{\"username\":\"alice\",\"password\":\"$PW\"}"; else LOGIN="{\"password\":\"$PW\"}"; fi
check "登录" "$(code -c "$J" -X POST -H "$JSON" -d "$LOGIN" "$BASE/api/auth/login")" 204
mint() {  # 范围 → 「令牌 id」
  "${C[@]}" -b "$J" -X POST -H "$JSON" -d "{\"scope\":\"$1\",\"name\":\"ci-$1\"}" "$BASE/api/auth/tokens" \
    | py 'print(r["token"], r["id"])'
}
read -r REPORT REPORT_ID < <(mint report)
read -r READ _ < <(mint read)
read -r WRITE _ < <(mint write)

echo "── report：只能上报"
RID=$("${C[@]}" -X POST -H "$(bearer "$REPORT")" -H "$JSON" -d "$RUN" "$START" | py 'print(r["runId"])')
check "report 令牌开一个运行" "${RID:0:4}" "run_"
check "report 令牌报相位" "$(code -X POST -H "$(bearer "$REPORT")" -H "$JSON" \
  -d "{\"phase\":\"working\",\"at\":\"$(date -u +%Y-%m-%dT%H:%M:%S+00:00)\"}" "$BASE/api/core/agents/$RID/phase")" 200
check "report 令牌结束运行" "$(code -X POST -H "$(bearer "$REPORT")" -H "$JSON" -d '{"outcome":"done"}' \
  "$BASE/api/core/agents/$RID/stop")" 200
for path in views/tree views/current views/lanes views/agent-time events export planner/zones "agents/$RID/phase"; do
  check "report 令牌读 $path 被拒" "$(code -H "$(bearer "$REPORT")" "$BASE/api/core/$path")" 403
done
check "report 令牌调 MCP 被拒" "$(code -X POST -H "$(bearer "$REPORT")" -H "$JSON" -d "$LIST" "$MCP")" 403
check "report 令牌传事件被拒" "$(code -X POST -H "$(bearer "$REPORT")" -H "$JSON" -d '{"events":[]}' "$BASE/api/core/events")" 403
check "report 令牌开人的计时被拒" "$(code -X POST -H "$(bearer "$REPORT")" "$BASE/api/core/timer/stop")" 403
check "report 令牌开不了页面" "$(code -H "$(bearer "$REPORT")" "$BASE/hive/")" 302

echo "── 绕路与伪造"
for path in agents/start/ agents//start agents/%73tart agents/Start "agents/$RID/stop/" "agents/$RID/Stop"; do
  check "report 令牌 POST $path 不在表里" \
    "$(code --path-as-is -X POST -H "$(bearer "$REPORT")" -H "$JSON" -d "$RUN" "$BASE/api/core/$path")" 403
done
# nginx 把这两条规范化成 /api/core/views/tree 与 /hive/；门看的是原样路径，必须自己识破
check "report 令牌 GET agents/../views/tree" \
  "$(code --path-as-is -H "$(bearer "$REPORT")" "$BASE/api/core/agents/../views/tree")" 302
check "report 令牌 GET agents/%2e%2e/views/tree" \
  "$(code --path-as-is -H "$(bearer "$REPORT")" "$BASE/api/core/agents/%2e%2e/views/tree")" 302
check "report 令牌 POST agents/start/../../timer/stop" \
  "$(code --path-as-is -X POST -H "$(bearer "$REPORT")" "$BASE/api/core/agents/start/../../timer/stop")" 302
check "report 令牌 HEAD 上报端点" "$(code -I -H "$(bearer "$REPORT")" "$START")" 403
check "report 令牌 OPTIONS 上报端点" "$(code -X OPTIONS -H "$(bearer "$REPORT")" "$START")" 403
check "report 令牌 + 方法覆盖头读不了" \
  "$(code -X POST -H "$(bearer "$REPORT")" -H 'X-HTTP-Method-Override: GET' "$TREE")" 403
check "report 令牌自称 write 没用（范围头被网关覆盖）" \
  "$(code -H "$(bearer "$REPORT")" -H 'X-Nexus-Scope: write' -H 'X-Original-Method: POST' \
     -H "X-Original-URI: ${HONEYCOMB_BASE_PATH:-/}api/core/agents/start" "$TREE")" 403
check "read 令牌自称 write 写不了" \
  "$(code -X POST -H "$(bearer "$READ")" -H 'X-Nexus-Scope: write' -H 'X-Original-Method: GET' "$BASE/api/core/timer/stop")" 403
# 人的会话带着伪造的头：被网关盖掉，照常是人（读得到、开的运行不是 unverified）
check "网页会话自带 report / 匿名头被盖掉" \
  "$(code -b "$J" -H 'X-Nexus-Scope: report' -H 'X-Nexus-Anonymous: 1' "$TREE")" 200
HID=$("${C[@]}" -b "$J" -X POST -H "$JSON" -H 'X-Nexus-Anonymous: 1' -d "$RUN" "$START" | py 'print(r["runId"])')
check "网页会话开的运行不是 unverified" \
  "$("${C[@]}" -b "$J" "$BASE/api/core/views/lanes" | py "print([a['unverified'] for a in r['agents'] if a['runId'] == '$HID'])")" "[False]"

echo "── read：能读、能上报，不能写"
check "read 令牌读任务树" "$(code -H "$(bearer "$READ")" "$TREE")" 200
check "read 令牌读泳道" "$(code -H "$(bearer "$READ")" "$BASE/api/core/views/lanes")" 200
check "read 令牌上报" "$(code -X POST -H "$(bearer "$READ")" -H "$JSON" -d "$RUN" "$START")" 201
check "read 令牌传事件被拒" "$(code -X POST -H "$(bearer "$READ")" -H "$JSON" -d '{"events":[]}' "$BASE/api/core/events")" 403
check "read 令牌传在场心跳被拒" "$(code -X POST -H "$(bearer "$READ")" -H "$JSON" -d '{}' "$BASE/api/core/activity/presence")" 403
check "read 令牌 + 方法覆盖头写不了" \
  "$(code -X POST -H "$(bearer "$READ")" -H 'X-HTTP-Method-Override: GET' "$BASE/api/core/timer/stop")" 403
check "read 令牌的 MCP：只列只读工具" \
  "$("${C[@]}" -X POST -H "$(bearer "$READ")" -H "$JSON" -d "$LIST" "$MCP" \
     | py 't=r["result"]["tools"]; print(len(t), [x["name"] for x in t if not x["annotations"]["readOnlyHint"]])')" "11 []"
check "read 令牌的 MCP：只读工具能调" \
  "$("${C[@]}" -X POST -H "$(bearer "$READ")" -H "$JSON" \
     -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"list_projects","arguments":{}}}' "$MCP" \
     | py 'print(r["result"]["isError"])')" "False"
check "read 令牌的 MCP：会写的工具被拒" \
  "$("${C[@]}" -X POST -H "$(bearer "$READ")" -H "$JSON" \
     -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"get_window_awaiting_target","arguments":{}}}' "$MCP" \
     | py 'print(r["result"]["isError"], r["result"]["structuredContent"]["error"]["status"])')" "True 403"

echo "── write：同以前的设备令牌"
check "write 令牌读" "$(code -H "$(bearer "$WRITE")" "$TREE")" 200
check "write 令牌写" "$(code -X POST -H "$(bearer "$WRITE")" "$BASE/api/core/timer/stop")" 200
check "write 令牌的 MCP：十五个工具" \
  "$("${C[@]}" -X POST -H "$(bearer "$WRITE")" -H "$JSON" -d "$LIST" "$MCP" | py 'print(len(r["result"]["tools"]))')" 15
check "write 令牌改检测设置照旧被拒（只许人）" \
  "$(code -X DELETE -H "$(bearer "$WRITE")" "$BASE/api/core/detector/settings?deviceId=dev_ci")" 403
check "write 令牌开不了页面、换不出新令牌" \
  "$(code -H "$(bearer "$WRITE")" "$BASE/hive/") $(code -X POST -H "$(bearer "$WRITE")" -H "$JSON" -d '{}' "$BASE/api/auth/tokens")" "302 401"
check "令牌列表里没有令牌本身" \
  "$("${C[@]}" -b "$J" "$BASE/api/auth/tokens" | python3 -c "
import sys; body = sys.stdin.read()
print(any(t in body for t in sys.argv[1:]), body.count('\"scope\"') >= 3)" "$REPORT" "$READ" "$WRITE" "${REPORT##*.}")" "False True"

echo "── 单个吊销"
check "吊销 report 令牌" "$(code -b "$J" -X POST -H "$JSON" -d "{\"tokenId\":\"$REPORT_ID\"}" "$BASE/api/auth/tokens/revoke")" 204
check "吊销后它立即被拒（不是降成匿名）" "$(code -X POST -H "$(bearer "$REPORT")" -H "$JSON" -d "$RUN" "$START")" 302
check "别的令牌不受影响" "$(code -H "$(bearer "$READ")" "$TREE")" 200

echo "── 不带凭据"
check "匿名读被拒" "$(code "$TREE")" 302
check "匿名调 MCP 被拒" "$(code -X POST -H "$JSON" -d "$LIST" "$MCP")" 302
check "匿名传事件被拒" "$(code -X POST -H "$JSON" -d '{"events":[]}' "$BASE/api/core/events")" 302
check "坏令牌上报被拒（不降成匿名）" "$(code -X POST -H 'Authorization: Bearer hct2.bad' -H "$JSON" -d "$RUN" "$START")" 302
if [ "$MULTI" = 1 ]; then
  check "多账号：匿名上报被拒（没有可归属的租户）" "$(code -X POST -H "$JSON" -d "$RUN" "$START")" 302
  check "多账号：自称匿名 / 自带租户也没用" \
    "$(code -X POST -H "$JSON" -H 'X-Nexus-Anonymous: 1' -H 'X-Nexus-Scope: report' -H 'X-Nexus-Tenant: u_local' -d "$RUN" "$START")" 302
else
  AID=$("${C[@]}" -X POST -H "$JSON" -d "$RUN" "$START" | py 'print(r["runId"])')
  check "单人：匿名开一个运行" "${AID:0:4}" "run_"
  check "它在泳道里标着 unverified" \
    "$("${C[@]}" -b "$J" "$BASE/api/core/views/lanes" | py "print([a['unverified'] for a in r['agents'] if a['runId'] == '$AID'])")" "[True]"
  check "匿名自称 write / 抹掉匿名头没用" \
    "$(code -H 'X-Nexus-Scope: write' -H 'X-Nexus-Anonymous;' "$TREE")" 302
  check "匿名碰不到验证过的运行（与不存在同一个 404）" \
    "$(code -X POST -H "$JSON" -d '{"outcome":"failed"}' "$BASE/api/core/agents/$HID/stop") $(code -X POST -H "$JSON" -d '{"outcome":"failed"}' "$BASE/api/core/agents/run_000000000000/stop")" "404 404"
  check "匿名 POST 上报端点带结尾斜杠被拒" "$(code -X POST -H "$JSON" -d "$RUN" "$START/")" 302
  check "匿名结束自己的运行" "$(code -X POST -H "$JSON" -d '{"outcome":"done"}' "$BASE/api/core/agents/$AID/stop")" 200
  check "验证过的那个运行没被动过" \
    "$(code -b "$J" -X POST -H "$JSON" -d '{"outcome":"done"}' "$BASE/api/core/agents/$HID/stop")" 200

  echo "── 滥用的边界（放最后：限速区会被打满）"
  # 带令牌的不进匿名限速区：连发 70 个（超过突发 60）一个 429 都没有
  n429=0
  for _ in $(seq 70); do
    [ "$(code -X POST -H "$(bearer "$WRITE")" -H "$JSON" -d '{"outcome":"done"}' "$BASE/api/core/agents/$AID/stop")" = 429 ] && n429=$((n429 + 1))
  done
  check "带令牌的上报不被匿名限速" "$n429" 0
  # 匿名连发 100 个 start：先撞同时在跑的上限（后端的 429，JSON），再撞网关限速（nginx 的 429）
  capped=0; limited=0; created=0
  for _ in $(seq 100); do
    out=$("${C[@]}" -w '\n%{http_code}' -X POST -H "$JSON" -d "$RUN" "$START")
    case "${out##*$'\n'}" in
      201) created=$((created + 1)) ;;
      429) if grep -q detail <<<"$out"; then capped=$((capped + 1)); else limited=$((limited + 1)); fi ;;
    esac
  done
  echo "      匿名连发 100 个：新开 $created，撞上限 $capped，被限速 $limited"
  check "同时在跑的匿名运行有上限（≤ 20）" "$([ "$created" -le 20 ] && [ "$capped" -ge 1 ] && echo yes)" yes
  check "匿名上报有限速（429）" "$([ "$limited" -ge 1 ] && echo yes)" yes
  check "限速不影响带凭据的请求" "$(code -b "$J" "$TREE") $(code -H "$(bearer "$READ")" "$TREE")" "200 200"
fi

[ "$fail" = 0 ] && echo "✅ 权限范围与匿名上报全部通过" || { echo "❌ 有失败"; exit 1; }
