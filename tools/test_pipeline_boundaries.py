"""Bounded offline regressions for pipeline lock and transaction ownership."""
from __future__ import annotations

import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from jobs import cli, db, labels, run_state, sources


class PipelineBoundaries(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="pipeline-boundaries-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.cfg = {
            "db_path": str(self.root / "jobs.db"), "queries": ["synthetic"],
            "seek": {"enabled": True, "detail_sleep": 0},
            "jobspy": {"indeed": {"enabled": False}, "linkedin": {"enabled": False}},
            "report": {"out_dir": str(self.root / "reports")},
            "legacy_rule_profile": {"profile": "485"},
        }
        self.path = self.root / "config.yaml"
        self.path.write_text(yaml.safe_dump(self.cfg))
        self.rules = self.root / "rules.yaml"
        self.rules.write_text(yaml.safe_dump({"version": 1, "exclude": [
            {"id": "citizenship.exclusive", "any": ["SYNTHETIC_MARKER"]}]}))
        self.skills = self.root / "skills.yaml"
        self.skills.write_text("skills: []\n")
        with closing(db.connect(self.cfg["db_path"])) as conn:
            for number in ("1", "2"):
                job = sources.blank_job("seek", number)
                job.update(title="Synthetic Developer " + number, company="Example",
                           url="https://example.invalid/" + number)
                db.upsert_job(conn, job)
            conn.commit()
            labels.set_label(conn, "seek:1", source="test", note="Keep this note")
            self.label = dict(conn.execute("SELECT * FROM labels WHERE uid='seek:1'").fetchone())
        self.enterContext(patch.object(socket, "socket", side_effect=AssertionError("network forbidden")))
        self.enterContext(patch.object(socket, "create_connection", side_effect=AssertionError("network forbidden")))

    def args(self):
        return argparse.Namespace(config=str(self.path), rules=str(self.rules),
                                  skills=str(self.skills), timeout=0, windows_worker=True)

    def child(self, body, *arguments):
        # Independent process, same source, temporary cwd and no real sockets.
        bootstrap = """
import socket, sys
from unittest.mock import patch
sys.path.insert(0, sys.argv[1])
def blocked(*args, **kwargs):
    raise AssertionError('network forbidden')
socket.socket = blocked
socket.create_connection = blocked
"""
        return subprocess.run([sys.executable, "-B", "-c", bootstrap + body, str(ROOT),
                               *map(str, arguments)], cwd=self.root,
                              env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                              stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=15)

    def assert_terminal(self, expected):
        value = run_state.latest(self.cfg)
        self.assertEqual(value["state"], expected)
        with closing(db.connect(self.cfg["db_path"])) as conn:
            result = json.loads(conn.execute(
                "SELECT stats FROM runs WHERE kind='run' ORDER BY run_id DESC LIMIT 1").fetchone()[0])
            self.assertEqual(result["state"], expected)
            self.assertEqual(dict(conn.execute("SELECT * FROM labels WHERE uid='seek:1'").fetchone()), self.label)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM jobs WHERE description IS NOT NULL").fetchone()[0], 0)
        handle = run_state.acquire_lock(self.cfg)
        self.assertIsNotNone(handle)
        if handle:
            handle.close()

    def test_detail_alarm_after_uncommitted_write_records_timeout(self):
        result = self.child("""
import argparse, os, signal
from jobs import cli, sources
args = argparse.Namespace(config=sys.argv[2], rules=sys.argv[3], skills=sys.argv[4], timeout=10, windows_worker=True)
calls = 0
def detail(_source_id):
    global calls
    calls += 1
    if calls == 1:
        return 'Synthetic pending description'
    if os.name == 'nt':
        from jobs import RunTimeout
        raise RunTimeout()  # Real Windows deadline is in test_windows_runtime.
    signal.raise_signal(signal.SIGALRM)
with patch.object(sources, 'iter_sources', return_value=[('seek', lambda *_: [])]), patch.object(cli, '_resolve', return_value=detail):
    code = cli.cmd_run(args)
assert code == 3, code
""", self.path, self.rules, self.skills)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_terminal("timeout")
        self.assertEqual(run_state.latest(self.cfg)["error"], "run_timeout")

    def test_detail_exception_rolls_back_before_terminal_logging(self):
        calls = 0
        def detail(_source_id):
            nonlocal calls
            calls += 1
            if calls == 1:
                return "Synthetic pending description"
            raise RuntimeError("synthetic detail failure")
        with patch.object(sources, "iter_sources", return_value=[("seek", lambda *_: [])]), \
                patch.object(cli, "_resolve", return_value=detail):
            with self.assertRaisesRegex(RuntimeError, "synthetic detail failure"):
                cli.cmd_run(self.args())
        self.assert_terminal("error")
        self.assertEqual(run_state.latest(self.cfg)["error"], "run_failed")

    def test_fetch_timeout_rolls_back_before_terminal_logging(self):
        from jobs import RunTimeout
        original = db.upsert_job
        calls = 0
        jobs = [sources.blank_job("seek", number) for number in ("3", "4")]
        def interrupted(conn, job):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RunTimeout()
            return original(conn, job)
        with patch.object(sources, "iter_sources", return_value=[("seek", lambda *_: jobs)]), \
                patch.object(db, "upsert_job", side_effect=interrupted):
            self.assertEqual(cli.cmd_run(self.args()), 3)
        self.assert_terminal("timeout")
        with closing(db.connect(self.cfg["db_path"])) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 2)

    def test_competing_analyze_cannot_replace_frozen_pipeline_results(self):
        with closing(db.connect(self.cfg["db_path"])) as conn:
            conn.execute("UPDATE jobs SET description='SYNTHETIC_MARKER'")
            conn.commit()
        other = self.root / "other.yaml"
        other.write_text(yaml.safe_dump({**self.cfg, "legacy_rule_profile": {"profile": "citizen"}}))
        original_report = cli.cmd_report
        def report(args):
            child = self.child("""
from jobs import cli
raise SystemExit(cli.main(['--config', sys.argv[2], '--rules', sys.argv[3], '--skills', sys.argv[4], 'analyze']))
""", other, self.rules, self.skills)
            self.assertEqual(child.returncode, 4, child.stdout + child.stderr)
            with closing(db.connect(self.cfg["db_path"])) as conn:
                self.assertEqual(conn.execute("SELECT SUM(is_excluded) FROM decisions").fetchone()[0], 2)
            return original_report(args)
        def fetch(*_):
            # An ordinary file change does not alter this full run's snapshot.
            self.path.write_text(other.read_text())
            return []
        with patch.object(sources, "iter_sources", return_value=[("seek", fetch)]), \
                patch.object(cli, "cmd_report", side_effect=report):
            self.assertEqual(cli.cmd_run(self.args()), 0)
        self.assertEqual(run_state.latest(self.cfg)["state"], "success")
        with closing(db.connect(self.cfg["db_path"])) as conn:
            info = json.loads(conn.execute("SELECT stats FROM runs WHERE kind='analyze' ORDER BY run_id DESC LIMIT 1").fetchone()[0])
            self.assertEqual(info["rule_profile"]["profile"], "485")
            self.assertEqual(dict(conn.execute("SELECT * FROM labels WHERE uid='seek:1'").fetchone()), self.label)
        # Once the pipeline releases ownership, the same CLI operation succeeds.
        child = self.child("""
from jobs import cli
raise SystemExit(cli.main(['--config', sys.argv[2], '--rules', sys.argv[3], '--skills', sys.argv[4], 'analyze']))
""", other, self.rules, self.skills)
        self.assertEqual(child.returncode, 0, child.stdout + child.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
