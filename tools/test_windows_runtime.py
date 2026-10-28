"""Synthetic lock ownership checks; native Windows execution is required too."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import argparse
import contextlib
import json
import time
from unittest.mock import patch

import yaml
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from jobs.file_lock import acquire_file_lock


class LockTests(unittest.TestCase):
    def test_nonblocking_and_blocking_competitors(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'lock'
            owner = acquire_file_lock(path)
            self.assertIsNotNone(owner)
            self.assertIsNone(acquire_file_lock(path))
            obtained = threading.Event()
            def wait_for_lock():
                with acquire_file_lock(path, blocking=True):
                    obtained.set()
            thread = threading.Thread(target=wait_for_lock, daemon=True)
            thread.start()
            self.assertFalse(obtained.wait(0.15))
            owner.close()
            self.assertTrue(obtained.wait(5))
            thread.join(5)
            self.assertTrue(path.exists())

    def test_child_retains_lock_after_parent_closes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'lock'
            owner = acquire_file_lock(path)
            if os.name == 'nt':
                import msvcrt
                raw = msvcrt.get_osfhandle(owner.fileno())
                os.set_handle_inheritable(raw, True)
                startup = subprocess.STARTUPINFO()
                startup.lpAttributeList = {'handle_list': [raw]}
                options = {'startupinfo': startup, 'close_fds': True}
                script = ('import os,msvcrt,sys; '
                          'fd=msvcrt.open_osfhandle(int(sys.argv[1]),os.O_RDWR); '
                          'print("ready",flush=True); sys.stdin.read(); os.close(fd)')
                argument = str(raw)
            else:
                options = {'pass_fds': (owner.fileno(),)}
                script = ('import os,sys; fd=int(sys.argv[1]); '
                          'print("ready",flush=True); sys.stdin.read(); os.close(fd)')
                argument = str(owner.fileno())
            try:
                child = subprocess.Popen([sys.executable, '-c', script, argument],
                                         stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                         stderr=subprocess.PIPE, text=True, **options)
            finally:
                if os.name == 'nt':
                    os.set_handle_inheritable(raw, False)
            try:
                self.assertEqual(child.stdout.readline().strip(), 'ready')
                owner.close()
                self.assertIsNone(acquire_file_lock(path))
                child.stdin.close()
                self.assertEqual(child.wait(timeout=5), 0)
                with acquire_file_lock(path) as recovered:
                    self.assertIsNotNone(recovered)
            finally:
                owner.close()
                if child.poll() is None:
                    child.kill()
                    child.wait(timeout=5)
                child.stdout.close()
                child.stderr.close()



class ListenerTests(unittest.TestCase):
    def test_single_listener_owns_port_and_close_releases_it(self):
        import socket
        from http.server import BaseHTTPRequestHandler
        from jobs.http_server import ThreadingHTTPServer, configure_listener
        server = ThreadingHTTPServer(('127.0.0.1', 0), BaseHTTPRequestHandler)
        address = server.server_address
        try:
            with socket.socket() as competitor:
                configure_listener(competitor)
                with self.assertRaises(OSError): competitor.bind(address)
        finally:
            server.server_close()
        with socket.socket() as recovered:
            configure_listener(recovered)
            recovered.bind(address)


class NativeCreateTests(unittest.TestCase):
    """Native call contracts on a fake kernel; not Windows execution evidence."""
    def test_job_and_handle_attributes_are_present_at_process_creation(self):
        import ctypes as c
        from ctypes import wintypes as w
        import types
        from jobs import windows_process as native
        for fail_create in (False, True):
            with self.subTest(create_failure=fail_create):
                events, handles = [], []
                class Function:
                    def __init__(self, fn): self.fn = fn
                    def __call__(self, *args): return self.fn(*args)
                class Kernel:
                    pass
                k = Kernel()
                def duplicate(_source, raw, _target, result, *_):
                    result._obj.value = 700 + len(handles)
                    handles.append(result._obj.value)
                    return True
                def initialize(buffer, count, _flags, size):
                    self.assertEqual(count, 2)
                    size._obj.value = 512
                    return buffer is not None
                def attribute(_buffer, _flags, key, values, _size, *_):
                    events.append((key, list(values)))
                    return True
                def create(executable, command, _pa, _ta, inherit, flags, env, cwd, startup, info):
                    self.assertEqual(events[:2], [(0x20002, [400, *handles]), (0x2000d, [500])])
                    self.assertEqual(executable, 'C:/synthetic venv/python.exe')
                    self.assertIn('中文', command.value)
                    self.assertTrue(inherit)
                    self.assertEqual(flags & 0x80000, 0x80000)
                    self.assertIsNone(env)
                    self.assertEqual(startup._obj.StartupInfo.cb, c.sizeof(startup._obj))
                    events.append('create')
                    if fail_create: return False
                    info._obj.hProcess, info._obj.hThread, info._obj.dwProcessId = 501, 502, 503
                    return True
                methods = {'GetCurrentProcess': lambda: 100, 'GetStdHandle': lambda _id: 101,
                           'DuplicateHandle': duplicate, 'InitializeProcThreadAttributeList': initialize,
                           'UpdateProcThreadAttribute': attribute, 'DeleteProcThreadAttributeList': lambda _: events.append('delete-attributes'),
                           'CreateProcessW': create, 'CloseHandle': lambda h: events.append(('close', h)) or True,
                           'WaitForSingleObject': lambda *_: 0,
                           'GetExitCodeProcess': lambda h, code: setattr(code._obj, 'value', 0) or True,
                           'TerminateProcess': lambda *_: True}
                for name, fn in methods.items(): setattr(k, name, Function(fn))
                job = types.SimpleNamespace(c=c, w=w, k=k, handle=500)
                with patch.dict(sys.modules, msvcrt=types.SimpleNamespace()), \
                     patch.object(c, 'WinError', lambda *_: OSError('synthetic create failure'), create=True), \
                     patch.object(c, 'get_last_error', lambda: 5, create=True):
                    if fail_create:
                        with self.assertRaises(OSError):
                            native._create_in_job(job, ['C:/synthetic venv/python.exe', '中文'], 400)
                    else:
                        child = native._create_in_job(job, ['C:/synthetic venv/python.exe', '中文'], 400)
                        self.assertEqual(child.pid, 503)
                        self.assertEqual(child.wait(timeout=1), 0)
                        child.close()
                self.assertIn('delete-attributes', events)
                for handle in handles: self.assertIn(('close', handle), events)
                self.assertNotIn(('close', 400), events)  # Caller still owns lock.

    def test_failed_native_creation_resets_inheritance_and_closes_job(self):
        import types
        from jobs import windows_process as native
        events = []
        job = types.SimpleNamespace(close=lambda: events.append('close-job'))
        with patch.object(native, 'WindowsJob', return_value=job), \
             patch.object(native, 'lock_handle', return_value=400), \
             patch.object(native, '_create_in_job', side_effect=OSError('synthetic create failure')), \
             patch.object(os, 'set_handle_inheritable', lambda h, flag: events.append((h, flag)), create=True):
            with self.assertRaises(OSError): native.spawn_python(['run'], object())
        self.assertEqual(events, [(400, True), (400, False), 'close-job'])


# The same supervisor is exercised on macOS with a POSIX process-tree adapter.
# Windows uses its real gate, inherited handles and native Job Object instead.
class SupervisorTests(unittest.TestCase):
    def setUp(self):
        from jobs import db, labels, sources
        self.temp = tempfile.TemporaryDirectory(prefix='windows runtime 中文 ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = {'db_path': str(self.root / 'jobs.db'), 'queries': ['synthetic'],
                       'seek': {'enabled': True, 'detail_sleep': 0},
                       'jobspy': {'indeed': {'enabled': False}, 'linkedin': {'enabled': False}},
                       'report': {'out_dir': str(self.root / 'reports')}}
        self.path = self.root / 'config.yaml'
        self.path.write_text(yaml.safe_dump(self.config), encoding='utf-8')
        self.rules = self.root / 'rules.yaml'
        self.rules.write_text('version: 1\nexclude: []\n', encoding='utf-8')
        self.skills = self.root / 'skills.yaml'
        self.skills.write_text('skills: []\n', encoding='utf-8')
        conn = db.connect(self.config['db_path'])
        job = sources.blank_job('seek', 'synthetic-existing')
        job.update(title='Fictional existing engineer', url='https://example.invalid/existing')
        db.upsert_job(conn, job)
        conn.commit()
        labels.set_label(conn, 'seek:synthetic-existing', source='test', note='Keep this synthetic note', action='saved')
        self.saved = dict(conn.execute('SELECT * FROM labels WHERE uid=?', ('seek:synthetic-existing',)).fetchone())
        conn.close()
        # Inject only a handwritten source mock into child interpreters. No
        # recruitment request or private configuration can be made by this test.
        (self.root / 'sitecustomize.py').write_text("""
