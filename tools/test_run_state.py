"""Offline shared task facts and generation regressions; synthetic temporary DB."""
from __future__ import annotations
import argparse
import contextlib
import io
import json
from pathlib import Path
import socket
import os
import signal
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from test_panel_update import TemporaryTest
from jobs import cli, db, panel_update, run_state, sources


class SharedFacts(TemporaryTest):
    def setUp(self):
        super().setUp()
        self.cfg = self.fixture()

    def args(self, **extra):
        return argparse.Namespace(config=str(self.path), rules=str(self.path.parent / 'rules.yaml'),
                                  skills=str(self.path.parent / 'skills.yaml'), timeout=0, windows_worker=True, **extra)

    def test_scheduled_then_manual_latest_and_failed_attempt_visible(self):
        with patch.object(sources, 'iter_sources', return_value=[('seek', lambda *_: [])]), contextlib.redirect_stdout(io.StringIO()):
            first = self.args(trigger='scheduled')
            self.assertEqual(cli.cmd_run(first), 0)
        manager = panel_update.UpdateManager(self.cfg, self.path)
        prior = manager.status()
        self.assertEqual(prior['trigger'], 'scheduled')
        self.assertEqual(prior['state'], 'success')
        self.assertEqual(prior['generation'], prior['task_id'])
        with patch.object(cli, 'cmd_analyze', return_value=1), patch.object(sources, 'iter_sources', return_value=[('seek', lambda *_: [])]), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.cmd_run(self.args(trigger='manual')), 1)
        failed = manager.status()
        self.assertEqual(failed['trigger'], 'manual')
        self.assertEqual(failed['state'], 'error')
        self.assertNotEqual(failed['task_id'], prior['task_id'])
        self.assertEqual(failed['generation'], prior['generation'])

    def test_fetch_is_one_task_analyze_report_do_not_advance_fetch_fact(self):
        with patch.object(sources, 'iter_sources', return_value=[('seek', lambda *_: [])]), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.cmd_fetch(self.args()), 0)
        before = run_state.latest(self.cfg)
        self.assertEqual(before['kind'], 'fetch')
        self.assertEqual(before['state'], 'success')
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.cmd_analyze(self.args()), 0)
            self.assertEqual(cli.cmd_report(self.args()), 0)
        self.assertEqual(run_state.latest(self.cfg), before)

    def test_fetch_only_source_failure_without_results_does_not_publish_generation(self):
        def fail(*_):
            raise ValueError('private failure')
        with patch.object(sources, 'iter_sources', return_value=[('seek', fail)]), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.cmd_fetch(self.args()), 0)
        fact = run_state.latest(self.cfg)
        self.assertEqual(fact['state'], 'partial')
        self.assertFalse(fact['refresh_available'])
        self.assertEqual(fact['generation'], '')
        self.assertNotIn('private failure', json.dumps(fact))

    def test_partial_full_run_publishes_applied_config_with_no_new_listings(self):
        self.cfg['legacy_rule_profile'] = {'profile': 'citizen'}
        self.write(self.cfg)
        def fail(*_):
            raise ValueError('synthetic source failure')
        with patch.object(sources, 'iter_sources', return_value=[('seek', fail)]), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.cmd_run(self.args()), 0)
        fact = run_state.latest(self.cfg)
        self.assertEqual(fact['state'], 'partial')
        self.assertEqual(fact['counts']['seen'], 0)
        self.assertTrue(fact['refresh_available'])
        self.assertEqual(fact['generation'], fact['task_id'])
        conn = db.connect(self.cfg['db_path'])
        recorded = conn.execute("SELECT stats FROM runs WHERE kind='analyze' ORDER BY run_id DESC LIMIT 1").fetchone()
        self.assertEqual(json.loads(recorded['stats'])['config_snapshot']['values']['legacy_rule_profile.profile'], 'citizen')
        conn.close()

    def test_kill_during_final_logging_cannot_leave_terminal_success(self):
        run_state.publish(self.cfg, {'task_id': 'previous', 'state': 'success', 'refresh_available': True})
        script = """
import argparse, os
from unittest.mock import patch
from jobs import cli, db, sources
args = argparse.Namespace(config=CONFIG, rules=RULES, skills=SKILLS, timeout=0, windows_worker=True)
original = db.log_run
def kill_before_summary(conn, kind, started, stats):
    if kind == 'run':
        os._exit(23)
    return original(conn, kind, started, stats)
with patch.object(sources, 'iter_sources', return_value=[('seek', lambda *_: [])]), patch.object(db, 'log_run', kill_before_summary):
    cli.cmd_run(args)
"""
        for name, value in {'CONFIG': str(self.path), 'RULES': str(self.path.parent / 'rules.yaml'),
                            'SKILLS': str(self.path.parent / 'skills.yaml')}.items():
            script = script.replace(name, repr(value))
        child = subprocess.run([sys.executable, '-c', script], cwd=ROOT,
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=10)
        self.assertEqual(child.returncode, 23, child.stderr.decode())
        persisted = run_state.read_json(run_state.runtime_path(self.cfg) / 'latest.json')
        self.assertEqual(persisted['state'], 'running')
        observed = run_state.latest(self.cfg)
        self.assertEqual(observed['state'], 'interrupted')
        self.assertEqual(observed['generation'], 'previous')
        self.assertFalse(observed['refresh_available'])

    def test_external_config_edit_during_fetch_cannot_change_current_analysis(self):
        self.cfg['legacy_rule_profile'] = {'profile': '485'}
        self.write(self.cfg)
        def fetch(*_):
            changed = dict(self.cfg)
            changed['legacy_rule_profile'] = {'profile': 'citizen'}
            self.write(changed)
            return []
        with patch.object(sources, 'iter_sources', return_value=[('seek', fetch)]), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.cmd_run(self.args()), 0)
        conn = db.connect(self.cfg['db_path'])
        row = conn.execute("SELECT stats FROM runs WHERE kind='analyze' ORDER BY run_id DESC LIMIT 1").fetchone()
        conn.close()
        self.assertEqual(json.loads(row['stats'])['rule_profile']['profile'], '485')

    def test_abandoned_task_becomes_interrupted_once_and_keeps_identity(self):
        fact = {'task_id': 'abandoned', 'kind': 'run', 'trigger': 'scheduled', 'state': 'running', 'started_at': db.utcnow()}
        run_state.publish(self.cfg, fact)
        observed = run_state.latest(self.cfg)
        self.assertEqual(observed['state'], 'interrupted')
        self.assertEqual(observed['task_id'], 'abandoned')
        self.assertEqual(run_state.latest(self.cfg), observed)

    def test_busy_attempt_does_not_replace_active_task(self):
        handle = run_state.acquire_lock(self.cfg)
        try:
            run_state.publish(self.cfg, {'task_id': 'active', 'state': 'running', 'kind': 'run'})
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(cli.cmd_run(self.args()), 4)
            self.assertEqual(run_state.latest(self.cfg)['task_id'], 'active')
        finally:
            handle.close()

    def test_page_shrink_preserves_filtered_view_and_returns_last_available_page(self):
        from jobs import panel_views
        conn = db.connect(self.cfg['db_path'])
        skills_cfg = panel_views.skills_mod.load_skills(self.cfg.get('skills_path'))
        _, total = panel_views._query(conn, self.cfg, 'all', 0, 2, {}, None, skills_cfg)
        expected_offset = ((total - 1) // 2) * 2
        expected, _ = panel_views._query(conn, self.cfg, 'all', expected_offset, 2, {}, None, skills_cfg)
        html = panel_views.render_page(conn, self.cfg, 'all', 99999, 2, lang='en')
        self.assertTrue(expected)
        for row in expected:
            self.assertIn('data-uid="' + row['uid'] + '"', html)
        self.assertIn('view=all', html)
        conn.close()

    @unittest.skipIf(os.name == 'nt', 'POSIX FD handoff; native handles covered by test_windows_runtime')
    def test_detached_inherited_worker_recovers_generation_after_panel_restart(self):
        for code in (0, 9):
            with self.subTest(exit_code=code):
                handle = run_state.acquire_lock(self.cfg)
                task = 'detached-' + str(code)
                script = """
import argparse, socket
from unittest.mock import patch
from jobs import cli, sources
args = argparse.Namespace(config=CONFIG, rules=RULES, skills=SKILLS, timeout=0,
                          run_lock_fd=FD, task_id=TASK, trigger='manual')
with patch.object(sources, 'iter_sources', return_value=[('seek', lambda *_: [])]), \
     patch.object(cli, 'cmd_details', lambda _args: 0), \
     patch.object(cli, 'cmd_analyze', lambda _args: CODE), \
     patch.object(socket, 'create_connection', side_effect=AssertionError('network forbidden')):
    raise SystemExit(cli.cmd_run(args))
"""
                for name, value in {'CONFIG': str(self.path), 'RULES': str(self.path.parent / 'rules.yaml'),
                                    'SKILLS': str(self.path.parent / 'skills.yaml'), 'FD': handle.fileno(),
                                    'TASK': task, 'CODE': code}.items():
                    script = script.replace(name, repr(value))
                child = subprocess.Popen([sys.executable, '-c', script], cwd=ROOT,
                                         pass_fds=(handle.fileno(),), stdout=subprocess.DEVNULL,
                                         stderr=subprocess.PIPE)
                try:
                    _, stderr = child.communicate(timeout=10)
                    self.assertEqual(child.returncode, code, stderr.decode())
                    terminal = run_state.latest(self.cfg)
                    self.assertEqual(terminal['completion_ready'], True)
                    if code == 0:
                        self.assertNotEqual(terminal['generation'], task)
                finally:
                    handle.close()
                restarted = panel_update.UpdateManager(self.cfg, self.path)
                observed = restarted.status()
                self.assertEqual(observed['state'], 'success' if code == 0 else 'error')
                if code == 0:
                    self.assertEqual(observed['generation'], task)
                    stamp = (run_state.runtime_path(self.cfg) / 'generation.json').stat().st_mtime_ns
                    self.assertEqual(restarted.status()['generation'], task)
                    self.assertEqual((run_state.runtime_path(self.cfg) / 'generation.json').stat().st_mtime_ns, stamp)
                else:
                    self.assertNotEqual(observed['generation'], task)
                    self.assertFalse(observed['refresh_available'])

    def test_final_logging_failure_cannot_publish_successful_generation(self):
        original = db.log_run
        def fail_summary(conn, kind, started, stats):
            if kind == 'run':
                raise OSError('synthetic logging failure')
            return original(conn, kind, started, stats)
        with patch.object(sources, 'iter_sources', return_value=[('seek', lambda *_: [])]), \
             patch.object(db, 'log_run', fail_summary), contextlib.redirect_stdout(io.StringIO()), self.assertRaises(OSError):
            cli.cmd_run(self.args())
        terminal = run_state.latest(self.cfg)
        self.assertEqual(terminal['state'], 'error')
        self.assertEqual(terminal['generation'], '')
        self.assertFalse(terminal['refresh_available'])

    def test_termination_records_interruption_and_releases_shared_lock(self):
        def terminate(_args):
            if os.name == 'nt':
                raise SystemExit(143)  # Windows TerminateProcess has no Python signal handler.
            os.kill(os.getpid(), signal.SIGTERM)
        with patch.object(cli, 'cmd_fetch', terminate), self.assertRaises(SystemExit):
            cli.cmd_run(self.args(trigger='scheduled'))
        fact = run_state.latest(self.cfg)
        self.assertEqual(fact['state'], 'interrupted')
        self.assertEqual(fact['error'], 'run_interrupted')
        self.assertTrue(fact['finished_at'])
        handle = run_state.acquire_lock(self.cfg)
        self.assertIsNotNone(handle)
        handle.close()

    def test_empty_enabled_sources_rejected_with_terminal_failure(self):
        with patch.object(sources, 'iter_sources', return_value=[]), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.cmd_run(self.args()), 1)
        self.assertEqual(run_state.latest(self.cfg)['state'], 'error')
        self.assertEqual(run_state.latest(self.cfg)['error'], 'no_sources')


if __name__ == '__main__':
    unittest.main(verbosity=2)
