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
check "tools/list：17 个工具，不是只读的只有 propose_ 那三个与认窗口的两个（v1.13）" \
  "$(mcp "$TOK" tools/list '{}' | py 't=r["result"]["tools"]; print(len(t), [x["name"] for x in t if not x["annotations"]["readOnlyHint"]])')" \
  "17 ['propose_detector_rules', 'propose_activity_matches', 'propose_report', 'get_window_awaiting_target', 'suggest_window_target']"
# detector.rules.v1：经 MCP 起草规则 → 草稿在，但生效规则没变（应用只有人能，令牌直连 403）
V=$("${C[@]}" -b "$A" "$BASE/api/core/detector/rules" | py 'print(r["version"])')  # tokens.sh 可能已经存过
check "经 MCP 起草分类规则（草稿，不生效）" \
  "$(mcp "$TOK" tools/call "{\"name\":\"propose_detector_rules\",\"arguments\":{\"rules\":[{\"title\":\"mcp-ci\",\"taskId\":\"$T\"}],\"summary\":\"ci\"}}" \
     | py 's=r["result"]["structuredContent"]; print(r["result"]["isError"], s["applied"], s["diff"]["added"] != [])')" "False False True"
check "起草后生效规则没变" \
  "$("${C[@]}" -b "$A" "$BASE/api/core/detector/rules" | py 'print(r["version"])')" "$V"
D=$("${C[@]}" -b "$A" "$BASE/api/core/detector/rules/drafts/current" | py 'print(r["draft"]["id"])')
check "令牌应用草稿被拒" "$(post "/api/core/detector/rules/drafts/$D/apply" '' -H "Authorization: Bearer $TOK" \
  -H "If-Match: \"$V\"" -o /dev/null -w '%{http_code}')" 403
check "网页会话应用草稿" "$(post "/api/core/detector/rules/drafts/$D/apply" '' -b "$A" -H "If-Match: \"$V\"" \
  | py 'print(r["version"], [x["title"] for x in r["rules"]])')" "$((V + 1)) ['mcp-ci']"
check "bob 看不到 alice 的规则" \
  "$(mcp "$BOB" tools/call '{"name":"get_detector_rules","arguments":{}}' | py 'print(r["result"]["structuredContent"]["rules"])')" "[]"
# nexus-core v2.7 AI 匹配：设备令牌上传一段活动 → 经 MCP 配任务（只是建议）→ 令牌直连 matches / unmatch 403
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
check "经 MCP 给活动配任务（只是建议，不入账）" \
  "$(mcp "$TOK" tools/call "{\"name\":\"propose_activity_matches\",\"arguments\":{\"matches\":[{\"suggestionId\":\"$S\",\"taskId\":\"$T\",\"confidence\":0.6,\"reason\":\"ci\"}]}}" \
     | py 's=r["result"]["structuredContent"]; print(r["result"]["isError"], s["matched"], s["rejected"], s["confirmed"])')" "False 1 [] False"
check "配完仍是 pending，classifier=assistant" \
  "$("${C[@]}" -b "$A" "$BASE/api/core/activity/suggestions" | py "print([(i['status'], i['suggestion']['taskId'] == '$T', i['suggestion']['classifier']) for i in r['items'] if i['id'] == '$S'])")" \
  "[('pending', True, 'assistant')]"
check "网页会话说「否」→ 任务清掉并记住" \
  "$(post "/api/core/activity/suggestions/$S/unmatch" "{\"taskId\":\"$T\"}" -b "$A" | py "print(r['status'], r['rejectedTaskIds'] == ['$T'])")" "pending True"
check "bob 经 MCP 配不了 alice 的建议" \
  "$(mcp "$BOB" tools/call "{\"name\":\"propose_activity_matches\",\"arguments\":{\"matches\":[{\"suggestionId\":\"$S\",\"taskId\":\"$T\",\"confidence\":0.6}]}}" \
     | py 's=r["result"]["structuredContent"]; print(s["matched"], len(s["rejected"]))')" "0 1"
# v1.7 匹配历史（nexus-core v2.12）：人确认之后，助理读得到「这个窗口 → 这个任务」；别的账号读不到
check "网页会话确认这段到任务" \
  "$(post "/api/core/activity/suggestions/$S/confirm" "{\"taskId\":\"$T\"}" -b "$A" | py 'print(r["status"])')" confirmed
check "alice 经 MCP 读到匹配历史" \
  "$(mcp "$TOK" tools/call '{"name":"get_match_history","arguments":{}}' \
     | py "s=r['result']['structuredContent']; print([(i['app'], i['title'], i['path'], i['via'], i['count']) for i in s['items']], s['rejected'])")" \
  "[('code', 'mcp-ci', 'mcp-zone / mcp-proj / alice-mcp-task', 'confirm', 1)] []"
check "令牌直连也读得到历史（只读，同建议列表）" \
  "$("${C[@]}" -H "Authorization: Bearer $TOK" "$BASE/api/core/activity/suggestions/history" | py 'print(len(r["items"]))')" 1
check "bob 经 MCP 读不到 alice 的历史" \
  "$(mcp "$BOB" tools/call '{"name":"get_match_history","arguments":{}}' | py 'print(r["result"]["structuredContent"]["items"])')" "[]"
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
# v1.9 让 AI 认窗口（nexus-core v2.15）：没有窗口在等 → null；没被认领的 key 什么都写不进去；令牌直连那三个端点都 403
K=wk_00000000000000000000
RV=$("${C[@]}" -b "$A" "$BASE/api/core/detector/rules" | py 'print(r["version"])')
check "经 MCP 取等 AI 认的窗口：没有" \
  "$(mcp "$TOK" tools/call '{"name":"get_window_awaiting_target","arguments":{}}' | py 's=r["result"]; print(s["isError"], s["structuredContent"]["window"])')" "False None"
check "经 MCP 认一个没在等的窗口 → isError 409" \
  "$(mcp "$TOK" tools/call "{\"name\":\"suggest_window_target\",\"arguments\":{\"key\":\"$K\",\"taskId\":\"$T\",\"confidence\":0.9,\"reason\":\"ci\"}}" \
     | py 's=r["result"]; print(s["isError"], s["structuredContent"]["error"]["status"])')" "True 409"
check "规则没被它动过" "$("${C[@]}" -b "$A" "$BASE/api/core/detector/rules" | py 'print(r["version"])')" "$RV"
check "令牌直连认领被拒" "$(post /api/core/activity/ai/claim '{}' -H "Authorization: Bearer $TOK" -o /dev/null -w '%{http_code}')" 403
check "令牌直连回答被拒" "$(post /api/core/activity/ai/suggest "{\"key\":\"$K\",\"none\":true,\"reason\":\"ci\"}" -H "Authorization: Bearer $TOK" -o /dev/null -w '%{http_code}')" 403
check "令牌替人说「不对」被拒" "$(post /api/core/activity/choice/reject "{\"key\":\"$K\"}" -H "Authorization: Bearer $TOK" -o /dev/null -w '%{http_code}')" 403
check "网页会话对不是 AI 认的窗口说「不对」→ 404" "$(post /api/core/activity/choice/reject "{\"key\":\"$K\"}" -b "$A" -o /dev/null -w '%{http_code}')" 404

[ "$fail" = 0 ] && echo "✅ MCP 全部通过" || { echo "❌ 有失败"; exit 1; }
