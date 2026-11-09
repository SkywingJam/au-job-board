#!/usr/bin/env python3
"""v1.3.1 "ineligible last": effective-qualification zones across panel, API and pages.

Every record, label and token is handwritten synthetic data in a temporary
database. The option only reorders a query: no score, rule, label write or
snapshot membership is touched.
"""
from __future__ import annotations

import http.client
import json
import re
import sys
import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from jobs import agent_api as api, db, labels, panel, panel_data, panel_views  # noqa: E402

TOKEN = "synthetic-api-token-not-a-real-secret-000000"
CONFIG = {"location": {"local": [r"\bVIC\b", "melbourne"]}}

# uid -> (location, score, machine decision, manual eligibility, dedupe group)
# decision: None = no decision row, 0 = kept, otherwise the excluding rule.
ROWS = {
    "ok-high": ("Melbourne City Centre VIC", 90, 0, None, None),
    "ok-low": ("Carlton VIC", 10, 0, None, None),
    "no-decision": ("Carlton VIC", 50, None, None, None),
    "legacy-unsure": ("Geelong VIC", 30, 0, "unsure", None),
    "excluded-citizenship": ("Melbourne City Centre VIC", 95, "citizenship", None, None),
    "excluded-experience": ("Carlton VIC", 80, "experience", None, None),
    "excluded-other-rule": ("Undisclosed", 70, "unlisted-rule-kind", None, None),
    "excluded-unsure": ("Carlton VIC", 65, "citizenship", "unsure", None),
    "corrected-eligible": ("Melbourne VIC", 20, "citizenship", "eligible", None),
    "corrected-ineligible": ("Carlton VIC", 99, 0, "ineligible", None),
    "group-a": ("Carlton VIC", 60, 0, None, "group"),
    "group-b": ("Carlton VIC", 5, 0, "ineligible", "group"),
}
FRONT = ["ok-high", "corrected-eligible", "legacy-unsure", "no-decision", "ok-low"]
BACK = ["excluded-citizenship", "corrected-ineligible", "excluded-experience",
        "excluded-other-rule", "excluded-unsure", "group-a"]


def uid(name):
    return "synthetic:" + name


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="jobs-ineligible-last-")
        self.root = Path(self.temp.name)
        self.skills = self.root / "skills.yaml"
        self.skills.write_text("skills: []\n", encoding="utf-8")
        self.path = self.root / "synthetic.db"
        self.config = {**CONFIG, "db_path": str(self.path), "skills_path": str(self.skills),
                       "panel": {"page_size": 40}}
        self.conn = db.connect(self.path)
        for name, (location, score, decision, manual, group) in ROWS.items():
            db.upsert_job(self.conn, {
                "source": "synthetic", "source_id": name, "title": f"Synthetic role {name}",
                "company": "Synthetic Company", "location": location,
                "description": "Handwritten synthetic job.", "listing_date": "2026-10-22",
                "url": f"https://example.invalid/jobs/{name}"})
            db.set_dedupe(self.conn, uid(name), group or name, group or name)
            if decision is not None:
                db.replace_decision(self.conn, uid(name), decision != 0,
                                    rule_id=None if decision == 0 else "R-" + decision,
                                    rule_kind=None if decision == 0 else decision)
            db.replace_score(self.conn, uid(name), "T1", 0, 0, 0, score, [], 1)
            if manual == "unsure":      # legacy value: readable, no longer writable
                self.conn.execute("INSERT INTO labels(uid,eligibility) VALUES (?,?)",
                                  (uid(name), "unsure"))
            elif manual:
                labels.set_label(self.conn, uid(name), source="panel", eligibility=manual)
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def query(self, view="all", filters=None, *, offset=0, limit=100, geo=None):
        values = dict(filters or {})
        if geo:
            values["geo"] = geo
        with patch("jobs.skills.load_skills", return_value={"skills": []}):
            return panel_data._query(self.conn, self.config, view, offset, limit, values,
                                     None, {"skills": []})

    def names(self, *args, **kwargs):
        rows, total = self.query(*args, **kwargs)
        return [row["uid"].split(":", 1)[1] for row in rows], total


