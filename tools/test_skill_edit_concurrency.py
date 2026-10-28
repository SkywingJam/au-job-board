"""Synthetic overlay restoration, cross-process serialization and publication."""
from __future__ import annotations

import multiprocessing
import os
import sqlite3
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from jobs import db, skills


def competing_edit(vocab, database, token, attempted, reading, release, results):
    """Pause inside the locked read, without fabricating any file/SQL results."""
    conn = db.connect(database)
    real_load = skills.load_base

    def gated_load(path=None):
        reading.set()
        if release is not None and not release.wait(10):
            raise RuntimeError("test did not release first editor")
        return real_load(path)

    try:
        attempted.set()
        with patch.object(skills, "load_base", gated_load):
            results.put(skills.edit_and_sync(conn, vocab, token, "add"))
    finally:
        conn.close()


class SkillEditsTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="skill-edits-synthetic-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.vocab = self.root / "vocabulary.yaml"
        self.vocab.write_text("version: 1\nskills:\n- name: Kubernetes\n  aliases: [K8s]\n  not: []\n")
        self.database = self.root / "jobs.db"
        self.conn = db.connect(self.database)
        self.addCleanup(self.conn.close)
        for source_id, title in [("a", "Alpha Beta"), ("b", "K8s")]:
            db.upsert_job(self.conn, {"source": "synthetic", "source_id": source_id,
                                      "title": title})
        self.conn.commit()
        skills.sync(self.conn, skills.load_skills(self.vocab))

    def edit(self, token, action, target=None):
        return skills.edit_and_sync(self.conn, self.vocab, token, action, target)

    def test_restore_seed_identity_alias_only_jobs_and_repeated_remove(self):
        self.edit("Kubernetes", "remove")
        restored = self.edit("kUbErNeTeS", "add")
        self.assertEqual(restored["skill"], "Kubernetes")
        self.assertIsInstance(restored["counts"], dict)
        self.assertEqual(db.skills_map(self.conn)["synthetic:b"], ["Kubernetes"])
        local = skills.load_local(self.vocab)
        self.assertEqual(local["remove"], [])
        self.assertEqual(local["add"], [])
        self.edit("Kubernetes", "remove")
        self.assertNotIn("k8s", skills.canonical_tokens(skills.load_skills(self.vocab)))

    def test_restore_seed_alias_and_display_choice_stays_cleared(self):
        self.edit("K8s", "display_name", "Kubernetes")
        self.edit("k8s", "remove")
        self.edit("K8S", "alias", "Kubernetes")
        self.assertEqual(db.skills_map(self.conn)["synthetic:b"], ["Kubernetes"])
        self.assertEqual(skills.display_names(skills.load_skills(self.vocab)),
                         {"Kubernetes": "Kubernetes"})
        self.assertEqual(skills.load_local(self.vocab)["remove"], [])

    def test_reassigned_seed_name_does_not_revive_alias_only_tags(self):
        self.edit("Kubernetes", "remove")
        self.edit("Alpha", "add")
        result = self.edit("Kubernetes", "alias", "Alpha")
        self.assertIsInstance(result["counts"], dict)
        self.assertNotIn("synthetic:b", db.skills_map(self.conn))
        fresh = skills.load_skills(self.vocab)
        self.assertEqual(skills.canonical_tokens(fresh)["kubernetes"], "Alpha")
        self.assertNotIn("k8s", skills.canonical_tokens(fresh))

    def test_reassigned_seed_alias_remove_and_reload_keep_one_owner(self):
        self.edit("K8s", "remove")
        self.edit("Alpha", "add")
        self.edit("K8s", "alias", "Alpha")
        self.assertEqual(db.skills_map(self.conn)["synthetic:b"], ["Alpha"])
        self.assertEqual(skills.canonical_tokens(skills.load_skills(self.vocab))["k8s"], "Alpha")
        self.edit("Alpha", "remove")
        self.assertNotIn("synthetic:b", db.skills_map(self.conn))
        self.assertNotIn("k8s", skills.canonical_tokens(skills.load_skills(self.vocab)))

    def test_atomic_replace_preserves_mode_and_failed_replace_preserves_file(self):
        self.edit("Alpha", "add")
        overlay = skills.local_path_for(self.vocab)
        overlay.chmod(0o640)
        before = overlay.read_bytes()
        real_replace = os.replace

        def check_publication(source, target):
            self.assertEqual(overlay.read_bytes(), before)
            self.assertIn("Beta", Path(source).read_text())
            return real_replace(source, target)

        with patch.object(skills.os, "replace", check_publication):
            self.edit("Beta", "add")
        if os.name == "nt":
            from jobs.file_security import validate_private_file
            validate_private_file(overlay)
        else:
            self.assertEqual(stat.S_IMODE(overlay.stat().st_mode), 0o640)
        before = overlay.read_bytes()
        with patch.object(skills.os, "replace", side_effect=OSError("synthetic failure")):
            with self.assertRaises(OSError):
                self.edit("Gamma", "add")
        self.assertEqual(overlay.read_bytes(), before)
        self.assertEqual(list(self.root.glob("*.tmp")), [])
        self.edit("Gamma", "add")  # Failed publication releases the lock.

    def test_private_temp_setup_failure_preserves_overlay_and_retry(self):
        self.edit("Alpha", "add")
        overlay = skills.local_path_for(self.vocab)
        before = overlay.read_bytes()
        with patch.object(skills, "apply_file_mode", side_effect=OSError("synthetic ACL failure")):
            with self.assertRaises(OSError):
                self.edit("Beta", "add")
        self.assertEqual(overlay.read_bytes(), before)
        self.assertEqual(list(self.root.glob("*.tmp")), [])
        self.edit("Beta", "add")
        self.assertIn("beta", skills.canonical_tokens(skills.load_skills(self.vocab)))

    def test_database_contention_and_sync_failure_are_retryable(self):
        blocker=db.connect(self.database)
        self.addCleanup(blocker.close)
        self.conn.execute("PRAGMA busy_timeout=30")
        blocker.execute("BEGIN IMMEDIATE")
        try:
            with self.assertRaises(sqlite3.OperationalError):
                self.edit("Alpha","add")
            self.assertFalse(skills.local_path_for(self.vocab).exists())
        finally:
            blocker.rollback()
        self.edit("Alpha","add")
        before=skills.local_path_for(self.vocab).read_bytes()
        with patch.object(skills,"sync_incremental",side_effect=RuntimeError("synthetic sync failure")):
            with self.assertRaises(RuntimeError):
                self.edit("Beta","add")
        self.assertEqual(skills.local_path_for(self.vocab).read_bytes(),before)
        self.edit("Beta","add")
        self.assertEqual(db.skills_map(self.conn)["synthetic:a"],["Alpha","Beta"])

    def test_missing_overlay_is_private_and_invalid_edit_does_not_write(self):
        with self.assertRaises(ValueError):
            self.edit("", "add")
        overlay = skills.local_path_for(self.vocab)
        self.assertFalse(overlay.exists())
        self.edit("Alpha", "add")
        if os.name == "nt":
            from jobs.file_security import validate_private_file
            validate_private_file(overlay)
        else:
            self.assertEqual(stat.S_IMODE(overlay.stat().st_mode), 0o600)

    def test_overlay_symlink_keeps_target_and_permissions(self):
        actual = self.root / "actual-overlay.yaml"
        actual.write_text("version: 1\nadd: []\n")
        actual.chmod(0o640)
        overlay = skills.local_path_for(self.vocab)
        try:
            overlay.symlink_to(actual)
        except OSError as exc:
            if os.name == "nt":
                self.skipTest(f"symlink unavailable: {exc}")
            raise
        self.edit("Alpha", "add")
        self.assertTrue(overlay.is_symlink())
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(actual.stat().st_mode), 0o640)
        self.assertIn("alpha", skills.canonical_tokens(skills.load_skills(self.vocab)))

    def run_competitors(self, different_database):
        ctx = multiprocessing.get_context("spawn")
        release = ctx.Event()
        first_read, second_read = ctx.Event(), ctx.Event()
        first_attempt, second_attempt = ctx.Event(), ctx.Event()
        results = ctx.Queue()
        second_db = self.database
        if different_database:
            second_db = self.root / "other-config.db"
            other = db.connect(second_db)
            db.upsert_job(other, {"source": "synthetic", "source_id": "other",
                                 "title": "Alpha Beta"})
            other.commit()
            other.close()
        # Two different vocabulary names in one directory share one overlay.
        alias_vocab = self.root / "alternate-vocabulary.yaml"
        alias_vocab.write_bytes(self.vocab.read_bytes())
        a = ctx.Process(target=competing_edit,
                        args=(self.vocab, self.database, "Alpha", first_attempt,
                              first_read, release, results))
        b = ctx.Process(target=competing_edit,
                        args=(alias_vocab, second_db, "Beta", second_attempt,
                              second_read, None, results))
        try:
            a.start()
            self.assertTrue(first_read.wait(10))
            b.start()
            self.assertTrue(second_attempt.wait(10))
            self.assertFalse(second_read.wait(0.3), "second editor read before acquiring overlay lock")
            release.set()
            for process in (a, b):
                process.join(10)
                self.assertEqual(process.exitcode, 0)
            returned = [results.get(timeout=2), results.get(timeout=2)]
            self.assertEqual({item["skill"] for item in returned}, {"Alpha", "Beta"})
            tokens = skills.canonical_tokens(skills.load_skills(self.vocab))
            self.assertEqual(tokens["alpha"], "Alpha")
            self.assertEqual(tokens["beta"], "Beta")
            conn = db.connect(second_db)
            try:
                actual = db.skills_map(conn)
                skills.sync(conn, skills.load_skills(self.vocab))
                self.assertEqual(actual, db.skills_map(conn))
            finally:
                conn.close()
        finally:
            release.set()
            for process in (a, b):
                if process.pid is not None and process.is_alive():
                    process.terminate()
                    process.join(3)
            results.close()

    def test_competing_process_edits_preserved_and_tags_consistent(self):
        self.run_competitors(False)

    def test_same_overlay_coordinates_different_database_configs(self):
        self.run_competitors(True)


if __name__ == "__main__":
    unittest.main()
