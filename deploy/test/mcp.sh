#!/usr/bin/env bash
# MCP 只读工具（contracts/mcp.tools.v1）从门外验：设备令牌（auth.gate v1.3）经网关调 MCP，
# 两个账号的数据互相看不见；令牌开不了聊天后端；带陌生 Origin 403；时间缺偏移 400。
# 前提：同 tokens.sh（alice、bob 两个账号，密码都是 $1，严格租户，设了 AUTH_SECRET，站点已起好）。
# 在 deploy/ 下跑（命令行发令牌要用 compose）。
#
# 用法：deploy/test/mcp.sh <密码> [基址，缺省 http://127.0.0.1:8800]
set -euo pipefail
PW=$1
BASE=${2:-http://127.0.0.1:8800}
A=$(mktemp); trap 'rm -f "$A"' EXIT
C=(curl -s --noproxy '*')
fail=0
check() {  # 名字 实际 期望
  if [ "$2" = "$3" ]; then echo "ok    $1"; else echo "FAIL  $1: 得到 [$2]，期望 [$3]"; fail=1; fi
}
post() {  # 路径 JSON [curl 选项] → 响应体
  "${C[@]}" -X POST -H 'Content-Type: application/json' -d "$2" "${@:3}" "$BASE$1"
}
mcp() {  # 令牌 方法 params [curl 选项] → 响应体
  post /api/mcp/ "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"$2\",\"params\":$3}" \
    -H "Authorization: Bearer $1" -H 'Accept: application/json, text/event-stream' "${@:4}"
}
py() { python3 -c "import json,sys; r=json.load(sys.stdin); $1"; }

# alice 建一棵自己的树
"${C[@]}" -c "$A" -o /dev/null -X POST -H 'Content-Type: application/json' \
  -d "{\"username\":\"alice\",\"password\":\"$PW\"}" "$BASE/api/auth/login"
Z=$(post /api/core/planner/zones '{"name":"mcp-zone"}' -b "$A" | py 'print(r["id"])')
P=$(post /api/core/planner/projects "{\"zoneId\":\"$Z\",\"name\":\"mcp-proj\"}" -b "$A" | py 'print(r["id"])')
T=$(post /api/core/planner/tasks "{\"projectId\":\"$P\",\"name\":\"alice-mcp-task\"}" -b "$A" | py 'print(r["id"])')
TOK=$(post /api/auth/tokens '{"label":"mcp"}' -b "$A" | py 'print(r["token"])')
BOB=$(docker compose exec -T auth python /app/auth_stub.py token bob 2>/dev/null)
sleep 3  # 命令行发的令牌：auth 后台每 2 秒重读令牌文件

check "没登录调 MCP 被拒（302 去登录页）" \
  "$(post /api/mcp/ '{}' -o /dev/null -w '%{http_code}')" 302
check "令牌 initialize" \
  "$(mcp "$TOK" initialize '{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"ci","version":"1"}}' \
     | py 'print(r["result"]["protocolVersion"], list(r["result"]["capabilities"]))')" "2025-06-18 ['tools']"
check "tools/list：9 个工具全部只读" \
  "$(mcp "$TOK" tools/list '{}' | py 't=r["result"]["tools"]; print(len(t), all(x["annotations"]["readOnlyHint"] for x in t))')" \
  "9 True"
check "alice 经 MCP 看得到自己的任务与路径" \
  "$(mcp "$TOK" tools/call '{"name":"get_task_tree","arguments":{}}' \
     | py "print([i['path'] for i in r['result']['structuredContent']['items'] if i['taskId']=='$T'])")" \
  "['mcp-zone / mcp-proj / alice-mcp-task']"
check "bob 经 MCP 看不到 alice 的任务" \
  "$(mcp "$BOB" tools/call '{"name":"get_task_tree","arguments":{"includeDone":true,"includeEphemeral":true}}' \
     | grep -c alice-mcp-task || true)" 0
check "会话 cookie 也能调 MCP" \
  "$(post /api/mcp/ '{"jsonrpc":"2.0","id":1,"method":"ping"}' -b "$A" | py 'print(r["result"])')" "{}"
check "时间不带偏移 → isError 400" \
  "$(mcp "$TOK" tools/call '{"name":"list_time_sessions","arguments":{"from":"2026-09-01"}}' \
     | py 's=r["result"]; print(s["isError"], s["structuredContent"]["error"]["status"])')" "True 400"
check "带陌生 Origin → 403" \
  "$(mcp "$TOK" ping '{}' -H 'Origin: https://evil.example' -o /dev/null -w '%{http_code}')" 403
check "令牌开不了聊天后端 /api/agent/（只认会话）" \
  "$("${C[@]}" -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $TOK" "$BASE/api/agent/sessions")" 302

[ "$fail" = 0 ] && echo "✅ MCP 全部通过" || { echo "❌ 有失败"; exit 1; }
