"""假的 OpenAI 兼容模型服务（测试用，零依赖）。

没有真密钥也能端到端跑 opencode：AGENT_BASE_URL 指到这里。按用户最后一句话决定怎么答：
  - 含 `call:<工具名>` 且请求里带了名字以它结尾的工具 → 先回一个工具调用；工具结果回来后答 `TOOL_OK`
  - 含 `force:<名字>` → 不管请求里有没有，硬调这个工具（证明被拒的工具在服务端也执行不了）
  - 含 `slow` → 每 0.3 秒一个片段，共 200 个（测取消 / 断连）
  - 含 `fail` → 401
  - 其它 → 流式回 `Hello from fake`
GET /_log 看收到过的请求（路径、带没带密钥、工具名、系统提示）；DELETE /_log 清空。
"""
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOG = []
# 硬调工具时的入参：够 bash / read / webfetch 各自跑起来
FORCE_ARGS = {"command": "echo pwned > pwned.txt", "description": "x", "filePath": "/etc/passwd",
              "url": "http://example.com", "format": "text"}
LOCK = threading.Lock()


def _last_user(messages):
    for m in reversed(messages):
        if m.get("role") == "user":
            c = m.get("content")
            if isinstance(c, list):
                return " ".join(p.get("text", "") for p in c if isinstance(p, dict))
            return c or ""
    return ""


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _json(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        with LOCK:
            LOG.append({"method": "GET", "path": self.path})
        if self.path == "/_log":
            return self._json(200, LOG)
        if self.path.rstrip("/").endswith("/models"):
            return self._json(200, {"object": "list", "data": [{"id": "fake-model", "object": "model"}]})
        self._json(404, {"error": {"message": "not found"}})

    def do_DELETE(self):
        with LOCK:
            LOG.clear()
        self._json(200, {})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        msgs = body.get("messages") or []
        tools = [t.get("function", {}).get("name") for t in body.get("tools") or []]
        system = "\n".join(
            m["content"] if isinstance(m.get("content"), str) else json.dumps(m.get("content"))
            for m in msgs if m.get("role") == "system")
        with LOCK:
            LOG.append({"method": "POST", "path": self.path, "auth": self.headers.get("Authorization"),
                        "stream": body.get("stream"), "model": body.get("model"), "tools": tools,
                        "system": system, "last": msgs[-1].get("role") if msgs else None})
        if not self.path.rstrip("/").endswith("/chat/completions"):
            return self._json(404, {"error": {"message": "not found"}})
        text = _last_user(msgs)
        if "fail" in text:
            return self._json(401, {"error": {"message": "invalid api key sk-SECRET-LEAK", "type": "auth"}})
        call, args = None, {}
        if msgs and msgs[-1].get("role") != "tool" and "call:" in text:
            want = text.split("call:", 1)[1].split()[0]
            call = next((t for t in tools if t and t.endswith(want)), None)
        if msgs and msgs[-1].get("role") != "tool" and "force:" in text:
            call, args = text.split("force:", 1)[1].split()[0], FORCE_ARGS
        if msgs and msgs[-1].get("role") == "tool":
            pieces = ["TOOL_OK"]
        elif "slow" in text:
            pieces = [f"s{i} " for i in range(200)]
        else:
            pieces = ["Hello", " from", " fake"]
        if not body.get("stream"):
            msg = {"role": "assistant", "content": "".join(pieces)}
            return self._json(200, {"id": "c1", "object": "chat.completion", "model": body.get("model"),
                                    "choices": [{"index": 0, "message": msg, "finish_reason": "stop"}],
                                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Connection", "close")
        self.end_headers()

        def chunk(delta, finish=None):
            c = {"id": "c1", "object": "chat.completion.chunk", "created": int(time.time()),
                 "model": body.get("model"), "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
            self.wfile.write(f"data: {json.dumps(c)}\n\n".encode())
            self.wfile.flush()

        try:
            chunk({"role": "assistant", "content": ""})
            if call:
                chunk({"tool_calls": [{"index": 0, "id": "call_1", "type": "function",
                                       "function": {"name": call, "arguments": json.dumps(args)}}]})
                chunk({}, "tool_calls")
            else:
                for p in pieces:
                    chunk({"content": p})
                    if "slow" in text:
                        time.sleep(0.3)
                chunk({}, "stop")
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            with LOCK:
                LOG.append({"method": "POST", "path": self.path, "aborted": True})
        self.close_connection = True


if __name__ == "__main__":
    port = int(sys.argv[1] if len(sys.argv) > 1 else os.environ.get("PORT", "9100"))
    ThreadingHTTPServer(("0.0.0.0", port), H).serve_forever()
