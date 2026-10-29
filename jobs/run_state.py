"""Small persistent execution summary shared by CLI, scheduled jobs and panel.

The runtime directory lives beside the database, so one mounted data directory
also preserves the cross-process lock, latest attempt and result generation.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import time
import uuid

from .config import db_path
from . import db
from .file_security import set_private
from .file_lock import acquire_file_lock

# Windows refuses to replace a file that another process (panel status poll,
# agent API) briefly has open, and a reader can hit the file mid-replace; both
# surface as PermissionError. POSIX rename is unaffected and is never retried.
_WINDOWS = os.name == 'nt'
_REPLACE_RETRY_SECONDS = 2.0
_READ_RETRY_SECONDS = 0.5


def _retry_sharing(operation, budget):
    if not _WINDOWS:
        return operation()
    deadline = time.monotonic() + budget
    delay = 0.001
    while True:
        try:
            return operation()
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(delay)
            delay = min(delay * 2, 0.05)


def read_shared_bytes(path):
    """Read a state file that a concurrent writer may be replacing."""
    return _retry_sharing(Path(path).read_bytes, _READ_RETRY_SECONDS)


def runtime_path(config):
    target = db_path(config).resolve()
    return target.parent / ('.' + target.name + '.panel-update')


def lock_path(config):
    return runtime_path(config) / 'run.lock'


def acquire_lock(config):
    target = lock_path(config)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    return acquire_file_lock(target)


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('w', encoding='utf-8') as stream:
            set_private(temporary)
            json.dump(value, stream)
        _retry_sharing(lambda: os.replace(temporary, path), _REPLACE_RETRY_SECONDS)
    finally:
        temporary.unlink(missing_ok=True)


def read_json(path):
    try:
        value = json.loads(read_shared_bytes(path).decode('utf-8'))
        return value if isinstance(value, dict) else None
    except (OSError, ValueError, UnicodeError):
        return None


def publish(config, value, *, apply_generation=True):
    runtime = runtime_path(config)
    atomic_json(runtime / 'latest.json', value)
    if apply_generation and value.get('task_id') and value.get('state') in ('success', 'partial') and value.get('refresh_available'):
        atomic_json(runtime / 'generation.json', {'generation': value['task_id']})


def generation(config):
    return (read_json(runtime_path(config) / 'generation.json') or {}).get('generation', '')


def latest(config, conn=None):
    value = read_json(runtime_path(config) / 'latest.json')
    recoverable = bool(value and value.get('state') in ('success', 'partial') and
                       value.get('completion_ready') is True and value.get('refresh_available') and
                       value.get('task_id') != generation(config))
    if value and (value.get('state') == 'running' or recoverable):
        try:
            handle = acquire_lock(config)
        except OSError:
            handle = None
        if handle is not None:
            try:
                # The worker inherits this flock through process exit. The panel
                # watcher also holds it until checking the exit code. Re-read
                # under lock so recovery cannot replace a newer attempt.
                value = read_json(runtime_path(config) / 'latest.json') or value
                if value.get('state') == 'running':
                    value = {**value, 'state': 'interrupted', 'error': 'run_interrupted',
                             'finished_at': db.utcnow(), 'refresh_available': False}
                    publish(config, value)
                elif (value.get('state') in ('success', 'partial') and
                      value.get('completion_ready') is True and value.get('refresh_available')):
                    publish(config, value)
            finally:
                handle.close()
    if value is None and conn is not None:
        # Old rows have no trustworthy terminal state/trigger. Keep them readable
        # without guessing success or treating analyze/report as fetching.
        row = conn.execute("SELECT * FROM runs WHERE kind IN ('run','fetch') ORDER BY run_id DESC LIMIT 1").fetchone()
        if row:
            value = {'task_id': 'legacy-' + str(row['run_id']), 'kind': row['kind'],
                     'trigger': 'unknown', 'state': 'unknown', 'started_at': row['started_at'],
                     'finished_at': row['finished_at'], 'counts': None, 'refresh_available': False}
    return {**(value or {'state': 'idle'}), 'generation': generation(config)}
