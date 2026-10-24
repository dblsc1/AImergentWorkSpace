"""agent-hooks「目录 → 项目」部分（README「挂到项目」节，nexus-core 契约 v2.13）的单测。

跑法同 test_agent_hooks.py：python3 -m pytest -q tools/agent-hooks（纯标准库）。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
import unittest.mock as mock
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import claude_hook  # noqa: E402
import cockpit_client as cc  # noqa: E402
from test_agent_hooks import RUN_SCRIPT, _IsolatedHomeMixin, _start_server, _stop_server  # noqa: E402

CFG = {"tasks": {"/code/a/backend": "task-map"}, "projects": {"/code": "p-shallow", "/code/a": "p-map"}}


class TargetResolutionTests(unittest.TestCase):
    def _resolve(self, task=None, project=None, cwd="/code/a/backend/x", env=None, config=CFG):
        with mock.patch.dict("os.environ", env or {}, clear=False) as environ:
            for key in ("COCKPIT_TASK", "COCKPIT_PROJECT"):
                if key not in (env or {}):
                    environ.pop(key, None)
            return cc.resolve_target(task, project, cwd, config)

    def test_order_task_sources_beat_every_project_source(self):
        both = {"COCKPIT_TASK": "task-env", "COCKPIT_PROJECT": "p-env"}
        self.assertEqual(self._resolve("task-flag", "p-flag", env=both), ("task-flag", None))
        self.assertEqual(self._resolve(None, "p-flag", env=both), ("task-env", None))
        self.assertEqual(self._resolve(None, "p-flag", env={"COCKPIT_PROJECT": "p-env"}), ("task-map", None))

    def test_order_among_project_sources(self):
        cwd = "/code/a/docs"  # 不在 tasks 映射里
        self.assertEqual(self._resolve(None, "p-flag", cwd, {"COCKPIT_PROJECT": "p-env"}), (None, "p-flag"))
        self.assertEqual(self._resolve(cwd=cwd, env={"COCKPIT_PROJECT": "p-env"}), (None, "p-env"))
        self.assertEqual(self._resolve(cwd=cwd), (None, "p-map"))
        self.assertEqual(self._resolve(cwd="/elsewhere"), (None, None))

    def test_projects_map_longest_prefix_and_no_sibling_false_match(self):
        self.assertEqual(self._resolve(cwd="/code/a"), (None, "p-map"))
        self.assertEqual(self._resolve(cwd="/code/ab"), (None, "p-shallow"))  # /code/ab 不在 /code/a 下面
        self.assertEqual(self._resolve(cwd="/codex"), (None, None))

    def test_broken_maps_are_tolerated(self):
        for bad in ({"projects": ["x"]}, {"projects": {"/code": 7}}, {"projects": {"/code": ""}}, {}):
            self.assertEqual(self._resolve(cwd="/code", config=bad), (None, None))


class ProjectConfigAndPayloadTests(_IsolatedHomeMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.server, self.thread, self.log = _start_server(expect_token="good-token")
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.work = Path(self._home_tmpdir.name) / "CFO_agent"
        self.work.mkdir()
        cfg_dir = Path(self._home_tmpdir.name) / "config"
        cfg_dir.mkdir()
        (cfg_dir / cc.CONFIG_FILENAME).write_text(json.dumps(
            {"url": self.url, "token": "good-token", "projects": {str(self.work): "p_cfo"}}), encoding="utf-8")
        env = mock.patch.dict("os.environ")
        env.start()
        self.addCleanup(env.stop)
        for key in ("COCKPIT_URL", "COCKPIT_TOKEN", "COCKPIT_TASK", "COCKPIT_PROJECT"):
            os.environ.pop(key, None)

    def tearDown(self):
        _stop_server(self.server, self.thread)
        super().tearDown()

    def _starts(self):
        return [r["body"] for r in self.log.requests if r["path"].endswith("/agents/start")]

    def test_load_config_reads_projects_and_tolerates_bad_shape(self):
        self.assertEqual(cc.load_config()["projects"], {str(self.work): "p_cfo"})
        (Path(self._home_tmpdir.name) / "config" / cc.CONFIG_FILENAME).write_text('{"projects": 3}', encoding="utf-8")
        self.assertEqual(cc.load_config()["projects"], {})

    def test_start_run_sends_project_only_without_a_task(self):
        config = {"url": self.url, "token": "good-token"}
        cc.start_run(config, None, "a", "t", project_id="p1")
        cc.start_run(config, "t1", "a", "t", project_id="p1")
        cc.start_run(config, None, "a", "t")
        self.assertEqual(self._starts(), [{"agent": "a", "tool": "t", "projectId": "p1"},
                                          {"agent": "a", "tool": "t", "taskId": "t1"},
                                          {"agent": "a", "tool": "t"}])

    def test_session_started_in_a_mapped_directory_reports_the_project(self):
        claude_hook.handle_session_start({"session_id": "s1", "cwd": str(self.work / "sub")})
        body = self._starts()[-1]
        self.assertEqual((body["projectId"], body["label"]), ("p_cfo", "sub"))
        self.assertNotIn("taskId", body)

    def test_cockpit_run_project_flag_and_directory_map(self):
        base = [sys.executable, str(RUN_SCRIPT)]
        cmd = ["--", sys.executable, "-c", "pass"]
        env = self._subprocess_env()
        subprocess.run(base + cmd, env=env, cwd=self.work, check=True, timeout=10)
        subprocess.run(base + ["--project", "p_flag"] + cmd, env=env, cwd=self.work, check=True, timeout=10)
        subprocess.run(base + ["--task", "t1", "--project", "p_flag"] + cmd, env=env, cwd=self.work, check=True,
                       timeout=10)
        got = [(b.get("taskId"), b.get("projectId")) for b in self._starts()]
        self.assertEqual(got, [(None, "p_cfo"), (None, "p_flag"), ("t1", None)])


if __name__ == "__main__":
    unittest.main()
