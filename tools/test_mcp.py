"""Opt-in MCP stdio -> authenticated HTTP -> synthetic DB integration checks.

Requires requirements-mcp.txt. No real jobs, personal settings or host config.
"""
from contextlib import closing
from pathlib import Path
import hashlib
import json
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mcp import Client, StdioServerParameters
from jobs import agent_api, agent_client, batches, db, labels
from jobs.file_security import set_private
from test_file_security import grant_public_read

TOKEN = 'synthetic-mcp-token-not-a-real-secret-000000'


class MCPFixture(unittest.TestCase):
    label_writes = False   # agent_api.allow_label_writes in the synthetic config

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / 'synthetic.db'
        skills = self.root / 'skills.yaml'
        skills.write_text('skills: []\n')
        rules = self.root / 'rules.yaml'
        rules.write_text('location: {local: [Melbourne, VIC]}\n')
        self.token_file = self.root / 'agent.token'
        self.token_file.write_text(TOKEN)
        set_private(self.token_file)
        config = {'db_path': str(self.path), 'skills_path': str(skills), 'rules_path': str(rules)}
        if self.label_writes:
            config['agent_api'] = {'allow_label_writes': True}
        with closing(db.connect(self.path)) as conn:
            for ident, loc, score in [('a', 'Melbourne VIC', 9), ('b', 'Sydney NSW', 20),
                                      ('c', 'Undisclosed', 2), ('d', 'Carlton VIC', 5)]:
                db.upsert_job(conn, dict(source='synthetic', source_id=ident,
                    title='Synthetic Developer ' + ident, company='Synthetic Company',
                    location=loc, url='https://example.invalid/jobs/' + ident,
                    description='Handwritten synthetic complete JD ' + ident,
                    listing_date='2026-10-22'))
                db.set_dedupe(conn, 'synthetic:' + ident, ident, ident)
                db.replace_decision(conn, 'synthetic:' + ident, False)
                db.replace_score(conn, 'synthetic:' + ident, 'T1', 0, 0, 0, score, [], 1)
            db.set_dedupe(conn, 'synthetic:a', 'shared-group', 'shared-group')
            db.set_dedupe(conn, 'synthetic:b', 'shared-group', 'shared-group')
            labels.set_label(conn, 'synthetic:a', action='saved', note='Synthetic group note')
            self.batch_id = batches.start_batch(conn)
            for ident, is_new in [('a', True), ('d', False)]:
                batches.record_member(conn, self.batch_id, 'synthetic:' + ident, is_new)
            batches.finish_batch(conn, self.batch_id, 'partial', error='synthetic_source_failed')
            db.log_run(conn, 'fetch', '2020-01-01T00:00:00+00:00', {})
            self.legacy_id = 'legacy-' + str(conn.execute('SELECT last_insert_rowid()').fetchone()[0])
            conn.commit()
        self.clock = [100.]
        self.server = agent_api.make_server(config, TOKEN, port=0,
            store=agent_api.SnapshotStore(clock=lambda: self.clock[0]))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        self.base = 'http://127.0.0.1:' + str(self.server.server_port)
        self.errlog = self.enterContext((self.root / 'mcp-stderr.log').open('w+'))

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def client(self, *, mode='auto', writes=False):
        from mcp.client.stdio import stdio_client
        params = StdioServerParameters(command=sys.executable,
            args=['-B', '-m', 'jobs.mcp_server', '--api-url', self.base,
                  '--token-file', str(self.token_file)] + (['--allow-label-writes'] if writes else []),
            cwd=str(self.root),
            env={'PYTHONPATH': str(ROOT), 'PYTHONDONTWRITEBYTECODE': '1'})
        return Client(stdio_client(params, errlog=self.errlog), read_timeout_seconds=15, mode=mode)

    def human_state(self):
        with closing(sqlite3.connect(self.path)) as conn:
            return {t: [tuple(r) for r in conn.execute('SELECT * FROM %s ORDER BY 1' % t)]
                    for t in ('labels', 'label_events')}

    async def call(self, client, name, arguments=None):
        result = await client.call_tool(name, arguments or {})
        self.assertFalse(result.is_error, result.content)
        self.assertIsInstance(result.structured_content, dict)
        return result.structured_content



