"""Batch label write regressions for the authenticated HTTP API and its client.

Handwritten synthetic jobs, a temporary SQLite file and a loopback listener
only. The MCP tool is covered by the opt-in tools/test_mcp*.py entries.
"""
from contextlib import closing
import http.client
import json
import os
from pathlib import Path
import socket
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from jobs import agent_api as api, agent_client, config as config_mod, db, labels, migrations

TOKEN = 'synthetic-batch-token-not-a-real-secret-0000'
PATH = '/api/v1/labels/batch'
T1, T2, T3 = ('2026-01-01T00:00:0%d+00:00' % n for n in (1, 2, 3))
TABLES = ('labels', 'label_events')


class Clock:
    """Deterministic db.utcnow so timestamp ownership is observable."""
    def __init__(self):
        self.now = T1

    def __call__(self):
        return self.now


class BatchBase(unittest.TestCase):
    writes = True

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / 'synthetic.db'
        skills = self.root / 'skills.yaml'
        skills.write_text('skills: []\n')
        self.config = {'db_path': str(self.path), 'skills_path': str(skills),
                       'agent_api': {'allow_label_writes': self.writes}}
        with closing(db.connect(self.path)) as conn:
            for ident, group in [('a', 'grp-ab'), ('b', 'grp-ab'), ('c', 'c'),
                                 ('d', 'd'), ('e', 'e'), ('p', 'grp-pq'), ('q', 'grp-pq')]:
                self.job(conn, ident, group)
            conn.commit()
        self.clock = Clock()
        clock = patch.object(db, 'utcnow', self.clock)
        clock.start()
        self.addCleanup(clock.stop)
        self.store = api.SnapshotStore()
        self.start_server()

    def start_server(self):
        self.server = api.make_server(self.config, TOKEN, port=0, store=self.store,
                                      panel_url='http://127.0.0.1:8299/')
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()

        def stop():
            self.server.shutdown()
            self.server.server_close()
            thread.join()
        self.addCleanup(stop)

    @staticmethod
    def job(conn, ident, group):
        db.upsert_job(conn, dict(source='synthetic', source_id=ident,
            title='Synthetic Developer ' + ident, company='Synthetic Company',
            location='Melbourne VIC', url='https://example.invalid/jobs/' + ident,
            description='Handwritten synthetic full description ' + ident,
            listing_date='2026-10-22'))
        db.set_dedupe(conn, 'synthetic:' + ident, group, group)
        db.replace_decision(conn, 'synthetic:' + ident, False)
        db.replace_score(conn, 'synthetic:' + ident, 'T1', 0, 0, 0, 5, [], 1)

    def request(self, path=PATH, *, method='POST', body=None, raw=None, headers=None,
                token=TOKEN, ctype='application/json', host=None):
        data = raw if raw is not None else (
            None if body is None else json.dumps(body).encode())
        h = {} if token is None else {'Authorization': 'Bearer ' + token}
        if ctype and data is not None:
            h['Content-Type'] = ctype
        h.update(headers or {})
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=10)
        try:
            connection.putrequest(method, path, skip_host=host is not None)
            if host is not None:
                connection.putheader('Host', host)
            for key, value in h.items():
                connection.putheader(key, value)
            if data is not None:
                connection.putheader('Content-Length', str(len(data)))
            connection.endheaders(data)
            response = connection.getresponse()
            text = response.read()
            return response.status, (json.loads(text) if text else None), dict(response.getheaders())
        finally:
            connection.close()

    def batch(self, items, **kwargs):
        status, data, _ = self.request(body={'items': items}, **kwargs)
        return status, data

    def ok(self, items):
        status, data = self.batch(items)
        self.assertEqual(status, 200, data)
        return data

    def dump(self):
        with closing(sqlite3.connect(self.path)) as conn:
            return {t: [tuple(r) for r in conn.execute('SELECT * FROM %s ORDER BY 1' % t)]
                    for t in TABLES}

    def row(self, ident):
        with closing(db.connect(self.path)) as conn:
            found = conn.execute('SELECT * FROM labels WHERE uid=?', ('synthetic:' + ident,)).fetchone()
            return dict(found) if found else None

    def events(self, ident, field=None):
        with closing(db.connect(self.path)) as conn:
            return [dict(r) for r in conn.execute(
                'SELECT * FROM label_events WHERE uid=? AND (? IS NULL OR field=?) ORDER BY event_id',
                ('synthetic:' + ident, field, field))]

    def effective(self, ident):
        """What the panel/API show for the job's duplicate group."""
        with closing(db.connect(self.path)) as conn:
            group = conn.execute('SELECT dedupe_group FROM jobs WHERE uid=?',
                                 ('synthetic:' + ident,)).fetchone()[0]
            peers = [u for (u,) in conn.execute('SELECT uid FROM jobs WHERE dedupe_group=?', (group,))]
            everything = labels.get_labels(conn, with_events=True)
            return labels.merge_labels([everything[u] for u in peers if u in everything]) or {}

    def no_write_connection(self):
        sentinel = patch.object(api, 'write_connection',
                                side_effect=AssertionError('write connection must not open'))
        sentinel.start()
        self.addCleanup(sentinel.stop)


