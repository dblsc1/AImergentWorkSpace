"""agent-hooks 的单测：内进程假 HTTP 服务器录请求 + 真 subprocess 跑 cockpit-run。

跑法：python3 -m pytest -q tools/agent-hooks
（也支持 `python3 -m unittest discover -s tools/agent-hooks`，纯标准库无第三方依赖）

所有测试都靠 `HONEYCOMB_AGENT_HOOKS_HOME`（见 cockpit_client.user_dir）把
config/state 目录钉在临时目录——不依赖 XDG_*（Windows/mac 上不生效），
不会碰到跑测试那台机器上真实的配置/状态文件。
"""

from __future__ import annotations

import http.server
import importlib.machinery
import importlib.util
import itertools
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import unittest.mock as mock
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import cockpit_client as cc  # noqa: E402
import claude_hook  # noqa: E402

RUN_SCRIPT = HERE / "cockpit-run"
HOOK_SCRIPT = HERE / "claude_hook.py"


def _load_cockpit_run_module():
    """`cockpit-run` 没有 .py 后缀，不能用 `import` 语句——用 importlib 按路径加载，
    这样才能直接调用脚本内部的函数（比如 `_wait_through_ctrl_c`）、
    mock 掉它引用的 `cc.start_run`/`cc.stop_run`，而不用真的开子进程发信号。
    信号时序/子进程编排这类真跑一遍很难稳定复现的边界情况，靠这个更快也更稳。
    """
    # spec_from_file_location() 靠文件扩展名猜 loader，`cockpit-run` 没有
    # .py 后缀猜不出来（返回 None）——显式给一个 SourceFileLoader。
    loader = importlib.machinery.SourceFileLoader("cockpit_run_script", str(RUN_SCRIPT))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


# ── 假 cockpit：记请求、按 token 判 401、start/stop 各回该回的形状 ──
class _RequestLog:
    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.lock = threading.Lock()

    def add(self, entry: dict) -> None:
        with self.lock:
            self.requests.append(entry)


def _make_handler(log: _RequestLog, expect_token: str | None, run_id_factory=None):
    run_id_factory = run_id_factory or (lambda: "run-1")

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
                self._json(201, {"runId": run_id_factory(), "startedAt": "2026-01-01T00:00:00Z"})
                return
            if self.command == "POST" and self.path.startswith("/api/core/agents/") and self.path.endswith("/stop"):
                self._json(200, {})
                return
            if self.command == "POST" and self.path.startswith("/api/core/agents/") and self.path.endswith("/phase"):
                self._json(200, {"applied": True})
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


def _start_server(expect_token: str | None = "good-token", unique_run_ids: bool = False):
    log = _RequestLog()
    run_id_factory = None
    if unique_run_ids:
        counter = itertools.count(1)
        lock = threading.Lock()

        def run_id_factory():
            with lock:
                return f"run-{next(counter)}"

    handler = _make_handler(log, expect_token, run_id_factory)
    server = http.server.HTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, log


def _stop_server(server, thread):
    server.shutdown()
    thread.join(timeout=2)


def _unused_port() -> int:
    """绑一个端口立刻关掉——连它会被拒得很快，不会像超时那样拖测试。"""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _start_redirect_server(target_url: str):
    """一直回 302 到 `target_url`：用来确认我们的 opener 根本不跟重定向。"""

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _handle(self):
            self.send_response(302)
            self.send_header("Location", target_url)
            self.end_headers()

        do_GET = _handle
        do_POST = _handle

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _start_fixed_response_server(status: int, body: bytes, content_type: str = "application/json"):
    """固定回一个状态码 + body，不管请求是什么——用来喂"服务端返回一个数组
    而不是对象"这种畸形响应。
    """

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _handle(self):
            length = int(self.headers.get("Content-Length", 0) or 0)
            if length:
                self.rfile.read(length)
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.end_headers()
            self.wfile.write(body)

        do_GET = _handle
        do_POST = _handle

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _start_dribble_server(body: bytes, per_byte_delay: float):
    """把 body 一个字节一个字节地吐出来，字节之间睡一下：单次 recv 都不超时，
    累计起来却远超我们想要的总时限——专门用来验证"总时限"而不是"单次 socket
    操作超时"（见 cockpit_client._run_with_deadline 的注释）。
    """

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _handle(self):
            length = int(self.headers.get("Content-Length", 0) or 0)
            if length:
                self.rfile.read(length)
            self.send_response(201)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            for b in body:
                try:
                    self.wfile.write(bytes([b]))
                    self.wfile.flush()
                except OSError:
                    return
                time.sleep(per_byte_delay)

        do_GET = _handle
        do_POST = _handle

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


