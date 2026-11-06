#!/usr/bin/env python3
"""Synthetic offline consent, worker and download boundary checks."""
import json
import io
from contextlib import redirect_stdout, redirect_stderr
import os
from pathlib import Path
import sqlite3
import sys
import subprocess
import time
import threading
import zipfile
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from jobs import plugin_host as host, plugin_downloads as downloads
from jobs.config_edit import ConfigEditor, ConfigEditError


class ConsentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.plugins = self.base / 'plugins'
        self.plugin = self.plugins / 'sample'
        self.plugin.mkdir(parents=True)
        self.manifest = {'id':'sample', 'name':{'en':'Sample'}, 'description':{'en':'Synthetic'},
            'version':'1', 'compatibility':{'host_api':1}, 'entry':'plugin.py',
            'hooks':['after_analyze_success'], 'network':'none', 'display':True, 'i18n':['en'],
            'permissions':{'job_fields':['uid','company'], 'files':['plugin_data'], 'network':[],
                'execution':['python_worker'], 'display':['job_badges','job_details']}}
        self.write_manifest()
        self.config_path = self.base / 'config.yaml'
        self.config_path.write_text('plugins:\n  sample:\n    enabled: true\n')
        self.config = {'db_path':str(self.base/'test.db'), 'plugins':{'sample':{'enabled':True}}}
        with sqlite3.connect(self.config['db_path']) as conn:
            conn.execute('create table runs (run_id integer, kind text)')
            conn.execute("insert into runs values (1, 'analyze')")
        self.patcher = patch.object(host, 'PLUGIN_ROOT', self.plugins)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.editor = ConfigEditor(self.config, self.config_path)

    def write_manifest(self):
        (self.plugin/'manifest.json').write_text(json.dumps(self.manifest))

    def save(self, **extra):
        state = self.editor.read_plugins()
        return self.editor.save_plugin({'digest':state['digest'], 'id':'sample', 'enabled':True, **extra})

    def test_consent_gate_change_revoke_and_version(self):
        marker = self.base/'imported'
        (self.plugin/'plugin.py').write_text('open('+repr(str(marker))+',"w").write("bad")')
        with patch.object(host, '_run_worker') as worker:
            host.dispatch(self.config, 'after_analyze_success', [{'uid':'1','company':'Imaginary'}], 1)
            worker.assert_not_called()
        self.assertFalse(marker.exists())
        with self.assertRaises(ConfigEditError) as e:
            self.save()
        self.assertEqual(e.exception.code, 'plugin_consent_required')
        fp = self.editor.read_plugins()['plugins'][0]['consent']['fingerprint']
        self.save(consent_fingerprint=fp)
        self.assertEqual(host.list_plugins(self.config)[0]['consent']['status'], 'granted')
        self.manifest['version'] = '2'
        self.manifest['description'] = {'en': 'Updated wording, same permissions'}
        self.manifest['name'] = {'en': 'New display name'}
        self.write_manifest()
        self.assertEqual(host.list_plugins(self.config)[0]['consent']['status'], 'granted')
        self.manifest['hooks'] = ['after_fetch', 'after_analyze_success']
        self.write_manifest()
        self.assertEqual(host.list_plugins(self.config)[0]['consent']['status'], 'changed')
        with self.assertRaises(ConfigEditError):
            self.save(consent_fingerprint=fp)
        state = self.editor.read_plugins()
        self.editor.save_plugin({'digest':state['digest'], 'id':'sample', 'enabled':False, 'action':'revoke'})
        self.assertEqual(host.list_plugins(self.config)[0]['consent']['status'], 'required')

    def test_failed_config_save_does_not_publish_a_grant_and_retry_succeeds(self):
        self.config_path.write_text('plugins:\n  sample:\n    enabled: false\n')
        fingerprint = host.fingerprint(self.manifest)
        with patch.object(self.editor, '_replace', side_effect=ConfigEditError('write_failed')):
            with self.assertRaises(ConfigEditError):
                self.save(consent_fingerprint=fingerprint)
        self.assertEqual(host.consent(self.config, self.manifest)['status'], 'required')
        self.assertIn('enabled: false', self.config_path.read_text())
        self.save(consent_fingerprint=fingerprint)
        self.assertEqual(host.consent(self.config, self.manifest)['status'], 'granted')

    def test_grant_storage_failure_and_revoke_save_failure_remain_inert(self):
        self.config_path.write_text('plugins:\n  sample:\n    enabled: false\n')
        fingerprint = host.fingerprint(self.manifest)
        with patch.object(host, 'grant', side_effect=OSError('simulated storage failure')):
            with self.assertRaises(ConfigEditError):
                self.save(consent_fingerprint=fingerprint)
        with patch.object(host, '_run_worker') as worker:
            host.dispatch(self.editor.plugin_config(), 'after_analyze_success', [], 1)
            worker.assert_not_called()

        self.save(consent_fingerprint=fingerprint)
        state = self.editor.read_plugins()
        with patch.object(self.editor, '_replace', side_effect=ConfigEditError('write_failed')):
            with self.assertRaises(ConfigEditError):
                self.editor.save_plugin({'digest': state['digest'], 'id': 'sample', 'action': 'revoke'})
        with patch.object(host, '_run_worker') as worker:
            host.dispatch(self.editor.plugin_config(), 'after_analyze_success', [], 1)
            worker.assert_not_called()

    def test_concurrent_revoke_is_refused_until_the_grant_finishes(self):
        entered, release = threading.Event(), threading.Event()
        real_grant, failures = host.grant, []
        fingerprint = host.fingerprint(self.manifest)
        def paused(*args, **kwargs):
            entered.set()
            if not release.wait(3):
                raise AssertionError('Grant test was not released')
            return real_grant(*args, **kwargs)
        def save():
            try:
                self.save(consent_fingerprint=fingerprint)
            except BaseException as exc:
                failures.append(exc)
        with patch.object(host, 'grant', side_effect=paused):
            thread = threading.Thread(target=save)
            thread.start()
            try:
                self.assertTrue(entered.wait(3))
                state = self.editor.read_plugins()
                with self.assertRaises(ConfigEditError) as error:
                    self.editor.save_plugin({'digest': state['digest'], 'id': 'sample', 'action': 'revoke'})
                self.assertEqual(error.exception.code, 'run_busy')
            finally:
                release.set(); thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(failures, [])
        self.assertEqual(host.consent(self.config, self.manifest)['status'], 'granted')
        state = self.editor.read_plugins()
        self.editor.save_plugin({'digest': state['digest'], 'id': 'sample', 'action': 'revoke'})
        with patch.object(host, '_run_worker') as worker:
            host.dispatch(self.editor.plugin_config(), 'after_analyze_success', [], 1)
            worker.assert_not_called()

    @unittest.skipIf(os.name == 'nt', 'Native process injection is POSIX-only; Windows uses the shared Job Object supervisor.')
    def test_stalled_host_download_is_terminated_by_wall_clock_budget(self):
        real_popen = subprocess.Popen
        def stalled(command, **kwargs):
            if command[0] == '/bin/ps':
                return real_popen(command, **kwargs)
            self.assertEqual(command[2], 'jobs.plugin_downloads')
            return real_popen([sys.executable, '-c', 'import time; time.sleep(30)'], **kwargs)
        started = time.monotonic()
        with patch.object(host, 'TIMEOUT_FETCH', .15), patch.object(host.subprocess, 'Popen', side_effect=stalled):
            with self.assertRaises(subprocess.TimeoutExpired):
                host._refresh_references(self.config, 'sample', self.base / 'data')
        self.assertLess(time.monotonic() - started, 3)

    @unittest.skipIf(os.name == 'nt', 'The subprocess audit bootstrap is POSIX-only.')
    def test_trusted_download_process_uses_valid_synthetic_cache_offline(self):
        data = self.base / 'data'; data.mkdir()
        (data / 'sanctioned.json').write_text(json.dumps({'d': {'success': True, 'data': [
            {'sponsorname': 'Imaginary Tree Pty Ltd'}]}}))
        (data / 'asx-listed.csv').write_text('Company name,Code\nImaginary Tree Pty Ltd,XYZ\n')
        with zipfile.ZipFile(data / 'ato-tax-2024-25.xlsx', 'w') as archive:
            archive.writestr('xl/worksheets/sheet1.xml', '<worksheet><row><c><v>Name</v></c><c><v>Total income</v></c></row><row><c><v>Imaginary Tree Pty Ltd</v></c><c><v>100000001</v></c></row></worksheet>')
        real_popen = subprocess.Popen
        def offline(command, **kwargs):
            if command[0] == '/bin/ps':
                return real_popen(command, **kwargs)
            self.assertEqual(command[2], 'jobs.plugin_downloads')
            bootstrap = ('import sys,runpy; from jobs.plugin_worker import _offline; '
                         'sys.addaudithook(_offline); '
                         "sys.argv=['jobs.plugin_downloads',*sys.argv[1:]]; "
                         "runpy.run_module('jobs.plugin_downloads',run_name='__main__')")
            return real_popen([sys.executable, '-c', bootstrap, *command[3:]], **kwargs)
        with patch.object(host.subprocess, 'Popen', side_effect=offline):
            self.assertEqual(host._refresh_references(self.config, 'sample', data), {'errors': []})

    def test_cli_requires_reviewed_fingerprint_for_noninteractive_grant(self):
        from jobs import cli
        with patch.object(cli, 'load_config', return_value=self.config), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(['--config', str(self.config_path), 'plugin', 'grant', 'sample']), 1)
            self.assertEqual(cli.main(['--config', str(self.config_path), 'plugin', 'grant', 'sample', '--fingerprint', 'incorrect']), 1)
            fp = host.fingerprint(self.manifest)
            self.assertEqual(cli.main(['--config', str(self.config_path), 'plugin', 'grant', 'sample', '--fingerprint', fp]), 0)
            self.assertEqual(host.list_plugins(self.config)[0]['consent']['status'], 'granted')
            self.assertEqual(cli.main(['--config', str(self.config_path), 'plugin', 'revoke', 'sample']), 0)
            self.assertEqual(host.list_plugins(self.config)[0]['consent']['status'], 'required')

    def test_worker_denies_direct_io_for_both_hooks(self):
        outside = self.base / 'outside.txt'
        outside.write_text('keep')
        for hook in ('after_fetch', 'after_analyze_success'):
            for source in (f'open({str(outside)!r}).read()',
                           f'open({str(outside)!r},"w").write("changed")',
                           f'__import__("os").remove({str(outside)!r})',
                           f'__import__("os").truncate({str(outside)!r},0)',
                           "__import__('socket').getaddrinfo('example.invalid',80)",
                           "__import__('subprocess').run(['true'])"):
                (self.plugin/'plugin.py').write_text(source)
                with self.assertRaises(host.WorkerError) as e:
                    host._run_worker(self.config, 'sample', hook, {'data_dir':str(self.base/'data')})
                self.assertEqual(e.exception.code, 'PermissionError')
                self.assertEqual(outside.read_text(), 'keep')

    def test_worker_can_use_own_data_but_cannot_inherit_credentials(self):
        (self.plugin/'plugin.py').write_text(
            'import os,tempfile\nfrom pathlib import Path\n'
            'def after_fetch(r):\n'
            ' p=Path(r["data_dir"]);p.mkdir(exist_ok=True);(p/"own.txt").write_text("ok")\n'
            ' with tempfile.NamedTemporaryFile() as f:\n'
            '  scoped_temp=Path(f.name).parent==Path(os.environ["TMPDIR"])\n'
            ' return {"own_data":(p/"own.txt").read_text(),"credential":os.environ.get("SYNTHETIC_CREDENTIAL"),"scoped_temp":scoped_temp}\n')
        with patch.dict(os.environ, {'SYNTHETIC_CREDENTIAL': 'never pass this'}):
            value = host._run_worker(self.config, 'sample', 'after_fetch', {'data_dir':str(self.base/'data')})
        self.assertEqual(value, {'own_data': 'ok', 'credential': None, 'scoped_temp': True})

    def test_dispatch_passes_only_the_declared_job_fields(self):
        host.grant(self.config, self.manifest, host.fingerprint(self.manifest), 'cli')
        job = {'uid':'synthetic:private', 'company':'Imaginary Tree',
               'title':'Synthetic private title', 'description':'Synthetic private body'}
        view = {'badges': [], 'sections': []}
        with patch.object(host, '_run_worker', return_value={
                'analysis_id':1, 'results':{job['uid']:{'zh':view, 'en':view}}}) as worker:
            host.dispatch(self.config, 'after_analyze_success', [job], 1)
        request = worker.call_args.args[3]
        self.assertEqual(request['jobs'], [{'uid':job['uid'], 'company':job['company']}])
        self.assertEqual(request['config'], {})
        self.assertIs(request['allow_network'], False)

    def test_download_rejects_unapproved_targets(self):
        self.assertFalse(downloads._allowed('https://example.invalid/file.xlsx', 'ato_file'))
        self.assertFalse(downloads._allowed('https://data.gov.au.evil.invalid/file.xlsx', 'ato_file'))
        self.assertFalse(downloads._allowed('http://www.asx.com.au/asx/research/ASXListedCompanies.csv', 'asx'))
        with self.assertRaises(ValueError):
            downloads._read('asx', 'https://example.invalid/')

    def test_host_request_body_size_and_redirect_rules(self):
        class Response(io.BytesIO):
            pass
        class Opener:
            def __init__(self, body):
                self.body = body
                self.request = None
            def open(self, request, timeout):
                self.request = request
                return Response(self.body)
        opener = Opener(b'public')
        with patch.object(downloads.urllib.request, 'build_opener', return_value=opener):
            self.assertEqual(downloads._read('abf', downloads.ABF), b'public')
        self.assertEqual(opener.request.data, b'{}')
        self.assertEqual(opener.request.get_method(), 'POST')
        with patch.object(downloads, 'LIMIT', 3), patch.object(downloads.urllib.request, 'build_opener', return_value=Opener(b'1234')):
            with self.assertRaises(ValueError):
                downloads._read('asx', downloads.ASX)
        with self.assertRaises(ValueError):
            downloads._Redirect('asx').redirect_request(None, None, 302, '', {}, 'https://evil.invalid/')

    def test_invalid_download_does_not_replace_valid_cache_and_timeout_propagates(self):
        data = self.base / 'data'
        data.mkdir()
        target = data / 'sanctioned.json'
        good = json.dumps({'d': {'success': True, 'data': [{'sponsorname': 'Synthetic Tree Pty'}]}}).encode()
        target.write_bytes(good)
        os.utime(target, (0, 0))
        with patch.object(downloads, '_read', return_value=b'invalid'):
            self.assertIn('abf', downloads.refresh(data))
        self.assertEqual(target.read_bytes(), good)
        from jobs import RunTimeout
        with patch.object(downloads, '_read', side_effect=RunTimeout()):
            with self.assertRaises(RunTimeout):
                downloads.refresh(data)


if __name__ == '__main__':
    unittest.main()