class BatchSemantics(BatchBase):
    def test_group_inheritance_explicit_false_and_null_clear(self):
        labels.set_label(self.connect_once(), 'synthetic:a', action='saved', priority=True,
                         note='Synthetic note', interest='want', eligibility='eligible')
        owner_before = self.row('a')
        self.assertTrue(self.effective('b')['priority'])
        self.assertEqual(self.effective('b')['action'], 'saved')

        self.clock.now = T2
        data = self.ok([{'uid': 'synthetic:b', 'priority': False}])
        self.assertEqual((data['updated'], data['unchanged']), (1, 0))
        self.assertEqual(data['items'], [{'uid': 'synthetic:b', 'action': 'saved',
                                          'priority': False, 'changed': True, 'human_scope': 'group'}])
        shown = self.effective('a')
        self.assertFalse(shown['priority'])
        self.assertEqual((shown['action'], shown['note'], shown['interest'], shown['eligibility']),
                         ('saved', 'Synthetic note；', 'want', 'eligible'))
        self.assertEqual(self.row('a'), owner_before, 'the peer row is not rewritten')
        self.assertEqual(self.row('b')['priority'], 0)
        self.assertIsNone(self.row('b')['action'])

        self.clock.now = T3
        self.ok([{'uid': 'synthetic:b', 'action': None}])
        shown = self.effective('a')
        self.assertIsNone(shown['action'])
        self.assertEqual(shown['note'], 'Synthetic note；')
        self.assertEqual(self.row('a'), owner_before)
        clear = self.events('b', 'action')[-1]
        self.assertEqual((clear['new_value'], clear['at']), (None, T3))

        self.ok([{'uid': 'synthetic:a', 'action': 'applied', 'priority': True}])
        shown = self.effective('b')
        self.assertEqual((shown['action'], bool(shown['priority'])), ('applied', True))

    def connect_once(self):
        conn = db.connect(self.path)
        self.addCleanup(conn.close)
        return conn

    def test_legacy_state_matches_single_set_label_and_keeps_other_fields(self):
        """Same row/event result as the existing writer, for rows with no events."""
        def legacy(path):
            with closing(db.connect(path)) as conn:
                self.job(conn, 'x', 'x')
                conn.execute("""INSERT INTO labels(uid,note,action,interest,eligibility,priority,
                                applied_at,action_at,labeled_at,rules_version)
                                VALUES ('synthetic:x','搬迁；','applied','want','eligible',1,?,?,?,3)""",
                             (T1, T1, T1))
                conn.commit()
        twin = self.root / 'twin.db'
        db.connect(twin).close()
        legacy(self.path)
        legacy(twin)
        self.clock.now = T2
        data = self.ok([{'uid': 'synthetic:x', 'action': 'skipped'}])
        self.assertEqual(data['items'][0]['priority'], True)
        with closing(db.connect(twin)) as conn:
            labels.set_label(conn, 'synthetic:x', source='api', action='skipped')

        def strip(path):
            with closing(sqlite3.connect(path)) as conn:
                rows = [tuple(r) for r in conn.execute('SELECT * FROM labels ORDER BY uid')]
                events = [tuple(r)[1:] for r in conn.execute('SELECT * FROM label_events ORDER BY event_id')]
            return rows, events
        self.assertEqual(strip(self.path), strip(twin))
        row = self.row('x')
        self.assertEqual((row['note'], row['interest'], row['eligibility'], row['priority']),
                         ('搬迁；', 'want', 'eligible', 1))
        self.assertIsNone(row['applied_at'])
        self.assertEqual(row['action_at'], T2)
        owned = {e['field']: e['source'] for e in self.events('x')}
        self.assertEqual(owned['note'], 'legacy_snapshot')
        self.assertEqual(owned['action'], 'api')

    def test_mixed_items_in_one_batch(self):
        labels.set_label(self.connect_once(), 'synthetic:d', action='saved', priority=True)
        labels.set_label(self.connect_once(), 'synthetic:e', action='applied')
        data = self.ok([
            {'uid': 'synthetic:a', 'action': 'saved', 'priority': True},
            {'uid': 'synthetic:c', 'action': 'skipped'},
            {'uid': 'synthetic:d', 'action': None, 'priority': False},
            {'uid': 'synthetic:e', 'action': 'applied'},          # already so
            {'uid': 'synthetic:p', 'priority': True},
        ])
        self.assertEqual((data['api_version'], data['updated'], data['unchanged']), (1, 4, 1))
        self.assertEqual([(i['uid'][10:], i['action'], i['priority'], i['changed']) for i in data['items']],
                         [('a', 'saved', True, True), ('c', 'skipped', False, True),
                          ('d', None, False, True), ('e', 'applied', False, False),
                          ('p', None, True, True)])
        self.assertEqual({i['human_scope'] for i in data['items']}, {'group'})
        for ident, action, star in [('a', 'saved', True), ('b', 'saved', True), ('c', 'skipped', False),
                                    ('d', None, False), ('p', None, True), ('q', None, True)]:
            shown = self.effective(ident)
            self.assertEqual((shown.get('action'), bool(shown.get('priority'))), (action, star), ident)
        self.assertNotIn('note', json.dumps(data))
        self.assertNotIn('description', json.dumps(data))

    def test_same_effective_state_is_a_complete_noop(self):
        self.ok([{'uid': 'synthetic:a', 'action': 'applied', 'priority': True},
                 {'uid': 'synthetic:c', 'action': 'saved'}])
        before = self.dump()
        self.clock.now = T3
        data = self.ok([
            {'uid': 'synthetic:a', 'action': 'applied', 'priority': True},
            {'uid': 'synthetic:c', 'action': 'saved', 'priority': False},
            {'uid': 'synthetic:d', 'action': None, 'priority': False},
        ])
        self.assertEqual((data['updated'], data['unchanged']), (0, 3))
        self.assertFalse(any(item['changed'] for item in data['items']))
        # The peer already shows the group's state, so it needs no row of its own.
        data = self.ok([{'uid': 'synthetic:b', 'action': 'applied', 'priority': True}])
        self.assertEqual((data['updated'], data['unchanged']), (0, 1))
        self.assertIsNone(self.row('b'))
        self.assertEqual(self.dump(), before, 'no events, timestamps or rows may change')

    def test_unchanged_fields_of_a_changed_item_keep_their_times(self):
        self.ok([{'uid': 'synthetic:c', 'action': 'applied'}])
        applied = self.row('c')
        self.assertEqual((applied['applied_at'], applied['action_at']), (T1, T1))
        self.clock.now = T2
        data = self.ok([{'uid': 'synthetic:c', 'action': 'applied', 'priority': True}])
        self.assertEqual(data['updated'], 1)
        row = self.row('c')
        self.assertEqual((row['applied_at'], row['action_at'], row['priority']), (T1, T1, 1))
        self.assertEqual(len(self.events('c', 'action')), 1)
        self.assertEqual(len(self.events('c', 'priority')), 1)

    def test_progress_times_and_audit_events(self):
        self.ok([{'uid': 'synthetic:c', 'action': 'saved'}])
        row = self.row('c')
        self.assertEqual((row['applied_at'], row['action_at'], row['labeled_at']), (None, T1, T1))
        self.clock.now = T2
        self.ok([{'uid': 'synthetic:c', 'action': 'applied'}])
        row = self.row('c')
        self.assertEqual((row['applied_at'], row['action_at'], row['labeled_at']), (T2, T2, T2))
        self.clock.now = T3
        self.ok([{'uid': 'synthetic:c', 'action': 'skipped'}])
        row = self.row('c')
        self.assertEqual((row['action'], row['applied_at'], row['action_at']), ('skipped', None, T3))
        self.ok([{'uid': 'synthetic:c', 'action': None}])
        self.assertIsNone(self.row('c')['action'])
        trail = [(e['old_value'], e['new_value'], e['source']) for e in self.events('c', 'action')]
        self.assertEqual(trail, [(None, 'saved', 'api'), ('saved', 'applied', 'api'),
                                 ('applied', 'skipped', 'api'), ('skipped', None, 'api')])

    def test_reapplying_after_a_peer_changed_progress_uses_the_new_application_time(self):
        self.ok([{'uid': 'synthetic:a', 'action': 'applied'}])
        self.clock.now = T2
        self.ok([{'uid': 'synthetic:b', 'action': 'skipped'}])
        self.clock.now = T3
        self.ok([{'uid': 'synthetic:a', 'action': 'applied'}])
        shown = self.effective('b')
        self.assertEqual((shown['action'], shown['action_at'], shown['applied_at']),
                         ('applied', T3, T3))
        self.ok([{'uid': 'synthetic:b', 'action': 'applied', 'priority': True}])
        shown = self.effective('a')
        self.assertEqual((shown['action_at'], shown['applied_at']), (T3, T3))
        self.assertEqual(len(self.events('a', 'action')), 2)

    def test_frozen_snapshot_keeps_old_human_state_and_new_query_sees_change(self):
        self.ok([{'uid': 'synthetic:a', 'action': 'saved'}])
        status, first, _ = self.request('/api/v1/jobs?limit=200', method='GET')
        self.assertEqual(status, 200)
        sid = first['snapshot_id']
        self.ok([{'uid': 'synthetic:b', 'action': 'applied', 'priority': True}])
        _, frozen, _ = self.request('/api/v1/jobs/synthetic%3Aa?snapshot_id=' + sid, method='GET')
        self.assertEqual((frozen['item']['human']['action'], frozen['item']['human']['priority']),
                         ('saved', None))
        _, fresh, _ = self.request('/api/v1/jobs?limit=200', method='GET')
        rows = {item['uid']: item for item in fresh['items']}
        human = next(rows[u]['human'] for u in ('synthetic:a', 'synthetic:b') if u in rows)
        self.assertEqual((human['action'], human['priority']), ('applied', 1))


