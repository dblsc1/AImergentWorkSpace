"""Codex 复审（PR #90 心跳功能）的回归测试：错误体受总时限管、进程身份与归属、备用伴随进程、
有限寿命、锁失败即关闭、状态目录文件的安全打开、URL 与令牌的配对。

跑法同 test_agent_hooks.py：python3 -m pytest -q tools/agent-hooks（纯标准库）。时钟 / sleep / 进程表全是注入的假货；
真 socket 只有「慢吐错误体」那一条（本机假服务器，硬时限）。
"""

from __future__ import annotations

import json
import os
import socket
import threading
import time
import unittest
import unittest.mock as mock
from pathlib import Path

HERE = Path(__file__).resolve().parent

import claude_hook  # noqa: E402
import cockpit_client as cc  # noqa: E402
from test_agent_hooks import _IsolatedHomeMixin  # noqa: E402
from test_agent_hooks_beat import CLI, _BeatMixin, _Clock, _Done  # noqa: E402


# ───────────────────────────────────────────── 4：错误体受总时限管


class _TrickleServer:
    """回 500 的头，然后一个字节一个字节地滴响应体（每次都在 socket 超时之内，攒起来远超总时限）。"""

    def __init__(self):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(5)
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.sock.getsockname()[1]}"

    def _serve(self):
        while not self.stop.is_set():
            try:
                self.sock.settimeout(0.2)
                conn, _ = self.sock.accept()
            except OSError:
                continue
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn):
        try:
            conn.settimeout(2)
            conn.recv(65536)
            conn.sendall(b"HTTP/1.1 500 Oops\r\nContent-Length: 1000\r\n\r\n")
            for _ in range(100):
                if self.stop.is_set():
                    break
                conn.sendall(b"x")
                time.sleep(0.1)
        except OSError:
            pass
        finally:
            conn.close()

    def close(self):
        self.stop.set()
        self.sock.close()
        self.thread.join(timeout=3)


class ErrorBodyDeadlineTests(unittest.TestCase):
    def test_trickling_error_body_cannot_outlive_the_total_deadline(self):
        server = _TrickleServer()
        self.addCleanup(server.close)
        t0 = time.monotonic()
        with self.assertRaises(cc.CockpitError) as caught:
            cc._request({"url": server.url, "token": ""}, "POST", "/api/core/agents/start", {}, 0.5)
        self.assertLess(time.monotonic() - t0, 2.5)  # 总时限 0.5s；以前错误体在时限之外读，要滴满 10 秒
        self.assertEqual(str(caught.exception), "超时")

    def test_a_beat_is_skipped_while_the_previous_request_is_still_stuck(self):
        server = _TrickleServer()
        self.addCleanup(server.close)
        config = {"url": server.url, "token": ""}
        before = cc.stuck_requests()
        self.assertEqual(cc.beat(config, "run_0123abcdef01", 0.3)[1], False)  # 这一下超时，线程还卡在后台
        self.assertEqual(cc.stuck_requests(), before + 1)
        with mock.patch.object(cc, "heartbeat_run") as sent:
            self.assertEqual(cc.beat(config, "run_0123abcdef01", 0.3), (cc.HEARTBEAT_MIN, False))
            sent.assert_not_called()  # 不叠第二个线程 / 连接
        server.stop.set()
        deadline = time.monotonic() + 5
        while cc.stuck_requests() > before and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertEqual(cc.stuck_requests(), before)  # 后台线程退出后恢复


# ───────────────────────────────────────────── 5 / 6 / 8：身份、归属、寿命


