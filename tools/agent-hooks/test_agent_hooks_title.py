"""agent-hooks「会话的名字」部分（README 同名节，nexus-core 契约 v2.13「会话改名」）的单测。

transcript 都是临时文件——不读跑测试那台机器上真实的 ~/.claude。
"""

from __future__ import annotations

import json
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


def _title(name) -> str:
    return json.dumps({"type": "custom-title", "customTitle": name, "sessionId": "s"})


class _TranscriptMixin(_IsolatedHomeMixin):
    def _transcript(self, *lines: str, raw: bytes | None = None) -> str:
        path = Path(self._home_tmpdir.name) / "transcript.jsonl"
        path.write_bytes(raw if raw is not None else ("\n".join(lines) + "\n").encode("utf-8"))
        return str(path)


class SessionTitleTests(_TranscriptMixin, unittest.TestCase):
    def test_latest_custom_title_wins_and_ai_title_is_ignored(self):
        path = self._transcript(
            _title("old name"), '{"type":"user","message":"hi"}', _title("Cockpit-Pub-Coder1"),
            '{"type":"ai-title","aiTitle":"Fix the build"}', '{"type":"assistant","message":"custom-title"}',
        )
        self.assertEqual(cc.session_title(path), "Cockpit-Pub-Coder1")
        self.assertEqual(cc.session_title(self._transcript(_title("  AImergent教育部门  技术主管 "))),
                         "AImergent教育部门 技术主管")

    def test_no_title_missing_file_and_bad_input_mean_none(self):
        self.assertIsNone(cc.session_title(self._transcript('{"type":"ai-title","aiTitle":"x"}')))
        self.assertIsNone(cc.session_title(self._transcript(raw=b"")))
        self.assertIsNone(cc.session_title(str(Path(self._home_tmpdir.name) / "nope.jsonl")))
        self.assertIsNone(cc.session_title(self._home_tmpdir.name))  # 是个目录
        for bad in (None, "", 7, ["x"]):
            self.assertIsNone(cc.session_title(bad))

    def test_malformed_lines_are_skipped(self):
        path = self._transcript(
            _title("good"), '{"type":"custom-title","customTitle":', "custom-title not json", '["custom-title"]',
        )
        self.assertEqual(cc.session_title(path), "good")
        bad_bytes = _title("good").encode() + b"\n\xff\xfe custom-title \xff\n"
        self.assertEqual(cc.session_title(self._transcript(raw=bad_bytes)), "good")

    def test_latest_record_without_a_usable_title_means_no_title(self):
        for last in (" ", None, 7):
            self.assertIsNone(cc.session_title(self._transcript(_title("was named"), _title(last))))

    def test_only_the_tail_of_a_big_file_is_read(self):
        filler = (json.dumps({"type": "user", "message": "x" * 1000}) + "\n").encode()
        head = _title("too far back").encode() + b"\n" + filler * 600  # ~600 KB，标题在末尾 256 KB 之外
        self.assertIsNone(cc.session_title(self._transcript(raw=head)))
        recent = head + _title("recent").encode() + b"\n" + filler * 100
        self.assertEqual(cc.session_title(self._transcript(raw=recent)), "recent")

        sizes: list = []
        real = cc.read_regular
        with mock.patch.object(cc, "read_regular", lambda *a, **k: sizes.append(len(real(*a, **k)[0])) or real(*a, **k)):
            self.assertEqual(cc.session_title(self._transcript(raw=recent)), "recent")
        self.assertEqual(sizes, [cc.TITLE_TAIL_BYTES])  # 一次读、有上限，从不整个读

    def test_a_huge_single_line_and_a_cut_first_line_are_tolerated(self):
        # 末尾 256 KB 整个落在一行里：切进来的半行丢掉，什么都找不到
        huge = _title("early").encode() + b"\n" + b'{"type":"user","message":"' + b"y" * 400_000 + b'"}\n'
        self.assertIsNone(cc.session_title(self._transcript(raw=huge)))
        # 窗口从一条标题记录中间切进来：那半行不解析，也不报错
        record = _title("cut").encode() + b"\n"
        pad = b"z" * (cc.TITLE_TAIL_BYTES - 20) + b"\n"
        self.assertIsNone(cc.session_title(self._transcript(raw=record + pad)))


