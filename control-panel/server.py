#!/usr/bin/env python3
"""任务控制台服务 —— 只用 Python 标准库，零依赖、零外部资源。

  python3 control-panel/server.py            默认 127.0.0.1:8787
  AIMERGENT_PANEL_PORT=9000 python3 ...      换端口

它只读仓里已有的留痕（logs/diary.jsonl、各角色 report.json），
不写任何东西、不引入新数据源 —— 面板不该成为第二个事实源。

每个脚本 source 了 scripts/lib/emit.sh，激活与退出都会写进 diary，
所以「一举一动」是自动可见的，不需要各脚本各自上报一遍。
"""
import json
import os
import subprocess
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = Path(
    subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=HERE,
                   capture_output=True, text=True).stdout.strip() or HERE.parent
)
DIARY = ROOT / "logs" / "diary.jsonl"
PORT = int(os.environ.get("AIMERGENT_PANEL_PORT", "8787"))


def _resolve_host():
    """AIMERGENT_PANEL_HOST 支持三种值：
       · 未设 / 127.0.0.1  只本机（默认，最安全）
       · tailscale         自动解析本机 tailnet 地址 —— 只有 tailnet 内设备够得着
       · 具体 IP           照绑
    面板**没有任何鉴权**，所以不要绑 0.0.0.0：这台机器还有局域网与 docker 网卡。
    """
    h = os.environ.get("AIMERGENT_PANEL_HOST", "127.0.0.1")
    if h != "tailscale":
        return h
    try:
        out = subprocess.run(["tailscale", "ip", "-4"], capture_output=True,
                             text=True, timeout=5).stdout.strip().splitlines()
        if out and out[0].strip():
            return out[0].strip()
    except (OSError, subprocess.SubprocessError):
        pass
    print("⚠️  取不到 tailscale 地址，退回 127.0.0.1（只本机可见）")
    return "127.0.0.1"


HOST = _resolve_host()


def read_events(limit=300):
    if not DIARY.exists():
        return []
    out = []
    for line in DIARY.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out[-limit:]


def read_reports():
    """扫全仓 report.json —— 报告流那一栏的数据源。"""
    reports = []
    try:
        tracked = subprocess.run(["git", "ls-files"], cwd=ROOT,
                                 capture_output=True, text=True).stdout.splitlines()
    except OSError:
        tracked = []
    for rel in tracked:
        if not rel.endswith("report.json"):
            continue
        p = ROOT / rel
        if not p.exists():
            continue
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        ts = subprocess.run(["git", "log", "-1", "--format=%aI", "--", rel],
                            cwd=ROOT, capture_output=True, text=True).stdout.strip()
        reports.append({
            "path": rel, "ts": ts,
            "role": d.get("role", "?"), "module": d.get("module", "?"),
            "task": d.get("task", ""), "tier": d.get("tier", ""),
            "status": d.get("status", "?"), "summary": d.get("summary", ""),
            "escalated": d.get("escalation") is not None,
            "contract_touched": (d.get("contract") or {}).get("touched", False),
            "reviewer": (d.get("reviewer_opinion") or {}).get("verdict", "—"),
            "sub_reports": len(d.get("sub_reports") or []),
        })
    reports.sort(key=lambda r: r.get("ts") or "", reverse=True)
    return reports


def snapshot():
    ev = read_events()
    def count(pred):
        return sum(1 for e in ev if pred(e))
    branch = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=ROOT,
                            capture_output=True, text=True).stdout.strip()
    reports = read_reports()
    return {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "branch": branch, "root": str(ROOT),
        "events": list(reversed(ev)),
        "reports": reports,
        "metrics": {
            "scripts_run": count(lambda e: e.get("event") == "script_start"),
            "reports_total": len(reports),
            "rejected": sum(1 for r in reports if r["status"] == "rejected"),
            "escalated": sum(1 for r in reports if r["escalated"]),
            "exam_fail": count(lambda e: e.get("event") == "exam" and e.get("pass") is False),
            "blocked": count(lambda e: e.get("event") == "mission_blocked"),
            "override": count(lambda e: e.get("event") == "mission_override"),
            "failures": count(lambda e: e.get("event") == "script_end" and e.get("rc", 0) != 0),
        },
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # 别把访问日志刷进终端
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            f = HERE / "index.html"
            if not f.exists():
                return self._send(404, "index.html 缺失", "text/plain; charset=utf-8")
            return self._send(200, f.read_bytes(), "text/html; charset=utf-8")
        if path == "/api/snapshot":
            return self._send(200, json.dumps(snapshot(), ensure_ascii=False))
        if path == "/api/stream":
            return self._stream()
        return self._send(404, "not found", "text/plain; charset=utf-8")

    def _stream(self):
        """SSE：diary 有新行就推过去，面板即时看到每个 sh 的一举一动。"""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        pos = DIARY.stat().st_size if DIARY.exists() else 0
        try:
            while True:
                if DIARY.exists():
                    size = DIARY.stat().st_size
                    if size < pos:      # 文件被截断/重建
                        pos = 0
                    if size > pos:
                        with DIARY.open("r", encoding="utf-8", errors="replace") as fh:
                            fh.seek(pos)
                            chunk = fh.read()
                            pos = fh.tell()
                        for line in chunk.splitlines():
                            if line.strip():
                                self.wfile.write(f"data: {line.strip()}\n\n".encode("utf-8"))
                        self.wfile.flush()
                self.wfile.write(b": ping\n\n")
                self.wfile.flush()
                time.sleep(1.0)
        except (BrokenPipeError, ConnectionResetError):
            return


def main():
    DIARY.parent.mkdir(parents=True, exist_ok=True)
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"任务控制台  http://{HOST}:{PORT}   仓根 {ROOT}")
    if HOST not in ("127.0.0.1", "localhost"):
        print("⚠️  面板无鉴权：凡能访问该地址的设备都能读全部留痕与报告。")
    print("（只读留痕，不写任何东西。Ctrl-C 停止）")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")


if __name__ == "__main__":
    main()
