"""Exact collection and schema-3 regressions; handwritten data, no network."""
from __future__ import annotations

import argparse
from contextlib import closing, redirect_stdout, redirect_stderr
import io
import json
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from jobs import RunTimeout, batches, cli, db, labels, migrations, sources


def job(number, source="seek"):
    return dict(source=source, source_id=str(number), title="Synthetic Developer",
                company="Synthetic Workshop " + str(number), location="Melbourne VIC",
                description="Handwritten synthetic role.")


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="fetch-batches-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / "jobs.db"
        self.config = {"db_path": str(self.path), "queries": ["synthetic"],
                       "report": {"out_dir": str(self.root / "reports")}}
        self.config_path = self.root / "config.yaml"
        self.config_path.write_text(yaml.safe_dump(self.config))
        self.rules = self.root / "rules.yaml"
        self.rules.write_text("version: 1\nexclude: []\n")
        self.skills = self.root / "skills.yaml"
        self.skills.write_text("skills: []\n")
        self.enterContext(patch.object(socket, "socket", side_effect=AssertionError("network forbidden")))
        self.enterContext(patch.object(socket, "create_connection", side_effect=AssertionError("network forbidden")))
        self.enterContext(redirect_stdout(io.StringIO()))
        self.enterContext(redirect_stderr(io.StringIO()))

    def args(self, **extra):
        return argparse.Namespace(config=str(self.config_path), rules=str(self.rules),
                                  skills=str(self.skills), timeout=0, windows_worker=True, **extra)

    def fetch(self, entries, args=None):
        with patch.object(sources, "iter_sources", return_value=entries):
            return cli._cmd_fetch(args or self.args())

    def read(self):
        conn = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        return closing(conn)

    def test_exact_members_include_existing_and_or_duplicate_new(self):
        with closing(db.connect(self.path)) as conn:
            db.upsert_job(conn, job("old"))
            conn.commit()
        args = self.args(_task_id="synthetic-task", trigger="manual")
        self.assertEqual(self.fetch([
            ("seek", lambda *_: [job("old"), job("new"), job("new")]),
            ("indeed", lambda *_: [job("new"), job("peer", "indeed")]),
        ], args), 0)
        with self.read() as conn:
            value, = batches.list_batches(conn)
            self.assertEqual(value["collection_state"], "success")
            self.assertEqual(value["pipeline_task_id"], "synthetic-task")
            self.assertEqual(value["trigger"], "manual")
            self.assertEqual(value["counts"], {"seen": 3, "inserted": 2, "updated": 1})
            self.assertEqual(value["membership"], "available")
            self.assertEqual(set(value), {"batch_id", "started_at", "finished_at", "collection_state",
                                         "membership", "pipeline_task_id", "trigger", "error", "counts"})
            rows = conn.execute("SELECT * FROM fetch_batch_members ORDER BY uid").fetchall()
            self.assertEqual(set(rows[0].keys()), {"batch_id", "uid", "is_new", "seen_at"})
            self.assertEqual([(r["uid"], r["is_new"]) for r in rows],
                             [("indeed:peer", 1), ("seek:new", 1), ("seek:old", 0)])
            self.assertTrue(all(r["seen_at"] for r in rows))
            self.assertEqual(batches.get_batch(conn, value["batch_id"]), value)
            self.assertIsNone(batches.get_batch(conn, "missing"))

    def test_repeated_fetch_has_separate_ids_and_existing_members(self):
        for _ in range(2):
            self.assertEqual(self.fetch([("seek", lambda *_: [job(1)])]), 0)
        with self.read() as conn:
            newest, older = batches.list_batches(conn)
            self.assertNotEqual(newest["batch_id"], older["batch_id"])
            self.assertEqual(newest["counts"], {"seen": 1, "inserted": 0, "updated": 1})
            self.assertEqual(older["counts"], {"seen": 1, "inserted": 1, "updated": 0})
            self.assertEqual(batches.list_batches(conn, 1, 1), [older])
            self.assertEqual(batches.list_batches(conn, 0), [])
            for limit, offset in [(-1, 0), (1, -1), (1.5, 0), (1, "0")]:
                with self.assertRaises(ValueError):
                    batches.list_batches(conn, limit, offset)

    def test_no_queries_or_selected_source_creates_no_batch(self):
        for config, entries, args in [
            ({**self.config, "queries": []}, [("seek", lambda *_: [])], self.args()),
            (self.config, [], self.args()),
            (self.config, [("seek", lambda *_: [])], self.args(only="indeed")),
        ]:
            self.config_path.write_text(yaml.safe_dump(config))
            self.assertEqual(self.fetch(entries, args), 1)
            with self.read() as conn:
                self.assertEqual(batches.list_batches(conn), [])
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0], 0)

    def test_successful_empty_collection_is_available(self):
        self.assertEqual(self.fetch([("seek", lambda *_: [])]), 0)
        with self.read() as conn:
            value, = batches.list_batches(conn)
            self.assertEqual(value["collection_state"], "success")
            self.assertEqual(value["counts"]["seen"], 0)
            self.assertIsNone(value["pipeline_task_id"])

    def test_source_failures_keep_compatible_return_and_partial_fact(self):
        def fail(*_):
            raise ValueError("synthetic sensitive exception")
        for entries, seen in [([("seek", fail)], 0),
                              ([("seek", lambda *_: [job(1)]), ("indeed", fail)], 1)]:
            self.assertEqual(self.fetch(entries), 0)
            with self.read() as conn:
                value = batches.list_batches(conn, 1)[0]
                self.assertEqual(value["collection_state"], "partial")
                self.assertEqual(value["error"], "source_failed")
                self.assertEqual(value["counts"]["seen"], seen)
                self.assertNotIn("sensitive", json.dumps(value))

    def test_internal_source_failure_is_partial_even_with_results(self):
        def partial(config, _queries):
            config["_run_source_failures"]["seek"] = True
            return [job(1)]
        self.assertEqual(self.fetch([("seek", partial)]), 0)
        with self.read() as conn:
            value, = batches.list_batches(conn)
            self.assertEqual(value["collection_state"], "partial")
            self.assertEqual(value["counts"]["seen"], 1)

    def assert_aborted_source(self, exception, expected):
        original = db.upsert_job
        def upsert(conn, value):
            result = original(conn, value)
            if value["source_id"] == "uncommitted":
                raise exception
            return result
        with patch.object(db, "upsert_job", side_effect=upsert), self.assertRaises(type(exception)):
            self.fetch([("seek", lambda *_: [job("committed")]),
                        ("indeed", lambda *_: [job("pending", "indeed"), job("uncommitted", "indeed")])])
        with self.read() as conn:
            value = batches.list_batches(conn, 1)[0]
            self.assertEqual(value["collection_state"], expected)
            self.assertIsNotNone(value["finished_at"])
            self.assertEqual(value["counts"]["seen"], 1)
            self.assertEqual([r[0] for r in conn.execute("SELECT uid FROM jobs")], ["seek:committed"])
            self.assertEqual([r[0] for r in conn.execute("SELECT uid FROM fetch_batch_members")],
                             ["seek:committed"])

    def test_timeout_preserves_committed_source_and_rolls_back_pending_source(self):
        self.assert_aborted_source(RunTimeout(), "timeout")

    def test_keyboard_interrupt_preserves_committed_source(self):
        self.assert_aborted_source(KeyboardInterrupt(), "interrupted")

    def test_system_exit_preserves_committed_source(self):
        self.assert_aborted_source(SystemExit(143), "interrupted")

    def test_storage_failure_rolls_back_both_job_and_members(self):
        self.assert_aborted_source(sqlite3.OperationalError("synthetic write failure"), "error")

    def test_membership_failure_rolls_back_job(self):
        original = batches.record_member
        def record(conn, batch_id, uid, is_new):
            original(conn, batch_id, uid, is_new)
            raise sqlite3.IntegrityError("synthetic member failure")
        with patch.object(batches, "record_member", side_effect=record), self.assertRaises(sqlite3.IntegrityError):
            self.fetch([("seek", lambda *_: [job(1)])])
        with self.read() as conn:
            self.assertEqual(batches.list_batches(conn)[0]["collection_state"], "error")
            for table in ("jobs", "fetch_batch_members"):
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM " + table).fetchone()[0], 0)

    def test_fetch_exception_timeout_is_reraised(self):
        def timeout(*_):
            raise RunTimeout()
        with self.assertRaises(RunTimeout):
            self.fetch([("seek", timeout)])
        with self.read() as conn:
            self.assertEqual(batches.list_batches(conn)[0]["collection_state"], "timeout")

    def test_final_logging_failure_keeps_members_without_a_legacy_duplicate(self):
        original = db.log_run
        def failed_log(conn, *args):
            original(conn, *args)
            raise sqlite3.OperationalError("synthetic final logging failure")
        with patch.object(db, "log_run", side_effect=failed_log), self.assertRaises(sqlite3.OperationalError):
            self.fetch([("seek", lambda *_: [job(1)])])
        with self.read() as conn:
            value, = batches.list_batches(conn)
            self.assertEqual(value["collection_state"], "error")
            self.assertEqual(value["counts"]["seen"], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0], 0)

    def test_unhandled_process_exit_retains_running_fact_and_committed_members(self):
        script = """
import argparse, os, socket, sys
from unittest.mock import patch
sys.path.insert(0, sys.argv[1])
from jobs import cli, db, sources
def blocked(*args, **kwargs):
    raise AssertionError('network forbidden')
socket.socket = blocked
socket.create_connection = blocked
original = db.upsert_job
def upsert(conn, value):
    result = original(conn, value)
    if value['source_id'] == 'pending':
        os._exit(23)
    return result
def item(value):
    return dict(source='seek', source_id=value, location='Melbourne VIC')
with patch.object(sources, 'iter_sources', return_value=[
        ('seek', lambda *_: [item('committed')]),
        ('indeed', lambda *_: [item('pending')])]), patch.object(db, 'upsert_job', side_effect=upsert):
    cli._cmd_fetch(argparse.Namespace(config=sys.argv[2]))
"""
        result = subprocess.run([sys.executable, "-B", "-c", script, str(ROOT), str(self.config_path)],
                                cwd=self.root, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 23, result.stdout + result.stderr)
        with self.read() as conn:
            value, = batches.list_batches(conn)
            self.assertEqual(value["collection_state"], "running")
            self.assertIsNone(value["finished_at"])
            self.assertEqual(value["counts"]["seen"], 1)
            self.assertEqual([r[0] for r in conn.execute("SELECT uid FROM jobs")], ["seek:committed"])

    def test_analysis_failure_does_not_rewrite_collection(self):
        args = self.args(task_id="pipeline-fails-after-collection")
        with patch.object(sources, "iter_sources", return_value=[("seek", lambda *_: [job(1)])]), \
                patch.object(cli, "cmd_analyze", return_value=1):
            self.assertEqual(cli.cmd_run(args), 1)
        with self.read() as conn:
            value, = batches.list_batches(conn)
            self.assertEqual(value["collection_state"], "success")
            self.assertEqual(value["pipeline_task_id"], args.task_id)
            pipeline = json.loads(conn.execute("SELECT stats FROM runs WHERE kind='run'").fetchone()[0])
            self.assertEqual(pipeline["state"], "error")
            self.assertEqual(value["counts"]["seen"], 1)

    def test_terminal_collection_cannot_be_rewritten(self):
        self.fetch([("seek", lambda *_: [job(1)])])
        with closing(db.connect(self.path)) as conn:
            before, = batches.list_batches(conn)
            batches.finish_batch(conn, before["batch_id"], "error", error="later_failure")
            conn.commit()
            self.assertEqual(batches.get_batch(conn, before["batch_id"]), before)

    def test_legacy_mapping_has_no_guessed_members_or_pipeline_state(self):
        with closing(db.connect(self.path)) as conn:
            db.log_run(conn, "fetch", "2020-01-01", {"seek": {"inserted": 100}})
            run_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            db.log_run(conn, "run", "2020-01-01", {"state": "success"})
            db.log_run(conn, "analyze", "2020-01-01", {})
            db.upsert_job(conn, job("unrelated"))
            conn.commit()
        self.fetch([("seek", lambda *_: [job(1)])])
        with self.read() as conn:
            values = batches.list_batches(conn)
            self.assertEqual(len(values), 2)
            legacy = batches.get_batch(conn, "legacy-" + str(run_id))
            self.assertEqual(legacy["membership"], "unavailable")
            self.assertEqual(legacy["collection_state"], "unknown")
            self.assertIsNone(legacy["counts"])
            self.assertIsNone(legacy["pipeline_task_id"])
            self.assertEqual(values[1], legacy)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM fetch_batch_members").fetchone()[0], 1)

    def test_readers_do_not_recover_running_batches_or_write(self):
        with closing(db.connect(self.path)) as conn:
            batch_id = batches.start_batch(conn)
            conn.commit()
        with self.read() as conn:
            statements = []
            conn.set_trace_callback(statements.append)
            for _ in range(2):
                value, = batches.list_batches(conn)
                self.assertEqual(batches.get_batch(conn, batch_id), value)
                self.assertEqual(value["collection_state"], "running")
                self.assertIsNone(value["finished_at"])
            self.assertTrue(all(s.lstrip().upper().startswith("SELECT") for s in statements))
            self.assertEqual(conn.total_changes, 0)


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="batch-migration-")
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "schema2.db"
        # The pre-batch schema itself, not a schema-3 DB with a lowered version.
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.executescript(db.SCHEMA)
        conn.execute("""CREATE TABLE job_locations(
            location_id TEXT PRIMARY KEY, uid TEXT NOT NULL, raw TEXT NOT NULL)""")
        conn.execute("PRAGMA user_version=2")
        db.upsert_job(conn, job(1))
        conn.execute("INSERT INTO job_locations VALUES('loc-old','seek:1','Melbourne VIC')")
        conn.commit()
        labels.set_label(conn, "seek:1", note="Synthetic note; 优先投递", action="saved")
        db.replace_decision(conn, "seek:1", False, rules_version=7)
        db.replace_score(conn, "seek:1", "T1", 1, 2, 0, 3, [], 7)
        db.log_run(conn, "fetch", "2020-01-01", {})
        conn.commit()
        self.before = self.snapshot(conn)
        conn.close()

    def snapshot(self, conn):
        schema = [tuple(r) for r in conn.execute(
            "SELECT type,name,sql FROM sqlite_master WHERE tbl_name NOT LIKE 'fetch_batch%' ORDER BY name")]
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'fetch_batch%' ORDER BY name")]
        return schema, {table: [tuple(r) for r in conn.execute('SELECT * FROM "' + table + '" ORDER BY rowid')]
                        for table in tables}

    def test_schema2_only_adds_tables_and_reopen_is_idempotent(self):
        with patch.object(migrations, "_migrate_labels", side_effect=AssertionError("legacy conversion reran")):
            for _ in range(2):
                with closing(db.connect(self.path)) as conn:
                    self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 3)
                    self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
                    self.assertEqual(self.snapshot(conn), self.before)
                    self.assertEqual(conn.execute("SELECT COUNT(*) FROM fetch_batch_members").fetchone()[0], 0)
                    self.assertEqual(batches.list_batches(conn)[0]["membership"], "unavailable")

    def test_batch_members_keep_foreign_key_integrity(self):
        with closing(db.connect(self.path)) as conn:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO fetch_batch_members(batch_id, uid, is_new, seen_at) "
                    "VALUES ('missing-batch', 'seek:missing', 1, '2026-10-22T00:00:00Z')")

    def test_failed_ddl_rolls_back_tables_version_and_business_rows_then_retries(self):
        with patch.object(batches, "SCHEMA", batches.SCHEMA + ("INVALID SYNTHETIC SQL",)):
            with self.assertRaises(sqlite3.OperationalError):
                db.connect(self.path)
        with closing(sqlite3.connect(self.path)) as conn:
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 2)
            self.assertEqual(self.snapshot(conn), self.before)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE name LIKE 'fetch_batch%'").fetchone()[0], 0)
        with closing(db.connect(self.path)) as conn:
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 3)
            self.assertEqual(self.snapshot(conn), self.before)

    def test_version_publication_failure_is_atomic(self):
        original = sqlite3.connect
        def connect(*args, **kwargs):
            conn = original(*args, **kwargs)
            def authorize(action, name, value, *_):
                if action == sqlite3.SQLITE_PRAGMA and name == "user_version" and value == "3":
                    return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK
            conn.set_authorizer(authorize)
            return conn
        with patch.object(sqlite3, "connect", side_effect=connect), self.assertRaises(sqlite3.DatabaseError):
            db.connect(self.path)
        with closing(original(self.path)) as conn:
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 2)
            self.assertEqual(self.snapshot(conn), self.before)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE name LIKE 'fetch_batch%'").fetchone()[0], 0)

    def test_schema2_program_refuses_schema3_before_writes(self):
        with closing(db.connect(self.path)):
            pass
        with patch.object(migrations, "SCHEMA_VERSION", 2), self.assertRaisesRegex(ValueError, "newer"):
            db.connect(self.path)
        with closing(sqlite3.connect(self.path)) as conn:
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 3)
            self.assertEqual(self.snapshot(conn), self.before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
