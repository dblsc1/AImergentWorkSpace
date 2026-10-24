#!/usr/bin/env bash
# MCP 工具（contracts/mcp.tools.v1 传输 + mcp.tools.v2 工具集）从门外验：设备令牌（auth.gate v1.3）经网关调 MCP，
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
check "tools/list：15 个工具（write 令牌，mcp.tools.v2），不是只读的只有 6 个写工具" \
  "$(mcp "$TOK" tools/list '{}' | py 't=r["result"]["tools"]; print(len(t), [x["name"] for x in t if not x["annotations"]["readOnlyHint"]])')" \
  "15 ['CRUD_Windows_Classify_Rules', 'CRUD_Windowsblock_Classify_Rules', 'CRUD_Windowsblock', 'CRUD_Honeycomb', 'Submit_Report', 'Submit_Questions_To_Human']"
check "initialize 的 instructions 只有一句" \
  "$(mcp "$TOK" initialize '{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"ci","version":"1"}}' \
     | py 'print(r["result"]["instructions"])')" "第一次用先调 Get_Started"
check "v1 工具名只回迁移说明" \
  "$(mcp "$TOK" tools/call '{"name":"get_task_tree","arguments":{}}' \
     | py 's=r["result"]["structuredContent"]; print(r["result"]["isError"], s["deprecated"], s["use"])')" "False True Get_View"
check "bob 看不到 alice 的第一级规则" \
  "$(mcp "$BOB" tools/call '{"name":"Get_Rules","arguments":{"level":1}}' | py 'print(r["result"]["structuredContent"]["level1"]["rules"])')" "[]"
# nexus-core v2.7 AI 匹配：设备令牌上传一段活动 → 令牌直连 matches / unmatch 403（MCP v2 不再包装它们）
SEG=$(python3 -c "
import json; from datetime import datetime, timedelta, timezone
e = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=5)
print(json.dumps({'deviceId': 'dev_mcpci', 'segments': [{'startAt': (e - timedelta(minutes=4)).isoformat(), 'endAt': e.isoformat(),
  'durationSeconds': 200, 'app': 'code', 'title': 'mcp-ci', 'suggestion': {'taskId': None, 'confidence': 0.0, 'reason': '', 'classifier': 'rules'}}]}))")
check "令牌上传一段活动" "$(post /api/core/activity/suggestions "$SEG" -H "Authorization: Bearer $TOK" | py 'print(r["accepted"] + r["duplicates"])')" 1
S=$("${C[@]}" -b "$A" "$BASE/api/core/activity/suggestions" | py 'print([i["id"] for i in r["items"] if i["title"] == "mcp-ci"][0])')
M="{\"matches\":[{\"id\":\"$S\",\"taskId\":\"$T\",\"confidence\":0.5}]}"
check "令牌直连 matches 被拒" "$(post /api/core/activity/suggestions/matches "$M" -H "Authorization: Bearer $TOK" -o /dev/null -w '%{http_code}')" 403
check "令牌直连 unmatch 被拒" "$(post "/api/core/activity/suggestions/$S/unmatch" '' -H "Authorization: Bearer $TOK" -o /dev/null -w '%{http_code}')" 403
check "令牌直连也读得到历史（只读，同建议列表）" \
  "$("${C[@]}" -H "Authorization: Bearer $TOK" "$BASE/api/core/activity/suggestions/history" -o /dev/null -w '%{http_code}')" 200
check "alice 经 MCP 看得到自己的任务" \
  "$(mcp "$TOK" tools/call '{"name":"Get_View","arguments":{"depth":"task"}}' \
     | py "print([i['name'] for z in r['result']['structuredContent']['tree'] for p in z.get('children', []) for i in p.get('children', []) if i['id']=='$T'])")" \
  "['alice-mcp-task']"
check "bob 经 MCP 看不到 alice 的任务" \
  "$(mcp "$BOB" tools/call '{"name":"Get_View","arguments":{"depth":"task"}}' \
     | grep -c alice-mcp-task || true)" 0
