"""PR #96 外部静态评审（第三轮）的回归测试：归属核对、激活戳、超时后进程退出、同会话再开、重开写失败的补偿、
锁内定相位、stdin 时限与上限、输出里的路径过滤、心跳间隔从尝试开始排。跑法同 test_agent_hooks.py；全部在临时 HOME 里。
"""

from __future__ import annotations

import contextlib
import io
import json
import os
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
from test_agent_hooks import _IsolatedHomeMixin  # noqa: E402
from test_agent_hooks_beat import CLI, _BeatMixin, _Clock  # noqa: E402


class _R3(_BeatMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.popen = mock.patch.object(subprocess, "Popen").start()
        self.addCleanup(mock.patch.stopall)
        self.cli = CLI
        mock.patch.object(claude_hook, "_cli_find", lambda: (self.cli, claude_hook._born(self.cli))).start()
        self.phases, self.starts = [], []
        mock.patch.object(cc, "phase_run", lambda _c, run_id, phase, *_a, **_k: self.phases.append((run_id, phase)) or {"applied": True}).start()
        self.next_run = "run-2"
        mock.patch.object(cc, "start_run", lambda *_a, **kw: self.starts.append(kw) or {"runId": self.next_run}).start()
        self.procs[CLI + 1] = (1, "claude", "born-2")

    def _save(self, session, run_id, phase=None, label=None, **payload):
        claude_hook._save_run_id(session, run_id, phase, label, payload)  # `_cli_pid` 已在 setUp 里钉成 self.cli

    def _stamp(self, session, at):
        st = claude_hook._read_state(session)
        claude_hook._write_state(session, {**st, "activatedAt": at})


class OwnershipTests(_R3):
    def test_session_end_and_phase_hook_from_another_cli_do_nothing(self):
        self._save("s1", "run-1", "working", "garden")  # CLI A 的会话，B 随后恢复……这里直接让「发起者」是 B
        before = claude_hook._read_state("s1")
        self.cli = CLI + 1
        claude_hook.handle_session_end({"session_id": "s1"})
        claude_hook.handle_phase({"session_id": "s1", "hook_event_name": "UserPromptSubmit", "cwd": "/x"}, "t")
        self.assertEqual(self.calls, [])  # 没有 stop
        self.assertEqual(self.phases, [])
        self.assertEqual(self.starts, [])
        self.assertEqual(claude_hook._read_state("s1"), before)  # 状态一个字没动
        self.cli = CLI  # 对照：真正的主人照常收尾
        claude_hook.handle_session_end({"session_id": "s1"})
        self.assertEqual(self.calls, [("stop", "run-1", "done")])
        self.assertIsNone(claude_hook._read_state("s1"))

    def test_unidentifiable_invoker_keeps_the_old_behaviour(self):
        self._save("s1", "run-1", "working", "garden")
        self.cli = 0  # 认不出发起的 CLI
        claude_hook.handle_session_end({"session_id": "s1"})
        self.assertEqual(self.calls, [("stop", "run-1", "done")])


class ActivationStampTests(_R3):
    def test_rewriting_an_old_session_does_not_make_the_beater_stop_the_current_one(self):
        self._save("s1", "run-1", "idle", "a")
        self._save("s2", "run-2", "idle", "b")
        self._stamp("s1", 100.0)
        self._stamp("s2", 200.0)
        st = claude_hook._read_state("s1")  # 老会话 S1 的迟到钩子重写它：mtime 比 S2 新
        claude_hook._write_state("s1", {**st, "lastPhase": "working"})
        self.assertGreater(claude_hook._state_file("s1").stat().st_mtime_ns, claude_hook._state_file("s2").stat().st_mtime_ns - 1)
        self.assertEqual(claude_hook._session_of(CLI, "born-1"), "s2")
        self.assertFalse(self._loop(_Clock(max_sleeps=3), session="s2"))
        self.assertEqual(self.calls, [("beat", "run-2")])  # 没有 stop
        self.assertEqual(claude_hook._read_state("s2")["runId"], "run-2")

    def test_stamp_changes_only_at_session_start(self):
        ev = {"session_id": "s1", "cwd": "/tmp/garden", "hook_event_name": "SessionStart"}
        with contextlib.redirect_stderr(io.StringIO()):
            claude_hook.handle_session_start(ev)
        self._stamp("s1", 5.0)
        claude_hook.handle_phase({"session_id": "s1", "hook_event_name": "UserPromptSubmit", "cwd": "/tmp/garden"}, "t")
        claude_hook._relabel_locked({"cwd": "/tmp/other"}, "s1", "new title", claude_hook._read_state("s1"))
        self.assertEqual(claude_hook._read_state("s1")["activatedAt"], 5.0)
        with contextlib.redirect_stderr(io.StringIO()):
            claude_hook.handle_session_start(ev)  # 同会话再一次 SessionStart（compact / resume）：刷新
        self.assertGreater(claude_hook._read_state("s1")["activatedAt"], 5.0)

    def test_stop_rechecks_which_session_is_current_under_the_lock(self):
        self._save("s1", "run-1", "idle", "a")
        gen = claude_hook._read_state("s1")["gen"]
        self.assertFalse(claude_hook._stop_gone("s1", gen, CLI, "born-1", lambda _s: None, outcome="done", moved=True))  # s1 仍是该 CLI 唯一 / 最新的会话
        self.assertEqual(self.calls, [])
        self._save("s2", "run-2", "idle", "b")
        self._stamp("s1", 1.0)
        self._stamp("s2", 2.0)
        self.assertTrue(claude_hook._stop_gone("s1", gen, CLI, "born-1", lambda _s: None, outcome="done", moved=True))
        self.assertEqual(self.calls, [("stop", "run-1", "done")])


class StuckStopTests(_R3):
    def test_beater_exits_when_stop_times_out_with_a_live_worker(self):
        self._save("s1", "run-1", "working", "garden")
        gen = claude_hook._read_state("s1")["gen"]
        release = threading.Event()
        self.addCleanup(release.set)
        self.addCleanup(cc._stuck.clear)

        def slow_stop(_c, run_id, outcome, **_k):
            t = threading.Thread(target=release.wait, daemon=True)
            t.start()
            cc._stuck.append(t)
            self.calls.append(("stop", run_id, outcome))
            raise cc.CockpitError("超时")

        clock = _Clock(on_sleep=lambda n: n == 2 and self.procs.pop(CLI))
        with mock.patch.object(cc, "stop_run", slow_stop):
            end = claude_hook._beat_session("s1", "companion", CLI, "born-1", [1000], clock.sleep, clock.clock, gen)
        self.assertEqual(end, claude_hook.END_STUCK)
        self.assertEqual([c for c in self.calls if c[0] == "stop"], [("stop", "run-1", "cancelled")])  # 只一次：不在锁外重试
        self.assertEqual(clock.sleeps, 2)  # 没有 30 秒后的第二次尝试


    def test_heartbeat_worker_finishing_during_a_timed_out_stop_does_not_mask_it(self):
        self._save("s1", "run-1", "working", "garden")
        gen = claude_hook._read_state("s1")["gen"]
        release, beat_done = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        self.addCleanup(cc._stuck.clear)
        beat = threading.Thread(target=beat_done.wait, daemon=True)  # 卡着的心跳线程
        beat.start()
        cc._stuck.append(beat)

        def slow_stop(_c, run_id, outcome, **_k):
            t = threading.Thread(target=release.wait, daemon=True)
            t.start()
            cc._stuck.append(t)
            beat_done.set()  # 心跳线程恰在 stop 期间结束：前后数量都是 1
            beat.join()
            raise cc.CockpitError("超时")

        self.assertEqual(cc.stuck_requests(), 1)
        with mock.patch.object(cc, "stop_run", slow_stop):
            self.assertIsNone(claude_hook._stop_gone("s1", gen, CLI, "born-1", lambda _s: None))
        self.assertEqual(cc.stuck_requests(), 1)


class RespawnAfterEndTests(_R3):
    EVENT = {"session_id": "s1", "cwd": "/tmp/garden", "hook_event_name": "SessionStart"}

    def test_session_start_with_the_old_beater_still_holding_the_lock_leaves_a_beater_for_the_new_generation(self):
        old = claude_hook._beat_lock("s1")  # 老发心跳的进程睡着，占着 .beat；SessionEnd 已删了状态（没有 prev）
        self.addCleanup(old.close)
        self.assertIsNone(claude_hook._read_state("s1"))
        with contextlib.redirect_stderr(io.StringIO()):
            claude_hook.handle_session_start(self.EVENT)
        argv = self.popen.call_args.args[0]
        self.assertIn("--wait", argv)  # 有界的 --wait 伴随进程
        gen = claude_hook._read_state("s1")["gen"]
        self.assertEqual(argv[argv.index("--gen") + 1], gen)
        # 新进程等到老的醒来发现归属号变了、放锁，然后接着发
        clock = _Clock(max_sleeps=5, on_sleep=lambda n: n == 2 and old.close())
        with self.assertRaises(Exception):
            claude_hook.beat_loop("companion", "s1", CLI, sleep=clock.sleep, clock=clock.clock, born="born-1", gen=gen, wait=True)
        self.assertIn(("beat", "run-2"), self.calls)


class RelabelWriteFailureTests(_R3):
    def test_reopen_whose_state_cannot_be_written_stops_the_new_run_and_keeps_the_old_record(self):
        self._save("s1", "run-1", "working", "garden")
        before = claude_hook._read_state("s1")
        with mock.patch.object(claude_hook, "_write_state", return_value=False):
            claude_hook._relabel_locked({"cwd": "/tmp/garden"}, "s1", "new title", before)
        self.assertEqual(self.calls, [("stop", "run-2", "cancelled")])
        self.assertEqual(claude_hook._read_state("s1"), before)
        self.calls.clear()
        self.next_run = "run-1"  # 同一条改名（runId 没变）：写失败也不能停它
        with mock.patch.object(claude_hook, "_write_state", return_value=False):
            claude_hook._relabel_locked({"cwd": "/tmp/garden"}, "s1", "newer title", before)
        self.assertEqual(self.calls, [])


class PhaseUnderLockTests(_R3):
    def test_phase_decision_uses_the_state_read_inside_the_lock(self):
        self._save("s1", "run-1", "idle", "garden")
        real = claude_hook._session_lock

        @contextlib.contextmanager
        def racing(session_id, wait=None):
            with real(session_id, wait) as held:
                if held:  # 锁前读到 idle；拿到锁时并发的钩子已把它改成了 waiting_input
                    st = claude_hook._read_state(session_id)
                    claude_hook._write_state(session_id, {**st, "lastPhase": "waiting_input"})
                yield held

        with mock.patch.object(claude_hook, "_session_lock", racing):
            claude_hook.handle_phase({"session_id": "s1", "hook_event_name": "PostToolUse", "cwd": "/tmp/garden"}, "t")
        self.assertEqual(self.phases, [("run-1", "working")])  # 用锁前的 idle 判会是「不报」


class StdinTests(_IsolatedHomeMixin, unittest.TestCase):
    @staticmethod
    def _close(fd):
        with contextlib.suppress(OSError):
            os.close(fd)

    def _pipe(self):
        r, w = os.pipe()
        stdin = os.fdopen(r, "r")
        self.addCleanup(stdin.close)
        self.addCleanup(lambda: contextlib.suppress(OSError) and None or self._close(w))
        return stdin, w

    def test_open_pipe_returns_within_the_budget(self):
        stdin, w = self._pipe()
        os.write(w, b'{"a":')
        t0 = time.monotonic()
        with mock.patch.object(sys, "stdin", stdin), mock.patch.object(claude_hook, "_deadline", time.monotonic() + 0.3):
            self.assertIsNone(claude_hook._read_stdin())
        self.assertLess(time.monotonic() - t0, 2)

    def test_oversize_is_dropped_and_normal_input_read(self):
        stdin, w = self._pipe()
        def flood():
            with contextlib.suppress(OSError):  # 读端丢弃后写端 EPIPE：正常
                for _ in range(40):  # 2.5 MiB
                    os.write(w, b"x" * 65536)

        threading.Thread(target=flood, daemon=True).start()
        with mock.patch.object(sys, "stdin", stdin), mock.patch.object(claude_hook, "_deadline", time.monotonic() + 3):
            self.assertIsNone(claude_hook._read_stdin())
        r, w2 = os.pipe()
        os.write(w2, '{"k":"中"}'.encode())
        os.close(w2)
        with os.fdopen(r) as ok, mock.patch.object(sys, "stdin", ok), mock.patch.object(claude_hook, "_deadline", time.monotonic() + 3):
            self.assertEqual(claude_hook._read_stdin(), '{"k":"中"}')

    def test_real_process_with_stdin_held_open_exits_zero_in_time(self):
        p = subprocess.Popen([sys.executable, str(HERE / "claude_hook.py")], stdin=subprocess.PIPE, env=self._subprocess_env(),
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            t0 = time.monotonic()
            self.assertEqual(p.wait(timeout=8), 0)
            self.assertLess(time.monotonic() - t0, 6)
        finally:
            p.kill()
            p.stdin.close()
            p.wait()


@unittest.skipUnless(sys.platform.startswith("linux"), "XDG 路径只在 Linux 分支")
class HostilePathOutputTests(_IsolatedHomeMixin, unittest.TestCase):
    EVIL = "ev\x1b[31mil\nINJECTED line\x07"

    def _evil_env(self):
        root = Path(self._home_tmpdir.name)
        env = mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(root / "s" / self.EVIL), "XDG_CONFIG_HOME": str(root / "c" / self.EVIL)})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("HONEYCOMB_AGENT_HOOKS_HOME", None)
        os.environ.pop("COCKPIT_URL", None)
        os.environ.pop("CLAUDE_PLUGIN_OPTION_COCKPIT_URL", None)
        return root

    def _clean(self, text):
        self.assertFalse([c for c in text if (ord(c) < 32 and c != "\n") or c == "\x7f"], repr(text))
        self.assertEqual([l for l in text.splitlines() if l.startswith("INJECTED")], [])

    def test_doctor_and_warning_carry_no_control_characters(self):
        root = self._evil_env()
        cfg = root / "c" / self.EVIL / "honeycomb"
        cfg.mkdir(parents=True, mode=0o700)
        (cfg / cc.CONFIG_FILENAME).write_text(json.dumps({"url": "http://127.0.0.1:1"}))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            claude_hook._doctor()
        self._clean(out.getvalue())
        self.assertIn("INJECTED", out.getvalue())  # 内容还在，只是不再能换行 / 转义
        state_root = root / "s" / self.EVIL
        state_root.mkdir(parents=True)
        (state_root / "honeycomb").symlink_to(root)  # 状态目录是符号链接 → 拒绝，并打印路径
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            claude_hook._doctor()
        self._clean(out.getvalue())
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            claude_hook.handle_session_start({"session_id": "s1", "cwd": "/tmp"})
        self.assertIn("符号链接", err.getvalue())
        self._clean(err.getvalue())


