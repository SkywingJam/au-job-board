"""Synthetic HTTP/config integration for isolated optional plugins."""
from __future__ import annotations
import http.client
import json
import re
from contextlib import closing
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from jobs import db, panel, panel_views, plugin_host, config_edit, run_state, i18n, cli, sources, labels


class PluginPanelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.config = {'db_path': str(self.root/'jobs.db'), 'queries': ['Synthetic'],
                       'skills_path': str(self.root/'skills.yaml'),
                       'seek': {'enabled': False},
                       'jobspy': {'indeed': {'enabled': False}, 'linkedin': {'enabled': False}},
                       'plugins': {'sponsorship': {'enabled': False, 'future': 'keep'}},
                       'unrelated': {'preserve': 'yes'}}
        self.path = self.root/'config.yaml'
        self.path.write_text('# keep this comment\n'+yaml.safe_dump(self.config))
        (self.root/'skills.yaml').write_text('version: 1\nskills: []\n')
        with closing(db.connect(self.config['db_path'])) as conn, conn:
            db.upsert_job(conn, {'source': 'synthetic', 'source_id': 'one', 'title': 'Synthetic role',
                'company': 'Imaginary Silver Fern', 'location': 'Hobart TAS', 'description': 'Python development',
                'url': 'https://example.invalid/job/one'})
            db.replace_decision(conn, 'synthetic:one', False)
            db.replace_score(conn, 'synthetic:one', 'T1', 3, 0, 0, 5, '[]', 5)
            db.log_run(conn, 'analyze', db.utcnow(), {})
            conn.commit()
        handler = type('SyntheticPluginHandler', (panel.Handler,), {'config': self.config})
        self.server = panel.ThreadingHTTPServer(('127.0.0.1', 0), handler)
        self.server.config_editor = config_edit.ConfigEditor(self.config, self.path)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.origin = 'http://127.0.0.1:'+str(self.server.server_port)
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(3); self.tmp.cleanup()

    def request(self, path, body=None, headers=None):
        client = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        try:
            base = {'Origin': self.origin}
            if body is not None:
                base['Content-Type'] = 'application/json'
            base.update(headers or {})
            client.request('GET' if body is None else 'POST', path,
                           None if body is None else json.dumps(body), base)
            response = client.getresponse()
            raw = response.read().decode()
            if response.getheader('Content-Type', '').startswith('application/json'):
                raw = json.loads(raw)
            return response.status, raw
        finally:
            client.close()

    def toggle(self, enabled=True, digest=None):
        if digest is None:
            _, view = self.request('/api/plugins'); digest = view['digest']
        return self.request('/api/plugins', {'digest': digest, 'id': 'sponsorship', 'enabled': enabled})

    def test_navigation_and_debug_only_settings_in_both_languages(self):
        for lang, title in [('zh', '插件'), ('en', 'Plugins')]:
            headers = {'Cookie': 'jobs_lang='+lang}
            status, page = self.request('/settings?section=plugins', headers=headers)
            self.assertEqual(status, 200)
            self.assertIn(title, page)
            self.assertEqual(i18n.js_strings(lang)["plugin.no_errors"], i18n.t(lang, "plugin.no_errors"))
            self.assertIn('role="switch"', page)
            self.assertIn('aria-checked="false"', page)
            self.assertIn('aria-labelledby="pl-name-sponsorship"', page)
            self.assertNotIn('class="plugin-menu"', page)
            self.assertIn('plugin-toggle-track', page)
            self.assertIn('plugin=sponsorship', page)
            status, detail = self.request('/settings?section=plugins&plugin=sponsorship', headers=headers)
            self.assertEqual(status, 200)
            self.assertIn('data-plugin-debug="sponsorship"', detail)
            self.assertNotIn('data-plugin-toggle=', detail)
            self.assertNotIn('refresh_days', detail)
            self.assertIn('id="plugin-debug-status"', detail)
            log_tag = re.search(r'<pre[^>]*id="plugin-debug-output"[^>]*>', detail).group(0)
            self.assertNotIn('role="status"', log_tag)
        status, data = self.request('/api/plugins/debug?id=sponsorship')
        self.assertEqual((status, data['entries']), (200, []))

    def test_toggle_roundtrip_preserves_unknown_config_and_detects_conflicts(self):
        _, before = self.request('/api/plugins')
        status, data = self.toggle(True, before['digest'])
        self.assertEqual(status, 200)
        self.assertTrue(data['plugins'][0]['enabled'])
        saved = yaml.safe_load(self.path.read_text())
        self.assertEqual(saved['unrelated'], self.config['unrelated'])
        self.assertEqual(saved['plugins']['sponsorship']['future'], 'keep')
        self.assertIn('# keep this comment', self.path.read_text())
        self.assertEqual(self.toggle(False, before['digest'])[0], 409)
        self.assertEqual(self.toggle(False)[0], 200)
        self.assertFalse(yaml.safe_load(self.path.read_text())['plugins']['sponsorship']['enabled'])

    def test_http_same_origin_validation_and_running_lock(self):
        self.assertEqual(self.request('/api/plugins', headers={'Origin': 'http://example.invalid'})[0], 403)
        self.assertEqual(self.request('/api/plugins', headers={'Host': 'localhost:invalid'})[0], 403)
        for plugin_id in ['../escape', 'missing', 'sponsorship.enabled']:
            status, _ = self.request('/api/plugins/debug?id='+plugin_id)
            self.assertEqual(status, 404)
        _, view = self.request('/api/plugins')
        for body in [{'digest': view['digest'], 'id': 'sponsorship', 'enabled': 'yes'},
                     {'digest': view['digest'], 'id': 'sponsorship', 'enabled': True, 'command': 'bad'}]:
            self.assertEqual(self.request('/api/plugins', body)[0], 400)
        lock = run_state.acquire_lock(self.config)
        try:
            self.assertEqual(self.toggle()[0], 409)
        finally:
            lock.close()

    def test_debug_is_bounded_and_escaped_not_shared_with_other_plugins(self):
        plugin_host._log(self.config, 'sponsorship', 'after_fetch', 'RefreshError')
        plugin_host._log(self.config, 'other', 'test', 'OtherError')
        _, debug = self.request('/api/plugins/debug?id=sponsorship')
        self.assertEqual(len(debug['entries']), 1)
        self.assertNotIn('OtherError', json.dumps(debug))
        self.assertNotIn('preserve', json.dumps(debug))

    def test_plugin_output_escaped_and_disabled_immediately_without_restarting(self):
        self.toggle(True)
        view = {'badges': [{'text': '<b>Test</b>', 'level': 'info', 'tooltip': '<script>bad</script>'}],
                'sections': [{'title': '<b>Facts</b>', 'status': 'Reference', 'items': ['<script>bad</script>']}]}
        with closing(sqlite3.connect(self.config['db_path'])) as conn, conn:
            generation = conn.execute("SELECT MAX(run_id) FROM runs WHERE kind='analyze'").fetchone()[0]
        run_state.atomic_json(plugin_host._root(self.config)/'sponsorship'/'results.json',
                              {'analysis_id': generation, 'results': {'synthetic:one': {'zh': view, 'en': view}}})
        _, page = self.request('/?view=all')
        self.assertIn('&lt;b&gt;Test&lt;/b&gt;', page)
        _, detail = self.request('/api/job?uid=synthetic:one')
        self.assertIn('&lt;script&gt;bad&lt;/script&gt;', detail['html'])
        self.assertNotIn('<script>bad</script>', detail['html'])
        self.assertIn('<details class="plugin-facts"><summary>', detail['html'])
        self.assertNotIn('<details class="plugin-facts" open', detail['html'])
        self.assertIn('plugin-facts-summary', detail['html'])
        self.assertNotIn('plugin-summary-full', detail['html'])
        self.assertEqual(detail['html'].count('Reference'), 1)
        self.toggle(False)
        self.assertNotIn('&lt;b&gt;Test&lt;/b&gt;', self.request('/?view=all')[1])
        self.assertTrue((plugin_host._root(self.config)/'sponsorship'/'results.json').exists())

    def test_corrupt_config_and_result_never_break_panel(self):
        self.path.write_text('plugins: [broken]\n')
        self.assertEqual(self.request('/?view=all')[0], 200)
        self.assertEqual(self.request('/settings?section=plugins')[0], 200)
        self.assertEqual(self.request('/api/plugins')[0], 200)
        self.path.write_text('plugins: [not valid yaml')
        self.assertEqual(self.request('/?view=all')[0], 200)
        self.assertEqual(self.request('/api/plugins')[0], 400)

    def test_demo_does_not_access_installed_plugins_or_private_logs(self):
        self.server.RequestHandlerClass.config = {**self.config, 'demo': {}}
        with patch.object(plugin_host, '_root', side_effect=AssertionError('private access')):
            self.assertEqual(self.request('/settings?section=plugins')[0], 200)
            self.assertEqual(self.request('/api/plugins/debug?id=sponsorship')[0], 503)
            self.assertEqual(self.request('/api/job?uid=synthetic:one')[0], 200)

    def test_cli_hooks_with_plugin_failure_preserve_core_and_manual_results(self):
        # Real fetch/analyze entrypoints, mock recruitment source, no real network.
        with closing(db.connect(self.config['db_path'])) as conn, conn:
            labels.set_label(conn, 'synthetic:one', note='Keep this synthetic note', source='test')
            before_manual = {table: [tuple(r) for r in conn.execute('SELECT * FROM '+table)]
                             for table in ('labels', 'label_events')}
        args = ['--config', str(self.path), '--rules', str(ROOT/'rules.yaml'),
                '--skills', str(self.root/'skills.yaml')]
        self.assertEqual(cli.main(args+['analyze']), 0)
        with closing(db.connect(self.config['db_path'])) as conn, conn:
            before_scores = [tuple(r)[:-1] for r in conn.execute('SELECT * FROM scores ORDER BY uid')]
            before_decisions = [tuple(r)[:-1] for r in conn.execute('SELECT * FROM decisions ORDER BY uid')]
        self.toggle(True)
        job = sources.blank_job('synthetic', 'one')
        job.update(title='Synthetic role', company='Imaginary Silver Fern', location='Hobart TAS',
                   description='Python development', url='https://example.invalid/job/one')
        with patch.object(sources, 'iter_sources', return_value=iter([('synthetic', lambda config, queries: [job])])), \
             patch.object(plugin_host, '_run_worker', return_value={'errors': []}) as worker:
            # Keep the mock in this process; launcher/Job coverage lives in test_windows_runtime.
            self.assertEqual(cli.main(args+['fetch', '--windows-worker']), 0)
            self.assertEqual(worker.call_args.args[2], 'after_fetch')
        with patch.object(plugin_host, '_run_worker', side_effect=ValueError('simulated corrupt plugin')):
            self.assertEqual(cli.main(args+['analyze']), 0)
        config = self.server.config_editor.plugin_config()
        self.assertEqual(plugin_host.read_debug(config, 'sponsorship')['entries'][-1]['error'], 'ValueError')
        # A normal subsequent run succeeds and produces current-generation facts.
        self.assertEqual(cli.main(args+['analyze']), 0)
        self.assertTrue(plugin_host.presentation(config, 'synthetic:one')['sections'])
        with closing(db.connect(self.config['db_path'])) as conn, conn:
            self.assertEqual([tuple(r)[:-1] for r in conn.execute('SELECT * FROM scores ORDER BY uid')], before_scores)
            self.assertEqual([tuple(r)[:-1] for r in conn.execute('SELECT * FROM decisions ORDER BY uid')], before_decisions)
            for table, before in before_manual.items():
                self.assertEqual([tuple(r) for r in conn.execute('SELECT * FROM '+table)], before)

    def test_optional_input_failure_cannot_fail_committed_core_analysis(self):
        from jobs import RunTimeout
        args = ['--config', str(self.path), '--rules', str(ROOT/'rules.yaml'),
                '--skills', str(self.root/'skills.yaml'), 'analyze']
        with patch.object(plugin_host, '_analysis_jobs', side_effect=AssertionError('must not prepare disabled plugins')) as prepare:
            self.assertEqual(cli.main(args), 0)
            prepare.assert_not_called()
        self.toggle(True)
        for error in (sqlite3.OperationalError('simulated read failure'), MemoryError()):
            with patch.object(plugin_host, '_analysis_jobs', side_effect=error):
                self.assertEqual(cli.main(args), 0)
            config = self.server.config_editor.plugin_config()
            self.assertEqual(plugin_host.read_debug(config, 'sponsorship')['entries'][-1]['error'], type(error).__name__)
            with closing(db.connect(self.config['db_path'])) as conn, conn:
                self.assertTrue(conn.execute('SELECT COUNT(*) FROM scores').fetchone()[0])
        with patch.object(plugin_host, '_analysis_jobs', side_effect=RunTimeout()):
            with self.assertRaises(RunTimeout):
                cli.main(args)

    def test_only_highest_priority_plugin_badge_is_on_card(self):
        row = {'uid':'synthetic:badge','title':'Synthetic role','company':'Imaginary Fern',
               '_plugin_view':{'badges':[
                   {'text':'Accredited match','level':'info','tooltip':'Evidence one'},
                   {'text':'Sanctions match','level':'warn','tooltip':'Evidence two'}]}}
        card = panel_views._card(row)
        self.assertEqual(card.count('plugin-badge'), 1)
        self.assertIn('Sanctions match', card)
        self.assertNotIn('Accredited match', card)

    def test_plugin_description_is_plain_optional_metadata(self):
        _, data = self.request('/api/plugins')
        self.assertIn('public lists',data['plugins'][0]['description']['en'])
        _, page = self.request('/settings?section=plugins')
        self.assertIn('id="pl-desc-sponsorship"',page)

    def test_unavailable_switch_can_be_found_and_diagnostics_remain_reachable(self):
        item = {'id':'sponsorship','name':{'zh':'雇主信息','en':'Employer facts'},
                'version':'1','description':{'en':'Public references'},'enabled':False,
                'status':'error','error_count':3}
        with patch.object(plugin_host, 'list_plugins', return_value=[item]):
            status, page = self.request('/settings?section=plugins',headers={'Cookie':'jobs_lang=en'})
        self.assertEqual(status,200)
        self.assertIn('role="switch" aria-checked="false"',page)
        self.assertIn('aria-disabled="true"',page)
        self.assertIn('3 error records',page)
        self.assertIn('Plugin files, version or configuration',page)
        self.assertIn('plugin=sponsorship',page)


if __name__ == '__main__':
    unittest.main()