check "经 MCP 提议新任务（只是提议，什么都没建）" \
  "$(mcp "$TOK" tools/call "{\"name\":\"CRUD_Honeycomb\",\"arguments\":{\"op\":\"create\",\"kind\":\"task\",\"name\":\"mcp-ci-new\",\"to\":\"$P\",\"reason\":\"ci\"}}" \
     | py 's=r["result"]; print(s["isError"], s["structuredContent"]["result"]["ok"], s["structuredContent"]["result"]["status"])')" "False True pending"
check "提议没有直接施加（总开关没开 → 403）" \
  "$(mcp "$TOK" tools/call "{\"name\":\"CRUD_Honeycomb\",\"arguments\":{\"op\":\"rename\",\"target\":\"$T\",\"name\":\"mcp-ci-renamed\",\"direct_apply\":true}}" \
     | py 's=r["result"]; print(s["isError"], s["structuredContent"]["error"]["status"])')" "True 403"
check "任务名没变" \
  "$("${C[@]}" -b "$A" "$BASE/api/core/views/tree" | py "print([t['name'] for p in r['projects'] for t in p['tasks'] if t['id']=='$T'])")" "['alice-mcp-task']"
check "bob 经 MCP 提议不了 alice 的项目（对不上）" \
  "$(mcp "$BOB" tools/call "{\"name\":\"CRUD_Honeycomb\",\"arguments\":{\"op\":\"create\",\"kind\":\"task\",\"name\":\"x\",\"to\":\"$P\"}}" \
     | py 's=r["result"]; print(s["isError"], s["structuredContent"]["error"]["status"])')" "True 400"
check "会话 cookie 也能调 MCP" \
  "$(post /api/mcp/ '{"jsonrpc":"2.0","id":1,"method":"ping"}' -b "$A" | py 'print(r["result"])')" "{}"
check "日期格式不对 → isError 400（带 example）" \
  "$(mcp "$TOK" tools/call '{"name":"Get_History","arguments":{"fromDate":"2026/09/01"}}' \
     | py 's=r["result"]; print(s["isError"], s["structuredContent"]["error"]["status"], "example" in s["structuredContent"]["error"])')" "True 400 True"
check "带陌生 Origin → 403" \
  "$(mcp "$TOK" ping '{}' -H 'Origin: https://evil.example' -o /dev/null -w '%{http_code}')" 403
check "令牌开不了聊天后端 /api/agent/（只认会话）" \
  "$("${C[@]}" -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $TOK" "$BASE/api/agent/sessions")" 302
# nexus-core v2.15 让 AI 认窗口：令牌直连那三个端点都 403（MCP v2 不再包装它们）
K=wk_00000000000000000000
RV=$("${C[@]}" -b "$A" "$BASE/api/core/detector/rules" | py 'print(r["version"])')
check "规则版本没变" "$("${C[@]}" -b "$A" "$BASE/api/core/detector/rules" | py 'print(r["version"])')" "$RV"
check "令牌直连认领被拒" "$(post /api/core/activity/ai/claim '{}' -H "Authorization: Bearer $TOK" -o /dev/null -w '%{http_code}')" 403
check "令牌直连回答被拒" "$(post /api/core/activity/ai/suggest "{\"key\":\"$K\",\"none\":true,\"reason\":\"ci\"}" -H "Authorization: Bearer $TOK" -o /dev/null -w '%{http_code}')" 403
check "令牌替人说「不对」被拒" "$(post /api/core/activity/choice/reject "{\"key\":\"$K\"}" -H "Authorization: Bearer $TOK" -o /dev/null -w '%{http_code}')" 403
check "网页会话对不是 AI 认的窗口说「不对」→ 404" "$(post /api/core/activity/choice/reject "{\"key\":\"$K\"}" -b "$A" -o /dev/null -w '%{http_code}')" 404

[ "$fail" = 0 ] && echo "✅ MCP 全部通过" || { echo "❌ 有失败"; exit 1; }