class BatchRejection(BatchBase):
    def seed(self):
        self.ok([{'uid': 'synthetic:c', 'action': 'saved'}])
        return self.dump()

    def test_invalid_requests_reject_everything_and_never_open_a_writer(self):
        before = self.seed()
        self.no_write_connection()
        good = {'uid': 'synthetic:d', 'action': 'applied'}
        long_uid = 'synthetic:' + 'x' * 503
        cases = {
            'duplicate uid': [good, {'uid': 'synthetic:d', 'priority': True}],
            'empty uid': [good, {'uid': '', 'action': 'saved'}],
            'long uid': [good, {'uid': long_uid, 'action': 'saved'}],
            'uid not string': [good, {'uid': 7, 'action': 'saved'}],
            'missing uid': [good, {'action': 'saved'}],
            'no writable field': [good, {'uid': 'synthetic:e'}],
            'bad action': [good, {'uid': 'synthetic:e', 'action': 'Saved'}],
            'empty action': [good, {'uid': 'synthetic:e', 'action': ''}],
            'numeric action': [good, {'uid': 'synthetic:e', 'action': 1}],
            'priority int': [good, {'uid': 'synthetic:e', 'priority': 1}],
            'priority text': [good, {'uid': 'synthetic:e', 'priority': 'true'}],
            'priority null': [good, {'uid': 'synthetic:e', 'priority': None}],
            'note field': [good, {'uid': 'synthetic:e', 'action': 'saved', 'note': 'x'}],
            'eligibility field': [good, {'uid': 'synthetic:e', 'eligibility': 'eligible'}],
            'item not object': [good, 'synthetic:e'],
            'empty list': [],
            'too many': [{'uid': 'synthetic:e%d' % n, 'action': 'saved'} for n in range(101)],
        }
        for name, items in cases.items():
            with self.subTest(name):
                status, data = self.batch(items)
                self.assertEqual(status, 400, (name, data))
                self.assertIn('error', data)
                self.assertEqual(self.dump(), before)
        for name, body in {'items not list': {'items': 'synthetic:a'},
                           'items missing': {},
                           'unknown top-level': {'items': [good], 'dry_run': True},
                           'top-level list': [good], 'select all': {'query': {'view': 'all'}}}.items():
            with self.subTest(name):
                status, data, _ = self.request(body=body)
                self.assertEqual(status, 400, data)
                self.assertEqual(self.dump(), before)
        for name, raw in {'invalid json': b'{"items": [',
                          'duplicate key': b'{"items":[{"uid":"synthetic:d","action":"saved","action":"applied"}]}',
                          'duplicate items': b'{"items":[],"items":[{"uid":"synthetic:d","action":"saved"}]}',
                          'nan': b'{"items":[{"uid":"synthetic:d","action":NaN}]}',
                          'not utf8': b'{"items":[{"uid":"\xff","action":"saved"}]}',
                          'deep': b'{"items":' + b'[' * 15000 + b']' * 15000 + b'}',
                          'empty body': b''}.items():
            with self.subTest(name):
                status, data, _ = self.request(raw=raw)
                self.assertEqual(status, 400, data)
                self.assertEqual(self.dump(), before)

    def test_unknown_or_same_group_uid_rejects_whole_batch(self):
        before = self.seed()
        for name, items, code, index in [
                ('unknown', [{'uid': 'synthetic:d', 'action': 'applied'},
                             {'uid': 'synthetic:missing', 'action': 'saved'}], 'unknown_uid', 1),
                ('same group', [{'uid': 'synthetic:d', 'action': 'applied'},
                                {'uid': 'synthetic:a', 'action': 'saved'},
                                {'uid': 'synthetic:b', 'priority': True}], 'duplicate_group', 2),
                ('case differs', [{'uid': 'SYNTHETIC:D', 'action': 'saved'}], 'unknown_uid', 0)]:
            with self.subTest(name):
                status, data = self.batch(items)
                self.assertEqual((status, data), (400, {'error': code, 'index': index}))
                self.assertEqual(self.dump(), before)
        self.assertEqual(self.ok([{'uid': 'synthetic:a', 'action': 'saved'},
                                  {'uid': 'synthetic:p', 'action': 'saved'}])['updated'], 2)

    def test_error_responses_do_not_echo_request_content(self):
        secret_uid = 'synthetic:private-looking-value'
        status, data, _ = self.request(body={'items': [{'uid': secret_uid, 'action': 'saved'}]})
        self.assertEqual(status, 400)
        self.assertNotIn(secret_uid, json.dumps(data))


