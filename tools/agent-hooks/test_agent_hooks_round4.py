"""PR #96 静态复审三条 MED 的回归：takeover 后必有发心跳的、CLI 身份（pid + 启动时刻）取自同一次观察、
请求绑定开 run 的那个服务器。跑法同 test_agent_hooks.py：python3 -m pytest -q tools/agent-hooks。"""

from __future__ import annotations

import io
import contextlib
import os
import sys
import unittest
import unittest.mock as mock
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import claude_hook  # noqa: E402
import cockpit_client as cc  # noqa: E402
from test_agent_hooks_beat import CLI, _BeatMixin, _Clock, _Done  # noqa: E402

A_URL, A_TOKEN = "http://user:pw-secret@a.invalid:8080/cockpit/", "tok-A"
B_URL, B_TOKEN = "http://b.invalid:9090", "tok-B"
EVENT = {"session_id": "s1", "cwd": "/home/u/garden", "hook_event_name": "SessionStart"}


class TakeoverAlwaysHasABeaterTests(_BeatMixin, unittest.TestCase):
    def test_spawn_probe_that_sees_a_free_lock_still_passes_wait(self):
        """A 的发心跳进程验过归属、还没拿 `.beat`；CLI B 恢复会话并提交新一代，B 的探测看到锁空闲。
        A 随后拿锁、发现换了主人退出；B 的伴随进程必须带 `--wait` 才能接着发。"""
        self._save("s1", "run-1", "idle")
        gen_a = claude_hook._read_state("s1")["gen"]
        b_cli = CLI + 1
        self.procs[b_cli] = (1, "claude", "born-2")
        spawned: list[list[str]] = []
        real_lock, paused = claude_hook._beat_lock, []

        def lock_with_b_interleaved(session_id):
            if not paused:  # A 头一次来拿锁：在它拿到之前，B 完整地恢复会话
                paused.append(1)
                with mock.patch.object(claude_hook, "_cli_find", lambda: (b_cli, "born-2")), \
                        mock.patch.object(cc, "start_run", lambda *_a, **_k: {"runId": "run-1"}), \
                        mock.patch("subprocess.Popen", lambda argv, **_k: spawned.append(argv)):
                    claude_hook.handle_session_start(EVENT)
            return real_lock(session_id)

        with mock.patch.object(claude_hook, "_beat_lock", lock_with_b_interleaved):
            end_a = claude_hook._beat_session("s1", "companion", CLI, "born-1", [5], lambda _s: None, lambda: 0.0, gen=gen_a)
        self.assertEqual(end_a, claude_hook.END_OTHER)  # A 发现换了主人退出
        self.assertEqual(len(spawned), 1)
        argv = spawned[0]
        self.assertIn("--wait", argv)
        gen_b = argv[argv.index("--gen") + 1]
        self.assertNotEqual(gen_b, gen_a)
        self.calls.clear()  # B 的伴随进程现在跑：必须拿到锁并发出心跳
        clock = _Clock(max_sleeps=2)
        with self.assertRaises(_Done):
            claude_hook._beat_session("s1", "companion", b_cli, "born-2", [5], clock.sleep, clock.clock, gen=gen_b, wait="--wait" in argv)
        self.assertIn(("beat", "run-1"), self.calls)

    def test_occupied_beat_always_spawns_a_waiter_even_for_a_live_same_generation_holder(self):
        self._save("s1", "run-1", "idle")
        state = claude_hook._read_state("s1")
        holder = claude_hook._beat_lock("s1")
        self.addCleanup(holder.close)
        holder.write(f"{os.getpid()} companion {state['gen']}")  # 本代、pid 活着——也不能信它不会马上退
        holder.flush()
        with mock.patch("subprocess.Popen") as popen:
            claude_hook._spawn_beat("s1", state, wait=True, probe=(CLI, "born-1"))
            self.assertIn("--wait", popen.call_args.args[0])
            popen.reset_mock()
            claude_hook._spawn_beat("s1", state, probe=(CLI, "born-1"))  # 非 wait：仍不起
            self.assertFalse(popen.called)

    def test_same_generation_holder_exits_right_after_the_spawn_decision_and_the_waiter_beats(self):
        self._save("s1", "run-1", "idle")
        gen = claude_hook._read_state("s1")["gen"]
        holder = claude_hook._beat_lock("s1")
        self.addCleanup(holder.close)
        holder.write(f"{os.getpid()} companion {gen}")
        holder.flush()
        clock = _Clock(max_sleeps=4, on_sleep=lambda n: n == 2 and holder.close())  # 老的（同代）在 spawn 之后退出放锁
        with self.assertRaises(_Done):
            claude_hook._beat_session("s1", "companion", CLI, "born-1", [5], clock.sleep, clock.clock, gen=gen, wait=True)
        self.assertIn(("beat", "run-1"), self.calls)

    def test_only_one_waiter_at_a_time(self):
        self._save("s1", "run-1", "idle")
        gen = claude_hook._read_state("s1")["gen"]
        holder = claude_hook._beat_lock("s1")
        self.addCleanup(holder.close)
        first_sleeps, others = [], []

        def first_sleep(_s):  # 第一个等待者睡着时，再来 20 次 SessionStart 的等待者
            first_sleeps.append(1)
            if len(first_sleeps) == 1:
                for _ in range(20):
                    others.append(claude_hook._beat_session("s1", "companion", CLI, "born-1", [5], lambda _x: others.append("slept"), lambda: 0.0, gen=gen, wait=True))
            if len(first_sleeps) == 3:
                holder.close()

        clock = _Clock(max_sleeps=3)
        with self.assertRaises(_Done):
            claude_hook._beat_session("s1", "companion", CLI, "born-1", [5], lambda s: (first_sleep(s), clock.sleep(s)), clock.clock, gen=gen, wait=True)
        self.assertEqual(others, [claude_hook.END_OTHER] * 20)  # 全部立刻退出，没睡过
        # 等完放了 .wait：下一个 wait 进程可以再来
        w = claude_hook._beat_lock("s1", ".wait")
        self.assertIsNotNone(w)
        w.close()