class MCPIntegration(MCPFixture, unittest.IsolatedAsyncioTestCase):
    async def test_discovery_and_complete_frozen_read_without_writes(self):
        before = hashlib.sha256(self.path.read_bytes()).hexdigest()
        async with self.client() as client:
            tools = (await client.list_tools()).tools
            self.assertEqual({t.name for t in tools},
                             {'list_jobs', 'get_job', 'list_batches', 'get_batch'})
            for tool in tools:
                self.assertTrue(tool.annotations.read_only_hint)
                self.assertFalse(tool.annotations.destructive_hint)
            page = await self.call(client, 'list_jobs', {'query': {'limit': 1}})
            sid, total = page['snapshot_id'], page['total']
            self.assertEqual(total, 3)
            self.assertNotIn('description', page['items'][0])
            seen, pages = [], 0
            while True:
                pages += 1
                self.assertEqual(page['snapshot_id'], sid)
                self.assertEqual(page['total'], total)
                for row in page['items']:
                    detail = await self.call(client, 'get_job', {'uid': row['uid'], 'snapshot_id': sid})
                    self.assertEqual(detail['snapshot_id'], sid)
                    self.assertTrue(detail['item']['description'].startswith('Handwritten synthetic complete JD'))
                    seen.append(row['uid'])
                if page['next_cursor'] is None:
                    break
                page = await self.call(client, 'list_jobs', {'query': {'cursor': page['next_cursor']}})
            self.assertEqual((pages, len(set(seen))), (3, total))
            self.assertIn('synthetic:a', seen)
            self.assertNotIn('synthetic:b', seen)
            folded = await self.call(client, 'get_job', {'uid': 'synthetic:b', 'snapshot_id': sid})
            self.assertEqual(folded['item']['human']['action'], 'saved')
            self.assertEqual(folded['item']['human_scope'], 'group')
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(), before)
        self.errlog.flush()
        log = (self.root / 'mcp-stderr.log').read_text()
        self.assertNotIn(TOKEN, log)
        self.assertNotIn('Handwritten synthetic complete JD', log)

    async def test_text_is_compact_and_equals_structured_for_every_tool(self):
        body = 'Caf\u00e9 \u65e5\u672c "quoted" back\\slash\nline two\ttab  spaced'
        with closing(db.connect(self.path)) as conn:
            conn.execute('UPDATE jobs SET description=? WHERE uid=?', (body, 'synthetic:a'))
            conn.commit()
        async with self.client() as client:
            published = {t.name: t.output_schema for t in (await client.list_tools()).tools}
            self.assertTrue(all(isinstance(schema, dict) for schema in published.values()), published)
            page = await client.call_tool('list_jobs', {'query': {'limit': 1}})
            sid = page.structured_content['snapshot_id']
            results = {'list_jobs': page}
            results['get_job'] = await client.call_tool(
                'get_job', {'uid': 'synthetic:a', 'snapshot_id': sid})
            results['list_batches'] = await client.call_tool('list_batches', {'limit': 1})
            results['get_batch'] = await client.call_tool('get_batch', {'batch_id': self.batch_id})
            self.assertEqual(set(results), set(published))
            for name, result in results.items():
                with self.subTest(name=name):
                    self.assertFalse(result.is_error)
                    self.assertEqual(len(result.content), 1)
                    text = result.content[0].text
                    self.assertEqual(json.loads(text), result.structured_content)
                    self.assertEqual(text, json.dumps(result.structured_content, ensure_ascii=False,
                                                      separators=(',', ':')))
                    self.assertNotIn('\n', text)      # newlines inside the JD stay escaped
                    self.assertNotIn(': ', text.replace('"description":"' + body, ''))
            self.assertEqual(results['get_job'].structured_content['item']['description'], body)
            self.assertIn('Caf\u00e9 \u65e5\u672c', results['get_job'].content[0].text)

    async def test_snapshot_keeps_body_and_human_state_after_live_edit(self):
        async with self.client() as client:
            first = await self.call(client, 'list_jobs')
            with closing(db.connect(self.path)) as conn:
                conn.execute('UPDATE jobs SET description=? WHERE uid=?', ('Changed synthetic JD', 'synthetic:a'))
                labels.set_label(conn, 'synthetic:a', action='applied', note='Changed synthetic note')
                conn.commit()
            old = await self.call(client, 'get_job', {'uid': 'synthetic:a', 'snapshot_id': first['snapshot_id']})
            self.assertEqual(old['item']['description'], 'Handwritten synthetic complete JD a')
            self.assertEqual(old['item']['human']['action'], 'saved')
            fresh = await self.call(client, 'list_jobs')
            new = await self.call(client, 'get_job', {'uid': 'synthetic:a', 'snapshot_id': fresh['snapshot_id']})
            self.assertEqual(new['item']['description'], 'Changed synthetic JD')
            self.assertEqual(new['item']['human']['action'], 'applied')

    async def test_legacy_initialization_and_tool_call(self):
        async with self.client(mode='legacy') as client:
            self.assertEqual(client.server_info.name, 'AU Job Board (read-only)')
            self.assertEqual(len((await client.list_tools()).tools), 4)
            page = await self.call(client, 'list_jobs', {'query': {'limit': 1}})
            detail = await self.call(client, 'get_job', {
                'uid': page['items'][0]['uid'], 'snapshot_id': page['snapshot_id']})
            self.assertTrue(detail['item']['description'])

    async def test_filters_match_http_and_repeated_skills_are_encoded(self):
        async with self.client() as client:
            query = {'geo': 'VIC', 'sort': 'date', 'q': 'Synthetic', 'limit': 1}
            first = await self.call(client, 'list_jobs', {'query': query})
            direct = agent_client.APIClient(self.base, TOKEN).get('/api/v1/jobs', query)
            for key in ('total', 'items', 'query', 'membership'):
                self.assertEqual(first[key], direct[key])
            # Observe the real HTTP handler's parsed query, not just urlencode().
            original = self.server.api.jobs
            captured = []
            def capture(qs):
                captured.append(qs)
                return original(qs)
            with patch.object(self.server.api, 'jobs', side_effect=capture):
                await self.call(client, 'list_jobs', {'query': {'skill': ['python', 'rust']}})
            self.assertEqual(captured[0]['skill'], ['python', 'rust'])

    async def test_ineligible_last_matches_http_and_invalid_values_are_tool_errors(self):
        with closing(db.connect(self.path)) as conn:
            db.replace_decision(conn, 'synthetic:d', True, rule_id='R-synthetic', rule_kind='synthetic')
            conn.commit()
        async with self.client() as client:
            default = await self.call(client, 'list_jobs', {'query': {'limit': 10}})
            query = {'ineligible_last': '1', 'limit': 10}
            sunk = await self.call(client, 'list_jobs', {'query': query})
            direct = agent_client.APIClient(self.base, TOKEN).get('/api/v1/jobs', query)
            for key in ('total', 'items', 'query', 'membership'):
                self.assertEqual(sunk[key], direct[key])
            self.assertNotIn('ineligible_last', default['query']['filters'])
            self.assertEqual(sunk['query']['filters']['ineligible_last'], '1')
            self.assertEqual(sunk['items'][-1]['uid'], 'synthetic:d')   # machine-excluded sinks
            self.assertNotEqual([i['uid'] for i in default['items']][-1], 'synthetic:d')
            for arguments in ({'query': {'ineligible_last': '0'}},
                              {'query': {'ineligible_last': '1', 'view': 'recommend'}},
                              {'query': {'ineligible_last': ['1']}}):
                result = await client.call_tool('list_jobs', arguments)
                self.assertTrue(result.is_error, arguments)

    async def test_batch_membership_and_legacy_facts(self):
        async with self.client() as client:
            first = await self.call(client, 'list_batches', {'limit': 1})
            self.assertEqual(len(first['items']), 1)
            second = await self.call(client, 'list_batches', {'limit': 1, 'offset': first['next_offset']})
            self.assertIsNone(second['next_offset'])
            exact = await self.call(client, 'get_batch', {'batch_id': self.batch_id})
            self.assertEqual(exact['item']['collection_state'], 'partial')
            self.assertEqual(exact['item']['pipeline']['state'], 'unknown')
            legacy = await self.call(client, 'get_batch', {'batch_id': self.legacy_id})
            self.assertEqual(legacy['item']['membership'], 'unavailable')
            new = await self.call(client, 'list_jobs', {'query': {'scope': 'batch_new', 'batch_id': self.batch_id}})
            self.assertEqual([r['uid'] for r in new['items']], ['synthetic:a'])
            seen = await self.call(client, 'list_jobs', {'query': {'scope': 'batch_seen', 'batch_id': self.batch_id}})
            self.assertEqual({r['uid'] for r in seen['items']}, {'synthetic:a', 'synthetic:d'})

    async def test_bad_query_expiry_auth_and_unavailable_are_tool_errors(self):
        async with self.client() as client:
            page = await self.call(client, 'list_jobs', {'query': {'limit': 1}})
            cases = [('list_jobs', {'query': {'geo': 'VIC', 'view': 'recommend'}}, '400'),
                     ('list_jobs', {'query': {'geo': []}}, None),
                     ('list_jobs', {'query': {'scope': ['batch_new']}}, None),
                     ('list_jobs', {'query': {'skill': []}}, None),
                     ('list_jobs', {'query': {'cursor': page['next_cursor'], 'limit': 1}}, '400'),
                     ('get_job', {'uid': 'synthetic:a'}, None),
                     ('get_job', {'uid': 'synthetic:a', 'snapshot_id': ''}, None),
                     ('get_job', {'uid': 'synthetic:missing', 'snapshot_id': page['snapshot_id']}, '404'),
                     ('set_label', {'uid': 'synthetic:a', 'action': 'applied'}, None)]
            for name, args, code in cases:
                with self.subTest(name=name, args=args):
                    result = await client.call_tool(name, args)
                    self.assertTrue(result.is_error)
                    if code:
                        self.assertIn(code, json.dumps([c.model_dump() for c in result.content]))
            self.clock[0] += 301
            for name, args in [('get_job', {'uid': 'synthetic:a', 'snapshot_id': page['snapshot_id']}),
                               ('list_jobs', {'query': {'cursor': page['next_cursor']}})]:
                result = await client.call_tool(name, args)
                self.assertTrue(result.is_error)
                self.assertIn('410', json.dumps([c.model_dump() for c in result.content]))
            self.server.token = 'different-synthetic-token-not-a-secret-00000000'
            result = await client.call_tool('list_jobs', {})
            self.assertTrue(result.is_error)
            self.assertIn('401', json.dumps([c.model_dump() for c in result.content]))
            self.server.shutdown()
            self.server.server_close()
            result = await client.call_tool('list_jobs', {})
            self.assertTrue(result.is_error)
            self.assertNotIn(TOKEN, str(result))

    async def test_redirect_refused_and_errors_do_not_expose_private_fields(self):
        async with self.client() as client:
            def redirect(handler):
                handler.send_response(302)
                handler.send_header('Location', 'https://example.invalid/private-synthetic-url')
                handler.send_header('Content-Length', '0')
                handler.end_headers()
            with patch.object(agent_api.Handler, 'do_GET', redirect):
                result = await client.call_tool('list_jobs', {})
            self.assertTrue(result.is_error)
            self.assertIn('redirects are not followed', str(result))
            self.assertNotIn('private-synthetic-url', str(result))
            self.assertNotIn(TOKEN, str(result))

    def test_startup_rejects_unsafe_url_and_public_token_file(self):
        for url, public_token in [('http://example.invalid', False), (self.base, True)]:
            if public_token:
                grant_public_read(self.token_file)
            proc = subprocess.run([sys.executable, '-B', '-m', 'jobs.mcp_server',
                '--api-url', url, '--token-file', str(self.token_file)], cwd=ROOT,
                input='', capture_output=True, text=True, timeout=10)
            self.assertEqual(proc.returncode, 1)
            self.assertEqual(proc.stdout, '')
            self.assertIn('MCP startup failed', proc.stderr)
            self.assertNotIn(TOKEN, proc.stderr)