class BatchHTTPBoundary(BatchBase):
    def valid(self):
        return {'items': [{'uid': 'synthetic:c', 'action': 'saved'}]}

    def test_authentication_host_and_origin(self):
        before = self.dump()
        self.no_write_connection()
        for token in (None, 'wrong'):
            self.assertEqual(self.request(body=self.valid(), token=token)[0], 401)
        for headers in ({'Origin': 'http://evil.invalid'}, {'Origin': 'null'}):
            self.assertEqual(self.request(body=self.valid(), headers=headers)[0], 403)
        self.assertEqual(self.request(body=self.valid(), host='evil.invalid')[0], 403)
        self.assertEqual(self.request(body=self.valid(), host='127.0.0.1')[0], 403)
        self.assertEqual(self.dump(), before)

    def test_methods_paths_media_type_and_size(self):
        before = self.dump()
        self.no_write_connection()
        for method in ('PUT', 'PATCH', 'DELETE', 'OPTIONS', 'TRACE'):
            self.assertEqual(self.request(method=method, body=self.valid())[0], 405)
        self.assertEqual(self.request(method='GET')[0], 404)
        self.assertEqual(self.request('/api/label', body=self.valid())[0], 405)
        self.assertEqual(self.request('/api/v1/labels/batch/', body=self.valid())[0], 405)
        self.assertEqual(self.request(PATH + '?dry_run=1', body=self.valid())[0], 400)
        for ctype in (None, 'text/plain', 'application/x-www-form-urlencoded',
                      'application/json; charset=latin-1', 'application/jsonx'):
            status, data, _ = self.request(body=self.valid(), ctype=ctype)
            self.assertEqual((status, data), (415, {'error': 'unsupported_media_type'}), ctype)
        status, data, _ = self.request(body=self.valid(), headers={'Transfer-Encoding': 'chunked'})
        self.assertEqual(status, 400)
        status, data, _ = self.request(headers={'Content-Type': 'application/json'})
        self.assertEqual((status, data), (411, {'error': 'length_required'}))
        body = json.dumps(self.valid()).encode()
        status, data, _ = self.request(raw=body + b' ' * (api.MAX_LABEL_BODY + 1 - len(body)))
        self.assertEqual((status, data), (413, {'error': 'payload_too_large'}))
        status, data, _ = self.request(raw=b'x' * (200 * 1024))
        self.assertEqual(status, 413)
        try:  # far beyond the bounded drain the peer may see a reset instead
            self.assertEqual(self.request(raw=b'x' * (4 * 1024 * 1024))[0], 413)
        except ConnectionError:
            pass
        self.assertEqual(self.dump(), before)

    def test_body_at_the_limit_and_accepted_media_type_forms(self):
        body = json.dumps(self.valid()).encode()
        status, data, headers = self.request(raw=body + b' ' * (api.MAX_LABEL_BODY - len(body)),
                                             ctype='Application/JSON; charset=UTF-8')
        self.assertEqual(status, 200, data)
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertEqual(headers['Content-Type'], 'application/json; charset=utf-8')
        self.assertNotIn('Access-Control-Allow-Origin', headers)
        self.assertEqual(self.row('c')['action'], 'saved')

    def test_incomplete_body_times_out_within_the_total_read_budget(self):
        self.no_write_connection()
        before = self.dump()
        with patch.object(api, 'BODY_DEADLINE', 0.05):
            connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=1)
            try:
                connection.request('POST', PATH, body=b'{', headers={
                    'Authorization': 'Bearer ' + TOKEN,
                    'Content-Type': 'application/json', 'Content-Length': '100'})
                response = connection.getresponse()
                self.assertEqual(response.status, 408)
                self.assertEqual(json.loads(response.read()), {'error': 'request_timeout'})
            finally:
                connection.close()
        self.assertEqual(self.dump(), before)

    def test_absolute_assignment_can_be_resent_after_a_lost_response(self):
        first = self.ok([{'uid': 'synthetic:c', 'action': 'applied'}])
        applied = self.dump()
        again = self.ok([{'uid': 'synthetic:c', 'action': 'applied'}])
        self.assertEqual((first['updated'], again['updated'], again['unchanged']), (1, 0, 1))
        self.assertEqual(self.dump(), applied)

    def test_reads_stay_read_only(self):
        with closing(api.read_connection(self.path)) as conn:
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("UPDATE labels SET action='saved'")
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("INSERT INTO labels(uid) VALUES ('synthetic:c')")