class _CompanionCase(_BeatMixin, unittest.TestCase):
    OTHER = 77

    def _resume_elsewhere(self, session="s1", run_id="run-2"):
        """另一个活着的 CLI 恢复了这个会话：SessionStart 换了状态（新归属号 + 新 CLI 身份）。"""
        self.procs[self.OTHER] = (1, "claude", "born-77")
        with mock.patch.object(claude_hook, "_cli_pid", return_value=self.OTHER):
            claude_hook._save_run_id(session, run_id, "idle", "garden", {})

    def test_old_companion_leaves_a_resumed_sessions_new_run_alone(self):
        self._save("s1", "run-1")
        done = []

        def world(n):
            if n == 1:
                self._resume_elsewhere()
                self.procs.pop(CLI)  # 原来那个 CLI 退出了
            done.append(n)

        self.assertTrue(self._loop(_Clock(on_sleep=world)))  # 自己退出（放手），不是被测试掐断
        self.assertNotIn(("stop", "run-2", "cancelled"), self.calls)
        self.assertEqual([c for c in self.calls if c[0] == "stop"], [])
        self.assertEqual(claude_hook._read_run_id("s1"), "run-2")  # 新 CLI 的运行没被停、状态没被删

    def test_ownership_is_checked_again_under_the_lock_before_stopping(self):
        self._save("s1", "run-1")
        real_alive = claude_hook._alive

        def dead_and_resumed(pid, born):
            if pid == CLI and not getattr(self, "_swapped", False):
                self._swapped = True
                self._resume_elsewhere()  # 读状态之后、持锁之前被恢复
                return False
            return real_alive(pid, born)

        with mock.patch.object(claude_hook, "_alive", dead_and_resumed):
            self.assertTrue(self._loop(_Clock(max_sleeps=3)))
        self.assertEqual([c for c in self.calls if c[0] == "stop"], [])
        self.assertEqual(claude_hook._read_run_id("s1"), "run-2")

    def test_same_owner_still_gets_its_stop(self):
        self._save("s1", "run-1")
        self.assertTrue(self._loop(_Clock(on_sleep=lambda n: self.procs.pop(CLI, None), max_sleeps=3)))
        self.assertEqual([c for c in self.calls if c[0] == "stop"], [("stop", "run-1", "cancelled")])

    def test_cli_without_a_birth_time_is_never_supervised_or_declared(self):
        self._save("s1", "run-1")
        clock = _Clock()
        claude_hook.beat_loop("companion", "s1", CLI, sleep=clock.sleep, clock=clock.clock, born=None)
        self.assertEqual((self.calls, clock.sleeps), ([], 0))

    def test_companion_needs_the_launchers_birth_time(self):
        self._save("s1", "run-1")
        with mock.patch.object(claude_hook, "beat_loop") as loop:
            claude_hook._beat_main(["--beat", "--source", "companion", "--session", "s1", "--cli", str(CLI)])
            loop.assert_not_called()  # 没有钩子给的启动时刻：不自己事后去取
            claude_hook._beat_main(["--beat", "--source", "companion", "--session", "s1", "--cli", str(CLI), "--born", "born-1",
                                    "--gen", "g", "--standby"])
            loop.assert_called_once_with("companion", "s1", CLI, born="born-1", gen="g", standby=True)


