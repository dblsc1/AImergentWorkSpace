"""PR #96 收尾轮的回归测试：doctor 不回显不可信内容、悬空链接、幽灵 run、预算含解释器启动、beat 值规整、三个存活变异体。

跑法同 test_agent_hooks.py。
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
from test_agent_hooks_simple import POSIX, _HangingServer, _HookProcMixin  # noqa: E402


class DoctorHostileInputTests(_IsolatedHomeMixin, unittest.TestCase):
    def _doctor(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(claude_hook._doctor(), 0)
        return buf.getvalue()

    def _assert_clean(self, out):
        self.assertFalse([c for c in out if ord(c) < 32 and c != "\n"], repr(out))
        self.assertNotIn("\x7f", out)
        self.assertEqual([l for l in out.splitlines() if l.startswith("INJECTED")], [])

    def test_hostile_beat_file_prints_only_pid_and_known_source(self):
        claude_hook._save_run_id("s1", "run-1", "idle", "garden", {"cwd": "/tmp"})
        beat = claude_hook._state_file("s1").with_suffix(".beat")
        beat.write_text("4321 \x1b[31mred\nINJECTED line companion", encoding="utf-8")
        out = self._doctor()
        self._assert_clean(out)
        self.assertIn("记录的持有者 4321（", out)
        beat.write_text("\x1b[2J 99\nINJECTED", encoding="utf-8")
        self._assert_clean(self._doctor())
        beat.write_text("77 monitor", encoding="utf-8")
        self.assertIn("77 monitor", self._doctor())

    def test_hostile_process_name_and_host_are_neutralised(self):
        os.environ["COCKPIT_URL"] = "http://ho\x1b[31mst\nINJECTED:80/"
        self.addCleanup(os.environ.pop, "COCKPIT_URL", None)
        procs = {50: (1, "cl\x1b[31maude\nINJECTED", "b50")}
        with mock.patch.object(claude_hook, "_proc", lambda pid: procs.get(pid)), mock.patch.object(os, "getppid", return_value=50):
            out = self._doctor()
        self._assert_clean(out)
        self.assertIn("50:cl??31maude?INJECTED", out)

    def test_printable_caps_length(self):
        self.assertEqual(len(claude_hook._printable("a" * 500)), 64)
        self.assertEqual(claude_hook._printable("a b\x1b中"), "a?b??")

    def test_dangling_session_symlink_is_skipped_and_no_doubled_parentheses(self):
        out = self._doctor()
        self.assertNotIn("（（", out)
        state = cc.user_dir("state")
        state.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.symlink(state / "nowhere", state / "session-dangling.json")
        self.assertIn("最新会话：还没有", self._doctor())
        claude_hook._save_run_id("s1", "run-1", "idle", "garden", {"cwd": "/tmp"})
        self.assertIn("最新会话的心跳", self._doctor())

    def test_title_control_chars_never_reach_the_label(self):
        path = Path(self._home_tmpdir.name) / "t.jsonl"
        path.write_text(json.dumps({"type": "custom-title", "customTitle": "a\x1b[31mb‮c​d\ne"}) + "\n", encoding="utf-8")
        title = cc.session_title(str(path))
        self.assertEqual(title, "a [31mb c d e")
        self.assertFalse([c for c in cc.lane_names("/x", title)[0] if ord(c) < 32])


class BeatModeNormaliseTests(_IsolatedHomeMixin, unittest.TestCase):
    def test_value_is_stripped_and_case_folded(self):
        for raw, want in (("OFF", "off"), ("monitor ", "monitor"), (" Companion", "companion"), ("junk", "companion")):
            with mock.patch.dict(os.environ, {"COCKPIT_BEAT": raw}):
                self.assertEqual(cc.beat_mode({}), want)
        with mock.patch.dict(os.environ, {"COCKPIT_BEAT": ""}):
            self.assertEqual(cc.beat_mode({"beat": " MONITOR\n"}), "monitor")


class GhostRunTests(_BeatMixin, unittest.TestCase):
    def test_old_session_is_stopped_when_the_same_cli_now_owns_another(self):
        self._save("old", "run-old", "idle", "garden")
        self._save("new", "run-new", "idle", "garden")
        os.utime(claude_hook._state_file("old"), (1, 1))  # 老的更旧
        clock = _Clock(max_sleeps=5)
        self.assertTrue(self._loop(clock, session="old"))  # 自己退出
        self.assertEqual(self.calls, [("stop", "run-old", "done")])  # 没发过心跳；结局同 SessionEnd 缺省
        self.assertIsNone(claude_hook._read_state("old"))
        self.assertEqual(claude_hook._read_run_id("new"), "run-new")  # 新的不动

    def test_newest_session_keeps_beating(self):
        self._save("old", "run-old", "idle", "garden")
        self._save("new", "run-new", "idle", "garden")
        os.utime(claude_hook._state_file("old"), (1, 1))
        self.assertFalse(self._loop(_Clock(max_sleeps=3), session="new"))
        self.assertEqual({c[0] for c in self.calls}, {"beat"})


@POSIX
class BudgetFixTests(_HookProcMixin, unittest.TestCase):
    def test_hook_budget_counts_from_before_imports(self):
        # 起点是模块最前面的 _T0：钩子进程从解释器启动起算
        src = (HERE / "claude_hook.py").read_text(encoding="utf-8")
        body = src.split("from __future__ import annotations", 1)[1]
        self.assertTrue(body.lstrip().startswith("import time\n\n_T0 = time.monotonic()"))
        with mock.patch.object(time, "monotonic", return_value=100.0):
            with mock.patch.object(claude_hook, "_deadline", None):
                with mock.patch.object(claude_hook.sys.stdin, "read", return_value="{}"):
                    claude_hook.main(start=99.0)  # 解释器启动用掉的 1 秒要算进去：99 + 3 > 100 + 1.5
                    self.assertEqual(claude_hook._deadline, 99.0 + claude_hook.HOOK_BUDGET)
                    claude_hook.main(start=None)  # 对照：不给起点 = 从现在起算
                    self.assertEqual(claude_hook._deadline, 100.0 + claude_hook.HOOK_BUDGET)
                    claude_hook.main(start=50.0)  # 启动慢到预算早用完：仍保证启动之后 HOOK_MIN_WORK
                    self.assertEqual(claude_hook._deadline, 100.0 + claude_hook.HOOK_MIN_WORK)

    def test_hook_against_a_hanging_server_finishes_within_the_overall_budget(self):
        """锁被占到约 1.9 秒才放，之后相位请求 + 改名重开两次请求各卡 1 秒：没有总预算（`_deadline=None`）要 ≥ 4 秒。"""
        hanging = _HangingServer()
        self.addCleanup(hanging.close)
        claude_hook._write_state("s1", {"runId": "run-1", "lastPhase": "idle", "label": "old-name"})  # 与目录名不同 → 会改名
        ready, release = threading.Event(), threading.Event()

        def hold():
            with claude_hook._session_lock("s1"):
                ready.set()
                release.wait(1.9)

        t = threading.Thread(target=hold)
        t.start()
        self.assertTrue(ready.wait(5))
        try:
            proc, took = self._run_hook({"hook_event_name": "UserPromptSubmit", "session_id": "s1", "cwd": "/home/u/garden"}, COCKPIT_URL=hanging.url)
        finally:
            release.set()
            t.join()
        self.assertEqual(proc.returncode, 0)
        self.assertLess(took, 3.6)


class SurvivingMutantTests(_IsolatedHomeMixin, unittest.TestCase):
    def test_failed_commit_with_the_same_run_id_already_in_state_sends_no_stop(self):  # M5b
        claude_hook._write_state("s1", {"runId": "run-1", "gen": "g"})
        stops = []
        with mock.patch.object(claude_hook, "_cli_find", new=lambda: (0, claude_hook._born(0))), \
                mock.patch.object(claude_hook, "_write_state", return_value=False), \
                mock.patch.object(cc, "start_run", return_value={"runId": "run-1"}), \
                mock.patch.object(cc, "stop_run", lambda *a, **k: stops.append(a)), \
                mock.patch.object(cc, "load_config", return_value={"url": "http://x.invalid", "token": "", "beat": None}):
            claude_hook.handle_session_start({"session_id": "s1", "cwd": "/tmp"})
        self.assertEqual(stops, [])
        with mock.patch.object(claude_hook, "_cli_find", new=lambda: (0, claude_hook._born(0))), \
                mock.patch.object(claude_hook, "_write_state", return_value=False), \
                mock.patch.object(cc, "start_run", return_value={"runId": "run-2"}), \
                mock.patch.object(cc, "stop_run", lambda *a, **k: stops.append(a)), \
                mock.patch.object(cc, "load_config", return_value={"url": "http://x.invalid", "token": "", "beat": None}):
            claude_hook.handle_session_start({"session_id": "s1", "cwd": "/tmp"})
        self.assertEqual(len(stops), 1)  # 对照：runId 不同才停

    def test_ps_call_honours_the_remaining_budget(self):  # M16
        seen = []

        def fake_run(cmd, **kw):
            seen.append(kw.get("timeout"))
            return subprocess.CompletedProcess(cmd, 0, stdout="1 bash\n")

        pid = 2 ** 22 + 99  # 没有这个 /proc 条目 → 走 ps
        with mock.patch.object(claude_hook.subprocess, "run", fake_run):
            with mock.patch.object(claude_hook, "_deadline", time.monotonic() + 0.5):
                self.assertEqual(claude_hook._proc(pid)[:2], (1, "bash"))
            with mock.patch.object(claude_hook, "_deadline", None):
                claude_hook._proc(pid)
        self.assertLessEqual(seen[0], 0.5)
        self.assertGreater(seen[0], 0)
        self.assertEqual(seen[1], 2)


if __name__ == "__main__":
    unittest.main()