class BatchDisabled(BatchBase):
    writes = False

    def test_default_and_explicit_false_refuse_before_opening_a_writer(self):
        self.no_write_connection()
        before = self.dump()
        status, data, _ = self.request(body={'items': [{'uid': 'synthetic:c', 'action': 'saved'}]})
        self.assertEqual((status, data), (403, {'error': 'label_writes_disabled'}))
        status, data, _ = self.request(raw=b'{' * 100)
        self.assertEqual(status, 403)
        self.assertEqual(self.request(body={'items': []}, token=None)[0], 401)
        self.assertEqual(self.dump(), before)
        self.assertEqual(self.request(method='GET')[0], 404)
        self.assertEqual(self.request('/api/v1/jobs', method='GET')[0], 200)

    def test_missing_key_defaults_to_disabled(self):
        config = dict(self.config, agent_api={})
        self.assertFalse(api.AgentAPI(config).allow_label_writes)
        self.assertFalse(api.AgentAPI({k: v for k, v in config.items() if k != 'agent_api'}).allow_label_writes)


class BatchConfiguration(unittest.TestCase):
    def test_only_a_literal_true_enables_writes(self):
        self.assertTrue(api.label_writes_enabled({'agent_api': {'allow_label_writes': True}}))
        self.assertFalse(api.label_writes_enabled({}))
        for value in ('true', 'yes', 1, 0, None, [], {}):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    api.label_writes_enabled({'agent_api': {'allow_label_writes': value}})
                with self.assertRaises(ValueError):
                    api.AgentAPI({'agent_api': {'allow_label_writes': value}})
        with self.assertRaises(ValueError):
            api.label_writes_enabled({'agent_api': True})

    def test_public_example_keeps_writes_off(self):
        example = config_mod.load_config(ROOT / 'config.example.yaml')
        self.assertIs(example['agent_api']['allow_label_writes'], False)
        self.assertFalse(api.AgentAPI(example).allow_label_writes)


