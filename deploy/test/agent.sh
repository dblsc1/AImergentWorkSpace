#!/usr/bin/env bash
# agent.chat.v1 组装测试（配 test/agent.yml）。在 deploy/ 下跑。
#  1. 容器里跑适配器全部测试，真 opencode 必须在（AGENT_REQUIRE_OPENCODE=1），假模型用内网服务名
#  2. 按 compose 的环境变量起来的那个服务本身：health、建会话、带工具调用的一轮对话、MCP 收到的租户头
set -euo pipefail
dc() { docker compose -f docker-compose.yml -f test/agent.yml "$@"; }

dc exec -T -e AGENT_REQUIRE_OPENCODE=1 -e FAKE_LLM_URL=http://fake-llm:9100 -e FAKE_MCP_URL=http://fake-mcp:8020 \
  -w /app agent python -m pytest -q -p no:cacheprovider tests

dc exec -T agent python - <<'PY'
import json, urllib.request
B = "http://127.0.0.1:8030/api/agent/"
def req(m, p, body=None, tenant="alice"):
    r = urllib.request.Request(B + p, method=m, data=json.dumps(body).encode() if body is not None else None,
                               headers={"Content-Type": "application/json", "X-Nexus-Tenant": tenant})
    return urllib.request.urlopen(r, timeout=120)
h = json.load(req("GET", "health"))
assert h == {"status": "ok", "configured": True}, h
sid = json.load(req("POST", "sessions", {}))["id"]
body = req("POST", f"sessions/{sid}/messages", {"text": "call:get_task_tree"}).read().decode()
events = [l[7:] for l in body.splitlines() if l.startswith("event: ")]
assert events == ["start", "tool", "tool", "delta", "done"], body
log = json.load(urllib.request.urlopen("http://fake-mcp:8020/_log"))
assert [e["tenant"] for e in log if e["method"] == "tools/call"][-1] == "alice", log
print("agent service ok:", events)
PY