class BeatSpacingTests(_R3):
    def test_spacing_is_measured_from_the_start_of_the_previous_attempt(self):
        self._save("s1", "run-1", "idle", "garden")
        clock = _Clock(max_sleeps=130)
        starts = []

        def slow_beat(_c, run_id, _t=None, beat_source=None):
            starts.append(clock.now)
            clock.now += 200  # 请求本身要 200 秒
            return 600, False

        with mock.patch.object(cc, "beat", slow_beat):
            self.assertFalse(self._loop(clock))
        gaps = [b - a for a, b in zip(starts, starts[1:])]
        self.assertGreaterEqual(len(gaps), 3)
        self.assertTrue(all(g <= 600 + 2 * claude_hook.BEAT_CHECK_SECONDS for g in gaps), gaps)  # 从请求结束排会是 800+


class _FakeStdin:
    def __init__(self, fd):
        self._fd = fd

    def fileno(self):
        return self._fd


class MainRound4Tests(_R3):
    EVENT = {"session_id": "s1", "hook_event_name": "UserPromptSubmit", "cwd": "/x"}

    def setUp(self):
        super().setUp()
        mock.patch.object(claude_hook, "_deadline", None).start()  # main() 会设这个全局：别漏给别的测试（addCleanup(stopall) 在 _R3 里）

    def _main(self, text, **kw):
        with mock.patch.object(claude_hook, "_read_stdin", return_value=text):
            return claude_hook.main(**kw)

    def test_slow_start_still_leaves_time_for_the_phase_request(self):
        self._save("s1", "run-1", "idle", "garden")
        self.assertEqual(self._main(json.dumps(self.EVENT), start=time.monotonic() - 10), 0)  # 进程早在 10 秒前启动
        self.assertEqual(self.phases, [("run-1", "working")])

    def test_oversized_event_on_stdin_is_ignored(self):
        self._save("s1", "run-1", "idle", "garden")
        r, w = os.pipe()
        body = json.dumps({**self.EVENT, "pad": "x" * (claude_hook.STDIN_MAX + 10)}).encode()
        t = threading.Thread(target=self._feed, args=(w, body))
        t.start()
        self.addCleanup(os.close, r)
        with mock.patch.object(claude_hook.sys, "stdin", _FakeStdin(r)):
            self.assertEqual(claude_hook.main(), 0)
        t.join(5)
        self.assertEqual(self.phases, [])

    @staticmethod
    def _feed(fd, body):
        try:
            view = memoryview(body)
            while view:
                view = view[os.write(fd, view):]
        except OSError:  # 读端不读了（超限就退出）
            pass
        finally:
            os.close(fd)