class BatchConcurrencyAndFailure(BatchBase):
    def test_concurrent_partial_note_edit_is_preserved(self):
        """A panel-style note edit that commits first is not replaced by the batch."""
        writer = db.connect(self.path)
        self.addCleanup(writer.close)
        writer.execute('BEGIN IMMEDIATE')
        result = {}

        def run():
            result['value'] = self.batch([{'uid': 'synthetic:c', 'action': 'saved'}])
        thread = threading.Thread(target=run)
        thread.start()
        time.sleep(0.4)  # the batch is now waiting for the write lock
        labels.set_label(writer, 'synthetic:c', source='panel', note='Concurrent note')
        thread.join(10)
        self.assertEqual(result['value'][0], 200, result)
        row = self.row('c')
        self.assertEqual((row['note'], row['action']), ('Concurrent note；', 'saved'))

    def test_edit_waiting_behind_an_open_batch_is_applied_afterwards(self):
        reached, release = threading.Event(), threading.Event()
        real = labels._set_label

        def paused(*args, **kwargs):
            value = real(*args, **kwargs)
            reached.set()
            self.assertTrue(release.wait(10))
            return value
        done = {}

        def edit():
            with closing(db.connect(self.path)) as conn:
                done['row'] = labels.set_label(conn, 'synthetic:c', source='panel', note='After batch')
        with patch.object(labels, '_set_label', paused):
            thread = threading.Thread(target=lambda: done.setdefault(
                'batch', self.batch([{'uid': 'synthetic:c', 'action': 'applied'}])))
            thread.start()
            self.assertTrue(reached.wait(10))
            editor = threading.Thread(target=edit)
            editor.start()
            time.sleep(0.3)
            self.assertNotIn('row', done, 'the editor must wait for the batch transaction')
            release.set()
            thread.join(10)
            editor.join(10)
        self.assertEqual(done['batch'][0], 200)
        row = self.row('c')
        self.assertEqual((row['action'], row['note']), ('applied', 'After batch；'))

    def test_failure_midway_rolls_back_every_item_and_retry_succeeds(self):
        self.ok([{'uid': 'synthetic:c', 'action': 'saved'}])
        before = self.dump()
        items = [{'uid': 'synthetic:a', 'action': 'applied', 'priority': True},
                 {'uid': 'synthetic:c', 'action': 'skipped'},
                 {'uid': 'synthetic:d', 'priority': True}]
        real, calls = labels._set_label, []

        def failing(*args, **kwargs):
            calls.append(args[1])
            if len(calls) == 3:
                raise sqlite3.OperationalError('disk I/O error')
            return real(*args, **kwargs)
        self.clock.now = T2
        with patch.object(labels, '_set_label', failing):
            status, data = self.batch(items)
        self.assertEqual((status, data), (503, {'error': 'data_unavailable'}))
        self.assertGreaterEqual(len(calls), 3)
        self.assertEqual(self.dump(), before)
        data = self.ok(items)
        self.assertEqual(data['updated'], 3)
        self.assertEqual(self.effective('b')['action'], 'applied')
        self.assertEqual(self.row('c')['action'], 'skipped')

    def test_commit_failure_rolls_back_and_retry_succeeds(self):
        before = self.dump()
        items = [{'uid': 'synthetic:a', 'action': 'saved'}, {'uid': 'synthetic:c', 'priority': True}]
        real_connect, state = sqlite3.connect, {'fail': True}

        class Flaky(sqlite3.Connection):
            def commit(self):
                if state['fail']:
                    state['fail'] = False
                    raise sqlite3.OperationalError('disk I/O error')
                super().commit()

        def flaky_connection(path):
            conn = real_connect(Path(path).resolve().as_uri() + '?mode=rw', uri=True, factory=Flaky)
            conn.row_factory = sqlite3.Row
            conn.execute('BEGIN IMMEDIATE')
            return conn
        with patch.object(api, 'write_connection', flaky_connection):
            status, data = self.batch(items)
            self.assertEqual((status, data), (503, {'error': 'data_unavailable'}))
            self.assertEqual(self.dump(), before)
            self.assertEqual(self.ok(items)['updated'], 2)
        self.assertEqual(self.row('a')['action'], 'saved')

    def test_write_that_cannot_win_the_group_merge_is_rolled_back(self):
        """A peer event dated in the future would silently beat the new value."""
        with closing(db.connect(self.path)) as conn:
            conn.execute("INSERT INTO labels(uid,action,action_at,labeled_at) VALUES ('synthetic:a','saved',?,?)",
                         ('2999-01-01T00:00:00+00:00',) * 2)
            conn.execute("INSERT INTO label_events(uid,field,new_value,source,at) VALUES "
                         "('synthetic:a','action','saved','panel','2999-01-01T00:00:00+00:00')")
            conn.commit()
        before = self.dump()
        status, data = self.batch([{'uid': 'synthetic:d', 'action': 'applied'},
                                   {'uid': 'synthetic:b', 'action': 'skipped'}])
        self.assertEqual((status, data), (409, {'error': 'label_not_effective', 'index': 1}))
        self.assertEqual(self.dump(), before)


