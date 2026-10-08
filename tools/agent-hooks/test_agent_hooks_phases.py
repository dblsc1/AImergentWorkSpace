"""agent-hooks 相位部分（README「相位」节，nexus-core 契约 v2.4）的单测。

跑法同 test_agent_hooks.py：python3 -m pytest -q tools/agent-hooks（纯标准库）。
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
import unittest.mock as mock
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import claude_hook  # noqa: E402
import cockpit_client as cc  # noqa: E402
from test_agent_hooks import (  # noqa: E402
    HOOK_SCRIPT,
    RUN_SCRIPT,
    _IsolatedHomeMixin,
    _start_server,
    _stop_server,
    _unused_port,
)


class _ServerMixin(_IsolatedHomeMixin):
    def setUp(self):
        super().setUp()
        self.server, self.thread, self.log = _start_server(expect_token="good-token")
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.config = {"url": self.url, "token": "good-token", "tasks": {}}

    def tearDown(self):
        _stop_server(self.server, self.thread)
        super().tearDown()

    def _bodies(self, suffix):
        return [r["body"] for r in self.log.requests if r["path"].endswith(suffix)]


class ClientPhaseTests(_ServerMixin, unittest.TestCase):
    def test_start_run_sends_v24_fields_only_when_given(self):
        cc.start_run(self.config, None, "a", "t", phase="idle", label="garden", match="garden", client_key="k")
        self.assertEqual(self._bodies("/start")[-1], {"agent": "a", "tool": "t", "phase": "idle", "label": "garden",
                                                      "match": "garden", "clientKey": "k"})

    def test_phase_run_body(self):
        cc.phase_run(self.config, "run-1", "error", "2026-09-30T10:00:00+08:00", detail="x" * 80)
        cc.phase_run(self.config, "run-1", "working", "2026-09-30T10:00:01+08:00", reply=True)
        first, second = self._bodies("/run-1/phase")
        self.assertEqual(first, {"phase": "error", "at": "2026-09-30T10:00:00+08:00", "detail": "x" * 64})
        self.assertEqual(second, {"phase": "working", "at": "2026-09-30T10:00:01+08:00", "reply": True})

    def test_now_iso_has_offset_and_lane_names(self):
        self.assertIsNotNone(datetime.fromisoformat(cc.now_iso()).tzinfo)
        self.assertEqual(cc.lane_names("/home/u/garden"), ("garden", "garden"))
        self.assertEqual(cc.lane_names("/home/u/ab"), ("ab", None))  # match 不足 3 码点不带


class ClaudeHookPhaseTests(_ServerMixin, unittest.TestCase):
    AT = "2026-09-30T10:00:00+08:00"

    def _hook(self, event, session="s1", **extra):
        with mock.patch.object(cc, "load_config", return_value=self.config):
            claude_hook.handle_phase({"hook_event_name": event, "session_id": session, **extra}, self.AT)

    def _last(self):
        return self._bodies("/phase")[-1] if self._bodies("/phase") else None

    def test_session_start_sends_idle_label_match_and_hashed_client_key(self):
        with mock.patch.object(cc, "load_config", return_value=self.config):
            claude_hook.handle_session_start({"session_id": "sess-raw", "cwd": "/home/u/garden",
                                              "hook_event_name": "SessionStart"})
        body = self._bodies("/start")[-1]
        self.assertEqual((body["phase"], body["label"], body["match"]), ("idle", "garden", "garden"))
        self.assertEqual(body["clientKey"], hashlib.sha256(b"sess-raw").hexdigest()[:32])
        self.assertNotIn("sess-raw", json.dumps(self.log.requests))
        self.assertEqual(claude_hook._read_state("sess-raw"), {"runId": "run-1", "lastPhase": "idle", "label": "garden"})  # label：v0.4

    def test_mapping_table(self):
        claude_hook._save_run_id("s1", "run-1", "idle")
        cases = [
            ({"hook_event_name": "UserPromptSubmit", "prompt": "SECRET"}, ("working", None, True)),
            ({"hook_event_name": "PermissionRequest", "tool_name": "Bash", "tool_input": {"command": "CMDSECRET"}},
             ("waiting_permission", "Bash", None)),
            ({"hook_event_name": "Notification", "notification_type": "permission_prompt", "message": "SECRET"},
             ("waiting_permission", "permission_prompt", None)),
            ({"hook_event_name": "Notification", "notification_type": "elicitation_dialog"},
             ("waiting_input", "elicitation_dialog", None)),
            ({"hook_event_name": "Notification", "notification_type": "agent_needs_input"},
             ("waiting_input", "agent_needs_input", None)),
            ({"hook_event_name": "PostToolUse"}, ("working", None, True)),  # 上一条是等：报
            ({"hook_event_name": "Notification", "notification_type": "idle_prompt"}, ("idle", "idle_prompt", None)),
            ({"hook_event_name": "Stop"}, ("idle", None, None)),
            ({"hook_event_name": "StopFailure", "error": "rate_limit"}, ("error", "rate_limit", None)),
        ]
        for payload, (phase, detail, reply) in cases:
            with self.subTest(payload=payload):
                self._hook(payload.pop("hook_event_name"), **payload)
                body = self._last()
                self.assertEqual((body["phase"], body.get("detail"), body.get("reply")), (phase, detail, reply))
                self.assertEqual(body["at"], self.AT)
                self.assertEqual(claude_hook._read_state("s1")["lastPhase"], phase)  # 先写状态
        self.assertNotIn("SECRET", json.dumps(self.log.requests))
        self.assertNotIn("CMDSECRET", json.dumps(self.log.requests))

    def test_post_tool_use_only_after_waiting(self):
        claude_hook._save_run_id("s1", "run-1", "working")
        self._hook("PostToolUse")
        self._hook("PostToolUseFailure")
        self.assertEqual(self._bodies("/phase"), [])
        claude_hook._save_run_id("s1", "run-1", "waiting_input")
        self._hook("PostToolUseFailure")
        self.assertEqual(self._last(), {"phase": "working", "at": self.AT, "reply": True})

    def test_unknown_notification_and_event_not_reported(self):
        claude_hook._save_run_id("s1", "run-1", "idle")
        self._hook("Notification", notification_type="auth_success")
        self._hook("PreToolUse", tool_name="Bash")
        self.assertEqual(self._bodies("/phase"), [])

    def test_no_state_means_no_request(self):
        self._hook("Stop", session="never-started")
        self.assertEqual(self.log.requests, [])

    def test_hook_process_exits_zero_for_phase_events_even_when_down(self):
        claude_hook._save_run_id("s1", "run-1", "idle")
        env = self._subprocess_env(COCKPIT_URL=f"http://127.0.0.1:{_unused_port()}", COCKPIT_TOKEN="x")
        for event in ("UserPromptSubmit", "Stop", "StopFailure", "Notification"):
            proc = subprocess.run([sys.executable, str(HOOK_SCRIPT)], env=env, capture_output=True, text=True,
                                  input=json.dumps({"hook_event_name": event, "session_id": "s1"}), timeout=10)
            self.assertEqual(proc.returncode, 0)
        proc = subprocess.run([sys.executable, str(HOOK_SCRIPT)], env=env, capture_output=True, text=True,
                              input="[1, 2]", timeout=10)
        self.assertEqual(proc.returncode, 0)


class CockpitRunPhaseTests(_ServerMixin, unittest.TestCase):
    def _run(self, *args, **env):
        full_env = self._subprocess_env(COCKPIT_URL=self.url, COCKPIT_TOKEN="good-token", **env)
        full_env.pop("COCKPIT_TASK", None)
        return subprocess.run([sys.executable, str(RUN_SCRIPT), *args], env=full_env, capture_output=True,
                              text=True, timeout=10)

    def test_phase_subcommand_uses_env_run_id(self):
        proc = self._run("phase", "waiting_permission", "--detail", "Bash", "--reply", COCKPIT_RUN_ID="run-9")
        self.assertEqual(proc.returncode, 0)
        [body] = self._bodies("/run-9/phase")
        self.assertEqual((body["phase"], body["detail"], body["reply"]), ("waiting_permission", "Bash", True))
        self.assertIsNotNone(datetime.fromisoformat(body["at"]).tzinfo)

    def test_phase_run_flag_wins_and_missing_run_id_is_exit_zero(self):
        self._run("phase", "idle", "--run", "run-7", COCKPIT_RUN_ID="run-9")
        self.assertEqual(len(self._bodies("/run-7/phase")), 1)
        os.environ.pop("COCKPIT_RUN_ID", None)
        proc = self._run("phase", "idle")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("COCKPIT_RUN_ID", proc.stderr)
        self.assertEqual(self._run("phase", "busy").returncode, 0)  # 用法错也是 0

    def test_phase_failure_is_exit_zero(self):
        full_env = self._subprocess_env(COCKPIT_URL=f"http://127.0.0.1:{_unused_port()}", COCKPIT_TOKEN="x")
        proc = subprocess.run([sys.executable, str(RUN_SCRIPT), "phase", "idle", "--run", "r"], env=full_env,
                              capture_output=True, text=True, timeout=10)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("相位上报失败", proc.stderr)

    def test_wrapped_command_gets_run_id_and_start_carries_label_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "env.txt"
            code = f"import os; open({str(out)!r}, 'w').write(os.environ.get('COCKPIT_RUN_ID', ''))"
            proc = self._run("--task", "t1", "--label", "L", "--match", "ab", "--", sys.executable, "-c", code)
            self.assertEqual(proc.returncode, 0)
            self.assertEqual(out.read_text(), "run-1")
        body = self._bodies("/agents/start")[-1]
        self.assertEqual(body["label"], "L")
        self.assertNotIn("match", body)  # 不足 3 码点不带

    def test_failed_start_does_not_leak_parent_run_id_to_child(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "env.txt"
            code = f"import os; open({str(out)!r}, 'w').write(os.environ.get('COCKPIT_RUN_ID', '<none>'))"
            full_env = self._subprocess_env(COCKPIT_URL=f"http://127.0.0.1:{_unused_port()}", COCKPIT_TOKEN="x",
                                            COCKPIT_RUN_ID="outer-run")
            subprocess.run([sys.executable, str(RUN_SCRIPT), "--", sys.executable, "-c", code], env=full_env,
                           capture_output=True, text=True, timeout=10)
            self.assertEqual(out.read_text(), "<none>")

    def test_literal_phase_after_double_dash_is_still_wrapped(self):
        proc = self._run("--task", "t1", "--", sys.executable, "-c", "import sys; sys.exit(3)", "phase")
        self.assertEqual(proc.returncode, 3)
        self.assertEqual(self._bodies("/phase"), [])
        self.assertEqual(len(self._bodies("/stop")), 1)


if __name__ == "__main__":
    unittest.main()