READ_TOOLS = {'list_jobs', 'get_job', 'list_batches', 'get_batch'}
WRITE_TOOL = 'batch_label_jobs'


def text_of(result):
    return json.dumps([c.model_dump() for c in result.content])


class MCPLabelWrites(MCPFixture, unittest.IsolatedAsyncioTestCase):
    """Real stdio SDK -> HTTP API with agent_api.allow_label_writes enabled."""
    label_writes = True

    async def test_flag_registers_a_fifth_tool_with_write_annotations(self):
        async with self.client(writes=True) as client:
            self.assertEqual(client.server_info.name, 'AU Job Board')
            tools = {t.name: t for t in (await client.list_tools()).tools}
            self.assertEqual(set(tools), READ_TOOLS | {WRITE_TOOL})
            for name in READ_TOOLS:
                note = tools[name].annotations
                self.assertEqual((note.read_only_hint, note.destructive_hint,
                                  note.idempotent_hint, note.open_world_hint),
                                 (True, False, True, False), name)
            note = tools[WRITE_TOOL].annotations
            self.assertEqual((note.read_only_hint, note.destructive_hint,
                              note.idempotent_hint, note.open_world_hint), (False, True, True, False))
            self.assertIsInstance(tools[WRITE_TOOL].output_schema, dict)
            schema = tools[WRITE_TOOL].input_schema
            self.assertEqual(schema['required'], ['items'])
            self.assertEqual((schema['properties']['items']['minItems'],
                              schema['properties']['items']['maxItems']), (1, 100))
            item = schema['$defs']['LabelItem']
            self.assertEqual(set(item['properties']), {'uid', 'action', 'priority'})
            self.assertEqual(item['required'], ['uid'])
            self.assertFalse(item['additionalProperties'])
            self.assertNotIn('default', item['properties']['action'])
            self.assertNotIn('default', item['properties']['priority'])
            text = client.instructions
            self.assertNotIn('Read-only access', text)
            for phrase in ('untrusted', 'never', 'action', 'priority', 'cannot'):
                self.assertIn(phrase, text)

    async def test_default_stdio_stays_read_only_even_when_the_api_allows_writes(self):
        before = self.human_state()
        async with self.client() as client:
            self.assertEqual(client.server_info.name, 'AU Job Board (read-only)')
            self.assertEqual({t.name for t in (await client.list_tools()).tools}, READ_TOOLS)
            result = await client.call_tool(WRITE_TOOL, {'items': [{'uid': 'synthetic:c', 'action': 'saved'}]})
            self.assertTrue(result.is_error)
        self.assertEqual(self.human_state(), before)

    async def test_call_persists_group_state_and_returns_equal_compact_channels(self):
        async with self.client(writes=True) as client:
            frozen = await self.call(client, 'list_jobs', {'query': {'limit': 10}})
            result = await client.call_tool(WRITE_TOOL, {'items': [
                {'uid': 'synthetic:b', 'action': 'applied', 'priority': True},   # group of a
                {'uid': 'synthetic:c', 'action': 'skipped'},
                {'uid': 'synthetic:d', 'priority': False}]})
            self.assertFalse(result.is_error, result.content)
            self.assertEqual(len(result.content), 1)
            text = result.content[0].text
            self.assertEqual(json.loads(text), result.structured_content)
            self.assertEqual(text, json.dumps(result.structured_content, ensure_ascii=False,
                                              separators=(',', ':'), allow_nan=False))
            self.assertNotIn('\n', text)
            data = result.structured_content
            self.assertEqual((data['api_version'], data['updated'], data['unchanged']), (1, 2, 1))
            self.assertEqual([(i['uid'], i['action'], i['priority'], i['changed'], i['human_scope'])
                              for i in data['items']],
                             [('synthetic:b', 'applied', True, True, 'group'),
                              ('synthetic:c', 'skipped', False, True, 'group'),
                              ('synthetic:d', None, False, False, 'group')])
            self.assertNotIn('Handwritten', text)
            self.assertNotIn('Synthetic group note', text)
            # The fixture's group note on a survives; a saw the group-wide change.
            first = await self.call(client, 'list_jobs', {'query': {'limit': 10}})
            detail = await self.call(client, 'get_job', {'uid': 'synthetic:a', 'snapshot_id': first['snapshot_id']})
            human = detail['item']['human']
            self.assertEqual((human['action'], human['priority'], human['note']),
                             ('applied', 1, 'Synthetic group note；'))
            # null cancels; an omitted field is kept; repeating is a no-op.
            cleared = await self.call(client, WRITE_TOOL, {'items': [{'uid': 'synthetic:a', 'action': None}]})
            self.assertEqual(cleared['items'][0], {'uid': 'synthetic:a', 'action': None, 'priority': True,
                                                   'changed': True, 'human_scope': 'group'})
            again = await self.call(client, WRITE_TOOL, {'items': [{'uid': 'synthetic:a', 'action': None}]})
            self.assertEqual((again['updated'], again['unchanged']), (0, 1))
            old = await self.call(client, 'get_job', {'uid': 'synthetic:a', 'snapshot_id': frozen['snapshot_id']})
            self.assertEqual((old['item']['human']['action'], old['item']['human']['priority']),
                             ('saved', None))   # a snapshot taken before the writes keeps its state

    async def test_invalid_arguments_and_rejected_batches_are_tool_errors_without_writes(self):
        before = self.human_state()
        good = {'uid': 'synthetic:c', 'action': 'saved'}
        cases = {
            'priority text': [good, {'uid': 'synthetic:d', 'priority': 'true'}],
            'priority int': [good, {'uid': 'synthetic:d', 'priority': 1}],
            'bad action': [good, {'uid': 'synthetic:d', 'action': 'done'}],
            'extra field': [good, {'uid': 'synthetic:d', 'action': 'saved', 'note': 'x'}],
            'no field': [good, {'uid': 'synthetic:d'}],
            'empty': [], 'too many': [{'uid': 'synthetic:z%d' % n, 'action': 'saved'} for n in range(101)],
            'empty uid': [good, {'uid': '', 'action': 'saved'}],
            'duplicate': [good, {'uid': 'synthetic:c', 'priority': True}],
            'same group': [{'uid': 'synthetic:a', 'action': 'saved'}, {'uid': 'synthetic:b', 'priority': True}],
            'unknown': [good, {'uid': 'synthetic:missing', 'action': 'saved'}],
        }
        async with self.client(writes=True) as client:
            for name, items in cases.items():
                with self.subTest(name):
                    result = await client.call_tool(WRITE_TOOL, {'items': items})
                    self.assertTrue(result.is_error, name)
                    self.assertNotIn(TOKEN, text_of(result))
                    self.assertEqual(self.human_state(), before)
            result = await client.call_tool(WRITE_TOOL, {'items': [good, {'uid': 'synthetic:missing', 'action': 'saved'}]})
            self.assertIn('HTTP 400: unknown_uid at item 1', text_of(result))
            self.assertNotIn('synthetic:missing', text_of(result))
            self.assertEqual(self.human_state(), before)
            self.assertTrue((await client.call_tool(WRITE_TOOL, {})).is_error)

    async def test_redirect_and_auth_failure_do_not_write_or_leak(self):
        before = self.human_state()
        items = {'items': [{'uid': 'synthetic:c', 'action': 'applied'}]}
        async with self.client(writes=True) as client:
            def redirect(handler):
                handler.send_response(307)
                handler.send_header('Location', 'https://example.invalid/private-synthetic-url')
                handler.send_header('Content-Length', '0')
                handler.end_headers()
            with patch.object(agent_api.Handler, 'do_POST', redirect):
                result = await client.call_tool(WRITE_TOOL, items)
            self.assertTrue(result.is_error)
            self.assertIn('redirects are not followed', text_of(result))
            self.assertNotIn('private-synthetic-url', text_of(result))
            self.assertEqual(self.human_state(), before)
            self.server.token = 'different-synthetic-token-not-a-secret-00000000'
            result = await client.call_tool(WRITE_TOOL, items)
            self.assertTrue(result.is_error)
            self.assertIn('HTTP 401', text_of(result))
            self.assertNotIn(TOKEN, text_of(result))
            self.assertEqual(self.human_state(), before)

    async def test_connection_loss_is_an_error_not_a_success(self):
        before = self.human_state()
        async with self.client(writes=True) as client:
            self.server.shutdown()
            self.server.server_close()
            result = await client.call_tool(WRITE_TOOL, {'items': [{'uid': 'synthetic:c', 'action': 'saved'}]})
            self.assertTrue(result.is_error)
            self.assertIn('not retried automatically', text_of(result))
            self.assertNotIn(self.base, text_of(result))
        self.assertEqual(self.human_state(), before)


class MCPFlagWithoutApiPermission(MCPFixture, unittest.IsolatedAsyncioTestCase):
    """The stdio flag cannot grant what the API configuration refuses."""

    async def test_tool_is_listed_but_every_call_is_refused(self):
        before = self.human_state()
        async with self.client(writes=True) as client:
            self.assertIn(WRITE_TOOL, {t.name for t in (await client.list_tools()).tools})
            result = await client.call_tool(WRITE_TOOL, {'items': [{'uid': 'synthetic:c', 'action': 'saved'}]})
            self.assertTrue(result.is_error)
            self.assertIn('HTTP 403: label_writes_disabled', text_of(result))
            self.assertEqual(await self.call(client, 'list_batches', {'limit': 1}) is not None, True)
        self.assertEqual(self.human_state(), before)


if __name__ == '__main__':
    unittest.main(verbosity=2)
