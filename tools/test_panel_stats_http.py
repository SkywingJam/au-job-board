"""Synthetic loopback checks for the read-only header-count endpoint (BUG-1) and
the server-rendered hooks used by detail navigation, the mobile label sheet,
the mobile filter drawer and the skill candidate list (v1.2.2 PR-B).

The endpoint must reuse the same Host/Origin boundary as the write routes, open
no write path, and report exactly the counts the page renders
(labels.summary); /api/label write handling is exercised only as a client.
"""
from __future__ import annotations

import datetime as dt
import http.client
import json
import re
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import temp_cleanup  # noqa: E402
from jobs import db as database  # noqa: E402
from jobs import labels as labels_mod  # noqa: E402
from jobs import panel, panel_views, skills  # noqa: E402

SKILLS_YAML = """version: 1
skills:
  - name: Amazon Web Services
    display_name: AWS
    aliases: [AWS]
  - name: Google Cloud Platform
    aliases: []
  - name: Kubernetes
    aliases: [K8s]
"""


def seed(conn) -> None:
    today = dt.date.today().isoformat()
    for sid, excluded in (("one", 0), ("two", 0), ("three", 1)):
        database.upsert_job(conn, {
            "source": "seek", "source_id": sid, "title": f"Synthetic Role {sid}",
            "company": "Example Co", "location": "Melbourne VIC", "listing_date": today,
            "description": "Synthetic description with Python and K8s.",
            "url": f"https://example.invalid/job/{sid}"})
        database.replace_decision(conn, f"seek:{sid}", excluded)
        database.replace_score(conn, f"seek:{sid}", "T1", 3, 0, 0, 5, "[]", 5)
    conn.commit()


class StatsHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.skills_path = root / "skills.yaml"
        self.skills_path.write_text(SKILLS_YAML, encoding="utf-8")
        self.config = {"db_path": str(root / "synthetic.db"),
                       "skills_path": str(self.skills_path),
                       "panel": {"host": "panel.internal"}}
        conn = database.connect(self.config["db_path"])
        seed(conn)
        conn.close()
        handler = type("SyntheticHandler", (panel.Handler,), {"config": self.config})
        self.server = panel.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.host = "127.0.0.1:%s" % self.server.server_port

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        temp_cleanup.cleanup(self.temp)

    def request(self, path, method="GET", body=None, headers=None):
        client = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        try:
            client.request(method, path, body=body, headers=headers or {})
            response = client.getresponse()
            raw = response.read()
            try:
                return response.status, json.loads(raw)
            except ValueError:
                return response.status, raw
        finally:
            client.close()

    def stats(self, headers=None):
        return self.request("/api/stats", headers=headers)

    def label(self, uid, field, value):
        return self.request("/api/label", "POST", json.dumps({"uid": uid, "field": field, "value": value}),
                            {"Content-Type": "application/json", "Origin": "http://" + self.host})

    def expected(self):
        conn = database.connect(self.config["db_path"])
        try:
            summary = labels_mod.summary(conn)
        finally:
            conn.close()
        return {key: summary[name] for key, name in panel_views.STAT_KEYS.items()}

    def test_machine_default_is_read_only_and_correction_is_audited(self):
        status, reply = self.label("seek:one", "eligibility", "eligible")
        self.assertEqual(status, 200)
        self.assertFalse(reply["qualification"]["corrected"])
        conn = database.connect(self.config["db_path"])
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM labels").fetchone()[0], 0)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM label_events").fetchone()[0], 0)
        conn.close()
        status, reply = self.label("seek:one", "eligibility", "ineligible")
        self.assertEqual(status, 200)
        self.assertTrue(reply["qualification"]["corrected"])
        self.assertEqual(reply["qualification"]["source"], "panel")
        self.assertEqual(reply["qualification"]["analysis_snapshot"]["is_excluded"], 0)
        status, reply = self.label("seek:one", "eligibility", None)
        self.assertEqual(status, 200)
        self.assertFalse(reply["qualification"]["corrected"])
        self.assertEqual(reply["qualification"]["effective"], "eligible")

    def test_independent_priority_and_partial_cancel_use_stored_values(self):
        self.assertEqual(self.label("seek:one", "note", "Synthetic note")[0], 200)
        status, reply = self.label("seek:one", "priority", True)
        self.assertEqual(status, 200)
        self.assertEqual(reply["value"], 1)
        self.assertIsNone(reply["label"]["action"])
        self.label("seek:one", "action", "saved")
        status, reply = self.label("seek:one", "action", None)
        self.assertEqual(status, 200)
        self.assertIsNone(reply["value"])
        self.assertEqual(reply["label"]["priority"], 1)
        self.assertEqual(reply["label"]["note"], "Synthetic note；")

    def test_new_unsure_and_invalid_priority_are_rejected_without_changes(self):
        self.assertEqual(self.label("seek:one", "eligibility", "unsure")[0], 400)
        self.assertEqual(self.label("seek:one", "priority", "yes")[0], 400)
        conn = database.connect(self.config["db_path"])
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM labels").fetchone()[0], 0)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM label_events").fetchone()[0], 0)
        conn.close()

    def dump(self):
        conn = database.connect(self.config["db_path"])
        try:
            return list(conn.iterdump())
        finally:
            conn.close()

    def test_counts_follow_label_writes_with_unchanged_semantics(self):
        code, body = self.stats()
        self.assertEqual(code, 200)
        self.assertEqual(body, self.expected())
        self.assertEqual(body, {"processed": 0, "saved": 0, "priority": 0, "applied": 0,
                                "labelled": 0, "miss": 0, "false_excl": 0})
        for uid, field, value in (("seek:one", "eligibility", "eligible"),
                                  ("seek:one", "action", "saved"),
                                  ("seek:two", "eligibility", "ineligible"),
                                  ("seek:three", "eligibility", "eligible"),
                                  ("seek:two", "action", "applied")):
            self.assertEqual(self.label(uid, field, value)[0], 200)
            code, body = self.stats()
            self.assertEqual((code, body), (200, self.expected()), (uid, field, value))
        self.assertEqual(body, {"processed": 2, "saved": 1, "priority": 0, "applied": 1,
                                "labelled": 3, "miss": 1, "false_excl": 1})
        # A star alone is labelled but not processed; the queue needs saved + star.
        self.assertEqual(self.label("seek:three", "priority", True)[0], 200)
        self.assertEqual({k: self.stats()[1][k] for k in ("processed", "priority", "labelled")},
                         {"processed": 2, "priority": 0, "labelled": 3})
        self.assertEqual(self.label("seek:one", "priority", True)[0], 200)
        self.assertEqual(self.stats()[1]["priority"], 1)
        # Clearing returns to the same per-record semantics; a note-only row still counts.
        self.assertEqual(self.label("seek:one", "action", None)[0], 200)
        self.assertEqual(self.stats()[1]["saved"], 0)
        self.assertEqual(self.label("seek:new-note", "note", "Synthetic；")[0], 200)
        self.assertEqual(self.stats()[1], self.expected())

    def test_same_origin_and_host_boundary_before_database(self):
        port = str(self.server.server_port)
        for headers in ({"Host": "example.invalid:" + port},
                        {"Host": "127.0.0.1:1"},
                        {"Host": "user@127.0.0.1:" + port},
                        {"Host": "127.0.0.1:not-a-port"},
                        {"Origin": "http://example.invalid"},
                        {"Origin": "null"},
                        {"Origin": "https://" + self.host},
                        {"Origin": "http://" + self.host + "/path"}):
            with self.subTest(headers=headers), patch.object(
                    panel.database, "connect", side_effect=AssertionError("DB opened")):
                code, body = self.stats(headers)
                self.assertEqual(code, 403)
                self.assertEqual(body, {"error": "origin_rejected"})
        for headers in ({}, {"Origin": "http://" + self.host}, {"Host": "localhost:" + port},
                        {"Host": "panel.internal:" + port}):
            with self.subTest(headers=headers):
                self.assertEqual(self.stats(headers)[0], 200)

    def test_endpoint_is_read_only(self):
        self.assertEqual(self.label("seek:one", "eligibility", "eligible")[0], 200)
        before = self.dump()
        with patch.object(labels_mod, "set_label", side_effect=AssertionError("write path")):
            for _ in range(3):
                self.assertEqual(self.stats()[0], 200)
        self.assertEqual(self.dump(), before)
        code, _ = self.request("/api/stats", "POST", "{}", {
            "Content-Type": "application/json", "Origin": "http://" + self.host})
        self.assertEqual(code, 404)
        self.assertEqual(self.dump(), before)


    def test_shortcut_cookie_reaches_detail_and_settings(self):
        for cookie, nxt in ((None, "ArrowRight"), ("jobs_keys=vim", "J"), ("jobs_keys=gamer", "D"),
                            ("jobs_keys=bogus", "ArrowRight")):
            headers = {"Cookie": cookie} if cookie else {}
            code, payload = self.request("/api/job?uid=seek:one", headers=headers)
            self.assertEqual(code, 200)
            self.assertEqual(payload["html"].count(f'aria-keyshortcuts="{nxt}"'), 2, cookie)
        code, raw = self.request("/settings?section=display", headers={"Cookie": "jobs_keys=gamer"})
        self.assertEqual(code, 200)
        self.assertRegex(raw.decode("utf-8"), r'aria-pressed="true" data-keys-choice="gamer"')


class RenderHookTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.skills_path = root / "skills.yaml"
        self.skills_path.write_text(SKILLS_YAML, encoding="utf-8")
        self.config = {"db_path": str(root / "synthetic.db"), "skills_path": str(self.skills_path)}
        self.conn = database.connect(self.config["db_path"])
        seed(self.conn)
        labels_mod.set_label(self.conn, "seek:one", source="panel", eligibility="eligible", action="saved")
        labels_mod.set_label(self.conn, "seek:three", source="panel", eligibility="eligible")
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        temp_cleanup.cleanup(self.temp)

    def page(self, view, lang="zh"):
        return panel.render_page(self.conn, self.config, view, 0, 40, None, {}, lang, None)

    def test_rendered_counts_carry_stat_keys_with_summary_values(self):
        summary = labels_mod.summary(self.conn)
        for view in ("recommend", "all", "new", "labelled", "excluded"):
            for lang in ("zh", "en"):
                html = self.page(view, lang)
                found = re.findall(r'<b data-stat="([a-z_]+)">(\d+)</b>', html)
                self.assertTrue(found, (view, lang))
                for key, value in found:
                    self.assertEqual(int(value), summary[panel_views.STAT_KEYS[key]], (view, lang, key))
        self.assertIn('data-stat="miss"', self.page("excluded"))
        self.assertIn('data-stat="false_excl"', self.page("excluded"))
        # Drawer counters are refreshed by the same keys.
        self.assertEqual(re.findall(r'<div><b data-stat="([a-z]+)">', self.page("all")),
                         ["processed", "saved", "priority", "applied"])

    def test_skill_owner_embed_matches_backend_tokens(self):
        html = self.page("all")
        match = re.search(r"window\.__SKILL_OWNERS__=(\[.*?\]);</script>", html)
        self.assertIsNotNone(match)
        cfg = skills.load_skills(str(self.skills_path))
        self.assertEqual([tuple(pair) for pair in json.loads(match.group(1))],
                         sorted(skills.canonical_tokens(cfg).items()))
        embedded = dict(json.loads(match.group(1)))
        self.assertEqual(embedded["aws"], "Amazon Web Services")
        self.assertEqual(embedded["k8s"], "Kubernetes")
        skills_embed = json.loads(re.search(r"window\.__SKILLS__=(\[.*?\]);</script>", html).group(1))
        aws = next(s for s in skills_embed if s["name"] == "Amazon Web Services")
        self.assertEqual(aws["display_name"], "AWS")

    def test_detail_navigation_and_single_label_sheet(self):
        for lang in ("zh", "en"):
            row = panel._find(self.conn, self.config, "seek:one")
            html = panel._detail_html(row, lang, "vim")   # J/K is the explicit Vim profile
            self.assertEqual(html.count('data-step="-1"'), 2)
            self.assertEqual(html.count('data-step="1"'), 2)
            self.assertIn('aria-keyshortcuts="J"', html)
            self.assertIn('aria-keyshortcuts="K"', html)
            # The note input and label buttons exist once: the mobile sheet reuses them.
            self.assertEqual(html.count("<input data-uid="), 1)
            self.assertEqual(html.count("data-field=\"eligibility\""), 2)
            sheet = html[html.index('id="label-sheet"'):html.index('class="label-sheet-foot"')]
            self.assertIn("<input data-uid=", sheet)
            self.assertIn('data-field="action"', sheet)
            # The job description follows the sheet; on mobile it is the first content.
            self.assertLess(html.index('id="label-sheet"'), html.index('class="jd"'))
            self.assertIn('class="label-sum"', html)
            self.assertIn('aria-controls="label-sheet"', html)

    def test_shortcut_profile_hints_follow_selected_profile(self):
        row = panel._find(self.conn, self.config, "seek:one")
        expected = {"default": ("ArrowLeft", "ArrowRight", "←", "→"), "vim": ("K", "J", "K", "J"),
                    "gamer": ("A", "D", "A", "D")}
        for profile, (prev, nxt, prev_glyph, next_glyph) in expected.items():
            for lang in ("zh", "en"):
                with self.subTest(profile=profile, lang=lang):
                    html = panel._detail_html(row, lang, profile)
                    self.assertEqual(html.count(f'aria-keyshortcuts="{prev}"'), 2)
                    self.assertEqual(html.count(f'aria-keyshortcuts="{nxt}"'), 2)
                    self.assertIn(f'<kbd>{prev_glyph}</kbd>', html)
                    self.assertIn(f'<kbd>{next_glyph}</kbd>', html)
                    others = {k for k in ("ArrowLeft", "ArrowRight", "J", "K", "A", "D")} - {prev, nxt}
                    for other in others:
                        self.assertNotIn(f'aria-keyshortcuts="{other}"', html)
        # Missing or unknown preference is the default profile.
        self.assertEqual(panel._detail_html(row, "en"), panel._detail_html(row, "en", "default"))
        for cookie in (None, "", "jobs_keys=", "jobs_keys=emacs", "jobs_lang=en"):
            self.assertEqual(panel.i18n.resolve_keys(cookie), "default")
        self.assertEqual(panel.i18n.resolve_keys("jobs_lang=en; jobs_keys=gamer"), "gamer")
        for profile in ("default", "vim", "gamer"):
            html = panel.render_settings(self.conn, self.config, "en", None, "display", "auto", profile)
            pressed = re.findall(r'aria-pressed="true" data-keys-choice="(\w+)"', html)
            self.assertEqual(pressed, [profile])
            self.assertIn('onclick="setKeys(\'gamer\')"', html)

    def test_copy_text_is_full_stored_description_and_escaped(self):
        from html.parser import HTMLParser
        row = dict(panel._find(self.conn, self.config, "seek:one"))
        hostile = '</button><script>alert(1)</script><img src=x onerror=alert(2)> & "quoted" \'single\''
        body = "Intro line\r\n" + hostile + "\n\n" + "\n".join(f"Requirement {i}: " + "x" * 120 for i in range(400))
        row.update(title='Engineer <b>"Lead"</b>', company="A&B <Co>", location="Sydney NSW",
                   url="https://example.invalid/job?a=1&b=<2>", description=body)

        class Copy(HTMLParser):
            def __init__(self):
                super().__init__(); self.values = []; self.tags = []
            def handle_starttag(self, tag, attrs):
                self.tags.append(tag)
                self.values += [v for k, v in attrs if k == "data-copy-text"]

        for lang in ("zh", "en"):
            with self.subTest(lang=lang):
                html = panel._detail_html(row, lang)
                parsed = Copy(); parsed.feed(html)
                self.assertEqual(len(parsed.values), 1)
                text = parsed.values[0]
                self.assertNotIn("script", parsed.tags)
                self.assertNotIn("img", parsed.tags)
                self.assertIn("UID: seek:one\n", text)
                self.assertIn('Engineer <b>"Lead"</b>', text)
                self.assertIn("A&B <Co>", text)
                self.assertIn("https://example.invalid/job?a=1&b=<2>", text)
                # The full stored JD, not the rendered or truncated DOM.
                self.assertTrue(text.endswith(body.replace("\r\n", "\n") + "\n"))
                self.assertGreater(len(text), 48000)
                self.assertNotIn("copy-btn", text)
        empty = dict(row, description="")
        parsed = Copy(); parsed.feed(panel._detail_html(empty, "en"))
        self.assertIn("(No description was captured for this posting)", parsed.values[0])

    def test_filter_controls_defer_through_filter_changed(self):
        for view in ("all", "labelled", "recommend", "excluded"):
            html = self.page(view)
            self.assertNotRegex(html, r'data-fkey="[^"]+"[^>]*onchange="applyFilter\(\)"')
            self.assertIn('onchange="filterChanged()"', html)
            self.assertIn('onclick="choiceToggle(event,this)"', html)
            self.assertIn('id="more-done" onclick="moreDone()"', html)
            if view != "recommend":
                self.assertIn('class="fbtn more-btn"', html)
                self.assertIn('class="more-panel"', html)
                self.assertIn('onclick="clearHidden()"', html)
            else:
                # The panel remains as the mobile home for primary filters, but
                # desktop restore is blocked and the visible more button is absent.
                self.assertNotIn('class="fbtn more-btn"', html)
                self.assertIn('class="more-panel sheet-only"', html)
                self.assertIn('id="more-panel" data-keys=""', html)
            self.assertIn(".more-foot{display:none}", html)
            self.assertIn("body.portrait .more-foot{display:flex", html)
            self.assertIn("body.portrait .more-panel .choicepick{width:100%;min-width:0}", html)
            self.assertIn("body.portrait .more-panel .choicepanel{position:static;width:100%;min-width:0;max-width:none", html)
            self.assertNotIn('<select', html)


if __name__ == "__main__":
    unittest.main()