class BatchDatabaseState(BatchBase):
    def test_missing_database_is_not_created(self):
        missing = self.root / 'not-created' / 'jobs.db'
        server = api.AgentAPI(dict(self.config, db_path=str(missing)))
        with self.assertRaises(sqlite3.OperationalError):
            server.label_batch([{'uid': 'synthetic:c', 'action': 'saved'}])
        self.assertFalse(missing.parent.exists())
        self.server.api.db_path = missing
        status, data = self.batch([{'uid': 'synthetic:c', 'action': 'saved'}])
        self.assertEqual((status, data), (503, {'error': 'data_unavailable'}))
        self.assertFalse(missing.parent.exists())

    def test_schema_mismatch_fails_closed_without_upgrade(self):
        for version in (migrations.SCHEMA_VERSION - 1, migrations.SCHEMA_VERSION + 1):
            with self.subTest(version=version):
                with closing(sqlite3.connect(self.path)) as conn:
                    conn.execute('PRAGMA user_version=%d' % version)
                before = self.dump()
                status, data = self.batch([{'uid': 'synthetic:c', 'action': 'saved'}])
                self.assertEqual((status, data), (503, {'error': 'schema_upgrade_required'}))
                self.assertEqual(self.dump(), before)
                with closing(sqlite3.connect(self.path)) as conn:
                    self.assertEqual(conn.execute('PRAGMA user_version').fetchone()[0], version)
        with closing(sqlite3.connect(self.path)) as conn:
            conn.execute('PRAGMA user_version=%d' % migrations.SCHEMA_VERSION)

    def test_journal_mode_is_not_changed(self):
        for mode in ('wal', 'delete'):
            with self.subTest(mode=mode):
                with closing(sqlite3.connect(self.path)) as conn:
                    conn.execute('PRAGMA journal_mode=%s' % mode)
                self.ok([{'uid': 'synthetic:c', 'action': 'saved' if mode == 'wal' else 'applied'}])
                with closing(sqlite3.connect(self.path)) as conn:
                    self.assertEqual(conn.execute('PRAGMA journal_mode').fetchone()[0], mode)


