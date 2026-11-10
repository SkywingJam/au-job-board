"""Synthetic map page HTTP, snapshot, and reference regressions."""
from contextlib import closing
import gzip
import hashlib
import http.client
import json
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import urlencode, parse_qs

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import temp_cleanup  # noqa: E402
from jobs import db, labels, panel, panel_data, plugin_host, plugin_pages, skills


class MapPages(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.plugin_root = self.root / 'plugins'
        target = self.plugin_root / 'map'
        (target / 'web').mkdir(parents=True)
        (target / 'reference').mkdir()
        shutil.copyfile(ROOT / 'plugins/map/manifest.json', target / 'manifest.json')
        (target / 'web/index.html').write_text('<p>synthetic map</p>')
        (target / 'web/app.js').write_text('// synthetic asset')
        # An import of installed plugin Python would leave a visible marker.
        (target / '__init__.py').write_text(f"open({str(self.root / 'imported')!r}, 'w').write('bad')")
        raw = gzip.compress(json.dumps({'type': 'FeatureCollection', 'features': []}).encode(), mtime=0)
        (target / 'reference/poa-1.geojson.gz').write_bytes(raw)
        (target / 'reference/index.json').write_text(json.dumps({'reference_version': 'synthetic-v1', 'files': [
            {'path': 'poa-1.geojson.gz', 'sha256': hashlib.sha256(raw).hexdigest()}], 'postcodes': {}}))
        self.patcher = patch.object(plugin_host, 'PLUGIN_ROOT', self.plugin_root)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.addCleanup(temp_cleanup.cleanup, self.tmp)
        self.config = {'db_path': str(self.root / 'jobs.db'), 'skills_path': str(self.root / 'skills.yaml'),
                       'queries': ['Synthetic'], 'plugins': {'map': {'enabled': False}}}
        (self.root / 'skills.yaml').write_text('version: 1\nskills: []\n')
        with closing(db.connect(self.config['db_path'])) as conn, conn:
            for name, title, group, excluded in [('one', 'Python engineer', 'a', False), ('peer', 'Python engineer', 'a', False), ('two', 'Rust engineer', 'b', False), ('out', 'Python excluded', 'c', True), ('outpeer', 'Python excluded', 'c', True)]:
                uid = 'synthetic:' + name
                db.upsert_job(conn, {'source': 'synthetic', 'source_id': name, 'title': title,
                    'company': 'Synthetic Company', 'location': 'Melbourne VIC 3000',
                    'description': 'SECRET_SYNTHETIC_JD', 'url': 'https://example.invalid/' + name})
                conn.execute('UPDATE jobs SET dedupe_group=? WHERE uid=?', (group, uid))
                db.replace_decision(conn, uid, excluded)
                db.replace_score(conn, uid, 'T1', 3, 0, 0, 20 - len(name), '[]', 20 - len(name))
            labels.set_label(conn, 'synthetic:one', note='SECRET_SYNTHETIC_NOTE')
            labels.set_label(conn, 'synthetic:one', note='SECRET_SYNTHETIC_HISTORY')
            db.log_run(conn, 'analyze', db.utcnow(), {})
        handler = type('MapSyntheticHandler', (panel.Handler,), {'config': self.config})
        self.server = panel.ThreadingHTTPServer(('127.0.0.1', 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        self.origin = 'http://127.0.0.1:' + str(self.server.server_port)

    def stop_server(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(3)

    def enable(self):
        self.config['plugins']['map']['enabled'] = True
        manifest = json.loads((self.plugin_root / 'map/manifest.json').read_text())
        plugin_host.grant(self.config, manifest, plugin_host.fingerprint(manifest), 'test')

    def request(self, path, origin=None):
        client = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        try:
            client.request('GET', path, headers={'Origin': self.origin if origin is None else origin})
            response = client.getresponse()
            body = response.read()
            return response.status, (json.loads(body) if response.getheader('Content-Type', '').startswith('application/json') else body.decode()), dict(response.getheaders())
        finally:
            client.close()

    def test_authorization_and_origin(self):
        paths = ['/plugins/map/frame', '/plugins/map/assets/app.js', '/api/plugin-view/map/jobs?view=all']
        for path in paths:
            self.assertEqual(self.request(path)[0], 403)
        self.config['plugins']['map']['enabled'] = True
        for path in paths:
            self.assertEqual(self.request(path)[0], 403)
        self.enable()
        for path in paths:
            self.assertEqual(self.request(path)[0], 200, path)
        for path in paths[:2]:
            status, _, headers = self.request(path, 'null')
            self.assertEqual(status, 200)
            self.assertEqual(headers.get('Access-Control-Allow-Origin'), '*')
            self.assertEqual(self.request(path, 'http://example.invalid')[0], 403)
        self.assertEqual(self.request(paths[2], 'null')[0], 403)
        self.assertEqual(self.request(paths[2], 'http://example.invalid')[0], 403)
        self.config['demo'] = {}
        for path in paths:
            self.assertEqual(self.request(path)[0], 403)
        self.assertFalse((self.root / 'imported').exists())

    def test_snapshot_query_parity_and_privacy(self):
        self.enable()
        for query in ['view=all', 'view=all&q=Python', 'view=excluded', 'view=labelled', 'view=recommend']:
            with self.subTest(query=query):
                status, result, _ = self.request('/api/plugin-view/map/jobs?' + query)
                self.assertEqual(status, 200, result)
                view, filters = plugin_pages.normalize(parse_qs(query))
                with closing(db.connect(self.config['db_path'])) as conn:
                    since = panel_data._resolve_since(conn, view, filters)
                    rows, total = panel_data._query(conn, self.config, view, 0, 20001, filters, since, skills.load_skills(self.config['skills_path']), snippets=False)
                self.assertEqual([x['uid'] for x in result['items']], [x['uid'] for x in rows])
                self.assertEqual(result['total'], total)
                if query == 'view=all':
                    self.assertEqual(total, 3)  # two duplicate groups and one distinct role
                if query == 'view=all&q=Python':
                    self.assertEqual(total, 2)
                    self.assertTrue(all('Python' in item['title'] for item in result['items']))
                if query == 'view=excluded':
                    self.assertEqual({item['uid'] for item in result['items']}, {'synthetic:out', 'synthetic:outpeer'})
                self.assertEqual([x['core_order'] for x in result['items']], list(range(total)))
                self.assertEqual(result['count_unit'], 'source_records' if view == 'excluded' else 'jobs')
                self.assertNotIn('SECRET_SYNTHETIC_', json.dumps(result))
                for item in result['items']:
                    self.assertEqual(set(item), {'uid','group_id','title','company','location_raw','location_fact','salary','work_type','listing_date','total_score','priority','core_order','panel_url'})
        self.assertFalse((self.root / 'imported').exists())

    def test_membership_expiry_and_capacity(self):
        self.enable()
        _, first, _ = self.request('/api/plugin-view/map/jobs?view=all&q=Rust')
        _, second, _ = self.request('/api/plugin-view/map/jobs?view=all&q=Python')
        self.assertTrue(first['items']); self.assertTrue(second['items'])
        member = lambda sid, uid: self.request('/api/plugin-view/map/member?' + urlencode({'snapshot_id': sid, 'uid': uid}))
        self.assertEqual(member(first['snapshot_id'], first['items'][0]['uid'])[0], 200)
        self.assertEqual(member(first['snapshot_id'], second['items'][0]['uid'])[0], 404)
        self.assertEqual(member('unknown', first['items'][0]['uid'])[0], 410)
        store = self.server.page_snapshots.store
        # One second past the limit: (created + ttl) - created can round just below ttl
        # when Windows' coarse monotonic clock gives both snapshots the same timestamp.
        store.clock = lambda: max(item.created for item in store.items.values()) + store.ttl + 1
        self.assertEqual(member(first['snapshot_id'], first['items'][0]['uid'])[0], 410)
        store.max_snapshots = len(store.items)
        self.assertEqual(self.request('/api/plugin-view/map/jobs?view=all')[0], 429)
        store.max_snapshots += 1
        store.max_snapshot_bytes = 1
        self.assertEqual(self.request('/api/plugin-view/map/jobs?view=all')[0], 413)

    def test_snapshot_reuse_eviction_and_csp_host(self):
        self.enable()
        # Re-entering the map / toggling List-Map reuses the live snapshot.
        _, a, _ = self.request('/api/plugin-view/map/jobs?view=all')
        _, b, _ = self.request('/api/plugin-view/map/jobs?view=all&display=map')
        self.assertEqual(a['snapshot_id'], b['snapshot_id'])
        store = self.server.page_snapshots.store
        # More distinct queries than slots: oldest is evicted, nothing fails with 429.
        for n in range(store.max_snapshots + 3):
            status, result, _ = self.request('/api/plugin-view/map/jobs?' + urlencode({'view': 'all', 'q': f'x{n}'}))
            self.assertEqual(status, 200, result)
        self.assertLessEqual(len(store.items), store.max_snapshots)
        # The frame CSP is scoped to this plugin and never echoes a crafted Host header.
        status, _, headers = self.request('/plugins/map/frame')
        csp = headers['Content-Security-Policy']
        self.assertIn(f"{self.origin}/plugins/map/", csp)
        self.assertIn('sandbox allow-scripts', csp)
        self.assertNotIn(f"{self.origin} ", csp)
        client = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        try:
            client.putrequest('GET', '/plugins/map/frame', skip_host=True)
            client.putheader('Host', f'127.0.0.1:{self.server.server_port}/;script-src *')
            client.endheaders()
            response = client.getresponse(); response.read()
            self.assertEqual(response.status, 403)
        finally:
            client.close()

    def test_reuse_follows_labels_and_expiry(self):
        self.enable()
        url = '/api/plugin-view/map/jobs?view=all&labeled=no'
        _, first, _ = self.request(url)
        store = self.server.page_snapshots.store
        self.assertIn('synthetic:two', [x['uid'] for x in first['items']])
        _, again, _ = self.request(url + '&display=map')
        self.assertEqual(first['snapshot_id'], again['snapshot_id'])
        # A label written in the meantime must not be hidden by the reused snapshot.
        with closing(db.connect(self.config['db_path'])) as conn, conn:
            labels.set_label(conn, 'synthetic:two', action='applied')
        _, after, _ = self.request(url)
        self.assertNotEqual(first['snapshot_id'], after['snapshot_id'])
        self.assertNotIn('synthetic:two', [x['uid'] for x in after['items']])
        # A snapshot close to its expiry is never handed out again.
        _, same, _ = self.request(url)
        self.assertEqual(after['snapshot_id'], same['snapshot_id'])
        created = store.items[after['snapshot_id']].created
        store.clock = lambda: created + store.ttl - plugin_pages.MIN_REUSE_TTL + 1
        _, fresh, _ = self.request(url)
        self.assertNotEqual(after['snapshot_id'], fresh['snapshot_id'])

    def test_reference_and_asset_boundaries(self):
        self.enable()
        base = '/api/plugin-view/map/reference?'
        self.assertEqual(self.request(base + urlencode({'file': 'index.json'}))[0], 200)
        self.assertEqual(self.request(base + urlencode({'file': 'poa-1.geojson.gz'}))[0], 200)
        for name in ['../index.json', 'poa-2.geojson.gz', '/etc/passwd', 'poa-1.geojson.gz/../../x']:
            self.assertEqual(self.request(base + urlencode({'file': name}))[0], 400)
        self.assertEqual(self.request('/plugins/map/assets/../manifest.json')[0], 404)
        (self.plugin_root / 'map/reference/poa-1.geojson.gz').write_bytes(b'bad')
        status, body, _ = self.request(base + urlencode({'file': 'poa-1.geojson.gz'}))
        self.assertEqual((status, body['error']['code']), (503, 'reference_integrity'))


if __name__ == '__main__':
    unittest.main()