class CliIdentityFromOneObservationTests(_BeatMixin, unittest.TestCase):
    def _flapping_proc(self):
        births = iter(f"b{n}" for n in range(1, 100))
        return lambda pid: (1, "claude", next(births)) if pid == 10 else None  # 每次观察 pid 10 都是另一个进程

    def test_pid_and_birth_come_from_the_same_proc_call(self):
        with mock.patch.object(claude_hook, "_proc", self._flapping_proc()), mock.patch.object(os, "getppid", return_value=10):
            self.assertEqual(claude_hook._cli_probe(), (10, "b1"))

    def test_override_path_too(self):
        with mock.patch.object(claude_hook, "_proc", self._flapping_proc()), mock.patch.dict(os.environ, {"COCKPIT_CLI_PID": "10"}):
            self.assertEqual(claude_hook._cli_probe(), (10, "b1"))

    def test_monitor_entry_takes_birth_from_the_same_observation(self):
        with mock.patch.object(claude_hook, "_proc", self._flapping_proc()), mock.patch.object(os, "getppid", return_value=10), \
                mock.patch.object(claude_hook, "beat_loop") as loop, mock.patch.dict(os.environ, {"COCKPIT_BEAT": "monitor"}), \
                mock.patch.object(claude_hook, "_silence"):
            claude_hook._beat_main(["--beat", "--source", "monitor"])
        self.assertEqual((loop.call_args.args[2], loop.call_args.kwargs["born"]), (10, "b1"))