class MonitorLifetimeTests(_CompanionCase):
    SOURCE = "monitor"

    def test_monitor_without_a_session_still_exits_after_the_lifetime(self):
        clock = _Clock()  # 活着的 CLI、没有任何会话状态：以前永远转
        self.assertTrue(self._loop(clock))
        self.assertEqual(clock.sleeps, claude_hook.BEAT_MAX_LIFETIME // claude_hook.BEAT_CHECK_SECONDS)

    def test_monitor_with_a_session_shares_one_lifetime_budget(self):
        self._save("s1", "run-1")
        clock = _Clock()
        self.assertTrue(self._loop(clock))
        self.assertLessEqual(clock.sleeps, claude_hook.BEAT_MAX_LIFETIME // claude_hook.BEAT_CHECK_SECONDS)
        self.assertGreater(len(self.calls), 0)

    def test_monitor_rechecks_the_cli_identity_every_round(self):
        self._save("s1", "run-1")
        ident = {"v": (CLI, "born-1")}

        def loop_with_identity(clock):
            try:
                claude_hook.beat_loop("monitor", None, CLI, sleep=clock.sleep, clock=clock.clock, identify=lambda: ident["v"])
            except _Done:
                return False
            return True

        def swap(n):
            if n == 2:
                ident["v"] = (4000, "born-x")  # 祖先变了：不是原来那个 CLI

        self.assertTrue(loop_with_identity(_Clock(on_sleep=swap)))
        self.assertEqual([c for c in self.calls if c[0] == "stop"], [])
        self.assertLessEqual(len(self.calls), 2)


# ───────────────────────────────────────────── 7：备用伴随进程


class StandbyTests(_CompanionCase):
    def test_standby_beats_itself_when_no_monitor_takes_over(self):
        self._save("s1", "run-1")
        clock = _Clock(max_sleeps=40)
        try:
            claude_hook.beat_loop("companion", "s1", CLI, sleep=clock.sleep, clock=clock.clock, born="born-1", standby=True)
        except _Done:
            pass
        self.assertEqual(self.calls[0], ("beat", "run-1"))  # 宽限一过就自己发——不靠以后的钩子事件
        self.assertGreaterEqual(clock.sleeps, claude_hook.MONITOR_GRACE_SECONDS // claude_hook.STANDBY_POLL)

    def test_standby_steps_aside_when_the_monitor_takes_the_lock(self):
        self._save("s1", "run-1")
        monitor = claude_hook._beat_lock("s1")
        self.addCleanup(monitor.close)
        clock = _Clock()
        claude_hook.beat_loop("companion", "s1", CLI, sleep=clock.sleep, clock=clock.clock, born="born-1", standby=True)
        self.assertEqual((self.calls, clock.sleeps), ([], 0))  # 一下都没发：exactly one beater

    def test_standby_exits_when_the_session_ends_meanwhile(self):
        self._save("s1", "run-1")
        clock = _Clock(on_sleep=lambda n: claude_hook._delete_run_id("s1"))
        claude_hook.beat_loop("companion", "s1", CLI, sleep=clock.sleep, clock=clock.clock, born="born-1", standby=True)
        self.assertEqual((self.calls, clock.sleeps), ([], 1))

    def test_only_one_standby_waits_at_a_time(self):
        self._save("s1", "run-1")
        other = claude_hook._beat_lock("s1", ".standby")
        self.addCleanup(other.close)
        self.assertFalse(claude_hook._standby_wait("s1", _Clock().sleep))


# ───────────────────────────────────────────── 9：锁超时失败即关闭


class LockFailClosedTests(_IsolatedHomeMixin, unittest.TestCase):
    def test_relabel_does_nothing_when_the_lock_cannot_be_taken(self):
        claude_hook._write_state("s1", {"runId": "run-1", "lastPhase": "idle", "label": "garden"})
        with mock.patch.object(claude_hook, "LOCK_WAIT", 0.05), mock.patch.object(claude_hook, "_start") as start:
            with claude_hook._session_lock("s1") as held:  # 别人（SessionEnd / 另一个钩子）占着锁
                self.assertTrue(held)
                claude_hook._relabel({"cwd": "/x"}, "s1", "working", None, claude_hook._read_state("s1"))
            start.assert_not_called()  # 不改名、不重开：留给下一个事件
        self.assertEqual(claude_hook._read_state("s1")["runId"], "run-1")

    def test_phase_state_write_is_skipped_without_the_lock_but_session_end_still_stops(self):
        claude_hook._write_state("s1", {"runId": "run-1", "lastPhase": "idle"})
        with mock.patch.object(claude_hook, "LOCK_WAIT", 0.05), \
                mock.patch.object(cc, "stop_run", return_value={}) as stop, \
                mock.patch.object(cc, "load_config", return_value={"url": "http://x.invalid", "token": ""}):
            with claude_hook._session_lock("s1"):
                claude_hook.handle_session_end({"session_id": "s1", "hook_event_name": "SessionEnd"})
            stop.assert_called_once()  # 收尾不因拿不到锁而放弃
        self.assertIsNone(claude_hook._read_run_id("s1"))


# ───────────────────────────────────────────── 10：状态目录里的文件当不可信


@unittest.skipIf(os.name == "nt", "属主 / 符号链接 / FIFO 是 POSIX 的事")
class StateFileSafetyTests(_IsolatedHomeMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        claude_hook._ensure_dir(cc.user_dir("state"))

    def test_fifo_in_place_of_the_state_file_does_not_block_the_hook(self):
        path = claude_hook._state_file("s1")
        os.mkfifo(path)
        result = []
        t = threading.Thread(target=lambda: result.append(claude_hook._read_state("s1")), daemon=True)
        t.start()
        t.join(3)
        blocked = t.is_alive()
        if blocked:  # 放它出来，别漏线程
            fd = os.open(path, os.O_WRONLY)
            os.close(fd)
            t.join(3)
        self.assertFalse(blocked)  # 以前 open() 在 FIFO 上一直阻塞
        self.assertEqual(result, [None])

    def test_symlinked_state_and_predictable_temp_names_never_touch_other_files(self):
        victim = Path(self._home_tmpdir.name) / "victim.txt"
        victim.write_text("precious")
        path = claude_hook._state_file("s1")
        old_style_tmp = path.with_suffix(f".{os.getpid()}.tmp")  # 以前的临时名：可预测
        old_style_tmp.symlink_to(victim)
        claude_hook._write_state("s1", {"runId": "run-1"})
        self.assertEqual(victim.read_text(), "precious")
        self.assertEqual(claude_hook._read_state("s1")["runId"], "run-1")
        path.unlink()
        path.symlink_to(victim)  # 状态文件本身是链接：不跟
        self.assertIsNone(claude_hook._read_state("s1"))
        self.assertEqual(victim.read_text(), "precious")

    def test_symlinked_lock_files_are_refused(self):
        victim = Path(self._home_tmpdir.name) / "victim2.txt"
        victim.write_text("precious")
        claude_hook._state_file("s1").with_suffix(".beat").symlink_to(victim)
        self.assertIsNone(claude_hook._beat_lock("s1"))
        claude_hook._state_file("s1").with_suffix(".lock").symlink_to(victim)
        with claude_hook._session_lock("s1") as held:
            self.assertFalse(held)
        self.assertEqual(victim.read_text(), "precious")

    def test_group_or_world_writable_state_dir_is_not_used(self):
        state_dir = cc.user_dir("state")
        os.chmod(state_dir, 0o770)
        claude_hook._write_state("s1", {"runId": "run-1"})
        self.assertEqual(list(state_dir.iterdir()), [])  # 没写
        os.chmod(state_dir, 0o700)
        claude_hook._write_state("s1", {"runId": "run-1"})
        os.chmod(state_dir, 0o777)
        self.assertIsNone(claude_hook._read_state("s1"))
        self.assertIsNone(claude_hook._beat_lock("s1"))
        os.chmod(state_dir, 0o700)

    def test_state_dir_owned_by_someone_else_is_not_used(self):
        with mock.patch.object(os, "getuid", return_value=os.getuid() + 1):
            with self.assertRaises(OSError):
                claude_hook._ensure_dir(cc.user_dir("state"))

    def test_temp_names_are_random_and_cleaned_up_on_failure(self):
        with mock.patch.object(os, "replace", side_effect=OSError("boom")):
            claude_hook._write_state("s1", {"runId": "run-1"})
        self.assertEqual([p.name for p in cc.user_dir("state").iterdir()], [])

    def test_replaced_beat_file_makes_the_holder_let_go(self):
        lock = claude_hook._beat_lock("s1")
        self.addCleanup(lock.close)
        self.assertTrue(claude_hook._lock_current(lock, "s1"))
        path = claude_hook._state_file("s1").with_suffix(".beat")
        path.unlink()
        path.write_text("")
        self.assertFalse(claude_hook._lock_current(lock, "s1"))
        second = claude_hook._beat_lock("s1")  # 换进来的新文件上别人拿得到——旧的持有者必须放手，才只剩一个
        self.assertIsNotNone(second)
        second.close()


# ───────────────────────────────────────────── 11：状态里的地址不会和文件里的令牌配对


class UrlTokenPairingTests(_IsolatedHomeMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        env = mock.patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        for key in ("COCKPIT_URL", "COCKPIT_TOKEN", "CLAUDE_PLUGIN_OPTION_COCKPIT_URL", "CLAUDE_PLUGIN_OPTION_COCKPIT_TOKEN"):
            os.environ.pop(key, None)

    def _config_file(self, **cfg):
        path = cc.user_dir("config") / cc.CONFIG_FILENAME
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cfg), encoding="utf-8")

    def test_state_derived_url_never_meets_a_token_from_the_config_file(self):
        planted = {"runId": "run-1", "url": "http://evil.invalid", "auth": False}
        self.assertTrue(claude_hook._usable(planted))  # 无令牌、没有地址：采用状态里的地址（只有地址，没有凭据）
        for later in ({"token": "file-token"}, {"url": "http://file.invalid", "token": "file-token"}):
            self._config_file(**later)  # 用户之后往配置文件里加了令牌（也可能带地址）
            config = cc.load_config()  # `_beat_once` 重读配置读到的就是这个
            self.assertEqual(config["token"], "")  # 环境里的地址是「第一个给了地址的来源」，令牌与它成对：没有
            self.assertEqual(config["url"], "http://evil.invalid")
        self.assertEqual(os.environ.get("COCKPIT_TOKEN"), None)

    def test_plugin_option_token_without_url_is_not_paired_either(self):
        os.environ["CLAUDE_PLUGIN_OPTION_COCKPIT_TOKEN"] = "plugin-token"
        planted = {"runId": "run-1", "url": "http://evil.invalid", "auth": False}
        claude_hook._usable(planted)
        self.assertEqual(cc.load_config()["token"], "")


if __name__ == "__main__":
    unittest.main()
