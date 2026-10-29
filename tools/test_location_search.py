#!/usr/bin/env python3
"""Synthetic group-representative and three-block location search."""
from __future__ import annotations

import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from jobs import cli, db, labels, panel_data, report  # noqa: E402

CONFIG = {"location": {"local": [r"\bVIC\b", "melbourne"]}}


class LocationSearchQuery(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="jobs-location-search-")
        self.path = Path(self.temp.name) / "synthetic.db"
        self.conn = db.connect(self.path)
        self.patch = patch("jobs.skills.load_skills", return_value={"skills": []})
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.conn.close()
        self.temp.cleanup()

    def add(self, ident, location, score, *, group=None, title=None, excluded=False):
        db.upsert_job(self.conn, {
            "source": "synthetic",
            "source_id": ident,
            "title": title or f"Synthetic role {ident}",
            "company": "Synthetic Company",
            "location": location,
            "description": "Handwritten synthetic job.",
            "listing_date": "2026-10-22",
        })
        db.set_dedupe(self.conn, "synthetic:" + ident, group or ident, group or ident)
        db.replace_decision(self.conn, "synthetic:" + ident, excluded)
        db.replace_score(self.conn, "synthetic:" + ident, "T1", 0, 0, 0, score, [], 1)
        self.conn.commit()
        return "synthetic:" + ident

    def query(self, query, *, view="all", offset=0, limit=100, filters=None,
              candidate_uids=None):
        values = dict(filters or {})
        if query is not None:
            values["geo"] = query
        return panel_data._query(
            self.conn, CONFIG, view, offset, limit, values, None, {"skills": []},
            candidate_uids=candidate_uids)

    def test_fresh_database_has_no_location_storage(self):
        tables = {row[0] for row in self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertFalse({"job_locations", "location_labels", "location_events"} & tables)

    def test_representative_local_then_score_then_title_then_read_order(self):
        self.add("away", "Sydney NSW", 20, group="local-first", title="Away")
        self.add("home", "Melbourne VIC", 1, group="local-first", title="Home")
        self.add("low", "Melbourne VIC", 4, group="score", title="Low")
        self.add("high", "Carlton VIC", 8, group="score", title="High")
        self.add("z", "Melbourne VIC", 8, group="title", title="Zulu")
        self.add("a", "Carlton VIC", 8, group="title", title="Alpha")
        self.add("first", "Melbourne VIC", 8, group="read", title="Same")
        self.add("second", "Melbourne VIC", 8, group="read", title="Same")

        kept, _, _, _ = report.load(self.conn, CONFIG)
        by_group = {row.get("dedupe_group") or row["uid"]: row for row in kept}
        self.assertEqual(by_group["local-first"]["uid"], "synthetic:home")
        self.assertEqual(by_group["score"]["uid"], "synthetic:high")
        self.assertEqual(by_group["title"]["uid"], "synthetic:a")
        self.assertEqual(by_group["read"]["uid"], "synthetic:first")

    def test_blocks_are_ordered_before_pagination(self):
        exact = self.add("exact", "Carlton VIC", 1)
        near = self.add("near", "Melbourne VIC", 100)
        unknown = self.add("unknown", "Undisclosed", 200)
        hidden = self.add("hidden", "Geelong VIC", 300)
        rows, total = self.query("3053", limit=2)
        self.assertEqual(total, 3)
        self.assertEqual([(r["uid"], r["_geo_match"]) for r in rows],
                         [(exact, "exact"), (near, "near")])
        page2, total2 = self.query("3053", offset=2, limit=2)
        self.assertEqual(total2, 3)
        self.assertEqual([(r["uid"], r["_geo_match"]) for r in page2],
                         [(unknown, "unknown")])
        self.assertNotIn(hidden, {r["uid"] for r in rows + page2})

    def test_capital_postcode_uses_cbd_then_other_state_then_unknown(self):
        cbd = self.add("cbd", "Melbourne City Centre VIC", 1, group="cbd")
        suburb = self.add("suburb", "Carlton VIC", 20, group="suburb")
        city = self.add("city", "Melbourne VIC", 10, group="city")
        interstate = self.add("interstate", "Sydney NSW", 99, group="interstate")
        unknown = self.add("unknown", "Undisclosed", 98, group="unknown")
        for query in ("Melbourne", "3000"):
            rows, total = self.query(query)
            self.assertEqual(total, 4)
            self.assertEqual([r["uid"] for r in rows],
                             [cbd, suburb, city, unknown])
            self.assertEqual([r["_geo_match"] for r in rows],
                             ["exact", "near", "near", "unknown"])
            self.assertNotIn(interstate, {r["uid"] for r in rows})

    def test_state_country_and_ambiguous_locality(self):
        vic = self.add("vic", "Carlton VIC", 3)
        nsw = self.add("nsw", "Carlton NSW", 4)
        other = self.add("other", "Brisbane QLD", 5)
        unknown = self.add("unknown", "Undisclosed", 6)
        rows, _ = self.query("VIC")
        self.assertEqual([r["uid"] for r in rows], [vic, unknown])
        self.assertNotIn(nsw, {r["uid"] for r in rows})
        self.assertNotIn(other, {r["uid"] for r in rows})
        rows, _ = self.query("Australia")
        self.assertEqual({r["_geo_match"] for r in rows}, {"exact", "unknown"})
        self.assertEqual([r["uid"] for r in rows], [other, nsw, vic, unknown])
        rows, _ = self.query("Carlton")
        self.assertEqual({r["uid"]: r["_geo_match"] for r in rows}[vic], "exact")
        self.assertEqual({r["uid"]: r["_geo_match"] for r in rows}[nsw], "exact")

    def test_excluded_location_blocks_use_score_order_not_rule_audit_order(self):
        low = self.add("low", "Carlton VIC", 1, group="low", excluded=True)
        high = self.add("high", "Carlton VIC", 9, group="high", excluded=True)
        self.conn.execute("UPDATE decisions SET rule_id='z-rule' WHERE uid=?", (low,))
        self.conn.execute("UPDATE decisions SET rule_id='a-rule' WHERE uid=?", (high,))
        self.conn.commit()
        rows, total = self.query("3053", view="excluded")
        self.assertEqual(total, 2)
        self.assertEqual([row["uid"] for row in rows], [high, low])

    def test_excluded_audit_keeps_every_group_member(self):
        first = self.add("first", "Melbourne VIC", 5, group="audit", excluded=True)
        second = self.add("second", "Carlton VIC", 3, group="audit", excluded=True)
        self.conn.execute("UPDATE decisions SET rule_id='r_company' WHERE uid=?", (first,))
        self.conn.execute("UPDATE decisions SET rule_id='r_title' WHERE uid=?", (second,))
        self.conn.commit()
        for geo in (None, "VIC"):
            with self.subTest(geo=geo):
                rows, total = self.query(geo, view="excluded")
                self.assertEqual(total, 2)
                self.assertEqual({row["uid"] for row in rows}, {first, second})

    def test_group_human_state_and_fixed_representative_precede_search(self):
        self.add("away", "Sydney NSW", 30, group="same", title="Away")
        home = self.add("home", "Melbourne VIC", 2, group="same", title="Home")
        labels.set_label(self.conn, "synthetic:away", action="saved", note="Synthetic group state；")
        rows, total = self.query("Melbourne", filters={"action": "saved"})
        self.assertEqual(total, 1)
        self.assertEqual(rows[0]["uid"], home)
        self.assertEqual(rows[0]["_geo_match"], "near")
        self.assertEqual(rows[0]["_label"]["action"], "saved")

    def test_scoped_query_keeps_the_same_global_representative(self):
        self.add("away", "Sydney NSW", 30, group="same", title="Away")
        home = self.add("home", "Melbourne VIC", 2, group="same", title="Home")
        for geo in (None, "Melbourne"):
            rows, total = self.query(
                geo, candidate_uids={"synthetic:away"})
            self.assertEqual(total, 1)
            self.assertEqual(rows[0]["uid"], home)

    def test_scoped_group_membership_is_same_with_and_without_geo(self):
        kept = self.add("kept", "Melbourne VIC", 8, group="mixed")
        excluded = self.add("excluded", "Melbourne VIC", 4, group="mixed", excluded=True)
        plain, plain_total = self.query(
            None, view="excluded", candidate_uids={kept})
        located, located_total = self.query(
            "VIC", view="excluded", candidate_uids={kept})
        self.assertEqual((plain_total, located_total), (1, 1))
        self.assertEqual([row["uid"] for row in plain], [excluded])
        self.assertEqual([row["uid"] for row in located], [excluded])

    def test_cli_report_without_rules_argument_uses_default_rules_file(self):
        self.add("home", "Melbourne VIC", 1, group="same")
        self.add("away", "Sydney NSW", 9, group="same")
        package_root = Path(self.temp.name) / "project"
        (package_root / "jobs").mkdir(parents=True)
        default_rules = package_root / "rules.yaml"
        default_rules.write_text(
            "version: 1\nlocation:\n  local: ['\\\\bNSW\\\\b', sydney]\n",
            encoding="utf-8")
        config = {
            "db_path": str(self.path),
            "skills_path": None,
            "report": {"out_dir": str(Path(self.temp.name) / "out")},
        }
        args = SimpleNamespace(config="synthetic-config.yaml", rules=None, skills=None)
        with patch("jobs.cli.__file__", str(package_root / "jobs" / "cli.py")), \
             patch.object(cli, "_open", side_effect=lambda _args: (config, db.connect(self.path))), \
             redirect_stdout(StringIO()):
            self.assertEqual(cli.cmd_report(args), 0)
        self.assertEqual(Path(config["rules_path"]), default_rules.resolve())
        kept, _, _, _ = report.load(self.conn, config)
        chosen = next(row for row in kept if (row.get("dedupe_group") or row["uid"]) == "same")
        self.assertEqual(chosen["uid"], "synthetic:away")

    def test_source_outside_current_report_remains_clickable(self):
        self.add("away", "Sydney NSW", 20, group="same", title="Away")
        self.add("home", "Melbourne VIC", 1, group="same", title="Home")
        row = __import__("jobs.panel", fromlist=["_find"])._find(
            self.conn, CONFIG, "synthetic:away")
        self.assertEqual(row["uid"], "synthetic:away")

    def test_detail_keeps_full_group_sources_for_every_uid(self):
        from jobs import location_query, panel_views

        home = self.add("home", "Melbourne VIC", 1, group="same")
        db.upsert_job(self.conn, {
            "source": "other-synthetic", "source_id": "away",
            "title": "Synthetic alternate source", "company": "Synthetic Company",
            "location": "Sydney NSW", "description": "Handwritten alternate listing.",
        })
        away = "other-synthetic:away"
        db.set_dedupe(self.conn, away, "same", "same")
        # Even an excluded peer belongs in the complete group's summary.
        db.replace_decision(self.conn, away, True)
        self.conn.commit()
        rows, total = self.query("Melbourne")
        self.assertEqual((total, rows[0]["uid"]), (1, home))
        for uid, peer in [(home, "other-synthetic@Sydney NSW"),
                          (away, "synthetic@Melbourne VIC")]:
            with self.subTest(uid=uid):
                row = location_query.find(self.conn, CONFIG, uid, {"geo": "Melbourne"})
                self.assertEqual(row["uid"], uid)
                self.assertEqual(row.get("duplicate_count"), 1)
                self.assertEqual(row.get("duplicate_sources"), peer)
                self.assertIn(peer, panel_views._detail_html(row, "en"))
        self.assertEqual(location_query.find(
            self.conn, CONFIG, away, {"geo": "Melbourne"})["_geo_match"], "hidden")

    def test_recommend_view_does_not_offer_location_search(self):
        self.assertNotIn("geo", panel_data.VIEW_FILTER_KEYS["recommend"])
        self.assertNotIn("mode", panel_data.ALL_FILTER_KEYS)


if __name__ == "__main__":
    unittest.main(verbosity=2)
