"""Offline v1.1 update regressions using synthetic temporary data only.

No source calls or child processes may escape mocks. CLI integration exercises
real fetch -> details -> analyze -> report against a generated synthetic DB.
Run: PYTHONDONTWRITEBYTECODE=1 .venv/bin/python tools/test_panel_update.py
"""
from __future__ import annotations

import argparse
import contextlib
import copy
import io
import json
import os
import socket
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import yaml
from jobs import RunTimeout, cli, config, db as database, demo, labels, panel_update, sources
from jobs import panel, run_state, skills as skills_mod
from jobs.sources import jobspy_source, seek


class TemporaryTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="job-board-update-test-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.enterContext(patch.object(config, "ROOT", self.root))
        self.enterContext(patch.object(socket, "socket", side_effect=AssertionError("network forbidden")))
        self.enterContext(patch.object(socket, "create_connection", side_effect=AssertionError("network forbidden")))
        original_connect = database.connect
        self.connections = []
        self.addCleanup(lambda: [conn.close() for conn in self.connections])

        def isolated_connect(target):
            target = Path(target).resolve()
            if self.root not in target.parents:
                raise AssertionError(f"DB outside temporary root: {target}")
            conn = original_connect(target)
            self.connections.append(conn)
            return conn

        self.enterContext(patch.object(database, "connect", side_effect=isolated_connect))

    def fixture(self):
        generated = self.root / "synthetic"
        with contextlib.redirect_stdout(io.StringIO()):
            demo.generate(generated)
        cfg = config.load_config(generated / "config.yaml")
        cfg.pop("demo", None)
        cfg.update(queries=["synthetic developer"], seek={"enabled": True, "detail_sleep": 0},
                   jobspy={"indeed": {"enabled": False}, "linkedin": {"enabled": False}})
        self.path = generated / "config.yaml"
        self.write(cfg)
        return cfg

    def write(self, cfg):
        self.path.write_text(yaml.safe_dump(cfg), encoding="utf-8")

    def run_pipeline(self, **overrides):
        args = argparse.Namespace(config=str(self.path), rules=str(self.path.parent / "rules.yaml"),
                                  skills=str(self.path.parent / "skills.yaml"), timeout=0, windows_worker=True,
                                  result_file=str(self.root / "result.json"), **overrides)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = cli.cmd_run(args)
        return code, json.loads(Path(args.result_file).read_text())


