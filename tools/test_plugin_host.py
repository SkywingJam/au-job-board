#!/usr/bin/env python3
"""Offline synthetic coverage for plugin isolation and result generations."""
import json
import os
from contextlib import closing
from pathlib import Path
import sqlite3
import sys
import tempfile
import zipfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from jobs import RunTimeout, plugin_host as host


class PluginTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.config = {'db_path': str(self.root / 'test.db'), 'plugins': {'sample': {'enabled': True}}}
        with closing(sqlite3.connect(self.config['db_path'])) as conn, conn:
            conn.execute('CREATE TABLE runs (run_id INTEGER PRIMARY KEY, kind TEXT)')
            conn.execute("INSERT INTO runs VALUES (1, 'analyze')")
        self.plugin_root = self.root / 'plugins'
        self.path = self.plugin_root / 'sample'
        self.path.mkdir(parents=True)
        self.manifest = {'id': 'sample', 'name': {'en': 'Sample'}, 'version': '1', 'compatibility': {'host_api': 1}, 'entry': 'plugin.py', 'hooks': ['after_analyze_success'], 'network': 'none', 'display': True, 'i18n': ['en', 'zh']}
        self.write_manifest()
        self.root_patch = patch.object(host, 'PLUGIN_ROOT', self.plugin_root)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)
        self.addCleanup(self.tmp.cleanup)
        self.jobs = [{'uid': 'synthetic:1', 'company': 'Imaginary Gumtree Pty'}]

    def write_manifest(self):
        (self.path / 'manifest.json').write_text(json.dumps(self.manifest))

    def code(self, source):
        (self.path / 'plugin.py').write_text(source)

    def dispatch(self):
        host.dispatch(self.config, 'after_analyze_success', self.jobs, 1)

    def good(self):
        self.code("def after_analyze_success(r):\n v={'badges':[{'text':'ok','level':'info','tooltip':'source'}],'sections':[]}\n return {'analysis_id':r['analysis_id'],'results':{j['uid']:{'zh':v,'en':v} for j in r['jobs']}}\n")

    def test_success_stale_disabled_and_uninstalled_retains_results(self):
        self.good()
        self.dispatch()
        self.assertEqual(host.presentation(self.config, 'synthetic:1')['badges'][0]['text'], 'ok')
        result_file = host._root(self.config) / 'sample' / 'results.json'
        self.config['plugins']['sample']['enabled'] = False
        self.assertFalse(host.presentation(self.config, 'synthetic:1')['badges'])
        self.assertTrue(result_file.exists())
        self.config['plugins']['sample']['enabled'] = True
        with closing(sqlite3.connect(self.config['db_path'])) as conn, conn:
            conn.execute("INSERT INTO runs VALUES (2, 'analyze')")
        self.assertFalse(host.presentation(self.config, 'synthetic:1')['badges'])
        self.dispatch()
        self.assertEqual(json.loads(result_file.read_text())['analysis_id'], 1)
        self.path.rename(self.root / 'removed')
        self.assertTrue(result_file.exists())
        self.assertEqual(host.presentation(self.config, 'synthetic:1'), {'badges': [], 'sections': []})

    def test_worker_failures_are_isolated(self):
        cases = ['syntax invalid !!!', 'import missing_fake_module_743', 'raise RuntimeError("SECRET")', 'raise SystemExit(7)', 'import os; os._exit(9)', 'import time; time.sleep(5)']
        with patch.object(host, 'TIMEOUT_ANALYZE', 0.2):
            for code in cases:
                self.code(code)
                self.dispatch()
        entries = host.read_debug(self.config, 'sample')['entries']
        self.assertEqual(len(entries), len(cases))
        self.assertNotIn('SECRET', json.dumps(entries))
        self.assertFalse(host.presentation(self.config, 'synthetic:1')['badges'])

    def test_invalid_and_oversized_results(self):
        for body in ["return 'invalid'", "return {'analysis_id': r['analysis_id'], 'results': {'synthetic:1': 'x' * 9000000}}"]:
            self.code('def after_analyze_success(r):\n ' + body + '\n')
            self.dispatch()
        self.assertEqual(len(host.read_debug(self.config, 'sample')['entries']), 2)
        result_file = host._root(self.config) / 'sample' / 'results.json'
        result_file.write_bytes(b'x' * (host.MAX_RESULT + 1))
        self.assertEqual(host.presentation(self.config, 'synthetic:1'), {'badges': [], 'sections': []})
        result_file.write_text('{bad')
        self.assertEqual(host.presentation(self.config, 'synthetic:1'), {'badges': [], 'sections': []})

    def test_manifest_config_and_storage_failure(self):
        self.manifest['compatibility'] = {'host_api': 2}
        self.write_manifest()
        self.assertEqual(host.list_plugins(self.config)[0]['status'], 'error')
        (self.path / 'manifest.json').write_text('{broken')
        self.assertEqual(host.list_plugins(self.config)[0]['status'], 'error')
        self.manifest['compatibility'] = {'host_api': 1}
        self.write_manifest()
        self.config['plugins']['sample'] = {'enabled': 'yes'}
        self.assertEqual(host.list_plugins(self.config)[0]['status'], 'error')
        self.config['plugins']['sample'] = {'enabled': True}
        self.code('raise RuntimeError()')
        with patch.object(host, 'atomic_json', side_effect=OSError('unwritable')):
            self.dispatch()
        with patch.object(Path, 'mkdir', side_effect=OSError('unwritable')):
            self.dispatch()

    def test_network_is_unavailable_to_analysis_hook(self):
        self.code("import socket\ndef after_analyze_success(r):\n socket.create_connection(('example.invalid', 80))\n")
        self.dispatch()
        self.assertEqual(len(host.read_debug(self.config, 'sample')['entries']), 1)

    def test_bundled_employer_facts(self):
        self.root_patch.stop()
        self.addCleanup(self.root_patch.start)
        self.config['plugins'] = {'sponsorship': {'enabled': True}}
        data = host._root(self.config) / 'sponsorship' / 'data'
        data.mkdir(parents=True)
        (data / 'accredited-2025-01-15.txt').write_text('Imaginary Gumtree Pty Ltd\n')
        (data / 'sanctioned.json').write_text(json.dumps({'d': {'success': True, 'data': [{'sponsorname': 'Imaginary Gumtree Pty Ltd', 'sanctionimposeddatetext': '2025-01-01'}]}}))
        (data / 'asx-listed.csv').write_text('Company name,Code\nImaginary Gumtree Ltd,XYZ\n')
        with zipfile.ZipFile(data / 'ato-tax-2024-25.xlsx', 'w') as archive:
            archive.writestr('xl/worksheets/sheet1.xml', '<worksheet><row><c><v>Name</v></c><c><v>Total income</v></c></row><row><c><v>Imaginary Gumtree Ltd</v></c><c><v>100000001</v></c></row></worksheet>')
        jobs = self.jobs + [{'uid': 'synthetic:2', 'company': 'Imaginary Talent Recruitment'}]
        host.dispatch(self.config, 'after_analyze_success', jobs, 1)
        view = host.presentation(self.config, 'synthetic:1', 'en')
        self.assertEqual(len(view['badges']), 2)
        self.assertIn('ABF', str(view))
        self.assertIn('ASX', str(view))
        self.assertIn('ATO', str(view))
        self.assertIn('2024-25', str(view))
        self.assertIn('Home Affairs', str(view))
        self.assertIn('unknown', str(host.presentation(self.config, 'synthetic:2', 'en')).lower())
        self.assertEqual(host.read_debug(self.config, 'sponsorship')['entries'], [])

    def test_parent_timeout_and_interrupt_propagate(self):
        self.good()
        for error in (RunTimeout(), KeyboardInterrupt()):
            with patch.object(host, '_run_worker', side_effect=error):
                with self.assertRaises(type(error)):
                    self.dispatch()

    def test_external_execution_udp_and_lookup_blocked_for_offline_hook(self):
        cases = ["import subprocess; subprocess.run(['curl', 'https://example.invalid'])",
                 "import socket; socket.socket(socket.AF_INET,socket.SOCK_DGRAM).sendto(b'x',('127.0.0.1',9))",
                 "import socket; socket.gethostbyname('example.invalid')"]
        for source in cases:
            self.code(source)
            self.dispatch()
        entries = host.read_debug(self.config, 'sample')['entries']
        self.assertEqual(len(entries), len(cases))
        self.assertTrue(all(e['error'] == 'PermissionError' for e in entries))

    def test_deep_json_and_fifo_do_not_block_main_readers(self):
        (self.path/'manifest.json').write_text('['*2000+'0'+']'*2000)
        self.assertEqual(host.list_plugins(self.config)[0]['status'], 'error')
        self.write_manifest(); self.good(); self.dispatch()
        target = host._root(self.config)/'sample'/'results.json'
        target.write_text('['*2000+'0'+']'*2000)
        self.assertEqual(host.presentation(self.config, 'synthetic:1'), {'badges': [], 'sections': []})
        if hasattr(os, 'mkfifo'):
            target.unlink(); os.mkfifo(target)
            self.assertEqual(host.presentation(self.config, 'synthetic:1'), {'badges': [], 'sections': []})

    def test_failed_atomic_publication_preserves_previous_results(self):
        self.good(); self.dispatch()
        target = host._root(self.config)/'sample'/'results.json'
        original = target.read_bytes()
        real = host.atomic_json
        def fail_results(path, value):
            if Path(path).name == 'results.json':
                raise OSError('simulated full disk')
            return real(path, value)
        with patch.object(host, 'atomic_json', side_effect=fail_results):
            self.dispatch()
        self.assertEqual(target.read_bytes(), original)
        self.assertEqual(host.read_debug(self.config, 'sample')['entries'][-1]['error'], 'OSError')

    @unittest.skipUnless(sys.platform == 'darwin', 'macOS RSS monitor')
    def test_macos_memory_budget_contains_worker(self):
        self.code("chunks=[]\nwhile True: chunks.append(bytearray(16*1024*1024))")
        with patch.object(host, 'TIMEOUT_ANALYZE', 3):
            self.dispatch()
        self.assertEqual(host.read_debug(self.config, 'sample')['entries'][-1]['error'], 'MemoryLimit')

    def test_reference_matching_and_sparse_spreadsheet(self):
        from plugins.sponsorship import reference as ref
        self.assertEqual(ref.NameIndex(['Imaginary Gumtree Services Pty Ltd']).match('Imaginary Gumtree')[0], 'fuzzy')
        self.assertEqual(ref.NameIndex(['Imaginary Gumtree Australia Pty Ltd']).match('Imaginary Gumtree')[0], 'fuzzy')
        self.assertEqual(ref.NameIndex(['Imaginary Gumtree Pty Ltd']).match('Imaginary Gumtree')[0], 'exact')
        data = self.root/'references'; data.mkdir()
        with zipfile.ZipFile(data/'ato-tax-2024-25.xlsx', 'w') as archive:
            archive.writestr('xl/worksheets/sheet1.xml', '<worksheet><row><c r="A1"><v>Name</v></c><c r="C1"><v>Total income</v></c></row><row><c r="A2" t="inlineStr"><is><t>Imaginary &amp; Fern</t></is></c><c r="C2"><v>12345</v></c></row></worksheet>')
        values, year = ref.load_ato(data)
        self.assertEqual(values['Imaginary & Fern'], 12345)
        self.assertEqual(year, '2024-25')

    def test_parent_timeout_during_discovery_read_and_logging_propagates(self):
        with patch.object(host, '_read', side_effect=RunTimeout()):
            with self.assertRaises(RunTimeout):
                host.read_debug(self.config, 'sample')
        with patch.object(host, '_manifest', side_effect=RunTimeout()):
            with self.assertRaises(RunTimeout):
                host.list_plugins(self.config)
        with patch.object(host, 'atomic_json', side_effect=RunTimeout()):
            with self.assertRaises(RunTimeout):
                host._log(self.config, 'sample', 'test', 'Failure')

    def test_invalid_reference_refresh_preserves_good_cache(self):
        import io
        from plugins.sponsorship import reference as ref
        target = self.root/'sanctioned.json'
        original = json.dumps({'d': {'success': True, 'data': [{'sponsorname': 'Imaginary Silver Fern Pty Ltd'}]}}).encode()
        target.write_bytes(original)
        for bad in [{'d': {'success': True}}, {'d': {'success': True, 'data': []}},
                    {'d': {'success': True, 'data': ['bad']}},
                    {'d': {'success': True, 'data': [{'sponsorname': None}]}}]:
            with patch.object(ref.urllib.request, 'urlopen', return_value=io.BytesIO(json.dumps(bad).encode())):
                with self.assertRaises(ValueError):
                    ref._download('https://example.invalid/reference', target, ref._check_sanctions)
            self.assertEqual(target.read_bytes(), original)
        target.write_text('{"d":{"success":true}}')
        with self.assertRaises(ValueError):
            ref.load_sanctions(self.root)

    def test_stale_and_corrupt_same_period_ato_are_downloaded_again(self):
        from plugins.sponsorship import plugin, reference as ref
        data = self.root/'refresh'; data.mkdir()
        target = data/'ato-tax-2024-25.xlsx'; target.write_text('damaged')
        with patch.object(ref, '_stale', return_value=True), \
             patch.object(ref, '_latest_ato_url', return_value=('2024-25','https://example.invalid/report.xlsx')), \
             patch.object(ref, '_download') as download:
            plugin.after_fetch({'data_dir':str(data)})
            self.assertTrue(any(call.args[1] == target for call in download.call_args_list))
        with patch.object(ref, '_stale', return_value=False), \
             patch.object(ref, '_latest_ato_url', return_value=('2024-25','https://example.invalid/report.xlsx')), \
             patch.object(ref, '_download') as download:
            plugin.after_fetch({'data_dir':str(data)})
            self.assertTrue(any(call.args[1] == target for call in download.call_args_list))


if __name__ == '__main__':
    unittest.main()
