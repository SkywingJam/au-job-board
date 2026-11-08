"""Synthetic regressions for frozen snapshot work, admission and delivery.

These checks also run unchanged against v1.3.2 to demonstrate the old failures.
Events control slow work; production data and wall-clock performance are unused.
"""
from concurrent.futures import ThreadPoolExecutor, wait
from contextlib import closing
from io import BytesIO
from pathlib import Path
import http.client
import json
import socket
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jobs import agent_api as api, agent_client, db, labels, location_query, report

TOKEN = 'synthetic-snapshot-lifecycle-token-00000000'


class TrackingHandler(api.Handler):
    def finish(self):
        try:
            super().finish()
        finally:
            self.server.finished.set()


class SnapshotLifecycle(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.path = root / 'synthetic.db'
        skills = root / 'skills.yaml'
        skills.write_text('skills: []\n')
        self.config = {'db_path': str(self.path), 'skills_path': str(skills)}
        with closing(db.connect(self.path)) as conn:
            for ident, score in [('a', 9), ('b', 8), ('c', 7)]:
                uid = 'synthetic:' + ident
                db.upsert_job(conn, dict(source='synthetic', source_id=ident,
                    title='Synthetic Developer ' + ident, company='Synthetic Company',
                    location='Melbourne VIC', url='https://example.invalid/' + ident,
                    description='Synthetic role ' + ident + '. Full working rights required.',
                    listing_date='2026-11-01'))
                db.set_dedupe(conn, uid, uid, uid)
                db.replace_decision(conn, uid, False)
                db.replace_score(conn, uid, 'T1', 0, 0, 0, score, [], 1)
            conn.commit()
        self.store = api.SnapshotStore(ttl=3600)
        self.server = api.make_server(self.config, TOKEN, port=0, store=self.store)
        self.server.RequestHandlerClass = TrackingHandler
        self.server.finished = threading.Event()
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        self.base = 'http://127.0.0.1:' + str(self.server.server_port)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)

    def request(self, path):
        with closing(http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)) as conn:
            conn.request('GET', path, headers={'Authorization': 'Bearer ' + TOKEN})
            response = conn.getresponse()
            return response.status, json.loads(response.read())

    def first(self, query='?limit=1'):
        status, value = self.request('/api/v1/jobs' + query)
        self.assertEqual(status, 200, value)
        self.assertTrue(self.server.finished.wait(2))
        return value

    def test_only_requested_page_and_detail_scan_frozen_descriptions(self):
        with patch.object(report, 'eligibility_snippet', wraps=report.eligibility_snippet) as snippets:
            page = self.first()
            self.assertEqual(snippets.call_count, 1, 'a one-row page must not scan the full detail pool')
            # Non-display and nonmatching source candidates must remain readable.
            self.assertEqual(set(self.store.get(page['snapshot_id']).records),
                             {'synthetic:a', 'synthetic:b', 'synthetic:c'})
            status, detail = self.request('/api/v1/jobs/synthetic%3Ac?snapshot_id=' + page['snapshot_id'])
            self.assertEqual(status, 200)
            self.assertEqual(snippets.call_count, 2)
            self.assertEqual(detail['item']['eligibility_cue'], '工作权')

    def test_narrow_query_keeps_unmatched_sources_and_frozen_evidence(self):
        with closing(api.read_connection(self.path)) as conn:
            expected = api.serialize(conn, location_query.find(conn, self.config, 'synthetic:c'),
                                     self.server.api.panel_base, {})
        page = self.first('?q=Developer%20a&limit=1')
        self.assertEqual(page['total'], 1)
        with closing(db.connect(self.path)) as conn:
            conn.execute('UPDATE jobs SET description=? WHERE uid=?',
                         ('Synthetic changed role. Australian citizenship required.', 'synthetic:c'))
            labels.set_label(conn, 'synthetic:c', action='applied', note='Synthetic later note')
            conn.commit()
        status, frozen = self.request('/api/v1/jobs/synthetic%3Ac?snapshot_id=' + page['snapshot_id'])
        self.assertEqual(status, 200)
        self.assertEqual(frozen['item'], expected)
        self.assertEqual(self.request('/api/v1/jobs/synthetic%3Ac')[1]['item']['eligibility_cue'], '公民/PR')

    def test_existing_detail_and_cursor_do_not_wait_for_new_snapshot(self):
        page = self.first()
        entered, release = threading.Event(), threading.Event()
        original = self.server.api._materialize

        def slow(query, **kwargs):
            entered.set()
            if not release.wait(5):
                raise RuntimeError('synthetic loader was not released')
            return original(query, **kwargs)

        with ThreadPoolExecutor(max_workers=3) as workers, patch.object(self.server.api, '_materialize', slow):
            creating = workers.submit(self.request, '/api/v1/jobs?q=Developer')
            try:
                self.assertTrue(entered.wait(2))
                detail = workers.submit(self.request, '/api/v1/jobs/synthetic%3Aa?snapshot_id=' + page['snapshot_id'])
                cursor = workers.submit(self.request, '/api/v1/jobs?cursor=' + quote(page['next_cursor']))
                self.assertFalse(wait([detail, cursor], timeout=1).not_done,
                                 'existing detail and cursor were blocked by a new loader')
                self.assertEqual(detail.result()[0], 200)
                self.assertEqual(cursor.result()[0], 200)
            finally:
                release.set()
                self.assertEqual(creating.result(timeout=5)[0], 200)

    def test_pending_build_counts_towards_capacity_without_blocking(self):
        self.server.api.store = self.store = api.SnapshotStore(max_snapshots=1)
        entered, release = threading.Event(), threading.Event()
        original = self.server.api._materialize

        def slow(query, **kwargs):
            entered.set()
            if not release.wait(5):
                raise RuntimeError('synthetic loader was not released')
            return original(query, **kwargs)

        with ThreadPoolExecutor(max_workers=2) as workers, patch.object(self.server.api, '_materialize', slow):
            creating = workers.submit(self.request, '/api/v1/jobs')
            try:
                self.assertTrue(entered.wait(2))
                rejected = workers.submit(self.request, '/api/v1/jobs?q=Developer')
                self.assertFalse(wait([rejected], timeout=1).not_done,
                                 'capacity rejection waited for unrelated work')
                status, value = rejected.result()
                self.assertEqual((status, value['error']), (429, 'snapshot_capacity'))
            finally:
                release.set()
                self.assertEqual(creating.result(timeout=5)[0], 200)

    def test_disconnected_reader_does_not_leave_an_unusable_snapshot(self):
        entered, release = threading.Event(), threading.Event()
        original = self.server.api._materialize

        def slow(query, **kwargs):
            entered.set()
            if not release.wait(5):
                raise RuntimeError('synthetic loader was not released')
            return original(query, **kwargs)

        self.server.finished.clear()
        with patch.object(self.server.api, '_materialize', slow), patch.object(self.server, 'handle_error'):
            sock = socket.create_connection(('127.0.0.1', self.server.server_port), timeout=2)
            try:
                request = ('GET /api/v1/jobs HTTP/1.1\r\nHost: 127.0.0.1:' + str(self.server.server_port)
                           + '\r\nAuthorization: Bearer ' + TOKEN
                           + '\r\nX-Jobs-Cancel-On-Disconnect: 1\r\n\r\n')
                sock.sendall(request.encode('ascii'))
                self.assertTrue(entered.wait(2))
            finally:
                sock.close()
                release.set()
            self.assertTrue(self.server.finished.wait(3))
        self.assertEqual(len(self.store.items), 0, 'a closed reader must not consume a one-hour slot')
        self.first()

    def test_http_reader_may_half_close_after_sending_its_request(self):
        with closing(socket.create_connection(('127.0.0.1', self.server.server_port), timeout=3)) as sock:
            request = ('GET /api/v1/jobs HTTP/1.1\r\nHost: 127.0.0.1:' + str(self.server.server_port)
                       + '\r\nAuthorization: Bearer ' + TOKEN + '\r\n\r\n')
            sock.sendall(request.encode('ascii'))
            sock.shutdown(socket.SHUT_WR)
            with closing(http.client.HTTPResponse(sock)) as response:
                response.begin()
                value = json.loads(response.read())
                self.assertEqual(response.status, 200, value)
                self.assertIn(value['snapshot_id'], self.store.items)

    def test_failed_response_reclaims_only_its_new_snapshot(self):
        existing = self.first()
        self.server.finished.clear()
        with patch.object(TrackingHandler, 'end_headers', side_effect=BrokenPipeError), \
                patch.object(self.server, 'handle_error'):
            with self.assertRaises((http.client.HTTPException, OSError)):
                self.request('/api/v1/jobs?q=Developer')
            self.assertTrue(self.server.finished.wait(3))
        self.assertEqual(set(self.store.items), {existing['snapshot_id']})
        # A failed cursor response must not discard the existing snapshot either.
        self.server.finished.clear()
        with patch.object(TrackingHandler, 'end_headers', side_effect=BrokenPipeError), \
                patch.object(self.server, 'handle_error'):
            with self.assertRaises((http.client.HTTPException, OSError)):
                self.request('/api/v1/jobs?cursor=' + quote(existing['next_cursor']))
            self.assertTrue(self.server.finished.wait(3))
        self.assertEqual(set(self.store.items), {existing['snapshot_id']})

    def test_server_deadline_prevents_publishing_a_timed_out_query(self):
        with patch.object(api, 'SNAPSHOT_DEADLINE', 0, create=True):
            status, value = self.request('/api/v1/jobs')
        self.assertEqual((status, value.get('error')), (503, 'snapshot_timeout'))
        self.assertEqual(len(self.store.items), 0)
        self.first()

    def test_nine_parallel_queries_have_eight_usable_snapshots_and_one_capacity_error(self):
        with ThreadPoolExecutor(max_workers=9) as workers:
            responses = list(workers.map(lambda _: self.request('/api/v1/jobs?limit=1'), range(9)))
        self.assertEqual(sorted(status for status, _ in responses), [200] * 8 + [429])
        for status, value in responses:
            if status == 429:
                self.assertEqual(value['error'], 'snapshot_capacity')
            else:
                self.assertEqual(self.request('/api/v1/jobs/synthetic%3Aa?snapshot_id=' + value['snapshot_id'])[0], 200)

    def test_failed_loader_releases_capacity_and_published_ttl_starts_after_loading(self):
        clock = [0.]
        store = api.SnapshotStore(max_snapshots=1, ttl=10, clock=lambda: clock[0])
        def failing():
            raise RuntimeError('synthetic loader failure')
        with self.assertRaises(RuntimeError):
            store.create(failing)
        def slow():
            clock[0] = 100.
            return {'query': {'limit': 1}}, [], {}
        item = store.create(slow)
        self.assertEqual(store.get(item.snapshot_id), item)
        clock[0] = 110.
        with self.assertRaises(api.APIError):
            store.get(item.snapshot_id)
        store.create(lambda: ({'query': {'limit': 1}}, [], {}))

    def test_concurrent_loaders_respect_the_total_byte_budget(self):
        store = api.SnapshotStore(max_snapshots=2, max_snapshot_bytes=300, max_bytes=300)
        entered = threading.Barrier(2)
        def create(_):
            def load():
                entered.wait(2)
                return {'query': {'limit': 1}}, ['synthetic:a'], {'synthetic:a': {'description': 'x' * 160}}
            try:
                store.create(load)
                return 200
            except api.APIError as exc:
                return exc.status
            except threading.BrokenBarrierError:
                return 'loaders_were_serialized'
        with ThreadPoolExecutor(max_workers=2) as workers:
            statuses = list(workers.map(create, range(2)))
        self.assertCountEqual(statuses, [200, 429])
        self.assertLessEqual(sum(item.size for item in store.items.values()), store.max_bytes)

    def test_capacity_error_keeps_the_safe_code_and_recovery_hint(self):
        self.server.api.store = api.SnapshotStore(max_snapshots=1)
        self.first()
        client = agent_client.APIClient(self.base, TOKEN)
        with self.assertRaises(agent_client.ClientError) as caught:
            client.get('/api/v1/jobs', {})
        message = str(caught.exception)
        self.assertIn('snapshot_capacity', message)
        self.assertIn('reuse an existing snapshot', message)
        self.assertNotIn('snapshot expired', message)
        self.assertNotIn(TOKEN, message)