class _IsolatedHomeMixin:
    """把 HONEYCOMB_AGENT_HOOKS_HOME 钉在临时目录，测试完还原。跨平台都生效
    （不像 XDG_* 只在 Linux 分支起作用）。
    """

    def setUp(self):
        super().setUp()
        self._home_tmpdir = tempfile.TemporaryDirectory()
        self._old_home = os.environ.get("HONEYCOMB_AGENT_HOOKS_HOME")
        os.environ["HONEYCOMB_AGENT_HOOKS_HOME"] = self._home_tmpdir.name
        # 心跳（伴随进程 / cockpit-run 的线程）缺省关掉：不许测试留下脱离的进程，也不让异步的心跳混进请求记录。
        # 心跳自己的测试在 test_agent_hooks_beat.py 里按需打开。
        self._old_beat = os.environ.get("COCKPIT_BEAT")
        os.environ["COCKPIT_BEAT"] = "off"

    def tearDown(self):
        if self._old_beat is None:
            os.environ.pop("COCKPIT_BEAT", None)
        else:
            os.environ["COCKPIT_BEAT"] = self._old_beat
        if self._old_home is None:
            os.environ.pop("HONEYCOMB_AGENT_HOOKS_HOME", None)
        else:
            os.environ["HONEYCOMB_AGENT_HOOKS_HOME"] = self._old_home
        self._home_tmpdir.cleanup()
        super().tearDown()

    def _subprocess_env(self, **extra) -> dict:
        env = dict(os.environ, HONEYCOMB_AGENT_HOOKS_HOME=self._home_tmpdir.name)
        env.update(extra)
        return env


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

    def test_tasks_shape_not_a_dict_is_tolerated(self):
        # 手改坏的配置（tasks 写成了列表）不该让解析炸掉，当没配处理即可（P1 #4）
        got = cc.resolve_task(cwd="/proj/a", config={"tasks": ["not", "a", "dict"]})
        self.assertIsNone(got)


