"""假的 MCP 服务（mcp.tools.v1 的最小形状，测试用，零依赖）。

Streamable HTTP、无状态：POST 收 JSON-RPC，GET 回 405。两个只读工具。
每次 tools/call 记下 X-Nexus-Tenant 与有没有 Cookie/Authorization；GET /_log 查看。
"""
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOG = []
LOCK = threading.Lock()
RO = {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False}
TOOLS = [
    {"name": "get_task_tree", "description": "任务树", "annotations": RO,
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "get_current_timer", "description": "此刻在计什么", "annotations": RO,
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}},
]


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, code, obj=None):
        b = json.dumps(obj).encode() if obj is not None else b""
        self.send_response(code)
        if obj is not None:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path == "/_log":
            with LOCK:
                return self._send(200, LOG)
        self._send(405, {"detail": "no server stream"})

    def do_DELETE(self):
        with LOCK:
            LOG.clear()
        self._send(200, {})

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        method, rid = req.get("method"), req.get("id")
        with LOCK:
            LOG.append({"method": method, "tenant": self.headers.get("X-Nexus-Tenant"),
                        "tool": (req.get("params") or {}).get("name"),
                        "cookie": "Cookie" in self.headers, "authorization": "Authorization" in self.headers})
        if rid is None:
            return self._send(202)
        if method == "initialize":
            res = {"protocolVersion": (req.get("params") or {}).get("protocolVersion", "2025-06-18"),
                   "capabilities": {"tools": {}}, "serverInfo": {"name": "fake-honeycomb", "version": "0"}}
        elif method == "tools/list":
            res = {"tools": TOOLS}
        elif method == "tools/call":
            data = {"tenant": self.headers.get("X-Nexus-Tenant"), "items": []}
            res = {"structuredContent": data, "content": [{"type": "text", "text": json.dumps(data)}]}
        elif method == "ping":
            res = {}
        else:
            return self._send(200, {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "no"}})
        self._send(200, {"jsonrpc": "2.0", "id": rid, "result": res})


if __name__ == "__main__":
    port = int(sys.argv[1] if len(sys.argv) > 1 else os.environ.get("PORT", "8020"))
    ThreadingHTTPServer(("0.0.0.0", port), H).serve_forever()