class ReadErrors(unittest.TestCase):
    def test_timeout_connection_and_json_errors_are_distinct_and_private(self):
        client = agent_client.APIClient('http://127.0.0.1:1', TOKEN)
        secret = 'synthetic-private-error-text'
        cases = [(TimeoutError(secret), 'timed out'),
                 (URLError(TimeoutError(secret)), 'timed out'),
                 (URLError(OSError(secret)), 'connection failed'),
                 (ValueError(secret), 'invalid JSON')]
        messages = []
        for error, expected in cases:
            with patch.object(client.opener, 'open', side_effect=error):
                with self.assertRaises(agent_client.ClientError) as caught:
                    client.get('/api/v1/jobs', {})
                message = str(caught.exception)
                self.assertNotIn(secret, message)
                self.assertNotIn(TOKEN, message)
                messages.append((message, expected))
        for message, expected in messages:
            self.assertIn(expected, message)
        self.assertEqual(len({message for message, _ in messages}), 3)

    def test_foreign_http_error_body_is_not_echoed(self):
        client = agent_client.APIClient('http://127.0.0.1:1', TOKEN)
        error = HTTPError('http://example.invalid/private', 429, 'private header', {},
                          BytesIO(b'{"error":"synthetic-private-response","index":123}'))
        with patch.object(client.opener, 'open', side_effect=error):
            with self.assertRaises(agent_client.ClientError) as caught:
                client.get('/api/v1/jobs', {})
        self.assertIn('HTTP 429', str(caught.exception))
        for private in ('synthetic-private-response', 'private header', 'example.invalid', TOKEN):
            self.assertNotIn(private, str(caught.exception))


if __name__ == '__main__':
    unittest.main(verbosity=2)
