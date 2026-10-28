"""Authenticated loopback API, source scope and immutable snapshot regressions.

Every record and token here is handwritten synthetic data. No production paths.
"""
from pathlib import Path
from contextlib import closing
import datetime as dt
import hashlib
import http.client
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jobs import agent_api as api, agent_client, batches, db, labels, panel_data

TOKEN = 'synthetic-api-token-not-a-real-secret-000000'


class AgentAPI(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path = self.root/'synthetic.db'
        self.skills = self.root/'skills.yaml'; self.skills.write_text('skills: []\n')
        self.config = {'db_path': str(self.path), 'skills_path': str(self.skills), 'panel': {'page_size': 40}}
        with closing(db.connect(self.path)) as conn:
            for uid, loc, score in [
                    ('a', 'Melbourne VIC / Sydney NSW', 9),
                    ('b', 'Sydney NSW', 20),
                    ('c', 'Undisclosed', 2)]:
                self.job(conn, uid, loc, score)
            db.set_dedupe(conn, 'synthetic:a', 'synthetic-group', 'synthetic-group')
            db.set_dedupe(conn, 'synthetic:b', 'synthetic-group', 'synthetic-group')
            labels.set_label(conn, 'synthetic:a', action='applied',
                             note='Synthetic group context')
        self.clock = [100.]
        self.store = api.SnapshotStore(clock=lambda: self.clock[0])
        self.server = api.make_server(self.config, TOKEN, port=0, store=self.store, panel_url='http://127.0.0.1:8299/')
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()

    def job(self, conn, ident, location='Melbourne VIC', score=5):
        db.upsert_job(conn, dict(source='synthetic', source_id=ident, title='Synthetic Developer '+ident,
                               company='Synthetic Company', location=location, url='https://example.invalid/jobs/'+ident,
                               description='Handwritten synthetic full job description '+ident, listing_date='2026-10-22'))
        db.set_dedupe(conn, 'synthetic:'+ident, ident, ident)
        db.replace_decision(conn, 'synthetic:'+ident, False)
        db.replace_score(conn, 'synthetic:'+ident, 'T1', 0, 0, 0, score, [], 1)
        conn.commit()

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(); self.temp.cleanup()

    def request(self, path, *, method='GET', token=TOKEN, headers=None, server=None):
        server = self.server if server is None else server
        c = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=10)
        h = {} if token is None else {'Authorization': 'Bearer '+token}
        h.update(headers or {})
        c.request(method, path, headers=h)
        response = c.getresponse(); body = response.read(); status = response.status
        out = json.loads(body) if body else None
        hs = dict(response.getheaders()); c.close()
        return status, out, hs

    def first(self, query=''):
        status, data, _ = self.request('/api/v1/jobs'+query)
        self.assertEqual(status, 200, data)
        return data

    def test_auth_origin_host_and_write_routes(self):
        for path in ['/api/v1/jobs', '/api/v1/batches', '/api/v1/jobs/synthetic%3Aa', '/api/config-settings']:
            self.assertEqual(self.request(path, token=None)[0], 401)
            self.assertEqual(self.request(path, token='wrong')[0], 401)
        for headers in [{'Origin': 'http://evil.invalid'}, {'Origin': 'null'}, {'Host': 'evil.invalid'}]:
            self.assertEqual(self.request('/api/v1/jobs', headers=headers)[0], 403)
        for method in ['POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS', 'TRACE']:
            self.assertEqual(self.request('/api/label', method=method)[0], 405)
        self.assertEqual(self.request('/api/label')[0], 404)
        _, _, headers = self.request('/api/v1/jobs')
        self.assertNotIn('Access-Control-Allow-Origin', headers)
        self.assertEqual(headers['Cache-Control'], 'no-store')

    def test_bind_host_rejects_source_wildcard_and_nonloopback_addresses(self):
        for container in (None, '', '0', 'true', '1'):
            with patch.dict(os.environ):
                os.environ.pop('JOBS_CONTAINER', None)
                if container is not None:
                    os.environ['JOBS_CONTAINER'] = container
                hosts = ['', '::', '192.0.2.1', 'example.invalid']
                if container != '1':
                    hosts.append('0.0.0.0')
                for host in hosts:
                    with self.subTest(container=container, host=host):
                        with patch.object(api, 'ThreadingHTTPServer') as listener:
                            with self.assertRaises(ValueError):
                                api.make_server(self.config, TOKEN, host=host, port=0)
                            listener.assert_not_called()
                for host in ('127.0.0.1', 'localhost'):
                    with api.make_server(self.config, TOKEN, host=host, port=0) as server:
                        self.assertEqual(server.server_address[0], '127.0.0.1')
        self.first()

    def test_container_wildcard_http_keeps_auth_host_origin_and_readonly_guards(self):
        with patch.dict(os.environ, JOBS_CONTAINER='1'):
            server = api.make_server(self.config, TOKEN, host='0.0.0.0', port=0)
        self.addCleanup(server.server_close)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(server.shutdown)
        self.assertEqual(server.server_address[0], '0.0.0.0')
        before = self.path.read_bytes()

        def request(path='/api/v1/jobs/synthetic%3Aa', **kwargs):
            return self.request(path, server=server, **kwargs)

        def followup():
            status, data, headers = request()
            self.assertEqual(status, 200, data)
            self.assertEqual(data['item']['uid'], 'synthetic:a')
            self.assertEqual(headers['Cache-Control'], 'no-store')
            self.assertNotIn('Access-Control-Allow-Origin', headers)

        with patch('jobs.db.connect', side_effect=AssertionError('writer forbidden')):
            for token in (None, 'wrong'):
                status, data, headers = request(token=token)
                self.assertEqual((status, data), (401, {'error': 'unauthorized'}))
                self.assertEqual(headers['WWW-Authenticate'], 'Bearer')
                followup()
            for headers in (
                    {'Host': f'0.0.0.0:{server.server_port}'},
                    {'Host': f'localhost:{server.server_port}'},
                    {'Host': f'evil.invalid:{server.server_port}'},
                    {'Host': '127.0.0.1:0'}, {'Host': '127.0.0.1'},
                    {'Origin': f'http://127.0.0.1:{server.server_port}'},
                    {'Origin': 'null'}, {'Origin': ''}):
                with self.subTest(headers=headers):
                    status, data, _ = request(headers=headers)
                    self.assertEqual((status, data), (403, {'error': 'forbidden_origin_or_host'}))
                    followup()
            for method in ('POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS', 'TRACE', 'CONNECT', 'HEAD'):
                with self.subTest(method=method):
                    status, data, _ = request('/api/label', method=method)
                    self.assertEqual(status, 405)
                    self.assertEqual(data, None if method == 'HEAD' else {'error': 'method_not_allowed'})
                    followup()
            # The client can still list and read full details through the
            # wildcard bind, while every database connection remains read-only.
            exported = agent_client.read_jobs(f'http://127.0.0.1:{server.server_port}', TOKEN, {})
            self.assertTrue(exported['complete'])
            self.assertEqual(exported['total'], 2)
            with closing(api.read_connection(self.path)) as conn:
                self.assertEqual(conn.execute('PRAGMA query_only').fetchone()[0], 1)
                with self.assertRaises(sqlite3.OperationalError):
                    conn.execute('DELETE FROM jobs')
        self.assertEqual(self.path.read_bytes(), before)

    def test_cli_host_container_opt_in_with_synthetic_config(self):
        config_path = self.root/'config.yaml'
        config_path.write_text(json.dumps(self.config))
        token_path = self.root/'agent.token'
        token_path.write_text(TOKEN)
        from jobs.file_security import set_private
        set_private(token_path)
        args = ['--config', str(config_path), '--token-file', str(token_path),
                '--host', '0.0.0.0', '--port', '0']
        env = {**os.environ, 'JOBS_CONTAINER': '0'}
        rejected = subprocess.run([sys.executable, '-B', '-m', 'jobs.agent_api', *args],
                                  cwd=Path(__file__).resolve().parents[1], env=env,
                                  capture_output=True, text=True, timeout=10)
        self.assertEqual(rejected.returncode, 2, rejected.stderr)
        self.assertIn('wildcard requires JOBS_CONTAINER=1', rejected.stderr)
        self.assertNotIn('listening', rejected.stdout)
        # Use the same CLI argument path, then issue real HTTP while its
        # listener is active. The socket is always closed by main().
        original = api.ThreadingHTTPServer.serve_forever
        def exercise(server):
            self.assertEqual(server.server_address[0], '0.0.0.0')
            thread = threading.Thread(target=original, args=(server,), daemon=True)
            thread.start()
            try:
                status, data, _ = self.request('/api/v1/jobs/synthetic%3Aa', server=server)
                self.assertEqual(status, 200, data)
            finally:
                server.shutdown()
                thread.join()
        with patch.dict(os.environ, JOBS_CONTAINER='1'), patch.object(
                api.ThreadingHTTPServer, 'serve_forever', exercise):
            self.assertEqual(api.main(args), 0)

    def test_complete_337_pagination_stable_detail_and_changes(self):
        with closing(db.connect(self.path)) as conn:
            conn.execute('DELETE FROM labels'); conn.execute('DELETE FROM label_events')
            for i in range(337): self.job(conn, 'page-'+str(i), score=i)
        result = self.first('?limit=100'); seen = list(result['items']); sid = result['snapshot_id']
        first_uid = seen[0]['uid']
        with closing(db.connect(self.path)) as conn:
            self.job(conn, 'later', score=999)
            conn.execute('UPDATE jobs SET description=? WHERE uid=?', ('Changed after snapshot', first_uid)); conn.commit()
        while result['next_cursor']:
            result = self.first('?cursor='+quote(result['next_cursor'], safe=''))
            self.assertEqual(result['snapshot_id'], sid); seen.extend(result['items'])
        self.assertEqual(len(seen), 339)
        self.assertEqual(len({r['uid'] for r in seen}), 339)
        self.assertNotIn('synthetic:later', {r['uid'] for r in seen})
        status, detail, _ = self.request('/api/v1/jobs/'+quote(first_uid,safe='')+'?snapshot_id='+sid)
        self.assertEqual(status, 200); self.assertIn('Handwritten synthetic', detail['item']['description'])
        self.assertEqual(self.request('/api/v1/jobs/synthetic%3Alater?snapshot_id='+sid)[0], 404)
        self.assertEqual(self.request('/api/v1/jobs/'+quote(first_uid,safe=''))[1]['item']['description'], 'Changed after snapshot')

    def test_filter_parity_folded_source_and_group_scope(self):
        result = self.first('?geo=Melbourne%20VIC')
        with closing(api.read_connection(self.path)) as conn:
            expected, total = panel_data._query(conn, self.config, 'all', 0, 100, {'geo':'Melbourne VIC'})
        self.assertEqual([r['uid'] for r in result['items']], [r['uid'] for r in expected])
        self.assertEqual(result['total'], total)
        row = next(r for r in result['items'] if r['uid']=='synthetic:a')
        self.assertEqual(row['human_scope'], 'group')
        self.assertEqual(row['human']['action'], 'applied')
        self.assertIn('Synthetic group context', row['human']['note'])
        self.assertNotIn('location_id=', row['panel_url'])
        self.assertEqual(row['location_match'], 'near')
        all_result = self.first()
        all_uids = {r['uid'] for r in all_result['items']}
        self.assertIn('synthetic:a', all_uids)
        self.assertNotIn('synthetic:b', all_uids)
        status, detail, _ = self.request('/api/v1/jobs/synthetic%3Ab?snapshot_id='+all_result['snapshot_id'])
        self.assertEqual(status, 200); self.assertEqual(detail['item']['uid'], 'synthetic:b')
        self.assertEqual(detail['item']['human']['action'], 'applied')
        self.assertEqual(self.request(
            '/api/v1/jobs/synthetic%3Ab?snapshot_id='+all_result['snapshot_id']+'&location_id=old')[0], 400)

    def test_cursor_tampering_query_binding_expiry_and_restart(self):
        result = self.first('?limit=1'); cursor = result['next_cursor']
        self.assertEqual(self.request('/api/v1/jobs?cursor='+quote(cursor)+'&limit=2')[0], 400)
        bad = ('A' if cursor[0]!='A' else 'B')+cursor[1:]
        self.assertEqual(self.request('/api/v1/jobs?cursor='+quote(bad))[0], 400)
        self.clock[0] += 301
        self.assertEqual(self.request('/api/v1/jobs?cursor='+quote(cursor))[0], 410)
        self.assertEqual(self.request('/api/v1/jobs/synthetic%3Aa?snapshot_id='+result['snapshot_id'])[0], 410)
        self.server.api.store = api.SnapshotStore()
        self.assertEqual(self.request('/api/v1/jobs?cursor='+quote(cursor))[0], 400)

    def test_readonly_missing_schema_and_no_runtime_repair(self):
        before = self.path.read_bytes()
        runtime = self.root/'.synthetic.db.panel-update'; runtime.mkdir()
        marker = runtime/'latest.json'; marker.write_text('{"state":"running","task_id":"synthetic-pipeline"}')
        old = marker.read_bytes()
        with patch('jobs.db.connect', side_effect=AssertionError('writer forbidden')), patch('jobs.run_state.latest', side_effect=AssertionError('repair forbidden')):
            self.first(); self.request('/api/v1/jobs/synthetic%3Aa')
            self.assertEqual(api._pipeline(self.config, 'synthetic-pipeline')['state'], 'unknown')
        self.assertEqual(self.path.read_bytes(), before); self.assertEqual(marker.read_bytes(), old)
        with closing(api.read_connection(self.path)) as conn:
            with self.assertRaises(sqlite3.OperationalError): conn.execute('DELETE FROM jobs')
        missing = self.root/'missing'/'absent.db'
        with self.assertRaises(sqlite3.OperationalError): api.read_connection(missing)
        self.assertFalse(missing.parent.exists())
        with closing(sqlite3.connect(self.path)) as conn: conn.execute('PRAGMA user_version=0'); conn.commit()
        self.assertEqual(self.request('/api/v1/jobs')[0], 503)

    def test_invalid_queries_and_capacity_are_explicit(self):
        for query in ['?limit=0','?limit=201','?limit=no','?view=invalid','?q=a&q=b','?mode=office','?geo=not-a-real-place',
                      '?scope=batch_new&day=2026-10-22','?scope=first_seen_day','?scope=legacy_new&view=all','?salary=0', '?action=bogus', '?view=recommend&action=saved']:
            self.assertEqual(self.request('/api/v1/jobs'+query)[0], 400, query)
        self.server.api.store = api.SnapshotStore(max_snapshots=1)
        original = self.first('?limit=1')
        self.assertEqual(self.request('/api/v1/jobs')[0],429)
        self.assertEqual(self.request('/api/v1/jobs?cursor='+quote(original['next_cursor']))[0],200)
        self.server.api.store = api.SnapshotStore(max_snapshot_bytes=10)
        self.assertEqual(self.request('/api/v1/jobs')[0],413)

    def test_invalid_days_return_json_and_allow_subsequent_requests(self):
        with patch.object(self.server, 'handle_error') as handle_error:
            for day in ('9999-12-31', '0001-01-01', '2026-02-30', 'not-a-day', '20261022'):
                with self.subTest(day=day):
                    status, data, headers = self.request(
                        '/api/v1/jobs?scope=first_seen_day&day=' + day)
                    self.assertEqual(status, 400)
                    self.assertEqual(data, {'error': 'invalid_day'})
                    self.assertIn('application/json', headers['Content-Type'])
                    self.first()
            handle_error.assert_not_called()
        # Corrupt stored boundaries are skipped rather than crashing the request.
        self.assertIsNone(api._timestamp('0001-01-01T00:00:00+10:00'))

    def test_melbourne_day_boundaries_and_dst(self):
        for day, hours in [('2026-10-04', 23), ('2026-04-05', 25), ('2026-10-22', 24)]:
            with self.subTest(day=day):
                start, end = api.day_window(day)
                self.assertEqual((end-start).total_seconds(), hours*3600)
                window = self.first('?scope=first_seen_day&day=' + day)['time_window']
                self.assertEqual(window, {'start': start.isoformat(), 'end': end.isoformat(),
                                          'timezone': 'Australia/Melbourne'})
        start,end=api.day_window('2026-10-22')
        with closing(db.connect(self.path)) as conn:
            conn.execute('UPDATE jobs SET first_seen_at=? WHERE uid=?',(start.isoformat(),'synthetic:a'))
            conn.execute('UPDATE jobs SET first_seen_at=? WHERE uid=?',(end.isoformat(),'synthetic:b'))
            conn.execute('UPDATE jobs SET first_seen_at=? WHERE uid=?',((start-dt.timedelta(seconds=1)).isoformat(),'synthetic:c'));conn.commit()
        result=self.first('?scope=first_seen_day&day=2026-10-22')
        self.assertEqual([r['uid'] for r in result['items']],['synthetic:a'])
        self.assertEqual(result['time_window']['timezone'],'Australia/Melbourne')

    def test_token_file_permissions_and_panel_url_validation(self):
        from jobs.file_security import set_private
        token=self.root/'token';token.write_text(TOKEN);set_private(token)
        self.assertEqual(api.read_token(token),TOKEN)
        if os.name != 'nt':
            token.chmod(0o644)
            with self.assertRaises(ValueError):api.read_token(token)
            with self.assertRaises(agent_client.ClientError):agent_client.read_token(token)
        for value in ['short','x'*32+' space','é'*32]:
            with self.assertRaises(ValueError):api.make_server(self.config,value,port=0)
        for url in ['javascript:alert(1)','http://user:pass@example.invalid','http://example.invalid/#fragment']:
            with self.assertRaises(ValueError):api.AgentAPI(self.config,panel_url=url)

    def test_exact_batch_filters_before_grouping_and_legacy_is_unavailable(self):
        with closing(db.connect(self.path)) as conn:
            bid=batches.start_batch(conn,pipeline_task_id='synthetic-task')
            batches.record_member(conn,bid,'synthetic:a',True)
            batches.record_member(conn,bid,'synthetic:c',False)
            batches.finish_batch(conn,bid,'partial',error='source_failed');conn.commit()
            db.log_run(conn,'fetch','2020-01-01T00:00:00+00:00',{});legacy='legacy-'+str(conn.execute('SELECT last_insert_rowid()').fetchone()[0]);conn.commit()
        result=self.first('?scope=batch_new&batch_id='+bid)
        self.assertEqual([r['uid'] for r in result['items']],['synthetic:a'])
        self.assertEqual(result['batch']['collection_state'],'partial')
        self.assertEqual(result['batch']['pipeline']['state'],'unknown')
        self.assertTrue(result['items'][0]['batch_membership']['is_new'])
        result=self.first('?scope=batch_seen&batch_id='+bid)
        self.assertEqual({r['uid'] for r in result['items']},{'synthetic:a','synthetic:c'})
        self.assertEqual(self.request('/api/v1/jobs?scope=batch_new&batch_id='+legacy)[0],409)
        status,data,_=self.request('/api/v1/batches/'+legacy)
        self.assertEqual(status,200);self.assertEqual(data['item']['membership'],'unavailable')
        status,data,_=self.request('/api/v1/batches?limit=1')
        self.assertEqual(status,200);self.assertEqual(len(data['items']),1);self.assertEqual(data['next_offset'],1)

    def test_scoped_group_membership_is_identical_with_location_filter(self):
        with closing(db.connect(self.path)) as conn:
            self.job(conn, 'mixed-kept', 'Carlton VIC', 8)
            self.job(conn, 'mixed-excluded', 'Melbourne VIC', 4)
            db.set_dedupe(conn, 'synthetic:mixed-kept', 'mixed-group', 'mixed-group')
            db.set_dedupe(conn, 'synthetic:mixed-excluded', 'mixed-group', 'mixed-group')
            db.replace_decision(conn, 'synthetic:mixed-excluded', True)
            batch_id = batches.start_batch(conn)
            batches.record_member(conn, batch_id, 'synthetic:mixed-kept', True)
            batches.finish_batch(conn, batch_id, 'success')
            conn.commit()
        base = f'?scope=batch_new&batch_id={batch_id}&view=excluded'
        plain = self.first(base)
        located = self.first(base + '&geo=VIC')
        self.assertEqual(plain['total'], located['total'])
        self.assertEqual([row['uid'] for row in plain['items']],
                         ['synthetic:mixed-excluded'])
        self.assertEqual([row['uid'] for row in located['items']],
                         ['synthetic:mixed-excluded'])

    def test_unfinished_batch_rejected_and_new_batch_does_not_mix_snapshot(self):
        with closing(db.connect(self.path)) as conn:
            bid=batches.start_batch(conn)
            batches.record_member(conn,bid,'synthetic:a',True);conn.commit()
        self.assertEqual(self.request('/api/v1/jobs?scope=batch_seen&batch_id='+bid)[0],409)
        with closing(db.connect(self.path)) as conn:
            batches.record_member(conn,bid,'synthetic:c',True)
            batches.finish_batch(conn,bid,'success');conn.commit()
        first=self.first('?scope=batch_new&limit=1')
        self.assertEqual(first['resolved_batch_id'],bid)
        with closing(db.connect(self.path)) as conn:
            later=batches.start_batch(conn)
            batches.record_member(conn,later,'synthetic:b',True);batches.finish_batch(conn,later,'success');conn.commit()
        page=self.first('?cursor='+quote(first['next_cursor']))
        self.assertEqual(page['resolved_batch_id'],bid)
        self.assertEqual(page['total'],2)
        self.assertEqual(self.first('?scope=batch_new')['resolved_batch_id'],later)

    def test_external_client_complete_export_and_failure_preserves_old_file(self):
        base='http://127.0.0.1:'+str(self.server.server_port)
        value=agent_client.read_jobs(base,TOKEN,{'limit':1})
        self.assertTrue(value['complete']);self.assertEqual(value['total'],2)
        self.assertTrue(all(r['description'] for r in value['items']))
        target=self.root/'export.json';agent_client.export(target,value);old=target.read_bytes()
        if os.name == "nt":
            from jobs.file_security import _WindowsSecurity
            _WindowsSecurity().validate(target)
        else:
            self.assertEqual(target.stat().st_mode&0o077,0)
        original=self.server.api.jobs
        def fail_later(qs):
            if 'cursor' in qs:raise api.APIError('synthetic_failure',503)
            return original(qs)
        with patch.object(self.server.api,'jobs',side_effect=fail_later):
            with self.assertRaises(agent_client.ClientError):agent_client.read_jobs(base,TOKEN,{'limit':1})
        self.assertEqual(target.read_bytes(),old)
        with self.assertRaises(agent_client.ClientError):agent_client.export(target,{'complete':False,'total':0,'items':[]})
        self.assertEqual(target.read_bytes(),old)
        with self.assertRaises(agent_client.ClientError):agent_client.read_jobs('http://example.invalid',TOKEN,{})
        with self.assertRaises(agent_client.ClientError):agent_client.read_jobs(base,'wrong',{})

    def test_group_qualification_is_frozen_in_snapshots(self):
        first = self.first()
        sid = first['snapshot_id']
        with closing(db.connect(self.path)) as conn:
            labels.set_label(conn, 'synthetic:a', eligibility='ineligible')
        live = self.request('/api/v1/jobs/synthetic%3Aa')[1]['item']
        self.assertEqual(live['human']['eligibility'], 'ineligible')
        self.assertEqual(live['qualification']['effective'], 'ineligible')
        self.assertTrue(live['qualification']['corrected'])
        frozen = self.request(
            '/api/v1/jobs/synthetic%3Aa?snapshot_id=' + sid)[1]['item']
        self.assertIsNone(frozen['human']['eligibility'])

    def test_first_failed_pipeline_without_generation_remains_confirmed(self):
        runtime=self.root/'.synthetic.db.panel-update';runtime.mkdir()
        for state in ('error','timeout','interrupted'):
            (runtime/'latest.json').write_text(json.dumps({'state':state,'task_id':'synthetic-task','completion_ready':True,'refresh_available':False}))
            self.assertEqual(api._pipeline(self.config,'synthetic-task')['state'],state)
        (runtime/'latest.json').write_text(json.dumps({'state':'success','task_id':'synthetic-task','completion_ready':True,'refresh_available':True}))
        self.assertEqual(api._pipeline(self.config,'synthetic-task')['state'],'unknown')


if __name__=='__main__':unittest.main(verbosity=2)
