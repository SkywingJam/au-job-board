#!/usr/bin/env python3
"""Synthetic loopback HTTP checks for group-level records and location search."""
import http.client
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jobs import db, labels, panel  # noqa: E402


class LocationHTTP(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "test.db"
        conn = db.connect(self.path)
        db.upsert_job(conn, {
            "source": "synthetic",
            "source_id": "a",
            "title": "Synthetic Developer",
            "company": "Synthetic Team",
            "location": "Sydney NSW / Melbourne VIC",
            "description": "Newly written synthetic text.",
        })
        db.replace_decision(conn, "synthetic:a", False)
        labels.set_label(conn, "synthetic:a", action="applied", note="Synthetic group note")
        conn.commit()
        conn.close()
        Handler = type("Handler", (panel.Handler,), {
            "config": {
                "db_path": str(self.path),
                "panel": {"host": "127.0.0.1"},
                "location": {"local": [r"\bVIC\b", "melbourne"]},
            },
        })
        self.server = panel.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_port
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.patch = patch("jobs.skills.load_skills", return_value={"skills": []})
        self.patch.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.patch.stop()
        self.temp.cleanup()

    def request(self, method, url, data=None, origin=None):
        client = http.client.HTTPConnection("127.0.0.1", self.port)
        headers = {}
        if data is not None:
            headers["Content-Type"] = "application/json"
        if origin:
            headers["Origin"] = origin
        client.request(method, url, body=json.dumps(data) if data is not None else None,
                       headers=headers)
        response = client.getresponse()
        result = (response.status, response.read().decode())
        client.close()
        return result

    def test_group_write_changes_the_whole_group(self):
        status, body = self.request("POST", "/api/label", {
            "uid": "synthetic:a", "field": "action", "value": "saved",
        })
        self.assertEqual(status, 200)
        self.assertNotIn("location_id", json.loads(body))
        conn = db.connect(self.path)
        self.assertEqual(labels.get_labels(conn)["synthetic:a"]["action"], "saved")
        conn.close()

    def test_old_location_scoped_writes_are_rejected_without_partial_write(self):
        for path, payload in [
            ("/api/label", {"uid": "synthetic:a", "location_id": "old", "field": "action", "value": "saved"}),
            ("/api/location", {"uid": "synthetic:a", "location_id": "old", "raw": "Melbourne VIC"}),
        ]:
            status, _ = self.request("POST", path, payload)
            self.assertIn(status, (400, 404))
        conn = db.connect(self.path)
        self.assertEqual(labels.get_labels(conn)["synthetic:a"]["action"], "applied")
        conn.close()

    def test_foreign_origin_blocks_writes(self):
        self.assertEqual(self.request("POST", "/api/label", {
            "uid": "synthetic:a", "field": "priority", "value": True,
        }, "http://foreign.invalid")[0], 403)

    def test_detail_is_group_scoped_and_old_scope_query_is_rejected(self):
        status, body = self.request(
            "GET", "/api/job?uid=synthetic%3Aa&geo=Melbourne%20VIC")
        self.assertEqual(status, 200)
        self.assertIn("Synthetic group note", body)
        self.assertEqual(self.request(
            "GET", "/api/job?uid=synthetic%3Aa&location_id=old")[0], 400)

    def test_ambiguous_locality_is_valid(self):
        status, body = self.request("GET", "/?view=all&geo=Carlton")
        self.assertEqual(status, 200)
        self.assertIn('data-fkey="geo"', body)
        self.assertIn('value="Carlton"', body)
        self.assertNotIn('id="geo-error"', body)

    def test_invalid_region_is_editable_html_but_json_detail_is_strict(self):
        status, body = self.request("GET", "/?view=all&geo=not-a-real-place")
        self.assertEqual(status, 200)
        self.assertIn('id="geo-error"', body)
        self.assertIn("not-a-real-place", body)
        self.assertIn('aria-invalid="true"', body)
        self.assertEqual(self.request(
            "GET", "/api/job?uid=synthetic%3Aa&geo=not-a-real-place")[0], 400)
        self.assertEqual(self.request(
            "GET", "/?view=all&geo=not-a-real-place&mode=office")[0], 200)

    def test_stats_use_group_records(self):
        stats = json.loads(self.request("GET", "/api/stats")[1])
        self.assertEqual(stats["applied"], 1)
        self.assertEqual(stats["saved"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
