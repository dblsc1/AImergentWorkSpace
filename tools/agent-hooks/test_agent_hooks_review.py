"""PR #96 评审意见的回归测试（心跳适配器的第二轮加固）。跑法同 test_agent_hooks.py。

每条都是「先红后绿」：对应的修复去掉就会失败。时钟 / sleep / 进程表全是注入的假货（沿用 test_agent_hooks_beat 的 `_BeatMixin`），
只有「真进程」那一条真起子进程，并在 finally 里全部杀掉。
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import signal
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
from test_agent_hooks import (  # noqa: E402
    _IsolatedHomeMixin,
    _start_dribble_server,
    _start_server,
    _stop_server,
)
from test_agent_hooks_beat import CLI, _BeatMixin, _Clock, _Done  # noqa: E402


def _run_hook(fn, *args, **kw):
    """跑 fn，返回 (结果, stderr 文本)。"""
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        result = fn(*args, **kw)
    return result, err.getvalue()


@contextlib.contextmanager
def _no_lock(*_a, **_k):
    yield False  # 会话锁拿不到


# ── 1：本人的 0775 状态目录（v0.3 在 umask 002 下建的）要修好继续用 ─────────────────────────────
@unittest.skipIf(os.name == "nt", "POSIX 权限位")
class StateDirUpgradeTests(_IsolatedHomeMixin, unittest.TestCase):
    EVENT = {"session_id": "s1", "cwd": "/home/u/garden"}

    def _fakes(self):
        self.sent = []
        cfg = {"url": "http://cockpit.invalid", "token": "t", "beat": None}
        for target, attr, fake in (
            (cc, "load_config", lambda: cfg),
            (cc, "start_run", lambda *_a, **_k: self.sent.append("start") or {"runId": "run-1"}),
            (cc, "phase_run", lambda *_a, **_k: self.sent.append("phase") or {"applied": True}),
            (cc, "stop_run", lambda *_a, **_k: self.sent.append("stop") or {}),
        ):
            p = mock.patch.object(target, attr, fake)
            p.start()
            self.addCleanup(p.stop)

    def test_own_0775_dir_is_repaired_and_phases_and_stop_are_sent(self):
        self._fakes()
        state_dir = cc.user_dir("state")
        state_dir.mkdir(parents=True)
        os.chmod(state_dir, 0o775)
        _, err = _run_hook(claude_hook.handle_session_start, self.EVENT)
        claude_hook.handle_phase({**self.EVENT, "hook_event_name": "UserPromptSubmit"}, cc.now_iso())
        claude_hook.handle_session_end({**self.EVENT, "hook_event_name": "SessionEnd"})
        self.assertEqual(self.sent, ["start", "phase", "stop"])  # 以前只有 start：之后全被静默吞掉
        self.assertEqual(err, "")
        self.assertEqual(state_dir.stat().st_mode & 0o777, 0o700)

    def test_foreign_or_unrepairable_dir_sends_no_start_and_warns_once(self):
        self._fakes()
        state_dir = cc.user_dir("state")
        state_dir.mkdir(parents=True)
        os.chmod(state_dir, 0o775)
        for patch in (mock.patch.object(os, "fchmod", side_effect=PermissionError),  # 改不了
                      mock.patch.object(os, "getuid", return_value=os.getuid() + 1)):  # 别人的
            with patch:
                _, err = _run_hook(claude_hook.handle_session_start, self.EVENT)
            self.assertEqual(self.sent, [])  # 没有孤儿 run
            self.assertEqual(len(err.strip().splitlines()), 1)
            self.assertIn("chmod 700", err)  # 一行里说清哪里不对、怎么修
            self.assertIn(str(state_dir), err)


# ── 2：CLI 死了，发心跳的（monitor / 伴随进程）要报 stop（真进程）─────────────────────────────────
@unittest.skipUnless(Path("/proc/self/stat").exists(), "要 /proc")
class DeadCliRealProcessTests(_IsolatedHomeMixin, unittest.TestCase):
    # 「假 Claude Code」：一个 python 进程，等 go 文件出现后把里面的代码当子进程跑（发心跳的进程于是是它的子进程），再睡着。
    # 被 kill -9 之后，发心跳的进程成了孤儿，祖先里再没有 CLI——和真的 Claude Code 崩掉一样。
    FAKE_CLI = (
        "import os, subprocess, sys, time\n"
        "while not os.path.exists(sys.argv[1]): time.sleep(0.05)\n"
        "subprocess.Popen([sys.executable, '-c', open(sys.argv[1]).read()], stdin=subprocess.DEVNULL,"
        " stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
        "time.sleep(600)\n"
    )

    def _check(self, mode):
        server, thread, log = _start_server(expect_token=None)
        self.addCleanup(_stop_server, server, thread)
        os.environ["COCKPIT_URL"] = f"http://127.0.0.1:{server.server_address[1]}"
        self.addCleanup(os.environ.pop, "COCKPIT_URL", None)
        os.environ["COCKPIT_BEAT"] = mode
        go = Path(self._home_tmpdir.name) / "go"
        fake = subprocess.Popen([sys.executable, "-c", self.FAKE_CLI, str(go)], env=self._subprocess_env(COCKPIT_CLI_NAMES="python"))
        driver = None
        try:
            with mock.patch.object(claude_hook, "_cli_pid", return_value=fake.pid):
                claude_hook._save_run_id("s1", "run-1", "idle", "garden", {"cwd": "/tmp"})
            state = claude_hook._read_state("s1")
            start = f"import sys; sys.path.insert(0, {str(HERE)!r}); import claude_hook as h; h.BEAT_CHECK_SECONDS = 1; "
            go.write_text(start + ("h._beat_main(['--beat', '--source', 'monitor'])" if mode == "monitor" else
                                   f"h.beat_loop('companion', 's1', {fake.pid}, born={claude_hook._born(fake.pid)!r}, gen={state['gen']!r})"))
            beat = claude_hook._state_file("s1").with_suffix(".beat")
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline and not (beat.exists() and beat.read_text().endswith(f" {mode}")):
                time.sleep(0.05)
            self.assertTrue(beat.read_text().endswith(f" {mode}"), "发心跳的进程没起来")
            driver = int(beat.read_text().split()[0])
            fake.kill()  # kill -9 Claude Code：没有 SessionEnd
            fake.wait()
            stops = lambda: [r for r in log.requests if r["path"].endswith("/stop")]  # noqa: E731
            deadline = time.monotonic() + 25
            while time.monotonic() < deadline and not stops():
                time.sleep(0.1)
            self.assertEqual([r["body"] for r in stops()], [{"outcome": "cancelled"}])  # 恰好一次
            self.assertIsNone(claude_hook._read_state("s1"))
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and Path(f"/proc/{driver}").exists():
                time.sleep(0.1)
            self.assertFalse(Path(f"/proc/{driver}").exists(), "收尾后发心跳的进程还活着")
        finally:
            for pid in (fake.pid, driver):
                if pid:
                    with contextlib.suppress(OSError):
                        os.kill(pid, signal.SIGKILL)
            fake.wait()

    def test_monitor_stops_the_lane_when_the_cli_dies(self):
        self._check("monitor")

    def test_companion_stops_the_lane_when_the_cli_dies(self):
        self._check("companion")


class _SessionCases(_BeatMixin):
    """假 cc、假进程表：SessionStart 全流程。"""

    EVENT = {"session_id": "s1", "cwd": "/home/u/garden", "hook_event_name": "SessionStart"}

    def setUp(self):
        super().setUp()
        self.starts = []
        self.next_run = "run-1"
        p = mock.patch.object(cc, "start_run", lambda *_a, **kw: self.starts.append(kw) or {"runId": self.next_run})
        p.start()
        self.addCleanup(p.stop)
        self.popen_mock = mock.patch.object(subprocess, "Popen").start()
        self.addCleanup(mock.patch.stopall)
        self.cli = CLI
        mock.patch.object(claude_hook, "_cli_pid", lambda: self.cli).start()

    def _start(self, **extra):
        return _run_hook(claude_hook.handle_session_start, {**self.EVENT, **extra})


class SessionStartTests(_SessionCases, unittest.TestCase):
    def test_same_cli_keeps_gen_across_compact_and_resume_and_spawns_no_second_beater(self):
        self._start()
        gen = claude_hook._read_state("s1")["gen"]
        self._start()  # /compact、恢复：同一个 CLI 再来一次 SessionStart
        self.assertEqual(claude_hook._read_state("s1")["gen"], gen)  # 以前每次都换：在发的老进程就此退出，没人接着发
        self.assertTrue(all("--wait" not in c.args[0] for c in self.popen_mock.call_args_list))

    def test_other_cli_gets_a_new_gen_and_a_waiting_beater_even_while_the_old_one_holds_the_lock(self):
        self._start()
        old_gen = claude_hook._read_state("s1")["gen"]
        self.procs[CLI + 1] = (1, "claude", "born-2")
        self.cli = CLI + 1
        old_lock = claude_hook._beat_lock("s1")  # 老 CLI 的发心跳进程还占着锁
        self.addCleanup(old_lock.close)
        self.popen_mock.reset_mock()
        self._start()
        self.assertNotEqual(claude_hook._read_state("s1")["gen"], old_gen)
        argv = self.popen_mock.call_args.args[0]
        self.assertIn("--wait", argv)  # 新进程自己等锁（钩子不阻塞）
        self.assertEqual(argv[argv.index("--cli") + 1], str(CLI + 1))

    def test_a_waiting_beater_takes_over_once_the_old_one_lets_go(self):
        self._save("s1", "run-1", "idle", "garden")
        gen = claude_hook._read_state("s1")["gen"]
        old_lock = claude_hook._beat_lock("s1")
        self.addCleanup(old_lock.close)
        clock = _Clock(max_sleeps=5, on_sleep=lambda n: n == 2 and old_lock.close())
        with self.assertRaises(_Done):
            claude_hook.beat_loop("companion", "s1", CLI, sleep=clock.sleep, clock=clock.clock, born="born-1", gen=gen, wait=True)
        self.assertIn(("beat", "run-1"), self.calls)
        self.calls.clear()  # 不等的（wait=False）遇到占着的锁直接放弃
        held = claude_hook._beat_lock("s1")
        self.addCleanup(held.close)
        claude_hook.beat_loop("companion", "s1", CLI, sleep=_Clock().sleep, clock=_Clock().clock, born="born-1", gen=gen)
        self.assertEqual(self.calls, [])

    def test_lock_not_held_sends_no_start_and_writes_no_state(self):
        """锁或者什么都不做：拿不到会话锁 = 不发 /start、不写状态、不起伴随进程；最多一行 stderr。"""
        self._save("s1", "run-1", "working", "old")
        before = claude_hook._read_state("s1")
        self.next_run = "run-2"
        with mock.patch.object(claude_hook, "_session_lock", _no_lock):
            _, err = self._start()
            self._start(session_id="s2")  # 没有旧状态的会话也一样
        self.assertEqual(self.starts, [])
        self.assertEqual(claude_hook._read_state("s1"), before)
        self.assertIsNone(claude_hook._read_state("s2"))
        self.assertEqual(self.calls, [])
        self.assertEqual(self.popen_mock.call_count, 0)
        self.assertLessEqual(len(err.strip().splitlines()), 1)

    def test_state_that_cannot_be_committed_stops_the_run_it_just_started(self):
        with mock.patch.object(claude_hook, "_write_state", return_value=False):
            self._start()
        self.assertEqual(self.calls, [("stop", "run-1", "cancelled")])
        self.assertIsNone(claude_hook._read_state("s1"))

    def test_the_compensating_stop_happens_inside_the_lock_so_a_concurrent_start_is_never_stopped(self):
        """start → 落状态失败 → stop 全在一个临界区：stop 时锁还被持着；同一 clientKey 的另一个 SessionStart 只能排在它后面。"""
        order, lock_held_at_stop = [], []

        def fake_start(*_a, **_k):
            order.append("start")
            return {"runId": "run-1"}  # 同一 clientKey → 同一条 run

        def fake_stop(_c, run_id, outcome, **_k):
            with claude_hook._session_lock("s1", wait=0) as held:
                lock_held_at_stop.append(held)
            order.append("stop")
            time.sleep(0.3)  # 慢 stop：锁外的补偿 stop 会让另一个 start 插进来
            return {}

        writes = iter([False])  # 只有第一次落状态失败
        real_write = claude_hook._write_state
        second = threading.Thread(target=lambda: claude_hook.handle_session_start(self.EVENT))
        with mock.patch.object(cc, "start_run", fake_start), mock.patch.object(cc, "stop_run", fake_stop), \
                mock.patch.object(claude_hook, "_write_state", lambda *a: next(writes, None) is None and real_write(*a)):
            first = threading.Thread(target=lambda: claude_hook.handle_session_start(self.EVENT))
            first.start()
            while "stop" not in order:
                time.sleep(0.01)
            second.start()  # 第一个正在（慢）stop
            first.join(5)
            second.join(5)
        self.assertEqual(order, ["start", "stop", "start"])  # 第二个的 start 排在 stop 之后，它落下来的 run 没人停
        self.assertEqual(lock_held_at_stop, [False])  # stop 时锁还在第一个手里
        self.assertEqual(claude_hook._read_run_id("s1"), "run-1")


class UnsupervisedWarningTests(_SessionCases, unittest.TestCase):
    def test_one_stderr_line_with_reason_and_remedy_once_per_session(self):
        self.cli = 0  # 认不出 Claude Code
        _, err = self._start()
        self.assertEqual(len(err.strip().splitlines()), 1)
        self.assertIn("COCKPIT_CLI_NAMES", err)
        self.assertIn("COCKPIT_CLI_PID", err)
        _, err2 = self._start()  # 再来一次（/compact）：不重复说
        self.assertEqual(err2, "")
        self.assertTrue(claude_hook._read_state("s1")["warned"])

    def test_silent_when_supervised_or_when_heartbeat_is_switched_off(self):
        _, err = self._start()
        self.assertEqual(err, "")
        os.environ["COCKPIT_BEAT"] = "off"
        self.cli = 0
        _, err = self._start(session_id="s2")
        self.assertEqual(err, "")

    def test_the_beat_loop_itself_never_prints(self):
        """提示只在 SessionStart 钩子里；monitor 的输出会叫醒模型，发心跳的循环从不出声。"""
        self.cli = 0
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf), contextlib.redirect_stdout(buf):
            claude_hook.beat_loop("monitor", None, 0, sleep=_Clock(max_sleeps=3).sleep, clock=_Clock().clock)
        self.assertEqual(buf.getvalue(), "")


class CliNameTests(_BeatMixin, unittest.TestCase):
    def test_cli_names_match_case_insensitively(self):
        self.procs[7] = (1, "Claude", "b7")
        self.procs[8] = (7, "bash", "b8")
        with mock.patch.object(os, "getppid", return_value=8):
            self.assertEqual(claude_hook._cli_pid(), 7)  # 大写开头的也认
            self.procs[7] = (1, "Node", "b7")
            self.assertEqual(claude_hook._cli_pid(), 0)
            os.environ["COCKPIT_CLI_NAMES"] = "node, other"
            self.assertEqual(claude_hook._cli_pid(), 7)
            os.environ["COCKPIT_CLI_NAMES"] = "NODE"
            self.assertEqual(claude_hook._cli_pid(), 7)


class DoctorTests(_IsolatedHomeMixin, unittest.TestCase):
    def _doctor(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(claude_hook._doctor(), 0)
        return buf.getvalue()

    def test_doctor_reports_config_state_dir_cli_mode_and_live_beater_and_never_the_token(self):
        os.environ["COCKPIT_URL"] = "http://user:pw@cockpit.example:8800/x"
        os.environ["COCKPIT_TOKEN"] = "sekret-token"
        for k in ("COCKPIT_URL", "COCKPIT_TOKEN"):
            self.addCleanup(os.environ.pop, k, None)
        claude_hook._save_run_id("s1", "run-1", "idle", "garden", {"cwd": "/tmp"})
        out = self._doctor()
        self.assertIn("cockpit.example:8800", out)
        self.assertNotIn("sekret-token", out)
        self.assertNotIn("user:pw", out)
        self.assertIn("令牌 有", out)
        self.assertIn(str(cc.user_dir("state")), out)
        self.assertIn("心跳方式", out)
        self.assertIn("没有记录", out)
        lock = claude_hook._beat_lock("s1")  # 有人占着 .beat
        self.addCleanup(lock.close)
        lock.write("4321 companion")
        lock.flush()
        self.assertIn("4321 companion", self._doctor())

    def test_doctor_flag_runs_as_a_script_and_exits_zero(self):
        proc = subprocess.run([sys.executable, str(HERE / "claude_hook.py"), "--doctor"], env=self._subprocess_env(),
                              capture_output=True, timeout=30, text=True)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("Claude Code", proc.stdout)


# ── 5：陈旧快照不写归属字段 ────────────────────────────────────────────────────────────────────
class StaleSnapshotTests(_BeatMixin, unittest.TestCase):
    EVENT = {"hook_event_name": "UserPromptSubmit", "session_id": "s1", "cwd": "/home/u/garden"}

    def test_phase_keeps_the_ownership_fields_a_concurrent_writer_changed(self):
        self._save("s1", "run-1", "idle", "garden", cwd="/home/u/garden")

        def concurrent(_session, _state, wait=False, probe=None):  # 钩子读了快照之后，别人（发心跳的进程）改了状态
            claude_hook._write_state("s1", {**claude_hook._read_state("s1"), "beat": "unsupported"})

        with mock.patch.object(claude_hook, "_spawn_beat", concurrent), mock.patch.object(cc, "phase_run", return_value={}):
            claude_hook.handle_phase(self.EVENT, cc.now_iso())
        st = claude_hook._read_state("s1")
        self.assertEqual((st["lastPhase"], st["beat"]), ("working", "unsupported"))  # 只改自己的字段，别人的不抹掉

        def new_session(_session, _state, wait=False, probe=None):
            claude_hook._write_state("s1", {**claude_hook._read_state("s1"), "gen": "new-gen", "lastPhase": "idle"})

        with mock.patch.object(claude_hook, "_spawn_beat", new_session), mock.patch.object(cc, "phase_run", return_value={}):
            claude_hook.handle_phase(self.EVENT, cc.now_iso())
        st = claude_hook._read_state("s1")
        self.assertEqual((st["gen"], st["lastPhase"]), ("new-gen", "idle"))  # 换了会话：旧快照什么都不许写回

    def test_phase_is_neither_written_nor_sent_without_the_session_lock(self):
        self._save("s1", "run-1", "working", "garden")
        with mock.patch.object(claude_hook, "_session_lock", _no_lock), mock.patch.object(cc, "phase_run", return_value={}) as sent:
            claude_hook.handle_phase({"hook_event_name": "Stop", "session_id": "s1"}, cc.now_iso())
        sent.assert_not_called()  # 依赖状态的改状态请求不发
        self.assertEqual(claude_hook._read_state("s1")["lastPhase"], "working")  # 状态不写

    def test_relabel_and_reopen_take_the_phase_from_the_fresh_state_and_never_write_last_phase(self):
        """旧版：重开 / 改名拿调用方的旧参数写 lastPhase，把别处刚写的新相位盖掉。"""
        self._save("s1", "run-1", "idle", "old", cwd="/home/u/garden")
        stale = claude_hook._read_state("s1")  # lastPhase=idle 的旧快照
        claude_hook._write_state("s1", {**stale, "lastPhase": "waiting_permission"})  # 之后别处写了更新的相位
        sent = []

        def start(*_a, **kw):
            sent.append(kw["phase"])
            return {"runId": "run-2"}

        with mock.patch.object(cc, "start_run", start):
            with claude_hook._session_lock("s1"):
                claude_hook._relabel_locked({"cwd": "/home/u/garden"}, "s1", "New Name", stale)
        st = claude_hook._read_state("s1")
        self.assertEqual(sent, ["waiting_permission"])  # 开跑相位 = 锁内重读的
        self.assertEqual((st["runId"], st["label"], st["lastPhase"]), ("run-2", "New Name", "waiting_permission"))

    def test_unsupported_is_not_recorded_without_the_session_lock(self):
        self._save("s1", "run-1")
        self.beat_result = (900, cc.BEAT_UNSUPPORTED)
        with mock.patch.object(claude_hook, "_session_lock", _no_lock):
            self.assertIsNone(claude_hook._beat_once("s1", claude_hook._read_state("s1"), "companion"))
        self.assertNotIn("beat", claude_hook._read_state("s1"))
        self.assertIsNone(claude_hook._beat_once("s1", claude_hook._read_state("s1"), "companion"))
        self.assertEqual(claude_hook._read_state("s1")["beat"], "unsupported")


# ── 2 / 6 / 8 与变异存活的几处 ─────────────────────────────────────────────────────────────────
class LoopTests(_BeatMixin, unittest.TestCase):
    def _run(self, clock, **kw):
        try:
            claude_hook.beat_loop("companion", "s1", CLI, sleep=clock.sleep, clock=clock.clock, born="born-1", **kw)
        except _Done:
            return False
        return True  # 自己退出的

    def test_other_gen_with_the_same_cli_and_born_lets_go_without_stopping(self):
        self._save("s1", "run-1")
        gen = claude_hook._read_state("s1")["gen"]
        self.assertTrue(self._run(_Clock(), gen="someone-elses"))  # 一开始就对不上：不发
        self.assertEqual(self.calls, [])

        def swap(n):  # 圈中被「同一个 CLI 的新一次会话」换了归属号
            if n == 2:
                claude_hook._write_state("s1", {**claude_hook._read_state("s1"), "gen": "new"})
                self.procs.clear()  # 之后 CLI 死了也不许停（不是自己的会话了）

        self.assertTrue(self._run(_Clock(max_sleeps=10, on_sleep=swap), gen=gen))
        self.assertNotIn(("stop", "run-1", "cancelled"), self.calls)

    def test_replaced_beat_file_ends_the_loop(self):
        self._save("s1", "run-1")
        beat = claude_hook._state_file("s1").with_suffix(".beat")

        def replace(n):
            if n == 2:
                beat.unlink()
                beat.write_text("")

        self.assertTrue(self._run(_Clock(max_sleeps=10, on_sleep=replace), gen=claude_hook._read_state("s1")["gen"]))

    def test_failed_stop_is_retried_with_backoff_then_succeeds(self):
        self._save("s1", "run-1")
        clock = _Clock(on_sleep=lambda n: n == 1 and self.procs.clear())
        outcomes = [cc.CockpitError("连不上"), cc.CockpitError("超时"), {}]
        with mock.patch.object(cc, "stop_run", side_effect=outcomes) as stop:
            self.assertTrue(self._run(clock, gen=claude_hook._read_state("s1")["gen"]))
        self.assertEqual(stop.call_count, 3)
        self.assertIsNone(claude_hook._read_state("s1"))
        self.assertEqual(clock.sleeps, 1 + 2)  # 一圈发现 + 两次退避

    def test_stop_retries_are_bounded_and_the_state_is_kept(self):
        self._save("s1", "run-1")
        clock = _Clock(on_sleep=lambda n: n == 1 and self.procs.clear())
        with mock.patch.object(cc, "stop_run", side_effect=cc.CockpitError("连不上")) as stop:
            self.assertTrue(self._run(clock, gen=claude_hook._read_state("s1")["gen"]))
        self.assertEqual(stop.call_count, claude_hook.STOP_ATTEMPTS)
        self.assertEqual(claude_hook._read_run_id("s1"), "run-1")

    def test_beater_exits_after_consecutive_rounds_with_a_stuck_request(self):
        self._save("s1", "run-1")
        clock = _Clock(max_sleeps=100)
        with mock.patch.object(cc, "stuck_requests", return_value=1):
            self.assertTrue(self._run(clock, gen=claude_hook._read_state("s1")["gen"]))
        self.assertEqual(self.calls, [])  # 一下都没叠
        self.assertLess(clock.sleeps, 12)


class MonitorLoopTests(_BeatMixin, unittest.TestCase):
    SOURCE = "monitor"

    def test_dead_cli_is_stopped_even_when_the_monitor_can_no_longer_identify_it(self):
        self._save("s1", "run-1")
        clock = _Clock(on_sleep=lambda n: n == 2 and self.procs.pop(CLI))
        # monitor 被收养后认不出任何 CLI：identify 回 (0, None)。以前这就当「换了 CLI」静悄悄退出
        gone = lambda: (0, None) if CLI not in self.procs else (CLI, "born-1")  # noqa: E731
        claude_hook.beat_loop("monitor", None, CLI, sleep=clock.sleep, clock=clock.clock, born="born-1", identify=gone)
        self.assertEqual(self.calls[-1], ("stop", "run-1", "cancelled"))

    def test_unidentifiable_but_alive_cli_counts_as_unchanged_and_another_cli_does_not(self):
        self._save("s1", "run-1")
        clock = _Clock(max_sleeps=6)
        with self.assertRaises(_Done):  # 认不出但 pid + 启动时刻还活着：照常发
            claude_hook.beat_loop("monitor", None, CLI, sleep=clock.sleep, clock=clock.clock, born="born-1",
                                  identify=lambda: (0, None))
        self.assertIn(("beat", "run-1"), self.calls)
        self.calls.clear()
        clock = _Clock(max_sleeps=6)
        with self.assertRaises(_Done):  # 认出的是另一个 CLI：这个会话放手（monitor 继续找自己 CLI 的会话）
            claude_hook.beat_loop("monitor", None, CLI, sleep=clock.sleep, clock=clock.clock, born="born-1",
                                  identify=lambda: (CLI + 5, "x"))
        self.assertNotIn(("stop", "run-1", "cancelled"), self.calls)


class StuckRequestTests(_IsolatedHomeMixin, unittest.TestCase):
    def test_trickling_body_ends_the_worker_by_the_absolute_deadline_and_the_next_beat_goes_out(self):
        body = json.dumps({"applied": True, "heartbeatSeconds": 900, "pad": "x" * 400}).encode()
        server, thread = _start_dribble_server(body, per_byte_delay=0.2)  # 每次 recv 都在时限内，累计 ~80 秒
        try:
            config = {"url": f"http://127.0.0.1:{server.server_address[1]}", "token": ""}
            t0 = time.monotonic()
            self.assertEqual(cc.beat(config, "run-1", 0.5), (cc.HEARTBEAT_MIN, False))  # 超时按「没发成」，一分钟后补
            self.assertLess(time.monotonic() - t0, 1.5)
            deadline = time.monotonic() + 2.5  # 绝对时限 = 2 × 0.5 秒（+ 一次 recv 的余量）
            while time.monotonic() < deadline and cc.stuck_requests():
                time.sleep(0.05)
            self.assertEqual(cc.stuck_requests(), 0, "后台线程没有按绝对时限结束")
        finally:
            _stop_server(server, thread)
        good, gthread, log = _start_server(expect_token=None)
        try:
            cc.beat({"url": f"http://127.0.0.1:{good.server_address[1]}", "token": ""}, "run-1", 1.0)
            self.assertEqual([r["path"] for r in log.requests], ["/api/core/agents/run-1/heartbeat"])  # 下一下发出去了
        finally:
            _stop_server(good, gthread)


class ContractNoteTests(unittest.TestCase):
    def test_lane_contract_states_the_beat_cap(self):
        text = (HERE.parent.parent / "contracts" / "agent.lane.v1" / "contract.md").read_text(encoding="utf-8")
        self.assertIn("lostAfterSeconds / 2", text)
