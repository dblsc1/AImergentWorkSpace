"""简化轮（PR #96 第四轮评审）的回归测试：锁或者什么都不做、一个临界区、静态心跳方式（没有 auto / standby）、
一个总预算、卡住是终态、状态目录符号链接、`--doctor` 只读。

跑法同 test_agent_hooks.py。真子进程的地方（钩子进程、伴随进程、monitor 进程）都在 finally / cleanup 里杀干净。
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import unittest
import unittest.mock as mock
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import claude_hook  # noqa: E402
import cockpit_client as cc  # noqa: E402
from test_agent_hooks import _IsolatedHomeMixin, _start_server, _stop_server  # noqa: E402
from test_agent_hooks_beat import CLI, _BeatMixin, _Clock  # noqa: E402

POSIX = unittest.skipIf(os.name == "nt", "POSIX 的事")


class _HangingServer:
    """接受连接、读掉请求、永远不回。"""

    def __init__(self):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(16)
        self.conns: list = []
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.sock.getsockname()[1]}"

    def _serve(self):
        while True:
            try:
                self.conns.append(self.sock.accept()[0])
            except OSError:
                return

    def close(self):
        self.sock.close()
        for c in self.conns:
            with contextlib.suppress(OSError):
                c.close()


class _HookProcMixin(_IsolatedHomeMixin):
    """把钩子当真进程跑（和 Claude Code 一样：stdin 一份 JSON）。"""

    def _hook_env(self, **extra):
        env = self._subprocess_env()
        for key in ("COCKPIT_BEAT", "COCKPIT_URL", "COCKPIT_TOKEN", "COCKPIT_CLI_PID", "CLAUDE_PLUGIN_ROOT"):
            env.pop(key, None)
        env.update(extra)
        return env

    def _run_hook(self, event, script=None, timeout=20, **env):
        t0 = time.monotonic()
        proc = subprocess.run([sys.executable, str(script or HERE / "claude_hook.py")], input=json.dumps(event), text=True,
                              capture_output=True, env=self._hook_env(**env), timeout=timeout)
        return proc, time.monotonic() - t0


# ── 1：锁或者什么都不做 ─────────────────────────────────────────────────────────────────────────
@POSIX
class LockOrNothingProcessTests(_HookProcMixin, unittest.TestCase):
    def test_session_start_with_the_lock_held_by_another_process_sends_no_start_and_writes_no_state(self):
        server, thread, log = _start_server(expect_token=None)
        self.addCleanup(_stop_server, server, thread)
        holder = subprocess.Popen(
            [sys.executable, "-c",
             "import sys; sys.path.insert(0, %r); import claude_hook as h\n"
             "with h._session_lock('s1') as held:\n    print('held' if held else 'no', flush=True); sys.stdin.read()" % str(HERE)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, env=self._hook_env())
        try:
            self.assertEqual(holder.stdout.readline().strip(), "held")  # 另一个进程持着会话锁
            proc, took = self._run_hook({"hook_event_name": "SessionStart", "session_id": "s1", "cwd": "/home/u/garden"},
                                        COCKPIT_URL=f"http://127.0.0.1:{server.server_address[1]}")
        finally:
            holder.stdin.close()
            holder.wait(10)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(log.requests, [])  # 一个请求都没有：连 /start 都不发
        self.assertFalse(claude_hook._state_file("s1").exists())
        self.assertLessEqual(len(proc.stderr.strip().splitlines()), 1)
        self.assertLess(took, 3.5)


# ── 2：静态心跳方式：缺省伴随进程（resume 后恰好一个且接着发）；monitor 只在显式时 ────────────────────
@POSIX
@unittest.skipUnless(Path("/proc/self/stat").exists(), "要 /proc")
class StaticBeaterTests(_HookProcMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.server, self.thread, self.log = _start_server(expect_token=None)
        self.addCleanup(_stop_server, self.server, self.thread)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.clis: list[subprocess.Popen] = []
        self.addCleanup(self._reap)
        # 钩子脚本的副本，把「看一眼 CLI 的间隔」从 30 秒改成 1 秒（伴随进程跑的是这份副本）
        self.copy_dir = Path(self._home_tmpdir.name) / "hookcopy"
        self.copy_dir.mkdir()
        for name in ("cockpit_client.py", "claude_hook.py"):
            text = (HERE / name).read_text(encoding="utf-8").replace("BEAT_CHECK_SECONDS = 30", "BEAT_CHECK_SECONDS = 1")
            (self.copy_dir / name).write_text(text, encoding="utf-8")
        self.script = self.copy_dir / "claude_hook.py"

    def _fake_cli(self):
        p = subprocess.Popen(["sleep", "600"])
        self.clis.append(p)
        return p

    def _beaters(self):
        out = []
        for d in Path("/proc").iterdir():
            if d.name.isdigit():
                with contextlib.suppress(OSError):
                    argv = (d / "cmdline").read_bytes().split(b"\0")
                    if str(self.script).encode() in argv and b"--beat" in argv:
                        out.append(int(d.name))
        return out

    def _reap(self):
        for p in self.clis:
            with contextlib.suppress(OSError):
                p.kill()
            p.wait()
        deadline = time.monotonic() + 15
        while self._beaters() and time.monotonic() < deadline:
            time.sleep(0.1)
        for pid in self._beaters():
            with contextlib.suppress(OSError):
                os.kill(pid, signal.SIGKILL)

    def _heartbeats(self):
        return [r for r in self.log.requests if r["path"].endswith("/heartbeat")]

    def _wait(self, cond, what, limit=20):
        deadline = time.monotonic() + limit
        while time.monotonic() < deadline:
            if cond():
                return
            time.sleep(0.05)
        self.fail(what)

    def test_resume_from_another_cli_in_plugin_env_leaves_exactly_one_beater_that_keeps_beating(self):
        """回归（评审 C）：auto + 插件时，CLI#2 的 --standby 抢不到 .standby 就退，老的又因归属变了退出 → 零个发心跳的。"""
        event = {"hook_event_name": "SessionStart", "session_id": "s1", "cwd": "/home/u/garden"}
        env = dict(COCKPIT_URL=self.url, CLAUDE_PLUGIN_ROOT=str(HERE))  # 装成插件、没有 monitor 在跑、缺省心跳方式
        cli1, cli2 = self._fake_cli(), self._fake_cli()
        proc, _ = self._run_hook(event, self.script, COCKPIT_CLI_PID=str(cli1.pid), **env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        beat = claude_hook._state_file("s1").with_suffix(".beat")
        self._wait(lambda: beat.exists() and beat.read_text().endswith(" companion"), "CLI#1 的伴随进程没起来")
        first = int(beat.read_text().split()[0])
        self._wait(lambda: len(self._heartbeats()) >= 1, "第一下心跳没到")
        proc, _ = self._run_hook({**event, "source": "resume"}, self.script, COCKPIT_CLI_PID=str(cli2.pid), **env)  # CLI#2 恢复
        self.assertEqual(proc.returncode, 0, proc.stderr)
        before = len(self._heartbeats())
        self._wait(lambda: len(self._heartbeats()) > before, "CLI#2 之后没有任何心跳（没有钩子事件再来）")  # 新的伴随进程接手、不靠以后的事件
        self._wait(lambda: len(self._beaters()) == 1, "发心跳的进程不是恰好一个: %s" % self._beaters())
        second = int(beat.read_text().split()[0])
        self.assertNotEqual(first, second)  # 是 CLI#2 的新进程，不是老的
        self.assertEqual(self._beaters(), [second])
        self.assertTrue(all(r["body"] == {"beatSource": "companion"} for r in self._heartbeats()))

    def test_monitor_mode_spawns_no_companion_and_the_monitor_beats(self):
        cli = self._fake_cli()
        proc, _ = self._run_hook({"hook_event_name": "SessionStart", "session_id": "s1", "cwd": "/home/u/garden"}, self.script,
                                 COCKPIT_URL=self.url, COCKPIT_BEAT="monitor", COCKPIT_CLI_PID=str(cli.pid))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        time.sleep(0.5)
        self.assertEqual(self._beaters(), [])  # 钩子没起伴随进程
        self.assertFalse(claude_hook._state_file("s1").with_suffix(".beat").exists())
        monitor = subprocess.Popen([sys.executable, str(self.script), "--beat", "--source", "monitor"], stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, env=self._hook_env(COCKPIT_URL=self.url, COCKPIT_BEAT="monitor",
                                                                             COCKPIT_CLI_PID=str(cli.pid)))
        try:
            self._wait(lambda: len(self._heartbeats()) >= 1, "monitor 没有发心跳")
        finally:
            monitor.kill()
        self.assertEqual(monitor.communicate(timeout=10), (b"", b""))
        self.assertEqual(self._heartbeats()[0]["body"], {"beatSource": "monitor"})

    def test_monitor_command_exits_silently_and_sends_nothing_unless_beat_is_monitor(self):
        cli = self._fake_cli()
        with mock.patch.object(claude_hook, "_cli_pid", return_value=cli.pid):
            claude_hook._save_run_id("s1", "run-1", "idle", "garden", {"cwd": "/tmp"})
        for beat in (None, "companion", "off"):  # 缺省也一样：不是显式 monitor 就退
            extra = {"COCKPIT_BEAT": beat} if beat else {}
            proc = subprocess.run([sys.executable, "-W", "always", str(self.script), "--beat", "--source", "monitor"], capture_output=True,
                                  timeout=20, env=self._hook_env(COCKPIT_URL=self.url, COCKPIT_CLI_PID=str(cli.pid), **extra))
            self.assertEqual((proc.returncode, proc.stdout, proc.stderr), (0, b"", b""), beat)
        self.assertEqual(self.log.requests, [])


# ── 3：一个总预算；输入文件不阻塞 ─────────────────────────────────────────────────────────────────
@POSIX
class BudgetTests(_HookProcMixin, unittest.TestCase):
    def test_whole_hook_wall_time_is_bounded_under_a_hanging_server_and_a_held_lock(self):
        hanging = _HangingServer()
        self.addCleanup(hanging.close)
        with claude_hook._session_lock("s1"):  # 本进程持着会话锁，钩子进程拿不到
            for event in ("SessionStart", "SessionEnd", "Stop", "UserPromptSubmit"):
                proc, took = self._run_hook({"hook_event_name": event, "session_id": "s1", "cwd": "/home/u/garden"}, COCKPIT_URL=hanging.url)
                self.assertEqual(proc.returncode, 0, event)
                self.assertLess(took, 3.5, event)
        # 锁没人占、服务端挂着：每步 1 秒请求，总预算 3 秒封顶
        claude_hook._write_state("s1", {"runId": "run-1", "lastPhase": "idle", "label": "garden"})
        for event in ("SessionStart", "SessionEnd", "UserPromptSubmit"):
            proc, took = self._run_hook({"hook_event_name": event, "session_id": "s1", "cwd": "/home/u/garden"}, COCKPIT_URL=hanging.url)
            self.assertEqual(proc.returncode, 0, event)
            self.assertLess(took, 3.5, event)

    def test_budget_is_shared_by_lock_wait_and_requests(self):
        with mock.patch.object(claude_hook, "_deadline", time.monotonic() + 0.3):
            self.assertLessEqual(claude_hook._remaining(2.0), 0.3)
            t0 = time.monotonic()
            with claude_hook._session_lock("s1"):
                with claude_hook._session_lock("s1", wait=2.0) as held:  # 想等 2 秒，预算只剩 0.3
                    self.assertFalse(held)
            self.assertLess(time.monotonic() - t0, 1.0)
        with mock.patch.object(claude_hook, "_deadline", time.monotonic() - 1):
            with self.assertRaises(cc.CockpitError):  # 预算用完：请求不发，按「超时」跳过
                claude_hook._http_timeout()
            self.assertIsNone(claude_hook._proc(2 ** 22 + 99))  # 且不再起 ps

    def test_cli_ancestry_is_discovered_once_per_session_start(self):
        calls = []
        with mock.patch.object(claude_hook, "_cli_pid", lambda: calls.append(1) or 0), \
                mock.patch.object(cc, "start_run", return_value={"runId": "run-1"}), \
                mock.patch.object(cc, "load_config", return_value={"url": "http://x.invalid", "token": "", "beat": None}):
            claude_hook.handle_session_start({"session_id": "s1", "cwd": "/tmp"})
        self.assertEqual(len(calls), 1)  # 状态里的 CLI 身份、没监督的提示、起伴随进程共用一次查询

    def test_fifo_as_config_or_transcript_does_not_hang_the_hook(self):
        server, thread, log = _start_server(expect_token=None)
        self.addCleanup(_stop_server, server, thread)
        cfg = cc.user_dir("config") / cc.CONFIG_FILENAME
        cfg.parent.mkdir(parents=True)
        os.mkfifo(cfg)
        fifo = Path(self._home_tmpdir.name) / "transcript.jsonl"
        os.mkfifo(fifo)
        t0 = time.monotonic()
        self.assertEqual(cc.load_config()["url"], "")
        self.assertIsNone(cc.session_title(str(fifo)))
        self.assertLess(time.monotonic() - t0, 1)
        proc, took = self._run_hook({"hook_event_name": "SessionStart", "session_id": "s1", "cwd": "/home/u/garden",
                                     "transcript_path": str(fifo)}, COCKPIT_URL=f"http://127.0.0.1:{server.server_address[1]}")
        self.assertEqual(proc.returncode, 0)
        self.assertLess(took, 3.5)
        [start] = [r for r in log.requests if r["path"].endswith("/start")]
        self.assertEqual(start["body"]["label"], "garden")  # FIFO 当没有标题

    def test_symlinked_config_and_oversized_config_are_ignored(self):
        cfg = cc.user_dir("config") / cc.CONFIG_FILENAME
        cfg.parent.mkdir(parents=True)
        real = Path(self._home_tmpdir.name) / "real.json"
        real.write_text(json.dumps({"url": "http://file.invalid"}))
        cfg.symlink_to(real)
        self.assertEqual(cc.load_config()["url"], "")
        cfg.unlink()
        cfg.write_text(json.dumps({"url": "http://file.invalid", "pad": "x" * cc.CONFIG_MAX_BYTES}))
        self.assertEqual(cc.load_config()["url"], "")
        cfg.write_text(json.dumps({"url": "http://file.invalid"}))
        self.assertEqual(cc.load_config()["url"], "http://file.invalid")


# ── 4：卡住是终态 ─────────────────────────────────────────────────────────────────────────────────
class StuckIsTerminalTests(_BeatMixin, unittest.TestCase):
    def _stuck(self):
        return mock.patch.object(cc, "stuck_requests", return_value=1)

    def test_companion_and_monitor_both_return_so_the_process_exits(self):
        for source in ("companion", "monitor"):
            with self.subTest(source=source):
                self.SOURCE = source
                self.calls.clear()
                claude_hook._delete_run_id("s1")
                self._save("s1", "run-1")
                clock = _Clock(max_sleeps=200)
                with self._stuck():
                    self.assertTrue(self._loop(clock))  # 自己退出（不是被测试掐断）；monitor 以前会回到外圈、重置计数继续转
                self.assertLessEqual(clock.sleeps, claude_hook.BEAT_MAX_STUCK_SKIPS + 2)
                self.assertEqual(self.calls, [])  # 卡着的时候一下都没叠

    def test_beat_session_reports_a_distinct_stuck_result(self):
        self._save("s1", "run-1")
        clock = _Clock(max_sleeps=200)
        with self._stuck():
            result = claude_hook._beat_session("s1", "companion", CLI, "born-1", [1000], clock.sleep, clock.clock)
        self.assertEqual(result, claude_hook.END_STUCK)


# ── 5：状态目录是符号链接 → 拒绝，目标原样 ─────────────────────────────────────────────────────────
@POSIX
class SymlinkedStateDirTests(_IsolatedHomeMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.target = Path(self._home_tmpdir.name) / "elsewhere"
        self.target.mkdir()
        os.chmod(self.target, 0o775)
        state = cc.user_dir("state")
        state.parent.mkdir(parents=True, exist_ok=True)
        state.symlink_to(self.target)

    def test_ensure_dir_refuses_the_link_and_does_not_chmod_the_target(self):
        with self.assertRaises(OSError):
            claude_hook._ensure_dir(cc.user_dir("state"))
        self.assertEqual(self.target.stat().st_mode & 0o777, 0o775)
        self.assertEqual(list(self.target.iterdir()), [])

    def test_session_start_sends_no_start_warns_once_and_leaves_the_target_alone(self):
        sent = []
        with mock.patch.object(cc, "start_run", lambda *a, **k: sent.append(1) or {"runId": "run-1"}), \
                mock.patch.object(cc, "load_config", return_value={"url": "http://x.invalid", "token": ""}):
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                claude_hook.handle_session_start({"session_id": "s1", "cwd": "/tmp"})
        self.assertEqual(sent, [])
        self.assertEqual(len(err.getvalue().strip().splitlines()), 1)
        self.assertEqual(self.target.stat().st_mode & 0o777, 0o775)
        self.assertEqual(list(self.target.iterdir()), [])


# ── 6：--doctor 只读 ───────────────────────────────────────────────────────────────────────────────
def _snapshot(root: Path):
    out = {}
    for p in sorted([root, *root.rglob("*")]):
        st = os.lstat(p)
        out[str(p.relative_to(root))] = (st.st_mode, st.st_mtime_ns, st.st_size)
    return out


@POSIX
class DoctorReadOnlyTests(_HookProcMixin, unittest.TestCase):
    def _doctor(self):
        proc = subprocess.run([sys.executable, str(HERE / "claude_hook.py"), "--doctor"], capture_output=True, text=True, timeout=30,
                              env=self._hook_env(COCKPIT_URL="http://127.0.0.1:1"))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc.stdout

    def test_doctor_on_an_empty_home_creates_nothing(self):
        before = _snapshot(Path(self._home_tmpdir.name))
        out = self._doctor()
        self.assertEqual(_snapshot(Path(self._home_tmpdir.name)), before)
        self.assertIn("还没有", out)  # 报「还没有」，不是替你建出来

    def test_doctor_on_a_populated_home_changes_no_name_mode_or_mtime(self):
        state = cc.user_dir("state")
        state.mkdir(parents=True)
        os.chmod(state, 0o775)  # 老版本留下的：钩子会修，doctor 不修
        claude_hook._write_state("s1", {"runId": "run-1", "session": "s1"})
        os.chmod(state, 0o775)
        (state / claude_hook._state_file("s1").with_suffix(".beat").name).write_text("99999 companion")
        before = _snapshot(Path(self._home_tmpdir.name))
        time.sleep(0.05)
        out = self._doctor()
        self.assertEqual(_snapshot(Path(self._home_tmpdir.name)), before)
        self.assertEqual(state.stat().st_mode & 0o777, 0o775)
        self.assertIn("99999 companion", out)
        self.assertNotIn(".lock", " ".join(p.name for p in state.iterdir()))


if __name__ == "__main__":
    unittest.main()