class HookTitleTests(_TranscriptMixin, unittest.TestCase):
    AT = "2026-10-08T10:00:00+08:00"

    def setUp(self):
        super().setUp()
        self.server, self.thread, self.log = _start_server(expect_token="good-token")
        config = {"url": f"http://127.0.0.1:{self.server.server_address[1]}", "token": "good-token",
                  "tasks": {}, "projects": {}}
        patch = mock.patch.object(cc, "load_config", return_value=config)
        patch.start()
        self.addCleanup(patch.stop)

    def tearDown(self):
        _stop_server(self.server, self.thread)
        super().tearDown()

    def _starts(self):
        return [r["body"] for r in self.log.requests if r["path"].endswith("/agents/start")]

    def _event(self, name, **extra):
        return {"hook_event_name": name, "session_id": "s1", "cwd": "/home/u/garden", **extra}

    def test_session_start_reports_the_custom_title_as_label_and_match(self):
        path = self._transcript(_title("Cockpit-Pub-Coder1"))
        claude_hook.handle_session_start(self._event("SessionStart", transcript_path=path))
        body = self._starts()[-1]
        self.assertEqual((body["label"], body["match"], body["agent"]),
                         ("Cockpit-Pub-Coder1", "Cockpit-Pub-Coder1", "garden"))
        self.assertEqual(claude_hook._read_state("s1")["label"], "Cockpit-Pub-Coder1")

    def test_no_title_falls_back_to_directory_name_and_costs_no_extra_request(self):
        path = self._transcript('{"type":"ai-title","aiTitle":"Fix the build"}')
        claude_hook.handle_session_start(self._event("SessionStart", transcript_path=path))
        claude_hook.handle_session_start(self._event("SessionStart", session_id="s2"))  # 没有 transcript_path
        self.assertEqual([(b["label"], b["match"]) for b in self._starts()], [("garden", "garden")] * 2)
        claude_hook.handle_phase(self._event("UserPromptSubmit", transcript_path=path), self.AT)
        claude_hook.handle_phase(self._event("Stop", transcript_path=path), self.AT)
        self.assertEqual(len(self._starts()), 2)  # 没起名且目录名没变：相位事件不多发 start

    def test_rename_mid_session_is_picked_up_once_on_the_next_reported_event(self):
        path = self._transcript('{"type":"user"}')
        claude_hook.handle_session_start(self._event("SessionStart", transcript_path=path))
        first = self._starts()[-1]
        Path(path).write_text(_title("Sherpa_Builder4") + "\n", encoding="utf-8")
        claude_hook.handle_phase(self._event("PostToolUse", transcript_path=path), self.AT)  # 这个事件本来就不报
        self.assertEqual(len(self._starts()), 1)
        claude_hook.handle_phase(self._event("UserPromptSubmit", transcript_path=path), self.AT)
        renamed = self._starts()[-1]
        self.assertEqual((renamed["label"], renamed["match"], renamed["phase"]),
                         ("Sherpa_Builder4", "Sherpa_Builder4", "working"))
        self.assertEqual(renamed["clientKey"], first["clientKey"])  # 同一个 key：服务端给原运行换名字
        state = claude_hook._read_state("s1")
        self.assertEqual((state["runId"], state["label"], state["lastPhase"]), ("run-1", "Sherpa_Builder4", "working"))
        claude_hook.handle_phase(self._event("Stop", transcript_path=path), self.AT)
        self.assertEqual(len(self._starts()), 2)  # 名字没再变：不再发

    def test_rename_report_failure_is_swallowed_and_retried_next_event(self):
        path = self._transcript(_title("X-company Coder1"))
        claude_hook._save_run_id("s1", "run-1", "idle", "garden")
        with mock.patch.object(cc, "start_run", side_effect=cc.CockpitError("超时")):
            claude_hook.handle_phase(self._event("Stop", transcript_path=path), self.AT)
        self.assertEqual(claude_hook._read_state("s1")["label"], "garden")
        claude_hook.handle_phase(self._event("Stop", transcript_path=path), self.AT)
        self.assertEqual(claude_hook._read_state("s1")["label"], "X-company Coder1")

    def _stops(self):
        return [r for r in self.log.requests if r["path"].endswith("/stop")]

    def test_cleared_title_reverts_the_lane_to_the_directory_name(self):
        path = self._transcript(_title(""))
        claude_hook._save_run_id("s1", "run-1", "idle", "Old Name")
        claude_hook.handle_phase(self._event("Stop", transcript_path=path), self.AT)
        self.assertEqual(self._starts()[-1]["label"], "garden")
        self.assertEqual(claude_hook._read_state("s1")["label"], "garden")

    def test_rename_with_state_gone_before_start_sends_nothing(self):
        path = self._transcript(_title("New Name"))
        claude_hook._save_run_id("s1", "run-1", "idle", "garden")
        real_read = claude_hook._read_state
        calls = []

        def read(sid):
            calls.append(1)
            if len(calls) == 2:  # 第 1 次是 handle_phase 自己读的，第 2 次是发前复查
                claude_hook._delete_run_id(sid)
            return real_read(sid)

        with mock.patch.object(claude_hook, "_read_state", side_effect=read):
            claude_hook.handle_phase(self._event("Stop", transcript_path=path), self.AT)
        self.assertEqual(self._starts(), [])
        self.assertIsNone(real_read("s1"))

    def test_rename_returning_a_different_run_while_live_is_adopted(self):
        path = self._transcript(_title("New Name"))
        claude_hook._save_run_id("s1", "run-1", "idle", "garden")  # 服务端已把 run-1 收掉：start 回新 run
        with mock.patch.object(cc, "start_run", return_value={"runId": "run-2"}):
            claude_hook.handle_phase(self._event("Stop", transcript_path=path), self.AT)
        state = claude_hook._read_state("s1")
        self.assertEqual((state["runId"], state["label"]), ("run-2", "New Name"))
        self.assertEqual(self._stops(), [])

    def test_session_end_starting_during_the_relabel_request_waits_and_stops_the_new_run(self):
        path = self._transcript(_title("New Name"))
        claude_hook._save_run_id("s1", "run-1", "idle", "garden")
        ender = threading.Thread(target=claude_hook.handle_session_end, args=({"session_id": "s1"},))

        def slow_start(*a, **k):  # start 进行中，SessionEnd 钩子到了：必须等在锁外
            ender.start()
            time.sleep(0.4)
            self.assertTrue(ender.is_alive())
            self.assertEqual(self._stops(), [])
            return {"runId": "run-2"}  # 原 run 已被服务端收掉 → 新 run，被采纳

        with mock.patch.object(cc, "start_run", side_effect=slow_start):
            claude_hook.handle_phase(self._event("Stop", transcript_path=path), self.AT)
        ender.join(5)
        self.assertEqual([r["path"] for r in self._stops()], ["/api/core/agents/run-2/stop"])
        self.assertIsNone(claude_hook._read_state("s1"))

    def test_relabel_after_session_end_sends_nothing(self):
        claude_hook._save_run_id("s1", "run-1", "idle", "garden")
        stale = claude_hook._read_state("s1")
        claude_hook.handle_session_end({"session_id": "s1"})
        with claude_hook._session_lock("s1"):
            claude_hook._relabel_locked(self._event("Stop"), "s1", "New Name", stale)
        self.assertEqual(self._starts(), [])
        self.assertIsNone(claude_hook._read_state("s1"))

    def test_lock_wait_is_bounded_and_reports_not_held(self):
        with claude_hook._session_lock("s1"):
            t0 = time.monotonic()
            with claude_hook._session_lock("s1", wait=0.15) as held:
                self.assertFalse(held)
            self.assertTrue(0.15 <= time.monotonic() - t0 < 2)
        t0 = time.monotonic()  # 外层放锁后能立刻拿到
        with claude_hook._session_lock("s1", wait=0.15):
            pass
        self.assertLess(time.monotonic() - t0, 0.1)

    def test_leftover_lock_file_is_not_a_stale_lock(self):
        lock = claude_hook._state_file("s1").with_suffix(".lock")
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_bytes(b"left by a killed hook")
        t0 = time.monotonic()
        with claude_hook._session_lock("s1", wait=1):
            pass
        self.assertLess(time.monotonic() - t0, 0.1)


if __name__ == "__main__":
    unittest.main()