import atexit, json, os, socket, subprocess, sys, time, traceback
from pathlib import Path
from jobs import sources, run_state
from jobs.sources import blank_job, seek
root = Path(os.environ['SYNTHETIC_RUNTIME_ROOT'])
trace_path = root / ('worker-' + str(os.getpid()) + '.jsonl')
events = 0
def trace(event, **fields):
    global events
    if events >= 64:
        return
    events += 1
    try:
        with trace_path.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(dict(event=event, pid=os.getpid(), **fields)) + '\\n')
    except OSError:
        pass  # Diagnostic failure must not change the synthetic worker outcome.
trace('boot')
atexit.register(lambda: trace('exit'))
def failed(kind, value, tb):
    trace('unhandled', exception=kind.__name__, errno=getattr(value, 'errno', None),
          winerror=getattr(value, 'winerror', None))
    try:
        (root / ('worker-' + str(os.getpid()) + '.traceback')).write_bytes(
            ''.join(traceback.format_exception(kind, value, tb, limit=12)).encode('utf-8')[-16384:]
            .decode('utf-8', errors='ignore').encode('utf-8'))
    except OSError:
        pass
sys.excepthook = failed
write_json = run_state.atomic_json
def observed_write(path, value):
    fields = dict(file=Path(path).name, state=value.get('state'), stage=value.get('stage'))
    try:
        if (os.environ.get('SYNTHETIC_RUNTIME_FAIL_WRITE') and
                value.get('stage') == 'fetch' and value.get('state') == 'running'):
            raise PermissionError(13, 'synthetic status replacement denied')
        write_json(path, value)
    except OSError as error:
        trace('write_failed', **fields, exception=type(error).__name__,
              errno=error.errno, winerror=getattr(error, 'winerror', None))
        raise
    trace('write_ok', **fields)