class PipelineTests(TemporaryTest):
    def setUp(self):
        super().setUp()
        self.cfg = self.fixture()

    def test_full_pipeline_preserves_existing_and_during_update_labels(self):
        conn = database.connect(self.cfg["db_path"])
        before = [tuple(row) for row in conn.execute("SELECT * FROM labels ORDER BY uid")]
        job = dict(conn.execute("SELECT * FROM jobs WHERE source='seek' LIMIT 1").fetchone())
        uid = job["uid"]
        job["raw"] = {}
        conn.close()

        def fetched(_cfg, _queries):
            conn = database.connect(self.cfg["db_path"])
            labels.set_label(conn, uid, source="synthetic-test", note="Saved during update", action="saved")
            conn.close()
            return [job]

        with patch.object(sources, "iter_sources", return_value=[("seek", fetched)]), \
             patch.object(seek, "fetch_detail", return_value="Synthetic Python developer role"):
            code, result = self.run_pipeline()
        self.assertEqual(code, 0)
        self.assertEqual(result["state"], "success")
        self.assertEqual(result["counts"]["seen"], 1)
        self.assertEqual(result["counts"]["updated"], 1)
        self.assertEqual(result["counts"]["inserted"], 0)
        conn = database.connect(self.cfg["db_path"])
        after = [tuple(row) for row in conn.execute("SELECT * FROM labels ORDER BY uid")]
        self.assertEqual([row for row in before if row[0] != uid],
                         [row for row in after if row[0] != uid])
        current = dict(conn.execute("SELECT * FROM labels WHERE uid=?", (uid,)).fetchone())
        self.assertEqual(current["note"], "Saved during update；")
        self.assertEqual(current["action"], "saved")
        self.assertTrue(conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0])
        conn.close()
        self.assertTrue(list(Path(self.cfg["report"]["out_dir"]).glob("*-top.md")))

    def test_source_exception_is_partial_and_does_not_leak_error(self):
        def failed(_cfg, _queries):
            raise RuntimeError("PRIVATE-CONFIG-AND-LOG-SENTINEL")
        with patch.object(sources, "iter_sources", return_value=[("seek", failed)]), \
             patch.object(seek, "fetch_detail", return_value="Synthetic description"):
            code, result = self.run_pipeline()
        self.assertEqual(code, 0)
        self.assertEqual(result["state"], "partial")
        self.assertEqual(result["failed_sources"], ["seek"])
        self.assertNotIn("PRIVATE-CONFIG", json.dumps(result))

    def test_missing_details_are_partial(self):
        conn = database.connect(self.cfg["db_path"])
        conn.execute("UPDATE jobs SET description=NULL, description_fetched_at=NULL WHERE source='seek'")
        conn.commit()
        conn.close()
        with patch.object(sources, "iter_sources", return_value=[("seek", lambda _cfg, _queries: [])]), \
             patch.object(seek, "fetch_detail", return_value=None):
            code, result = self.run_pipeline()
        self.assertEqual(code, 0)
        self.assertEqual(result["state"], "partial")
        self.assertEqual(result["error"], "details_incomplete")

    def test_step_failure_and_timeout_release_scheduler_lock(self):
        for replacement, expected, code in ((lambda _args: 7, "error", 7),
                                             (lambda _args: (_ for _ in ()).throw(RunTimeout()), "timeout", 3)):
            with self.subTest(expected=expected), patch.object(cli, "cmd_fetch", replacement):
                actual, result = self.run_pipeline()
                self.assertEqual(actual, code)
                self.assertEqual(result["state"], expected)
                self.assertTrue(result["finished_at"])
                lock = cli._acquire_run_lock(self.cfg)
                self.assertIsNotNone(lock)
                if lock:
                    lock.close()

    def test_cli_attempt_timestamp_matches_panel_even_if_child_starts_later(self):
        with patch.object(sources, "iter_sources", return_value=[("seek", lambda _cfg, _queries: [])]), \
             patch.object(seek, "fetch_detail", return_value="Synthetic description"):
            code, result = self.run_pipeline(result_started_at="2026-10-15T23:59:59Z")
        self.assertEqual(code, 0)
        self.assertEqual(result["started_at"], "2026-10-15T23:59:59Z")
        self.assertEqual(result["state"], "success")

    def test_existing_scheduler_lock_skips_all_steps(self):
        lock = cli._acquire_run_lock(self.cfg)
        self.assertIsNotNone(lock)
        try:
            with patch.object(cli, "cmd_fetch", side_effect=AssertionError("must not fetch")):
                code, result = self.run_pipeline()
            self.assertEqual(code, 4)
            self.assertEqual(result["state"], "busy")
        finally:
            lock.close()

    def test_swallowed_seek_and_jobspy_query_failures_stay_partial(self):
        variants = (
            ("seek", seek.fetch, patch.object(seek, "search_page", side_effect=URLError("synthetic offline failure"))),
            ("indeed", jobspy_source.fetch, patch.dict(sys.modules, {"jobspy": types.SimpleNamespace(
                scrape_jobs=lambda **_kw: (_ for _ in ()).throw(RuntimeError("synthetic failure")))})),
        )
        for name, fetch, mock in variants:
            with self.subTest(source=name), mock, \
                 patch.object(sources, "iter_sources", return_value=[(name, fetch)]), \
                 patch.object(seek, "fetch_detail", return_value="Synthetic description"):
                code, result = self.run_pipeline()
                self.assertEqual(code, 0)
                self.assertEqual(result["state"], "partial")
                self.assertIn(name, result["failed_sources"])


