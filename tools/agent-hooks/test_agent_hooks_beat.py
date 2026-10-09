"""agent-hooks 心跳部分（README「心跳」节，`contracts/agent.lane.v1`，nexus-core 契约 v2.18）的单测。

跑法同 test_agent_hooks.py：python3 -m pytest -q tools/agent-hooks（纯标准库）。
时钟与 sleep 全是注入的假货，不真睡；进程是否活着用假 pid 表，不碰真进程。只有「起伴随进程」那两条真起一个子进程。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import unittest
import unittest.mock as mock
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import claude_hook  # noqa: E402
import cockpit_client as cc  # noqa: E402
from test_agent_hooks import (  # noqa: E402
    RUN_SCRIPT,
    _IsolatedHomeMixin,
    _load_cockpit_run_module,
    _start_fixed_response_server,
    _start_server,
    _stop_server,
    _unused_port,
)

CLI = 4242  # 假的 Claude Code pid


class _Done(Exception):
    """假 sleep 跑够了轮数：把循环掐掉。"""


class _Clock:
    """假时钟 + 假 sleep：sleep(n) 只是把时钟拨 n 秒；`on_sleep(第几次)` 里可以改世界。"""

    def __init__(self, max_sleeps=10_000, on_sleep=None):
        self.now, self.sleeps, self.max_sleeps, self.on_sleep = 1_000_000.0, 0, max_sleeps, on_sleep

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps += 1
        self.now += seconds
        if self.on_sleep:
            self.on_sleep(self.sleeps)
        if self.sleeps >= self.max_sleeps:
            raise _Done


class _BeatMixin(_IsolatedHomeMixin):
    def setUp(self):
        super().setUp()
        os.environ["COCKPIT_BEAT"] = "companion"  # 基类缺省关着；tearDown 由基类还原
        self.procs = {CLI: (1, "claude", "born-1")}  # pid → (ppid, 进程名, 启动时刻)
        env = mock.patch.dict(os.environ)  # 发心跳的进程会往自己的环境里补 COCKPIT_URL：测完还原
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("COCKPIT_URL", None)
        self.calls: list[tuple] = []
        self.beat_result: object = (900, False)
        for target, attr, fake in (
            (claude_hook, "_proc", lambda pid: self.procs.get(pid)),
            (cc, "load_config", lambda: {"url": "http://cockpit.invalid", "token": "t", "beat": None}),
            (cc, "beat", self._beat),
            (cc, "stop_run", lambda _c, run_id, outcome, **_k: self.calls.append(("stop", run_id, outcome)) or {}),
        ):
            patcher = mock.patch.object(target, attr, fake)
            patcher.start()
            self.addCleanup(patcher.stop)

    SOURCE = "companion"

    def _beat(self, _config, run_id, _timeout=None, beat_source=None):
        self.assertEqual(beat_source, self.SOURCE)
        self.calls.append(("beat", run_id))
        result = self.beat_result(len(self.calls)) if callable(self.beat_result) else self.beat_result
        return result

    def _save(self, session, run_id, phase=None, label=None, **payload):
        """像 SessionStart 那样存状态（带上这个假 Claude Code 的 pid：monitor 靠它认会话）。"""
        with mock.patch.object(claude_hook, "_cli_pid", return_value=CLI):
            claude_hook._save_run_id(session, run_id, phase, label, payload)

    def _loop(self, clock, session="s1", cli=CLI):
        try:
            claude_hook.beat_loop(self.SOURCE, None if self.SOURCE == "monitor" else session, cli,
                                  sleep=clock.sleep, clock=clock.clock)
        except _Done:
            return False  # 被测试掐掉的
        return True  # 自己退出的


class _LoopCases:
    """同一个循环、两种起法：下面每一条在 companion 与 monitor 下各跑一遍，结果必须一样。"""

    def test_beats_at_once_then_at_the_server_interval(self):
        self._save("s1", "run-1", "idle", "garden")
        self.beat_result = (600, False)  # 服务端说 10 分钟一次
        clock = _Clock(max_sleeps=61)  # 61 × 30 秒 = 30.5 分钟
        self.assertFalse(self._loop(clock))
        self.assertEqual(self.calls, [("beat", "run-1")] * 4)  # 第 0、10、20、30 分钟
        self.assertEqual(clock.sleeps, 61)  # 每 30 秒醒一次看 CLI 还在不在

    def test_dead_cli_sends_stop_under_the_session_lock_and_exits(self):
        self._save("s1", "run-1", "working", "garden")
        held = []
        real_lock = claude_hook._session_lock

        def spy_lock(session_id, wait=None):
            held.append(session_id)
            return real_lock(session_id, wait)

        clock = _Clock(on_sleep=lambda n: n == 3 and self.procs.pop(CLI))
        with mock.patch.object(claude_hook, "_session_lock", spy_lock):
            self.assertTrue(self._loop(clock))
        self.assertEqual(self.calls, [("beat", "run-1"), ("stop", "run-1", "cancelled")])
        self.assertEqual(held, ["s1"])
        self.assertIsNone(claude_hook._read_state("s1"))
        self.assertEqual(clock.sleeps, 3)  # CLI 没了之后的下一次检查（≤ 30 秒）就收

    def test_reused_pid_counts_as_dead(self):
        self._save("s1", "run-1")
        clock = _Clock(on_sleep=lambda n: self.procs.update({CLI: (1, "bash", "born-2")}))  # 同一个 pid，另一个进程
        self.assertTrue(self._loop(clock))
        self.assertEqual(self.calls[-1], ("stop", "run-1", "cancelled"))

    def test_failed_stop_keeps_the_state_for_a_later_retry(self):
        self._save("s1", "run-1")
        clock = _Clock(on_sleep=lambda n: self.procs.clear())
        with mock.patch.object(cc, "stop_run", side_effect=cc.CockpitError("连不上")):
            self.assertTrue(self._loop(clock))
        self.assertEqual(claude_hook._read_run_id("s1"), "run-1")  # 同 SessionEnd：没报成就不删

    def test_exits_quietly_when_the_state_file_is_removed(self):
        self._save("s1", "run-1")
        clock = _Clock(max_sleeps=4, on_sleep=lambda n: n == 2 and claude_hook._delete_run_id("s1"))  # SessionEnd 收过尾了
        self.assertEqual(self._loop(clock), self.SOURCE == "companion")  # 伴随进程退出；monitor 留着等下一个会话
        self.assertEqual(self.calls, [("beat", "run-1")])  # 不再发 stop

    def test_failures_are_ignored_and_retried_soon(self):
        self._save("s1", "run-1")
        self.beat_result = lambda n: (cc.HEARTBEAT_MIN, False) if n <= 2 else (900, False)  # 前两下没发成
        clock = _Clock(max_sleeps=20)  # 10 分钟
        self.assertFalse(self._loop(clock))
        self.assertEqual(len(self.calls), 3)  # 0 秒、60 秒、120 秒；之后回到 15 分钟一次

    def test_closed_run_is_restarted_with_the_saved_cwd_and_then_declared_again(self):
        self._save("s1", "run-1", "idle", "garden", cwd="/home/u/garden", transcript_path="/nope.jsonl")
        self.beat_result = lambda n: (900, n == 1)  # 第一下：服务端说 run-1 已结束
        starts = []

        def fake_start(_config, _task, agent, tool, **kw):
            starts.append((agent, kw["phase"], kw["label"], kw["client_key"]))
            return {"runId": "run-2"}

        with mock.patch.object(cc, "start_run", fake_start):
            self.assertFalse(self._loop(_Clock(max_sleeps=2)))
        self.assertEqual([(a, p, l) for a, p, l, _k in starts], [("garden", "idle", "garden")])
        self.assertEqual(claude_hook._read_run_id("s1"), "run-2")
        self.assertEqual(self.calls, [("beat", "run-1"), ("beat", "run-2")])  # 新开的那条下一轮就声明心跳

    def test_closed_run_without_saved_cwd_is_left_for_the_next_hook_event(self):
        self._save("s1", "run-1")  # 升级前开的会话：状态里没有 cwd
        self.beat_result = (900, True)
        with mock.patch.object(cc, "start_run") as start:
            self.assertFalse(self._loop(_Clock(max_sleeps=2)))
        start.assert_not_called()


class CompanionLoopTests(_LoopCases, _BeatMixin, unittest.TestCase):
    def test_no_state_at_all_exits_without_a_single_request(self):
        self.assertTrue(self._loop(_Clock()))
        self.assertEqual(self.calls, [])

    def test_max_lifetime(self):
        self._save("s1", "run-1")
        clock = _Clock()
        self.assertTrue(self._loop(clock))
        self.assertEqual(clock.sleeps, claude_hook.BEAT_MAX_LIFETIME // claude_hook.BEAT_CHECK_SECONDS)
        self.assertEqual(len(self.calls), 24 * 4)  # 一天、15 分钟一次；到点自己退，不报 stop
        self.assertEqual(claude_hook._read_run_id("s1"), "run-1")

    def test_unknown_cli_falls_back_to_the_state_file_only(self):
        self._save("s1", "run-1")
        self.procs.clear()
        clock = _Clock(on_sleep=lambda n: n == 5 and claude_hook._delete_run_id("s1"))
        self.assertTrue(self._loop(clock, cli=0))
        self.assertEqual(self.calls, [("beat", "run-1")])

    def test_only_one_instance_per_session(self):
        self._save("s1", "run-1")
        self._save("s2", "run-2")
        first = claude_hook._beat_lock("s1")
        self.addCleanup(first.close)
        self.assertTrue(self._loop(_Clock()))  # 已经有一个拿着锁：直接退
        self.assertEqual(self.calls, [])
        self.assertFalse(self._loop(_Clock(max_sleeps=1), session="s2"))  # 别的会话不受影响
        self.assertEqual(self.calls, [("beat", "run-2")])


class MonitorLoopTests(_LoopCases, _BeatMixin, unittest.TestCase):
    """monitor：CLI 起它、CLI 管它的生死；会话号得自己按「同一个 Claude Code 进程」去认。"""

    SOURCE = "monitor"

    def test_attaches_to_the_newest_session_of_its_own_cli_and_no_other(self):
        with mock.patch.object(claude_hook, "_cli_pid", return_value=77):  # 另一个 Claude Code 窗口的会话
            claude_hook._save_run_id("other", "run-9", "idle", "x", {"cwd": "/x"})
        self._save("old", "run-1")
        old = claude_hook._state_file("old")
        os.utime(old, (old.stat().st_atime, old.stat().st_mtime - 60))  # 收尾没报成留下的旧状态
        self._save("new", "run-2")
        self.assertFalse(self._loop(_Clock(max_sleeps=2)))
        self.assertEqual(self.calls, [("beat", "run-2")])

    def test_waits_for_session_start_then_follows_the_cli_to_its_next_session(self):
        def world(n):
            if n == 2:
                self._save("s1", "run-1")  # SessionStart 钩子比 monitor 晚到
            if n == 5:
                claude_hook._delete_run_id("s1")  # /clear：旧会话收尾 …
                self._save("s2", "run-2")  # … 同一个 Claude Code 开了新会话
        self.assertFalse(self._loop(_Clock(max_sleeps=8, on_sleep=world)))
        self.assertEqual(self.calls, [("beat", "run-1"), ("beat", "run-2")])

    def test_never_beats_a_session_the_companion_already_has_and_takes_over_when_it_is_gone(self):
        self._save("s1", "run-1")
        companion = claude_hook._beat_lock("s1")
        clock = _Clock(max_sleeps=6, on_sleep=lambda n: n == 3 and companion.close())
        self.assertFalse(self._loop(clock))
        self.assertEqual(self.calls, [("beat", "run-1")])  # 前三轮一下没发；伴随进程没了才接手

    def test_without_the_hook_token_it_stands_aside_and_leaves_the_guard_free(self):
        self._save("s1", "run-1")  # 钩子带着令牌（auth: true）
        with mock.patch.object(cc, "load_config", lambda: {"url": "", "token": "", "beat": None}):  # monitor 拿不到插件设置
            self.assertFalse(self._loop(_Clock(max_sleeps=3)))
        self.assertEqual(self.calls, [])
        probe = claude_hook._beat_lock("s1")  # 没占锁：伴随进程可以来
        self.assertIsNotNone(probe)
        probe.close()

    def test_token_less_cockpit_works_with_the_url_the_hook_remembered(self):
        with mock.patch.object(cc, "load_config", lambda: {"url": "http://cockpit.invalid", "token": "", "beat": None}):
            self._save("s1", "run-1")  # 钩子没有令牌（单人部署的无令牌上报）
        seen = []
        with mock.patch.object(cc, "load_config", lambda: {"url": os.environ.get("COCKPIT_URL", ""), "token": "", "beat": None}), \
                mock.patch.object(cc, "beat", lambda config, *_a: seen.append(config["url"]) or (900, False)):
            self.assertFalse(self._loop(_Clock(max_sleeps=1)))
        self.assertEqual(seen, ["http://cockpit.invalid"])

    def test_exits_at_once_when_it_cannot_tell_which_process_is_the_cli(self):
        self._save("s1", "run-1")
        self.assertTrue(self._loop(_Clock(), cli=0))
        self.assertEqual(self.calls, [])


class ProcessLookupTests(_BeatMixin, unittest.TestCase):
    def _ancestors(self, *chain):
        """chain = 从钩子的父进程往上的 (pid, 进程名)。"""
        self.procs.clear()
        for (pid, name), parent in zip(chain, [pid for pid, _ in chain[1:]] + [1]):
            self.procs[pid] = (parent, name, f"born-{pid}")
        return mock.patch.object(os, "getppid", return_value=chain[0][0])

    def test_cli_is_the_ancestor_named_claude_else_the_first_non_shell(self):
        with self._ancestors((10, "sh"), (11, "claude"), (12, "zsh")):
            self.assertEqual(claude_hook._cli_pid(), 11)
        with self._ancestors((10, "sh"), (11, "node"), (12, "bash"), (13, "tmux: server")):
            self.assertEqual(claude_hook._cli_pid(), 11)
        with self._ancestors((10, "uv"), (11, "sh"), (12, "claude")):
            self.assertEqual(claude_hook._cli_pid(), 12)
        with self._ancestors((10, "bash")):
            self.assertEqual(claude_hook._cli_pid(), 0)  # 认不出：只看状态文件

    def test_alive_checks_pid_and_start_time(self):
        self.assertTrue(claude_hook._alive(CLI, "born-1"))
        self.assertFalse(claude_hook._alive(CLI, "born-0"))
        self.assertFalse(claude_hook._alive(9, "born-1"))
        self.assertTrue(claude_hook._alive(os.getpid(), None))  # 没有启动时刻的平台：pid 在就算

    @unittest.skipUnless(os.path.exists("/proc/self/stat"), "只在有 /proc 的系统上")
    def test_proc_reads_real_proc_stat(self):
        mock.patch.stopall()
        ppid, name, born = claude_hook._proc(os.getpid())
        self.assertEqual(ppid, os.getppid())
        self.assertTrue(name and born and born.isdigit())
        self.assertIsNone(claude_hook._proc(2 ** 22 + 12345))  # 超出 pid 上限：不存在


class ClientBeatTests(_IsolatedHomeMixin, unittest.TestCase):
    def test_beat_posts_no_body_and_never_raises(self):
        server, thread, log = _start_server(expect_token="good-token")
        try:
            config = {"url": f"http://127.0.0.1:{server.server_address[1]}", "token": "good-token"}
            # 假服务端不认识 heartbeat（= 老服务端）：404 → 照常间隔，不猛敲
            self.assertEqual(cc.beat(config, "run-1"), (900, False))
            req = log.requests[-1]
            self.assertEqual((req["method"], req["path"], req["body"]), ("POST", "/api/core/agents/run-1/heartbeat", None))
        finally:
            _stop_server(server, thread)
        down = {"url": f"http://127.0.0.1:{_unused_port()}", "token": "x"}
        self.assertEqual(cc.beat(down, "run-1", timeout=1), (cc.HEARTBEAT_MIN, False))  # 连不上：很快再试

    def test_beat_reads_interval_and_closed(self):
        with mock.patch.object(cc, "heartbeat_run", return_value={"applied": True, "reason": None, "heartbeatSeconds": 120}):
            self.assertEqual(cc.beat({}, "r"), (120, False))
        with mock.patch.object(cc, "heartbeat_run", return_value={"applied": False, "reason": "closed"}):
            self.assertEqual(cc.beat({}, "r"), (900, True))


@unittest.skipIf(os.name == "nt", "伴随进程只在 POSIX 上起")
class SpawnTests(_IsolatedHomeMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        os.environ["COCKPIT_BEAT"] = "companion"
        os.environ["COCKPIT_URL"] = f"http://127.0.0.1:{_unused_port()}"  # 连不上：真起的伴随进程发不出任何东西
        self.addCleanup(os.environ.pop, "COCKPIT_URL", None)

    def test_spawn_returns_at_once_is_detached_and_only_one_per_session(self):
        spawned = []
        real_popen = subprocess.Popen

        def spy(*a, **kw):
            spawned.append((a, kw, real_popen(*a, **kw)))
            return spawned[-1][2]

        claude_hook._save_run_id("s1", "run-1")
        with mock.patch.object(subprocess, "Popen", spy), mock.patch.object(claude_hook, "_cli_pid", return_value=0):
            t0 = time.monotonic()
            claude_hook._spawn_beat("s1", None)
            self.assertLess(time.monotonic() - t0, 2)
            argv, kw, child = spawned[0]
            try:
                self.assertEqual(argv[0][2:], ["--beat", "--source", "companion", "--session", "s1", "--cli", "0"])
                self.assertTrue(kw["start_new_session"])
                self.assertEqual((kw["stdin"], kw["stdout"], kw["stderr"]), (subprocess.DEVNULL,) * 3)
                self.assertNotIn("shell", kw)
                deadline = time.monotonic() + 10
                pidfile = claude_hook._state_file("s1").with_suffix(".beat")
                while time.monotonic() < deadline and pidfile.read_text() != f"{child.pid} companion":
                    time.sleep(0.05)
                self.assertEqual(pidfile.read_text(), f"{child.pid} companion")  # 伴随进程拿到了锁
                self.assertNotEqual(os.getsid(child.pid), os.getsid(0))
                claude_hook._spawn_beat("s1", None)  # 已经有一个：不再起
                claude_hook.handle_phase({"hook_event_name": "Stop", "session_id": "s1"}, cc.now_iso())
                self.assertEqual(len(spawned), 1)
            finally:
                child.kill()
                child.wait(timeout=10)
            claude_hook._spawn_beat("s1", None)  # 它死了（锁被内核放掉）：下一个事件补一个
            self.assertEqual(len(spawned), 2)
            spawned[1][2].kill()
            spawned[1][2].wait(timeout=10)

    def test_disabled_by_env_and_never_raises(self):
        claude_hook._save_run_id("s1", "run-1")
        with mock.patch.object(subprocess, "Popen", side_effect=OSError("no fork")) as popen:
            claude_hook._spawn_beat("s1", None)  # 起不来：吞掉
            self.assertEqual(popen.call_count, 1)
            for mode in ("off", "monitor"):  # 关了 / 只许 monitor：钩子不起伴随进程
                os.environ["COCKPIT_BEAT"] = mode
                claude_hook._spawn_beat("s1", None)
            self.assertEqual(popen.call_count, 1)

    def test_beat_entry_point_exits_zero_and_quietly_without_state(self):
        proc = subprocess.run([sys.executable, str(HERE / "claude_hook.py"), "--beat", "--session", "nobody"],
                              env=self._subprocess_env(), capture_output=True, timeout=20)
        self.assertEqual((proc.returncode, proc.stdout, proc.stderr), (0, b"", b""))
        bad = subprocess.run([sys.executable, str(HERE / "claude_hook.py"), "--beat", "--cli", "not-a-pid"],
                             env=self._subprocess_env(), capture_output=True, timeout=20)
        self.assertEqual(bad.returncode, 0)


class HookRestartTests(_IsolatedHomeMixin, unittest.TestCase):
    def test_phase_answered_closed_restarts_the_run_with_the_same_client_key(self):
        claude_hook._save_run_id("s1", "run-1", "idle", "garden")
        starts = []

        def fake_start(_config, _task, _agent, _tool, **kw):
            starts.append((kw["phase"], kw["client_key"]))
            return {"runId": "run-2"}

        event = {"hook_event_name": "UserPromptSubmit", "session_id": "s1", "cwd": "/home/u/garden"}
        with mock.patch.object(cc, "load_config", return_value={"url": "http://x", "token": "t"}), \
                mock.patch.object(cc, "phase_run", return_value={"applied": False, "reason": "closed"}), \
                mock.patch.object(cc, "start_run", fake_start):
            claude_hook.handle_phase(event, cc.now_iso())
        self.assertEqual([p for p, _k in starts], ["working"])
        self.assertEqual(claude_hook._read_state("s1")["runId"], "run-2")


class CockpitRunBeatTests(_IsolatedHomeMixin, unittest.TestCase):
    def test_beat_thread_beats_at_once_then_waits_the_server_interval_and_stops(self):
        module = _load_cockpit_run_module()
        waits, beats = [], []

        class FakeEvent:
            def wait(self, seconds):
                waits.append(seconds)
                return len(waits) > 3  # 第 4 次等待时命令结束了

        with mock.patch.object(cc, "beat", lambda _c, run_id, beat_source: beats.append((run_id, beat_source)) or (600, False)):
            module._beat_forever({}, "run-1", FakeEvent())
        self.assertEqual((waits, beats), ([0.0, 600, 600, 600], [("run-1", "wrapper")] * 3))

        waits.clear()
        with mock.patch.object(cc, "beat", return_value=(900, True)):  # 服务端说已结束：不再发
            module._beat_forever({}, "run-1", FakeEvent())
        self.assertEqual(waits, [0.0])

    def test_wrapped_command_gets_a_heartbeat_only_when_enabled(self):
        server, thread, log = _start_server(expect_token="good-token")
        try:
            env = self._subprocess_env(COCKPIT_URL=f"http://127.0.0.1:{server.server_address[1]}",
                                       COCKPIT_TOKEN="good-token", COCKPIT_BEAT="auto")
            cmd = [sys.executable, str(RUN_SCRIPT), "--", sys.executable, "-c", "import time; time.sleep(0.5)"]
            self.assertEqual(subprocess.run(cmd, env=env, timeout=30).returncode, 0)
            self.assertEqual([r["path"].rsplit("/", 1)[-1] for r in log.requests], ["start", "heartbeat", "stop"])
            log.requests.clear()
            self.assertEqual(subprocess.run(cmd, env={**env, "COCKPIT_BEAT": "off"}, timeout=30).returncode, 0)
            self.assertEqual([r["path"].rsplit("/", 1)[-1] for r in log.requests], ["start", "stop"])
        finally:
            _stop_server(server, thread)


class BoundsTests(_IsolatedHomeMixin, unittest.TestCase):
    """外面来的数（服务端响应、状态文件）当界限用之前都过同一个钳子；读多少字节有上限。"""

    def test_clamp_accepts_only_real_positive_ints(self):
        nan, inf = float("nan"), float("inf")
        for bad in (None, True, False, 900.0, 59.5, "900", "soon", [900], {"s": 900}, 0, -1, -10 ** 12, 1e12, nan, inf, -inf):
            got = cc.clamp_seconds(bad, 60, 3600, 900)
            self.assertTrue(type(got) is int and got == 900, bad)
        self.assertEqual([cc.clamp_seconds(v, 60, 3600, 900) for v in (1, 59, 60, 61, 900, 3600, 3601, 10 ** 12)],
                         [60, 60, 60, 61, 900, 3600, 3600, 3600])

    def test_heartbeat_interval_default_and_every_bypass_shape(self):
        self.assertEqual(cc.HEARTBEAT_SECONDS, 900)
        for response in ({}, {"heartbeatSeconds": None}, [900], None, "900", {"heartbeatSeconds": float("nan")},
                         {"heartbeatSeconds": float("inf")}, {"heartbeatSeconds": True}, {"heartbeatSeconds": "900"},
                         {"heartbeatSeconds": 900.0}, {"heartbeatSeconds": 0}, {"heartbeatSeconds": -1},
                         {"heartbeatSeconds": 1e12}):
            self.assertEqual(cc.heartbeat_interval(response), 900, response)
        self.assertEqual(cc.heartbeat_interval({"heartbeatSeconds": 5}), 60)
        self.assertEqual(cc.heartbeat_interval({"heartbeatSeconds": 10 ** 12}), 3600)

    def test_nan_from_the_wire_does_not_stop_the_beat(self):
        """原来的写法 `min(max(v, 60), 3600)` 对 NaN 不起作用：JSON 里一个 NaN 就让下一次心跳的时刻变成 NaN，
        `now >= NaN` 永远为假——心跳悄悄停了，运行半小时后被判失联。"""
        server, thread = _start_fixed_response_server(200, b'{"applied": true, "reason": null, "heartbeatSeconds": NaN}')
        try:
            config = {"url": f"http://127.0.0.1:{server.server_address[1]}", "token": ""}
            interval, closed = cc.beat(config, "run-1")
        finally:
            _stop_server(server, thread)
        self.assertEqual((interval, closed), (900, False))

    def test_interval_is_clamped_again_on_every_beat(self):
        answers = iter([{"heartbeatSeconds": 120}, {"heartbeatSeconds": float("nan")}, {"heartbeatSeconds": 5},
                        {"heartbeatSeconds": 10 ** 9}, {"heartbeatSeconds": "1"}, {}])
        with mock.patch.object(cc, "heartbeat_run", lambda *_a: next(answers)):
            self.assertEqual([cc.beat({}, "r")[0] for _ in range(6)], [120, 900, 60, 3600, 900, 900])

    def test_response_body_read_is_bounded(self):
        big = b'{"runId": "' + b"x" * (cc.MAX_RESPONSE_BYTES + 10) + b'"}'
        server, thread = _start_fixed_response_server(201, big)
        try:
            config = {"url": f"http://127.0.0.1:{server.server_address[1]}", "token": ""}
            with self.assertRaises(cc.CockpitError) as caught:
                cc.start_run(config, None, "a", "t")
            self.assertEqual(str(caught.exception), "响应格式不对")
            self.assertEqual(cc.beat(config, "run-1"), (cc.HEARTBEAT_MIN, False))  # 心跳同一条路：不抛
        finally:
            _stop_server(server, thread)

    def test_run_id_from_the_state_file_cannot_reach_another_endpoint(self):
        server, thread, log = _start_server(expect_token=None)
        try:
            config = {"url": f"http://127.0.0.1:{server.server_address[1]}", "token": ""}
            cc.beat(config, "x/../../timer/stop?a=1#")
            cc.stop_run(config, "../../planner/tasks/t_1", "done")
        finally:
            _stop_server(server, thread)
        self.assertEqual([r["path"] for r in log.requests],
                         ["/api/core/agents/x%2F..%2F..%2Ftimer%2Fstop%3Fa%3D1%23/heartbeat",
                          "/api/core/agents/..%2F..%2Fplanner%2Ftasks%2Ft_1/stop"])

    def test_state_file_values_are_untrusted(self):
        now = time.time()
        for bad in (None, "0", True, float("nan"), float("inf"), now + 10 ** 9, -1, now - claude_hook.MONITOR_GRACE_SECONDS - 1):
            self.assertFalse(claude_hook._in_grace(bad), bad)  # 坏值 = 不在宽限里：伴随进程照常补上
        self.assertTrue(claude_hook._in_grace(now))
        path = claude_hook._state_file("huge")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"runId": "run-1", "pad": "x" * claude_hook.STATE_MAX_BYTES}), encoding="utf-8")
        self.assertIsNone(claude_hook._read_state("huge"))  # 过大的不读
        self.assertIsNone(claude_hook._session_of(1, None))

    def test_token_is_never_sent_to_a_url_read_from_the_state_file(self):
        env = mock.patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        for key in ("COCKPIT_URL", "COCKPIT_TOKEN"):
            os.environ.pop(key, None)
        planted = {"runId": "run-1", "url": "http://evil.invalid", "auth": False}
        os.environ["COCKPIT_TOKEN"] = "good-token"  # 自己有令牌、没有地址：不许去状态文件里拿地址
        self.assertFalse(claude_hook._usable(planted))
        self.assertNotIn("COCKPIT_URL", os.environ)
        os.environ["COCKPIT_URL"] = "http://real.invalid"  # 自己有地址：状态文件里的那个不看
        self.assertTrue(claude_hook._usable(planted))
        self.assertEqual(cc.load_config()["url"], "http://real.invalid")
        del os.environ["COCKPIT_TOKEN"], os.environ["COCKPIT_URL"]
        self.assertTrue(claude_hook._usable(planted))  # 无令牌上报：只有地址会去那里，没有任何凭据
        self.assertEqual(cc.load_config(), {**cc.load_config(), "url": "http://evil.invalid", "token": ""})


class BeatModeTests(_IsolatedHomeMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        env = mock.patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        for key in ("COCKPIT_BEAT", "COCKPIT_URL", "COCKPIT_TOKEN", "CLAUDE_PLUGIN_ROOT"):
            os.environ.pop(key, None)

    def _write_config(self, **cfg):
        path = cc.user_dir("config") / cc.CONFIG_FILENAME
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cfg), encoding="utf-8")

    def test_env_beats_config_file_beats_auto_and_junk_is_auto(self):
        self.assertEqual(cc.beat_mode(), "auto")
        self._write_config(beat="monitor")
        self.assertEqual(cc.beat_mode(), "monitor")
        os.environ["COCKPIT_BEAT"] = "companion"
        self.assertEqual(cc.beat_mode(), "companion")
        os.environ["COCKPIT_BEAT"] = "sometimes"
        self.assertEqual(cc.beat_mode(), "auto")
        self._write_config(beat=["off"])
        del os.environ["COCKPIT_BEAT"]
        self.assertEqual(cc.beat_mode(), "auto")

    def test_plugin_options_sit_between_cockpit_env_and_the_config_file(self):
        self._write_config(url="http://file/", token="file-token")
        os.environ["CLAUDE_PLUGIN_OPTION_COCKPIT_URL"] = "http://plugin/"
        config = cc.load_config()
        self.assertEqual((config["url"], config["token"]), ("http://plugin", "file-token"))
        os.environ["COCKPIT_URL"] = "http://env/"
        os.environ["CLAUDE_PLUGIN_OPTION_COCKPIT_TOKEN"] = "plugin-token"
        config = cc.load_config()
        self.assertEqual((config["url"], config["token"]), ("http://env", "plugin-token"))

    @unittest.skipIf(os.name == "nt", "伴随进程只在 POSIX 上起")
    def test_auto_lets_the_plugin_monitor_go_first_then_falls_back_to_the_companion(self):
        claude_hook._save_run_id("s1", "run-1")
        fresh, stale = {"at": time.time()}, {"at": time.time() - claude_hook.MONITOR_GRACE_SECONDS - 1}
        with mock.patch.object(subprocess, "Popen") as popen:
            os.environ["CLAUDE_PLUGIN_ROOT"] = str(HERE)  # 从带 monitor 的插件里跑起来的钩子
            claude_hook._spawn_beat("s1", None)  # SessionStart
            claude_hook._spawn_beat("s1", fresh)  # 宽限之内的事件
            self.assertEqual(popen.call_count, 0)
            taken = claude_hook._beat_lock("s1")  # monitor 接手了
            claude_hook._spawn_beat("s1", stale)
            self.assertEqual(popen.call_count, 0)
            taken.close()  # monitor 没来 / 不能用（-p、Bedrock、老版本 CLI）
            claude_hook._spawn_beat("s1", stale)
            self.assertEqual(popen.call_count, 1)
            os.environ["COCKPIT_BEAT"] = "companion"  # 强制伴随进程：不等
            claude_hook._spawn_beat("s1", None)
            self.assertEqual(popen.call_count, 2)
            del os.environ["COCKPIT_BEAT"], os.environ["CLAUDE_PLUGIN_ROOT"]
            claude_hook._spawn_beat("s1", None)  # 不是插件（settings.json 里配的钩子）：auto = 伴随进程
            self.assertEqual(popen.call_count, 3)


@unittest.skipIf(os.name == "nt", "发心跳的进程只在 POSIX 上起")
class MonitorProcessTests(_IsolatedHomeMixin, unittest.TestCase):
    """真起一个 monitor 进程：它的 stdout / stderr 会被 Claude Code 送进会话，所以必须一个字都没有。"""

    def _monitor(self, mode, **popen):
        env = self._subprocess_env(COCKPIT_BEAT=mode, COCKPIT_URL=f"http://127.0.0.1:{_unused_port()}")
        return subprocess.Popen([sys.executable, "-W", "always", str(HERE / "claude_hook.py"), "--beat", "--source", "monitor"],
                                env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **popen)

    def test_attached_monitor_beats_into_the_void_and_prints_nothing(self):
        os.environ["COCKPIT_URL"] = "http://127.0.0.1:1"
        self.addCleanup(os.environ.pop, "COCKPIT_URL", None)
        claude_hook._save_run_id("s1", "run-1", "idle", "garden", {"cwd": "/tmp"})  # cli = 本测试进程的那个祖先
        child = self._monitor("monitor")
        try:
            pidfile = claude_hook._state_file("s1").with_suffix(".beat")
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline and not (pidfile.exists() and pidfile.read_text() == f"{child.pid} monitor"):
                time.sleep(0.05)
            self.assertEqual(pidfile.read_text(), f"{child.pid} monitor")  # 认出了会话、拿到了锁
            time.sleep(1.5)  # 第一下心跳（连不上）已经发过
        finally:
            child.kill()
        self.assertEqual(child.communicate(timeout=10), (b"", b""))

    def test_monitor_exits_at_once_and_silently_when_the_mode_says_no(self):
        for mode in ("off", "companion"):
            child = self._monitor(mode)
            self.assertEqual(child.communicate(timeout=20), (b"", b""))
            self.assertEqual(child.returncode, 0)

    def test_bad_arguments_stay_silent_too(self):
        proc = subprocess.run([sys.executable, str(HERE / "claude_hook.py"), "--beat", "--source", "monitor", "--bogus"],
                              env=self._subprocess_env(), capture_output=True, timeout=20)
        self.assertEqual((proc.returncode, proc.stdout, proc.stderr), (0, b"", b""))


class PluginFilesTests(unittest.TestCase):
    """插件只是个薄壳：三个 JSON 把同一个 claude_hook.py 接到 Claude Code 上。CI 里没有 `claude plugin validate`，
    这里按官方文档（plugins-reference）查形状：能解析、必填键在、严格对象里没有多余的键、路径指向真文件。"""

    REPO = HERE.parent.parent

    def _json(self, path):
        return json.loads(path.read_text(encoding="utf-8"))

    def test_manifest_and_marketplace(self):
        manifest = self._json(HERE / ".claude-plugin" / "plugin.json")
        self.assertRegex(manifest["name"], r"^[a-z0-9][a-z0-9-]*$")
        self.assertNotRegex(manifest["name"], r"claude|anthropic")  # 保留字
        option_keys = {"type", "title", "description", "required", "default", "options", "multiple", "sensitive", "min", "max"}
        for key, option in manifest["userConfig"].items():
            self.assertRegex(key, r"^[A-Za-z_][A-Za-z0-9_]*$")
            self.assertTrue({"type", "title", "description"} <= set(option) <= option_keys, key)
        self.assertTrue(manifest["userConfig"]["cockpit_token"]["sensitive"])
        self.assertNotIn("required", manifest["userConfig"]["cockpit_token"])  # 令牌选填：单人部署可以无令牌上报
        # 钩子进程拿到的是 CLAUDE_PLUGIN_OPTION_<大写键>：与 cockpit_client.load_config 读的名字对得上
        self.assertEqual({f"CLAUDE_PLUGIN_OPTION_{k.upper()}" for k in manifest["userConfig"]},
                         {"CLAUDE_PLUGIN_OPTION_COCKPIT_URL", "CLAUDE_PLUGIN_OPTION_COCKPIT_TOKEN"})

        market = self._json(self.REPO / ".claude-plugin" / "marketplace.json")
        self.assertTrue(market["name"] and market["owner"]["name"])
        [entry] = market["plugins"]
        self.assertEqual(entry["name"], manifest["name"])
        self.assertTrue(entry["source"].startswith("./"))
        self.assertEqual((self.REPO / entry["source"]).resolve(), HERE)

    def test_hooks_wire_every_event_the_hook_handles_to_the_one_script(self):
        hooks = self._json(HERE / "hooks" / "hooks.json")["hooks"]  # 文件形式必须有顶层 "hooks"
        self.assertEqual(set(hooks), {"SessionStart", "SessionEnd", "UserPromptSubmit", "PermissionRequest", "Notification",
                                      "PostToolUse", "PostToolUseFailure", "Stop", "StopFailure"})
        for event, matchers in hooks.items():
            [handler] = [h for m in matchers for h in m["hooks"]]
            self.assertEqual((handler["type"], handler["command"]), ("command", 'python3 "${CLAUDE_PLUGIN_ROOT}"/claude_hook.py'))
            self.assertEqual(handler.get("async", False), event not in ("SessionStart", "SessionEnd"), event)
        self.assertEqual(hooks["SessionEnd"][0]["hooks"][0]["timeout"], 5)

    def test_monitor_entry_is_strict_and_runs_the_same_script_in_monitor_mode(self):
        [monitor] = self._json(HERE / "monitors" / "monitors.json")  # 缺省位置 monitors/monitors.json，内容是数组
        self.assertTrue({"name", "command", "description"} <= set(monitor) <= {"name", "command", "description", "when"})
        self.assertEqual(monitor["command"], 'python3 "${CLAUDE_PLUGIN_ROOT}"/claude_hook.py --beat --source monitor')
        self.assertNotIn("user_config", monitor["command"])  # monitor 的命令不许引用插件设置
        self.assertEqual(monitor.get("when", "always"), "always")
        self.assertTrue((HERE / "claude_hook.py").is_file() and (HERE / "cockpit_client.py").is_file())


if __name__ == "__main__":
    unittest.main()
