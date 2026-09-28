"""agent-hooks 的单测：内进程假 HTTP 服务器录请求 + 真 subprocess 跑 cockpit-run。

跑法：python3 -m pytest -q tools/agent-hooks
（也支持 `python3 -m unittest discover -s tools/agent-hooks`，纯标准库无第三方依赖）
"""

from __future__ import annotations

import http.server
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import cockpit_client as cc  # noqa: E402
import claude_hook  # noqa: E402

RUN_SCRIPT = HERE / "cockpit-run"
HOOK_SCRIPT = HERE / "claude_hook.py"


# ── 假 cockpit：记请求、按 token 判 401、start/stop 各回该回的形状 ──
class _RequestLog:
    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.lock = threading.Lock()

    def add(self, entry: dict) -> None:
        with self.lock:
            self.requests.append(entry)


def _make_handler(log: _RequestLog, expect_token: str | None):
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):  # 别刷测试输出
            pass

        def _handle(self):
            length = int(self.headers.get("Content-Length", 0) or 0)
            raw = self.rfile.read(length) if length else b""
            auth = self.headers.get("Authorization", "")
            entry = {
                "method": self.command,
                "path": self.path,
                "auth": auth,
                "body": json.loads(raw) if raw else None,
            }
            log.add(entry)

            if expect_token is not None and auth != f"Bearer {expect_token}":
                self.send_response(401)
                self.end_headers()
                return

            if self.command == "POST" and self.path == "/api/core/agents/start":
                self._json(201, {"runId": "run-1", "startedAt": "2026-01-01T00:00:00Z"})
                return
            if self.command == "POST" and self.path.startswith("/api/core/agents/") and self.path.endswith("/stop"):
                self._json(200, {})
                return
            self.send_response(404)
            self.end_headers()

        def _json(self, code: int, payload: dict):
            body = json.dumps(payload).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        do_GET = _handle
        do_POST = _handle

    return Handler


def _start_server(expect_token: str | None = "good-token"):
    log = _RequestLog()
    handler = _make_handler(log, expect_token)
    server = http.server.HTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, log


def _unused_port() -> int:
    """绑一个端口立刻关掉——连它会被拒得很快，不会像超时那样拖测试。"""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# ── 任务解析顺序 ─────────────────────────────────────────────────
class TaskResolutionTests(unittest.TestCase):
    def setUp(self):
        self._old_task = os.environ.pop("COCKPIT_TASK", None)

    def tearDown(self):
        if self._old_task is not None:
            os.environ["COCKPIT_TASK"] = self._old_task

    def test_explicit_wins_over_everything(self):
        os.environ["COCKPIT_TASK"] = "from-env"
        got = cc.resolve_task(explicit="from-flag", cwd="/proj/a", config={"tasks": {"/proj/a": "from-map"}})
        self.assertEqual(got, "from-flag")

    def test_env_wins_over_directory_map(self):
        os.environ["COCKPIT_TASK"] = "from-env"
        got = cc.resolve_task(cwd="/proj/a", config={"tasks": {"/proj/a": "from-map"}})
        self.assertEqual(got, "from-env")

    def test_longest_matching_prefix_wins(self):
        cfg = {"tasks": {"/proj": "shallow", "/proj/a/sub": "deep", "/proj/a": "mid"}}
        got = cc.resolve_task(cwd="/proj/a/sub/deeper", config=cfg)
        self.assertEqual(got, "deep")

    def test_sibling_prefix_does_not_false_match(self):
        # /proj/foobar 不该被 /proj/foo 判定为匹配
        cfg = {"tasks": {"/proj/foo": "wrong"}}
        got = cc.resolve_task(cwd="/proj/foobar", config=cfg)
        self.assertIsNone(got)

    def test_no_match_means_inbox(self):
        got = cc.resolve_task(cwd="/nowhere", config={"tasks": {"/proj/a": "x"}})
        self.assertIsNone(got)


