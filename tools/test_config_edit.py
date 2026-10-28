"""Synthetic/offline configuration roundtrip, mutation boundary and HTTP tests."""
from __future__ import annotations

import io
import argparse
import contextlib
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import yaml
from jobs.config_edit import ConfigEditor, ConfigEditError, MAX_BYTES
from jobs import cli, db, panel, run_state


class ConfigEditorTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="config-editor-synthetic-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "non-default.yaml"
        self.original = ('# retained header\nqueries: [synthetic] # keyword note\n'
                         'db_path: "' + str(self.root / 'data/jobs.db') + '"\n'
                         'private_unknown:\n  password: "synthetic-secret" # private note\n'
                         'seek:\n  enabled: true\n  pages_per_query: 2 # keep note\n'
                         'legacy_rule_profile:\n  profile: "485"\n')
        self.path.write_text(self.original, encoding="utf-8")
        self.path.chmod(0o640)
        self.config = yaml.safe_load(self.original)
        self.editor = ConfigEditor(self.config, self.path, ROOT / "rules.yaml")

    def save(self, changes, digest=None):
        return self.editor.save({"digest": digest or self.editor.read()["digest"], "changes": changes})

    def error(self, code, action):
        with self.assertRaises(ConfigEditError) as caught:
            action()
        self.assertEqual(caught.exception.code, code)

    def test_public_example_and_roundtrip_yaml_numeric_subclasses(self):
        example = (ROOT / "config.example.yaml").read_text(encoding="utf-8")
        self.path.write_text(example, encoding="utf-8")
        result = self.editor.read()
        self.assertEqual(result["values"]["seek.detail_sleep"], 0.25)
        result = self.save({"seek.detail_sleep": 0})
        self.assertEqual(result["values"]["seek.detail_sleep"], 0)
        result = self.save({"seek.detail_sleep": 0.05})
        self.assertEqual(result["values"]["seek.detail_sleep"], 0.05)
        result = self.save({"seek.pages_per_query": 3, "seek.daterange": 0,
                            "jobspy.indeed.results_wanted": 10})
        self.assertEqual(result["values"]["seek.daterange"], 0)
        self.assertEqual(result, self.editor.read())
        # Explicit formatted integers are ruamel ScalarInt instances as well.
        self.path.write_text("queries: [synthetic]\nseek:\n  pages_per_query: 02\n  daterange: 0\n  detail_sleep: 0\n")
        result = self.editor.read()
        self.assertEqual(result["values"]["seek.pages_per_query"], 2)
        self.assertEqual(result["values"]["seek.detail_sleep"], 0)
        for field in ("seek.detail_sleep", "seek.pages_per_query", "seek.daterange", "jobspy.indeed.results_wanted"):
            self.error("validation_failed", lambda field=field: self.save({field: True}))

    def test_saved_text_has_identical_cli_yaml_semantics(self):
        from jobs.config import load_config
        terms = ["on", "yes", "no", "off", "2026-10-17", "012", "null", "1e2", "1.25"]
        self.path.write_text(self.original + "unknown_bool: yes # keep boolean\nunknown_date: 2026-10-17\nunknown_octal: 012\nunknown_string: 'off'\n")
        before = load_config(self.path)
        changes = {"queries": terms, "seek.locations": terms,
                   "jobspy.indeed.location": "on", "jobspy.linkedin.location": "2026-10-17",
                   "jobspy.indeed.country_indeed": "012"}
        result = self.save(changes)
        actual = load_config(self.path)
        self.assertEqual(actual["queries"], terms)
        self.assertEqual(actual["seek"]["locations"], terms)
        self.assertEqual(actual["jobspy"]["indeed"]["location"], "on")
        self.assertEqual(actual["jobspy"]["linkedin"]["location"], "2026-10-17")
        self.assertEqual(actual["jobspy"]["indeed"]["country_indeed"], "012")
        for path, value in changes.items():
            self.assertEqual(result["values"][path], value)
        for key in ("unknown_bool", "unknown_date", "unknown_octal", "unknown_string"):
            self.assertEqual(actual[key], before[key])
            self.assertIs(type(actual[key]), type(before[key]))
        self.assertIn("# keep boolean", self.path.read_text())
        self.assertFalse(self.path.read_text().startswith("%YAML"))
        scalar_paths = ("jobspy.indeed.location", "jobspy.linkedin.location", "jobspy.indeed.country_indeed")
        for term in terms:
            result = self.save({path: term for path in scalar_paths})
            actual = load_config(self.path)
            for path in scalar_paths:
                _, source, field = path.split(".")
                self.assertEqual(actual["jobspy"][source][field], term)
                self.assertIs(type(actual["jobspy"][source][field]), str)
                self.assertEqual(result["values"][path], term)

    def test_external_yaml_uses_same_semantics_as_cli(self):
        self.path.write_text("queries: [on]\n")
        self.error("validation_failed", self.editor.read)
        self.path.write_text("queries: [synthetic]\njobspy: {indeed: {location: 2026-10-17}}\n")
        self.error("validation_failed", self.editor.read)
        self.path.write_text("queries: [synthetic]\nseek: {enabled: yes}\n")
        result = self.editor.read()
        self.assertIs(result["values"]["seek.enabled"], True)

    def test_actual_file_and_public_defaults_only(self):
        result = self.editor.read()
        self.assertEqual(result["values"]["seek.locations"], ["All Australia"])
        self.assertFalse(result["explicit"]["seek.locations"])
        self.assertTrue(result["explicit"]["queries"])
        serialized = json.dumps(result)
        self.assertNotIn("synthetic-secret", serialized)
        self.assertNotIn(str(self.path), serialized)
        self.assertNotIn("db_path", serialized)

    def test_roundtrip_unknown_comments_mode_and_readback(self):
        result = self.save({"queries": ["synthetic analyst"], "seek.pages_per_query": 4})
        text = self.path.read_text()
        self.assertIn("# retained header", text)
        self.assertIn("# keyword note", text)
        self.assertIn("# keep note", text)
        self.assertIn('password: "synthetic-secret" # private note', text)
        if os.name == "nt":
            from jobs.file_security import validate_private_file
            validate_private_file(self.path)
        else:
            self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o640)
        self.assertEqual(result, self.editor.read())
        self.assertEqual(yaml.safe_load(text)["queries"], ["synthetic analyst"])
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_external_change_and_second_tab_conflict(self):
        initial = self.editor.read()
        self.save({"seek.pages_per_query": 3})
        newer = self.path.read_bytes()
        self.error("conflict", lambda: self.save({"seek.pages_per_query": 5}, initial["digest"]))
        self.assertEqual(self.path.read_bytes(), newer)
        current = self.editor.read()
        self.path.write_text(self.path.read_text()+"# external change\n")
        self.error("conflict", lambda: self.save({"seek.pages_per_query": 5}, current["digest"]))

    def test_invalid_fields_and_types_preserve_file(self):
        bad = ({"db_path": "/tmp/other"}, {"seek.enabled": "true"},
               {"seek.pages_per_query": True}, {"seek.pages_per_query": 0},
               {"seek.pages_per_query": 101}, {"queries": []},
               {"queries": ["x\nheader"]}, {"seek.daterange": -1},
               {"seek.detail_sleep": float("nan")}, {"seek.detail_sleep": 61},
               {"legacy_rule_profile.profile": "unknown"},
               {"legacy_rule_profile.exclude.unknown": False})
        for changes in bad:
            before = self.path.read_bytes()
            with self.subTest(changes=changes):
                with self.assertRaises(ConfigEditError):
                    self.save(changes)
                self.assertEqual(self.path.read_bytes(), before)

    def test_disabled_all_sources_can_be_saved(self):
        result = self.save({"seek.enabled": False, "jobspy.indeed.enabled": False,
                            "jobspy.linkedin.enabled": False})
        self.assertFalse(result["values"]["seek.enabled"])
        self.assertFalse(result["values"]["jobspy.indeed.enabled"])
        self.assertFalse(result["values"]["jobspy.linkedin.enabled"])

    def test_profile_overrides_and_reset_follow_preset(self):
        result = self.save({"legacy_rule_profile.profile": "citizen",
                            "legacy_rule_profile.exclude.citizenship.exclusive": True})
        document = yaml.safe_load(self.path.read_text())
        self.assertEqual(document["legacy_rule_profile"]["exclude"], {"citizenship.exclusive": True})
        self.assertTrue(result["effective_rules"]["exclude"]["citizenship.exclusive"])
        result = self.save({"legacy_rule_profile.exclude.citizenship.exclusive": None})
        self.assertFalse(result["effective_rules"]["exclude"]["citizenship.exclusive"])
        self.assertIsNone(result["values"]["legacy_rule_profile.exclude.citizenship.exclusive"])

    def test_busy_lock_blocks_save(self):
        handle = run_state.acquire_lock(self.config)
        try:
            self.error("run_busy", lambda: self.save({"seek.pages_per_query": 3}))
            self.assertEqual(self.path.read_text(), self.original)
        finally:
            handle.close()
        self.save({"seek.pages_per_query": 3})

    def test_private_temp_setup_failure_preserves_original_and_retry(self):
        before = self.path.read_bytes()
        with patch("jobs.config_edit.apply_file_mode", side_effect=OSError("synthetic ACL failure")):
            self.error("write_failed", lambda: self.save({"queries": ["synthetic retry"]}))
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.root.glob("*.tmp")), [])
        self.save({"queries": ["synthetic retry"]})
        self.assertEqual(yaml.safe_load(self.path.read_text())["queries"], ["synthetic retry"])

    def test_atomic_replace_failure_preserves_original_and_cleans_temp(self):
        with patch("jobs.config_edit.os.replace", side_effect=PermissionError):
            self.error("write_failed", lambda: self.save({"seek.pages_per_query": 3}))
        self.assertEqual(self.path.read_text(), self.original)
        self.assertEqual(list(self.root.glob(".*.tmp")), [])

    def test_unsafe_duplicate_alias_merge_and_invalid_yaml_rejected(self):
        documents = ["queries: [x]\nqueries: [y]\n", "queries: !!python/object/apply:os.system [false]\n",
                     "queries: &q [synthetic]\nprivate: *q\n", "queries: [synthetic]\nprivate: {<<: {a: b}}\n",
                     "queries: [unterminated\n", "- list-root\n", "", "queries: [synthetic]\nseek: {enabled: true, enabled: false}\n"]
        for document in documents:
            with self.subTest(document=document):
                self.path.write_text(document)
                self.error("config_invalid", self.editor.read)
                self.assertEqual(self.path.read_text(), document)

    def test_external_edit_during_validation_is_not_overwritten(self):
        original_replace = self.editor._replace
        def concurrent(encoded, digest):
            self.path.write_text(self.path.read_text()+"# concurrent\n")
            original_replace(encoded, digest)
        with patch.object(self.editor, "_replace", side_effect=concurrent):
            self.error("conflict", lambda: self.save({"seek.pages_per_query": 3}))
        self.assertIn("# concurrent", self.path.read_text())
        self.assertIn("pages_per_query: 2", self.path.read_text())

    def test_saved_file_drives_next_update_without_restarting_panel(self):
        from jobs.panel_update import UpdateManager
        manager = UpdateManager(self.config, self.path, rules_path=ROOT / "rules.yaml")
        self.save({"queries": ["changed synthetic term"],
                   "seek.pages_per_query": 7, "legacy_rule_profile.profile": "whv"})
        fresh, preview = manager._fresh()
        self.assertEqual(fresh["legacy_rule_profile"]["profile"], "whv")
        self.assertEqual(preview["queries"], ["changed synthetic term"])
        self.assertEqual(preview["sources"][0]["pages_per_query"], 7)
        self.save({"seek.enabled": False, "jobspy.indeed.enabled": False,
                   "jobspy.linkedin.enabled": False})
        fresh, preview = manager._fresh()
        self.assertIsNone(fresh)
        self.assertEqual(preview["error"], "no_sources")

    def test_demo_refuses_reads_and_writes(self):
        editor = ConfigEditor({**self.config, "demo": {}}, self.path, ROOT/"rules.yaml")
        self.error("demo_disabled", editor.read)

    def seed_analysis(self):
        self.skills_path = self.root / "skills.yaml"
        self.skills_path.write_text("version: 1\nskills: [{name: Python, aliases: []}]\n")
        conn = db.connect(self.config["db_path"])
        self.addCleanup(conn.close)
        db.upsert_job(conn, {"source": "seek", "source_id": "synthetic",
                            "title": "Synthetic Engineer", "company": "Example",
                            "description": "Synthetic Python role", "url": "https://example.invalid/job"})
        conn.commit()
        return conn

    def analyze(self):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return cli.cmd_analyze(argparse.Namespace(
                config=str(self.path), rules=str(ROOT / "rules.yaml"), skills=str(self.skills_path)))

    def test_applied_snapshot_tracks_successful_analyze_and_survives_reload(self):
        self.seed_analysis()
        self.assertEqual(self.editor.read()["applied"]["status"], "unavailable")
        initial = self.editor.read()
        self.assertEqual(self.analyze(), 0)
        applied = self.editor.read()["applied"]
        self.assertEqual(applied["status"], "recorded")
        self.assertEqual(applied["values"], initial["values"])
        self.assertEqual(applied["explicit"], initial["explicit"])
        self.assertEqual(applied["effective_rules"], initial["effective_rules"])
        self.assertIsInstance(applied["analyzed_at"], str)
        saved = self.save({"queries": ["next synthetic term"], "seek.pages_per_query": 7,
                           "legacy_rule_profile.profile": "whv"})
        self.assertEqual(saved["applied"], applied)
        self.assertEqual(saved["values"]["seek.pages_per_query"], 7)
        # Reload uses the committed run, never the panel's startup config.
        fresh = ConfigEditor(yaml.safe_load(self.path.read_text()), self.path, ROOT / "rules.yaml")
        self.assertEqual(fresh.read()["applied"], applied)
        self.assertEqual(self.analyze(), 0)
        newer = fresh.read()
        self.assertEqual(newer["applied"]["values"], newer["values"])
        self.assertEqual(newer["applied"]["effective_rules"], newer["effective_rules"])
        serialized = json.dumps(newer["applied"])
        for private in ("synthetic-secret", "private_unknown", "db_path", str(self.path), str(self.root)):
            self.assertNotIn(private, serialized)

    def test_failed_analyze_does_not_advance_applied_snapshot(self):
        conn = self.seed_analysis()
        self.assertEqual(self.analyze(), 0)
        applied = self.editor.read()["applied"]
        before = conn.execute("SELECT COUNT(*) FROM runs WHERE kind='analyze'").fetchone()[0]
        self.save({"seek.pages_per_query": 8})
        with patch("jobs.cli._sync_skills_transactional", side_effect=RuntimeError("synthetic failure")):
            self.assertEqual(self.analyze(), 1)
        self.assertEqual(self.editor.read()["applied"], applied)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM runs WHERE kind='analyze'").fetchone()[0], before)

    def test_full_run_applies_same_locked_configuration_and_pre_analyze_failure_does_not(self):
        self.seed_analysis()
        self.assertEqual(self.analyze(), 0)
        applied = self.editor.read()["applied"]
        self.save({"seek.pages_per_query": 9, "legacy_rule_profile.profile": "whv"})
        args = argparse.Namespace(config=str(self.path), rules=str(ROOT / "rules.yaml"),
                                  skills=str(self.skills_path), timeout=0, windows_worker=True)
        # Real pipeline orchestration and analyze; only network/report stages mocked.
        with patch("jobs.cli.cmd_fetch", return_value=1), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli.cmd_run(args), 1)
        self.assertEqual(self.editor.read()["applied"], applied)
        with patch("jobs.cli.cmd_fetch", return_value=0), patch("jobs.cli.cmd_details", return_value=0), \
                patch("jobs.cli.cmd_report", return_value=0), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.cmd_run(args), 0)
        latest = self.editor.read()
        self.assertEqual(latest["applied"]["values"], latest["values"])
        self.assertEqual(latest["applied"]["effective_rules"], latest["effective_rules"])

    def test_missing_old_or_corrupt_snapshot_does_not_infer_or_create(self):
        self.assertFalse(Path(self.config["db_path"]).exists())
        self.assertEqual(self.editor.read()["applied"]["status"], "unavailable")
        self.assertFalse(Path(self.config["db_path"]).exists())
        self.assertEqual(self.save({"seek.pages_per_query": 3})["applied"]["status"], "unavailable")
        conn = self.seed_analysis()
        self.assertEqual(self.analyze(), 0)
        for stats in ({}, {"config_snapshot": "synthetic-secret"},
                      {"config_snapshot": {"version": 1, "values": {}, "explicit": {}, "effective_rules": {}}}):
            db.log_run(conn, "analyze", db.utcnow(), stats)
            conn.commit()
            view = self.editor.read()
            self.assertEqual(view["applied"]["status"], "unavailable")
            self.assertNotIn("synthetic-secret", json.dumps(view))
        conn.execute("UPDATE runs SET stats='invalid json' WHERE kind='analyze'")
        conn.commit()
        self.assertEqual(self.editor.read()["applied"]["status"], "unavailable")

    def test_snapshot_allowlist_rejects_malformed_values_and_omits_unknowns(self):
        conn = self.seed_analysis()
        self.assertEqual(self.analyze(), 0)
        stats = json.loads(conn.execute("SELECT stats FROM runs WHERE kind='analyze'").fetchone()[0])
        snapshot = stats["config_snapshot"]
        snapshot["values"]["password"] = "synthetic-secret"
        snapshot["explicit"]["password"] = True
        snapshot["effective_rules"]["exclude"]["private-rule"] = "synthetic-secret"
        conn.execute("UPDATE runs SET stats=? WHERE kind='analyze'", (json.dumps(stats),))
        conn.commit()
        self.assertEqual(self.editor.read()["applied"]["status"], "recorded")
        self.assertNotIn("synthetic-secret", json.dumps(self.editor.read()))
        snapshot["values"]["seek.enabled"] = "synthetic-secret"
        conn.execute("UPDATE runs SET stats=? WHERE kind='analyze'", (json.dumps(stats),))
        conn.commit()
        self.assertEqual(self.editor.read()["applied"]["status"], "unavailable")

    def test_legacy_baseline_persists_edits_reload_and_revert_without_claiming_applied(self):
        conn = self.seed_analysis()
        db.log_run(conn, "analyze", db.utcnow(), {})
        conn.commit()
        initial = self.editor.read()
        self.assertEqual(initial["baseline"]["status"], "unavailable")
        self.assertFalse(self.editor._baseline_path().exists())
        first = self.save({"seek.pages_per_query": 5})
        self.assertEqual(first["applied"]["status"], "unavailable")
        self.assertEqual(first["baseline"]["status"], "recorded")
        self.assertEqual(first["baseline"]["source"], "file_before_edit")
        self.assertEqual(first["baseline"]["values"], initial["values"])
        self.assertEqual(first["values"]["seek.pages_per_query"], 5)
        second = self.save({"seek.pages_per_query": 7})
        self.assertEqual(second["baseline"], first["baseline"])
        fresh = ConfigEditor(yaml.safe_load(self.path.read_text()), self.path, ROOT / "rules.yaml")
        self.assertEqual(fresh.read()["baseline"], first["baseline"])
        reverted = fresh.save({"digest": fresh.read()["digest"], "changes": {"seek.pages_per_query": 2}})
        self.assertEqual(reverted["values"], reverted["baseline"]["values"])
        self.assertEqual(reverted["effective_rules"], reverted["baseline"]["effective_rules"])
        for private in ("synthetic-secret", "private_unknown", "db_path", str(self.path), str(self.root)):
            self.assertNotIn(private, self.editor._baseline_path().read_text())

    def test_baseline_get_and_noop_dont_write_failure_preserves_file(self):
        baseline_path = self.editor._baseline_path()
        self.assertEqual(self.editor.read()["baseline"]["status"], "unavailable")
        self.assertFalse(baseline_path.parent.exists())
        self.assertFalse(Path(self.config["db_path"]).exists())
        self.save({})
        self.save({"seek.pages_per_query": 2})
        self.assertFalse(baseline_path.exists())
        with patch("jobs.run_state.atomic_json", side_effect=OSError):
            self.error("write_failed", lambda: self.save({"seek.pages_per_query": 5}))
        self.assertEqual(self.path.read_text(), self.original)
        self.assertFalse(baseline_path.exists())
        with patch.object(self.editor, "_replace", side_effect=ConfigEditError("write_failed")):
            self.error("write_failed", lambda: self.save({"seek.pages_per_query": 5}))
        current = self.editor.read()
        self.assertEqual(current["baseline"]["values"], current["values"])
        self.assertEqual(current["applied"]["status"], "unavailable")
        self.assertEqual(self.path.read_text(), self.original)

    def test_baseline_context_invalidates_source_rules_or_new_analysis(self):
        conn = self.seed_analysis()
        self.save({"seek.pages_per_query": 5})
        self.assertEqual(self.editor.read()["baseline"]["status"], "recorded")
        other_path = self.root / "another-config.yaml"
        other_path.write_text(self.path.read_text())
        other = ConfigEditor(self.config, other_path, ROOT / "rules.yaml")
        self.assertEqual(other.read()["baseline"]["status"], "unavailable")
        rules_path = self.root / "rules-copy.yaml"
        rules_path.write_bytes((ROOT / "rules.yaml").read_bytes())
        other = ConfigEditor(self.config, self.path, rules_path)
        self.assertEqual(other.read()["baseline"]["status"], "unavailable")
        other.save({"digest": other.read()["digest"], "changes": {"seek.pages_per_query": 6}})
        self.assertEqual(other.read()["baseline"]["status"], "recorded")
        rules_path.write_text(rules_path.read_text()+"\n# changed rules file\n")
        self.assertEqual(other.read()["baseline"]["status"], "unavailable")
        # Any newer completed analyze invalidates old pre-edit history, even when
        # that analyze cannot supply a proven applied snapshot.
        self.save({"seek.pages_per_query": 7})
        db.log_run(conn, "analyze", db.utcnow(), {})
        conn.commit()
        self.assertEqual(self.editor.read()["baseline"]["status"], "unavailable")

    def test_recorded_applied_supersedes_baseline_and_unknown_fields_are_omitted(self):
        self.seed_analysis()
        self.save({"seek.pages_per_query": 5})
        info = json.loads(self.editor._baseline_path().read_text())
        info["values"]["password"] = "synthetic-secret"
        self.editor._baseline_path().write_text(json.dumps(info))
        self.assertNotIn("synthetic-secret", json.dumps(self.editor.read()["baseline"]))
        self.assertEqual(self.analyze(), 0)
        current = self.editor.read()
        self.assertEqual(current["applied"]["status"], "recorded")
        self.assertEqual(current["applied"]["values"], current["values"])
        self.assertEqual(current["baseline"]["status"], "unavailable")