class ServerBindingTests(_BeatMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.cfg = {"url": A_URL, "token": A_TOKEN, "beat": None}
        self.sent: list[tuple] = []  # (kind, url, token, runId)
        self.run_seq = 0
        for attr, fake in (
            ("load_config", lambda: dict(self.cfg)),
            ("start_run", self._start),
            ("phase_run", lambda c, run_id, *_a, **_k: self.sent.append(("phase", c["url"], c["token"], run_id)) or {"applied": True}),
            ("stop_run", lambda c, run_id, *_a, **_k: self.sent.append(("stop", c["url"], c["token"], run_id)) or {}),
            ("beat", lambda c, run_id, *_a, **_k: self.sent.append(("beat", c["url"], c["token"], run_id)) or (900, False)),
        ):
            p = mock.patch.object(cc, attr, fake)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(claude_hook, "_cli_find", lambda: (CLI, "born-1"))
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch("subprocess.Popen")
        p.start()
        self.addCleanup(p.stop)

    def _start(self, c, *_a, **_k):
        self.sent.append(("start", c["url"], c["token"], None))
        self.run_seq += 1
        return {"runId": f"run-{self.run_seq}"}

    def _switch_to_b(self):
        self.cfg.update(url=B_URL, token=B_TOKEN)

    def _begin(self):
        claude_hook.handle_session_start(EVENT)
        self.sent.clear()

    def _to_b(self):
        return [s for s in self.sent if s[1] == B_URL]

    def _all_paired(self):
        self.assertTrue(all((s[1], s[2]) in ((A_URL, A_TOKEN), (B_URL, B_TOKEN)) for s in self.sent), self.sent)

    def test_state_records_a_credential_free_server_identity(self):
        self._begin()
        raw = claude_hook._state_file("s1").read_text()
        self.assertEqual(claude_hook._read_state("s1")["url"], "http://a.invalid:8080/cockpit")
        self.assertNotIn("pw-secret", raw)
        self.assertNotIn(A_TOKEN, raw)

    def test_config_switch_between_start_and_state_commit_records_the_server_that_got_the_start(self):
        def flipping_start(c, *a, **k):
            r = self._start(c, *a, **k)
            self._switch_to_b()  # start 发出之后配置变了
            return r
        with mock.patch.object(cc, "start_run", flipping_start):
            claude_hook.handle_session_start(EVENT)
        self.assertEqual(claude_hook._read_state("s1")["url"], "http://a.invalid:8080/cockpit")

    def test_phase_after_switch_sends_nothing(self):
        self._begin()
        self._switch_to_b()
        claude_hook.handle_phase({**EVENT, "hook_event_name": "UserPromptSubmit"}, cc.now_iso())
        self.assertEqual(self.sent, [])
        self.assertIsNotNone(claude_hook._read_state("s1"))

    def test_relabel_after_switch_sends_nothing(self):
        self._begin()
        self._switch_to_b()
        claude_hook._relabel_locked(EVENT, "s1", "new title", claude_hook._read_state("s1"))
        self.assertEqual(self.sent, [])

    def test_session_end_after_switch_sends_nothing_deletes_state_and_warns_once_without_credentials(self):
        self._begin()
        self._switch_to_b()
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            claude_hook.handle_session_end({**EVENT, "hook_event_name": "SessionEnd", "reason": "other"})
        self.assertEqual(self.sent, [])
        self.assertIsNone(claude_hook._read_state("s1"))
        self.assertEqual(len(err.getvalue().strip().splitlines()), 1)
        for secret in ("pw-secret", A_TOKEN, B_TOKEN, "user:"):
            self.assertNotIn(secret, err.getvalue())

    def test_heartbeat_after_switch_sends_nothing(self):
        self._begin()
        self._switch_to_b()
        self.assertIsNone(claude_hook._beat_once("s1", claude_hook._read_state("s1"), "companion"))
        self.assertEqual(self.sent, [])

    def test_beater_death_stop_after_switch_sends_nothing_and_deletes_state(self):
        self._begin()
        gen = claude_hook._read_state("s1")["gen"]

        def on_sleep(_n):
            self._switch_to_b()
            self.procs.pop(CLI)  # CLI 死了
        clock = _Clock(max_sleeps=5, on_sleep=on_sleep)
        claude_hook._beat_session("s1", "companion", CLI, "born-1", [5], clock.sleep, clock.clock, gen=gen)
        self.assertEqual(self._to_b(), [])
        self.assertNotIn("stop", [s[0] for s in self.sent])
        self.assertIsNone(claude_hook._read_state("s1"))

    def test_old_format_state_without_a_server_keeps_current_behaviour(self):
        self._begin()
        st = claude_hook._read_state("s1")
        st.pop("url")
        claude_hook._write_state("s1", st)
        self._switch_to_b()
        claude_hook.handle_phase({**EVENT, "hook_event_name": "UserPromptSubmit"}, cc.now_iso())
        claude_hook.handle_session_end({**EVENT, "hook_event_name": "SessionEnd", "reason": "other"})
        self.assertEqual([s[0] for s in self.sent], ["phase", "stop"])
        self.assertEqual({s[1] for s in self.sent}, {B_URL})

    def test_new_session_start_after_switch_runs_on_b_and_tokens_stay_paired(self):
        self._begin()
        self._switch_to_b()
        claude_hook.handle_session_start(EVENT)
        claude_hook.handle_phase({**EVENT, "hook_event_name": "UserPromptSubmit"}, cc.now_iso())
        self.assertEqual([(s[0], s[1]) for s in self.sent], [("start", B_URL), ("phase", B_URL)])
        self._all_paired()
        self.assertEqual(claude_hook._read_state("s1")["url"], "http://b.invalid:9090")


if __name__ == "__main__":
    unittest.main()
