"""Synthetic regression checks for persistence, grouping and report boundaries."""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from jobs import db, labels, panel_data, report
from jobs.config_edit import ConfigEditor, ConfigEditError, MAX_BYTES
from jobs.dedupe import assign_groups
from jobs.sources import seek


class PatchBoundaries(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="jobs-patch-synthetic-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.vocab = self.root / "skills.yaml"
        self.vocab.write_text("skills: []\n", encoding="utf-8")
        self.config = {"db_path": str(self.root / "jobs.db"),
                       "skills_path": str(self.vocab),
                       "report": {"out_dir": str(self.root / "out")}}

    def connect(self):
        conn = db.connect(self.config["db_path"])
        self.addCleanup(conn.close)
        return conn

    def test_competing_partial_labels_preserve_both_fields(self):
        conn = self.connect()
        labels.set_label(conn, "synthetic:a", note="Original")
        selected, release, second_done = (threading.Event() for _ in range(3))
        errors = []

        class PausedConnection:
            def __init__(self, inner):
                self.inner = inner
            def __getattr__(self, name):
                return getattr(self.inner, name)
            def execute(self, sql, *args):
                result = self.inner.execute(sql, *args)
                if sql.startswith("SELECT * FROM labels WHERE uid"):
                    row = result.fetchone()
                    selected.set()
                    if not release.wait(3):
                        raise RuntimeError("synthetic scheduling timeout")
                    class Captured:
                        def fetchone(self):
                            return row
                    return Captured()
                return result

        def run(first):
            inner = db.connect(self.config["db_path"])
            try:
                if first:
                    labels.set_label(PausedConnection(inner), "synthetic:a", interest="want")
                else:
                    labels.set_label(inner, "synthetic:a", note="New note")
            except BaseException as exc:
                errors.append(repr(exc))
            finally:
                inner.close()
                if not first:
                    second_done.set()

        a = threading.Thread(target=run, args=(True,))
        b = threading.Thread(target=run, args=(False,))
        a.start()
        try:
            self.assertTrue(selected.wait(3))
            b.start()
            second_done.wait(.2)
        finally:
            release.set()
            a.join(5)
            if b.ident is not None:
                b.join(5)
        self.assertFalse(a.is_alive() or b.is_alive())
        self.assertEqual(errors, [])
        saved = labels.get_labels(conn)["synthetic:a"]
        self.assertEqual((saved["note"], saved["interest"]), ("New note；", "want"))

    def test_group_clear_preserves_peer_history_and_unrelated_fields(self):
        conn = self.connect()
        for sid in ("a", "b"):
            db.upsert_job(conn, {"source": "synthetic", "source_id": sid, "title": sid})
        conn.execute("UPDATE jobs SET dedupe_group='synthetic:a'")
        conn.commit()
        labels.set_label(conn, "synthetic:b", note="Peer history", action="applied", interest="want")
        labels.set_label(conn, "synthetic:a", note=None, action=None)
        kept, _, saved, index = report.load(conn, self.config)
        merged = report.label_of(kept[0], saved, index)
        self.assertIsNone(merged["note"])
        self.assertIsNone(merged["action"])
        self.assertIsNone(merged["applied_at"])
        self.assertEqual(merged["interest"], "want")
        self.assertEqual(labels.get_labels(conn)["synthetic:b"]["note"], "Peer history；")
        # An unrelated later edit on the old member must not revive its cleared field.
        labels.set_label(conn, "synthetic:b", eligibility="eligible")
        kept, _, saved, index = report.load(conn, self.config)
        self.assertIsNone(report.label_of(kept[0], saved, index)["note"])
        labels.set_label(conn, "synthetic:b", note="New intentional note")
        kept, _, saved, index = report.load(conn, self.config)
        self.assertEqual(report.label_of(kept[0], saved, index)["note"], "New intentional note；")

    def test_group_reapplication_and_formatting_keep_semantic_order(self):
        for kind in ("reapply", "tidy_clear", "tidy_new"):
            with self.subTest(kind=kind):
                conn = db.connect(self.root / (kind + ".db"))
                self.addCleanup(conn.close)
                with patch.object(db, "utcnow", return_value="2030-01-01T00:00:01+00:00"):
                    if kind == "reapply":
                        labels.set_label(conn,"b",note="Original",action="applied")
                    else:
                        conn.execute("INSERT INTO labels(uid,note,labeled_at) VALUES (?,?,?)",
                                     ("b","Legacy note","2030-01-01T00:00:01+00:00"))
                        conn.commit()
                with patch.object(db,"utcnow",return_value="2030-01-01T00:00:02+00:00"):
                    labels.set_label(conn,"a",note="New choice" if kind=="tidy_new" else None,action=None)
                with patch.object(db,"utcnow",return_value="2030-01-01T00:00:03+00:00"):
                    if kind=="reapply":
                        labels.set_label(conn,"b",note="Original",action="applied")
                    else:
                        labels.tidy_notes(conn,apply=True)
                merged=labels.merge_labels(list(labels.get_labels(conn,with_events=True).values()))
                self.assertEqual(merged["note"],{"reapply":"Original；","tidy_clear":None,"tidy_new":"New choice；"}[kind])
                self.assertEqual(merged["action"],"applied" if kind=="reapply" else None)

    def test_invalid_label_fields_roll_back_together(self):
        conn = self.connect()
        labels.set_label(conn, "synthetic:a", note="Before")
        before = conn.execute("SELECT COUNT(*) FROM label_events").fetchone()[0]
        with self.assertRaises(ValueError):
            labels.set_label(conn, "synthetic:a", note="Should roll back", action="invalid")
        self.assertEqual(labels.get_labels(conn)["synthetic:a"]["note"], "Before；")
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM label_events").fetchone()[0], before)

    def test_recreated_label_does_not_replay_deleted_row_events(self):
        conn = self.connect()
        labels.set_label(conn, "b", note="Peer history", action="applied")
        labels.set_label(conn, "a", note=None, action=None)
        self.assertIsNone(labels.merge_labels(list(
            labels.get_labels(conn, with_events=True).values()))["note"])
        self.assertTrue(labels.clear_label(conn, "a"))
        before = list(conn.execute("SELECT * FROM label_events"))
        with self.assertRaises(ValueError):
            labels.set_label(conn, "a", interest="invalid")
        self.assertEqual(list(conn.execute("SELECT * FROM label_events")), before)
        labels.set_label(conn, "a", interest="want")
        merged = labels.merge_labels(list(labels.get_labels(conn, with_events=True).values()))
        self.assertEqual((merged["note"], merged["action"], merged["interest"]),
                         ("Peer history；", "applied", "want"))
        # A subsequent explicit clear belongs to the new row and must still win.
        labels.set_label(conn, "a", note=None, action=None)
        merged = labels.merge_labels(list(labels.get_labels(conn, with_events=True).values()))
        self.assertEqual((merged["note"], merged["action"]), (None, None))

    def test_recreated_legacy_note_can_still_snapshot_current_row(self):
        conn = self.connect()
        labels.set_label(conn, "a", note="Old generation")
        labels.clear_label(conn, "a")
        labels.set_label(conn, "a", interest="want")
        # Simulate a legacy/imported current value with no field event.
        conn.execute("UPDATE labels SET note='Current legacy note' WHERE uid='a'")
        conn.commit()
        labels.set_label(conn, "a", action="saved")
        row = labels.get_labels(conn, with_events=True)["a"]
        self.assertEqual(row["_field_events"]["note"]["source"], "legacy_snapshot")
        self.assertEqual(row["_field_events"]["note"]["new_value"], "Current legacy note；")

    def test_legacy_peer_unrelated_edit_does_not_revive_cleared_note(self):
        cases = (("Legacy note；", None), ("Legacy note", None),
                 ("Legacy note", "before_clear"), ("Legacy note", "after_clear"))
        for index, (original, tidy) in enumerate(cases):
            with self.subTest(original=original, tidy=tidy):
                conn = db.connect(self.root / f"legacy-{index}.db")
                self.addCleanup(conn.close)
                for sid in ("a", "b"):
                    db.upsert_job(conn, {"source": "synthetic", "source_id": sid, "title": sid})
                conn.execute("UPDATE jobs SET dedupe_group='synthetic:a'")
                conn.execute("INSERT INTO labels(uid,note,labeled_at) VALUES (?,?,?)",
                             ("synthetic:b", original, "2030-01-01T00:00:01+00:00"))
                conn.commit()
                if tidy == "before_clear":
                    with patch.object(db, "utcnow", return_value="2030-01-01T00:00:01.5+00:00"):
                        labels.tidy_notes(conn, apply=True)
                with patch.object(db, "utcnow", return_value="2030-01-01T00:00:02+00:00"):
                    labels.set_label(conn, "synthetic:a", note=None)
                if tidy == "after_clear":
                    with patch.object(db, "utcnow", return_value="2030-01-01T00:00:02.5+00:00"):
                        labels.tidy_notes(conn, apply=True)
                with patch.object(db, "utcnow", return_value="2030-01-01T00:00:03+00:00"):
                    labels.set_label(conn, "synthetic:b", interest="want")
                kept, _, saved, group_index = report.load(conn, self.config)
                self.assertIsNone(report.label_of(kept[0], saved, group_index)["note"])
                expected_peer = "Legacy note；" if tidy else original
                self.assertEqual(labels.get_labels(conn)["synthetic:b"]["note"], expected_peer)

    def test_oversize_config_candidate_preserves_readable_original(self):
        target = self.root / "config.yaml"
        original = "queries: [synthetic]\n#" + "p" * (MAX_BYTES - 1024 - 23) + "\n"
        target.write_text(original, encoding="utf-8")
        editor = ConfigEditor(self.config, target)
        digest = editor.read()["digest"]
        with self.assertRaises(ConfigEditError):
            editor.save({"digest": digest, "changes": {"queries": ["x" * 1000] * 10}})
        self.assertEqual(target.read_text(), original)
        self.assertEqual(editor.read()["digest"], digest)

    def test_unknown_employers_do_not_establish_duplicate_identity(self):
        jobs = [dict(uid="synthetic:a", company=None, title="Engineer", location="Melbourne"),
                dict(uid="synthetic:b", company="", title="Engineer", location="Melbourne")]
        for city in (False, True):
            result = assign_groups(jobs, include_city=city)
            self.assertNotEqual(result["synthetic:a"], result["synthetic:b"])
        for job in jobs:
            job["company"] = "Synthetic Pty Ltd"
        result = assign_groups(jobs)
        self.assertEqual(result["synthetic:a"], result["synthetic:b"])

    def test_abstract_only_seek_detail_stays_retryable(self):
        for content in ("", "<p> </p>", None):
            body = {"data": {"jobDetails": {"job": {"content": content, "abstract": "Only summary"}}}}
            with patch.object(seek, "urlopen", return_value=io.BytesIO(json.dumps(body).encode())):
                self.assertIsNone(seek.fetch_detail("synthetic"))
        body = {"data": {"jobDetails": {"job": {"content": "<p>Full description</p>", "abstract": "Summary"}}}}
        with patch.object(seek, "urlopen", return_value=io.BytesIO(json.dumps(body).encode())):
            self.assertEqual(seek.fetch_detail("synthetic"), "Full description\n\nSummary")

    def test_zero_score_orders_above_negative_and_wins_duplicate(self):
        rows = [dict(uid="synthetic:zero", source="synthetic", title="Zero", total_score=0, dedupe_group="g"),
                dict(uid="synthetic:negative", source="synthetic", title="Negative", total_score=-1, dedupe_group="g")]
        self.assertEqual(sorted(rows, key=report._sort_key)[0]["uid"], "synthetic:zero")
        self.assertEqual(panel_data._ordered(rows, "all", {})[0]["uid"], "synthetic:zero")
        self.assertEqual(report.best_per_group(rows)[0]["uid"], "synthetic:zero")

    def test_csv_scores_preserve_positive_zero_and_negative(self):
        conn = self.connect()
        for title, score in (("Positive", 7), ("Zero", 0), ("Negative", -1)):
            uid = "synthetic:" + title
            db.upsert_job(conn, {"source": "synthetic", "source_id": title, "title": title})
            db.replace_score(conn, uid, "T3", 0, 0, 0, score, [], 1)
        conn.commit()
        info = report.write_report(conn, self.config)
        with (self.root / "out" / (info["stamp"] + "-scored.csv")).open(encoding="utf-8-sig") as stream:
            scores = {row["title"]: row["score"] for row in csv.DictReader(stream)}
        self.assertEqual(scores, {"Positive": "7", "Zero": "0", "Negative": "-1"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