run_state.atomic_json = observed_write
seek.fetch_detail = lambda _: 'Synthetic existing Python description'
def blocked(*a, **kw):
    raise AssertionError('network forbidden')
socket.create_connection = blocked

def fetch(config, queries):
    trace('fetch_entered')
    (root / 'entered').write_text(str(os.getpid()))
    time.sleep(float(os.environ.get('SYNTHETIC_RUNTIME_BRIEF', '0')))
    if os.environ.get('SYNTHETIC_RUNTIME_SLEEP'):
        # A real descendant must also stop before a timeout is published.
        child = subprocess.Popen([sys.executable, '-S', '-c',
            'import time; from pathlib import Path; p=Path(' + repr(str(root / 'heartbeat')) + '); '
            '\\nwhile True: p.write_text(str(time.time())); time.sleep(.02)'])
        time.sleep(30)
    job = blank_job('seek', 'synthetic-new')
    job.update(title='Fictional Python engineer', company='Example',
               url='https://example.invalid/new', description='Synthetic Python SQL role.')
    trace('fetch_returned')
    return [job]
sources.iter_sources = lambda config: iter([('seek', fetch)])
""", encoding='utf-8')
        self.enterContext(patch.dict(os.environ, PYTHONPATH=str(self.root) + os.pathsep + str(ROOT),
                                    SYNTHETIC_RUNTIME_ROOT=str(self.root), PYTHONUTF8='1'))
        if os.name != 'nt':
            from jobs import windows_pipeline
            self.enterContext(patch.object(windows_pipeline, 'spawn_python', self.posix_spawn))

    @staticmethod
    def posix_spawn(arguments, lock, **options):
        import signal
        child = subprocess.Popen([sys.executable, '-m', 'jobs.cli', *arguments,
                                  '--run-lock-fd', str(lock.fileno())],
                                 pass_fds=(lock.fileno(),), start_new_session=True, **options)
        class Job:
            def stop(self, process):
                try: os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError: pass
                process.wait(timeout=5)
            def close(self): pass
        child.close = lambda: None
        return child, Job()

    def args(self, timeout=10):
        return argparse.Namespace(config=str(self.path), rules=str(self.rules), skills=str(self.skills),
                                  timeout=timeout)

    def assert_saved(self):
        from jobs import db
        conn = db.connect(self.config['db_path'])
        try:
            self.assertEqual(dict(conn.execute('SELECT * FROM labels').fetchone()), self.saved)
        finally: conn.close()

    def panel_diagnostics(self, manager, done):
        """Bounded synthetic evidence; no production config or data is read."""
        from jobs import run_state
        process = manager._process
        report = {'watcher_done': done.is_set(),
                  'returncode': getattr(process, 'returncode', None),
                  'pid': getattr(process, 'pid', None)}
        for name in ('latest.json', 'state.json', 'result.json', 'generation.json'):
            report[name] = run_state.read_json(manager.runtime / name)
        report['workers'] = {p.name: p.read_bytes()[-16384:].decode('utf-8', errors='ignore')
                             for p in sorted(self.root.glob('worker-*'))[:8]}
        return report

    @contextlib.contextmanager
    def panel_run(self, manager):
        """The watcher owns finalization and its lock, even after child exit."""
        done = threading.Event()
        errors = []
        watch = manager._watch
        def watched(*args):
            try:
                watch(*args)
            except BaseException as error:
                errors.append(repr(error))
                raise
            finally:
                done.set()
        failed = False
        with patch.object(manager, '_watch', side_effect=watched):
            try:
                yield done
            except BaseException:
                failed = True
                raise
            finally:
                if manager._process is not None:
                    if not done.wait(5):
                        # Wake the watcher; it alone owns process-tree cleanup.
                        manager._process.kill()
                    finished = done.wait(5)
                    if failed or not finished or errors:
                        print('PANEL_WORKER_DIAGNOSTICS ' + json.dumps(
                            self.panel_diagnostics(manager, done), ensure_ascii=False), file=sys.stderr)
                    self.assertTrue(finished, 'panel watcher did not finish cleanup')
                    self.assertFalse(errors, errors)

    def test_real_panel_worker_handoff_and_completion(self):
        from jobs import panel_update, run_state
        manager = panel_update.UpdateManager(self.config, self.path, rules_path=self.rules, skills_path=self.skills)
        with self.panel_run(manager) as done:
            with patch.dict(os.environ, SYNTHETIC_RUNTIME_BRIEF='0.4'):
                initial = manager.start()
            self.assertTrue(initial['accepted'], initial)
            self.assertIsNone(run_state.acquire_lock(self.config))
            restarted = panel_update.UpdateManager(self.config, self.path, rules_path=self.rules, skills_path=self.skills)
            self.assertFalse(restarted.start()['accepted'])
            deadline = time.monotonic() + 15
            while True:
                final = manager.status()
                self.assertIn(final['state'], ('running', 'success'), final)
                if final['state'] == 'success' and final['generation'] == initial['task_id']:
                    break
                self.assertLess(time.monotonic(), deadline)
                time.sleep(.05)
            self.assertEqual(final['state'], 'success', final)
            self.assertEqual(final['task_id'], initial['task_id'])
            self.assertEqual(final['generation'], initial['task_id'])
            self.assertTrue(done.wait(5), 'terminal status preceded watcher cleanup')
            self.assertFalse((manager.runtime / 'config.yaml').exists())
            with run_state.acquire_lock(self.config) as owner:
                self.assertIsNotNone(owner)
            self.assert_saved()

    def test_panel_worker_write_failure_is_diagnosable_and_releases_lock_for_retry(self):
        from jobs import panel_update, run_state
        manager = panel_update.UpdateManager(self.config, self.path, rules_path=self.rules, skills_path=self.skills)
        with self.panel_run(manager) as done:
            with patch.dict(os.environ, SYNTHETIC_RUNTIME_FAIL_WRITE='1'):
                initial = manager.start()
            self.assertTrue(initial['accepted'], initial)
            self.assertTrue(done.wait(10))
            final = manager.status()
            self.assertEqual(final['state'], 'error', final)
            self.assertFalse(final['refresh_available'])
            self.assertEqual(final['generation'], '')
            evidence = self.panel_diagnostics(manager, done)
            events = [json.loads(line) for name, body in evidence['workers'].items()
                      if name.endswith('.jsonl') for line in body.splitlines()]
            self.assertTrue(any(event['event'] == 'write_failed' and event['stage'] == 'fetch'
                                and event['exception'] == 'PermissionError' for event in events), evidence)
            self.assertTrue(any(event['event'] == 'unhandled' for event in events), evidence)
            self.assertNotEqual(evidence['returncode'], 0, evidence)
            with run_state.acquire_lock(self.config) as owner:
                self.assertIsNotNone(owner)
            self.assert_saved()
        # A failed attempt must not poison the next run or its result generation.
        with self.panel_run(manager) as done:
            retried = manager.start()
            self.assertTrue(retried['accepted'], retried)
            self.assertTrue(done.wait(10))
            final = manager.status()
            self.assertEqual(final['state'], 'success', final)
            self.assertEqual(final['generation'], retried['task_id'])
            self.assert_saved()

    def test_panel_terminal_error_can_precede_watcher_lock_release(self):
        from jobs import panel_update, run_state
        manager = panel_update.UpdateManager(self.config, self.path, rules_path=self.rules, skills_path=self.skills)
        closing, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        watch = manager._watch
        def delayed_cleanup(process, handle, snapshot, initial):
            class HeldHandle:
                def close(inner):
                    closing.set()
                    try:
                        if not release.wait(10):
                            raise RuntimeError('synthetic cleanup barrier timed out')
                    finally:
                        handle.close()
            watch(process, HeldHandle(), snapshot, initial)
        with patch.object(manager, '_watch', side_effect=delayed_cleanup), self.panel_run(manager) as done:
            try:
                with patch.dict(os.environ, SYNTHETIC_RUNTIME_FAIL_WRITE='1'):
                    initial = manager.start()
                self.assertTrue(initial['accepted'], initial)
                self.assertTrue(closing.wait(10))
                self.assertEqual(manager.status()['state'], 'error')
                # The old finally skipped cleanup when poll() was non-None.
                # Neither child exit nor terminal status proves watcher completion.
                self.assertIsNotNone(manager._process.poll())
                self.assertFalse(done.is_set())
                self.assertIsNone(run_state.acquire_lock(self.config))
            finally:
                release.set()
            self.assertTrue(done.wait(5))
            with run_state.acquire_lock(self.config) as owner:
                self.assertIsNotNone(owner)
            self.assert_saved()

    def test_success_is_durable_and_busy_cannot_replace_it(self):
        from jobs import windows_pipeline, run_state
        self.assertEqual(windows_pipeline.execute(self.args()), 0)
        terminal = run_state.latest(self.config)
        self.assertEqual(terminal['state'], 'success')
        self.assertEqual(terminal['generation'], terminal['task_id'])
        self.assertTrue((self.root / 'reports').is_dir())
        self.assert_saved()
        with run_state.acquire_lock(self.config):
            self.assertEqual(windows_pipeline.execute(self.args()), 4)
            self.assertEqual(run_state.latest(self.config), terminal)

    def test_timeout_stops_descendant_preserves_labels_and_can_retry(self):
        from jobs import windows_pipeline, run_state
        with patch.dict(os.environ, SYNTHETIC_RUNTIME_SLEEP='1'):
            if os.name == 'nt':
                from jobs import windows_process
                original_create = windows_process._create_in_job
                def delayed_create(*arguments, **options):
                    child = original_create(*arguments, **options)
                    time.sleep(.2)
                    return child
                with patch.object(windows_process, '_create_in_job', delayed_create):
                    self.assertEqual(windows_pipeline.execute(self.args(timeout=5)), 3)
            else:
                self.assertEqual(windows_pipeline.execute(self.args(timeout=2)), 3)
        terminal = run_state.latest(self.config)
        self.assertEqual(terminal['state'], 'timeout')
        self.assertFalse(terminal['refresh_available'])
        self.assertEqual(terminal['generation'], '')
        heartbeat = self.root / 'heartbeat'
        self.assertTrue(heartbeat.exists(), 'descendant must start before timeout')
        before = heartbeat.read_bytes()
        time.sleep(.15)
        self.assertEqual(heartbeat.read_bytes(), before)
        self.assert_saved()
        self.assertEqual(windows_pipeline.execute(self.args()), 0)
        self.assertEqual(run_state.latest(self.config)['state'], 'success')

    def test_configured_vocabulary_does_not_replace_cli_default(self):
        from jobs import windows_pipeline
        configured = self.root / 'configured.yaml'
        configured.write_text('skills: [{name: ConfiguredOnly}]\n', encoding='utf-8')
        self.config['skills_path'] = str(configured)
        self.path.write_text(yaml.safe_dump(self.config), encoding='utf-8')
        for explicit in (None, str(self.skills)):
            with self.subTest(explicit=explicit):
                args = self.args()
                args.skills = explicit
                def inspect_command(arguments, _lock, **_options):
                    if explicit is None:
                        self.assertNotIn('--skills', arguments)
                    else:
                        self.assertEqual(arguments[arguments.index('--skills') + 1], str(Path(explicit).resolve()))
                    raise OSError('synthetic stop after command check')
                with patch.object(windows_pipeline, 'spawn_python', inspect_command):
                    with self.assertRaisesRegex(OSError, 'command check'):
                        windows_pipeline.execute(args)

    def test_cli_default_vocabulary_matches_supervised_results(self):
        from jobs import windows_pipeline, db
        configured = self.root / 'configured.yaml'
        configured.write_text('skills: [{name: ConfiguredOnly}]\n', encoding='utf-8')
        self.config['skills_path'] = str(configured)
        self.path.write_text(yaml.safe_dump(self.config), encoding='utf-8')
        # --windows-worker selects the existing in-process pipeline even on
        # Windows, so this is an equivalent baseline rather than two supervisors.
        command = [sys.executable, '-m', 'jobs.cli', '--config', str(self.path),
                   '--rules', str(self.rules), 'run', '--windows-worker', '--timeout', '0']
        baseline = subprocess.run(command, cwd=ROOT, capture_output=True, text=True,
                                  encoding='utf-8', timeout=20)
        self.assertEqual(baseline.returncode, 0, baseline.stdout + baseline.stderr)
        def persisted_skills():
            conn = db.connect(self.config['db_path'])
            try: return [tuple(row) for row in conn.execute('SELECT * FROM job_skills ORDER BY uid,skill')]
            finally: conn.close()
        before = persisted_skills()
        self.assertTrue(before, 'baseline must match at least one public vocabulary token')
        args = self.args()
        args.skills = None
        self.assertEqual(windows_pipeline.execute(args), 0)
        self.assertEqual(persisted_skills(), before)
        self.assertNotIn('ConfiguredOnly', [skill for _, skill in before])

    def test_spawn_failure_is_terminal_and_releases_lock(self):
        from jobs import windows_pipeline, run_state
        with patch.object(windows_pipeline, 'spawn_python', side_effect=OSError('synthetic start failure')):
            with self.assertRaises(OSError): windows_pipeline.execute(self.args())
        self.assertEqual(run_state.latest(self.config)['state'], 'error')
        self.assertEqual(run_state.latest(self.config)['generation'], '')
        with run_state.acquire_lock(self.config) as handle:
            self.assertIsNotNone(handle)
        self.assert_saved()

    def test_interrupt_stops_child_before_publishing(self):
        from jobs import windows_pipeline, run_state
        original_sleep = time.sleep
        def interrupt(seconds):
            if seconds == .05: raise KeyboardInterrupt
            return original_sleep(seconds)
        with patch.object(windows_pipeline.time, 'sleep', side_effect=interrupt):
            with self.assertRaises(KeyboardInterrupt): windows_pipeline.execute(self.args())
        self.assertEqual(run_state.latest(self.config)['state'], 'interrupted')
        self.assertEqual(run_state.latest(self.config)['generation'], '')
        self.assert_saved()

    @unittest.skipUnless(os.name == 'nt', 'native Windows parent-exit and Job Object cleanup')
    def test_native_supervisor_kill_stops_worker_and_descendants(self):
        from jobs import run_state
        self.assertNotEqual(sys.prefix, sys.base_prefix, 'native acceptance must use the documented venv')
        command = [sys.executable, '-m', 'jobs.cli', '--config', str(self.path),
                   '--rules', str(self.rules), '--skills', str(self.skills), 'run', '--timeout', '20']
        with patch.dict(os.environ, SYNTHETIC_RUNTIME_SLEEP='1'):
            parent = subprocess.Popen(command, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic() + 10
            while not (self.root / 'heartbeat').exists():
                self.assertIsNone(parent.poll())
                self.assertLess(time.monotonic(), deadline)
                time.sleep(.05)
            self.assertIsNone(run_state.acquire_lock(self.config))
            parent.kill()
            parent.wait(timeout=5)
            # A surviving worker continues holding its inherited exclusive handle
            # until the kernel kills it. Recovery must wait for that release.
            owner = None
            while owner is None:
                self.assertLess(time.monotonic(), deadline)
                owner = run_state.acquire_lock(self.config)
                time.sleep(.02)
            owner.close()
            stamp = (self.root / 'heartbeat').read_bytes()
            time.sleep(.15)
            self.assertEqual((self.root / 'heartbeat').read_bytes(), stamp)
            self.assertEqual(run_state.latest(self.config)['state'], 'interrupted')
            self.assert_saved()
        finally:
            if parent.poll() is None:
                parent.kill(); parent.wait(timeout=5)
            parent.stderr.close()


if __name__ == '__main__':
    unittest.main(verbosity=2)