class OrderingAndPagination(Fixture):
    def test_off_by_default_keeps_existing_order(self):
        baseline, total = self.names("all")
        self.assertEqual(total, len(ROWS) - 1)           # group-a/b share one row
        scores = {n: ROWS[n][1] for n in baseline}
        self.assertEqual(baseline, sorted(baseline, key=lambda n: -scores[n]))
        self.assertEqual(self.names("all", {})[0], baseline)
        self.assertEqual(self.names("all", {"ineligible_last": ""})[0], baseline)

    def test_effective_qualification_zones_keep_score_order_inside(self):
        names, total = self.names("all", {"ineligible_last": "1"})
        self.assertEqual(total, len(ROWS) - 1)
        # Machine exclusion of any rule kind sinks; a human eligible lifts it,
        # a human ineligible sinks a kept row, and unsure/no decision never sink.
        self.assertEqual(sorted(names[:len(FRONT)]), sorted(FRONT))
        self.assertEqual(sorted(names[len(FRONT):]), sorted(BACK))
        self.assertEqual(names[:len(FRONT)], sorted(FRONT, key=lambda n: -ROWS[n][1]))
        self.assertEqual(names[len(FRONT):], sorted(BACK, key=lambda n: -ROWS[n][1]))

    def test_group_label_decides_for_the_displayed_representative(self):
        rows, _ = self.query("all", {"ineligible_last": "1"})
        by_name = {r["uid"].split(":", 1)[1]: r for r in rows}
        self.assertEqual(by_name["group-a"]["qualification"]["effective"], "ineligible")
        self.assertEqual(by_name["group-a"]["_ineligible_last"], 1)
        self.assertEqual(by_name["ok-high"]["_ineligible_last"], 0)

    def test_sort_choice_and_other_filters_apply_before_and_inside_zones(self):
        by_date, _ = self.names("all", {"ineligible_last": "1", "sort": "date"})
        self.assertEqual(sorted(by_date[:len(FRONT)]), sorted(FRONT))
        names, total = self.names("all", {"ineligible_last": "1", "q": "excluded"})
        self.assertEqual(names, sorted((n for n in ROWS if n.startswith("excluded")),
                                       key=lambda n: -ROWS[n][1]))
        self.assertEqual(total, 4)
        filtered, total = self.names("all", {"ineligible_last": "1", "salary": "1"})
        self.assertEqual((filtered, total), ([], 0))      # nothing states a salary

    def test_zones_are_sorted_over_the_whole_result_before_paging(self):
        full, total = self.names("all", {"ineligible_last": "1"})
        for limit in (1, 2, 3, 4, 5, 7):
            with self.subTest(limit=limit):
                pages = []
                for offset in range(0, total, limit):
                    page, again = self.names("all", {"ineligible_last": "1"},
                                             offset=offset, limit=limit)
                    self.assertEqual(again, total)
                    pages.extend(page)
                self.assertEqual(pages, full)             # no gap, no duplicate
                self.assertEqual(len(set(pages)), total)
        first_page, _ = self.names("all", {"ineligible_last": "1"}, limit=3)
        self.assertTrue(set(first_page) <= set(FRONT))
        last_page, _ = self.names("all", {"ineligible_last": "1"}, offset=6, limit=6)
        self.assertTrue(set(last_page) <= set(BACK))

    def test_location_blocks_stay_inside_each_qualification_zone(self):
        rows, total = self.query("all", {"ineligible_last": "1"}, geo="Melbourne")
        got = [(r["uid"].split(":", 1)[1], r["_ineligible_last"], r["_geo_match"]) for r in rows]
        # Geelong is clearly outside Greater Melbourne; Undisclosed is the fallback.
        self.assertNotIn("legacy-unsure", [g[0] for g in got])
        self.assertEqual(total, len(got))
        zones = [g[1] for g in got]
        self.assertEqual(zones, sorted(zones))
        for zone in (0, 1):
            tiers = [g[2] for g in got if g[1] == zone]
            self.assertEqual(tiers, sorted(tiers, key=("exact", "near", "unknown").index))
        self.assertEqual([g[0] for g in got if g[1] == 0][:1], ["ok-high"])
        self.assertEqual([g[0] for g in got if g[1] == 1][:1], ["excluded-citizenship"])
        self.assertEqual(got[-1][0], "excluded-other-rule")        # fallback after metro, last zone
        plain = self.names("all", {}, geo="Melbourne")[0]
        self.assertEqual(plain[0], "excluded-citizenship")       # off: the old exact-first order

    def test_pages_split_zones_and_location_blocks_without_loss(self):
        full, total = self.names("all", {"ineligible_last": "1"}, geo="Melbourne")
        pages = []
        for offset in range(0, total, 2):
            pages += self.names("all", {"ineligible_last": "1"}, offset=offset, limit=2,
                                geo="Melbourne")[0]
        self.assertEqual(pages, full)

    def test_only_all_new_and_labelled_support_it(self):
        self.assertEqual(panel_data.INELIGIBLE_LAST_VIEWS, ("all", "new", "labelled"))
        for view in ("recommend", "excluded"):
            with self.subTest(view=view):
                self.assertNotIn("ineligible_last", panel_data.VIEW_FILTER_KEYS[view])
                self.assertEqual(self.names(view, {"ineligible_last": "1"}),
                                 self.names(view, {}))
        labelled, _ = self.names("labelled", {"ineligible_last": "1"})
        self.assertEqual(sorted(labelled), sorted(
            ["legacy-unsure", "excluded-unsure", "corrected-eligible",
             "corrected-ineligible", "group-a"]))
        self.assertEqual(labelled[:3], ["legacy-unsure", "corrected-eligible", "corrected-ineligible"][:2]
                         + labelled[2:3])
        self.assertEqual(labelled[-1], "group-a")
        with patch.object(db, "last_batch_started_at", return_value="2000-01-01T00:00:00+00:00"):
            new, _ = self.names("new", {"ineligible_last": "1"})
        self.assertEqual(sorted(new[:len(FRONT)]), sorted(FRONT))

    def test_correction_reorders_the_next_query_without_changing_scores_or_rules(self):
        before, _ = self.query("all", {"ineligible_last": "1"})
        scores = {r["uid"]: (r["total_score"], r["is_excluded"], r["rule_id"]) for r in before}
        labels.set_label(self.conn, uid("ok-high"), source="panel", eligibility="ineligible")
        labels.set_label(self.conn, uid("excluded-citizenship"), source="panel",
                         eligibility="eligible")
        names, _ = self.names("all", {"ineligible_last": "1"})
        self.assertEqual(names[0], "excluded-citizenship")
        self.assertIn("ok-high", names[len(FRONT):])
        self.assertEqual(names[:len(FRONT)], sorted(
            [n for n in FRONT if n != "ok-high"] + ["excluded-citizenship"],
            key=lambda n: -ROWS[n][1]))
        after, _ = self.query("all", {"ineligible_last": "1"})
        self.assertEqual({r["uid"]: (r["total_score"], r["is_excluded"], r["rule_id"])
                          for r in after}, scores)
        labels.set_label(self.conn, uid("ok-high"), source="panel", eligibility=None)
        self.assertEqual(self.names("all", {"ineligible_last": "1"})[0][0], "excluded-citizenship")