class ClosedStdinTests(_IsolatedHomeMixin, unittest.TestCase):  # 不继承 _R3：它把 subprocess.Popen 换成了假的
    @unittest.skipIf(os.name == "nt", "POSIX 关 fd 0")
    def test_closed_stdin_is_no_event_and_exit_0(self):
        proc = subprocess.run(
            [sys.executable, str(HERE / "claude_hook.py")], preexec_fn=lambda: os.close(0),  # fd 0 关掉 = `<&-`
            env=self._subprocess_env(), capture_output=True, text=True, timeout=20,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)


class SessionOfTieTests(_R3):
    def test_equal_stamps_are_broken_by_state_mtime_not_session_id(self):
        self._save("s1", "run-1", "idle", "a")
        self._save("s2", "run-2", "idle", "b")
        self._stamp("s1", 100.0)
        self._stamp("s2", 100.0)
        os.utime(claude_hook._state_file("s1"), (200, 200))
        os.utime(claude_hook._state_file("s2"), (100, 100))
        self.assertEqual(claude_hook._session_of(CLI, "born-1"), "s1")  # 会话号更大的 s2 不该赢
        os.utime(claude_hook._state_file("s2"), (300, 300))
        self.assertEqual(claude_hook._session_of(CLI, "born-1"), "s2")

    def test_old_format_states_cannot_prove_a_move(self):
        self._save("s1", "run-1", "idle", "a")
        self._save("s2", "run-2", "idle", "b")
        for s in ("s1", "s2"):  # 老格式：没有 activatedAt
            claude_hook._write_state(s, {k: v for k, v in claude_hook._read_state(s).items() if k != "activatedAt"})
        os.utime(claude_hook._state_file("s1"), (100, 100))
        os.utime(claude_hook._state_file("s2"), (500, 500))
        self.assertEqual(claude_hook._session_of(CLI, "born-1"), "s2")  # 监督者挑会话照旧
        self.assertIsNone(claude_hook._session_of(CLI, "born-1", proven=True))  # 但不能据此断言「已换会话」
        self.assertFalse(self._loop(_Clock(max_sleeps=2), session="s1") is None)  # 循环能正常转，不因此 stop
        self.assertNotIn(("stop", "run-1", "done"), self.calls)
        self._stamp("s2", 7.0)
        self.assertEqual(claude_hook._session_of(CLI, "born-1", proven=True), "s2")

if __name__ == "__main__":
    unittest.main()