class ManagerTests(TemporaryTest):
    def setUp(self):
        super().setUp()
        self.path = self.root / "config.yaml"
        self.cfg = {"queries": ["synthetic developer"], "db_path": str(self.root / "jobs.db"),
                    "report": {"out_dir": str(self.root / "reports")}}
        self.write(self.cfg)
        self.enterContext(patch.object(panel_update, "ROOT", self.root))
        self.process_mock = self.enterContext(patch.object(panel_update.subprocess, "Popen",
            side_effect=AssertionError("unmocked child process forbidden")))
        if os.name == 'nt':
            # Manager state-machine tests keep their fake Popen boundary. Native
            # gate/Job Object integration is in test_windows_runtime instead.
            from jobs import windows_process
            def fake_spawn(arguments, lock, **options):
                process = self.process_mock([sys.executable, '-m', 'jobs.cli', *arguments], **options)
                process.close = lambda: None
                class Job:
                    def stop(self, child): child.wait()
                    def close(self): pass
                return process, Job()
            self.enterContext(patch.object(windows_process, 'spawn_python', fake_spawn))

    def manager(self, cfg=None):
        return panel_update.UpdateManager(cfg or self.cfg, self.path)

    def wait_finished(self, manager):
        end = time.monotonic() + 2
        while time.monotonic() < end:
            state = manager.status()
            if state["state"] != "running":
                return state
            time.sleep(0.005)
        self.fail("mock worker did not finish")

    def fake_worker(self, manager, final="success", code=0):
        release = threading.Event()
        reaped = threading.Event()
        def cleanup_worker():
            release.set()
            deadline = time.monotonic() + 2
            while (manager.runtime / "config.yaml").exists() and time.monotonic() < deadline:
                time.sleep(0.005)
        self.addCleanup(cleanup_worker)

        class Process:
            pid = 99999999
            def wait(inner, timeout=None):
                if not release.wait(2):
                    raise AssertionError("test did not release worker")
                initial = json.loads(manager.state_path.read_text())
                result = {**initial, "state": final, "stage": "report", "finished_at": "2026-10-16T01:00:00Z"}
                manager.result_path.write_text(json.dumps(result))
                reaped.set()
                return code

        self.process_mock.side_effect = lambda *_args, **_kwargs: Process()
        return release, reaped

    def test_repaired_fetch_config_is_visible_without_panel_restart(self):
        self.cfg.pop("queries")
        self.write(self.cfg)
        manager = self.manager()
        self.assertEqual(manager.preview()["error"], "config_invalid")
        self.cfg["queries"] = ["repaired synthetic developer"]
        self.write(self.cfg)
        preview = manager.preview()
        self.assertTrue(preview["ok"])
        self.assertEqual(preview["queries"], self.cfg["queries"])
        self.assertTrue(preview["changed_since_start"])

    def test_external_cwd_keeps_rules_skills_and_worker_selection_consistent(self):
        launch = self.root / "external-launch"
        launch.mkdir()
        (launch / "rules.yaml").write_text("version: 91\n")
        for name in ("vocab.yaml", "override.yaml"):
            (launch / name).write_text(yaml.safe_dump({"version": 1, "skills": [{"name": name}]}))
        self.cfg["skills_path"] = "vocab.yaml"
        self.write(self.cfg)
        for override in (None, "override.yaml"):
            with self.subTest(override=override):
                with contextlib.chdir(launch):
                    manager = panel_update.UpdateManager(self.cfg, self.path, rules_path="rules.yaml", skills_path=override)
                expected_skills = launch / (override or "vocab.yaml")
                self.assertEqual(manager.rules_path, launch / "rules.yaml")
                self.assertEqual(manager.skills_path, expected_skills)
                self.assertEqual(skills_mod.load_skills(manager.skills_path)["skills"][0]["name"], expected_skills.name)
                self.assertTrue(manager.preview()["ok"])
                release, reaped = self.fake_worker(manager)
                self.assertTrue(manager.start()["accepted"])
                command = self.process_mock.call_args.args[0]
                self.assertEqual(Path(command[command.index("--rules") + 1]), launch / "rules.yaml")
                self.assertEqual(Path(command[command.index("--skills") + 1]), expected_skills)
                release.set()
                self.assertTrue(reaped.wait(2))
                self.assertEqual(self.wait_finished(manager)["state"], "success")
                # Once the watcher closes its snapshot, the next subtest may start.
                deadline = time.monotonic() + 2
                while (manager.runtime / "config.yaml").exists() and time.monotonic() < deadline:
                    time.sleep(0.005)
        with contextlib.chdir(launch):
            manager = panel_update.UpdateManager(self.cfg, self.path)
        self.write({**self.cfg, "skills_path": "other.yaml"})
        self.assertEqual(manager.preview()["error"], "reload_required")

    def test_panel_from_external_cwd_pins_the_editable_vocabulary_target(self):
        launch = self.root / "external-launch"
        launch.mkdir()
        self.cfg["skills_path"] = "vocab.yaml"
        self.write(self.cfg)
        for override in (None, "override.yaml"):
            with self.subTest(override=override), contextlib.chdir(launch), patch.object(panel, "serve") as serve:
                args = argparse.Namespace(config=str(self.path), rules="rules.yaml", skills=override, host=None, port=None)
                self.assertEqual(cli.cmd_panel(args), 0)
                self.assertEqual(serve.call_args.args[0]["skills_path"], str(launch / (override or "vocab.yaml")))

    def test_preview_uses_real_adapter_defaults_and_source_switches_without_writes(self):
        manager = self.manager()
        preview = manager.preview()
        self.assertTrue(preview["ok"])
        self.assertFalse(preview["changed_since_start"])
        self.assertEqual(preview["queries"], self.cfg["queries"])
        self.assertEqual(preview["sources"], [
            {"name": "seek", "enabled": True, "locations": ["All Australia"], "pages_per_query": 2, "daterange": None},
            {"name": "indeed", "enabled": True, "locations": ["Melbourne VIC"], "results_wanted": 40, "country_indeed": "australia"},
            {"name": "linkedin", "enabled": True, "locations": ["Melbourne VIC"], "results_wanted": 40, "fetch_description": True},
        ])
        changed = copy.deepcopy(self.cfg)
        changed.update(queries=["edited keyword"], seek={"enabled": False},
                       jobspy={"indeed": {"location": "Synthetic City", "results_wanted": 12},
                               "linkedin": {"enabled": False}})
        changed["private_token"] = "PRIVATE-CONFIG-SENTINEL"
        self.write(changed)
        preview = manager.preview()
        self.assertTrue(preview["changed_since_start"])
        self.assertEqual([s["name"] for s in preview["sources"] if s["enabled"]],
                         [name for name, _fn in sources.iter_sources(changed)])
        self.assertNotIn("PRIVATE-CONFIG", json.dumps(preview))
        self.assertNotIn(str(self.root), json.dumps(preview))
        self.assertFalse(manager.runtime.exists())
        self.assertEqual(manager.status()["state"], "idle")
        self.assertFalse(manager.runtime.exists())
        self.process_mock.assert_not_called()

    def test_invalid_missing_disabled_and_demo_configs_never_launch(self):
        manager = self.manager()
        cases = [([], "config_invalid"), ({"queries": []}, "config_invalid"),
                 ({**self.cfg, "seek": {"enabled": False}, "jobspy": {
                     "indeed": {"enabled": False}, "linkedin": {"enabled": False}}}, "no_sources")]
        for cfg, error in cases:
            with self.subTest(error=error):
                self.write(cfg)
                self.assertEqual(manager.preview()["error"], error)
                state = manager.start()
                self.assertFalse(state["accepted"])
                self.assertEqual(state["error"], error)
        self.path.unlink()
        self.assertEqual(manager.preview()["error"], "config_unavailable")
        self.assertFalse(manager.start()["accepted"])
        demo_cfg = {**self.cfg, "demo": {"id": "synthetic"}}
        demo_manager = self.manager(demo_cfg)
        self.assertEqual(demo_manager.preview()["error"], "demo_disabled")
        self.assertEqual(demo_manager.start()["state"], "disabled")
        self.assertEqual(demo_manager.status()["state"], "disabled")
        self.assertFalse(manager.runtime.exists())
        self.process_mock.assert_not_called()

    def test_changed_database_report_and_skills_targets_require_panel_reload(self):
        manager = self.manager()
        for field in ("db_path", "report", "skills_path"):
            with self.subTest(target=field):
                cfg = copy.deepcopy(self.cfg)
                cfg[field] = {"out_dir": str(self.root / "different-report")} if field == "report" else str(self.root / ("different-" + field))
                self.write(cfg)
                self.assertEqual(manager.preview()["error"], "reload_required")
                self.assertEqual(manager.start()["error"], "reload_required")
        self.assertFalse(manager.runtime.exists())
        self.process_mock.assert_not_called()

    def test_snapshot_is_frozen_and_restart_observes_shared_lock_and_completion(self):
        manager = self.manager()
        release, reaped = self.fake_worker(manager)
        self.assertTrue(manager.start()["accepted"])
        call = self.process_mock.call_args
        command = call.args[0]
        initial = json.loads(manager.state_path.read_text())
        self.assertEqual(command[command.index("--result-started-at") + 1], initial["started_at"])
        snapshot = Path(command[command.index("--config") + 1])
        self.assertEqual(config.load_config(snapshot), self.cfg)
        if os.name == 'nt':
            from jobs.file_security import validate_private_file
            validate_private_file(snapshot)
        else:
            self.assertEqual(snapshot.stat().st_mode & 0o777, 0o600)
            self.assertEqual(manager.runtime.stat().st_mode & 0o777, 0o700)
        changed = {**self.cfg, "queries": ["edited while running"]}
        self.write(changed)
        self.assertEqual(config.load_config(snapshot), self.cfg)
        self.assertTrue(manager.preview()["changed_since_start"])
        restarted = self.manager()
        self.assertEqual(restarted.status()["state"], "running")
        self.assertFalse(manager.start()["accepted"])
        self.assertFalse(restarted.start()["accepted"])
        self.assertEqual(self.process_mock.call_count, 1)
        release.set()
        self.assertTrue(reaped.wait(2))
        self.assertEqual(self.wait_finished(manager)["state"], "success")
        self.assertEqual(self.wait_finished(restarted)["state"], "success")
        deadline = time.monotonic() + 2
        while snapshot.exists() and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertFalse(snapshot.exists())

    def test_start_failure_and_orphaned_running_state_are_recoverable(self):
        manager = self.manager()
        self.process_mock.side_effect = OSError("PRIVATE-PATH-ERROR")
        failed = manager.start()
        self.assertFalse(failed["accepted"])
        self.assertEqual(failed["error"], "start_failed")
        self.assertEqual(manager.status()["state"], "error")
        self.assertNotIn("PRIVATE-PATH", json.dumps(manager.status()))
        (manager.runtime / "latest.json").write_text(json.dumps(panel_update._empty("running")))
        self.assertEqual(self.manager().status()["state"], "interrupted")
        self.assertFalse((manager.runtime / "config.yaml").exists())

    def test_malformed_persisted_state_is_bounded_and_does_not_leak_content(self):
        manager = self.manager()
        manager.runtime.mkdir()
        payloads = [[], "PRIVATE-SENTINEL", {"state": "success", "stage": "PRIVATE-SENTINEL",
                    "error": "PRIVATE-SENTINEL", "started_at": "/PRIVATE-SENTINEL",
                    "counts": {"seen": True, "inserted": -4, "updated": "PRIVATE-SENTINEL"},
                    "failed_sources": 123},
                    {"state": "PRIVATE-SENTINEL"}]
        for payload in payloads:
            with self.subTest(payload=payload):
                (manager.runtime / "latest.json").write_text(json.dumps(payload))
                result = manager.status()
                self.assertIn(result["state"], panel_update.STATES)
                self.assertNotIn("PRIVATE-SENTINEL", json.dumps(result))
                self.assertTrue(result["counts"] is None or result["counts"]["seen"] == 0)
        (manager.runtime / "latest.json").write_text("{broken json")
        self.assertIn(manager.status()["state"], ("idle", "unknown"))

    def test_supervisor_timeout_stops_worker_and_preserves_failure_after_restart(self):
        manager = self.manager()
        class Process:
            pid = 99999999
            def wait(self, timeout=None):
                raise panel_update.subprocess.TimeoutExpired("synthetic worker", timeout)
        self.process_mock.side_effect = lambda *_a, **_kw: Process()
        with patch.object(manager, "_stop") as stop:
            self.assertTrue(manager.start()["accepted"])
            self.assertEqual(self.wait_finished(manager)["state"], "timeout")
            self.assertGreaterEqual(stop.call_count, 1)
        self.assertEqual(self.manager().status()["state"], "timeout")

    def test_nonzero_child_exit_cannot_be_presented_as_success(self):
        manager = self.manager()
        release, reaped = self.fake_worker(manager, code=9)
        self.assertTrue(manager.start()["accepted"])
        release.set()
        self.assertTrue(reaped.wait(2))
        self.assertEqual(self.wait_finished(manager)["state"], "error")

    def test_watcher_preserves_completion_for_recovery_after_generation_write_failure(self):
        for state in ("success", "partial"):
            with self.subTest(state=state):
                manager = self.manager()
                handle = run_state.acquire_lock(self.cfg)
                self.assertIsNotNone(handle)
                self.addCleanup(handle.close)
                task = "completed-" + state
                result = {**panel_update._empty(state), "task_id": task,
                          "completion_ready": True, "refresh_available": True}
                manager.result_path.write_text(json.dumps(result))
                snapshot = manager.runtime / "config.yaml"
                snapshot.write_text("queries: [synthetic]\n")
                original = run_state.atomic_json

                def fail_generation(path, value):
                    if Path(path).name == "generation.json":
                        raise OSError("synthetic generation write failure")
                    original(path, value)

                class Process:
                    def wait(self, timeout=None):
                        return 0

                with patch.object(run_state, "atomic_json", side_effect=fail_generation):
                    manager._watch(Process(), handle, snapshot, result)
                self.assertTrue(handle.closed)
                self.assertFalse(snapshot.exists())
                self.assertNotEqual(run_state.generation(self.cfg), task)
                self.assertIs(json.loads((manager.runtime / "latest.json").read_text())["completion_ready"], True)
                restarted = self.manager()
                recovered = restarted.status()
                self.assertEqual(recovered["state"], state)
                self.assertEqual(recovered["generation"], task)
                generation_path = manager.runtime / "generation.json"
                stamp = generation_path.stat().st_mtime_ns
                self.assertEqual(restarted.status()["generation"], task)
                self.assertEqual(generation_path.stat().st_mtime_ns, stamp)

    def test_start_failure_cannot_overwrite_a_run_started_after_unlock(self):
        manager = self.manager()
        acquire = run_state.acquire_lock
        handle = acquire(self.cfg)
        self.assertIsNotNone(handle)
        contender = []
        def cleanup():
            handle.close()
            for lock in contender:
                lock.close()
        self.addCleanup(cleanup)

        class ReleasingHandle:
            def fileno(inner):
                return handle.fileno()
            def close(inner):
                handle.close()
                newer = acquire(self.cfg)
                self.assertIsNotNone(newer)
                contender.append(newer)
                run_state.publish(self.cfg, {"state": "running", "task_id": "newer-scheduled",
                                             "kind": "run", "trigger": "scheduled"})

        self.process_mock.side_effect = OSError("synthetic start failure")
        with patch.object(run_state, "acquire_lock", return_value=ReleasingHandle()):
            failed = manager.start()
        self.assertFalse(failed["accepted"])
        self.assertEqual(failed["error"], "start_failed")
        latest = run_state.latest(self.cfg)
        self.assertEqual(latest["task_id"], "newer-scheduled")
        self.assertEqual(latest["state"], "running")
        self.assertFalse((manager.runtime / "config.yaml").exists())

    def test_completion_marker_requires_a_boolean_true(self):
        for marker in (True, False, 1, "true", None):
            with self.subTest(marker=marker):
                safe = panel_update._safe_state({"state": "success", "completion_ready": marker})
                self.assertIs(safe["completion_ready"], marker is True)

    def test_zero_exit_without_terminal_result_is_not_success(self):
        manager = self.manager()
        class Process:
            def wait(self, timeout=None):
                return 0
        self.process_mock.side_effect = lambda *_a, **_kw: Process()
        self.assertTrue(manager.start()["accepted"])
        self.assertEqual(self.wait_finished(manager)["state"], "error")


if __name__ == "__main__":
    unittest.main(verbosity=2)