class FilterParsing(unittest.TestCase):
    def test_panel_accepts_only_the_explicit_value(self):
        self.assertEqual(panel_data.parse_filters({"ineligible_last": ["1"]}),
                         {"ineligible_last": "1"})
        for value in ("", "0", "on", "true", "yes", "2"):
            with self.subTest(value=value):
                self.assertEqual(panel_data.parse_filters({"ineligible_last": [value]}), {})
        self.assertEqual(panel_data.parse_filters({}), {})

    def test_sorting_option_is_not_a_filter_or_a_second_qualification_axis(self):
        self.assertNotIn("ineligible_last", panel_data.FILTER_KEYS)
        self.assertTrue(panel_data.passes_filters({}, {}, {"ineligible_last": "1"}))


class PanelPage(Fixture):
    def render(self, view="all", filters=None, **kwargs):
        with patch("jobs.skills.load_skills", return_value={"skills": []}):
            return panel_views.render_page(self.conn, self.config, view, 0, 40, None,
                                           filters or {}, "en", None)

    def test_checkbox_lives_in_the_collapsed_job_details_group(self):
        html = self.render("all")
        more = html[html.index('id="more-panel"'):]
        self.assertRegex(html[html.index('id="more-panel"'):][:200], r"hidden>")
        job = more[more.index('<fieldset class="fgroup"><legend>Job details'):]
        job = job[:job.index("</fieldset>")]
        self.assertIn('data-fkey="ineligible_last"', job)
        self.assertIn('value="1"', job[job.index('data-fkey="ineligible_last"'):][:80])
        self.assertIn("Ineligible last", job)
        self.assertNotIn("checked", job[job.index('data-fkey="ineligible_last"'):][:90])
        self.assertNotIn('data-fkey="ineligible_last"', html[:html.index('id="more-panel"')])
        self.assertNotIn("ineligible_last", html[html.index('id="more-panel"') - 3000:
                                                 html.index('id="more-panel"')])

    def test_state_chip_and_clear_all_follow_the_option(self):
        html = self.render("all", {"ineligible_last": "1"})
        self.assertRegex(html, r'data-fkey="ineligible_last" value="1" checked')
        self.assertIn('<span class="achip">Ineligible last<button', html)
        self.assertIn("ineligible_last=1", html)            # pager/nav links keep it
        chips = html[html.index('id="active-chips"'):]
        self.assertIn("removeFilter('ineligible_last')", chips[:600])

    def test_other_views_do_not_offer_or_apply_it(self):
        for view in ("recommend", "excluded"):
            with self.subTest(view=view):
                html = self.render(view, {"ineligible_last": "1"})
                self.assertNotIn('data-fkey="ineligible_last"', html)

    def test_one_divider_between_zones_and_none_when_off(self):
        on = self.render("all", {"ineligible_last": "1"})
        self.assertEqual(on.count('class="geo-divider"'), 1)
        self.assertEqual(self.render("all").count('class="geo-divider"'), 0)
        both = self.render("all", {"ineligible_last": "1", "geo": "Melbourne"})
        self.assertGreaterEqual(both.count('class="geo-divider"'), 2)
        cards = [m.group(1) for m in re.finditer(r'data-uid="(synthetic:[^"]+)"', on)]
        self.assertTrue(cards)

    def test_chinese_label(self):
        with patch("jobs.skills.load_skills", return_value={"skills": []}):
            html = panel_views.render_page(self.conn, self.config, "all", 0, 40, None,
                                           {}, "zh", None)
        self.assertIn("资格不符置后", html)


