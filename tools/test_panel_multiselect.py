"""Panel multi-select bulk marking: same-origin POST /api/labels/batch and its rendered hooks.

Handwritten synthetic jobs, a temporary SQLite file and a loopback listener only.
The endpoint must reuse labels.parse_progress_items / set_progress_batch unchanged
(one transaction, group-aware, absolute assignment, whole-batch rejection), record
source='panel', not depend on agent_api.allow_label_writes, and keep the panel's
own Host/Origin and body checks. The browser behavior (selection, long press,
gates) is exercised by tools/test_panel_navigation.mjs.
"""
from __future__ import annotations

import datetime as dt
import http.client
import json
import re
import sqlite3
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from jobs import db as database  # noqa: E402
from jobs import i18n, labels as labels_mod, panel, panel_views  # noqa: E402

PATH = "/api/labels/batch"
LONG_TITLE = 'Title with "quotes" & <tags> ' + "very long " * 12


def seed(conn, count=101) -> None:
    today = dt.date.today().isoformat()
    for n in range(count):
        sid = f"j{n:03d}"
        database.upsert_job(conn, {
            "source": "seek", "source_id": sid, "title": LONG_TITLE if n == 0 else f"Synthetic Role {sid}",
            "company": "Example Co", "location": "Melbourne VIC", "listing_date": today,
            "description": "Synthetic description with Python.", "url": f"https://example.invalid/job/{sid}"})
        database.set_dedupe(conn, f"seek:{sid}", sid, sid)
        database.replace_decision(conn, f"seek:{sid}", 0)
        database.replace_score(conn, f"seek:{sid}", "T1", 3, 0, 0, 500 - n, "[]", 5)
    # Two rows of one duplicate group: the panel lists one card, both UIDs exist.
    for sid in ("pairA", "pairB"):
        database.upsert_job(conn, {
            "source": "seek", "source_id": sid, "title": f"Synthetic Pair {sid}", "company": "Example Co",
            "location": "Sydney NSW", "listing_date": today, "description": "Synthetic description.",
            "url": f"https://example.invalid/job/{sid}"})
        database.set_dedupe(conn, f"seek:{sid}", "pair", "g-pair")
        database.replace_decision(conn, f"seek:{sid}", 0)
        database.replace_score(conn, f"seek:{sid}", "T1", 3, 0, 0, 1, "[]", 5)
    conn.commit()


class PanelMultiselectTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        (root / "skills.yaml").write_text("skills: []\n", encoding="utf-8")
        # Token-API write gating is deliberately off: the panel's own write
        # permission must not depend on it.
        self.config = {"db_path": str(root / "synthetic.db"), "skills_path": str(root / "skills.yaml"),
                       "agent_api": {"allow_label_writes": False}, "panel": {"host": "127.0.0.1"}}
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
        self.temp.cleanup()

    # -- helpers --------------------------------------------------------------
    def raw(self, path, method="POST", body=None, headers=None):
        client = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        try:
            client.request(method, path, body=body, headers=headers or {})
            response = client.getresponse()
            data = response.read()
            try:
                return response.status, json.loads(data)
            except ValueError:
                return response.status, data
        finally:
            client.close()

    def post(self, payload, *, headers=None, path=PATH):
        body = payload if isinstance(payload, (str, bytes)) else json.dumps(payload)
        merged = {"Content-Type": "application/json", "Origin": "http://" + self.host}
        merged.update(headers or {})
        return self.raw(path, "POST", body, merged)

    def batch(self, items, **kwargs):
        return self.post({"items": items}, **kwargs)

    def db(self):
        conn = database.connect(self.config["db_path"])
        conn.row_factory = sqlite3.Row
        return conn

    def snapshot(self):
        conn = self.db()
        try:
            return ([tuple(r) for r in conn.execute("SELECT * FROM labels ORDER BY uid")],
                    [tuple(r) for r in conn.execute("SELECT * FROM label_events ORDER BY event_id")])
        finally:
            conn.close()

    def assert_nothing_written(self, before):
        self.assertEqual(self.snapshot(), before)

    @staticmethod
    def uid(n):
        return f"seek:j{n:03d}"

    # -- success and semantics --------------------------------------------------
    def test_assigns_action_and_priority_with_source_panel_and_ignores_token_api_gate(self):
        status, reply = self.batch([{"uid": self.uid(1), "action": "saved"},
                                    {"uid": self.uid(2), "priority": True},
                                    {"uid": self.uid(3), "action": "applied", "priority": True}])
        self.assertEqual(status, 200, reply)
        self.assertEqual(reply, {"ok": True, "updated": 3, "unchanged": 0, "items": [
            {"uid": self.uid(1), "action": "saved", "priority": False, "changed": True},
            {"uid": self.uid(2), "action": None, "priority": True, "changed": True},
            {"uid": self.uid(3), "action": "applied", "priority": True, "changed": True}]})
        conn = self.db()
        try:
            labels = {r["uid"]: dict(r) for r in conn.execute("SELECT * FROM labels")}
            self.assertEqual(labels[self.uid(1)]["action"], "saved")
            self.assertIsNone(labels[self.uid(1)]["priority"])
            self.assertEqual(labels[self.uid(2)]["priority"], 1)
            self.assertEqual(labels[self.uid(3)]["action"], "applied")
            sources = {r[0] for r in conn.execute("SELECT DISTINCT source FROM label_events")}
            self.assertEqual(sources, {"panel"})
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM label_events").fetchone()[0], 4)
        finally:
            conn.close()

    def test_absolute_repeat_is_a_noop_and_status_and_star_are_independent(self):
        self.assertEqual(self.batch([{"uid": self.uid(1), "action": "saved"}])[0], 200)
        before = self.snapshot()
        status, reply = self.batch([{"uid": self.uid(1), "action": "saved"}])
        self.assertEqual(status, 200)
        self.assertEqual(reply["items"], [{"uid": self.uid(1), "action": "saved", "priority": False, "changed": False}])
        self.assertEqual((reply["updated"], reply["unchanged"]), (0, 1))
        self.assertEqual(self.snapshot(), before)           # no event, no timestamp refresh
        # Star leaves the status alone; a later status leaves the star alone.
        status, reply = self.batch([{"uid": self.uid(1), "priority": True}])
        self.assertEqual(reply["items"][0]["action"], "saved")
        self.assertTrue(reply["items"][0]["priority"])
        status, reply = self.batch([{"uid": self.uid(1), "action": "skipped"}])
        self.assertEqual(reply["items"][0]["action"], "skipped")
        self.assertTrue(reply["items"][0]["priority"])

    def test_group_members_share_one_effective_state(self):
        status, reply = self.batch([{"uid": "seek:pairA", "action": "saved"}])
        self.assertEqual(status, 200)
        self.assertEqual(reply["items"][0]["action"], "saved")
        status, reply = self.batch([{"uid": "seek:pairB", "action": "saved"}])   # group already saved
        self.assertEqual(status, 200)
        self.assertEqual(reply["items"][0], {"uid": "seek:pairB", "action": "saved", "priority": False,
                                             "changed": False})
        status, reply = self.batch([{"uid": "seek:pairB", "action": "applied"}])
        self.assertEqual(reply["items"][0]["action"], "applied")
        # Reading the other member shows the group's state.
        status, reply = self.batch([{"uid": "seek:pairA", "action": "applied"}])
        self.assertFalse(reply["items"][0]["changed"])

    def test_exactly_one_hundred_items_commit_together(self):
        items = [{"uid": self.uid(n), "action": "saved"} for n in range(100)]
        status, reply = self.batch(items)
        self.assertEqual(status, 200, reply)
        self.assertEqual(reply["updated"], 100)
        conn = self.db()
        try:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM labels WHERE action='saved'").fetchone()[0], 100)
        finally:
            conn.close()

    # -- whole-batch rejection, no partial write -----------------------------------
    def test_invalid_batches_are_rejected_without_any_write(self):
        before = self.snapshot()
        good = {"uid": self.uid(1), "action": "saved"}
        cases = {
            "unknown_uid": ([good, {"uid": "seek:nope", "action": "saved"}], 400, {"error": "unknown_uid", "index": 1}),
            "duplicate_uid": ([good, {"uid": self.uid(1), "priority": True}], 400, {"error": "duplicate_uid", "index": 1}),
            "duplicate_group": ([good, {"uid": "seek:pairA", "action": "saved"}, {"uid": "seek:pairB", "action": "saved"}],
                                400, {"error": "duplicate_group", "index": 2}),
            "bad_action": ([good, {"uid": self.uid(2), "action": "eligible"}], 400, {"error": "invalid_item", "index": 1}),
            "numeric_priority": ([good, {"uid": self.uid(2), "priority": 1}], 400, {"error": "invalid_item", "index": 1}),
            "string_priority": ([good, {"uid": self.uid(2), "priority": "true"}], 400, {"error": "invalid_item", "index": 1}),
            "no_field": ([good, {"uid": self.uid(2)}], 400, {"error": "invalid_item", "index": 1}),
            "unknown_key": ([good, {"uid": self.uid(2), "action": "saved", "note": "x"}], 400,
                            {"error": "invalid_item", "index": 1}),
            "empty_uid": ([good, {"uid": "", "action": "saved"}], 400, {"error": "invalid_item", "index": 1}),
            "long_uid": ([good, {"uid": "x" * 513, "action": "saved"}], 400, {"error": "invalid_item", "index": 1}),
            "not_object": ([good, "seek:j002"], 400, {"error": "invalid_item", "index": 1}),
            "empty": ([], 400, {"error": "invalid_request"}),
            "too_many": ([{"uid": self.uid(n), "action": "saved"} for n in range(101)], 400, {"error": "invalid_request"}),
        }
        for name, (items, status, error) in cases.items():
            with self.subTest(name):
                got_status, reply = self.batch(items)
                self.assertEqual((got_status, reply), (status, error))
                self.assert_nothing_written(before)
        with self.subTest("items not a list"):
            self.assertEqual(self.post({"items": {"uid": self.uid(1)}})[0], 400)
            self.assert_nothing_written(before)

    def test_strict_json_and_body_limits(self):
        before = self.snapshot()
        item = '{"uid": "%s", "action": "saved"}' % self.uid(1)
        bodies = {
            "duplicate top-level key": '{"items": [%s], "items": [%s]}' % (item, item),
            "duplicate item key": '{"items": [{"uid": "%s", "action": "saved", "action": "skipped"}]}' % self.uid(1),
            "NaN": '{"items": [{"uid": "%s", "priority": NaN}]}' % self.uid(1),
            "extra top-level key": '{"items": [%s], "extra": true}' % item,
            "missing items": "{}",
            "array body": "[%s]" % item,
            "not json": "items=1",
            "invalid utf-8": b'{"items": [\xff]}',
            "deep nesting": "[" * 100000,
        }
        for name, body in bodies.items():
            with self.subTest(name):
                status, reply = self.post(body)
                self.assertEqual(status, 400)
                self.assertEqual(reply, {"error": "invalid_request"})
                self.assert_nothing_written(before)
        with self.subTest("wrong content type"):
            self.assertEqual(self.post({"items": [{"uid": self.uid(1), "action": "saved"}]},
                                       headers={"Content-Type": "text/plain"})[0], 400)
        with self.subTest("oversize body"):
            padding = json.dumps({"items": [{"uid": self.uid(1), "action": "saved"}], "pad": "x" * (128 * 1024)})
            self.assertEqual(self.post(padding)[0], 400)
        with self.subTest("no content length"):
            status, _ = self.raw(PATH, "POST", None, {"Content-Type": "application/json",
                                                      "Origin": "http://" + self.host})
            self.assertEqual(status, 400)
        self.assert_nothing_written(before)

    def test_only_post_and_only_this_path(self):
        self.assertEqual(self.raw(PATH, "GET")[0], 404)
        self.assertEqual(self.post({"items": []}, path=PATH + "/")[0], 404)
        self.assertEqual(self.post({"items": []}, path="/api/v1/labels/batch")[0], 404)

    # -- same-origin boundary -------------------------------------------------------
    def test_cross_origin_and_foreign_host_are_rejected_before_any_write(self):
        before = self.snapshot()
        items = [{"uid": self.uid(1), "action": "saved"}]
        for name, headers in {
            "foreign origin": {"Origin": "http://evil.example"},
            "scheme mismatch": {"Origin": "https://" + self.host},
            "origin with path": {"Origin": "http://" + self.host + "/x"},
            "foreign host": {"Host": "evil.example"},
            "wrong port": {"Host": "127.0.0.1:1"},
            "userinfo host": {"Host": "user@" + self.host},
        }.items():
            with self.subTest(name):
                status, reply = self.batch(items, headers=headers)
                self.assertEqual((status, reply), (403, {"error": "origin_rejected"}))
                self.assert_nothing_written(before)
        with self.subTest("no origin header is a same-origin non-browser client"):
            client_headers = {"Content-Type": "application/json"}
            status, _ = self.raw(PATH, "POST", json.dumps({"items": items}), client_headers)
            self.assertEqual(status, 200)

    # -- failure and transaction ownership -------------------------------------------
    def test_database_failure_is_503_and_helper_rollback_leaves_no_partial_state(self):
        before = self.snapshot()
        with patch.object(labels_mod, "set_progress_batch", side_effect=sqlite3.OperationalError("locked")):
            status, reply = self.batch([{"uid": self.uid(1), "action": "saved"}])
        self.assertEqual((status, reply), (503, {"error": "write_failed"}))
        self.assert_nothing_written(before)
        with patch.object(labels_mod, "set_progress_batch",
                          side_effect=labels_mod.BatchLabelError("label_not_effective", 0)):
            status, reply = self.batch([{"uid": self.uid(1), "action": "saved"}])
        self.assertEqual((status, reply), (409, {"error": "label_not_effective", "index": 0}))
        # A real mid-batch failure after the first item was written rolls everything back.
        real = labels_mod._set_label
        calls = []

        def flaky(*args, **kwargs):
            calls.append(args[1])
            if len(calls) == 2:
                raise sqlite3.OperationalError("disk I/O error")
            return real(*args, **kwargs)

        with patch.object(labels_mod, "_set_label", side_effect=flaky):
            status, reply = self.batch([{"uid": self.uid(1), "action": "saved"},
                                        {"uid": self.uid(2), "action": "saved"}])
        self.assertEqual((status, reply), (503, {"error": "write_failed"}))
        self.assertEqual(len(calls), 2)
        self.assert_nothing_written(before)
        # Retrying the same request succeeds once the failure is gone.
        status, reply = self.batch([{"uid": self.uid(1), "action": "saved"},
                                    {"uid": self.uid(2), "action": "saved"}])
        self.assertEqual((status, reply["updated"]), (200, 2))

    def test_concurrent_batch_and_single_writes_are_serialised_and_both_land(self):
        results = []

        def run(fn):
            results.append(fn())

        single = lambda: self.raw("/api/label", "POST", json.dumps(  # noqa: E731
            {"uid": self.uid(5), "field": "note", "value": "synthetic note"}),
            {"Content-Type": "application/json", "Origin": "http://" + self.host})
        batch = lambda: self.batch([{"uid": self.uid(5), "priority": True}, {"uid": self.uid(6), "action": "saved"}])  # noqa: E731
        threads = [threading.Thread(target=run, args=(fn,)) for fn in (single, batch, single, batch)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        self.assertEqual(sorted(status for status, _ in results), [200, 200, 200, 200], results)
        conn = self.db()
        try:
            row = dict(conn.execute("SELECT * FROM labels WHERE uid=?", (self.uid(5),)).fetchone())
        finally:
            conn.close()
        self.assertEqual((row["priority"], row["note"]), (1, "synthetic note；"))

    def test_existing_single_label_route_is_unchanged(self):
        status, reply = self.raw("/api/label", "POST", json.dumps(
            {"uid": self.uid(1), "field": "action", "value": "saved"}),
            {"Content-Type": "application/json", "Origin": "http://" + self.host})
        self.assertEqual(status, 200)
        self.assertEqual(reply["value"], "saved")
        # The batch sees the single write's effective state and does not rewrite it.
        status, reply = self.batch([{"uid": self.uid(1), "action": "saved"}])
        self.assertFalse(reply["items"][0]["changed"])

    # -- rendered hooks -----------------------------------------------------------------
    def page(self, lang="zh", query="?view=all"):
        status, html = self.raw("/" + query, "GET", None, {"Accept-Language": lang, "Host": self.host})
        self.assertEqual(status, 200)
        return html.decode("utf-8") if isinstance(html, bytes) else html

    def test_page_has_checkbox_circle_bar_and_keeps_the_search_row_intact(self):
        for lang, expect in (("zh", {"bar": "批量操作", "count": "sel.count", "star": "设为星标", "cancel": "取消", "mark": "标记为",
                                    "marks": ("想投", "已投", "跳过"), "check": "选择：Title with"}),
                             ("en", {"bar": "Bulk actions", "count": "sel.count", "star": "Set star", "cancel": "Cancel", "mark": "Mark as",
                                    "marks": ("To apply", "Applied", "Skipped"), "check": "Select: Title with"})):
            with self.subTest(lang):
                html = self.page(lang)
                self.assertIn('<div class="toolzone"><div class="toolbar">', html)
                self.assertIn('class="search"', html)
                self.assertLess(html.index('class="search"'), html.index('id="selbar"'))
                self.assertIn(f'aria-label="{expect["bar"]}"', html)
                # "Mark as…" is the panel's own popover (choicepick), not a native <select> and not a filter:
                # one trigger that starts disabled (nothing selected) plus the three actions, in order.
                picker = re.search(r'<div class="choicepick selpick" data-sel-mark>(.*?)</div></div>', html, re.S)
                self.assertIsNotNone(picker)
                trigger = re.search(r'<button type="button" class="selbtn selmark"[^>]*>', picker.group(1)).group(0)
                self.assertIn(f'aria-label="{expect["mark"]}"', trigger)
                for attr in ("data-choice-trigger", "data-sel-write", " disabled", 'aria-haspopup="listbox"'):
                    self.assertIn(attr, trigger)
                self.assertNotIn("data-fkey", picker.group(0))
                self.assertIn(f'<span class="v">{expect["mark"]}…</span>', picker.group(1))
                options = re.findall(r'data-choice-value="([^"]*)"[^>]*><span class="choice-label">([^<]*)</span>', picker.group(1))
                self.assertEqual(options, [("saved", expect["marks"][0]), ("applied", expect["marks"][1]),
                                           ("skipped", expect["marks"][2])])
                self.assertNotIn("<select", html)
                self.assertNotIn("data-sel-action", html)
                self.assertRegex(html, r'<button type="button" class="selbtn selstar" data-sel-star data-sel-write disabled ')
                self.assertNotRegex(html, r'data-sel-cancel[^>]*disabled')
                self.assertIn(expect["star"], html)
                self.assertIn(f'>{expect["cancel"]}</button>', html)
                self.assertIn('data-check-uid="seek:j000"', html)
                self.assertIn('role="checkbox" aria-checked="false"', html)
                # The tick is an SVG whose path centre (12, 12) is the centre of its 24-unit box.
                self.assertIn('<svg class="card-tick" viewBox="0 0 24 24" aria-hidden="true" focusable="false">'
                              '<path d="m6 12 4 4 8-8"/></svg>', html)
                # The accessible name is HTML-escaped, never raw markup.
                self.assertIn(expect["check"], html)
                self.assertNotIn("<tags>", html)
                self.assertIn("&lt;tags&gt;", html)
                # The card is no longer role=button (its button would hide the checkbox child).
                card = re.search(r'<div class="card[^"]*" data-uid="seek:j001"[^>]*>', html).group(0)
                self.assertNotIn('role="button"', card)
                self.assertIn('role="group"', card)
                self.assertIn('tabindex="0"', card)
                self.assertEqual(html.count('class="card-check"'), html.count('class="card'
                                                                              + '" data-uid=') + html.count('class="card done" data-uid='))

    def test_script_strings_exist_in_both_languages_with_matching_placeholders(self):
        zh, en = i18n.js_strings("zh"), i18n.js_strings("en")
        keys = [k for k in zh if k.startswith("sel.")]
        self.assertGreaterEqual(len(keys), 15)
        for key in keys:
            with self.subTest(key):
                self.assertIn(key, en)
                self.assertEqual(sorted(re.findall(r"\{(\w+)\}", zh[key])), sorted(re.findall(r"\{(\w+)\}", en[key])))

    def test_card_render_is_pure_and_selection_hooks_do_not_change_other_card_fields(self):
        row = {"uid": "seek:x", "title": "T", "company": "C", "location": "L", "source": "seek", "total_score": 5,
               "_label": {"action": "saved", "priority": 1}}
        html = panel_views._card(row, None, "zh")
        self.assertIn('data-star-uid="seek:x"', html)
        self.assertIn('data-prog-uid="seek:x"', html)
        self.assertIn("onclick=\"openJob('seek:x')\"", html)


if __name__ == "__main__":
    unittest.main(verbosity=2)
