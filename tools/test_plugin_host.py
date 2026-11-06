#!/usr/bin/env python3
"""Offline synthetic coverage for plugin isolation and result generations."""
import json
import os
from contextlib import closing
from pathlib import Path
import sqlite3
import shutil
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
        self.manifest = {'id': 'sample', 'name': {'en': 'Sample'}, 'version': '1', 'compatibility': {'host_api': 1}, 'entry': 'plugin.py', 'hooks': ['after_analyze_success'], 'network': 'none', 'display': True, 'i18n': ['en', 'zh'], 'permissions': {'job_fields': ['uid', 'company'], 'files': ['plugin_data'], 'network': [], 'execution': ['python_worker'], 'display': ['job_badges', 'job_details']}}
        self.write_manifest()
        self.root_patch = patch.object(host, 'PLUGIN_ROOT', self.plugin_root)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)
        host.grant(self.config, self.manifest, host.fingerprint(self.manifest), 'test')
        self.addCleanup(self.tmp.cleanup)
        self.jobs = [{'uid': 'synthetic:1', 'company': 'Imaginary Gumtree Pty'}]

    def use_sponsorship(self):
        # Keep public test inputs synthetic: copy code, but no bundled public data.
        source = Path(__file__).resolve().parent.parent / 'plugins' / 'sponsorship'
        destination = self.plugin_root / 'sponsorship'
        shutil.copytree(source, destination, ignore=shutil.ignore_patterns('baseline', '__pycache__'))
        manifest = host._manifest(destination, 'sponsorship')
        host.grant(self.config, manifest, host.fingerprint(manifest), 'test')

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

    def test_result_budget_and_storage_use_the_workers_utf8_encoding(self):
        self.code("def after_analyze_success(r):\n v={'badges':[],'sections':[{'title':'Facts','status':'Ready','items':['中文'*100]}]}\n return {'analysis_id':r['analysis_id'],'results':{j['uid']:{'zh':v,'en':v} for j in r['jobs']}}\n")
        with patch.object(host, 'MAX_RESULT', 2400):
            self.dispatch()
            target = host._root(self.config) / 'sample' / 'results.json'
            self.assertTrue(target.exists(), host.read_debug(self.config, 'sample'))
            self.assertLessEqual(target.stat().st_size, host.MAX_RESULT)
            self.assertIn('中文', target.read_text(encoding='utf-8'))
            self.assertEqual(host.presentation(self.config, 'synthetic:1')['sections'][0]['items'], ['中文'*100])

    def test_shared_views_are_validated_and_resolved(self):
        self.jobs = [{'uid': 'synthetic:1', 'company': 'A'}, {'uid': 'synthetic:2', 'company': 'B'},
                     {'uid': 'synthetic:3', 'company': 'C'}]
        result_file = host._root(self.config) / 'sample' / 'results.json'
        # Shared key for two jobs, an inline view for the third.
        self.code("def after_analyze_success(r):\n s={'badges':[{'text':'shared','level':'info','tooltip':'t'}],'sections':[]}\n"
                  " own={'badges':[{'text':'own','level':'info','tooltip':'t'}],'sections':[]}\n"
                  " return {'analysis_id':r['analysis_id'],'views':{'v0':{'zh':s,'en':s}},"
                  "'results':{'synthetic:1':'v0','synthetic:2':'v0','synthetic:3':{'zh':own,'en':own}}}\n")
        self.dispatch()
        views = host.presentation_many(self.config, ['synthetic:1', 'synthetic:2', 'synthetic:3'], 'en')
        self.assertEqual([views[u]['badges'][0]['text'] for u in ('synthetic:1', 'synthetic:2', 'synthetic:3')],
                         ['shared', 'shared', 'own'])
        good = result_file.read_bytes()
        bad_bodies = [
            # Unknown key, invalid shared view, non-string key, extra top-level field.
            "{'analysis_id':r['analysis_id'],'views':{},'results':{j['uid']:'missing' for j in r['jobs']}}",
            "{'analysis_id':r['analysis_id'],'views':{'v0':{'zh':{},'en':{}}},'results':{j['uid']:'v0' for j in r['jobs']}}",
            "{'analysis_id':r['analysis_id'],'views':[],'results':{}}",
            "{'analysis_id':r['analysis_id'],'views':{},'results':{},'extra':1}"]
        for body in bad_bodies:
            self.code('def after_analyze_success(r):\n return ' + body + '\n')
            self.dispatch()
        self.assertEqual(len(host.read_debug(self.config, 'sample')['entries']), len(bad_bodies))
        self.assertEqual(result_file.read_bytes(), good)

    def test_sponsorship_results_share_repeated_employer_views(self):
        self.use_sponsorship()
        self.config['plugins'] = {'sponsorship': {'enabled': True}}
        # Thousands of jobs from a few employers stay far below the result budget.
        jobs = [{'uid': f'synthetic:{i}', 'company': f'Imaginary Employer {i % 40} Pty Ltd'} for i in range(8000)]
        host.dispatch(self.config, 'after_analyze_success', jobs, 1)
        self.assertEqual(host.read_debug(self.config, 'sponsorship')['entries'], [])
        stored = json.loads((host._root(self.config) / 'sponsorship' / 'results.json').read_text(encoding='utf-8'))
        self.assertLessEqual(len(stored['views']), 40)
        self.assertLess(len(json.dumps(stored).encode()), host.MAX_RESULT // 8)
        self.assertEqual(host.presentation(self.config, 'synthetic:7999', 'en'),
                         host.presentation(self.config, 'synthetic:39', 'en'))
        self.assertTrue(host.presentation(self.config, 'synthetic:7999', 'en')['sections'])

    @unittest.skipIf(os.name == 'nt', 'Windows workers are contained by a kill-on-close job')
    def test_worker_exits_when_its_host_is_killed(self):
        import signal, subprocess, time
        data = self.root / 'data'
        self.code("import os, time\nfrom pathlib import Path\n"
                  "def after_analyze_success(r):\n p=Path(r['data_dir']); p.mkdir(exist_ok=True)\n"
                  " (p/'pid').write_text(str(os.getpid()))\n time.sleep(60)\n")
        script = ("import sys; from pathlib import Path; sys.path.insert(0, sys.argv[1])\n"
                  "from jobs import plugin_host as h\n"
                  "h.PLUGIN_ROOT = Path(sys.argv[2])\n"
                  "h._run_worker({}, 'sample', 'after_analyze_success', "
                  "{'data_dir': sys.argv[3], 'jobs': [], 'analysis_id': 1})\n")
        host_process = subprocess.Popen([sys.executable, '-c', script, str(Path(__file__).resolve().parent.parent),
                                         str(self.plugin_root), str(data)])
        try:
            deadline = time.monotonic() + 20
            while not (data / 'pid').exists():
                self.assertLess(time.monotonic(), deadline, 'worker did not start')
                self.assertIsNone(host_process.poll())
                time.sleep(0.05)
            worker = int((data / 'pid').read_text())
            host_process.send_signal(signal.SIGKILL)
            host_process.wait()
            deadline = time.monotonic() + 5
            def gone():
                # Without an init process (e.g. `docker run` with no --init) the
                # orphan is reparented to this test process and stays a zombie.
                try:
                    if os.waitpid(worker, os.WNOHANG)[0] == worker:
                        return True
                except ChildProcessError:
                    pass
                try:
                    os.kill(worker, 0)
                except ProcessLookupError:
                    return True
                try:
                    return Path(f'/proc/{worker}/stat').read_text().rsplit(')', 1)[1].split()[0] == 'Z'
                except OSError:
                    return False
            while True:
                if gone():
                    break
                if time.monotonic() > deadline:
                    os.kill(worker, signal.SIGKILL)
                    self.fail('plugin worker outlived its killed host')
                time.sleep(0.05)
        finally:
            if host_process.poll() is None:
                host_process.kill(); host_process.wait()

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
        self.use_sponsorship()
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

    def test_accredited_departments_are_not_treated_as_page_headers(self):
        self.use_sponsorship()
        self.config['plugins'] = {'sponsorship': {'enabled': True}}
        data = host._root(self.config) / 'sponsorship' / 'data'
        data.mkdir(parents=True)
        employers = ['Department of Imaginary Gumtrees',
                     'Imaginary Department of Fanciful Rivers']
        (data / 'accredited-2025-01-15.txt').write_text(
            'Australian Government\nDepartment of Home Affairs\n'
            'Released by Department of Home Affairs\n001\n'
            + '\n'.join(employers) + '\n', encoding='utf-8')
        jobs = [{'uid': f'synthetic:department:{i}', 'company': name}
                for i, name in enumerate(employers)]
        host.dispatch(self.config, 'after_analyze_success', jobs, 1)
        for job in jobs:
            view = host.presentation(self.config, job['uid'], 'en')
            self.assertEqual([badge['text'] for badge in view['badges']],
                             ['Accredited list match'])
        self.assertEqual(host.read_debug(self.config, 'sponsorship')['entries'], [])

    def test_corrupt_cache_falls_back_to_synthetic_baseline(self):
        self.use_sponsorship()
        self.config['plugins'] = {'sponsorship': {'enabled': True}}
        baseline = self.plugin_root / 'sponsorship' / 'baseline'
        baseline.mkdir()
        (baseline / 'accredited-2025-01-15.txt').write_text('Imaginary Gumtree Pty Ltd\n')
        (baseline / 'sanctioned.json').write_text(json.dumps({'d': {'success': True, 'data': [
            {'sponsorname': 'Synthetic Other Employer Pty Ltd'}]}}))
        data = host._root(self.config) / 'sponsorship' / 'data'
        data.mkdir(parents=True)
        (data / 'sanctioned.json').write_text('{damaged')
        manual = data / 'accredited-2025-01-15.txt'
        for damaged in (b'\xff', b''):
            with self.subTest(manual_bytes=damaged):
                manual.write_bytes(damaged)
                host.dispatch(self.config, 'after_analyze_success', self.jobs, 1)
                view = host.presentation(self.config, 'synthetic:1', 'en')
                self.assertIn('bundled baseline', str(view))
                self.assertIn('Accredited list match', str(view))
                self.assertEqual(manual.read_bytes(), damaged)
        self.assertEqual((data / 'sanctioned.json').read_text(), '{damaged')
        self.assertEqual(host.read_debug(self.config, 'sponsorship')['entries'], [])

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
        def fail_results(path, value, **kwargs):
            if Path(path).name == 'results.json':
                raise OSError('simulated full disk')
            return real(path, value, **kwargs)
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
        import io
        from jobs import plugin_downloads as downloads
        data = self.root/'refresh'; data.mkdir()
        target = data/'ato-tax-2024-25.xlsx'; target.write_text('damaged')
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as archive:
            archive.writestr('xl/worksheets/sheet1.xml', '<worksheet><row><c><v>Name</v></c><c><v>Total income</v></c></row><row><c><v>Imaginary Tree</v></c><c><v>123</v></c></row></worksheet>')
        url = 'https://data.gov.au/data/dataset/c2524c87-cea4-4636-acac-599a82048a26/resource/1234/download/test.xlsx'
        package = json.dumps({'result': {'resources': [{'name':'2024-25 Report of Entity Tax Information', 'url':url}]}}).encode()
        def fake_read(kind, url):
            if kind == 'ato': return package
            if kind == 'ato_file': return buffer.getvalue()
            raise OSError('offline')
        with patch.object(downloads, '_read', side_effect=fake_read):
            downloads.refresh(data)
        self.assertEqual(target.read_bytes(), buffer.getvalue())

    def test_fact_summaries_distinguish_missing_no_match_and_name_uncertainty(self):
        from plugins.sponsorship import plugin
        cases = [
            ('exact', None, True, True, '认证担保雇主 ✓'),
            ('fuzzy', None, True, True, '可能是认证担保雇主'),
            (None, None, True, True, '未见违规记录'),
            ('exact', 'confirmed', True, True, '⚠ 有违规担保记录'),
            ('exact', 'possible', True, True, '⚠ 可能有违规记录'),
            (None, None, False, False, '认证担保名单、违规名单暂不可用'),
            (None, None, False, True, '认证担保名单暂不可用'),
            (None, None, True, False, '违规名单暂不可用'),
            ('exact', None, True, False, '违规名单暂不可用')]
        for acc, sanction, a, b, expected in cases:
            summary=plugin._summary('zh',acc,sanction,a,b)
            self.assertTrue(summary.startswith(expected),summary)
            self.assertNotIn('可担保', summary)
            self.assertNotIn('不可担保', summary)
        self.assertIn('Possible',plugin._summary('en','fuzzy',None,True,True))
        self.assertIn('unavailable',plugin._summary('en',None,None,False,False))

    def test_employer_copy_explains_findings_and_groups_sources_in_both_languages(self):
        self.use_sponsorship()
        self.config['plugins'] = {'sponsorship': {'enabled': True}}
        data = host._root(self.config) / 'sponsorship' / 'data'
        data.mkdir(parents=True)
        (data / 'accredited-1999-01-15.txt').write_text(
            'Imaginary Gumtree Pty Ltd\nImaginary Cedar Pty Ltd\nMoonlit Walnut Systems Pty Ltd\n')
        (data / 'sanctioned.json').write_text(json.dumps({'d': {'success': True, 'data': [
            {'sponsorname': 'Imaginary Gumtree Pty Ltd', 'sanctionimposeddatetext': '2025-01-01'},
            {'sponsorname': 'Invented Lantern Goods Pty Ltd', 'tradingname': 'Fanciful Willow',
             'sanctionimposeddatetext': '2025-02-02'}]}}))
        (data / 'asx-listed.csv').write_text('Company name,Code\nImaginary Cedar Pty Ltd,XYZ\n')
        with zipfile.ZipFile(data / 'ato-tax-2024-25.xlsx', 'w') as archive:
            archive.writestr('xl/worksheets/sheet1.xml', '<worksheet><row><c><v>Name</v></c><c><v>Total income</v></c></row><row><c><v>Imaginary Cedar Pty Ltd</v></c><c><v>100000001</v></c></row></worksheet>')
        names = ['Imaginary Gumtree Pty Ltd', 'Imaginary Cedar Pty Ltd', 'Moonlit Walnut',
                 'Fanciful Quartz', 'Fanciful Willow', 'Imaginary Talent Recruitment']
        jobs = [{'uid': f'synthetic:copy:{i}', 'company': name} for i, name in enumerate(names)]
        host.dispatch(self.config, 'after_analyze_success', jobs, 1)
        for lang in ('zh', 'en'):
            views = [host.presentation(self.config, job['uid'], lang) for job in jobs]
            for view in views[:-1]:
                section = view['sections'][0]
                source = section['items'][-1]
                self.assertTrue(source.startswith('来源：' if lang == 'zh' else 'Sources: '))
                self.assertIn('1999-01', source)
                self.assertIn('较旧' if lang == 'zh' else 'older source', source)
                self.assertNotIn('.txt', str(view))
                self.assertNotIn('未匹配' if lang == 'zh' else 'no match', str(view))
                self.assertEqual(sum(item.startswith('来源：' if lang == 'zh' else 'Sources: ')
                                     for item in section['items']), 1)
                self.assertTrue(all('\n' in item for item in section['items'][:-2]))
            self.assertIn('2025-01-01', views[0]['sections'][0]['items'][0])
            self.assertTrue(views[0]['sections'][0]['items'][0].startswith('⚠'))
            self.assertTrue(views[1]['sections'][0]['items'][0].startswith('✓'))
            self.assertIn('ASX', views[1]['sections'][0]['items'][-1])
            self.assertIn('ATO', views[1]['sections'][0]['items'][-1])
            self.assertIn('2024-25', views[1]['sections'][0]['items'][-1])
            self.assertIn('公司全称' if lang == 'zh' else 'full company name',
                          views[2]['sections'][0]['items'][0])
            self.assertEqual(views[3]['sections'][0]['status'],
                             '未见违规记录' if lang == 'zh' else 'No sponsorship breach found')
            self.assertIn('普通担保雇主' if lang == 'zh' else 'Standard business sponsors',
                          views[3]['sections'][0]['items'][1])
            self.assertIn('2025-02-02', views[4]['sections'][0]['items'][0])
            self.assertEqual(views[5]['badges'], [])
            self.assertIn('实际雇主' if lang == 'zh' else 'actual employer',
                          views[5]['sections'][0]['items'][0])
        self.assertEqual(host.read_debug(self.config, 'sponsorship')['entries'], [])

    def test_missing_reference_copy_does_not_claim_a_clean_record(self):
        self.use_sponsorship()
        self.config['plugins'] = {'sponsorship': {'enabled': True}}
        host.dispatch(self.config, 'after_analyze_success', self.jobs, 1)
        for lang in ('zh', 'en'):
            section = host.presentation(self.config, 'synthetic:1', lang)['sections'][0]
            self.assertIn('暂不可用' if lang == 'zh' else 'unavailable', section['status'])
            self.assertNotIn('未见违规' if lang == 'zh' else 'No sponsorship breach found', str(section))
            self.assertNotIn('未匹配' if lang == 'zh' else 'no match', str(section))

    def test_reference_age_is_marked_after_one_year_without_exposing_filenames(self):
        from datetime import datetime, timezone
        from plugins.sponsorship import plugin
        with patch.object(plugin, 'datetime') as clock:
            clock.strptime.side_effect = datetime.strptime
            clock.now.return_value = datetime(2026, 11, 6, tzinfo=timezone.utc)
            self.assertEqual(plugin._source_date('2025-11-06', 'zh'), '2025-11-06')
            self.assertEqual(plugin._source_date('2025-11-05', 'zh', month=True), '2025-11，较旧')
            self.assertEqual(plugin._source_date('2025-11-05', 'en', month=True), '2025-11, older source')
            self.assertEqual(plugin._source_date('not-a-date', 'zh'), '日期未知')


if __name__ == '__main__':
    unittest.main()