# ── config 文件 + 环境变量合并 ───────────────────────────────────
class LoadConfigTests(_IsolatedHomeMixin, unittest.TestCase):
    def _write_config(self, content: str) -> None:
        cfg_dir = Path(self._home_tmpdir.name) / "config"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        (cfg_dir / cc.CONFIG_FILENAME).write_text(content, encoding="utf-8")

    def _load(self, **env):
        keys = ("COCKPIT_URL", "COCKPIT_TOKEN", "CLAUDE_PLUGIN_OPTION_COCKPIT_URL", "CLAUDE_PLUGIN_OPTION_COCKPIT_TOKEN")
        clean = {k: v for k, v in os.environ.items() if k not in keys}
        with mock.patch.dict(os.environ, {**clean, **env}, clear=True):
            return cc.load_config()

    def test_env_overrides_file(self):
        self._write_config(json.dumps({"url": "http://file-url/", "token": "file-token", "tasks": {"/x": "t"}}))
        cfg = self._load(COCKPIT_URL="http://env-url/", COCKPIT_TOKEN="env-token")
        self.assertEqual((cfg["url"], cfg["token"]), ("http://env-url", "env-token"))  # 环境变量赢，且去掉了结尾斜杠
        self.assertEqual(cfg["tasks"], {"/x": "t"})

    def test_token_only_goes_with_the_url_it_was_configured_with(self):
        """地址与令牌成对取：别的来源的令牌不跟着另一个来源的地址走（文件令牌不能发去插件设置 / 环境变量的地址）。"""
        self._write_config(json.dumps({"url": "http://file-url/", "token": "file-token"}))
        plugin_url = "CLAUDE_PLUGIN_OPTION_COCKPIT_URL"
        plugin_token = "CLAUDE_PLUGIN_OPTION_COCKPIT_TOKEN"
        cases = [
            (dict(COCKPIT_URL="http://env-url/"), ("http://env-url", "")),  # 环境变量只给地址：文件令牌不跟
            (dict(COCKPIT_TOKEN="env-token"), ("http://file-url", "file-token")),  # 只给令牌的来源：令牌不用
            (dict(**{plugin_url: "http://plugin-url/"}), ("http://plugin-url", "")),
            (dict(**{plugin_url: "http://plugin-url/", plugin_token: "plugin-token"}), ("http://plugin-url", "plugin-token")),
            (dict(**{plugin_token: "plugin-token"}), ("http://file-url", "file-token")),  # 插件只填了令牌（地址留空）
            (dict(COCKPIT_URL="http://env-url/", **{plugin_url: "http://plugin-url/", plugin_token: "plugin-token"}),
             ("http://env-url", "")),  # 先到先得：插件的令牌不跟环境变量的地址
            ({}, ("http://file-url", "file-token")),
        ]
        for env, expected in cases:
            with self.subTest(env=sorted(env)):
                cfg = self._load(**env)
                self.assertEqual((cfg["url"], cfg["token"]), expected)

    def test_plugin_manifest_has_no_default_url(self):
        manifest = json.loads((Path(__file__).parent / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        self.assertNotIn("default", manifest["userConfig"]["cockpit_url"])

    def test_missing_config_file_is_tolerated(self):
        cfg = cc.load_config()  # 临时目录里压根没有 agent-hooks.json
        self.assertEqual(cfg["tasks"], {})

    def test_top_level_not_an_object_is_tolerated(self):
        # 配置文件顶层是数组（比如手改坏了）——不该让 .get() 炸出 AttributeError（P1 #4）
        self._write_config(json.dumps(["not", "an", "object"]))
        cfg = cc.load_config()
        self.assertEqual(cfg["tasks"], {})
        self.assertEqual(cfg["url"], "")

    def test_tasks_field_not_an_object_is_tolerated(self):
        self._write_config(json.dumps({"tasks": ["nope"]}))
        cfg = cc.load_config()
        self.assertEqual(cfg["tasks"], {})


# ── HTTP 客户端：payload 形状 + Bearer 头 + 安全边界 ──────────────
class ClientHttpTests(unittest.TestCase):
    def setUp(self):
        self.server, self.thread, self.log = _start_server(expect_token="good-token")
        port = self.server.server_address[1]
        self.config = {"url": f"http://127.0.0.1:{port}", "token": "good-token"}

    def tearDown(self):
        _stop_server(self.server, self.thread)

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

    def test_no_token_sends_no_authorization_header(self):
        # 没配令牌 = 匿名上报（auth.gate v1.4）：不能带一个空的 Bearer，那是「出示了坏令牌」
        server, thread, log = _start_server(expect_token=None)
        try:
            port = server.server_address[1]
            for config in ({"url": f"http://127.0.0.1:{port}"}, {"url": f"http://127.0.0.1:{port}", "token": ""}):
                self.assertEqual(cc.start_run(config, None, "a", "t")["runId"], "run-1")
                self.assertEqual(log.requests[-1]["auth"], "")
        finally:
            _stop_server(server, thread)

    def test_bad_token_raises_cockpit_error(self):
        bad_config = dict(self.config, token="wrong")
        with self.assertRaises(cc.CockpitError):
            cc.start_run(bad_config, None, "a", "t")

    def test_server_returning_a_json_array_is_response_shape_error(self):
        # 服务端返回一个 JSON 数组而不是对象（P1 #4 举的例子之一）——不该崩，
        # 该被归类成"响应格式不对"这个固定分类（P1 #3）。
        server, thread = _start_fixed_response_server(201, b"[]")
        try:
            port = server.server_address[1]
            config = {"url": f"http://127.0.0.1:{port}", "token": "good-token"}
            with self.assertRaises(cc.CockpitError) as ctx:
                cc.start_run(config, None, "a", "t")
            self.assertEqual(str(ctx.exception), "响应格式不对")
        finally:
            _stop_server(server, thread)

    # ── P1 #1：总时限，不是单次 socket 超时 ──────────────────────
    def test_slow_dribbling_body_is_bounded_by_total_timeout(self):
        body = json.dumps({"runId": "run-1", "startedAt": "x"}).encode("utf-8")
        server, thread = _start_dribble_server(body, per_byte_delay=0.3)  # 总耗时 ~ len(body)*0.3s，远超下面的 1s 时限
        try:
            port = server.server_address[1]
            config = {"url": f"http://127.0.0.1:{port}", "token": "good-token"}
            started = time.monotonic()
            with self.assertRaises(cc.CockpitError) as ctx:
                cc.start_run(config, None, "a", "t", timeout=1.0)
            elapsed = time.monotonic() - started
            self.assertEqual(str(ctx.exception), "超时")
            self.assertLess(elapsed, 2.5, "总时限没生效，等了跟 dribble 一样久")
        finally:
            _stop_server(server, thread)

    # ── P1 #2 / P2 #4：不跟重定向，Authorization 到不了别的源，且分类
    # 必须是 "HTTP 302"（不是靠 redirect_request 返回 None 之后指望 opener
    # 里那条隐式的默认错误处理兜底链路，见 _NoRedirect 的注释）。
    def test_redirect_is_not_followed_and_other_origin_gets_nothing(self):
        target_server, target_thread, target_log = _start_server(expect_token=None)
        try:
            target_port = target_server.server_address[1]
            target_url = f"http://127.0.0.1:{target_port}/api/core/agents/start"
            redirect_server, redirect_thread = _start_redirect_server(target_url)
            try:
                port = redirect_server.server_address[1]
                config = {"url": f"http://127.0.0.1:{port}", "token": "secret-token"}
                with self.assertRaises(cc.CockpitError) as ctx:
                    cc.start_run(config, None, "a", "t")
                self.assertEqual(str(ctx.exception), "HTTP 302")
                self.assertEqual(target_log.requests, [], "重定向被跟了——Authorization 头可能到了别的源")
            finally:
                _stop_server(redirect_server, redirect_thread)
        finally:
            _stop_server(target_server, target_thread)

    # ── P1 #3：原始异常文本（可能含 token）绝不出现在错误信息里 ──
    def test_invalid_header_value_does_not_leak_token_in_error(self):
        # token 带换行是非法 header 值，http.client 会把整条 "Bearer <token>"
        # 塞进它的 ValueError 信息里——CockpitError 的消息绝不能照抄这个。
        config = dict(self.config, token="SECRET-VALUE\nBAD")
        with self.assertRaises(cc.CockpitError) as ctx:
            cc.start_run(config, None, "a", "t")
        self.assertNotIn("SECRET-VALUE", str(ctx.exception))
        self.assertEqual(str(ctx.exception), "配置错误")


# ── cockpit-run：出口码映射 + cockpit 挂了也不耽误命令 ───────────
class CockpitRunIntegrationTests(_IsolatedHomeMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.server, self.thread, self.log = _start_server(expect_token="good-token")
        port = self.server.server_address[1]
        self.env = self._subprocess_env(COCKPIT_URL=f"http://127.0.0.1:{port}", COCKPIT_TOKEN="good-token")
        self.env.pop("COCKPIT_TASK", None)

    def tearDown(self):
        _stop_server(self.server, self.thread)
        super().tearDown()

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

    # ── P1 #4：配置形状不对也不能拦住命令执行 ─────────────────────
    def test_bad_config_shape_still_runs_the_command(self):
        cfg_dir = Path(self._home_tmpdir.name) / "config"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        (cfg_dir / cc.CONFIG_FILENAME).write_text("[]", encoding="utf-8")  # 顶层是数组
        env = dict(self.env)
        env.pop("COCKPIT_URL", None)
        env.pop("COCKPIT_TOKEN", None)  # 逼 load_config 去读那份坏配置
        proc = subprocess.run(
            [sys.executable, str(RUN_SCRIPT), "--task", "t1", "--",
             sys.executable, "-c", "import sys; sys.exit(5)"],
            env=env, capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(proc.returncode, 5, f"命令没跑起来；stderr={proc.stderr!r}")

    # ── P1 #3（CLI 落地版）：token 带非法字符也不能出现在 stderr 里 ──
    def test_token_with_newline_does_not_leak_into_stderr(self):
        env = dict(self.env, COCKPIT_TOKEN="SECRET-MARKER\nBAD")
        proc = subprocess.run(
            [sys.executable, str(RUN_SCRIPT), "--task", "t1", "--",
             sys.executable, "-c", "import sys; sys.exit(0)"],
            env=env, capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(proc.returncode, 0, "命令本身必须照跑")
        self.assertNotIn("SECRET-MARKER", proc.stderr)
        self.assertNotIn("SECRET-MARKER", proc.stdout)

    @unittest.skipIf(os.name != "posix", "SIGTERM 语义只在 POSIX 上测")
    def test_sigterm_maps_to_cancelled_and_wrapper_dies_of_same_signal(self):
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
                time.sleep(0.1)
            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=10)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
        stop_req = [r for r in self.log.requests if r["path"].endswith("/stop")][-1]
        self.assertEqual(stop_req["body"]["outcome"], "cancelled")
        # P1 #8：包装进程自己也该死于同一个信号（负的 returncode），
        # 不是 sys.exit(-15) 被截断成的 241。
        self.assertEqual(proc.returncode, -signal.SIGTERM)


# ── cockpit-run 内部函数：信号时序类的边界情况，直接调更稳 ─────────
class CockpitRunUnitTests(unittest.TestCase):
    # ── P2 #2：第二次 Ctrl-C 不能从 wait 循环里逃出去 ──────────────
    def test_repeated_ctrl_c_does_not_escape_and_still_waits(self):
        module = _load_cockpit_run_module()

        class FakeProc:
            def __init__(self):
                self.calls = 0

            def wait(self):
                self.calls += 1
                if self.calls < 3:
                    raise KeyboardInterrupt()
                return 0

        proc = FakeProc()
        exit_code, interrupted = module._wait_through_ctrl_c(proc)
        self.assertEqual(exit_code, 0)
        self.assertTrue(interrupted)
        self.assertEqual(proc.calls, 3)  # 吞了两次 KeyboardInterrupt，第三次才真的等到

    # ── P3 #C：子进程已经被别处 reap 掉，wait() 抛 ChildProcessError ──
    def test_child_already_reaped_returns_known_sentinel(self):
        module = _load_cockpit_run_module()

        class FakeProc:
            def __init__(self):
                self.calls = 0

            def wait(self):
                self.calls += 1
                if self.calls == 1:
                    raise KeyboardInterrupt()
                raise ChildProcessError()

            def kill(self):
                pass

        exit_code, interrupted = module._wait_through_ctrl_c(FakeProc())
        self.assertEqual(exit_code, 130)
        self.assertTrue(interrupted)

    # ── P3 #D：子进程不理 SIGINT，第 3 次 Ctrl-C 强杀 ─────────────
    def test_third_ctrl_c_kills_unresponsive_child(self):
        module = _load_cockpit_run_module()

        class FakeProc:
            def __init__(self):
                self.wait_calls = 0
                self.killed = False

            def wait(self):
                self.wait_calls += 1
                if self.wait_calls <= 3:
                    raise KeyboardInterrupt()
                return 0  # 假装 kill() 生效之后子进程才真的退出

            def kill(self):
                self.killed = True

        proc = FakeProc()
        exit_code, interrupted = module._wait_through_ctrl_c(proc)
        self.assertTrue(proc.killed)
        self.assertEqual(exit_code, 0)
        self.assertTrue(interrupted)
        self.assertEqual(proc.wait_calls, 4)  # 3 次 Ctrl-C + kill() 之后第 4 次才等到

    # ── P3 #E：上报 stop 那几秒内又被 Ctrl-C 一次，不能逃出 main() ──
    def test_ctrl_c_during_stop_reporting_does_not_escape_or_replace_exit_code(self):
        module = _load_cockpit_run_module()
        with mock.patch.object(module.cc, "load_config", return_value={"url": "http://x", "token": "t", "tasks": {}}), \
             mock.patch.object(module.cc, "start_run", return_value={"runId": "run-1"}), \
             mock.patch.object(module.cc, "stop_run", side_effect=KeyboardInterrupt()):
            exit_code = module.main(["--task", "t1", "--", sys.executable, "-c", "import sys; sys.exit(0)"])
        self.assertEqual(exit_code, 0)

    # ── P2 #3：stop 上报炸了不能顶替真实退出码 ─────────────────────
    def test_stop_reporting_exception_does_not_replace_exit_code(self):
        module = _load_cockpit_run_module()
        with mock.patch.object(module.cc, "load_config", return_value={"url": "http://x", "token": "t", "tasks": {}}), \
             mock.patch.object(module.cc, "start_run", return_value={"runId": "run-1"}), \
             mock.patch.object(module.cc, "stop_run", side_effect=RuntimeError("boom")):
            exit_code = module.main(["--task", "t1", "--", sys.executable, "-c", "import sys; sys.exit(0)"])
        # 命令本身退出码是 0；stop_run 炸出一个 RuntimeError（不是 CockpitError）
        # 不该逃出 main() 变成一次 Python 崩溃（那样 sys.exit(main()) 拿到的就不是
        # 0，而是未处理异常的 traceback + exit 1）。
        self.assertEqual(exit_code, 0)


# ── Claude Code 钩子：状态文件往返 + 失败也 exit 0 ────────────────
class ClaudeHookTests(_IsolatedHomeMixin, unittest.TestCase):
    def test_state_round_trips_by_session_id_and_cleans_up(self):
        claude_hook._save_run_id("sess-1", "run-1")
        claude_hook._save_run_id("sess-2", "run-2")

        # _read_run_id 是只读的，不删（P2 #1：删不删是 handle_session_end 自己
        # 根据 stop 成不成来决定的，不是读的时候顺手删）
        self.assertEqual(claude_hook._read_run_id("sess-1"), "run-1")
        self.assertEqual(claude_hook._read_run_id("sess-1"), "run-1")

        claude_hook._delete_run_id("sess-1")
        self.assertIsNone(claude_hook._read_run_id("sess-1"))
        # 另一个会话不受影响
        self.assertEqual(claude_hook._read_run_id("sess-2"), "run-2")

    def test_missing_state_file_is_tolerated(self):
        self.assertIsNone(claude_hook._read_run_id("nope"))
        claude_hook._delete_run_id("nope")  # 删一个不存在的也不该炸

    # ── P3 #B：文件不存在 ≠ 文件读不出可用内容——后者是垃圾，读的时候顺手清掉 ──
    def test_unparseable_state_file_is_deleted_not_left_as_litter(self):
        path = claude_hook._state_file("sess-corrupt")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("not json at all", encoding="utf-8")

        self.assertIsNone(claude_hook._read_run_id("sess-corrupt"))
        self.assertFalse(path.exists(), "内容不是合法 JSON 的状态文件该被清掉，不然一直是垃圾")

    def test_state_file_with_wrong_shape_is_also_deleted(self):
        path = claude_hook._state_file("sess-wrong-shape")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"runId": 12345}), encoding="utf-8")  # runId 不是字符串

        self.assertIsNone(claude_hook._read_run_id("sess-wrong-shape"))
        self.assertFalse(path.exists())

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
            # 只读，不删——记录还在，handle_session_end 待会还要用
            self.assertEqual(claude_hook._read_run_id("sess-x"), "run-1")

            claude_hook.handle_session_end(
                {"session_id": "sess-x", "hook_event_name": "SessionEnd", "reason": "other"}
            )
            # stop 报成功了，记录该被清掉
            self.assertIsNone(claude_hook._read_run_id("sess-x"))
            stop_req = [r for r in log.requests if r["path"].endswith("/stop")][-1]
            self.assertEqual(stop_req["body"]["outcome"], "done")
            start_req = [r for r in log.requests if r["path"].endswith("/agents/start")][-1]
            self.assertEqual(start_req["body"]["model"], "sonnet")
            self.assertEqual(start_req["body"]["tool"], "claude-code")
        finally:
            _stop_server(server, thread)
            for k, v in (("COCKPIT_URL", old_url), ("COCKPIT_TOKEN", old_token)):
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_prompt_input_exit_maps_to_cancelled(self):
        server, thread, log = _start_server(expect_token=None)
        try:
            port = server.server_address[1]
            claude_hook._save_run_id("sess-z", "run-1")
            config = {"url": f"http://127.0.0.1:{port}", "token": ""}
            with mock.patch.object(cc, "load_config", return_value=config):
                claude_hook.handle_session_end(
                    {"session_id": "sess-z", "hook_event_name": "SessionEnd", "reason": "prompt_input_exit"}
                )
            stop_req = [r for r in log.requests if r["path"].endswith("/stop")][-1]
            self.assertEqual(stop_req["body"]["outcome"], "cancelled")
            self.assertIsNone(claude_hook._read_run_id("sess-z"))  # stop 成功了，记录清掉
        finally:
            _stop_server(server, thread)

    # ── P2 #1：stop 没成功之前不能先删本地记录，不然这条 run 永远关不掉 ──
    def test_stop_failure_keeps_state_file_for_later_retry(self):
        claude_hook._save_run_id("sess-w", "run-1")
        bad_config = {"url": f"http://127.0.0.1:{_unused_port()}", "token": "x", "tasks": {}}
        with mock.patch.object(cc, "load_config", return_value=bad_config):
            claude_hook.handle_session_end(
                {"session_id": "sess-w", "hook_event_name": "SessionEnd", "reason": "other"}
            )
        # cockpit 连不上：本地记录必须还在，删了就真丢了（只能等服务端自己的
        # 兜底超时才会关掉这条 run）。
        self.assertEqual(claude_hook._read_run_id("sess-w"), "run-1")

    def test_stop_404_with_json_body_is_treated_as_definitively_gone(self):
        # nexus-core 应用层的 404：JSON 错误体——才是"cockpit 明确说这条 run 不存在了"。
        server, thread = _start_fixed_response_server(404, b'{"detail": "run not found"}')
        try:
            claude_hook._save_run_id("sess-v", "run-1")
            config = {"url": f"http://127.0.0.1:{server.server_address[1]}", "token": "x", "tasks": {}}
            with mock.patch.object(cc, "load_config", return_value=config):
                claude_hook.handle_session_end(
                    {"session_id": "sess-v", "hook_event_name": "SessionEnd", "reason": "other"}
                )
            self.assertIsNone(claude_hook._read_run_id("sess-v"))
        finally:
            _stop_server(server, thread)

    # ── P3 #A：404 但响应体不是 JSON（网关/nginx 的默认 404 页，比如
    # COCKPIT_URL 配错了指到了别的服务）不能当"确定丢弃"处理——不然一次
    # 配置错误就会把所有还开着的 run 的本地记录全部冲掉。
    def test_stop_404_with_html_body_keeps_state_file(self):
        server, thread = _start_fixed_response_server(404, b"<html>not found</html>", content_type="text/html")
        try:
            claude_hook._save_run_id("sess-u", "run-1")
            config = {"url": f"http://127.0.0.1:{server.server_address[1]}", "token": "x", "tasks": {}}
            with mock.patch.object(cc, "load_config", return_value=config):
                claude_hook.handle_session_end(
                    {"session_id": "sess-u", "hook_event_name": "SessionEnd", "reason": "other"}
                )
            self.assertEqual(claude_hook._read_run_id("sess-u"), "run-1")
        finally:
            _stop_server(server, thread)

    # ── P1 #5：两个会话交错也不会互相覆盖对方的 runId ─────────────
    def test_two_concurrent_sessions_keep_independent_run_ids(self):
        server, thread, log = _start_server(expect_token="good-token", unique_run_ids=True)
        old_url, old_token = os.environ.get("COCKPIT_URL"), os.environ.get("COCKPIT_TOKEN")
        try:
            os.environ["COCKPIT_URL"] = f"http://127.0.0.1:{server.server_address[1]}"
            os.environ["COCKPIT_TOKEN"] = "good-token"

            # 交错：a 开始 → b 开始 → a 结束 → b 结束
            claude_hook.handle_session_start({"session_id": "sess-a", "cwd": "/tmp", "hook_event_name": "SessionStart"})
            claude_hook.handle_session_start({"session_id": "sess-b", "cwd": "/tmp", "hook_event_name": "SessionStart"})
            claude_hook.handle_session_end({"session_id": "sess-a", "hook_event_name": "SessionEnd", "reason": "other"})
            claude_hook.handle_session_end({"session_id": "sess-b", "hook_event_name": "SessionEnd", "reason": "other"})

            stop_paths = sorted(r["path"] for r in log.requests if r["path"].endswith("/stop"))
            self.assertEqual(len(stop_paths), 2)
            # 两条 stop 各自带着自己那条 run 的 id——旧的共享文件实现在交错场景下
            # 会让后写的覆盖先写的，两次 stop 会指向同一个 runId。
            self.assertNotEqual(stop_paths[0], stop_paths[1])
        finally:
            _stop_server(server, thread)
            for k, v in (("COCKPIT_URL", old_url), ("COCKPIT_TOKEN", old_token)):
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_hook_exits_zero_even_when_cockpit_is_down(self):
        env = self._subprocess_env(COCKPIT_URL=f"http://127.0.0.1:{_unused_port()}", COCKPIT_TOKEN="x")
        payload = json.dumps({"session_id": "sess-y", "cwd": "/tmp", "hook_event_name": "SessionStart"})
        proc = subprocess.run(
            [sys.executable, str(HOOK_SCRIPT)], input=payload, env=env,
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn("agent-hooks", proc.stderr)

    def test_hook_exits_zero_on_garbage_stdin(self):
        env = self._subprocess_env()
        proc = subprocess.run(
            [sys.executable, str(HOOK_SCRIPT)], input="not json", env=env,
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(proc.returncode, 0)


if __name__ == "__main__":
    unittest.main()
