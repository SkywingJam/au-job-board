"""Windows pipeline supervisor; POSIX keeps the existing in-process signals."""
from __future__ import annotations

from pathlib import Path
import signal
import subprocess
import time
import uuid
import sys

import yaml

from . import db, run_state
from .config import ROOT, db_path, load_config
from .file_security import set_private
from .windows_process import inherited_lock, spawn_python


def execute(args, *, fetch_only=False):
    """Keep lock ownership through process-tree cleanup and terminal persistence."""
    config = load_config(getattr(args, 'config', None))
    raw = getattr(args, 'run_lock_handle', None)
    lock = inherited_lock(raw) if raw is not None else run_state.acquire_lock(config)
    if lock is None:
        print('[run] another update is running', file=sys.stderr)
        return 4
    task_id = getattr(args, 'task_id', None) or uuid.uuid4().hex
    started = getattr(args, 'result_started_at', None) or db.utcnow()
    kind = 'fetch' if fetch_only else 'run'
    runtime = run_state.runtime_path(config)
    snapshot = runtime / ('config-' + task_id + '.yaml')
    result_file = runtime / ('result-' + task_id + '.json')
    result = {'task_id': task_id, 'kind': kind, 'trigger': getattr(args, 'trigger', None) or 'cli',
              'state': 'running', 'stage': None, 'started_at': started, 'finished_at': None,
              'counts': {'seen': 0, 'inserted': 0, 'updated': 0, 'details_fetched': 0},
              'failed_sources': [], 'error': None, 'refresh_available': False}
    process = job = None
    previous_break = None
    code = 1
    interrupted = False
    try:
        # Recheck under the same lock used by configuration saves.
        fresh = load_config(getattr(args, 'config', None))
        if db_path(config).resolve() != db_path(fresh).resolve():
            raise ValueError('database changed while waiting for configuration lock')
        config = fresh
        with snapshot.open('w', encoding='utf-8') as stream:
            set_private(snapshot)
            yaml.safe_dump(config, stream, allow_unicode=True)
        run_state.publish(config, result, apply_generation=False)
        command = ['--config', str(snapshot)]
        for name in ('rules', 'skills'):
            value = getattr(args, name, None)
            if value:
                command += ['--' + name, str(Path(value).resolve())]
        command += [kind, '--windows-worker', '--result-file', str(result_file),
                    '--result-started-at', started, '--task-id', task_id,
                    '--trigger', result['trigger']]
        if not fetch_only:
            command += ['--timeout', '0']  # Supervisor enforces the real deadline.
        elif getattr(args, 'only', None):
            command += ['--only', args.only]
        process, job = spawn_python(command, lock, cwd=ROOT, stdin=subprocess.DEVNULL)
        # CTRL_BREAK is sent to the supervisor's new process group; only this
        # process handles it, then closes the entire nested job before publishing.
        if hasattr(signal, 'SIGBREAK'):
            previous_break = signal.signal(signal.SIGBREAK, _interrupt)
        limit = getattr(args, 'timeout', 0) or 0
        deadline = time.monotonic() + limit if limit > 0 else None
        while process.poll() is None:
            if deadline is not None and time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(process.args, limit)
            time.sleep(0.05)
        code = process.returncode
        candidate = run_state.read_json(result_file)
        if (candidate and candidate.get('task_id') == task_id and candidate.get('completion_ready') is True
                and candidate.get('state') in ('success', 'partial', 'error', 'timeout', 'interrupted')
                and (code == 0 or candidate.get('state') not in ('success', 'partial'))):
            result = candidate
        else:
            result.update(state='error', error='run_failed', refresh_available=False)
            code = code or 1
    except subprocess.TimeoutExpired:
        progress = run_state.read_json(result_file)
        if progress and progress.get('task_id') == task_id:
            result = progress
        result.update(state='timeout', error='run_timeout', refresh_available=False)
        code = 3
    except (KeyboardInterrupt, SystemExit):
        result.update(state='interrupted', error='run_interrupted', refresh_available=False)
        code, interrupted = 130, True
    except Exception:
        result.update(state='error', error='start_failed' if process is None else 'run_failed', refresh_available=False)
        raise
    finally:
        try:
            # Also terminate leftover descendants after a normal worker exit.
            # Final state and a new generation are published only after this.
            if job is not None:
                job.stop(process)
            if previous_break is not None:
                signal.signal(signal.SIGBREAK, previous_break)
            result['finished_at'] = result.get('finished_at') or db.utcnow()
            if result.get('completion_ready') is not True:
                if not fetch_only:
                    conn = db.connect(db_path(config))
                    try:
                        db.log_run(conn, 'run', started, result)
                        conn.commit()
                    finally:
                        conn.close()
                result['completion_ready'] = True
            target = getattr(args, 'result_file', None)
            if target:
                run_state.atomic_json(target, result)
            run_state.publish(config, result, apply_generation=raw is None)
        except BaseException:
            # Never leave a recoverable success on storage/cleanup failure.
            try:
                run_state.publish(config, {**result, 'state': 'error', 'error': 'storage_unavailable',
                                          'refresh_available': False, 'completion_ready': False})
            except OSError:
                pass
            raise
        finally:
            if job is not None:
                job.close()
                process.close()
            try:
                snapshot.unlink(missing_ok=True)
                result_file.unlink(missing_ok=True)
            finally:
                lock.close()
    if interrupted:
        raise KeyboardInterrupt
    return code


def _interrupt(_signum, _frame):
    raise KeyboardInterrupt