class Stub(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_POST(self):
        self.server.hits.append(self.path)
        self.rfile.read(int(self.headers.get('Content-Length', 0)))
        status, headers, body = self.server.script(self)
        self.send_response(status)
        for key, value in headers.items():
            self.send_header(key, value)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Connection', 'close')
        self.end_headers()
        self.wfile.write(body)


class BatchClient(unittest.TestCase):
    def stub(self, script):
        server = HTTPServer(('127.0.0.1', 0), Stub)
        server.hits, server.script = [], script
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def stop():
            server.shutdown()
            server.server_close()
            thread.join()
        self.addCleanup(stop)
        return server, agent_client.APIClient('http://127.0.0.1:%d' % server.server_port, TOKEN)

    BODY = {'items': [{'uid': 'synthetic:c', 'action': 'saved'}]}

    def test_redirect_is_never_followed_and_target_is_not_contacted(self):
        target, _ = self.stub(lambda h: (200, {'Content-Type': 'application/json'}, b'{}'))
        for status in (301, 302, 303, 307, 308):
            with self.subTest(status=status):
                server, client = self.stub(lambda h: (status, {'Location': 'http://127.0.0.1:%d/x' % target.server_port}, b''))
                with self.assertRaises(agent_client.ClientError) as caught:
                    client.post(PATH, self.BODY)
                self.assertNotIn(TOKEN, str(caught.exception))
        self.assertEqual(target.hits, [])

    def test_errors_are_bounded_and_do_not_leak(self):
        leak = b'{"error":"synthetic:private-value <script>","detail":"Synthetic JD text"}'
        cases = [(400, b'{"error":"unknown_uid","index":2}', 'HTTP 400: unknown_uid at item 2'),
                 (403, b'{"error":"label_writes_disabled"}', 'HTTP 403: label_writes_disabled'),
                 (400, leak, 'HTTP 400)'), (500, b'not json', 'HTTP 500)'),
                 (400, b'{"error":"ok_code","index":true}', 'HTTP 400)'),
                 (400, b'{"error":"private_synthetic_token_0123456789"}', 'HTTP 400)')]
        for status, body, expected in cases:
            with self.subTest(status=status, body=body[:20]):
                server, client = self.stub(lambda h: (status, {'Content-Type': 'application/json'}, body))
                with self.assertRaises(agent_client.ClientError) as caught:
                    client.post(PATH, self.BODY)
                message = str(caught.exception)
                self.assertIn(expected, message)
                for secret in (TOKEN, 'private-value', 'Synthetic JD', '127.0.0.1', 'synthetic:c', 'script'):
                    self.assertNotIn(secret, message)
                self.assertEqual(len(server.hits), 1, 'no automatic retry')

    def test_success_request_shape_and_no_retry_on_unreadable_answer(self):
        seen = {}

        def script(handler):
            seen['auth'] = handler.headers['Authorization']
            seen['type'] = handler.headers['Content-Type']
            return 200, {'Content-Type': 'application/json'}, b'{"updated":1}'
        server, client = self.stub(script)
        self.assertEqual(client.post(PATH, self.BODY), {'updated': 1})
        self.assertEqual((seen['auth'], seen['type']), ('Bearer ' + TOKEN, 'application/json'))
        for status, ctype, body in [(200, 'text/html', b'<html>'), (200, 'application/json', b'[1]'),
                                    (200, 'application/json', b'{broken'), (201, 'application/json', b'{}')]:
            server, client = self.stub(lambda h: (status, {'Content-Type': ctype}, body))
            with self.assertRaises(agent_client.ClientError):
                client.post(PATH, self.BODY)
            self.assertEqual(len(server.hits), 1)

    def test_unreachable_api_and_oversized_body(self):
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        client = agent_client.APIClient('http://127.0.0.1:%d' % port, TOKEN)
        with self.assertRaises(agent_client.ClientError) as caught:
            client.post(PATH, self.BODY)
        self.assertIn('not retried', str(caught.exception))
        self.assertNotIn(str(port), str(caught.exception))
        server, client = self.stub(lambda h: (200, {'Content-Type': 'application/json'}, b'{}'))
        with self.assertRaises(agent_client.ClientError):
            client.post(PATH, {'items': [{'uid': 'x' * 512, 'action': 'saved'}] * 100})
        with self.assertRaises(agent_client.ClientError):
            client.post(PATH, {'items': float('nan')})
        self.assertEqual(server.hits, [])

    def test_real_server_round_trip_and_disabled_error(self):
        class Live(BatchBase):
            def runTest(self):
                pass
        live = Live()
        live.setUp()
        self.addCleanup(live.doCleanups)
        client = agent_client.APIClient('http://127.0.0.1:%d' % live.server.server_port, TOKEN)
        result = client.post(PATH, {'items': [{'uid': 'synthetic:a', 'action': 'applied'}]})
        self.assertEqual((result['updated'], result['items'][0]['human_scope']), (1, 'group'))
        with self.assertRaises(agent_client.ClientError) as caught:
            client.post(PATH, {'items': [{'uid': 'synthetic:nope', 'action': 'applied'}]})
        self.assertIn('HTTP 400: unknown_uid at item 0', str(caught.exception))
        self.assertEqual(live.effective('b')['action'], 'applied')


if __name__ == '__main__':
    unittest.main()