class PanelHTTP(Fixture):
    def setUp(self):
        super().setUp()
        handler = type("SyntheticHandler", (panel.Handler,), {"config": self.config})
        self.server = panel.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        super().tearDown()

    def get(self, path):
        client = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        try:
            client.request("GET", path)
            response = client.getresponse()
            return response.status, response.read().decode("utf-8")
        finally:
            client.close()

    def test_query_string_round_trip_and_unsupported_views(self):
        status, html = self.get("/?view=all&ineligible_last=1")
        self.assertEqual(status, 200)
        self.assertRegex(html, r'data-fkey="ineligible_last" value="1" checked')
        status, html = self.get("/?view=all&ineligible_last=0")
        self.assertEqual(status, 200)
        self.assertNotRegex(html, r'data-fkey="ineligible_last" value="1" checked')
        for view in ("excluded", "recommend"):
            status, html = self.get(f"/?view={view}&ineligible_last=1")
            self.assertEqual(status, 200)
            self.assertNotIn("ineligible_last", html.split("</header>")[0])
            self.assertNotIn("Clear all</a>", html.split("</header>")[0])


class AgentAPIQueries(Fixture):
    def setUp(self):
        super().setUp()
        self.conn.close()                      # the server opens its own readers
        self.conn = db.connect(self.path)
        self.store = api.SnapshotStore()
        self.server = api.make_server(self.config, TOKEN, port=0, store=self.store,
                                      panel_url="http://127.0.0.1:8299/")
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        super().tearDown()

    def request(self, path):
        client = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=10)
        try:
            client.request("GET", path, headers={"Authorization": "Bearer " + TOKEN})
            response = client.getresponse()
            return response.status, json.loads(response.read())
        finally:
            client.close()

    def listing(self, query=""):
        status, data = self.request("/api/v1/jobs?limit=100" + query)
        self.assertEqual(status, 200, data)
        return data

    def uids(self, data):
        return [item["uid"].split(":", 1)[1] for item in data["items"]]

    def test_omitted_parameter_keeps_the_default_order_and_query(self):
        default = self.listing()
        self.assertNotIn("ineligible_last", default["query"]["filters"])
        self.assertEqual(self.uids(default), self.names("all")[0])

    def test_explicit_parameter_matches_the_panel_query(self):
        data = self.listing("&ineligible_last=1")
        self.assertEqual(data["query"]["filters"]["ineligible_last"], "1")
        self.assertEqual(self.uids(data), self.names("all", {"ineligible_last": "1"})[0])
        located = self.listing("&ineligible_last=1&geo=Melbourne")
        self.assertEqual(self.uids(located),
                         self.names("all", {"ineligible_last": "1"}, geo="Melbourne")[0])
        self.assertEqual({item["location_match"] for item in located["items"]},
                         {"exact", "near", "unknown"})

    def test_pages_follow_one_snapshot_order(self):
        full = self.uids(self.listing("&ineligible_last=1"))
        got, cursor = [], None
        status, page = self.request("/api/v1/jobs?limit=4&ineligible_last=1")
        while True:
            self.assertEqual(status, 200, page)
            got += self.uids(page)
            cursor = page["next_cursor"]
            if cursor is None:
                break
            status, page = self.request("/api/v1/jobs?cursor=" + cursor)
        self.assertEqual(got, full)

    def test_invalid_and_unsupported_values_are_rejected(self):
        for suffix in ("&ineligible_last=0", "&ineligible_last=true", "&ineligible_last=",
                       "&ineligible_last=1&ineligible_last=1"):
            with self.subTest(suffix=suffix):
                status, data = self.request("/api/v1/jobs?view=all" + suffix)
                self.assertEqual(status, 400, data)
        for view in ("recommend", "excluded"):
            with self.subTest(view=view):
                status, data = self.request(f"/api/v1/jobs?view={view}&ineligible_last=1")
                self.assertEqual((status, data["error"]), (400, "filter_not_supported_for_view"))
        for view in ("all", "labelled"):
            status, data = self.request(f"/api/v1/jobs?view={view}&ineligible_last=1")
            self.assertEqual(status, 200, data)
        status, data = self.request("/api/v1/jobs?scope=legacy_new&ineligible_last=1")
        self.assertEqual(status, 200, data)

    def test_snapshot_stays_stable_after_a_correction_and_new_queries_follow_it(self):
        status, first = self.request("/api/v1/jobs?limit=4&ineligible_last=1")
        self.assertEqual(status, 200, first)
        frozen = self.uids(self.listing("&ineligible_last=1"))
        labels.set_label(self.conn, uid("ok-high"), source="panel", eligibility="ineligible")
        labels.set_label(self.conn, uid("excluded-citizenship"), source="panel",
                         eligibility="eligible")
        later_pages = []
        cursor = first["next_cursor"]
        while cursor:
            status, page = self.request("/api/v1/jobs?cursor=" + cursor)
            self.assertEqual(status, 200, page)
            later_pages += self.uids(page)
            cursor = page["next_cursor"]
        self.assertEqual(self.uids(first) + later_pages, frozen)          # same snapshot
        status, detail = self.request(
            "/api/v1/jobs/" + "synthetic%3Aok-high?snapshot_id=" + first["snapshot_id"])
        self.assertEqual(status, 200, detail)
        self.assertEqual(detail["item"]["qualification"]["effective"], "eligible")
        fresh = self.uids(self.listing("&ineligible_last=1"))
        self.assertEqual(fresh[0], "excluded-citizenship")
        self.assertNotEqual(fresh, frozen)
        self.assertEqual(sorted(fresh), sorted(frozen))


class PublicContract(unittest.TestCase):
    def test_documented_in_agent_and_panel_docs(self):
        for name in ("docs/agent-api.md", "docs/mcp.md"):
            with self.subTest(doc=name):
                self.assertIn("ineligible_last", (ROOT / name).read_text(encoding="utf-8"))
        usage = (ROOT / "docs" / "usage.md").read_text(encoding="utf-8")
        self.assertIn("Ineligible last", usage)

    def test_mcp_tool_description_names_the_parameter(self):
        text = (ROOT / "jobs" / "mcp_server.py").read_text(encoding="utf-8")
        self.assertIn("ineligible_last", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
