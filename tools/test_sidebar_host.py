#!/usr/bin/env python3
"""Synthetic sidebar-plugin host checks: manifest profile, consent, routes and handed jobs."""
from contextlib import closing, redirect_stderr, redirect_stdout
import copy
import http.client
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import temp_cleanup  # noqa: E402
from jobs import agent_api, db, labels, panel, plugin_host, plugin_pages  # noqa: E402

# Consent fingerprints of the shipped manifests on dev (6b771e5). A change here
# silently asks every user to grant again, so it must be deliberate.
SPONSORSHIP_FINGERPRINT = 'ac036e6e6b8c337dcd2828821e82c0c26c44313643e7e378cd4c550ef64b0e0b'
MAP_FINGERPRINT = '65d50333a09235d241be631b156a0d9a2ae949582078c6b30701a11fff28ab21'
WORKSPACE_FINGERPRINT = 'fcf6a8283d3181ad79c54fb56c00fe2de27f1d7b3c99533c0620c636c6a9eff2'
WORKSPACE = json.loads((ROOT / 'plugins/workspace/manifest.json').read_text())
MAP = json.loads((ROOT / 'plugins/map/manifest.json').read_text())


def map_csp(base):
    # The literal map frame CSP as shipped on dev.
    tiles = 'https://tiles.openfreemap.org'
    return ("default-src 'none'; base-uri 'none'; form-action 'none'; sandbox allow-scripts; "
            f"script-src {base} blob:; style-src {base} 'unsafe-inline'; "
            f"worker-src {base} blob: data:; connect-src {base} {tiles}; "
            f"img-src {base} {tiles} data: blob:; font-src {base} {tiles}; frame-ancestors 'self'")


