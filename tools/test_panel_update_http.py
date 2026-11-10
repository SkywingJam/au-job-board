"""Synthetic loopback write boundaries; never launches a pipeline."""
from __future__ import annotations

import http.client
import json
import sqlite3
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from jobs import panel, skills


class FakeUpdates:
    def __init__(self):
        self.calls = 0
        self.next = {"state": "running", "accepted": True}

    def preview(self):
        return {"ok": True, "queries": ["fictional engineer"], "sources": []}

    def status(self):
        return {"state": "idle", "error": None}

    def start(self):
        self.calls += 1
        return self.next


class JoinedServer(panel.ThreadingHTTPServer):
    # server_close() then waits for handler threads, so none still holds the
    # SQLite file when the temporary directory is removed (Windows locks it).
    daemon_threads = False


class UpdateHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.skills_path = root / "skills.yaml"
        self.skills_path.write_text("version: 1\nskills: []\n", encoding="utf-8")
        self.config = {"db_path": str(root / "synthetic.db"),
                       "skills_path": str(self.skills_path),
                       "panel": {"host": "panel.internal"}}
        self.manager = FakeUpdates()
        handler = type("SyntheticHandler", (panel.Handler,), {"config": self.config})
        self.server = JoinedServer(("127.0.0.1", 0), handler)
        self.server.update_manager = self.manager
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.host = "127.0.0.1:%s" % self.server.server_port

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.temp.cleanup()

    def request(self, path, method="GET", body=None, headers=None):
        client = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        try:
            client.request(method, path, body=body, headers=headers or {})
            response = client.getresponse()
            return response.status, json.loads(response.read())
        finally:
            client.close()

    def post(self, body="{}", headers=None):
        return self.request("/api/update", "POST", body,
                            {"Content-Type": "application/json", **(headers or {})})

    def edit(self, path, payload, headers=None):
        body = json.dumps(payload) if isinstance(payload, dict) else payload
        return self.request(path, "POST", body, {
            "Content-Type": "application/json", "Origin": "http://" + self.host,
            **(headers or {})})

    def test_edit_routes_reject_foreign_origins_hosts_and_plain_text(self):
        for path, payload in (
                ("/api/label", {"uid": "synthetic:one", "field": "note", "value": "draft"}),
                ("/api/skills", {"token": "SyntheticSkill", "action": "add"})):
            for headers, expected in (({"Origin": "http://example.invalid"}, 403),
                                      ({"Host": "example.invalid:" + str(self.server.server_port)}, 403),
                                      ({"Content-Type": "text/plain"}, 400)):
                with self.subTest(path=path, headers=headers), patch.object(
                        panel.database, "connect", side_effect=AssertionError("DB opened")):
                    self.assertEqual(self.edit(path, payload, headers)[0], expected)

    def test_local_edits_persist_and_allow_notes_larger_than_update_body(self):
        note = "Synthetic note 合成 " * 400
        code, result = self.edit("/api/label", {
            "uid": "synthetic:one", "field": "note", "value": note})
        self.assertEqual(code, 200)
        conn = panel.database.connect(self.config["db_path"])
        try:
            stored = conn.execute("SELECT note FROM labels WHERE uid=?",
                                  ("synthetic:one",)).fetchone()[0]
            self.assertEqual(stored, result["value"])
            self.assertTrue(stored.startswith("Synthetic note"))
        finally:
            conn.close()
        self.assertEqual(self.edit("/api/skills", {
            "token": "SyntheticSkill", "action": "add"})[0], 200)
        self.assertIn("syntheticskill", skills.canonical_tokens(
            skills.load_skills(self.skills_path)))
        code, result = self.edit("/api/label", {
            "uid": "synthetic:one", "field": "note", "value": None})
        self.assertEqual((code, result["value"]), (200, None))

    def test_edit_accepts_configured_bind_hostname(self):
        host = "panel.internal:%s" % self.server.server_port
        code, result = self.edit("/api/label", {
            "uid": "synthetic:one", "field": "note", "value": "configured host"},
            {"Host": host, "Origin": "http://" + host})
        self.assertEqual((code, result["value"]), (200, "configured host；"))
        code, result = self.edit("/api/skills", {
            "token": "ConfiguredHostSkill", "action": "add"},
            {"Host": host, "Origin": "http://" + host})
        self.assertEqual((code, result["skill"]), (200, "ConfiguredHostSkill"))

    def test_label_sqlite_failure_rolls_back_and_retry_succeeds(self):
        payload = {"uid": "synthetic:one", "field": "note", "value": "original"}
        self.assertEqual(self.edit("/api/label", payload)[0], 200)
        conn = panel.database.connect(self.config["db_path"])
        try:
            before = [tuple(row) for row in conn.execute("SELECT * FROM label_events")]
            conn.execute("""CREATE TRIGGER fail_label BEFORE INSERT ON labels
                            BEGIN SELECT RAISE(ABORT, 'synthetic write failure'); END""")
            conn.commit()
            payload["value"] = "replacement"
            self.assertEqual(self.edit("/api/label", payload),
                             (503, {"error": "write_failed"}))
            self.assertEqual(conn.execute("SELECT note FROM labels").fetchone()[0],
                             "original；")
            self.assertEqual([tuple(row) for row in conn.execute("SELECT * FROM label_events")],
                             before)
            conn.execute("DROP TRIGGER fail_label")
            conn.commit()
            code, result = self.edit("/api/label", payload)
            self.assertEqual((code, result["ok"], result["field"], result["value"]),
                             (200, True, "note", "replacement；"))
            self.assertEqual(result["label"]["note"], "replacement；")
        finally:
            conn.close()

    def test_label_locked_database_returns_json_and_retry_succeeds(self):
        conn = panel.database.connect(self.config["db_path"])
        payload = {"uid": "synthetic:one", "field": "note", "value": "after lock"}

        def short_timeout_connect(path):
            connection = sqlite3.connect(path, timeout=0.02)
            connection.row_factory = sqlite3.Row
            return connection

        try:
            conn.execute("BEGIN IMMEDIATE")
            with patch.object(panel.database, "connect", side_effect=short_timeout_connect):
                self.assertEqual(self.edit("/api/label", payload),
                                 (503, {"error": "write_failed"}))
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM labels").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM label_events").fetchone()[0], 0)
            conn.rollback()
            code, result = self.edit("/api/label", payload)
            self.assertEqual((code, result["ok"], result["field"], result["value"]),
                             (200, True, "note", "after lock；"))
            self.assertEqual(result["label"]["note"], "after lock；")
        finally:
            conn.close()

    def test_label_sqlite_open_failure_returns_json(self):
        with patch.object(panel.database, "connect",
                          side_effect=sqlite3.OperationalError("synthetic open failure")):
            self.assertEqual(self.edit("/api/label", {
                "uid": "synthetic:one", "field": "note", "value": "draft"}),
                (503, {"error": "write_failed"}))

    def test_edit_body_limit_and_malformed_json(self):
        # 64 KiB permits normal notes but bounds the complete JSON request.
        limit = 64 * 1024
        payload = {"uid": "synthetic:one", "field": "note", "value": ""}
        payload["value"] = "x" * (limit - len(json.dumps(payload)))
        self.assertEqual(len(json.dumps(payload)), limit)
        self.assertEqual(self.edit("/api/label", payload)[0], 200)
        payload["value"] += "x"
        for path in ("/api/label", "/api/skills"):
            for body in (json.dumps(payload), "[]", "null", "not json", b"\xff"):
                with self.subTest(path=path, body=str(body)[:25]), patch.object(
                        panel.database, "connect", side_effect=AssertionError("DB opened")):
                    self.assertEqual(self.edit(path, body)[0], 400)

    def test_edit_field_types_are_validated(self):
        for path, payload in (
                ("/api/label", {"uid": [], "field": "note", "value": "draft"}),
                ("/api/label", {"uid": "synthetic:one", "field": "note", "value": {}}),
                ("/api/label", {"uid": "synthetic:one", "field": [], "value": None}),
                ("/api/skills", {"token": [], "action": "add"}),
                ("/api/skills", {"token": "Synthetic", "action": []}),
                ("/api/skills", {"token": "Synthetic", "action": "alias", "target": {}})):
            with self.subTest(path=path, payload=payload), patch.object(
                    panel.database, "connect", side_effect=AssertionError("DB opened")):
                self.assertEqual(self.edit(path, payload)[0], 400)

    def test_status_and_preview_never_open_database(self):
        with patch.object(panel.database, "connect", side_effect=AssertionError("DB opened")):
            code, state = self.request("/api/update-state")
            self.assertEqual((code, state["state"]), (200, "idle"))
            code, preview = self.request("/api/update-config")
            self.assertEqual(preview["queries"], ["fictional engineer"])
            self.assertEqual(code, 200)
        self.assertEqual(self.manager.calls, 0)

    def test_start_and_conflicting_attempt(self):
        code, state = self.post(headers={"Origin": "http://" + self.host})
        self.assertEqual((code, state["accepted"]), (202, True))
        self.manager.next = {"state": "running", "accepted": False}
        self.assertEqual(self.post()[0], 409)
        self.manager.next = {"state": "busy", "accepted": False}
        self.assertEqual(self.post()[0], 409)
        self.manager.next = {"state": "error", "error": "config_invalid", "accepted": False}
        self.assertEqual(self.post()[0], 400)

    def test_foreign_origin_and_rebound_host_cannot_launch(self):
        for headers in ({"Origin": "https://example.invalid"}, {"Origin": "null"},
                        {"Host": "example.invalid:%s" % self.server.server_port,
                         "Origin": "http://example.invalid:%s" % self.server.server_port},
                        {"Host": "127.0.0.1:bad"}, {"Origin": "http://" + self.host + "/bad"}):
            with self.subTest(headers=headers):
                self.assertEqual(self.post(headers=headers)[0], 403)
        self.assertEqual(self.manager.calls, 0)

    def test_browser_form_or_invalid_body_cannot_launch(self):
        for body in ("[]", "null", '{"command":"fetch"}', "not json", " " * 4097):
            with self.subTest(body=body[:30]):
                self.assertEqual(self.post(body)[0], 400)
        self.assertEqual(self.post(headers={"Content-Type": "text/plain"})[0], 400)
        self.assertEqual(self.manager.calls, 0)

    def test_demo_has_no_runner_and_no_fallback(self):
        self.server.RequestHandlerClass.config = {"demo": {"id": "synthetic"}}
        del self.server.update_manager
        with patch.object(panel.database, "connect", side_effect=AssertionError("DB opened")):
            code, preview = self.request("/api/update-config")
            self.assertEqual((code, preview["error"]), (200, "demo_disabled"))
            self.assertEqual(self.request("/api/update-state")[1]["state"], "disabled")
            self.assertEqual(self.post()[0], 503)
        self.assertEqual(self.manager.calls, 0)


if __name__ == "__main__":
    unittest.main()