class LocalRequestTest(unittest.TestCase):
    def handler(self, host="127.0.0.1:8090", origin="http://127.0.0.1:8090", payload=None):
        import email.message
        handler = object.__new__(panel.Handler)
        handler.headers = email.message.Message()
        body = json.dumps(payload if payload is not None else {}).encode()
        for key, value in {"Host": host, "Origin": origin, "Content-Type": "application/json", "Content-Length": str(len(body))}.items():
            handler.headers[key] = value
        handler.rfile = io.BytesIO(body)
        handler.server = type("Server", (), {"server_address": ("127.0.0.1", 8090)})()
        handler._json = lambda obj, status, started: setattr(handler, "response", (obj, status))
        return handler

    def test_local_request_accepted_and_foreign_origin_rejected(self):
        handler = self.handler(payload={"digest": "a"*64, "changes": {}})
        self.assertEqual(handler._local_json(0,65536)["digest"], "a"*64)
        for args in ({"host": "evil.test:8090"}, {"origin": "https://evil.test"}, {"host": "127.0.0.1:1"}):
            with self.subTest(args=args):
                handler = self.handler(**args)
                self.assertIsNone(handler._local_json(0))
                self.assertEqual(handler.response[1], 403)

    def test_config_post_accepts_large_valid_edit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            path.write_text("queries: [synthetic]\n")
            editor = ConfigEditor({"db_path": str(Path(directory) / "jobs.db")}, path, ROOT / "rules.yaml")
            queries = [str(i) + "x" * 997 for i in range(70)]
            payload = {"digest": editor.read()["digest"], "changes": {"queries": queries}}
            self.assertGreater(len(json.dumps(payload).encode()), 65536)
            handler = self.handler(payload=payload)
            handler.path = "/api/config-settings"
            handler.server.config_editor = editor
            handler.do_POST()
            self.assertEqual(handler.response[1], 200)
            self.assertEqual(editor.read()["values"]["queries"], queries)

    def test_config_post_rejects_body_above_editor_limit_without_reading(self):
        handler = self.handler()
        handler.path = "/api/config-settings"
        handler.headers.replace_header("Content-Length", str(MAX_BYTES + 1))
        with patch.object(handler.rfile, "read", side_effect=AssertionError("oversized body read")):
            handler.do_POST()
        self.assertEqual(handler.response[1], 400)
        self.assertTrue(handler.close_connection)

    def test_container_wildcard_keeps_local_host_origin_boundary(self):
        with patch.dict(os.environ, {"JOBS_CONTAINER": "1"}):
            handler = self.handler()
            handler.server.server_address = ("0.0.0.0",8090)
            self.assertEqual(handler._local_json(0), {})
            handler = self.handler(host="evil.test:8090",origin="http://evil.test:8090")
            handler.server.server_address = ("0.0.0.0",8090)
            self.assertIsNone(handler._local_json(0))
            self.assertEqual(handler.response[1],403)


if __name__ == "__main__":
    unittest.main()