class ManifestProfile(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(temp_cleanup.cleanup, self.tmp)
        self.root = Path(self.tmp.name)

    def check(self, manifest):
        target = self.root / manifest.get('id', 'x')
        target.mkdir(exist_ok=True)
        (target / 'manifest.json').write_text(json.dumps(manifest))
        return plugin_host._manifest(target, target.name)

    def test_shipped_fingerprints_are_pinned(self):
        for plugin_id, expected in (('sponsorship', SPONSORSHIP_FINGERPRINT), ('map', MAP_FINGERPRINT),
                                    ('workspace', WORKSPACE_FINGERPRINT)):
            manifest = plugin_host._manifest(ROOT / 'plugins' / plugin_id, plugin_id)
            self.assertEqual(plugin_host.fingerprint(manifest), expected, plugin_id)
        workspace = plugin_host._manifest(ROOT / 'plugins/workspace', 'workspace')
        self.assertTrue(plugin_host.handed(workspace))
        self.assertFalse(plugin_host.handed(MAP))

    def test_handed_profile_accepts_only_whitelisted_shapes(self):
        self.check(copy.deepcopy(WORKSPACE))
        no_detail = copy.deepcopy(WORKSPACE); del no_detail['permissions']['job_detail']
        self.check(no_detail)
        no_icons = copy.deepcopy(WORKSPACE); del no_icons['sidebar']['icon']; del no_icons['page']['icon']
        self.check(no_icons)
        switch = copy.deepcopy(WORKSPACE); switch['page']['switch'] = True
        self.check(switch)
        no_switch = copy.deepcopy(WORKSPACE); del no_switch['page']['switch']
        self.check(no_switch)

        def variant(path, value):
            m = copy.deepcopy(WORKSPACE)
            *keys, last = path
            target = m
            for key in keys:
                target = target[key]
            if value is KeyError:
                del target[last]
            else:
                target[last] = value
            return m
        rejected = {
            'sidebar id': (('sidebar', 'id'), 'other'),
            'sidebar entry': (('sidebar', 'entry'), 'web/compare.html'),
            'sidebar label long': (('sidebar', 'label', 'en'), 'x' * 33),
            'sidebar label one language': (('sidebar', 'label'), {'en': 'Workspace'}),
            'sidebar label empty': (('sidebar', 'label', 'zh'), ''),
            'handoff missing': (('sidebar', 'handoff_label'), KeyError),
            'handoff long': (('sidebar', 'handoff_label', 'zh'), '加' * 33),
            'handoff one language': (('sidebar', 'handoff_label'), {'zh': '加入'}),
            'sidebar icon': (('sidebar', 'icon'), 'rocket'),
            'sidebar extra key': (('sidebar', 'url'), 'https://example.invalid'),
            'sidebar missing': (('sidebar',), KeyError),
            'page missing': (('page',), KeyError),
            'page entry': (('page', 'entry'), 'web/index.html'),
            'page id': (('page', 'id'), 'map'),
            'page icon': (('page', 'icon'), 'rocket'),
            'page switch type': (('page', 'switch'), 'no'),
            'page extra key': (('page', 'hidden'), True),
            'top entry': (('entry',), 'web/compare.html'),
            'hooks': (('hooks',), ['after_fetch']),
            'top network': (('network',), 'view_open'),
            'i18n': (('i18n',), ['en']),
            'display flag': (('display',), False),
            'job scope': (('permissions', 'job_scope'), 'all'),
            'job scope missing': (('permissions', 'job_scope'), KeyError),
            'job fields': (('permissions', 'job_fields'), WORKSPACE['permissions']['job_fields'] + ['description']),
            'job detail note': (('permissions', 'job_detail'), WORKSPACE['permissions']['job_detail'] + ['note']),
            'job detail subset': (('permissions', 'job_detail'), ['description']),
            'files': (('permissions', 'files'), ['plugin_reference']),
            'network targets': (('permissions', 'network'), MAP['permissions']['network']),
            'display': (('permissions', 'display'), ['sidebar', 'page']),
            'execution': (('permissions', 'execution'), ['python_worker']),
            'host api 1': (('compatibility',), {'host_api': 1}),
        }
        for name, (path, value) in rejected.items():
            with self.subTest(name=name), self.assertRaises((ValueError, KeyError, TypeError)):
                self.check(variant(path, value))

    def test_map_profile_stays_closed(self):
        self.check(copy.deepcopy(MAP))
        for name, change in {
            'switch on map': lambda m: m['page'].update(switch=False),
            'sidebar on map': lambda m: m.update(sidebar=copy.deepcopy(WORKSPACE['sidebar'])),
            'scope on map': lambda m: m['permissions'].update(job_scope='handed'),
            'detail on map': lambda m: m['permissions'].update(job_detail=['description']),
            'map without network': lambda m: m.update(network='none'),
        }.items():
            m = copy.deepcopy(MAP)
            change(m)
            with self.subTest(name=name), self.assertRaises((ValueError, KeyError, TypeError)):
                self.check(m)

    def test_fingerprint_scope(self):
        base = plugin_host.fingerprint(WORKSPACE)
        cosmetic = copy.deepcopy(WORKSPACE)
        cosmetic['sidebar']['icon'] = 'compare'; cosmetic['page']['icon'] = 'workspace'
        self.assertEqual(plugin_host.fingerprint(cosmetic), base)
        for change in (lambda m: m['page'].update(switch=True),
                       lambda m: m['sidebar']['label'].update(en='Other'),
                       lambda m: m['sidebar']['handoff_label'].update(en='Other'),
                       lambda m: m['permissions'].pop('job_detail'),
                       lambda m: m.update(entry='web/other.html')):
            m = copy.deepcopy(WORKSPACE)
            change(m)
            self.assertNotEqual(plugin_host.fingerprint(m), base)

    def test_consent_text_in_both_languages(self):
        config = {'db_path': str(self.root / 'jobs.db')}
        result = plugin_host.consent(config, WORKSPACE)
        self.assertEqual(result['status'], 'required')
        self.assertEqual([p['id'] for p in result['permissions']],
                         ['identity', 'jobs', 'files', 'network', 'execution', 'display'])
        text = {lang: ' '.join(p['title'][lang] + ' ' + p['description'][lang] for p in result['permissions'])
                + ' ' + result['limits'][lang] for lang in ('zh', 'en')}
        for phrase in ('only reads jobs you put in the sidebar', 'full job description', 'rule results',
                       'label states', 'never reads note text', 'cannot connect to any outside website',
                       'cannot change jobs, labels or notes', 'compare page', '“add” action',
                       'contains only job IDs'):
            self.assertIn(phrase, text['en'])
        for phrase in ('只有你亲手放进侧栏的岗位', '岗位正文', '规则判断结果', '标注状态', '不读取备注内容',
                       '不能连接任何外部网站', '不能修改岗位、标注或备注', '对比页', '“加入”操作', '只包含岗位编号'):
            self.assertIn(phrase, text['zh'])
        self.assertNotIn('tiles.openfreemap.org', json.dumps(result, ensure_ascii=False))
        summary = copy.deepcopy(WORKSPACE); del summary['permissions']['job_detail']
        jobs = plugin_host.consent(config, summary)['permissions'][1]['description']
        self.assertIn('never reads job descriptions', jobs['en'])
        self.assertIn('不读取岗位正文', jobs['zh'])


class SidebarRoutes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.plugin_root = self.root / 'plugins'
        for plugin_id, manifest in (('map', MAP), ('workspace', WORKSPACE)):
            target = self.plugin_root / plugin_id
            (target / 'web').mkdir(parents=True)
            (target / 'manifest.json').write_text(json.dumps(manifest))
        (self.plugin_root / 'map/web/index.html').write_text('<p>synthetic map</p>')
        (self.plugin_root / 'workspace/web/index.html').write_text('<p>synthetic sidebar</p>')
        (self.plugin_root / 'workspace/web/compare.html').write_text('<p>synthetic compare</p>')
        (self.plugin_root / 'workspace/web/app.js').write_text('// synthetic asset')
        self.patcher = patch.object(plugin_host, 'PLUGIN_ROOT', self.plugin_root)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.addCleanup(temp_cleanup.cleanup, self.tmp)
        self.config = {'db_path': str(self.root / 'jobs.db'), 'skills_path': str(self.root / 'skills.yaml'),
                       'queries': ['Synthetic'],
                       'plugins': {'map': {'enabled': False}, 'workspace': {'enabled': False}}}
        (self.root / 'skills.yaml').write_text('version: 1\nskills: []\n')
        with closing(db.connect(self.config['db_path'])) as conn, conn:
            for name, title, group, excluded in [('one', 'Python engineer', 'a', False),
                                                 ('peer', 'Python engineer', 'a', False),
                                                 ('two', 'Rust engineer', 'b', False),
                                                 ('out', 'Citizens only analyst', 'c', True)]:
                uid = 'synthetic:' + name
                db.upsert_job(conn, {'source': 'synthetic', 'source_id': name, 'title': title,
                    'company': 'Synthetic Company', 'location': 'Melbourne VIC 3000', 'salary': '$100k',
                    'description': f'Synthetic description {name}. Applicants must hold full working rights in Australia.',
                    'url': 'https://example.invalid/' + name})
                conn.execute('UPDATE jobs SET dedupe_group=? WHERE uid=?', (group, uid))
                if excluded:
                    db.replace_decision(conn, uid, True, 'synthetic_rule', 'exclude', 'Citizens only')
                else:
                    db.replace_decision(conn, uid, False)
                db.replace_score(conn, uid, 'T1', 3, 0, 0, 20 - len(name), '[]', 20 - len(name))
            db.replace_skills(conn, 'synthetic:one', ['python', 'sql'])
            labels.set_label(conn, 'synthetic:peer', eligibility='eligible', action='saved', priority=1,
                             note='SECRET_SYNTHETIC_NOTE')
            db.log_run(conn, 'analyze', db.utcnow(), {})
        handler = type('SidebarSyntheticHandler', (panel.Handler,), {'config': self.config})
        self.server = panel.ThreadingHTTPServer(('127.0.0.1', 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        self.origin = 'http://127.0.0.1:' + str(self.server.server_port)

    def stop_server(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(3)

    def enable(self, plugin_id='workspace'):
        self.config['plugins'][plugin_id]['enabled'] = True
        manifest = json.loads((self.plugin_root / plugin_id / 'manifest.json').read_text())
        plugin_host.grant(self.config, manifest, plugin_host.fingerprint(manifest), 'test')

    def request(self, path, origin=None):
        client = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=10)
        try:
            client.request('GET', path, headers={'Origin': self.origin if origin is None else origin})
            response = client.getresponse()
            body = response.read()
            json_body = response.getheader('Content-Type', '').startswith('application/json')
            return response.status, (json.loads(body) if json_body else body.decode()), dict(response.getheaders())
        finally:
            client.close()

    def handed(self, *uids, **extra):
        query = urlencode([('uid', u) for u in uids] + list(extra.items()))
        return self.request('/api/plugin-view/workspace/handed?' + query)

    def test_authorization_lists_and_frames(self):
        paths = ['/plugins/workspace/frame', '/plugins/workspace/frame?slot=sidebar',
                 '/plugins/workspace/frame?slot=page', '/plugins/workspace/assets/app.js',
                 '/api/plugin-view/workspace/handed?uid=synthetic:one']
        for path in paths:
            self.assertEqual(self.request(path)[0], 403, path)
        self.assertEqual((plugin_host.page_plugins(self.config), plugin_host.sidebar_plugins(self.config)), ([], []))
        self.config['plugins']['workspace']['enabled'] = True    # enabled, not granted
        for path in paths:
            self.assertEqual(self.request(path)[0], 403, path)
        self.assertEqual((plugin_host.page_plugins(self.config), plugin_host.sidebar_plugins(self.config)), ([], []))
        self.enable()
        for path in paths:
            self.assertEqual(self.request(path)[0], 200, path)
        self.assertEqual(plugin_host.sidebar_plugins(self.config), [{
            'id': 'workspace', 'label': WORKSPACE['sidebar']['label'],
            'handoff_label': WORKSPACE['sidebar']['handoff_label'], 'icon': 'workspace'}])
        self.assertEqual(plugin_host.page_plugins(self.config), [{
            'id': 'workspace', 'label': WORKSPACE['page']['label'], 'icon': 'compare', 'switch': False}])
        self.enable('map')
        self.assertEqual([(p['id'], p['switch']) for p in plugin_host.page_plugins(self.config)],
                         [('map', True), ('workspace', False)])
        self.assertEqual([p['id'] for p in plugin_host.sidebar_plugins(self.config)], ['workspace'])
        self.assertIn('synthetic sidebar', self.request('/plugins/workspace/frame')[1])
        self.assertIn('synthetic sidebar', self.request('/plugins/workspace/frame?slot=sidebar')[1])
        self.assertIn('synthetic compare', self.request('/plugins/workspace/frame?slot=page')[1])
        self.assertIn('synthetic map', self.request('/plugins/map/frame')[1])
        self.assertIn('synthetic map', self.request('/plugins/map/frame?slot=page')[1])
        for path in ('/plugins/map/frame?slot=sidebar', '/plugins/workspace/frame?slot=other',
                     '/plugins/workspace/frame?slot=', '/plugins/workspace/frame?slot=page&slot=sidebar'):
            self.assertEqual(self.request(path)[0], 404, path)
        # The sandboxed frame (opaque origin) may load its assets but not read jobs.
        self.assertEqual(self.request('/plugins/workspace/frame?slot=page', 'null')[0], 200)
        self.assertEqual(self.request('/api/plugin-view/workspace/handed?uid=synthetic:one', 'null')[0], 403)
        self.assertEqual(self.request('/api/plugin-view/workspace/handed?uid=synthetic:one', 'http://example.invalid')[0], 403)
        # Handed plugins never reach list snapshots or reference files; map never reaches handed jobs.
        for path in ('/api/plugin-view/workspace/jobs?view=all',
                     '/api/plugin-view/workspace/member?snapshot_id=x&uid=synthetic:one',
                     '/api/plugin-view/workspace/reference?file=index.json',
                     '/api/plugin-view/map/handed?uid=synthetic:one'):
            status, body, _ = self.request(path)
            self.assertEqual((status, body['error']['code']), (403, 'permission_not_granted'), path)
        plugin_host.revoke(self.config, 'workspace')
        for path in paths:
            self.assertEqual(self.request(path)[0], 403, path)
        self.assertEqual(plugin_host.sidebar_plugins(self.config), [])
        self.enable()
        self.config['demo'] = {}
        for path in paths:
            self.assertEqual(self.request(path)[0], 403, path)
        self.assertEqual((plugin_host.page_plugins(self.config), plugin_host.sidebar_plugins(self.config)), ([], []))

    def test_frame_csp_follows_profile(self):
        self.enable(); self.enable('map')
        base = f'{self.origin}/plugins/map/'
        self.assertEqual(self.request('/plugins/map/frame')[2]['Content-Security-Policy'], map_csp(base))
        self.assertEqual(plugin_pages.frame_csp(MAP, base), map_csp(base))
        for path in ('/plugins/workspace/frame', '/plugins/workspace/frame?slot=page', '/plugins/workspace/assets/app.js'):
            csp = self.request(path)[2]['Content-Security-Policy']
            wbase = f'{self.origin}/plugins/workspace/'
            self.assertEqual(csp, map_csp(wbase).replace(' https://tiles.openfreemap.org', ''))
            self.assertNotIn('https:', csp)
            self.assertIn('sandbox allow-scripts', csp)

    def test_handed_jobs(self):
        self.enable()
        status, result, _ = self.handed('synthetic:two', 'synthetic:gone', 'synthetic:one')
        self.assertEqual(status, 200, result)
        self.assertEqual(set(result), {'plugin_id', 'as_of', 'generation', 'items'})
        self.assertEqual(result['plugin_id'], 'workspace')
        items = result['items']
        self.assertEqual([i['uid'] for i in items], ['synthetic:two', 'synthetic:gone', 'synthetic:one'])
        self.assertEqual(items[1], {'uid': 'synthetic:gone', 'missing': True})
        summary = set(plugin_host.PAGE_JOB_FIELDS) | {'revision', 'human_labels'}
        for item in (items[0], items[2]):
            self.assertEqual(set(item), summary)
            self.assertRegex(item['revision'], r'^[0-9a-f]{16}$')
        self.assertEqual((items[0]['core_order'], items[2]['core_order']), (0, 2))
        self.assertTrue(items[2]['panel_url'].endswith('#synthetic%3Aone'))
        # Labels follow the duplicate group (set on the peer) and never carry the note.
        self.assertEqual(items[2]['human_labels'], {'eligibility': 'eligible', 'action': 'saved', 'priority': True})
        self.assertTrue(items[2]['priority'])
        self.assertEqual(items[0]['human_labels'], {'eligibility': None, 'action': None, 'priority': False})
        self.assertNotIn('Synthetic description', json.dumps(result))
        self.assertNotIn('SECRET_SYNTHETIC', json.dumps(result))
        # Excluded jobs are found like in the agent API; they are not "missing".
        _, excluded, _ = self.handed('synthetic:out')
        self.assertNotIn('missing', excluded['items'][0])

    def test_handed_detail_matches_agent_api(self):
        self.enable()
        status, result, _ = self.handed('synthetic:one', 'synthetic:out', detail='1')
        self.assertEqual(status, 200, result)
        api = agent_api.AgentAPI(self.config)
        for item in result['items']:
            expected = api.detail(item['uid'], {})['item']
            self.assertEqual(item['description'], expected['description'])
            self.assertEqual(item['skills'], expected['skills'])
            self.assertEqual(item['tags'], expected['tags'])
            self.assertEqual(item['qualification'], expected['qualification'])
            self.assertEqual(item['eligibility_evidence'], {
                'rule_id': expected['rule_id'], 'rule_kind': expected['rule_kind'],
                'matched_text': expected['matched_text'], 'snippet': expected['eligibility_snippet'],
                'cue': expected['eligibility_cue']})
        self.assertEqual(result['items'][0]['skills'], ['python', 'sql'])
        self.assertEqual(result['items'][1]['eligibility_evidence']['rule_id'], 'synthetic_rule')
        self.assertNotIn('SECRET_SYNTHETIC', json.dumps(result))
        # Revision follows the JD text; summary and detail agree.
        _, plain, _ = self.handed('synthetic:one')
        self.assertEqual(plain['items'][0]['revision'], result['items'][0]['revision'])
        with closing(db.connect(self.config['db_path'])) as conn, conn:
            conn.execute("UPDATE jobs SET description='Changed synthetic text' WHERE uid='synthetic:one'")
        _, changed, _ = self.handed('synthetic:one')
        self.assertNotEqual(changed['items'][0]['revision'], plain['items'][0]['revision'])

    def test_handed_bounds_and_detail_gating(self):
        self.enable()
        cases = {
            (): 'invalid_uid_count',
            tuple(f'synthetic:{n}' for n in range(11)): 'invalid_uid_count',
            ('synthetic:one', 'synthetic:one'): 'duplicate_uid',
            ('x' * 513,): 'invalid_uid',
            ('',): 'invalid_uid',
        }
        for uids, code in cases.items():
            status, body, _ = self.handed(*uids)
            self.assertEqual((status, body['error']['code']), (400, code), uids[:2])
        self.assertEqual(self.handed(*[f'synthetic:{n}' for n in range(10)])[0], 200)
        self.assertEqual(self.handed('x' * 512)[0], 200)
        for extra in ({'detail': '0'}, {'detail': 'true'}, {'view': 'all'}):
            status, body, _ = self.handed('synthetic:one', **extra)
            self.assertEqual((status, body['error']['code']), (400, 'invalid_query'), extra)
        status, body, _ = self.request('/api/plugin-view/workspace/handed?uid=synthetic:one&detail=1&detail=1')
        self.assertEqual(status, 400)
        with patch.object(plugin_pages, 'MAX_HANDED_BYTES', 200):
            status, body, _ = self.handed('synthetic:one', 'synthetic:two')
            self.assertEqual((status, body['error']['code']), (413, 'response_too_large'))
        # A manifest without job_detail keeps the summary but never serves detail.
        manifest = copy.deepcopy(WORKSPACE); del manifest['permissions']['job_detail']
        (self.plugin_root / 'workspace/manifest.json').write_text(json.dumps(manifest))
        self.assertEqual(self.handed('synthetic:one')[0], 403)          # changed scope needs a new grant
        plugin_host.grant(self.config, manifest, plugin_host.fingerprint(manifest), 'test')
        status, result, _ = self.handed('synthetic:one')
        self.assertEqual(status, 200)
        self.assertNotIn('description', result['items'][0])
        status, body, _ = self.handed('synthetic:one', detail='1')
        self.assertEqual((status, body['error']['code']), (403, 'detail_not_granted'))

    def test_page_render_wiring(self):
        seen = []

        def fake_render(*args, **kwargs):
            seen.append(kwargs)
            return '<html>synthetic</html>'
        with patch.object(panel, 'render_page', fake_render):
            self.assertEqual(self.request('/?display=workspace')[0], 200)
            self.enable()
            self.request('/?display=workspace')
            self.server.mcp_running = True
            self.request('/?display=nope')
        disabled, enabled, mcp = seen
        self.assertEqual((disabled['display'], disabled['display_pages'], disabled['sidebars'], disabled['agent_access']),
                         ('list', [], [], 'off'))
        # display resolves for an authorized page outside the List/Map switch.
        self.assertEqual((enabled['display'], enabled['agent_access']), ('workspace', 'off'))
        self.assertEqual([s['id'] for s in enabled['sidebars']], ['workspace'])
        self.assertEqual((mcp['display'], mcp['agent_access']), ('list', 'on'))

    def test_serve_reports_running_mcp_listener(self):
        observed = []

        class FakeServer:
            def __init__(self, address, handler):
                self.server_address = address
            def serve_forever(self):
                observed.append(getattr(self, 'mcp_running', None))
                raise KeyboardInterrupt
            def server_close(self):
                pass

        class FakeMCP:
            url = 'http://127.0.0.1:1/mcp'
            api = type('A', (), {'api': type('B', (), {'allow_label_writes': False})()})()
            def close(self):
                pass
        config = {**self.config, 'panel': {'host': '127.0.0.1', 'port': 18090}}
        from jobs import config_edit, mcp_http, panel_update
        for service in (FakeMCP(), None):
            with patch.object(panel, 'ThreadingHTTPServer', FakeServer), \
                    patch.object(mcp_http, 'start', lambda c, s, v=service: v), \
                    patch.object(config_edit, 'ConfigEditor', lambda *a, **k: object()), \
                    patch.object(panel_update, 'UpdateManager', lambda *a, **k: object()), \
                    redirect_stdout(io.StringIO()):
                panel.serve(dict(config))
        self.assertEqual(observed, [True, False])

    def test_cli_review_and_grant_workspace(self):
        from jobs import cli
        config_path = self.root / 'config.yaml'
        config_path.write_text('plugins:\n  workspace:\n    enabled: false\n')
        out = io.StringIO()
        with patch.object(cli, 'load_config', return_value=self.config), redirect_stdout(out), redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(['--config', str(config_path), 'plugin', 'review', 'workspace']), 0)
            fp = plugin_host.fingerprint(WORKSPACE)
            self.assertEqual(cli.main(['--config', str(config_path), 'plugin', 'grant', 'workspace', '--fingerprint', fp]), 0)
        self.assertIn('only reads jobs you put in the sidebar', out.getvalue())
        self.assertEqual(plugin_host.consent(self.config, WORKSPACE)['status'], 'granted')


if __name__ == '__main__':
    unittest.main()