# ── config 文件 + 环境变量合并 ───────────────────────────────────
class LoadConfigTests(unittest.TestCase):
    def test_env_overrides_file(self, ):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            cfg_dir = Path(d) / "honeycomb"
            cfg_dir.mkdir()
            (cfg_dir / cc.CONFIG_FILENAME).write_text(
                json.dumps({"url": "http://file-url/", "token": "file-token", "tasks": {"/x": "t"}}),
                encoding="utf-8",
            )
            old_env = {k: os.environ.get(k) for k in ("XDG_CONFIG_HOME", "COCKPIT_URL", "COCKPIT_TOKEN")}
            try:
                os.environ["XDG_CONFIG_HOME"] = d
                os.environ["COCKPIT_URL"] = "http://env-url/"
                os.environ.pop("COCKPIT_TOKEN", None)
                cfg = cc.load_config()
                self.assertEqual(cfg["url"], "http://env-url")  # 环境变量赢，且去掉了结尾斜杠
                self.assertEqual(cfg["token"], "file-token")  # 没设环境变量，落到文件
                self.assertEqual(cfg["tasks"], {"/x": "t"})
            finally:
                for k, v in old_env.items():
                    if v is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = v

    def test_missing_config_file_is_tolerated(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            old = os.environ.get("XDG_CONFIG_HOME")
            try:
                os.environ["XDG_CONFIG_HOME"] = d  # 目录存在但没有 agent-hooks.json
                cfg = cc.load_config()
                self.assertEqual(cfg["tasks"], {})
            finally:
                if old is None:
                    os.environ.pop("XDG_CONFIG_HOME", None)
                else:
                    os.environ["XDG_CONFIG_HOME"] = old


# ── HTTP 客户端：payload 形状 + Bearer 头 ────────────────────────
class ClientHttpTests(unittest.TestCase):
    def setUp(self):
        self.server, self.thread, self.log = _start_server(expect_token="good-token")
        port = self.server.server_address[1]
        self.config = {"url": f"http://127.0.0.1:{port}", "token": "good-token"}

    def tearDown(self):
        self.server.shutdown()
        self.thread.join(timeout=2)

    def test_start_run_sends_bearer_and_payload(self):
        result = cc.start_run(self.config, "task-1", "my-agent", "claude-code", model="sonnet")
        self.assertEqual(result["runId"], "run-1")
        req = self.log.requests[-1]
        self.assertEqual(req["method"], "POST")
        self.assertEqual(req["path"], "/api/core/agents/start")
        self.assertEqual(req["auth"], "Bearer good-token")
        self.assertEqual(req["body"], {"agent": "my-agent", "tool": "claude-code", "taskId": "task-1", "model": "sonnet"})

    def test_start_run_omits_task_id_for_inbox(self):
        cc.start_run(self.config, None, "my-agent", "claude-code")
        body = self.log.requests[-1]["body"]
        self.assertNotIn("taskId", body)

    def test_stop_run_sends_outcome(self):
        cc.stop_run(self.config, "run-1", "done", output="http://x/output")
        req = self.log.requests[-1]
        self.assertEqual(req["method"], "POST")
        self.assertEqual(req["path"], "/api/core/agents/run-1/stop")
        self.assertEqual(req["body"], {"outcome": "done", "output": "http://x/output"})

    def test_bad_token_raises_cockpit_error(self):
        bad_config = dict(self.config, token="wrong")
        with self.assertRaises(cc.CockpitError):
            cc.start_run(bad_config, None, "a", "t")


# ── cockpit-run：出口码映射 + cockpit 挂了也不耽误命令 ───────────
class CockpitRunIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.server, self.thread, self.log = _start_server(expect_token="good-token")
        port = self.server.server_address[1]
        self.env = dict(os.environ, COCKPIT_URL=f"http://127.0.0.1:{port}", COCKPIT_TOKEN="good-token")
        self.env.pop("COCKPIT_TASK", None)

    def tearDown(self):
        self.server.shutdown()
        self.thread.join(timeout=2)

    def _run(self, exit_code: int):
        return subprocess.run(
            [sys.executable, str(RUN_SCRIPT), "--task", "t1", "--agent", "a", "--tool", "t", "--",
             sys.executable, "-c", f"import sys; sys.exit({exit_code})"],
            env=self.env, capture_output=True, text=True, timeout=10,
        )

    def test_exit_code_zero_maps_to_done(self):
        proc = self._run(0)
        self.assertEqual(proc.returncode, 0)
        stop_req = [r for r in self.log.requests if r["path"].endswith("/stop")][-1]
        self.assertEqual(stop_req["body"]["outcome"], "done")

    def test_nonzero_exit_maps_to_failed_and_is_preserved(self):
        proc = self._run(7)
        self.assertEqual(proc.returncode, 7)
        stop_req = [r for r in self.log.requests if r["path"].endswith("/stop")][-1]
        self.assertEqual(stop_req["body"]["outcome"], "failed")

    def test_cockpit_down_command_still_runs_and_exit_code_preserved(self):
        env = dict(self.env, COCKPIT_URL=f"http://127.0.0.1:{_unused_port()}")
        proc = subprocess.run(
            [sys.executable, str(RUN_SCRIPT), "--task", "t1", "--",
             sys.executable, "-c", "import sys; sys.exit(9)"],
            env=env, capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(proc.returncode, 9)
        self.assertIn("cockpit-run", proc.stderr)

    @unittest.skipIf(os.name != "posix", "SIGTERM 语义只在 POSIX 上测")
    def test_sigterm_maps_to_cancelled(self):
        proc = subprocess.Popen(
            [sys.executable, str(RUN_SCRIPT), "--task", "t1", "--",
             sys.executable, "-c", "import time; time.sleep(30)"],
            env=self.env,
        )
        try:
            # 等 start 请求真的打到假服务器，再发 SIGTERM，避免时序竞争。
            for _ in range(50):
                if any(r["path"] == "/api/core/agents/start" for r in self.log.requests):
                    break
                threading.Event().wait(0.1)
            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=10)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
        stop_req = [r for r in self.log.requests if r["path"].endswith("/stop")][-1]
        self.assertEqual(stop_req["body"]["outcome"], "cancelled")


# ── Claude Code 钩子：状态文件往返 + 失败也 exit 0 ────────────────
class ClaudeHookTests(unittest.TestCase):
    def setUp(self):
        import tempfile

        self._tmpdir = tempfile.TemporaryDirectory()
        self._old_env = {k: os.environ.get(k) for k in ("XDG_STATE_HOME", "XDG_CONFIG_HOME")}
        # 两个都指到隔离的临时目录：既测状态文件往返，也不去碰开发机上真实的
        # ~/.config/honeycomb（万一在那台机器上恰好有配置文件，会让测试不确定）。
        os.environ["XDG_STATE_HOME"] = self._tmpdir.name
        os.environ["XDG_CONFIG_HOME"] = self._tmpdir.name

    def tearDown(self):
        for k, v in self._old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._tmpdir.cleanup()

    def test_state_round_trips_by_session_id_and_cleans_up(self):
        state = {"sess-1": "run-1", "sess-2": "run-2"}
        claude_hook._save_state(state)
        self.assertEqual(claude_hook._load_state(), state)

        # SessionEnd 消费掉 sess-1 之后应该从状态文件里清掉，sess-2 保留
        remaining = claude_hook._load_state()
        remaining.pop("sess-1")
        claude_hook._save_state(remaining)
        self.assertEqual(claude_hook._load_state(), {"sess-2": "run-2"})

    def test_missing_state_file_is_tolerated(self):
        self.assertEqual(claude_hook._load_state(), {})

    def test_session_start_then_end_round_trip_against_fake_server(self):
        server, thread, log = _start_server(expect_token="good-token")
        old_url, old_token = os.environ.get("COCKPIT_URL"), os.environ.get("COCKPIT_TOKEN")
        try:
            port = server.server_address[1]
            os.environ["COCKPIT_URL"] = f"http://127.0.0.1:{port}"
            os.environ["COCKPIT_TOKEN"] = "good-token"

            claude_hook.handle_session_start(
                {"session_id": "sess-x", "cwd": "/tmp", "hook_event_name": "SessionStart", "model": "sonnet"}
            )
            self.assertEqual(claude_hook._load_state().get("sess-x"), "run-1")

            claude_hook.handle_session_end(
                {"session_id": "sess-x", "hook_event_name": "SessionEnd", "reason": "other"}
            )
            self.assertNotIn("sess-x", claude_hook._load_state())
            stop_req = [r for r in log.requests if r["path"].endswith("/stop")][-1]
            self.assertEqual(stop_req["body"]["outcome"], "done")
            start_req = [r for r in log.requests if r["path"].endswith("/agents/start")][-1]
            self.assertEqual(start_req["body"]["model"], "sonnet")
            self.assertEqual(start_req["body"]["tool"], "claude-code")
        finally:
            server.shutdown()
            thread.join(timeout=2)
            for k, v in (("COCKPIT_URL", old_url), ("COCKPIT_TOKEN", old_token)):
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_prompt_input_exit_maps_to_cancelled(self):
        server, thread, log = _start_server(expect_token=None)
        try:
            port = server.server_address[1]
            claude_hook._save_state({"sess-z": "run-1"})
            config = {"url": f"http://127.0.0.1:{port}", "token": ""}
            import unittest.mock as mock

            with mock.patch.object(cc, "load_config", return_value=config):
                claude_hook.handle_session_end(
                    {"session_id": "sess-z", "hook_event_name": "SessionEnd", "reason": "prompt_input_exit"}
                )
            stop_req = [r for r in log.requests if r["path"].endswith("/stop")][-1]
            self.assertEqual(stop_req["body"]["outcome"], "cancelled")
        finally:
            server.shutdown()
            thread.join(timeout=2)

    def test_hook_exits_zero_even_when_cockpit_is_down(self):
        env = dict(os.environ, COCKPIT_URL=f"http://127.0.0.1:{_unused_port()}", COCKPIT_TOKEN="x")
        env["XDG_STATE_HOME"] = self._tmpdir.name
        env["XDG_CONFIG_HOME"] = self._tmpdir.name
        payload = json.dumps({"session_id": "sess-y", "cwd": "/tmp", "hook_event_name": "SessionStart"})
        proc = subprocess.run(
            [sys.executable, str(HOOK_SCRIPT)], input=payload, env=env,
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn("agent-hooks", proc.stderr)

    def test_hook_exits_zero_on_garbage_stdin(self):
        env = dict(os.environ, XDG_STATE_HOME=self._tmpdir.name, XDG_CONFIG_HOME=self._tmpdir.name)
        proc = subprocess.run(
            [sys.executable, str(HOOK_SCRIPT)], input="not json", env=env,
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(proc.returncode, 0)


if __name__ == "__main__":
    unittest.main()
